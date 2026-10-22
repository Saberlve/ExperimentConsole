"""Record the selected console camera; each server launch owns one directory."""
from datetime import datetime
import os
import re
import threading
import time
import uuid


def video_name(value):
    if not isinstance(value, str):
        raise ValueError('视频名称必须是文本')
    name = re.sub(r'\.mp4$', '', value.strip(), flags=re.IGNORECASE).strip()
    if not name:
        return '录像'
    if len(name.encode('utf-8')) > 180 or name in ('.', '..') or re.search(r'[\\/<>:"|?*\x00-\x1f\x7f]', name):
        raise ValueError('视频名称不能包含路径或特殊字符，且不能超过 180 字节')
    return name


class VideoRecording:
    FPS = 15

    def __init__(self, app):
        self.app = app
        session = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:6]
        self.directory = app.output / 'recordings' / session
        self.directory.mkdir(parents=True)
        self.pending = self.directory / '.pending'
        self.pending.mkdir()
        self.lock = threading.RLock()
        self.operations = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = 'idle'
        self.error = ''
        self.frames = 0
        self.started = None
        self.name = '录像'
        self.last = None

    def status(self):
        with self.lock:
            return dict(state=self.state, active=self.state in ('recording', 'saving'),
                        directory=str(self.directory), name=self.name, error=self.error,
                        frames=self.frames, duration_seconds=self.frames / self.FPS,
                        last=self.last, videos=[dict(name=p.name, url=self.app.url(p))
                            for p in sorted(self.directory.glob('*.mp4'))])

    @staticmethod
    def decode(data):
        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('相机画面无法解码')
        return frame

    def start_replay(self, name, camera_id, cancelled):
        """Make the camera ready before the replay subprocess can move the arm."""
        if cancelled.is_set():
            return None
        if not self.app.preview_enabled:
            self.app.enable_preview()
        service = self.app.camera_service
        if service:
            service.start()
            deadline = time.monotonic() + self.app.config.get('camera_start_timeout', 30) + 2
            while service.state != 'ready':
                if cancelled.wait(.1):
                    return None
                if service.state == 'error' or time.monotonic() >= deadline:
                    raise ValueError('无法开启自动录制：' + service.detail)
        if cancelled.is_set():
            return None
        if not camera_id:
            devices = [device for device in self.app.camera.cameras()
                       if device.get('connected') and not device.get('error')]
            if not devices:
                raise ValueError('没有可用于自动录制的相机')
            camera_id = next((d['serial'] for d in devices if d.get('label') == '全景'), devices[0]['serial'])
        return self.start(dict(name=name, camera_id=camera_id))

    def start(self, payload):
        import cv2
        name = video_name(payload.get('name', ''))
        camera_id = payload.get('camera_id', '')
        with self.app.camera_lock, self.operations:
            with self.lock:
                if self.thread and self.thread.is_alive():
                    raise ValueError('已有视频正在录制，请先停止并保存')
            if not self.app.preview_enabled:
                raise ValueError('请先开启相机，再开始录制')
            frame = self.decode(self.app.camera.frame(camera_id)[0])
            height, width = frame.shape[:2]
            # mp4v requires even dimensions; preserve the rest of the camera image.
            size = (width - width % 2, height - height % 2)
            if min(size) < 2:
                raise ValueError('相机分辨率过小')
            temporary = self.pending / (uuid.uuid4().hex + '.mp4')
            writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*'mp4v'), self.FPS, size)
            if not writer.isOpened():
                writer.release()
                temporary.unlink(missing_ok=True)
                raise ValueError('无法创建 MP4 录像，请检查编码器和保存目录')
            try:
                writer.write(frame[:size[1], :size[0]])
                with self.lock:
                    self.name, self.state, self.error = name, 'recording', ''
                    self.frames, self.started = 1, time.monotonic()
                    self.stop_event.clear()
                    self.thread = threading.Thread(target=self._record,
                        args=(writer, temporary, frame, size, camera_id), daemon=True)
                    self.thread.start()
            except Exception:
                writer.release()
                temporary.unlink(missing_ok=True)
                raise
            return self.status()

    def _record(self, writer, temporary, frame, size, camera_id):
        error = ''
        try:
            while not self.stop_event.wait(max(0, self.started + self.frames / self.FPS - time.monotonic())):
                new_frame = self.decode(self.app.camera.frame(camera_id)[0])
                if new_frame.shape != frame.shape:
                    raise ValueError('相机分辨率发生变化，已结束本条录制')
                # Fill delayed slots with the last image to retain wall-clock duration.
                target = int((time.monotonic() - self.started) * self.FPS)
                while self.frames < target:
                    writer.write(frame[:size[1], :size[0]])
                    with self.lock:
                        self.frames += 1
                frame = new_frame
                writer.write(frame[:size[1], :size[0]])
                with self.lock:
                    self.frames += 1
        except Exception as exc:
            error = str(exc)
        finally:
            try:
                writer.release()
                with self.lock:
                    self.state = 'saving'
                    index = 0
                    while True:
                        filename = f'{self.name}{"_" + str(index) if index else ""}.mp4'
                        path = self.directory / filename
                        try:
                            # Atomic no-overwrite publication, including simultaneous requests.
                            os.link(temporary, path)
                            break
                        except FileExistsError:
                            index += 1
                    temporary.unlink()
                    self.last = dict(name=path.name, path=str(path), url=self.app.url(path))
            except Exception as exc:
                error = (error + '; ' if error else '') + '保存失败：' + str(exc)
            with self.lock:
                self.error = error
                self.state = 'error' if error else 'saved'

    def stop(self, payload=None):
        name = video_name(payload['name']) if payload and 'name' in payload else None
        with self.operations:
            with self.lock:
                thread = self.thread
                if thread and thread.is_alive():
                    if name is not None:
                        self.name = name
                    self.state = 'saving'
                    self.stop_event.set()
            if thread:
                thread.join(timeout=20)
                if thread.is_alive():
                    raise ValueError('视频仍在保存，请稍后重试')
            return self.status()

    def close(self):
        self.stop()
