"""Unified VLArmory workbench: training configurations and robot evaluation."""
import argparse
import json
from pathlib import Path
import re
import secrets
import sys
import uuid
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

CONSOLE = Path(__file__).resolve().parents[1]
FRONTEND = CONSOLE / 'frontend'
from backend.training import Manager, check_id
from backend.checkpoints import catalog
from backend.server import Application, load_config, _make_console_handler


def index_page():
    template = (FRONTEND / 'index.html').read_text()
    training = (FRONTEND / 'training.html').read_text()
    return template.replace('<!-- TRAINING_PAGE -->', training).encode('utf-8')


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
                    checkpoint_root = app.config.get('checkpoint_root')
                    if checkpoint_root:
                        return self.reply(catalog(root=checkpoint_root))
                    return self.reply(catalog(manager.repo) if manager is not None else catalog(root=app.output / 'Checkpoints'))
                if path == '/api/evaluation/config':
                    requested = parse_qs(parsed.query).get('path', [''])[0]
                    if not requested:
                        raise ValueError('请先选择或填写评测 YAML 路径')
                    file = Path(requested).expanduser().resolve()
                    roots = [(app.output / 'eval_configs').resolve(), (app.evaluation.root / 'runs').resolve()]
                    if manager is not None:
                        roots.append(manager.repo.resolve())
                    roots.extend(app.control.eval_config_roots())
                    if file.suffix.lower() not in ('.yaml', '.yml') or not any(file.is_relative_to(root) for root in roots):
                        raise ValueError('请选择主仓库或已配置评测目录中的 YAML 文件')
                    if file.stat().st_size > 262144:
                        raise ValueError('评测配置文件超过 256 KiB')
                    import yaml
                    content = file.read_text(encoding='utf-8')
                    parsed_config = yaml.safe_load(content)
                    return self.reply(dict(path=str(file), content=content,
                        record_video=parsed_config.get('record_video', app.config.get('vla_record_video', False))
                        if isinstance(parsed_config, dict) else False))
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
            path = urlsplit(self.path).path
            if not path.startswith('/api/training/') and path != '/api/evaluation/config':
                return super().do_POST()
            if not self.local_request(write=True):
                return self.reply({'error': '请求来源或令牌无效，请刷新页面'}, 403)
            try:
                if manager is None and path.startswith('/api/training/'):
                    raise ValueError('尚未配置训练主仓库')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= (1048576 if path == '/api/evaluation/config' else 65536):
                    raise ValueError('请求大小无效')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError('请求必须为对象')
                path = urlsplit(self.path).path
                if path == '/api/evaluation/config':
                    import yaml
                    from backend.checkpoints import canonical_model
                    model = canonical_model(request.get('model'))
                    content = request.get('content')
                    if not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > 262144:
                        raise ValueError('评测 YAML 内容不能为空，且不得超过 256 KiB')
                    try:
                        config = yaml.safe_load(content)
                    except yaml.YAMLError as exc:
                        raise ValueError('YAML 格式错误：' + str(exc)) from exc
                    if not isinstance(config, dict):
                        raise ValueError('评测 YAML 顶层必须是配置映射')
                    folder = app.output / 'eval_configs' / model
                    folder.mkdir(parents=True, exist_ok=True)
                    from backend.evaluation import config_filename
                    filename = config_filename(str(request.get('task') or 'task'), str(request.get('round') or 'round'))
                    file = folder / (filename.removesuffix('.yaml') + '_' + uuid.uuid4().hex[:12] + '.yaml')
                    # Each save is a new version; existing runs keep their original YAML.
                    with file.open('x', encoding='utf-8') as stream:
                        stream.write(content)
                    return self.reply(dict(path=str(file.resolve()), content=content), 201)
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
    import signal
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
        config['main_repo_root'] = str(main_root)
        config['camera_preview_on_start'] = False
    if args.control_config:
        config['process_control'] = json.loads(Path(args.control_config).read_text())
    app = Application(config)
    server = ThreadingHTTPServer((args.host, args.port), make_workbench_handler(app, manager))
    server.daemon_threads = True
    print(f'Experiment Console: http://{args.host}:{server.server_port}', flush=True)
    if app.camera_service and app.preview_enabled:
        app.camera_service.start()
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.browser_session.close()
        app.control.close()
        app.replay.close()
        app.recording.close()
        if app.camera_service:
            app.camera_service.close()


if __name__ == '__main__':
    main()
