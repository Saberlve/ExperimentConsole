"""Own only the console camera subprocess started by this console."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from adapters.camera import camera_urlopen


class CameraService:
    def __init__(self, config, console_root):
        self.config = config
        self.root = Path(console_root)
        self.process = None
        self.lock = threading.Lock()
        self.thread = None
        self.stop = threading.Event()
        self.state = 'idle'
        self.detail = '等待连接相机服务'

    def status(self):
        return {'state': self.state, 'detail': self.detail,
                'owned': self.process is not None and self.process.poll() is None}

    def start(self):
        with self.lock:
            if self.stop.is_set() or (self.thread and self.thread.is_alive()):
                return
            if self.state == 'ready' and (self.process is None or self.process.poll() is None):
                return
            self.state = 'connecting'
            self.detail = '正在自动连接双相机…'
            self.thread = threading.Thread(target=self._connect, daemon=True)
            self.thread.start()

    def _probe(self):
        url = self.config['camera_url'].rstrip('/') + '/api/cameras'
        with camera_urlopen(url, timeout=1) as response:
            payload = json.load(response)
        expected = Path(self.config['capture_root']).resolve()
        if Path(payload.get('output_dir', '')).resolve() != expected:
            raise ValueError('8766 上已有相机服务，但保存目录不匹配；请停止旧服务后点击刷新相机')
        devices = payload.get('devices', [])
        if len(devices) != 2 or any(not d.get('connected') or d.get('error') for d in devices):
            raise ValueError('原服务尚未连接两台可用相机')
        return True

    def _connect(self):
        try:
            try:
                self._probe()
            except (OSError, TimeoutError):
                parsed = urlsplit(self.config['camera_url'])
                if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1'):
                    raise ValueError('自动启动只支持本机 HTTP 相机服务；远程服务请自行启动')
                if self.stop.is_set():
                    return
                if self.process is None or self.process.poll() is not None:
                    launcher = self.root / 'run_camera.sh'
                    self.process = subprocess.Popen([str(launcher), '--host', '127.0.0.1',
                        '--port', str(parsed.port or 80)], start_new_session=True,
                        env={**os.environ, 'CAMERA_OUTPUT_DIR': self.config['capture_root'],
                             'CAMERA_PYTHON': self.config.get('camera_python') or os.environ.get('CAMERA_PYTHON') or sys.executable})
                deadline = time.monotonic() + self.config.get('camera_start_timeout', 30)
                while not self.stop.is_set():
                    if self.process.poll() is not None:
                        raise RuntimeError(f'相机服务退出（code={self.process.returncode}），检查两台 RealSense USB 连接及终端日志，再点击刷新相机')
                    try:
                        self._probe()
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('双相机连接超时；检查设备连接，点击刷新相机重试')
                        self.stop.wait(.25)
                else:
                    return
            self.state, self.detail = 'ready', '双相机已连接'
        except Exception as exc:
            self.state, self.detail = 'error', str(exc)
            self._stop_owned()

    def _stop_owned(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.detail += '；相机子进程尚未退出，请检查终端'

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        self._stop_owned()
