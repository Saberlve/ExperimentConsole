"""Use the console-owned xArm replay implementation; hardware calls are explicit only."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


class LegacyReplayAdapter:
    def __init__(self, console_root, config):
        self.root = Path(console_root).resolve()
        options = config.get('replay', {})
        self.script = self.root / 'backend/robot_replay.py'
        self.python = options.get('python') or os.environ.get('REPLAY_PYTHON') or sys.executable
        self._module = None

    def module(self):
        if self._module is None:
            if not self.script.is_file():
                raise ValueError(f'找不到控制台回放实现：{self.script}；请恢复 experiment_console/backend/robot_replay.py')
            spec = importlib.util.spec_from_file_location('console_owned_replay', self.script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._module = module
        return self._module

    def command(self, path, fps, ip=None):
        command = [self.python, '-u', str(self.script), str(path), '--format', 'tcp-rot6d-m', '--fps', str(fps)]
        return command + (['--ip', ip] if ip else ['--dry-run'])

    def dry_run(self, path, fps):
        result = subprocess.run(self.command(path, fps), capture_output=True, text=True, timeout=30)
        log = result.stdout + result.stderr
        if result.returncode != 0:
            raise ValueError('回放程序离线校验失败：' + log[-4000:])
        return log

    def launch(self, path, fps, ip, wait_for_recording=False):
        # Operator confirmed execution: run the replay script directly, no preflight gate.
        command = self.command(path, fps, ip)
        if wait_for_recording:
            command.append('--wait-for-recording')
        return subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE,
            stdin=subprocess.PIPE if wait_for_recording else None,
            stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)

    def stop(self, ip):
        from xarm.wrapper import XArmAPI
        arm = XArmAPI(ip)
        try:
            code = arm.set_state(4)
            if code:
                raise RuntimeError(f'停止指令失败：code={code}')
            code, state = arm.get_state()
            if code or state != 4:
                raise RuntimeError(f'停止状态未确认：state={state}, code={code}')
            return '控制器已确认 state=4（停止）'
        finally:
            arm.disconnect()
