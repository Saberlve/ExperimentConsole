"""Loopback camera requests bypass inherited proxy settings."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from adapters.camera import CameraAdapter, camera_urlopen
from adapters.camera_service import CameraService
from backend.server import app_camera_probe


class LocalCameraHTTPTests(unittest.TestCase):
    def test_loopback_camera_calls_work_with_unreachable_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = dict(output_dir=directory, devices=[dict(serial=s, connected=True, error=None) for s in ('external', 'wrist')])
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_):pass
                def do_GET(self):
                    data=json.dumps(payload).encode()
                    self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                config=dict(mode='legacy_http',camera_url=f'http://127.0.0.1:{server.server_port}',capture_root=directory)
                with patch.dict(os.environ,dict(http_proxy='http://127.0.0.1:1',HTTP_PROXY='http://127.0.0.1:1',no_proxy='',NO_PROXY='')):
                    self.assertEqual(len(CameraAdapter(config).cameras()),2)
                    self.assertTrue(CameraService(config,Path(directory))._probe())
                    with app_camera_probe(config['camera_url']) as response:self.assertEqual(response.status,200)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_remote_camera_preserves_normal_urlopen(self):
        with patch('adapters.camera.urllib.request.urlopen') as request:
            camera_urlopen('http://remote.example/api/cameras',timeout=1)
            request.assert_called_once_with('http://remote.example/api/cameras',timeout=1)


if __name__=='__main__':unittest.main()
