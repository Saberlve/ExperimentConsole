"""Subprocess ownership tests. No real cameras or launchers are started."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from adapters.camera_service import CameraService


class CameraServiceTests(unittest.TestCase):
    def service(self):
        return CameraService({'camera_url': 'http://127.0.0.1:8766',
                              'capture_root': '/tmp/console-test-frames'}, Path('/tmp/console'))

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
