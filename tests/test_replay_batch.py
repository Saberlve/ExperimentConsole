"""Exercise batch ordering, exports and cancellation with explicit fake hardware."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.replay import ReplayService
from backend.replay_batch import ReplayBatch
from backend.server import ROOT
from tests.replay_fixtures import FakeReplayAdapter, action_csv
from tests.test_replay_recording import camera_fixture


class ReplayBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source, self.output = root/'csv', root/'export'
        self.source.mkdir()
        for name in ('case_002.csv', 'case_000.csv'):
            (self.source/name).write_bytes(action_csv())
        config = dict(mode='legacy_http', replay={'enabled': True})
        self.adapter = FakeReplayAdapter(ROOT, config)
        camera = camera_fixture()
        camera.cameras.return_value[0]['label'] = '全景'
        camera.cameras.return_value[1]['label'] = '腕部'
        self.service = ReplayService(config, root/'service', ROOT, self.adapter,
                                     camera=camera, recording_ready=lambda: None)
        self.batch = ReplayBatch(self.source, self.output)
        self.batch.request = self.request
        self.stop_first = False
        self.stop_at = None
        self.workers = []

    def tearDown(self):
        self.service.close()
        for thread in self.workers:
            thread.join(2)
        self.temp.cleanup()

    def request(self, route, payload=None):
        if route == '/api/replay/status':
            return self.service.status()
        if route == '/api/evaluation/control':
            return {'jobs': {}}
        if route == '/api/replay/upload':
            return self.service.prepare(payload)
        if route == '/api/replay/stop':
            return self.service.stop()
        if route == '/api/replay/start':
            run = self.service.start(payload['job_id'], payload['confirmed'], payload['record_video'])
            def finish():
                deadline = time.monotonic()+3
                while self.service.status()['status'] != 'running' and time.monotonic() < deadline:
                    time.sleep(.01)
                time.sleep(.15)
                if self.stop_first or len(self.adapter.launches) == self.stop_at:
                    self.service.stop()
                else:
                    self.adapter.launches[-1].done.set()
            thread = threading.Thread(target=finish, daemon=True)
            self.workers.append(thread)
            thread.start()
            return run
        raise AssertionError(route)

    def test_sorted_replays_export_two_named_views_and_never_reexecute(self):
        plan = self.batch.prepare(expected_count=2)
        self.assertEqual([item['name'] for item in plan['items']], ['case_000.csv', 'case_002.csv'])
        self.assertFalse(self.adapter.launches)
        result = self.batch.execute()
        self.assertEqual(result['completed'], 2)
        self.assertEqual(len(self.adapter.launches), 2)
        for item in result['items']:
            for video, original in zip(item['videos'], item['recording']['views']):
                path = Path(video['path'])
                self.assertEqual(path.stem, Path(item['name']).stem)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),
                                 hashlib.sha256(Path(original['path']).read_bytes()).digest())
        self.assertEqual(len(list(self.output.rglob('*.mp4'))), 4)
        with self.assertRaisesRegex(RuntimeError, '避免重复'):
            self.batch.execute()

    def test_stop_first_run_prevents_second_launch(self):
        self.batch.prepare()
        self.stop_first = True
        with self.assertRaisesRegex(RuntimeError, '回放停止或失败'):
            self.batch.execute()
        self.assertEqual(len(self.adapter.launches), 1)
        self.assertEqual(json.loads(self.batch.manifest.read_text())['completed'], 0)

    def test_invalid_csv_prevents_all_execution(self):
        (self.source/'case_002.csv').write_bytes(b'bad\n1\n2\n')
        with self.assertRaises(ValueError):
            self.batch.prepare()
        self.assertFalse(self.adapter.launches)
        self.assertEqual(json.loads(self.batch.manifest.read_text())['status'], 'error')

    def test_explicit_resume_skips_completed_files_and_retries_interrupted_file(self):
        self.batch.prepare()
        self.stop_at = 2
        with self.assertRaises(RuntimeError):
            self.batch.execute()
        plan = json.loads(self.batch.manifest.read_text())
        self.assertEqual(plan['completed'], 1)
        first_video = Path(plan['items'][0]['videos'][0]['path']).read_bytes()
        self.service.thread.join(3)
        self.service.stop_thread.join(3)
        self.stop_at = None
        result = self.batch.execute(resume=True)
        self.assertEqual(result['completed'], 2)
        self.assertEqual(len(self.adapter.launches), 3)
        self.assertEqual(Path(result['items'][0]['videos'][0]['path']).read_bytes(), first_video)
        self.assertEqual(len(result['items'][1]['previous_attempts']), 1)


if __name__ == '__main__':
    unittest.main()
