"""Real HTTP/subprocess lifecycle tests using fake feeds, never camera hardware."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request

from adapters.camera import camera_urlopen
from adapters.camera_service import CameraService
from backend.server import Application


class CameraRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.config = dict(mode='legacy_http', output_dir=str(self.root / 'output'),
                           camera_url=f'http://127.0.0.1:{port}', capture_root=str(self.root),
                           camera_preview_on_start=False, datasets={})
        launcher = self.root / 'run_camera.sh'
        launcher.write_text(f'#!{sys.executable}\n' + '''
import time
from pathlib import Path
from backend import camera_server as server
class Feed:
    def status(self):
        return dict(connected=True, error=None)
    def stop(self):
        time.sleep(.2)
        Path(__file__).with_name('disconnected').touch()
server.open_feeds = lambda *args: {serial: Feed() for serial in server.CAMERAS}
server.main()
''')
        launcher.chmod(0o700)

    def launch(self, managed=True):
        service = CameraService(self.config, self.root)
        env = dict(os.environ, PYTHONPATH=str(Path.cwd()))
        if managed:
            env['CONSOLE_CAMERA_OWNER_TOKEN'] = service._owner_token(create=True)
        else:
            env.pop('CONSOLE_CAMERA_OWNER_TOKEN', None)
        process = subprocess.Popen([str(self.root / 'run_camera.sh'), '--port',
                                    self.config['camera_url'].rsplit(':', 1)[1],
                                    '--output-dir', str(self.root)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while True:
            try:
                service._probe()
                break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    self.fail('fake camera failed to start')
                time.sleep(.02)
        return service, process

    def test_restart_can_release_without_ever_enabling_preview(self):
        _, process = self.launch()
        restarted = Application(self.config)
        restarted.camera_service = CameraService(self.config, self.root)
        result = restarted.release_preview()
        self.assertFalse(result['enabled'])
        self.assertFalse(result['service']['owned'])
        self.assertTrue((self.root / 'disconnected').exists())
        self.assertEqual(process.wait(timeout=3), 0)
        # Releasing an already released service is idempotent.
        restarted.release_preview()

    def test_restarted_console_recognizes_managed_preview(self):
        _, process = self.launch()
        recovered = CameraService(self.config, self.root)
        recovered.start()
        recovered.thread.join(3)
        self.assertTrue(recovered.status()['owned'])
        recovered.close()
        self.assertEqual(process.wait(timeout=3), 0)

    def test_manual_service_is_not_stopped_even_with_stale_credential(self):
        CameraService(self.config, self.root)._owner_token(create=True)
        _, process = self.launch(managed=False)
        service = CameraService(self.config, self.root)
        service.close()
        self.assertIsNone(process.poll())
        with self.assertRaises(HTTPError) as exc:
            with camera_urlopen(Request(self.config['camera_url'] + '/api/shutdown',
                                       data=b'{}', headers={'Authorization': 'Bearer invalid'}), timeout=1):
                pass
        self.assertEqual(exc.exception.code, 403)

    def test_sigterm_releases_devices_before_process_exits(self):
        _, process = self.launch()
        process.terminate()
        self.assertEqual(process.wait(timeout=3), 0)
        self.assertTrue((self.root / 'disconnected').exists())


if __name__ == '__main__':
    unittest.main()
