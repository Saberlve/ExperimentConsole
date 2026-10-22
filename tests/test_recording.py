"""Actual MP4 encoding, naming collisions, and recording lifecycle without hardware."""
import concurrent.futures
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2

from backend.server import Application, ROOT
from tests.replay_fixtures import action_csv, FakeReplayAdapter


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(dict(mode='demo', output_dir=self.temp.name, datasets={}))
        self.recorder = self.app.recording

    def tearDown(self):
        self.app.replay.close()
        self.recorder.close()
        self.temp.cleanup()

    def wait_for_frames(self, count=3):
        deadline = time.monotonic() + 5
        while self.recorder.status()['frames'] < count and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertGreaterEqual(self.recorder.status()['frames'], count)

    def test_mp4_and_custom_final_name_and_collision(self):
        self.recorder.start(dict(camera_id='demo', name='初始名称'))
        self.wait_for_frames()
        result = self.recorder.stop(dict(name='抓取鸡蛋.mp4'))
        self.assertEqual(result['state'], 'saved')
        self.assertEqual(result['last']['name'], '抓取鸡蛋.mp4')
        saved = Path(result['last']['path'])
        original = saved.read_bytes()
        capture = cv2.VideoCapture(str(saved))
        try:
            self.assertTrue(capture.isOpened())
            self.assertTrue(capture.read()[0])
            self.assertGreaterEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 3)
        finally:
            capture.release()
        # An existing numbered video must be preserved as well.
        (saved.parent / '抓取鸡蛋_1.mp4').write_bytes(b'existing')
        self.recorder.start(dict(camera_id='demo', name='抓取鸡蛋'))
        result = self.recorder.stop()
        self.assertEqual(result['last']['name'], '抓取鸡蛋_2.mp4')
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual((saved.parent / '抓取鸡蛋_1.mp4').read_bytes(), b'existing')
        self.assertEqual(len(result['videos']), 3)
        self.assertFalse(list(self.recorder.pending.iterdir()))

    def test_new_service_new_folder_and_release_saves(self):
        second = Application(dict(mode='demo', output_dir=self.temp.name, datasets={}))
        self.assertNotEqual(self.recorder.directory, second.recording.directory)
        self.recorder.start(dict(camera_id='demo', name='release'))
        self.app.release_preview()
        self.assertEqual(self.recorder.status()['state'], 'saved')
        self.assertFalse(self.app.preview_enabled)
        self.assertTrue((self.recorder.directory / 'release.mp4').is_file())
        with self.assertRaisesRegex(ValueError, '开启相机'):
            self.recorder.start(dict(camera_id='demo'))

    def test_validation_and_concurrent_requests(self):
        for name in ('../escape', 'a/b', 'a\\b', '..', 'bad\x00name', 'a' * 181):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.recorder.start(dict(camera_id='demo', name=name))
        self.recorder.start(dict(camera_id='demo', name=''))
        with self.assertRaisesRegex(ValueError, '已有视频'):
            self.recorder.start(dict(camera_id='demo', name='other'))
        with self.assertRaises(ValueError):
            self.recorder.stop(dict(name='../bad'))
        self.assertTrue(self.recorder.status()['active'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.recorder.stop(), range(2)))
        self.assertEqual(results[0]['last'], results[1]['last'])
        self.assertEqual(results[0]['last']['name'], '录像.mp4')
        self.assertEqual(len(list(self.recorder.directory.glob('*.mp4'))), 1)

    def test_camera_error_and_shutdown_finalize_video(self):
        self.recorder.start(dict(camera_id='demo', name='断线'))
        with patch.object(self.app.camera, 'frame', side_effect=ValueError('相机断线')):
            self.recorder.thread.join(timeout=5)
        result = self.recorder.status()
        self.assertEqual(result['state'], 'error')
        self.assertIn('相机断线', result['error'])
        self.assertTrue(Path(result['last']['path']).is_file())
        self.recorder.start(dict(camera_id='demo', name='退出'))
        self.recorder.close()
        self.assertTrue((self.recorder.directory / '退出.mp4').is_file())

    def prepare_replay(self):
        self.app.replay.enabled = True
        adapter = FakeReplayAdapter(ROOT, self.app.config)
        self.app.replay.adapter = adapter
        job = self.app.replay.prepare(action_csv())
        return adapter, job

    def wait_for_replay(self, state):
        deadline = time.monotonic() + 5
        while self.app.replay.status()['status'] != state and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.app.replay.status()['status'], state)

    def test_csv_automatically_records_before_launch_and_saves_on_completion(self):
        adapter, job = self.prepare_replay()
        launch = adapter.launch
        def checked_launch(*args):
            self.assertTrue(self.recorder.status()['active'])
            return launch(*args)
        with patch.object(adapter, 'launch', side_effect=checked_launch):
            for filename in ('自动录制.mp4', '自动录制_1.mp4'):
                self.app.replay.start(job['id'], True, recording_name='自动录制')
                self.wait_for_replay('running')
                self.wait_for_frames()
                adapter.launches[-1].done.set()
                self.app.replay.thread.join(5)
                result = self.app.replay.status()
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['recording']['name'], filename)
                self.assertEqual(self.recorder.status()['state'], 'saved')
                self.assertTrue(Path(result['recording']['path']).is_file())

    def test_csv_stop_or_failure_automatically_saves(self):
        adapter, job = self.prepare_replay()
        for interrupted in (True, False):
            self.app.replay.start(job['id'], True, recording_name='回放')
            self.wait_for_replay('running')
            if interrupted:
                self.app.replay.stop()
            else:
                adapter.launches[-1].returncode = 3
                adapter.launches[-1].done.set()
            self.app.replay.thread.join(5)
            if self.app.replay.stop_thread:
                self.app.replay.stop_thread.join(5)
            self.assertEqual(self.recorder.status()['state'], 'saved')
            self.assertTrue(Path(self.app.replay.status()['recording']['path']).is_file())

    def test_csv_failed_recording_does_not_launch_or_stop_other_recording(self):
        adapter, job = self.prepare_replay()
        with patch.object(self.recorder, 'start', side_effect=ValueError('录像不可用')):
            self.app.replay.start(job['id'], True)
            self.app.replay.thread.join(5)
        self.assertEqual(self.app.replay.status()['status'], 'error')
        self.assertIn('录像不可用', self.app.replay.status()['error'])
        self.assertFalse(adapter.launches)
        self.recorder.start(dict(camera_id='demo', name='已有录像'))
        self.app.replay.start(job['id'], True)
        self.app.replay.thread.join(5)
        self.assertTrue(self.recorder.status()['active'])
        self.assertFalse(adapter.launches)

    def test_csv_cancel_while_preparing_does_not_launch(self):
        import threading
        adapter, job = self.prepare_replay()
        entered = threading.Event()
        def prepare(name, camera_id, cancelled):
            entered.set()
            cancelled.wait(5)
            return None
        with patch.object(self.recorder, 'start_replay', side_effect=prepare):
            self.app.replay.start(job['id'], True)
            self.assertTrue(entered.wait(2))
            self.app.replay.stop()
            self.app.replay.thread.join(5)
        self.assertFalse(adapter.launches)
        self.assertFalse(self.recorder.status()['active'])

    def test_csv_automatically_enables_preview_and_selects_camera(self):
        self.app.preview_enabled = False
        adapter, job = self.prepare_replay()
        self.app.replay.start(job['id'], True, recording_name='自动连接')
        self.wait_for_replay('running')
        self.assertTrue(self.app.preview_enabled)
        self.assertTrue(self.recorder.status()['active'])
        self.app.replay.close()
        self.assertEqual(self.recorder.status()['state'], 'saved')


if __name__ == '__main__':
    unittest.main()
