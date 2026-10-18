"""Local experiment console. Run from the independent repository root."""
import argparse
import io
import json
import mimetypes
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

def app_camera_probe(url):
    return urlopen(url.rstrip("/") + "/api/cameras", timeout=1)

from urllib.parse import parse_qs, urlsplit, quote
from PIL import Image, UnidentifiedImageError
from adapters.camera import CameraAdapter, FirstFrameAdapter, utc
from adapters.dataset import DatasetAdapter
from adapters.models import IDMAdapter, VLAAdapter
from adapters.camera_service import CameraService
from adapters.video import first_frame, MAX_VIDEO_BYTES
from backend.snapshot import SnapshotService
from backend.replay import ReplayService
from backend.evaluation import EvaluationService

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20_000_000


def load_config(path, output_dir=None):
    config = json.loads(Path(path).read_text())
    if output_dir is not None:
        config['output_dir'] = str(Path(output_dir).expanduser().resolve())
    if config['mode'] not in ('demo', 'legacy_http', 'reference_only'):
        raise ValueError('mode must be demo, legacy_http or reference_only')
    for key in ('output_dir', 'capture_root'):
        p = Path(config[key]).expanduser()
        config[key] = str((ROOT / p).resolve() if not p.is_absolute() else p.resolve())
    for entry in config.get('datasets', {}).values():
        p = Path(entry['root']).expanduser()
        dataset_root = (ROOT / p).resolve()
        for key in ('output_dir', 'capture_root'):
            destination = Path(config[key])
            if destination.is_relative_to(dataset_root) or dataset_root.is_relative_to(destination):
                raise ValueError('输出目录与原始数据集目录不得重叠')
        entry['root'] = str(dataset_root)
    return config


class Application:
    def __init__(self, config):
        self.config = config
        self.output = Path(config['output_dir']).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.camera = CameraAdapter(config)
        self.snapshot = SnapshotService(FirstFrameAdapter(self.camera), self.output)
        self.dataset = DatasetAdapter(config)
        self.idm, self.vla = IDMAdapter(), VLAAdapter()
        self.record_lock = threading.Lock()
        self.replay = ReplayService(config, self.output, ROOT)
        self.evaluation = EvaluationService(self)
        self.camera_lock = threading.RLock()
        self.preview_enabled = config.get('camera_preview_on_start', True) and config['mode'] != 'reference_only'
        self.camera_service = (CameraService(config, ROOT) if config['mode'] == 'legacy_http'
                               and config.get('camera_autostart', True) else None)

    def preview_status(self):
        return {'enabled': self.preview_enabled,
                'service': self.camera_service.status() if self.camera_service else None}

    def release_preview(self):
        # No camera may be reacquired by the preview polling endpoints after this point.
        with self.camera_lock:
            self.preview_enabled = False
            service = self.camera_service
            if service:
                service.close()
                if service.thread and service.thread.is_alive():
                    raise ValueError('相机连接线程尚未退出，暂不能标记开始，请稍后重试')
                if service.process is not None and service.process.poll() is None:
                    raise ValueError('相机子进程尚未退出，暂不能标记开始')
            if self.config['mode'] == 'legacy_http':
                # Borrowed services belong to another terminal: never silently kill them.
                try:
                    with app_camera_probe(self.config['camera_url']):
                        pass
                except HTTPError as exc:
                    raise ValueError('相机端口仍有服务响应，请先关闭外部相机服务') from exc
                except URLError as exc:
                    if not isinstance(exc.reason, ConnectionRefusedError):
                        raise ValueError('无法确认相机服务已退出，请检查相机服务终端') from exc
                except ConnectionRefusedError:
                    pass
                else:
                    raise ValueError('相机端口仍有外部服务运行，请在对应终端关闭它后重新释放相机')
            return self.preview_status()

    def enable_preview(self):
        with self.evaluation.lock:
            if any(r['metrics']['running'] for r in self.evaluation.catalog()['runs']):
                raise ValueError('仍有已标记开始的样本，请先记录结果或中止，再开启布局相机')
            with self.camera_lock:
                if self.config['mode'] == 'reference_only':
                    raise ValueError('当前是仅图片模式，请用布局相机配置启动控制台')
                if self.camera_service and self.camera_service.stop.is_set():
                    if self.camera_service.thread and self.camera_service.thread.is_alive():
                        raise ValueError('上一次相机连接还未退出，请稍后重试')
                    if self.camera_service.process is not None and self.camera_service.process.poll() is None:
                        raise ValueError('相机子进程尚未退出，请先释放相机')
                    self.camera_service = CameraService(self.config, ROOT)
                self.preview_enabled = True
                if self.camera_service:
                    self.camera_service.start()
                return self.preview_status()

    def asset(self, url):
        if not isinstance(url, str) or not url.startswith('/assets/'):
            raise ValueError('图像必须来自本控制台上传或保存的资源')
        path = (self.output / url[len('/assets/'):]).resolve()
        if not path.is_relative_to(self.output) or not path.is_file():
            raise ValueError('资源不存在或越界')
        return path

    def url(self, path):
        return '/assets/' + Path(path).resolve().relative_to(self.output).as_posix()

    def upload(self, data):
        if not data or len(data) > MAX_BODY:
            raise ValueError('图片为空或超过 12 MiB')
        image = Image.open(io.BytesIO(data))
        if image.format not in ('PNG', 'JPEG'):
            raise ValueError('仅支持 PNG/JPG')
        image.load()
        path = self.output / 'references' / (uuid.uuid4().hex + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            image.convert('RGB').save(stream, 'PNG')
        return {'url': self.url(path), 'width': image.width, 'height': image.height}

    def infer(self, payload):
        record = {'id': uuid.uuid4().hex, 'timestamp': utc(), 'inputs': payload}
        try:
            for field in ('current_image', 'target_image'):
                path = self.asset(payload.get(field))
                with Image.open(path) as image:
                    image.verify()
            result = self.idm.infer(payload)
            record.update(result)
        except Exception as exc:
            record.update(status='error', error=str(exc), mock=payload.get('mode') == 'mock')
            self.record(record)
            raise
        self.record(record)
        return record

    def record(self, record):
        with self.record_lock:
            with (self.output / 'idm_records.jsonl').open('a') as stream:
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')

    def history(self):
        path = self.output / 'idm_records.jsonl'
        with self.record_lock:
            return [json.loads(line) for line in path.read_text().splitlines()][-100:] if path.exists() else []


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_bytes(self, code, data, content_type='application/json; charset=utf-8', headers=None):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for name, value in (headers or {}).items():
                self.send_header(name, str(value))
            self.end_headers()
            self.wfile.write(data)

        def reply(self, value, code=200):
            self.send_bytes(code, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

        def handle_error(self, exc):
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                return
            code = 503 if isinstance(exc, (URLError, TimeoutError, ImportError)) else 400
            if not isinstance(exc, (ValueError, KeyError, TypeError, OSError, ImportError)):
                code = 500
            self.reply({'error': str(exc), 'type': type(exc).__name__}, code)

        def do_GET(self):
            try:
                self.get()
            except Exception as exc:
                self.handle_error(exc)

        def get(self):
            parsed = urlsplit(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path == '/api/status':
                self.reply({'mode': app.config['mode'], 'robot_execution': app.replay.enabled,
                            'replay': app.replay.capabilities(),
                            'idm': 'mock_only', 'vla': 'preparation_only',
                            'camera_service': app.camera_service.status() if app.camera_service else None, 'preview_enabled': app.preview_enabled})
            elif path == '/api/cameras':
                if not app.preview_enabled:
                    self.reply([])
                    return
                if app.camera_service:
                    app.camera_service.start()
                    if app.camera_service.state != 'ready':
                        self.reply({'error': app.camera_service.detail}, 503)
                        return
                self.reply(app.camera.cameras())
            elif path == '/api/datasets':
                self.reply(list(app.dataset.entries))
            elif path == '/api/dataset':
                self.reply(app.dataset.describe(query['key'][0]))
            elif path == '/api/reference':
                data = app.dataset.first_frame(query['key'][0], int(query['episode'][0]), query['view'][0])
                result = app.upload(data)
                result['source'] = {k: v[0] for k, v in query.items()}
                self.reply(result)
            elif path == '/api/replay/status':
                self.reply(app.replay.status())
            elif path == '/api/evaluation/camera':
                self.reply(app.preview_status())
            elif path == '/api/evaluation/catalog':
                self.reply(app.evaluation.catalog())
            elif path == '/api/evaluation/run':
                self.reply(app.evaluation.summary(app.evaluation.read('runs', query['id'][0])))
            elif path == '/api/evaluation/export':
                self.send_bytes(200, app.evaluation.export_csv(query['id'][0]).encode(), 'text/csv; charset=utf-8', {'Content-Disposition': 'attachment; filename="evaluation.csv"'})
            elif path == '/api/records':
                self.reply(app.history())
            elif path == '/api/frame':
                if not app.preview_enabled:
                    raise ValueError('布局预览已关闭，相机已交给终端评测')
                data, content_type, frame_id = app.camera.frame(query.get('camera', [''])[0])
                self.send_bytes(200, data, content_type, {'X-Frame-Id': frame_id})
            elif path == '/api/preview':
                if not app.preview_enabled:
                    raise ValueError('布局预览已关闭')
                serial = query.get('camera', [''])[0]
                if serial not in [d['serial'] for d in app.camera.cameras()]:
                    raise ValueError('未知相机')
                if app.config['mode'] == 'demo':
                    self.send_bytes(200, app.camera.demo_frame()[0], 'image/png')
                else:
                    # Relay the existing MJPEG stream. Closing the browser releases only this
                    # subscriber, never the shared physical camera owned by the old service.
                    with app.camera.request('/stream?serial=' + quote(serial, safe='')) as response:
                        self.send_response(200)
                        self.send_header('Content-Type', response.headers['Content-Type'])
                        self.send_header('Cache-Control', 'no-store')
                        self.end_headers()
                        try:
                            while True:
                                chunk = response.read1(65536)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, TimeoutError):
                            pass
            elif path.startswith('/assets/'):
                file = app.asset(path)
                self.send_bytes(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
            else:
                files = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/comparison.mjs': 'comparison.mjs', '/replay.mjs': 'replay.mjs', '/evaluation.mjs': 'evaluation.mjs'}
                if path not in files:
                    self.reply({'error': 'Not found'}, 404)
                    return
                file = ROOT / 'frontend' / files[path]
                self.send_bytes(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] + '; charset=utf-8')

        def do_POST(self):
            try:
                # Local-only service: reject browser cross-origin writes, including raw uploads.
                origin = self.headers.get('Origin')
                if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                    self.reply({'error': '跨源写入被拒绝'}, 403)
                    return
                if self.path.startswith(('/api/replay/', '/api/evaluation/')):
                    import secrets
                    host = urlsplit('http://' + self.headers.get('Host', '')).hostname
                    allowed = app.config.get('replay', {}).get('allowed_hosts', ['localhost', '127.0.0.1', '::1'])
                    token = self.headers.get('X-Replay-Token', '')
                    if host not in allowed or not secrets.compare_digest(token, app.replay.token):
                        self.reply({'error': '回放请求需要本控制台授权令牌与允许的本机地址'}, 403)
                        return
                size = int(self.headers.get('Content-Length', 0))
                path = urlsplit(self.path).path
                if path == '/api/video-first-frame':
                    if size <= 0 or size > MAX_VIDEO_BYTES:
                        raise ValueError('Episode 视频大小必须在 1 byte～256 MiB')
                    self.connection.settimeout(60)
                    data, source = first_frame(self.rfile, size, app.output)
                    result = app.upload(data)
                    result['source'] = source
                    metadata = app.asset(result['url']).with_suffix('.json')
                    with metadata.open('x') as target:
                        json.dump({'source': source, 'save_timestamp': utc()}, target, indent=2)
                    self.reply(result)
                    return
                if size <= 0 or size > MAX_BODY:
                    raise ValueError('请求体为空或过大')
                raw = self.rfile.read(size)
                path = urlsplit(self.path).path
                if path == '/api/replay/upload':
                    result = app.replay.prepare(raw)
                    result['action_url'] = app.url(result['action_path'])
                    self.reply(result)
                    return
                if path == '/api/upload':
                    self.reply(app.upload(raw))
                    return
                def reject_constant(value):
                    raise ValueError('非法 JSON 数值: ' + value)
                payload = json.loads(raw, parse_constant=reject_constant)
                if not isinstance(payload, dict):
                    raise ValueError('需要 JSON 对象')
                if path == '/api/evaluation/camera/enable':
                    self.reply(app.enable_preview())
                elif path == '/api/evaluation/camera/release':
                    self.reply(app.release_preview())
                elif path == '/api/evaluation/suites':
                    self.reply(app.evaluation.create_suite(payload))
                elif path == '/api/evaluation/runs':
                    self.reply(app.evaluation.create_run(payload))
                elif path == '/api/evaluation/transition':
                    self.reply(app.evaluation.transition(payload))
                elif path == '/api/replay/start':
                    self.reply(app.replay.start(payload.get('job_id'), payload.get('confirmed')))
                elif path == '/api/replay/stop':
                    self.reply(app.replay.stop())
                elif path == '/api/snapshot':
                    if not app.preview_enabled:
                        raise ValueError('先开启布局相机再拍摄')
                    result = app.snapshot.save(payload['camera_id'], payload['task_id'])
                    for frame in result['frames']:
                        frame['url'] = app.url(frame['image_path'])
                    self.reply(result)
                elif path == '/api/idm':
                    self.reply(app.infer(payload))
                elif path == '/api/vla/prepare':
                    self.reply(app.vla.prepare(payload))
                else:
                    self.reply({'error': 'Not found'}, 404)
            except Exception as exc:
                self.handle_error(exc)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(ROOT / 'config/default.json'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8088)
    parser.add_argument('--output-dir', help='Override the results directory (never the source datasets)')
    args = parser.parse_args()
    app = Application(load_config(args.config, args.output_dir))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    server.daemon_threads = True
    print(f'Experiment Console: http://{args.host}:{args.port} ({app.config["mode"]})', flush=True)
    if app.camera_service and app.preview_enabled:
        app.camera_service.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.replay.close()
        if app.camera_service:
            app.camera_service.close()


if __name__ == '__main__':
    main()
