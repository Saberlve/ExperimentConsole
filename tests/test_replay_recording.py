"""Exercise real MP4 encoding/decoding with synthetic cameras and fake robot processes."""
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import cv2
from PIL import Image

from backend.server import Application, ROOT
from backend.replay_recording import ReplayRecording
from tests.replay_fixtures import action_csv, FakeReplayAdapter


def camera_fixture():
    frames = {}
    for serial, color in [('overview', 'red'), ('wrist', 'blue')]:
        output = io.BytesIO()
        Image.new('RGB', (96, 64), color).save(output, 'PNG')
        frames[serial] = output.getvalue()
    camera = Mock()
    camera.cameras.return_value = [dict(serial=s, label=s, connected=True) for s in frames]
    camera.frame.side_effect = lambda serial: (frames[serial], 'image/png', str(time.monotonic_ns()))
    return camera


class ReplayRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(dict(mode='legacy_http', camera_autostart=False,
            output_dir=self.temp.name, datasets={}, replay={'enabled': True}))
        self.camera = camera_fixture()
        # Isolate the synchronized dual-view recorder from independent single-camera recording.
        self.app.replay.recording = None
        self.app.replay.camera = self.camera
        self.adapter = FakeReplayAdapter(ROOT, self.app.config)
        self.app.replay.adapter = self.adapter
        self.job = self.app.replay.prepare(action_csv())

    def tearDown(self):
        self.app.replay.close()
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic()+5
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('recording worker timed out')
            time.sleep(.01)

    def finish(self, stop=False):
        if stop:
            self.app.replay.stop()
        else:
            self.adapter.launches[-1].done.set()
        self.app.replay.thread.join(5)
        self.assertFalse(self.app.replay.thread.is_alive())
        if self.app.replay.stop_thread:
            self.app.replay.stop_thread.join(2)
        return self.app.replay.status()

    def verify_videos(self, recording):
        self.assertEqual(recording['status'], 'saved', recording)
        self.assertEqual(len(recording['views']), 2)
        self.assertEqual(len({v['frames'] for v in recording['views']}), 1)
        for view in recording['views']:
            video = cv2.VideoCapture(view['path'])
            try:
                self.assertTrue(video.isOpened())
                self.assertEqual(video.get(cv2.CAP_PROP_FPS), 15)
                self.assertEqual(video.get(cv2.CAP_PROP_FRAME_COUNT), view['frames'])
                count = 0
                while True:
                    ok, frame = video.read()
                    if not ok:
                        break
                    count += 1
                    self.assertEqual(frame.shape, (64, 96, 3))
                    channel = 2 if view['serial'] == 'overview' else 0
                    self.assertGreater(frame[:, :, channel].mean(), 200)
                self.assertEqual(count, view['frames'])
                self.assertGreater(count, 1)
            finally:
                video.release()
        metadata = json.loads((Path(recording['directory'])/'recording.json').read_text())
        self.assertEqual(metadata, recording)

    def test_complete_stop_and_repeated_runs_save_both_views_without_overwrite(self):
        directories = []
        for stop in (False, True):
            self.app.replay.start(self.job['id'], True, True)
            self.wait_for(lambda: self.app.replay.status()['status'] == 'running')
            self.assertTrue(self.app.replay.recording_active())
            with self.assertRaisesRegex(ValueError, '录制尚未结束'):
                self.app.release_preview()
            time.sleep(.22)
            status = self.finish(stop)
            self.assertEqual(status['status'], 'stopped' if stop else 'completed')
            self.verify_videos(status['recording'])
            self.assertFalse(self.app.replay.recording_active())
            directories.append(status['recording']['directory'])
        self.assertNotEqual(*directories)
        self.assertTrue(all(Path(path, 'view_1.mp4').is_file() for path in directories))

    def test_disabled_does_not_read_cameras_or_create_videos(self):
        self.app.replay.start(self.job['id'], True)
        self.wait_for(lambda: self.app.replay.status()['status'] == 'running')
        status = self.finish()
        self.assertFalse(status['recording']['enabled'])
        self.camera.cameras.assert_not_called()
        self.assertFalse((Path(self.temp.name)/'idm_recordings').exists())

    def test_approach_is_excluded_and_first_frame_is_fresh(self):
        self.adapter.approach_release = threading.Event()
        self.app.replay.start(self.job['id'], True, True)
        self.wait_for(lambda: bool(self.adapter.launches))
        time.sleep(.22)
        status = self.app.replay.status()
        self.assertEqual(status['recording']['status'], 'waiting')
        self.assertNotIn('started_at', status['recording'])
        self.assertTrue(all(view['frames'] == 0 for view in status['recording']['views']))
        self.assertFalse(self.adapter.launches[-1].recording_ready.is_set())
        # Swap both images while approaching: saved frames must come from after arrival.
        fresh = io.BytesIO()
        Image.new('RGB', (96, 64), 'lime').save(fresh, 'PNG')
        self.camera.frame.side_effect = lambda _: (fresh.getvalue(), 'image/png', 'fresh')
        self.adapter.approach_release.set()
        self.wait_for(lambda: self.app.replay.status()['status'] == 'running')
        time.sleep(.15)
        status = self.finish()
        self.assertEqual(status['recording']['status'], 'saved')
        for view in status['recording']['views']:
            video = cv2.VideoCapture(view['path'])
            try:
                ok, frame = video.read()
                self.assertTrue(ok)
                self.assertGreater(frame[:, :, 1].mean(), 200)
                self.assertLess(frame[:, :, [0, 2]].mean(), 20)
            finally:
                video.release()

    def test_stop_during_approach_cancels_without_recording_frames(self):
        self.adapter.approach_release = threading.Event()
        self.app.replay.start(self.job['id'], True, True)
        self.wait_for(lambda: bool(self.adapter.launches))
        status = self.finish(stop=True)
        self.assertEqual(status['status'], 'stopped')
        self.assertEqual(status['recording']['status'], 'cancelled')
        self.assertNotIn('started_at', status['recording'])
        self.assertTrue(all(view['frames'] == 0 for view in status['recording']['views']))

    def test_camera_failure_after_approach_never_releases_csv_commands(self):
        self.adapter.approach_release = threading.Event()
        self.app.replay.start(self.job['id'], True, True)
        self.wait_for(lambda: bool(self.adapter.launches))
        self.camera.frame.side_effect = ValueError('camera lost before CSV')
        self.adapter.approach_release.set()
        self.app.replay.thread.join(5)
        self.assertFalse(self.app.replay.thread.is_alive())
        status = self.app.replay.status()
        self.assertEqual(status['recording']['status'], 'error')
        self.assertIn('camera lost', status['recording']['error'])
        self.assertFalse(any(line.startswith('[run]') for line in status['logs']))
        self.assertTrue(all(view['frames'] == 0 for view in status['recording']['views']))

    def test_unavailable_camera_blocks_robot_launch(self):
        for scenario in ('disconnected', 'preview_disabled', 'decode_error'):
            with self.subTest(scenario=scenario):
                self.camera = camera_fixture()
                self.app.replay.camera = self.camera
                self.app.preview_enabled = scenario != 'preview_disabled'
                if scenario == 'disconnected':
                    self.camera.cameras.return_value = self.camera.cameras.return_value[:1]
                if scenario == 'decode_error':
                    self.camera.frame.return_value = (b'invalid', 'image/png', '1')
                    self.camera.frame.side_effect = None
                self.app.replay.start(self.job['id'], True, True)
                self.app.replay.thread.join(5)
                status = self.app.replay.status()
                self.assertEqual(status['status'], 'error')
                self.assertEqual(status['recording']['status'], 'error')
                self.assertFalse(self.adapter.launches)

    def test_runtime_camera_failure_keeps_partial_videos_and_visible_error(self):
        self.app.replay.start(self.job['id'], True, True)
        self.wait_for(lambda: self.app.replay.status()['status'] == 'running')
        time.sleep(.15)
        self.camera.frame.side_effect = ValueError('camera disconnected during recording')
        self.wait_for(lambda: self.app.replay.status()['recording']['status'] == 'error')
        status = self.finish()
        self.assertEqual(status['status'], 'completed')
        self.assertIn('camera disconnected', status['recording']['error'])
        for view in status['recording']['views']:
            video = cv2.VideoCapture(view['path'])
            self.assertTrue(video.isOpened())
            self.assertTrue(video.read()[0])
            video.release()

    def test_slow_capture_preserves_common_duration_and_shutdown_flushes_videos(self):
        original = self.camera.frame.side_effect
        def slow_frame(serial):
            time.sleep(.12)
            return original(serial)
        self.camera.frame.side_effect = slow_frame
        self.app.replay.start(self.job['id'], True, True)
        self.wait_for(lambda: self.app.replay.status()['status'] == 'running')
        time.sleep(.35)
        self.app.replay.close()
        status = self.app.replay.status()
        self.assertEqual(status['status'], 'stopped')
        self.verify_videos(status['recording'])
        self.assertGreater(status['recording']['views'][0]['repeated_frames'], 0)
        self.assertAlmostEqual(status['recording']['views'][0]['duration_s'], .35, delta=.17)

    def test_cancel_during_recording_setup_never_launches_robot(self):
        entered, release = threading.Event(), threading.Event()
        original = self.camera.frame.side_effect
        def blocked_frame(serial):
            entered.set()
            release.wait(3)
            return original(serial)
        self.camera.frame.side_effect = blocked_frame
        self.app.replay.start(self.job['id'], True, True)
        self.assertTrue(entered.wait(3))
        self.app.replay.stop()
        release.set()
        self.app.replay.thread.join(5)
        self.assertFalse(self.app.replay.thread.is_alive())
        self.assertFalse(self.adapter.launches)
        self.assertEqual(self.app.replay.status()['recording']['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
