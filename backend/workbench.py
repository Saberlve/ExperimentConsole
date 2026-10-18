"""Unified VLArmory workbench: training configurations and robot evaluation."""
import argparse
import json
from pathlib import Path
import re
import secrets
import sys
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

CONSOLE = Path(__file__).resolve().parents[1]
FRONTEND = CONSOLE / 'frontend'
from backend.training import Manager, check_id
from backend.checkpoints import catalog
from backend.server import Application, load_config, _make_console_handler


def index_page():
    return (FRONTEND / 'index.html').read_bytes()


def make_workbench_handler(app, manager=None):
    base = _make_console_handler(app)
    class Handler(base):
        def local_request(self, write=False):
            host = self.headers.get('Host', '')
            hostname = urlsplit('http://' + host).hostname
            allowed = app.config.get('replay', {}).get('allowed_hosts', ['localhost', '127.0.0.1', '::1'])
            origin = self.headers.get('Origin')
            return hostname in allowed and (not write or (
                (origin is None or origin == 'http://' + host) and
                secrets.compare_digest(self.headers.get('X-Replay-Token', ''), app.replay.token)))

        def do_GET(self):
            if not self.local_request():
                return self.reply({'error': '仅允许配置的本机地址访问'}, 403)
            try:
                parsed = urlsplit(self.path)
                path = parsed.path
                if path == '/':
                    return self.send_bytes(200, index_page(), 'text/html; charset=utf-8')
                if path == '/api/training/catalog':
                    if manager is None:
                        return self.reply(dict(recipes=[], sources=[], experiments=[], integration_available=False,
                                               message='尚未连接训练主仓库；请通过 --main-repo-root 配置 VLArmory 路径。'))
                    return self.reply(dict(manager.catalog(), integration_available=True))
                if path == '/api/checkpoints':
                    return self.reply(catalog(manager.repo) if manager is not None else catalog(root=app.output / 'Checkpoints'))
                match = re.fullmatch(r'/api/training/experiments/([A-Za-z0-9_-]+)/config', path)
                if match:
                    if manager is None:
                        raise ValueError('尚未配置训练主仓库')
                    name = check_id(match[1])
                    return self.send_bytes(200, (manager.root / 'saved' / name / 'config.yaml').read_bytes(), 'text/yaml; charset=utf-8')
                assets = {'/workbench.css': 'workbench.css', '/workbench.mjs': 'workbench.mjs', '/training.mjs': 'training.mjs'}
                if path in assets:
                    file = FRONTEND / assets[path]
                    return self.send_bytes(200, file.read_bytes(), ('text/javascript' if file.suffix == '.mjs' else 'text/css') + '; charset=utf-8')
                return super().do_GET()
            except Exception as exc:
                self.handle_error(exc)

        def do_POST(self):
            if not urlsplit(self.path).path.startswith('/api/training/'):
                return super().do_POST()
            if not self.local_request(write=True):
                return self.reply({'error': '请求来源或令牌无效，请刷新页面'}, 403)
            try:
                if manager is None:
                    raise ValueError('尚未配置训练主仓库')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('请求大小无效')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError('请求必须为对象')
                path = urlsplit(self.path).path
                if path == '/api/training/preview':
                    record, config = manager.compose(request)
                    return self.reply(dict(record=record, config=config))
                if path == '/api/training/save':
                    return self.reply(manager.save(request), 201)
                if path == '/api/training/sources':
                    return self.reply(manager.add_source(request), 201)
                return self.reply({'error': 'Not found'}, 404)
            except Exception as exc:
                self.handle_error(exc)
    return Handler


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(CONSOLE / 'config/default.json'))
    parser.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1', 'localhost'])
    parser.add_argument('--port', type=int, default=8088)
    parser.add_argument('--control-config')
    parser.add_argument('--output-dir')
    parser.add_argument('--main-repo-root', help='Optional VLArmory root for training recipes and checkpoint archives')
    args = parser.parse_args()
    config = load_config(args.config, args.output_dir)
    main_root = args.main_repo_root or config.get('main_repo_root') or os.environ.get('MAIN_REPO_ROOT')
    manager = None
    if main_root:
        main_root = Path(main_root).expanduser().resolve()
        if not (main_root / 'examples/realRobots/xArm7/experiments/recipes.yaml').is_file():
            parser.error('--main-repo-root 缺少训练配方 examples/realRobots/xArm7/experiments/recipes.yaml')
        manager = Manager(main_root)
        config['camera_preview_on_start'] = False
    if args.control_config:
        config['process_control'] = json.loads(Path(args.control_config).read_text())
    app = Application(config)
    server = ThreadingHTTPServer((args.host, args.port), make_workbench_handler(app, manager))
    server.daemon_threads = True
    print(f'Experiment Console: http://{args.host}:{server.server_port}', flush=True)
    if app.camera_service and app.preview_enabled:
        app.camera_service.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.control.close()
        app.replay.close()
        if app.camera_service:
            app.camera_service.close()


if __name__ == '__main__':
    main()
