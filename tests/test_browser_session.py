"""Model lifetime follows the last browser lease; no hardware or subprocesses."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.browser_session import BrowserSession


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.running = {'server': True, 'evaluation': False, 'reset': False}
        self.record = dict(id='run', model='ACT', checkpoint='/ckpt', cursor=0, revision=0,
                           trials=[dict(status='pending', note='')])
        control = SimpleNamespace(enabled=True, jobs={'server': dict(model='ACT', checkpoint='/ckpt')},
            active=lambda kind: self.running[kind], stop_server=self.stop_server, emergency_stop=self.stop_robot)
        evaluation = SimpleNamespace(lock=threading.RLock(), read=lambda *_: self.record,
            save_results=lambda *_: self.events.append('save'), transition=self.transition)
        self.app = SimpleNamespace(control=control, evaluation=evaluation,
            release_preview=lambda: self.events.append('release_camera'))
        self.session = BrowserSession(self.app)
        self.addCleanup(self.session.close)

    def stop_server(self):
        self.events.append('stop_server'); self.running['server'] = False

    def stop_robot(self):
        self.events.append('stop_robot'); self.running['evaluation'] = False; self.running['reset'] = False

    def transition(self, payload):
        self.events.append('record_aborted')
        self.record['trials'][0].update(status=payload['result'], note=payload['note'])

    def beat(self, client='tab', closing=False):
        return self.session.heartbeat(dict(client_id=client, run_id='run', cursor=0, note='draft note'), closing)

    def test_refresh_reconnect_cancels_close(self):
        with patch('backend.browser_session.time.monotonic', return_value=0):
            self.beat(); self.beat(closing=True)
        with patch('backend.browser_session.time.monotonic', return_value=10):
            self.beat()
        self.assertFalse(self.session.expire(now=16))
        self.assertEqual(self.session.run_id, 'run')
        self.assertFalse(self.events)

    def test_last_tab_saves_stops_and_discards_session(self):
        with patch('backend.browser_session.time.monotonic', return_value=0):
            self.beat(); self.beat(closing=True)
        self.assertTrue(self.session.expire(now=16))
        self.assertLess(self.events.index('save'), self.events.index('stop_server'))
        self.assertIsNone(self.session.run_id)
        self.assertEqual(self.session.state, 'closed')
        self.assertFalse(self.running['server'])

    def test_other_tab_keeps_model_alive(self):
        with patch('backend.browser_session.time.monotonic', return_value=0):
            self.beat('one'); self.beat('two'); self.beat('one', closing=True)
        self.assertFalse(self.session.expire(now=16))
        self.assertTrue(self.running['server'])

    def test_disconnect_during_trial_stops_robot_and_saves_aborted(self):
        self.running['evaluation'] = True
        self.record['trials'][0]['status'] = 'running'
        with patch('backend.browser_session.time.monotonic', return_value=0):
            self.beat(); self.beat(closing=True)
        self.assertTrue(self.session.expire(now=16))
        self.assertEqual(self.record['trials'][0]['status'], 'aborted')
        self.assertEqual(self.record['trials'][0]['note'], 'draft note')
        self.assertLess(self.events.index('stop_robot'), self.events.index('stop_server'))
        self.assertNotIn('reset', self.events)

    def test_stop_failure_is_visible_and_session_retained(self):
        self.app.control.stop_server = Mock(side_effect=ValueError('stop failed'))
        with patch('backend.browser_session.time.monotonic', return_value=0):
            self.beat(); self.beat(closing=True)
        self.assertFalse(self.session.expire(now=16))
        self.assertEqual(self.session.status()['state'], 'error')
        self.assertIn('stop failed', self.session.status()['error'])
        self.assertEqual(self.session.run_id, 'run')

    def test_missing_close_event_eventually_expires(self):
        with patch('backend.browser_session.time.monotonic', return_value=0):self.beat()
        self.assertTrue(self.session.expire(now=121))

    def test_save_failures_do_not_skip_process_or_camera_cleanup(self):
        for failure in ('snapshot', 'transition', 'final_export'):
            with self.subTest(failure=failure):
                self.events.clear()
                self.running.update(server=True, evaluation=True)
                self.record['trials'][0]['status'] = 'running'
                self.app.evaluation.transition = self.transition
                self.app.evaluation.save_results = Mock(side_effect=(
                    OSError('save failed') if failure == 'snapshot' else
                    [None, OSError('save failed')] if failure == 'final_export' else None))
                if failure == 'transition':
                    self.app.evaluation.transition = Mock(side_effect=OSError('save failed'))
                with patch('backend.browser_session.time.monotonic', return_value=0):
                    self.beat(); self.beat(closing=True)
                self.assertFalse(self.session.expire(now=16))
                self.assertFalse(self.running['evaluation'])
                self.assertFalse(self.running['server'])
                self.assertIn('release_camera', self.events)
                self.assertEqual(self.session.state, 'error')
                self.assertIn('save failed', self.session.error)
                self.assertEqual(self.session.run_id, 'run')

    def test_other_tab_cannot_replace_owned_run_or_its_draft(self):
        other = dict(self.record, id='other', trials=[dict(status='pending', note='')])
        records = {'run': self.record, 'other': other}
        self.app.evaluation.read = lambda _, key: records[key]
        self.app.evaluation.save_results = Mock()
        for kind in ('evaluation', 'reset'):
            with self.subTest(kind=kind):
                self.running.update(server=True, evaluation=False, reset=False)
                self.running[kind] = True
                self.app.control.jobs[kind] = dict(run_id='run', trial_index=0)
                self.record['trials'][0]['status'] = 'running'
                with patch('backend.browser_session.time.monotonic', return_value=0):
                    self.beat('one')
                    self.session.heartbeat(dict(client_id='two', run_id='other', cursor=0, note='wrong note'))
                    self.beat('one', closing=True)
                    self.session.heartbeat(dict(client_id='two', run_id='other', cursor=0, note='wrong note'), closing=True)
                self.assertEqual(self.session.run_id, 'run')
                self.assertTrue(self.session.expire(now=16))
                self.assertEqual(self.record['trials'][0]['status'], 'aborted')
                self.assertEqual(self.record['trials'][0]['note'], 'draft note')
                self.assertEqual(other['trials'][0]['status'], 'pending')
                self.assertTrue(all(call.args == ('run',) for call in self.app.evaluation.save_results.call_args_list))

    def test_expiry_uses_job_started_after_last_heartbeat(self):
        other = dict(self.record, id='other', trials=[dict(status='pending', note='')])
        records = {'run': self.record, 'other': other}
        self.app.evaluation.read = lambda _, key: records[key]
        with patch('backend.browser_session.time.monotonic', return_value=0):
            self.session.heartbeat(dict(client_id='tab', run_id='other', cursor=0, note='wrong note'))
        self.record['trials'][0].update(status='running', note='saved note')
        self.running['evaluation'] = True
        self.app.control.jobs['evaluation'] = dict(run_id='run', trial_index=0)
        self.assertTrue(self.session.expire(now=121))
        self.assertEqual(self.record['trials'][0]['status'], 'aborted')
        self.assertEqual(self.record['trials'][0]['note'], 'saved note')
        self.assertEqual(other['trials'][0]['status'], 'pending')


if __name__ == '__main__':unittest.main()
