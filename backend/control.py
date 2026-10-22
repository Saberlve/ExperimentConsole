"""Optional, server-configured process controls. Never accepts shell commands from HTTP."""
import os
from http.client import HTTPConnection, HTTPException
import re
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time


def eval_config_root(profile):
    if profile.get('eval_config_root'):
        return Path(profile['eval_config_root']).expanduser().resolve()
    if profile.get('eval_config'):
        return Path(profile['eval_config']).expanduser().resolve().parent
    return None


class ProcessControl:
    def __init__(self, app):
        self.app = app
        self.options = app.config.get('process_control', {})
        self.enabled = bool(self.options) and app.config['mode'] == 'legacy_http'
        self.jobs = {}
        self.lock = threading.RLock()
        self.stop_lock = threading.Lock()
        self.safety_lock = threading.RLock()
        self.stop_confirmed = False
        self.workflow_lock = threading.Lock()
        self.directory = app.output / 'processes'
        self.latch = self.directory / 'STOPPED'
        self.stopping = threading.Event()
        self.stopped = self.latch.exists()
        self.emergency_requested = threading.Event()
        if self.stopped:
            self.emergency_requested.set()
        self.stop_detail = '未执行软件急停'

    def active(self, kind):
        job = self.jobs.get(kind)
        return bool(job and job['process'].poll() is None)

    def eval_config_roots(self):
        return [root for profile in self.options.get('models', {}).values()
                if (root := eval_config_root(profile)) is not None]

    def status(self):
        with self.lock:
            jobs = {}
            for kind, job in self.jobs.items():
                p = job['process']
                with job['log'].open('rb') as f:
                    f.seek(max(0, job['log'].stat().st_size - 8000))
                    tail = f.read().decode(errors='replace')
                jobs[kind] = dict(model=job['model'], checkpoint=job['checkpoint'], run_id=job.get('run_id'),
                    running=p.poll() is None, exit_code=p.poll(), log=tail,
                    log_path=str(job['log']), trial_index=job.get('trial_index'),
                    recovering=job.get('recovering', False), recovered=job.get('recovered', False),
                    recovery_error=job.get('recovery_error', ''))
                if kind == 'server':
                    jobs[kind]['ready'] = self.server_ready()
            models = {}
            for model, profile in self.options.get('models', {}).items():
                root = eval_config_root(profile)
                paths = [str(p.resolve()) for p in sorted(root.rglob('*'))
                         if p.is_file() and p.suffix.lower() in ('.yaml', '.yml')
                         and p.resolve().is_relative_to(root)] if root else []
                models[model] = dict(checkpoint=profile.get('checkpoint', ''),
                    eval_config=profile.get('eval_config', ''), eval_config_root=str(root) if root else '',
                    eval_configs=paths, unavailable=profile.get('unavailable', ''))
            for model, profile in models.items():
                profile['eval_configs'].extend(str(p.resolve()) for p in sorted(
                    (self.app.output / 'eval_configs' / model).rglob('*.yaml')) if p.is_file())
                source = self.options['models'][model]
                root = eval_config_root(source) or Path('.').resolve()
                profile['eval_config_root'] = str(root)
                profile['eval_configs'] = sorted(set(profile['eval_configs']))
                profile['eval_config_catalog'] = []
                for value in profile['eval_configs']:
                    path = Path(value)
                    relative = path.relative_to(root) if path.is_relative_to(root) else None
                    parts = relative.parts if relative else ()
                    # Model/task directories are isolated; legacy shared templates remain available.
                    owner = next((name for name in models if parts and name.lower() == parts[0].lower()), None)
                    if owner and owner != model:
                        continue
                    profile['eval_config_catalog'].append(dict(
                        path=value, directory=str(relative.parent) if relative else '已保存配置',
                        task=parts[1] if owner and len(parts) >= 3 else '',
                        model=owner or model))
                profile['eval_configs'] = [entry['path'] for entry in profile['eval_config_catalog']]
            return dict(enabled=self.enabled, models=models, jobs=jobs, stopped=self.stopped,
                        stopping=self.stopping.is_set(), stop_detail=self.stop_detail)

    def server_ready(self):
        job = self.jobs.get('server')
        if not job or not self.active('server'):
            return False
        if not job.get('listening_seen'):
            with job['log'].open('rb') as stream:
                stream.seek(max(0, job.get('readiness_offset', 0) - 256))
                data = stream.read()
                job['readiness_offset'] = stream.tell()
            job['listening_seen'] = bool(re.search(rb'\blistening on\b', data, re.I))
        return bool(job.get('listening_seen'))

    def _profile(self, model):
        if not self.enabled:
            raise ValueError('此实例未配置进程启动')
        if model not in self.options['models']:
            raise ValueError('请选择已配置的模型')
        return self.options['models'][model]

    def _probe_server(self, address):
        # A bare TCP connect/disconnect produces a WebSocket handshake error.
        # HTTPConnection goes directly to localhost, ignoring proxy variables.
        connection = HTTPConnection(*address, timeout=3)
        try:
            connection.request('GET', '/health')
            response = connection.getresponse()
            if response.status != 200 or response.read(4) != b'ok\n':
                raise OSError(f'模型健康检查响应异常（HTTP {response.status}）')
        finally:
            connection.close()

    def _spawn(self, kind, command, cwd, env, model, checkpoint, folder=None):
        directory = Path(folder) if folder is not None else self.directory
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / (kind + '-' + str(time.time_ns()) + '.log')
        with log.open('wb') as f:
            p = subprocess.Popen(command, cwd=cwd, env=dict(os.environ, **env),
                stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        self.jobs[kind] = dict(process=p, log=log, model=model, checkpoint=checkpoint)

    def _device_environment(self, profile, record):
        """Start the private device owner lazily, on the first EEF evaluation."""
        env = dict(profile.get('env', {}))
        if not profile.get('device_session'):
            return env
        import yaml
        if yaml.safe_load(Path(record['eval_config']).read_text()).get('action_space') != 'eef10':
            return env
        if not self.active('devices'):
            old = self.jobs.get('devices')
            if old:
                self._close_devices()
            server = self.jobs['server']
            directory = Path(tempfile.mkdtemp(prefix='vlarmory-devices-'))
            address = str(directory / 'session.sock')
            values = dict(device_socket=address, owner_pid=os.getpid(), server_pid=server['process'].pid)
            try:
                self._spawn('devices', [x.format(**values) for x in profile['device_session']],
                            profile['cwd'], env, record['model'], record['checkpoint'])
            except BaseException:
                directory.rmdir()
                raise
            self.jobs['devices'].update(socket=address, directory=directory,
                control=profile['device_control'], cwd=profile['cwd'], run_id=record['id'],
                server_pid=server['process'].pid)
        device = self.jobs['devices']
        if device['server_pid'] != self.jobs['server']['process'].pid:
            raise ValueError('设备连接属于另一个模型会话，请先退出原评测会话')
        deadline = time.monotonic() + 8
        while not Path(device['socket']).exists():
            if not self.active('devices') or time.monotonic() >= deadline:
                self._close_devices()
                raise ValueError('评测设备会话启动失败，请查看 devices 日志')
            if self.emergency_requested.is_set():
                self._close_devices()
                raise ValueError('已急停，不会启动评测')
            time.sleep(.05)
        device['run_id'] = record['id']
        env['VLARMORY_DEVICE_SOCKET'] = device['socket']
        return env

    def _device_request(self, action):
        job = self.jobs.get('devices')
        if not job or not self.active('devices'):
            return
        values = dict(device_socket=job['socket'], request=action)
        result = subprocess.run([x.format(**values) for x in job['control']], cwd=job['cwd'],
                                capture_output=True, text=True, timeout=12)
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:] or result.stdout[-2000:])

    def _close_devices(self):
        job = self.jobs.get('devices')
        if not job:
            return
        try:
            self._device_request('shutdown')
            job['process'].wait(timeout=3)
        finally:
            self._kill('devices')
            Path(job['socket']).unlink(missing_ok=True)
            try:
                job['directory'].rmdir()
            except FileNotFoundError:
                pass

    def start(self, kind, payload):
        # Same lock order as camera enable/transition. Emergency stop sets its event first.
        with self.app.evaluation.lock, self.lock:
            profile = self._profile(payload.get('model'))
            if self.stopped or self.stopping.is_set() or self.emergency_requested.is_set():
                raise ValueError('急停已锁定，请先人工检查并解除启动锁定')
            if self.active(kind) or self.active('evaluation') or self.active('reset'):
                raise ValueError('已有进程运行，请先停止')
            if kind == 'evaluation' and self.active('server') and not self.server_ready():
                raise ValueError('模型服务仍在加载：尚未出现 listening on 日志，请等待服务就绪')
            checkpoint = str(Path(payload.get('checkpoint') or profile.get('checkpoint', '')).expanduser().resolve())
            if not Path(checkpoint).is_file():
                raise ValueError('请选择存在的模型 checkpoint 文件')
            values = dict(checkpoint=checkpoint)
            config = None
            server_uses_run_config = any('{eval_config}' in token for token in
                                         profile.get('server', []) + profile.get('validate_checkpoint', []))
            uses_run_config = any('{eval_config}' in token for token in
                                  profile.get(kind, []) + profile.get('validate_checkpoint', []))
            if uses_run_config:
                if not payload.get('run_id'):
                    raise ValueError('请先保存并加载评测轮次，模型服务使用该轮次的评测 YAML')
                selected = self.app.evaluation.read('runs', payload['run_id'])
                if (selected['model'].lower() != payload['model'].lower()
                        or str(Path(selected['checkpoint']).resolve()) != checkpoint):
                    raise ValueError('当前评测轮次的模型或 checkpoint 不一致')
                if kind == 'server' and 'eval_config_content' in payload:
                    if selected.get('revision') != payload.get('revision'):
                        raise ValueError('评测配置已更新，请刷新后再启动服务')
                    selected = self.app.evaluation.update_run_config(selected, payload['eval_config_content'])
                config = Path(selected.get('eval_config', '')).expanduser().resolve()
                if not config.is_file():
                    raise ValueError('当前轮次的评测 YAML 不存在')
                requested = payload.get('eval_config')
                if requested and Path(requested).expanduser().resolve() != config:
                    raise ValueError('请选择当前轮次已保存的评测 YAML')
                import yaml
                config_value = yaml.safe_load(config.read_text())
                if server_uses_run_config and (not isinstance(config_value, dict) or 'deployment_profile' not in config_value or config_value.get('action_space') not in ('eef10', 'joint')):
                    raise ValueError('当前轮次配置需要 deployment_profile 和 action_space（eef10 / joint），请补充后重启模型服务')
                values['eval_config'] = str(config)
            # Checkpoint preflight belongs to the model-server session, not
            # each layout/client launched against that same server.
            if kind == 'server' and profile.get('validate_checkpoint'):
                result = subprocess.run([x.format(**values) for x in profile['validate_checkpoint']], cwd=profile['cwd'], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise ValueError(result.stderr[-2000:] or result.stdout[-2000:])
            if kind == 'evaluation':
                if not profile.get('evaluation'):
                    raise ValueError(profile.get('unavailable') or '未配置评测入口')
                server = self.jobs.get('server', {})
                if not self.active('server') or server.get('model') != payload['model'] or server.get('checkpoint') != checkpoint:
                    raise ValueError('请先启动同一模型与 checkpoint 的服务')
                if server.get('eval_config'):
                    import yaml
                    current_config = config or Path(payload.get('eval_config', '')).expanduser().resolve()
                    current = yaml.safe_load(current_config.read_text())
                    if str(current_config) != server['eval_config'] or current.get('deployment_profile') != server.get('deployment_profile') or current.get('action_space') != server.get('action_space'):
                        raise ValueError('评测 YAML、deployment_profile 或 action_space 已修改，请重启模型服务使两端使用同一份配置')
                address = ('127.0.0.1', self.options['port'])
                try:
                    self._probe_server(address)
                except TimeoutError as exc:
                    raise ValueError(f'连接模型服务 {address[0]}:{address[1]} 超时（3 秒）；客户端尚未启动，请检查模型服务进程和端口') from exc
                except (OSError, HTTPException) as exc:
                    raise ValueError(f'连接模型服务 {address[0]}:{address[1]} 失败；客户端尚未启动：{exc}') from exc
                config = Path(payload.get('eval_config') or profile.get('eval_config', '')).expanduser().resolve()
                if not config.is_file():
                    raise ValueError('评测 YAML 不存在')
                run = self.app.evaluation.read('runs', payload.get('run_id'))
                if run['model'].lower() != payload['model'].lower() or str(Path(run['checkpoint']).resolve()) != checkpoint:
                    raise ValueError('当前评测轮次的模型或 checkpoint 不一致')
                if str(Path(run.get('eval_config', '')).resolve()) != str(config):
                    raise ValueError('评测 YAML 与已保存轮次不一致，请新建正确轮次')
                if run['revision'] != payload.get('revision') or run['trials'][run['cursor']]['status'] != 'running':
                    raise ValueError('请先加载当前轮次并标记当前布局开始')
                # A new server job starts a fresh validation session. YAML is
                # still parsed by each client so edits take effect immediately.
                validation_key = (payload['run_id'], str(config))
                validated = server.setdefault('validated_evaluations', set())
                if validation_key not in validated:
                    check = [x.format(eval_config=str(config), checkpoint=checkpoint) for x in profile['validate']]
                    result = subprocess.run(check, cwd=profile['cwd'], capture_output=True, text=True, timeout=10)
                    if result.returncode:
                        raise ValueError(result.stderr[-2000:] or result.stdout[-2000:])
                    validated.add(validation_key)
                self.app.release_preview()
                values['eval_config'] = str(config)
            else:
                if self.active('server'):
                    raise ValueError('请先停止当前模型服务')
                with socket.socket() as probe:
                    # Match asyncio's Unix server socket: closed connections in
                    # TIME_WAIT may be reused, but a live listener still blocks.
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        probe.bind(('0.0.0.0', self.options['port']))
                    except OSError as exc:
                        raise ValueError('模型服务端口已占用；不会接管外部服务') from exc
            if self.stopping.is_set():
                raise ValueError('急停正在执行')
            command = [x.format(**values) for x in profile[kind]]
            folder = self.app.output / 'vla_evaluations/runs' / run['id'] if kind == 'evaluation' and run.get('id') else None
            env = self._device_environment(profile, run) if kind == 'evaluation' else profile.get('env', {})
            if self.stopping.is_set() or self.emergency_requested.is_set():
                raise ValueError('已急停，不会启动评测')
            self._spawn(kind, command, profile['cwd'], env, payload['model'], checkpoint, folder)
            if kind == 'server' and uses_run_config:
                self.jobs[kind].update(run_id=payload['run_id'], eval_config=str(config),
                                      deployment_profile=config_value['deployment_profile'], action_space=config_value['action_space'])
            if kind == 'evaluation':
                self.jobs[kind].update(run_id=payload.get('run_id'), trial_index=run['cursor'])
                job = self.jobs[kind]
                job['monitor'] = threading.Thread(target=self._watch_evaluation, args=(job,), daemon=True)
                job['monitor'].start()
            return self.status()

    def _watch_evaluation(self, job):
        code = job['process'].wait()
        if code == 0 or job.get('intentional_stop') or self.emergency_requested.is_set():
            return
        # A finish/stop operation owns this lock until its result is saved.
        with self.workflow_lock, self.app.evaluation.lock:
            with self.lock:
                if self.jobs.get('evaluation') is not job or job.get('intentional_stop') or self.emergency_requested.is_set():
                    return
                record = self.app.evaluation.read('runs', job['run_id'])
                if record['cursor'] != job['trial_index']:
                    return
                trial = record['trials'][record['cursor']]
                if trial['status'] != 'running':
                    return
                job['recovering'] = True
            errors = []
            try:
                # Emergency stop takes stop_lock before self.lock. Never hold
                # self.lock while waiting for it; workflow/evaluation locks
                # still protect this trial from concurrent starts or edits.
                self._stop_item(record)
            except Exception as exc:
                errors.append(str(exc))
            try:
                with job['log'].open('rb') as stream:
                    stream.seek(max(0, job['log'].stat().st_size - 4000))
                    tail = stream.read().decode(errors='replace')
                trial.setdefault('failed_attempts', []).append(dict(
                    exit_code=code, log_path=str(job['log']), log_tail=tail,
                    started_at=trial.get('started_at'), ended_at=time.time(),
                    stop_error='; '.join(errors)))
                trial.update(status='pending', started_at=None, finished_at=None,
                             start_error=f'评测客户端异常退出（退出码 {code}）；本条已恢复待测，请重新对齐后重试')
                trial.pop('log_path', None)
                record.pop('metrics', None)
                record['revision'] += 1
                self.app.evaluation.write('runs', record)
                self.app.evaluation.save_results(record['id'])
                job['recovered'] = True
                try:
                    if not self.emergency_requested.is_set():
                        self.app.enable_preview()
                except Exception as exc:
                    errors.append('重新开启布局相机失败：' + str(exc))
            except Exception as exc:
                errors.append('保存失败后的评测记录失败：' + str(exc))
            finally:
                job['recovering'] = False
                job['recovery_error'] = '; '.join(errors)

    def _current_run(self, payload):
        record = self.app.evaluation.read('runs', payload.get('run_id'))
        if type(payload.get('revision')) is not int or record['revision'] != payload['revision']:
            raise ValueError('评测记录已更新，请重新加载轮次')
        return record

    def transition_trial(self, payload):
        """Manual recording and resuming a saved run never move the robot."""
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('正在启动、结束或复位，请等待完成')
        try:
            with self.app.evaluation.lock, self.lock:
                if self.active('reset') or self.active('evaluation'):
                    raise ValueError('请先停止网页启动的评测或复位进程')
                record = self.app.evaluation.transition(payload)
                return self.app.evaluation.save_results(record['id'])
        finally:
            self.workflow_lock.release()

    def start_trial(self, payload):
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('正在处理上一条评测，请稍后重试')
        try:
            return self._start_trial(payload)
        finally:
            self.workflow_lock.release()

    def _start_trial(self, payload):
        if payload.get('restart_current'):
            with self.app.evaluation.lock, self.lock:
                record = self._current_run(payload)
                trial = record['trials'][record['cursor']]
                job = self.jobs.get('evaluation', {})
                if (trial['status'] != 'running' or 'eval_config_content' not in payload
                        or job.get('run_id') != record['id'] or job.get('trial_index') != record['cursor']):
                    raise ValueError('只能应用配置并重启当前网页评测')
                if self.stopped or self.stopping.is_set() or self.emergency_requested.is_set():
                    raise ValueError('急停已锁定，请先人工检查并解除启动锁定')
                # Validate edits before stopping the current client.
                from .evaluation import validate_eval_yaml
                validate_eval_yaml(payload['eval_config_content'])
            # Do not hold self.lock while waiting for the controller stop lock.
            self._stop_item(record)
            with self.app.evaluation.lock, self.lock:
                if self.stopped or self.emergency_requested.is_set():
                    raise ValueError('已急停，不会重启评测')
                record = self._current_run(payload)
                trial = record['trials'][record['cursor']]
                trial.setdefault('restarted_attempts', []).append(dict(
                    started_at=trial.get('started_at'), ended_at=time.time(),
                    log_path=trial.get('log_path'),
                    eval_config_content=Path(record['eval_config']).read_text()))
                trial.update(status='pending', started_at=None, finished_at=None)
                trial.pop('log_path', None)
                record['revision'] += 1
                self.app.evaluation.write('runs', record)
                payload = dict(payload, revision=record['revision'])
        with self.app.evaluation.lock, self.lock:
            record = self._current_run(payload)
            profile = self._profile(record['model'])
            if not profile.get('reset'):
                raise ValueError('此模型尚未配置复位命令')
            if self.active('reset') or self.active('evaluation'):
                raise ValueError('已有复位或评测进程运行')
            if self.active('server') and not self.server_ready():
                raise ValueError('模型服务仍在加载：尚未出现 listening on 日志，请等待服务就绪')
            trial = record['trials'][record['cursor']]
            if trial['status'] != 'pending':
                raise ValueError('当前布局已开始或已记录，请切换下一张')
            if 'eval_config_content' in payload:
                record = self.app.evaluation.update_run_config(record, payload['eval_config_content'])
            record = self.app.evaluation.transition(dict(run_id=record['id'], revision=record['revision'], action='start'))
            try:
                self.start('evaluation', dict(model=record['model'], checkpoint=record['checkpoint'],
                           eval_config=record['eval_config'], run_id=record['id'], revision=record['revision']))
            except Exception as exc:
                record = self.app.evaluation.read('runs', record['id'])
                if not self.active('evaluation'):
                    record['trials'][record['cursor']].update(status='pending', started_at=None, start_error=str(exc))
                    record['revision'] += 1
                    record.pop('metrics', None)
                    self.app.evaluation.write('runs', record)
                raise
            record = self.app.evaluation.read('runs', record['id'])
            record.pop('metrics', None)
            record['trials'][record['cursor']]['log_path'] = str(self.jobs['evaluation']['log'])
            record['revision'] += 1
            self.app.evaluation.write('runs', record)
            return self.app.evaluation.summary(record)

    def _stop_item(self, record):
        with self.lock:
            job = self.jobs.get('evaluation')
            if self.active('reset'):
                raise ValueError('正在复位，请等待完成；需要立即停止请使用急停')
            if self.active('evaluation') and (job.get('run_id') != record['id'] or job.get('trial_index') != record['cursor']):
                raise ValueError('另一个评测样本正在运行，不会停止它')
            owned = job and job.get('run_id') == record['id'] and job.get('trial_index') == record['cursor']
            if owned:
                job['intentional_stop'] = True
        if not owned:
            return
        self.stopping.set()
        try:
            with self.stop_lock:
                self._kill('evaluation')
                device_error = None
                try:
                    self._device_request('stop')
                except Exception as exc:
                    self._kill('devices')
                    device_error = exc
                result = subprocess.run(self.options['stop'], capture_output=True, text=True, timeout=8)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1500:] or result.stdout[-1500:])
                self.stop_detail = result.stdout.strip()
                if device_error:
                    raise RuntimeError('评测设备停止或 RGB 释放未确认：' + str(device_error))
        except Exception as exc:
            self.stop_confirmed = False
            self.directory.mkdir(parents=True, exist_ok=True)
            self.latch.write_text('Item stop failed; manual check required\n')
            self.stopped = True
            raise ValueError('停止未确认，请使用实体急停：' + str(exc)) from exc
        finally:
            if not self.emergency_requested.is_set():
                self.stopping.clear()

    def _reset_item(self, record):
        with self.app.evaluation.lock, self.lock:
            profile = self._profile(record['model'])
            if self.stopped or self.stopping.is_set() or self.emergency_requested.is_set():
                raise ValueError('启动已锁定，不能自动复位')
            if self.active('evaluation') or self.active('reset'):
                raise ValueError('评测或复位仍在运行')
            # Keep preview enable blocked until the reset job is registered.
            # This also covers cameras reopened after client failure recovery.
            self.app.release_preview()
            if self.stopped or self.stopping.is_set() or self.emergency_requested.is_set():
                raise ValueError('启动已锁定，不能自动复位')
            command = [x.format(eval_config=record['eval_config']) for x in profile['reset']]
            folder = self.app.output / 'vla_evaluations/runs' / record['id']
            env = self._device_environment(profile, record)
            if self.emergency_requested.is_set():
                raise ValueError('已急停，不会启动复位')
            self._spawn('reset', command, profile['cwd'], env, record['model'], record['checkpoint'], folder)
            self.jobs['reset'].update(run_id=record['id'], trial_index=record['cursor'])
            process = self.jobs['reset']['process']
            record['trials'][record['cursor']]['reset_log_path'] = str(self.jobs['reset']['log'])
        try:
            code = process.wait(timeout=120)
            if code or self.stopped or self.emergency_requested.is_set():
                if code and not self.emergency_requested.is_set():
                    self._stop_item(record)
                raise ValueError('复位未成功，已保存本条结果，请查看复位日志；未切换下一张')
        except subprocess.TimeoutExpired as exc:
            self.emergency_stop()
            raise ValueError('复位超时，已急停并保存本条结果') from exc

    def finish_trial(self, payload, exit_run=False):
        if not self.workflow_lock.acquire(blocking=False):
            raise ValueError('正在结束或复位，请等待完成；需要立即停止请使用急停')
        try:
            return self._finish_trial(payload, exit_run)
        finally:
            self.workflow_lock.release()

    def _finish_trial(self, payload, exit_run=False):
        with self.app.evaluation.lock:
            record = self._current_run(payload)
            trial = record['trials'][record['cursor']]
            result = payload.get('result', 'aborted')
            if result not in ('success', 'failure', 'aborted'):
                raise ValueError('请选择成功、失败或中止')
            failed_pending = trial['status'] == 'pending' and bool(trial.get('failed_attempts'))
            if trial['status'] != 'running' and not failed_pending and not trial.get('reset_error') and not exit_run:
                raise ValueError('当前没有正在评测的布局')
            job = self.jobs.get('evaluation', {})
            failed_record = bool(trial.get('failed_attempts'))
            if not exit_run and not failed_record and (job.get('run_id') != record['id'] or job.get('trial_index') != record['cursor']):
                raise ValueError('当前样本没有由网页启动的 client，不能自动复位')
            # An explicit finish/reset may recover a completed software stop.
            # Clear it before this workflow begins, never after a new stop arrives.
            with self.lock, self.safety_lock:
                if not exit_run and self.stopped and self.stop_confirmed and not self.stopping.is_set():
                    self.unlock()
            stop_error = None
            try:
                self._stop_item(record)
            except ValueError as exc:
                if not self.stopped:
                    raise
                # An unconfirmed controller stop must block movement, but must
                # not prevent the operator from saving the observed outcome.
                stop_error = str(exc)
            if trial['status'] == 'running' or failed_pending:
                record = self.app.evaluation.transition(dict(run_id=record['id'], revision=record['revision'],
                    action='result', result=result, note=payload.get('note', '')))
            if stop_error:
                record['trials'][record['cursor']]['reset_error'] = stop_error
            record.pop('metrics', None)
            record['revision'] += 1
            self.app.evaluation.write('runs', record)
            record = self.app.evaluation.save_results(record['id'])
        if stop_error:
            raise ValueError('本条结果已保存；' + stop_error) from None
        finished = all(t['status'] in ('success', 'failure', 'aborted') for t in record['trials'])
        if exit_run or finished:
            try:
                self.app.release_preview()
            finally:
                self.stop_server()
            return dict(record, evaluation_exited=True)
        try:
            self._reset_item(record)
        except Exception as exc:
            with self.app.evaluation.lock:
                saved = self.app.evaluation.read('runs', record['id'])
                saved['trials'][saved['cursor']].update(reset_error=str(exc),
                    reset_log_path=record['trials'][record['cursor']].get('reset_log_path', ''))
                saved['revision'] += 1
                self.app.evaluation.write('runs', saved)
                self.app.evaluation.save_results(saved['id'])
            raise
        with self.app.evaluation.lock:
            if self.stopped or self.emergency_requested.is_set():
                raise ValueError('已急停，本条结果已保存，未切换下一张')
            record = self.app.evaluation.read('runs', record['id'])
            record['trials'][record['cursor']].pop('reset_error', None)
            record['trials'][record['cursor']]['reset_log_path'] = str(self.jobs['reset']['log'])
            if record['cursor'] + 1 < len(record['trials']):
                record['cursor'] += 1
            record['revision'] += 1
            self.app.evaluation.write('runs', record)
            record = self.app.evaluation.save_results(record['id'])
            if not self.stopped:
                self.app.enable_preview()
            return record

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
            if self.active('evaluation') or self.active('reset'):
                raise ValueError('请先停止评测')
            try:
                self._close_devices()
            finally:
                self._kill('server')
            return self.status()

    def emergency_stop(self):
        if not self.enabled:
            raise ValueError('未配置机器人停止接口')
        with self.safety_lock:
            self.stop_confirmed = False
            request = self.stop_request = object()
            self.emergency_requested.set()
            self.stopping.set()  # Never waits behind model loading / config validation.
        with self.stop_lock:
            self.stopped = True
            errors = []
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.latch.write_text('Operator stop: explicit unlock required\n')
            except Exception as exc:
                # Keep the in-memory lock and stop motion even if persistence fails.
                errors.append('停止锁定标记保存失败：' + str(exc))
            try:
                self._kill('evaluation')
                self._kill('reset')
                # Kill the owned worker before confirming controller state=4:
                # a pending reset must never re-enable motion after confirmation.
                self._kill('devices')
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
                self._kill('reset')
                self._kill('devices')
            # A start may have been past its last check when stop was requested.
            # Confirm controller stop again after the final owned-process kill.
            try:
                result = subprocess.run(self.options['stop'], capture_output=True, text=True, timeout=8)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1500:] or result.stdout[-1500:])
                self.stop_detail = result.stdout.strip()
            except Exception as exc:
                errors.append(str(exc))
            with self.safety_lock:
                if self.stop_request is request:
                    self.stopping.clear()
                    self.stop_confirmed = not errors
            if errors:
                self.stop_detail = '停止未确认，请使用实体急停：' + '; '.join(errors)
                raise ValueError(self.stop_detail)
            return self.status()

    def unlock(self):
        with self.lock, self.safety_lock:
            if self.stopping.is_set() or self.active('evaluation') or self.active('reset'):
                raise ValueError('仍在停止或评测中')
            self.latch.unlink(missing_ok=True)
            self.stopped = False
            self.emergency_requested.clear()
            return self.status()

    def close(self):
        self.emergency_requested.set()
        if self.active('evaluation') or self.active('reset'):
            self.emergency_stop()
        with self.lock:
            try:
                self._close_devices()
            finally:
                self._kill('server')
