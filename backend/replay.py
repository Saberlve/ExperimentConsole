"""CSV preparation and one-at-a-time, operator-triggered robot replay jobs."""
import csv
import hashlib
import io
import ipaddress
import json
import secrets
import signal
import threading
import uuid
from pathlib import Path
from adapters.camera import utc
from adapters.replay import LegacyReplayAdapter

COLUMNS = ['tcp_x_m', 'tcp_y_m', 'tcp_z_m', 'r11', 'r21', 'r31', 'r12', 'r22', 'r32', 'feedback_closure']
ACTIVE = {'checking', 'starting', 'running', 'stopping'}


class ReplayService:
    def __init__(self, config, output, console_root, adapter=None):
        self.options = config.get('replay', {})
        self.enabled = config['mode'] == 'legacy_http' and self.options.get('enabled', False)
        self.ip = str(ipaddress.ip_address(self.options.get('robot_ip', '192.168.1.245')))
        self.token = secrets.token_urlsafe(32)
        self.output = Path(output) / 'replays'
        self.adapter = adapter or LegacyReplayAdapter(console_root, config)
        self.jobs = {}
        self.lock = threading.RLock()
        self.run = None
        self.process = None
        self.closed = False
        self.stop_event = threading.Event()
        self.thread = None
        self.stop_thread = None

    def capabilities(self):
        return {'enabled': self.enabled, 'robot_ip': self.ip, 'token': self.token,
                'format': 'tcp-rot6d-m'}

    def prepare(self, raw):
        import numpy as np
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        if len(rows) < 3:
            raise ValueError('CSV 至少需要表头与两帧动作')
        header = [value.strip() for value in rows[0]]
        if len(set(header)) != len(header):
            raise ValueError('CSV 表头不能有重复列')
        if not set(COLUMNS + ['timestamp_s']).issubset(header):
            raise ValueError('需要 timestamp_s、tcp_x_m/tcp_y_m/tcp_z_m、r11/r21/r31/r12/r22/r32、feedback_closure 列')
        data = []
        for number, row in enumerate(rows[1:], 2):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise ValueError(f'CSV 第 {number} 行列数不符')
            values = dict(zip(header, row))
            try:
                data.append([float(values[key]) for key in ['timestamp_s'] + COLUMNS])
            except ValueError as exc:
                raise ValueError(f'CSV 第 {number} 行包含非数值动作') from exc
        array = np.asarray(data, dtype=float)
        if array.ndim != 2 or len(array) < 2 or not np.isfinite(array).all():
            raise ValueError('需要至少两帧有限数值，不允许 NaN/Inf')
        times, actions = array[:, 0], array[:, 1:]
        delta = np.diff(times)
        interval = float(np.median(delta))
        if interval <= 0 or not np.all(delta > 0) or not np.allclose(delta, interval, atol=max(2e-6, interval*.001), rtol=0):
            raise ValueError('timestamp_s 必须递增且等间隔；不自动重采样不规则时间戳')
        fps = 1 / interval
        if abs(fps-round(fps)) < .001:
            fps = float(round(fps))
        if not 1 <= fps <= 100:
            raise ValueError('动作频率必须在 1～100 Hz')
        if np.any((actions[:, 9] < 0) | (actions[:, 9] > 1)):
            raise ValueError('feedback_closure 必须在 [0,1]；不静默裁剪或反归一化')
        if np.max(np.abs(actions[:, 3:9])) > 10:
            raise ValueError('6D 旋转数值异常，请检查是否已反归一化')
        module = self.adapter.module()
        converted = module.convert_rot6d_chunk(actions)
        if not np.isfinite(converted).all():
            raise ValueError('转换后的 TCP/旋转包含非有限数值')
        warnings = module.sanity_check(converted)
        if warnings:
            raise ValueError('轨迹数值检查未通过：' + '；'.join(warnings))
        xyz = converted[:, :3]
        if np.any(np.linalg.norm(xyz, axis=1) > self.options.get('max_tcp_radius_mm', 1000)):
            raise ValueError('TCP 目标超出配置的最大半径（默认 1000mm）')
        steps = np.linalg.norm(np.diff(xyz, axis=0), axis=1)
        job_id = uuid.uuid4().hex
        directory = self.output / job_id
        directory.mkdir(parents=True)
        source, target = directory / 'source.csv', directory / 'actions.npy'
        with source.open('xb') as stream:
            stream.write(raw)
        with target.open('xb') as stream:
            np.save(stream, actions, allow_pickle=False)
        dry_log = self.adapter.dry_run(target, fps)
        report = {'id': job_id, 'created_at': utc(), 'format': 'tcp-rot6d-m',
            'frames': len(actions), 'fps': fps, 'duration_s': len(actions)/fps,
            'start_xyz_mm': xyz[0].tolist(), 'end_xyz_mm': xyz[-1].tolist(),
            'xyz_min_mm': xyz.min(axis=0).tolist(), 'xyz_max_mm': xyz.max(axis=0).tolist(),
            'max_step_mm': float(steps.max()), 'gripper_opening_mm': [float(converted[:, 6].min()), float(converted[:, 6].max())],
            'source_sha256': hashlib.sha256(raw).hexdigest(),
            'action_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'columns': COLUMNS, 'dry_run_log': dry_log,
            'source_path': str(source), 'action_path': str(target),
            'semantics': '机械臂基坐标系绝对 TCP，位置 m，旋转矩阵前两列，夹爪 0开/1闭；需要操作人确认',
            'first_action': actions[0].tolist()}
        with (directory / 'preparation.json').open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        with self.lock:
            self.jobs[job_id] = report
        return report

    def _persist(self):
        if self.run:
            path = self.output / self.run['job_id'] / (self.run['id'] + '.json')
            # This run owns its status file; updates do not replace source or prepared data.
            path.write_text(json.dumps(self.run, ensure_ascii=False, indent=2))

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.run)) if self.run else {'status': 'idle', 'logs': []}

    def start(self, job_id, confirmed):
        with self.lock:
            if self.closed or not self.enabled:
                raise ValueError('真实回放未启用；DEMO 模式仅支持转换和离线检查')
            if confirmed is not True:
                raise ValueError('请确认绝对 TCP 坐标、单位与夹爪含义后再执行')
            if self.run and (self.run['status'] in ACTIVE or self.run['status'] == 'stop_error'
                             or self.process is not None and self.process.poll() is None
                             or self.thread and self.thread.is_alive()
                             or self.stop_thread and self.stop_thread.is_alive()):
                raise ValueError('已有回放正在运行或停止中，不允许重复启动')
            if job_id not in self.jobs:
                raise ValueError('请先上传并转换 CSV；服务重启后需要重新上传')
            job = self.jobs[job_id]
            target = Path(job['action_path'])
            if hashlib.sha256(target.read_bytes()).hexdigest() != job['action_sha256']:
                raise ValueError('转换后的动作文件已被更改，请重新上传')
            self.stop_event.clear()
            self.process = None
            self.run = {'id': uuid.uuid4().hex, 'job_id': job_id, 'robot_ip': self.ip,
                'status': 'checking', 'started_at': utc(), 'logs': [], 'stop_confirmed': False}
            self._persist()
            self.thread = threading.Thread(target=self._execute, args=(job,), daemon=True)
            self.thread.start()
            return self.status()

    def _execute(self, job):
        try:
            with self.lock:
                if self.stop_event.is_set():
                    return
                self.run['status'] = 'starting'
                self.process = self.adapter.launch(job['action_path'], job['fps'], self.ip)
                self._persist()
                process = self.process
            log_path = self.output / job['id'] / (self.run['id'] + '.log')
            with log_path.open('x') as log:
                for line in process.stdout:
                    log.write(line); log.flush()
                    with self.lock:
                        self.run['logs'].append(line.rstrip())
                        self.run['logs'] = self.run['logs'][-200:]
                        if line.startswith('[run]') and not self.stop_event.is_set():
                            self.run['status'] = 'running'
                        self._persist()
            process.stdout.close()
            code = process.wait()
            with self.lock:
                self.run['return_code'] = code
                if not self.stop_event.is_set():
                    self.run['status'] = 'completed' if code == 0 else 'error'
                    if code:
                        self.run['error'] = f'原回放程序退出：code={code}；查看日志'
            if code != 0 and not self.stop_event.is_set():
                self.stop()
        except Exception as exc:
            with self.lock:
                if not self.stop_event.is_set():
                    self.run.update(status='error', error=str(exc))
            if self.process is not None and self.process.poll() is None:
                self.stop()
        finally:
            with self.lock:
                self.run['ended_at'] = utc()
                self._persist()

    def stop(self):
        with self.lock:
            if not self.run or self.run['status'] in ('stopped', 'idle'):
                return self.status()
            if self.stop_thread and self.stop_thread.is_alive():
                return self.status()
            self.stop_event.set()
            self.run['status'] = 'stopping'
            process = self.process
            self._persist()
            self.stop_thread = threading.Thread(target=self._stop, args=(process,), daemon=True)
            self.stop_thread.start()
            return self.status()

    def _stop(self, process):
        try:
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT)
            # Separate controller stop also covers the original blocking initial approach.
            detail = self.adapter.stop(self.ip) if process is not None else '已取消，尚未启动回放程序'
            if process is not None:
                try:
                    process.wait(timeout=5)
                except Exception:
                    # Never kill and claim success; preserve the visible unconfirmed status.
                    raise RuntimeError('回放进程尚未退出；请使用机械臂实体停止装置')
            with self.lock:
                self.run.update(status='stopped', stop_confirmed=True, stop_detail=detail)
                self._persist()
        except Exception as exc:
            with self.lock:
                self.run.update(status='stop_error', error='停止未确认：' + str(exc), stop_confirmed=False)
                self._persist()

    def close(self):
        with self.lock:
            self.closed = True
        if self.run and (self.run['status'] in ACTIVE or self.run['status'] == 'stop_error'
                         or self.process is not None and self.process.poll() is None):
            self.stop()
        if self.stop_thread:
            self.stop_thread.join(timeout=6)
