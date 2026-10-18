"""Optional, server-configured process controls. Never accepts shell commands from HTTP."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time


class ProcessControl:
    def __init__(self, app):
        self.app = app
        self.options = app.config.get('process_control', {})
        self.enabled = bool(self.options) and app.config['mode'] == 'legacy_http'
        self.jobs = {}
        self.lock = threading.RLock()
        self.stop_lock = threading.Lock()
        self.directory = app.output / 'processes'
        self.latch = self.directory / 'STOPPED'
        self.stopping = threading.Event()
        self.stopped = self.latch.exists()
        self.stop_detail = '未执行软件急停'

    def active(self, kind):
        job = self.jobs.get(kind)
        return bool(job and job['process'].poll() is None)

    def status(self):
        with self.lock:
            jobs = {}
            for kind, job in self.jobs.items():
                p = job['process']
                with job['log'].open('rb') as f:
                    f.seek(max(0, job['log'].stat().st_size - 8000))
                    tail = f.read().decode(errors='replace')
                jobs[kind] = dict(model=job['model'], checkpoint=job['checkpoint'],
                    running=p.poll() is None, exit_code=p.poll(), log=tail)
            models = {k:dict(checkpoint=v.get('checkpoint', ''), eval_config=v.get('eval_config', ''),
                            unavailable=v.get('unavailable', '')) for k,v in self.options.get('models', {}).items()}
            return dict(enabled=self.enabled, models=models, jobs=jobs, stopped=self.stopped,
                        stopping=self.stopping.is_set(), stop_detail=self.stop_detail)

    def _profile(self, model):
        if not self.enabled:
            raise ValueError('此实例未配置进程启动')
        if model not in self.options['models']:
            raise ValueError('请选择已配置的模型')
        return self.options['models'][model]

    def _spawn(self, kind, command, cwd, env, model, checkpoint):
        self.directory.mkdir(parents=True, exist_ok=True)
        log = self.directory / (kind + '-' + str(time.time_ns()) + '.log')
        with log.open('wb') as f:
            p = subprocess.Popen(command, cwd=cwd, env=dict(os.environ, **env),
                stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        self.jobs[kind] = dict(process=p, log=log, model=model, checkpoint=checkpoint)

    def start(self, kind, payload):
        # Same lock order as camera enable/transition. Emergency stop sets its event first.
        with self.app.evaluation.lock, self.lock:
            profile = self._profile(payload.get('model'))
            if self.stopped or self.stopping.is_set():
                raise ValueError('急停已锁定，请先人工检查并解除启动锁定')
            if self.active(kind) or self.active('evaluation'):
                raise ValueError('已有进程运行，请先停止')
            checkpoint = str(Path(payload.get('checkpoint') or profile.get('checkpoint', '')).expanduser().resolve())
            if not Path(checkpoint).is_file():
                raise ValueError('请选择存在的模型 checkpoint 文件')
            if profile.get('validate_checkpoint'):
                result = subprocess.run([x.format(checkpoint=checkpoint) for x in profile['validate_checkpoint']], cwd=profile['cwd'], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise ValueError(result.stderr[-2000:] or result.stdout[-2000:])
            values = dict(checkpoint=checkpoint)
            if kind == 'evaluation':
                if not profile.get('evaluation'):
                    raise ValueError(profile.get('unavailable') or '未配置评测入口')
                server = self.jobs.get('server', {})
                if not self.active('server') or server.get('model') != payload['model'] or server.get('checkpoint') != checkpoint:
                    raise ValueError('请先启动同一模型与 checkpoint 的服务')
                with socket.create_connection(('127.0.0.1', self.options['port']), timeout=1):
                    pass
                config = Path(payload.get('eval_config') or profile.get('eval_config', '')).expanduser().resolve()
                if not config.is_file():
                    raise ValueError('评测 YAML 不存在')
                # Parent integration validates config semantics without importing robot code.
                check = [x.format(eval_config=str(config), checkpoint=checkpoint) for x in profile['validate']]
                result = subprocess.run(check, cwd=profile['cwd'], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise ValueError(result.stderr[-2000:] or result.stdout[-2000:])
                run = self.app.evaluation.read('runs', payload.get('run_id'))
                if run['model'].lower() != payload['model'].lower() or str(Path(run['checkpoint']).resolve()) != checkpoint:
                    raise ValueError('当前评测轮次的模型或 checkpoint 不一致')
                if str(Path(run.get('eval_config', '')).resolve()) != str(config):
                    raise ValueError('评测 YAML 与已保存轮次不一致，请新建正确轮次')
                if run['revision'] != payload.get('revision') or run['trials'][run['cursor']]['status'] != 'running':
                    raise ValueError('请先加载当前轮次并标记当前布局开始')
                self.app.release_preview()
                values['eval_config'] = str(config)
            else:
                if self.active('server'):
                    raise ValueError('请先停止当前模型服务')
                with socket.socket() as probe:
                    try:
                        probe.bind(('0.0.0.0', self.options['port']))
                    except OSError as exc:
                        raise ValueError('模型服务端口已占用；不会接管外部服务') from exc
            if self.stopping.is_set():
                raise ValueError('急停正在执行')
            command = [x.format(**values) for x in profile[kind]]
            self._spawn(kind, command, profile['cwd'], profile.get('env', {}), payload['model'], checkpoint)
            return self.status()

    def _kill(self, kind):
        job = self.jobs.get(kind)
        if job and job['process'].poll() is None:
            # Kill the entire owned session: eval cleanup must not reset/re-enable the arm.
            try:
                os.killpg(job['process'].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            job['process'].wait(timeout=3)

    def stop_server(self):
        with self.lock:
            if self.active('evaluation'):
                raise ValueError('请先停止评测')
            self._kill('server')
            return self.status()

    def emergency_stop(self):
        if not self.enabled:
            raise ValueError('未配置机器人停止接口')
        self.stopping.set()  # Never waits behind model loading / config validation.
        with self.stop_lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.latch.write_text('Operator stop: explicit unlock required\n')
            self.stopped = True
            errors = []
            try:
                self._kill('evaluation')
            except Exception as exc:
                errors.append(str(exc))
            try:
                result = subprocess.run(self.options['stop'], capture_output=True, text=True, timeout=8)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1500:] or result.stdout[-1500:])
                self.stop_detail = result.stdout.strip()
            except Exception as exc:
                errors.append(str(exc))
            with self.lock:
                self._kill('evaluation')
            self.stopping.clear()
            if errors:
                self.stop_detail = '停止未确认，请使用实体急停：' + '; '.join(errors)
                raise ValueError(self.stop_detail)
            return self.status()

    def unlock(self):
        with self.lock:
            if self.stopping.is_set() or self.active('evaluation'):
                raise ValueError('仍在停止或评测中')
            self.latch.unlink(missing_ok=True)
            self.stopped = False
            return self.status()

    def close(self):
        if self.active('evaluation'):
            self.emergency_stop()
        with self.lock:
            self._kill('server')
