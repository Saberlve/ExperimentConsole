import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.control import ProcessControl


class ControlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ckpt=self.root/'model'; self.ckpt.write_text('checkpoint')
        self.config=self.root/'eval.yaml'; self.config.write_text('config')
        self.profile=dict(cwd=str(self.root),server=[sys.executable,'-c','import time; time.sleep(120)'],
            evaluation=[sys.executable,'-c','import time; time.sleep(120)'],
            validate=[sys.executable,'-c','pass'])
        self.app=SimpleNamespace(output=self.root, config=dict(mode='legacy_http',process_control=dict(port=0,models={'ACT':self.profile},stop=[sys.executable,'-c','print("confirmed stop")'])),
            evaluation=SimpleNamespace(lock=threading.RLock(),read=Mock(return_value=dict(model='ACT',checkpoint=str(self.ckpt),eval_config=str(self.config),revision=2,cursor=0,trials=[dict(status='running')]))),release_preview=Mock())
        self.control=ProcessControl(self.app)
        self.addCleanup(self.control.close)
        self.payload=dict(model='ACT',checkpoint=str(self.ckpt),eval_config=str(self.config),run_id='test',revision=2)

    def test_owned_process_duplicate_and_stop(self):
        self.control.start('server',self.payload)
        self.assertTrue(self.control.active('server'))
        with self.assertRaises(ValueError): self.control.start('server',self.payload)
        self.control.stop_server()
        self.assertFalse(self.control.active('server'))

    def test_stop_persists_latch_and_does_not_start_robot(self):
        self.control.emergency_stop()
        self.assertTrue(ProcessControl(self.app).stopped)
        with self.assertRaises(ValueError): self.control.start('server',self.payload)
        self.control.unlock()
        self.assertFalse(self.control.latch.exists())

    def test_stop_failure_never_reports_confirmation(self):
        self.control.options['stop']=[sys.executable,'-c','raise RuntimeError("unreachable")']
        with self.assertRaisesRegex(ValueError,'停止未确认'): self.control.emergency_stop()
        self.assertTrue(self.control.stopped)

    def test_eval_requires_matching_server_and_revision(self):
        with self.assertRaises(ValueError): self.control.start('evaluation',self.payload)
        self.control.start('server',self.payload)
        with patch('backend.control.socket.create_connection'):
            with self.assertRaises(ValueError): self.control.start('evaluation',dict(self.payload,revision=1))
            self.app.release_preview.assert_not_called()
            self.control.start('evaluation',self.payload)
        self.assertTrue(self.control.active('evaluation'))
        self.app.release_preview.assert_called_once()
        with self.assertRaises(ValueError): self.control.stop_server()
        self.control.emergency_stop()
        self.assertFalse(self.control.active('evaluation'))

    def test_release_failure_prevents_spawn(self):
        self.control.start('server',self.payload)
        self.app.release_preview.side_effect=ValueError('camera busy')
        with patch('backend.control.socket.create_connection'):
            with self.assertRaisesRegex(ValueError,'camera busy'): self.control.start('evaluation',self.payload)
        self.assertFalse(self.control.active('evaluation'))

    def test_unknown_model_and_disabled_instance(self):
        with self.assertRaises(ValueError): self.control.start('server',dict(self.payload,model='shell'))
        self.control.enabled=False
        with self.assertRaises(ValueError): self.control.start('server',self.payload)
