"""Hardware-free HTTP integration tests with a faithful legacy service fixture."""
import concurrent.futures
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from backend.server import Application, make_handler, load_config


def png(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (32, 24), color).save(output, 'PNG')
    return output.getvalue()


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application({'mode': 'demo', 'output_dir': str(self.root), 'datasets': {}})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.app.recording.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=raw, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as response:
                body = response.read()
                return response.status, json.loads(body) if 'application/json' in response.headers['Content-Type'] else body
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_pages_preview_snapshot_timestamps(self):
        self.assertEqual(self.request('/')[0], 200)
        self.assertIn(b'data-page="idm"', self.request('/')[1])
        self.assertEqual(self.request('/app.js')[0], 200)
        self.assertEqual(self.request('/api/cameras')[1][0]['serial'], 'demo')
        self.assertEqual(self.request('/api/preview?camera=demo')[0], 200)
        status, result = self.request('/api/snapshot', {'camera_id': 'demo', 'task_id': '../task'})
        self.assertEqual(status, 200)
        frame = result['frames'][0]
        self.assertLessEqual(frame['capture_timestamp'], frame['save_timestamp'])
        self.assertGreater(frame['monotonic_timestamp_ns'], 0)
        self.assertIsNone(frame['device_timestamp'])
        with Image.open(frame['image_path']) as image:
            self.assertEqual(image.size, (640, 480))
        self.assertEqual(self.request(frame['url'])[0], 200)
        self.assertEqual(self.request(frame['url'].replace('.png', '.json'))[1]['task_id'], '../task')

    def test_single_preview_frame_and_original_mjpeg_adapter(self):
        from adapters.camera import CameraAdapter
        code, body = self.request('/api/frame?camera=demo')
        self.assertEqual(code, 200)
        self.assertEqual(Image.open(io.BytesIO(body)).size, (640, 480))
        self.assertEqual(self.request('/api/frame?camera=unknown')[0], 400)
        camera = CameraAdapter({'mode': 'legacy_http'})
        jpeg = io.BytesIO()
        Image.new('RGB', (32, 24), 'red').save(jpeg, 'JPEG')
        data = jpeg.getvalue()
        stream = b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data + b'\r\n'
        device = {'serial': 'a', 'connected': True, 'frame_count': 42}
        with patch.object(camera, 'cameras', return_value=[device]), patch.object(camera, 'request', return_value=io.BytesIO(stream)):
            actual, content_type, sequence = camera.frame('a')
            self.assertEqual(actual, data)
            self.assertEqual(content_type, 'image/jpeg')
            self.assertEqual(sequence, '42')
        with patch.object(camera, 'cameras', return_value=[{**device, 'frame_count': 0}]):
            with self.assertRaisesRegex(ValueError, '新帧计数'):
                camera.frame('a')
        with patch.object(camera, 'cameras', return_value=[device]), patch.object(camera, 'request', return_value=io.BytesIO(b'broken')):
            with self.assertRaises(ValueError):
                camera.frame('a')

    def test_episode_video_first_frame(self):
        import cv2
        import numpy as np
        path = self.root / 'fixture.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (32, 24))
        self.assertTrue(writer.isOpened())
        for color in ((0, 0, 240), (240, 0, 0)):
            writer.write(np.full((24, 32, 3), color, dtype=np.uint8))
        writer.release()
        status, result = self.request('/api/video-first-frame', path.read_bytes())
        self.assertEqual(status, 200)
        self.assertEqual(result['source']['frame_index'], 0)
        with Image.open(self.app.asset(result['url'])) as image:
            red, green, blue = image.getpixel((12, 12))
            self.assertGreater(red, 200)
            self.assertLess(blue, 20)
        self.assertEqual(self.request(result['url'].replace('.png', '.json'))[0], 200)
        self.assertFalse(list(self.root.glob('.episode-*')))

        self.assertEqual(self.request('/api/video-first-frame', b'invalid video')[0], 400)
        self.assertFalse(list(self.root.glob('.episode-*')))

    def test_recording_http_auth_and_saved_asset(self):
        headers = {'X-Replay-Token': self.app.replay.token}
        self.assertEqual(self.request('/recording.mjs')[0], 200)
        self.assertEqual(self.request('/api/recording/start', {'camera_id': 'demo'})[0], 403)
        self.assertEqual(self.request('/api/recording/start', {'camera_id': 'demo'},
                                     dict(headers, Origin='https://foreign.example'))[0], 403)
        code, recording = self.request('/api/recording/start', {'camera_id': 'demo', 'name': '测试'}, headers)
        self.assertEqual(code, 200)
        self.assertTrue(recording['active'])
        self.assertTrue(self.request('/api/recording/status')[1]['active'])
        code, result = self.request('/api/recording/stop', {'name': '测试成品'}, headers)
        self.assertEqual(code, 200)
        self.assertEqual(result['last']['name'], '测试成品.mp4')
        code, data = self.request(result['last']['url'])
        self.assertEqual(code, 200)
        self.assertGreater(len(data), 0)

    def test_output_cannot_overlap_dataset(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps({'mode': 'demo', 'output_dir': str(self.root / 'dataset/results'),
            'capture_root': str(self.root / 'legacy'),
            'datasets': {'d': {'root': str(self.root / 'dataset'), 'repo_id': 'local/d'}}}))
        with self.assertRaisesRegex(ValueError, '不得重叠'):
            load_config(config)

    def test_upload_validation_and_path_boundary(self):
        self.assertEqual(self.request('/api/upload', png())[0], 200)
        self.assertEqual(self.request('/api/upload', b'not an image')[0], 400)
        self.assertEqual(self.request('/assets/../outside')[0], 400)
        self.assertEqual(self.request('/api/preview?camera=unknown')[0], 400)
        self.assertEqual(self.request('/api/snapshot', {'camera_id': 'demo', 'task_id': ''})[0], 400)
        self.assertEqual(self.request('/api/upload', png(), {'Origin': 'https://foreign.example'})[0], 403)
        self.assertEqual(self.request('/missing')[0], 404)

    def test_idm_inputs_history_and_vla(self):
        url = self.request('/api/upload', png())[1]['url']
        payload = {'mode': 'mock', 'current_image': url, 'target_image': url, 'state': [0, 1]}
        status, result = self.request('/api/idm', payload)
        self.assertEqual(status, 200)
        self.assertTrue(result['mock'])
        self.assertEqual(result['predicted_action'], [0, 0])
        self.assertIsNone(result['metrics'])
        for changes in ({'target_image': None}, {'state': []}, {'state': [True]}, {'mode': 'real'}, {'state': 'invalid'}):
            self.assertEqual(self.request('/api/idm', {**payload, **changes})[0], 400)
        self.assertEqual(len(self.request('/api/records')[1]), 6)
        self.assertEqual(self.request('/api/records')[1][-1]['status'], 'error')
        self.assertFalse(self.request('/api/vla/prepare', {'task_id': 't', 'camera_id': 'demo'})[1]['inference_available'])

    def test_shared_camera_and_concurrent_no_overwrite(self):
        original = self.app.camera
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: self.request('/api/snapshot', {'task_id': str(i), 'camera_id': 'demo'}), range(6)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(len({r['frames'][0]['image_path'] for _, r in results}), 6)
        self.assertIs(self.app.snapshot.adapter.camera, original)
        self.assertIs(self.app.camera, original)

    def test_dataset_delegation(self):
        # A stub replaces only LeRobot loading; the adapter's episode/view/index selection runs.
        import numpy as np
        class Tensor:
            def detach(self): return self
            def cpu(self): return self
            def numpy(self): return np.ones((3, 24, 32), dtype='float32')
        class Meta:
            episodes = [{'episode_index': 7, 'dataset_from_index': 11}]
            camera_keys = ['observation.images.front']
        class Dataset:
            meta = Meta()
            def __getitem__(self, index):
                self.index = index
                return {'observation.images.front': Tensor()}
        ds = Dataset()
        self.app.dataset.entries['fixture'] = {}
        with patch.object(self.app.dataset, 'load', return_value=ds):
            self.assertEqual(self.request('/api/dataset?key=fixture')[1]['episodes'], [7])
            status, data = self.request('/api/reference?key=fixture&episode=7&view=observation.images.front')
            self.assertEqual(status, 200)
            self.assertEqual(ds.index, 11)
            self.assertEqual(self.request(data['url'])[0], 200)
            self.assertEqual(self.request('/api/reference?key=fixture&episode=8&view=observation.images.front')[0], 400)
            self.assertEqual(self.request('/api/reference?key=fixture&episode=7&view=bad')[0], 400)
        self.assertEqual(self.request('/api/dataset?key=unknown')[0], 400)

    def test_legacy_http_reuses_original_pair_png(self):
        root = self.root / 'legacy'
        root.mkdir()
        data = png('blue')
        class Legacy(BaseHTTPRequestHandler):
            captures = 0
            def log_message(self, *_args): pass
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'devices': [{'serial': 'a', 'suffix': 'A'}, {'serial': 'w', 'suffix': 'W'}]}).encode())
            def do_POST(self):
                type(self).captures += 1
                paths = [root / f'{self.captures}_{suffix}.png' for suffix in ('A', 'W')]
                for p in paths: p.write_bytes(data)
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'number': self.captures, 'paths': list(map(str, paths))}).encode())
        legacy = ThreadingHTTPServer(('127.0.0.1', 0), Legacy)
        thread = threading.Thread(target=legacy.serve_forever, daemon=True); thread.start()
        self.app.camera.config.update(mode='legacy_http', camera_url=f'http://127.0.0.1:{legacy.server_port}', capture_root=str(root))
        try:
            code, result = self.request('/api/snapshot', {'camera_id': 'a', 'task_id': 'real-contract'})
            self.assertEqual(code, 200)
            self.assertEqual(Legacy.captures, 1)
            self.assertTrue(result['pair_capture'])
            self.assertEqual(len(result['frames']), 2)
            for frame in result['frames']:
                self.assertEqual(Path(frame['image_path']).read_bytes(), data)
                self.assertIsNone(frame['capture_timestamp'])
                self.assertIsNone(frame['camera_intrinsics'])
            self.app.camera.config['camera_url'] = 'http://127.0.0.1:1'
            self.assertEqual(self.request('/api/cameras')[0], 503)
        finally:
            legacy.shutdown(); legacy.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
