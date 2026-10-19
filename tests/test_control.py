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
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')
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

    def test_latch_write_failure_still_stops_client_and_controller(self):
        self.control.start('server', self.payload)
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')
        with patch('backend.control.socket.create_connection'):
            self.control.start('evaluation', self.payload)
        with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
            with patch('backend.control.subprocess.run', return_value=SimpleNamespace(
                    returncode=0, stdout='confirmed stop', stderr='')) as stop:
                with self.assertRaisesRegex(ValueError, 'disk full'):
                    self.control.emergency_stop()
        self.assertFalse(self.control.active('evaluation'))
        self.assertEqual(stop.call_count, 2)
        self.assertTrue(self.control.stopped)
        self.assertTrue(self.control.emergency_requested.is_set())
        self.assertFalse(self.control.stopping.is_set())

    def test_eval_requires_matching_server_and_revision(self):
        with self.assertRaises(ValueError): self.control.start('evaluation',self.payload)
        self.control.start('server',self.payload)
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')
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
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')
        self.app.release_preview.side_effect=ValueError('camera busy')
        with patch('backend.control.socket.create_connection'):
            with self.assertRaisesRegex(ValueError,'camera busy'): self.control.start('evaluation',self.payload)
        self.assertFalse(self.control.active('evaluation'))

    def test_eval_rejects_modified_round_snapshot(self):
        import hashlib
        self.app.evaluation.read.return_value['eval_config_sha256'] = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.control.start('server', self.payload)
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')
        self.config.write_text('modified after round was created')
        with patch('backend.control.socket.create_connection'):
            with self.assertRaisesRegex(ValueError, '本轮保存的配置已被修改'):
                self.control.start('evaluation', self.payload)
        self.assertFalse(self.control.active('evaluation'))

    def test_unknown_model_and_disabled_instance(self):
        with self.assertRaises(ValueError): self.control.start('server',dict(self.payload,model='shell'))
        self.control.enabled=False
        with self.assertRaises(ValueError): self.control.start('server',self.payload)

    def test_loading_model_blocks_evaluation_before_camera_release(self):
        self.control.start('server', self.payload)
        self.assertFalse(self.control.status()['jobs']['server']['ready'])
        with patch('backend.control.socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError, '尚未出现 listening on'):
                self.control.start('evaluation', self.payload)
        connect.assert_not_called()
        self.app.release_preview.assert_not_called()
        self.assertFalse(self.control.active('evaluation'))

    def test_readiness_survives_log_tail_rotation_and_clears_on_exit(self):
        self.control.start('server', self.payload)
        log = self.control.jobs['server']['log']
        log.write_text('server listen')
        self.assertFalse(self.control.server_ready())
        with log.open('a') as stream:stream.write('ing on 127.0.0.1:0\n' + 'later output\n' * 1000)
        self.assertTrue(self.control.status()['jobs']['server']['ready'])
        log.write_text('later output only')
        self.assertTrue(self.control.server_ready())
        self.control.stop_server()
        self.assertFalse(self.control.server_ready())

    def test_policy_port_timeout_identifies_failed_stage(self):
        self.control.start('server', self.payload)
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')
        with patch('backend.control.socket.create_connection', side_effect=TimeoutError('timed out')):
            with self.assertRaisesRegex(ValueError, '连接模型服务 127.0.0.1:0 超时'):
                self.control.start('evaluation', self.payload)
        self.app.release_preview.assert_not_called()
        self.assertFalse(self.control.active('evaluation'))


class TrialLifecycleTest(unittest.TestCase):
    """Exercise real record persistence with fake owned processes only."""
    def setUp(self):
        from backend.server import Application
        from tests.test_console import png
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.events=self.root/'events'
        def event(name):
            with self.events.open('a') as stream:stream.write(name+'\n')
        def command(name, sleep=False):
            code='import time\nwith open('+repr(str(self.events))+',"a") as f: f.write('+repr(name+'\n')+')\n'
            if sleep:code+='time.sleep(120)\n'
            return [sys.executable,'-u','-c',code]
        self.command=command
        self.profile=dict(cwd=str(self.root),env={},server=command('server',True),evaluation=command('client',True),
                          reset=command('reset'),validate=[sys.executable,'-c','pass'])
        self.app=Application(dict(mode='legacy_http',output_dir=str(self.root),capture_root=str(self.root/'captures'),
            camera_url='http://127.0.0.1:1',camera_autostart=False,camera_preview_on_start=False,datasets={},
            process_control=dict(models={'ACT':self.profile},port=0,stop=command('stop'))))
        self.control=self.app.control;self.addCleanup(self.control.close)
        self.app.release_preview=Mock(side_effect=lambda:event('release'))
        self.app.enable_preview=Mock(side_effect=lambda:event('camera'))
        image=self.app.upload(png());suite=self.app.evaluation.create_suite(dict(task='egg_pick',name='layouts',
            layouts=[dict(url=image['url'],label='one'),dict(url=image['url'],label='two')]))
        weight=self.root/'checkpoint';weight.write_text('fixture')
        self.run=self.app.evaluation.create_run(dict(suite_id=suite['id'],model='ACT',checkpoint=str(weight),round='r1',eval_config_content='fps: 15\n'))
        self.control.start('server',dict(model='ACT',checkpoint=str(weight)))
        self.control.jobs['server']['log'].write_text('server listening on 127.0.0.1:0\n')

    def start(self):
        with patch('backend.control.socket.create_connection'):
            self.run=self.control.start_trial(dict(run_id=self.run['id'],revision=self.run['revision']))

    def finish(self,exit_run=False):
        return self.control.finish_trial(dict(run_id=self.run['id'],revision=self.run['revision'],result='success'),exit_run=exit_run)

    def test_end_saves_resets_advances_and_connects_camera(self):
        import json
        self.start();self.assertTrue(self.control.active('evaluation'))
        saved=self.finish();self.assertEqual(saved['cursor'],1)
        self.assertEqual(saved['trials'][0]['status'],'success')
        self.assertEqual(saved['trials'][1]['status'],'pending')
        self.assertFalse(self.control.stopped)
        events=self.events.read_text().splitlines()
        for a,b in [('release','stop'),('stop','reset'),('reset','camera')]:self.assertLess(events.index(a),events.index(b))
        self.assertEqual(json.loads(Path(saved['result_files']['json']).read_text())['metrics']['success'],1)
        self.assertIn('success',Path(saved['result_files']['csv']).read_text())
        for key in ('log_path','reset_log_path'):self.assertTrue(Path(saved['trials'][0][key]).is_file())

    def test_reset_failure_retains_result_and_allows_retry(self):
        self.start();self.profile['reset']=[sys.executable,'-c','raise RuntimeError("fixture reset failure")']
        with self.assertRaisesRegex(ValueError,'复位未成功'):self.finish()
        self.run=self.app.evaluation.read('runs',self.run['id'])
        self.assertEqual(self.run['cursor'],0);self.assertEqual(self.run['trials'][0]['status'],'success')
        self.assertIn('reset_error',self.run['trials'][0]);self.app.enable_preview.assert_not_called()
        self.assertTrue(Path(self.run['result_files']['json']).is_file())
        self.profile['reset']=self.command('reset')
        retry=self.finish();self.assertEqual(retry['cursor'],1);self.assertNotIn('reset_error',retry['trials'][0])

    def test_exit_stops_and_saves_without_reset_or_advance(self):
        self.start();saved=self.finish(exit_run=True)
        self.assertFalse(self.control.active('evaluation'));self.assertEqual(saved['cursor'],0)
        self.assertNotIn('reset',self.events.read_text().splitlines());self.app.enable_preview.assert_not_called()
        self.assertTrue(Path(saved['result_files']['json']).is_file())

    def test_camera_release_failure_never_starts_client(self):
        self.app.release_preview.side_effect=ValueError('camera still in use')
        with self.assertRaisesRegex(ValueError,'camera still in use'):self.start()
        self.assertFalse(self.control.active('evaluation'))
        self.assertEqual(self.app.evaluation.read('runs',self.run['id'])['trials'][0]['status'],'pending')

    def test_exit_reload_advance_and_start_remaining_trial(self):
        self.start()
        saved = self.finish(exit_run=True)
        self.run = self.control.transition_trial(dict(run_id=saved['id'], revision=saved['revision'], action='next'))
        self.assertEqual(self.run['cursor'], 1)
        self.assertEqual(self.run['trials'][0]['status'], 'success')
        self.assertNotIn('reset', self.events.read_text().splitlines())
        self.start()
        self.assertEqual(self.run['trials'][1]['status'], 'running')

    def test_manual_transition_cannot_interrupt_owned_workflow(self):
        self.start()
        payload = dict(run_id=self.run['id'], revision=self.run['revision'], action='result', result='success')
        with self.assertRaisesRegex(ValueError, '请先停止'):
            self.control.transition_trial(payload)
        with self.control.workflow_lock:
            with self.assertRaisesRegex(ValueError, '请等待完成'):
                self.control.transition_trial(payload)
        self.assertEqual(self.app.evaluation.read('runs', self.run['id'])['trials'][0]['status'], 'running')

    def test_emergency_during_reset_prevents_next_and_camera(self):
        self.start();self.profile['reset']=self.command('reset',True)
        entered=threading.Event();errors=[];original=self.control._spawn
        def spawn(*args,**kw):
            out=original(*args,**kw)
            if args[0]=='reset':entered.set()
            return out
        def end():
            try:self.finish()
            except Exception as exc:errors.append(exc)
        with patch.object(self.control,'_spawn',side_effect=spawn):
            thread=threading.Thread(target=end);thread.start()
            self.assertTrue(entered.wait(5));self.control.emergency_stop();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertTrue(errors);self.assertTrue(self.control.stopped)
        self.assertFalse(self.control.active('reset'));self.app.enable_preview.assert_not_called()
        self.assertEqual(self.app.evaluation.read('runs',self.run['id'])['cursor'],0)
