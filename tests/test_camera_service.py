"""Subprocess ownership tests. No real cameras or launchers are started."""
import unittest
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch
from adapters.camera_service import CameraService


class CameraServiceTests(unittest.TestCase):
    def test_release_probe_timeout_names_camera_stage(self):
        from backend.server import Application
        with tempfile.TemporaryDirectory() as directory:
            app = Application(dict(mode='legacy_http', output_dir=directory, datasets={},
                                   camera_url='http://127.0.0.1:8766', camera_autostart=False))
            with patch('backend.server.app_camera_probe', side_effect=TimeoutError('timed out')):
                with self.assertRaisesRegex(ValueError, '释放相机时检查相机服务超时'):
                    app.release_preview()
            self.assertFalse(app.preview_enabled)

    def service(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CameraService({'camera_url': 'http://127.0.0.1:8766',
                              'capture_root': '/tmp/console-test-frames'}, Path(directory.name))

    def test_existing_service_is_reused_and_never_stopped(self):
        service = self.service()
        with patch.object(service, '_probe', return_value=True), patch('subprocess.Popen') as popen:
            service.start(); service.thread.join(2)
            self.assertEqual(service.state, 'ready')
            service.close()
            popen.assert_not_called()

    def test_single_owned_process_starts_with_configured_output_and_closes(self):
        service = self.service()
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = lambda **kwargs: setattr(process.poll, 'return_value', 0)
        with patch.object(service, '_probe', side_effect=[OSError('offline'), True]), \
             patch('subprocess.Popen', return_value=process) as popen:
            service.start(); service.thread.join(2)
            service.start()
            self.assertEqual(service.state, 'ready')
            popen.assert_called_once()
            self.assertEqual(popen.call_args.kwargs['env']['CAMERA_OUTPUT_DIR'], '/tmp/console-test-frames')
            service.close()
            process.send_signal.assert_called_once()
            process.wait.assert_called_once()

    def test_wrong_existing_output_does_not_start_or_kill(self):
        service = self.service()
        with patch.object(service, '_probe', side_effect=ValueError('保存目录不匹配')), patch('subprocess.Popen') as popen:
            service.start(); service.thread.join(2)
            self.assertEqual(service.state, 'error')
            popen.assert_not_called()
            service.close()

    def test_missing_cameras_exit_is_reported_and_can_retry(self):
        service = self.service()
        process = Mock(returncode=1)
        process.poll.return_value = 1
        with patch.object(service, '_probe', side_effect=OSError('offline')), patch('subprocess.Popen', return_value=process) as popen:
            service.start(); service.thread.join(2)
            self.assertEqual(service.state, 'error')
            self.assertIn('相机服务退出', service.detail)
            service.start(); service.thread.join(2)
            self.assertEqual(popen.call_count, 2)
            service.close()

    def test_remote_service_not_started_locally(self):
        service = self.service()
        service.config['camera_url'] = 'http://192.0.2.1:8766'
        with patch.object(service, '_probe', side_effect=OSError('offline')), patch('subprocess.Popen') as popen:
            service.start(); service.thread.join(2)
            self.assertEqual(service.state, 'error')
            popen.assert_not_called()
            service.close()

    def test_release_during_connection_cannot_launch_camera_afterwards(self):
        service = self.service()
        probing, proceed = threading.Event(), threading.Event()
        def probe():
            probing.set()
            proceed.wait(2)
            raise OSError('offline')
        with patch.object(service, '_probe', side_effect=probe), patch('subprocess.Popen') as popen:
            service.start()
            self.assertTrue(probing.wait(2))
            closer = threading.Thread(target=service.close)
            closer.start()
            self.assertTrue(service.stop.wait(2))
            proceed.set()
            closer.join(2)
            self.assertFalse(closer.is_alive())
            self.assertFalse(service.thread.is_alive())
            popen.assert_not_called()
