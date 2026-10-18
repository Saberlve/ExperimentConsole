"""Simulate moving only console code out of the parent repository."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from backend.server import ROOT
from adapters.replay import LegacyReplayAdapter
from tests.replay_fixtures import action_csv
from backend.replay import ReplayService


class StandaloneTests(unittest.TestCase):
    def test_parent_source_is_not_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'experiment_console';root.mkdir()
            for name in ('backend','adapters','frontend'):
                shutil.copytree(ROOT/name,root/name,ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('run.sh','run_camera.sh'):
                shutil.copy2(ROOT/name,root/name)
            self.assertFalse((root.parent/'examples').exists())
            isolated_check = r"""
from pathlib import Path
import json, sys, threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from backend.server import Application, make_handler
app = Application({'mode':'demo', 'output_dir':str(Path.cwd()/'service-output'), 'camera_preview_on_start':False})
server = ThreadingHTTPServer(('127.0.0.1',0), make_handler(app))
thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
base = 'http://127.0.0.1:'+str(server.server_port)
try:
    for route in ('/', '/app.js', '/workbench.mjs', '/training.mjs', '/workbench.css'):
        with urlopen(base+route) as response: assert response.status==200
    with urlopen(base+'/api/training/catalog') as response:
        assert json.load(response)['integration_available'] is False
    with urlopen(base+'/api/checkpoints') as response:
        assert len(json.load(response)['models'])==4
    assert not any(key=='starVLA' or key.startswith('starVLA.') for key in sys.modules)
finally:
    server.shutdown();server.server_close();thread.join()
"""
            isolated_env = {key:value for key,value in os.environ.items() if key not in ('MAIN_REPO_ROOT','PYTHONPATH')}
            result = subprocess.run([sys.executable,'-c',isolated_check],cwd=root,env=isolated_env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

            result=subprocess.run([sys.executable,'-m','backend.doctor','--hardware'],cwd=root,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(root/'run_camera.sh'),'--help'],cwd=directory,
                env={**os.environ,'CAMERA_PYTHON':sys.executable},capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            service=ReplayService({'mode':'demo'},root/'output',root)
            self.assertEqual(service.adapter.script,root/'backend/robot_replay.py')
            job=service.prepare(action_csv())
            self.assertEqual(job['fps'],15)
            self.assertIn('[dry-run]',job['dry_run_log'])
            command=service.adapter.command(job['action_path'],job['fps'])
            result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('[dry-run]',result.stdout)
            service.close()
