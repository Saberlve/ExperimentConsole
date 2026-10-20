"""Recovered dual-camera service, entirely owned by experiment_console."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
from urllib.parse import parse_qs, urlsplit
from adapters.realsense import CameraFeed, discover_devices

CAMERAS = {'148522072685': ('全景', 'A'), '242622070583': ('腕部', 'W')}
ROOT = Path(__file__).resolve().parents[1]


def next_capture_number(directory):
    numbers = [int(match.group(1)) for path in Path(directory).iterdir()
               if (match := re.fullmatch(r'(\d+)_?[AW]\.png', path.name, re.IGNORECASE))]
    return max(numbers, default=0) + 1


class PairCapture:
    def __init__(self, feeds, output_dir):
        self.feeds = feeds
        self.output_dir = Path(output_dir)

    def capture(self):
        import cv2
        pending = []
        for serial, (label, suffix) in CAMERAS.items():
            feed = self.feeds.get(serial)
            if feed is None or not feed.connected:
                raise RuntimeError(f'{label}相机未连接：{serial}')
            with feed.condition:
                pending.append((feed, feed.frame_id, label, suffix))

        def fresh_frame(item):
            feed, previous, label, suffix = item
            with feed.condition:
                ready = feed.condition.wait_for(lambda: feed.frame_id > previous or feed.stopped, timeout=3)
                if not ready or feed.stopped or not feed.connected or feed.error or feed.frame is None:
                    raise RuntimeError(f'{label}相机未获得新画面')
                return suffix, feed.frame.copy()

        with ThreadPoolExecutor(max_workers=2) as executor:
            frames = list(executor.map(fresh_frame, pending))
        encoded = []
        for suffix, frame in frames:
            ok, data = cv2.imencode('.png', frame)
            if not ok:
                raise RuntimeError(f'{suffix} PNG 编码失败')
            encoded.append((suffix, data.tobytes()))
        with (self.output_dir / '.capture.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            number = next_capture_number(self.output_dir)
            created = []
            try:
                for suffix, data in encoded:
                    target = self.output_dir / f'{number}_{suffix}.png'
                    with target.open('xb') as stream:
                        created.append(target)
                        stream.write(data)
            except Exception:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
        return {'number': number, 'paths': [str(path.resolve()) for path in created]}


def make_handler(feeds, output, owner_token=''):
    capture = PairCapture(feeds, output)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_json(self, code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def devices(self):
            devices = []
            for serial, feed in feeds.items():
                device = feed.status()
                device['label'], device['suffix'] = CAMERAS[serial]
                devices.append(device)
            return devices

        def do_GET(self):
            try:
                parsed = urlsplit(self.path)
                if parsed.path in ('/', '/health', '/api/cameras', '/api/devices'):
                    self.send_json(200, {'service': 'experiment_console.camera', 'devices': self.devices(),
                        'owner_id': hashlib.sha256(owner_token.encode()).hexdigest() if owner_token else None,
                        'output_dir': str(output), 'next_number': next_capture_number(output)})
                elif parsed.path == '/stream':
                    serial = parse_qs(parsed.query).get('serial', [''])[0]
                    feed = feeds.get(serial)
                    if feed is None or not feed.connected:
                        self.send_json(503, {'error': '相机未连接或不存在'})
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    last_id = 0
                    while True:
                        jpeg, frame_id, stopped = feed.wait_for_frame(last_id)
                        if stopped:
                            return
                        if jpeg is None or frame_id == last_id:
                            continue
                        last_id = frame_id
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\n' +
                            f'Content-Length: {len(jpeg)}\r\n\r\n'.encode() + jpeg + b'\r\n')
                        self.wfile.flush()
                else:
                    self.send_json(404, {'error': '接口不存在'})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                self.send_json(403, {'error': '跨源请求被拒绝'})
                return
            path = urlsplit(self.path).path
            if path == '/api/shutdown':
                if not owner_token or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner_token):
                    self.send_json(403, {'error': '此相机服务不归当前控制台管理'})
                    return
                self.send_json(200, {'stopping': True})
                self.server.shutdown()
                return
            if path != '/api/capture':
                self.send_json(404, {'error': '接口不存在'})
                return
            try:
                result = capture.capture()
                self.send_json(200, result)
            except RuntimeError as exc:
                self.send_json(503, {'error': str(exc)})
            except Exception as exc:
                self.send_json(500, {'error': str(exc)})
    return Handler


def open_feeds(width, height, fps):
    discovered = {device.serial: device for device in discover_devices()}
    missing = set(CAMERAS) - set(discovered)
    if missing:
        raise RuntimeError(f'指定 RealSense 未找到：{", ".join(sorted(missing))}，请检查 USB 连接')
    feeds = {}
    try:
        for serial in CAMERAS:
            feed = CameraFeed(discovered[serial], width, height, fps)
            feeds[serial] = feed
            feed.connect()
        return feeds
    except BaseException:
        for feed in feeds.values():
            feed.stop()
        raise


def main():
    parser = argparse.ArgumentParser(description='控制台双 RealSense 服务（不连接机械臂）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'WM_fisrtFrame')
    args = parser.parse_args()
    if min(args.width, args.height, args.fps) <= 0 or not 1 <= args.port <= 65535:
        parser.error('分辨率/帧率必须为正数，端口必须在 1～65535')
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    feeds, server = {}, None
    try:
        feeds = open_feeds(args.width, args.height, args.fps)
        server = ThreadingHTTPServer((args.host, args.port), make_handler(feeds, output,
            os.environ.get('CONSOLE_CAMERA_OWNER_TOKEN', '')))
        server.daemon_threads = True
        print(f'Console camera service: http://{args.host}:{args.port} -> {output}', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        raise SystemExit(f'相机服务启动失败：{exc}') from exc
    finally:
        for feed in feeds.values():
            feed.stop()
        # The port must stay bound until device cleanup has completed.
        if server:
            server.server_close()


if __name__ == '__main__':
    main()
