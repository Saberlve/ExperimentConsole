import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from backend.replay import ReplayService
from backend.server import Application, ROOT, make_handler
from tests.replay_fixtures import action_csv, FakeReplayAdapter


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {'mode': 'legacy_http', 'camera_autostart': False,
                       'output_dir': self.temp.name, 'replay': {'enabled': True}}
        self.adapter = FakeReplayAdapter(ROOT, self.config)
        self.service = ReplayService(self.config, self.temp.name, ROOT, self.adapter)

    def tearDown(self):
        self.service.close()
        if self.service.thread:
            self.service.thread.join(2)
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('test worker timed out')
            time.sleep(.01)

    def test_prepare_exact_columns_fps_and_original_dry_run(self):
        raw = action_csv()
        job = self.service.prepare(raw)
        self.assertEqual(job['frames'], 3)
        self.assertEqual(job['fps'], 15)
        self.assertEqual(np.load(job['action_path'], allow_pickle=False).shape, (3, 10))
        self.assertEqual(Path(job['source_path']).read_bytes(), raw)
        self.assertIn('[dry-run]', job['dry_run_log'])
        self.assertFalse(self.adapter.launches)
        second = self.service.prepare(raw)
        self.assertNotEqual(job['action_path'], second['action_path'])

    def test_invalid_data_never_starts_hardware(self):
        invalid = [b'bad,data\n1,2\n3,4\n', action_csv({1:{'timestamp_s':0}}),
                   action_csv({2:{'timestamp_s':1}}), action_csv({1:{'tcp_x_m':'nan'}}),
                   action_csv({1:{'feedback_closure':1.1}}), action_csv({1:{'tcp_z_m':-.01}}),
                   action_csv({1:{'tcp_x_m':.5}}), action_csv({1:{'r11':0,'r21':0,'r31':0}})]
        for raw in invalid:
            with self.subTest(raw=raw[:50]), self.assertRaises(ValueError):
                self.service.prepare(raw)
        self.assertFalse(self.adapter.launches)

    def test_explicit_start_single_process_stop_and_logs(self):
        job = self.service.prepare(action_csv())
        with self.assertRaises(ValueError):
            self.service.start(job['id'], False)
        self.service.start(job['id'], True)
        self.wait_for(lambda: self.service.status()['status'] == 'running')
        with self.assertRaises(ValueError):
            self.service.start(job['id'], True)
        self.assertEqual(len(self.adapter.launches), 1)
        self.service.stop()
        self.wait_for(lambda: self.service.status()['status'] == 'stopped')
        self.service.thread.join(2)
        self.assertTrue(self.service.status()['stop_confirmed'])
        self.assertEqual(len(self.adapter.stops), 1)
        self.assertIn('MOCK', '\n'.join(self.service.status()['logs']))
        self.assertTrue(list(Path(job['action_path']).parent.glob('*.log')))

    def test_changed_action_file_blocks_launch(self):
        job = self.service.prepare(action_csv())
        Path(job['action_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '更改'):
            self.service.start(job['id'], True)

    def test_demo_cannot_execute_and_worker_command_is_fixed(self):
        self.service.enabled = False
        with self.assertRaisesRegex(ValueError,'未启用'):
            self.service.start('anything', True)
        with patch('adapters.replay.subprocess.Popen') as popen:
            # Call the real launch implementation with Popen intercepted before any process.
            from adapters.replay import LegacyReplayAdapter
            LegacyReplayAdapter.launch(self.adapter, '/tmp/example.npy', 15, '192.168.1.245')
            args = popen.call_args.args[0]
            self.assertIn('robot_replay.py', ' '.join(args))
            self.assertNotIn('replay_worker', ' '.join(args))
            self.assertNotIn('--dry-run', args)

    def test_stop_failure_is_not_reported_as_stopped(self):
        job = self.service.prepare(action_csv())
        self.service.start(job['id'], True)
        self.wait_for(lambda: self.service.status()['status']=='running')
        with patch.object(self.adapter, 'stop', side_effect=RuntimeError('network failure')):
            self.service.stop()
            self.service.stop_thread.join(2)
        self.assertEqual(self.service.status()['status'], 'stop_error')
        self.assertFalse(self.service.status()['stop_confirmed'])
        with self.assertRaises(ValueError):
            self.service.start(job['id'], True)
        self.service.stop()
        self.service.stop_thread.join(2)
        self.assertEqual(self.service.status()['status'], 'stopped')

    def test_actual_replay_script_delegation_in_dry_run_only(self):
        import subprocess
        job=self.service.prepare(action_csv())
        command=self.adapter.command(job['action_path'],job['fps'])
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('[dry-run]',result.stdout)
        self.assertFalse(self.adapter.launches)

    def test_api_token_origin_and_disabled_execution(self):
        app = Application({'mode':'demo','output_dir':self.temp.name,'datasets':{}})
        app.replay.adapter = self.adapter
        server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(app))
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        def request(path, body, headers):
            req=urllib.request.Request(url+path,data=body,headers=headers)
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status,json.load(response)
            except urllib.error.HTTPError as exc:
                raw=exc.read()
                try: payload=json.loads(raw)
                except ValueError: payload={'error':raw.decode(errors='replace')}
                return exc.code,payload
        try:
            token={'X-Replay-Token':app.replay.token}
            self.assertEqual(request('/api/replay/upload',action_csv(),{})[0],403)
            self.assertEqual(request('/api/replay/upload',action_csv(),{**token,'Origin':'https://foreign.example'})[0],403)
            # Exercise our Host check directly; the environment proxy intercepts foreign Host requests.
            handler=object.__new__(make_handler(app))
            handler.path='/api/replay/upload'
            handler.headers={**token,'Host':'foreign.example'}
            rejected={}
            handler.reply=lambda payload,code=200:rejected.update(code=code,payload=payload)
            handler.do_POST()
            self.assertEqual(rejected['code'],403)
            status, job=request('/api/replay/upload',action_csv(),token)
            self.assertEqual(status,200)
            self.assertIn('/assets/replays/',job['action_url'])
            self.assertEqual(request('/api/replay/start',json.dumps({'job_id':job['id'],'confirmed':True}).encode(),token)[0],400)
        finally:
            server.shutdown();server.server_close();thread.join();app.replay.close()

