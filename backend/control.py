"""Optional, server-configured process controls. Never accepts shell commands from HTTP."""
import os
import hashlib
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

    def status(self):
        with self.lock:
            jobs = {}
            for kind, job in self.jobs.items():
                p = job['process']
                with job['log'].open('rb') as f:
                    f.seek(max(0, job['log'].stat().st_size - 8000))
                    tail = f.read().decode(errors='replace')
                jobs[kind] = dict(model=job['model'], checkpoint=job['checkpoint'], run_id=job.get('run_id'),
                    running=p.poll() is None, exit_code=p.poll(), log=tail)
            models = {k:dict(checkpoint=v.get('checkpoint', ''), eval_config=v.get('eval_config', ''),
                            eval_configs=[str(p.resolve()) for p in sorted(Path(v['eval_config']).expanduser().parent.glob('*'))
                                          if p.is_file() and p.suffix.lower() in ('.yaml', '.yml')] if v.get('eval_config') else [],
                            unavailable=v.get('unavailable', '')) for k,v in self.options.get('models', {}).items()}
            for model, profile in models.items():
                profile['eval_configs'].extend(str(p.resolve()) for p in sorted(
                    (self.app.output / 'eval_configs' / model).glob('*.yaml')) if p.is_file())
            return dict(enabled=self.enabled, models=models, jobs=jobs, stopped=self.stopped,
                        stopping=self.stopping.is_set(), stop_detail=self.stop_detail)

    def _profile(self, model):
        if not self.enabled:
            raise ValueError('此实例未配置进程启动')
        if model not in self.options['models']:
            raise ValueError('请选择已配置的模型')
        return self.options['models'][model]

    def _spawn(self, kind, command, cwd, env, model, checkpoint, folder=None):
        directory = Path(folder) if folder is not None else self.directory
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / (kind + '-' + str(time.time_ns()) + '.log')
        with log.open('wb') as f:
            p = subprocess.Popen(command, cwd=cwd, env=dict(os.environ, **env),
                stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        self.jobs[kind] = dict(process=p, log=log, model=model, checkpoint=checkpoint)

    def start(self, kind, payload):
        # Same lock order as camera enable/transition. Emergency stop sets its event first.
        with self.app.evaluation.lock, self.lock:
            profile = self._profile(payload.get('model'))
            if self.stopped or self.stopping.is_set() or self.emergency_requested.is_set():
                raise ValueError('急停已锁定，请先人工检查并解除启动锁定')
            if self.active(kind) or self.active('evaluation') or self.active('reset'):
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
                if run.get('eval_config_sha256') and hashlib.sha256(config.read_bytes()).hexdigest() != run['eval_config_sha256']:
                    raise ValueError('本轮保存的配置已被修改，请新建评测轮次')
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
            folder = self.app.output / 'vla_evaluations/runs' / run['id'] if kind == 'evaluation' and run.get('id') else None
            self._spawn(kind, command, profile['cwd'], profile.get('env', {}), payload['model'], checkpoint, folder)
            if kind == 'evaluation':
                self.jobs[kind].update(run_id=payload.get('run_id'), trial_index=run['cursor'])
            return self.status()

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
        with self.app.evaluation.lock, self.lock:
            record = self._current_run(payload)
            profile = self._profile(record['model'])
            if not profile.get('reset'):
                raise ValueError('此模型尚未配置复位命令')
            if self.active('reset') or self.active('evaluation'):
                raise ValueError('已有复位或评测进程运行')
            trial = record['trials'][record['cursor']]
            if trial['status'] != 'pending':
                raise ValueError('当前布局已开始或已记录，请切换下一张')
            record = self.app.evaluation.transition(dict(run_id=record['id'], revision=record['revision'], action='start'))
            try:
                self.start('evaluation', dict(model=record['model'], checkpoint=record['checkpoint'],
                           eval_config=record['eval_config'], run_id=record['id'], revision=record['revision']))
            except Exception as exc:
                if not self.active('evaluation'):
                    record['trials'][record['cursor']].update(status='pending', started_at=None, start_error=str(exc))
                    record['revision'] += 1
                    record.pop('metrics', None)
                    self.app.evaluation.write('runs', record)
                raise
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
        if not owned:
            return
        self.stopping.set()
        try:
            with self.stop_lock:
                self._kill('evaluation')
                result = subprocess.run(self.options['stop'], capture_output=True, text=True, timeout=8)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1500:] or result.stdout[-1500:])
                self.stop_detail = result.stdout.strip()
        except Exception as exc:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.latch.write_text('Item stop failed; manual check required\n')
            self.stopped = True
            raise ValueError('停止未确认，请使用实体急停：' + str(exc)) from exc
        finally:
            if not self.emergency_requested.is_set():
                self.stopping.clear()

    def _reset_item(self, record):
        with self.lock:
            profile = self._profile(record['model'])
            if self.stopped or self.stopping.is_set() or self.emergency_requested.is_set():
                raise ValueError('启动已锁定，不能自动复位')
            if self.active('evaluation') or self.active('reset'):
                raise ValueError('评测或复位仍在运行')
            command = [x.format(eval_config=record['eval_config']) for x in profile['reset']]
            folder = self.app.output / 'vla_evaluations/runs' / record['id']
            self._spawn('reset', command, profile['cwd'], profile.get('env', {}), record['model'], record['checkpoint'], folder)
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
            if trial['status'] != 'running' and not trial.get('reset_error') and not exit_run:
                raise ValueError('当前没有正在评测的布局')
            job = self.jobs.get('evaluation', {})
            if not exit_run and (job.get('run_id') != record['id'] or job.get('trial_index') != record['cursor']):
                raise ValueError('当前样本没有由网页启动的 client，不能自动复位')
            self._stop_item(record)
            if trial['status'] == 'running':
                record = self.app.evaluation.transition(dict(run_id=record['id'], revision=record['revision'],
                    action='result', result=result, note=payload.get('note', '')))
            record.pop('metrics', None)
            record['revision'] += 1
            self.app.evaluation.write('runs', record)
            record = self.app.evaluation.save_results(record['id'])
        if exit_run:
            self.app.release_preview()
            return record
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
            self._kill('server')
            return self.status()

    def emergency_stop(self):
        if not self.enabled:
            raise ValueError('未配置机器人停止接口')
        self.emergency_requested.set()
        self.stopping.set()  # Never waits behind model loading / config validation.
        with self.stop_lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.latch.write_text('Operator stop: explicit unlock required\n')
            self.stopped = True
            errors = []
            try:
                self._kill('evaluation')
                self._kill('reset')
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
            # A start may have been past its last check when stop was requested.
            # Confirm controller stop again after the final owned-process kill.
            try:
                result = subprocess.run(self.options['stop'], capture_output=True, text=True, timeout=8)
                if result.returncode:
                    raise RuntimeError(result.stderr[-1500:] or result.stdout[-1500:])
                self.stop_detail = result.stdout.strip()
            except Exception as exc:
                errors.append(str(exc))
            self.stopping.clear()
            if errors:
                self.stop_detail = '停止未确认，请使用实体急停：' + '; '.join(errors)
                raise ValueError(self.stop_detail)
            return self.status()

    def unlock(self):
        with self.lock:
            if self.stopping.is_set() or self.active('evaluation') or self.active('reset'):
                raise ValueError('仍在停止或评测中')
            self.latch.unlink(missing_ok=True)
            self.stopped = False
            self.emergency_requested.clear()
            return self.status()

    def close(self):
        if self.active('evaluation') or self.active('reset'):
            self.emergency_stop()
        with self.lock:
            self._kill('server')
