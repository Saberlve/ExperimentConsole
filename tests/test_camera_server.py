"""Recovered camera service tests with fake camera devices; no SDK connections."""
import io
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from PIL import Image
from adapters.camera import CameraAdapter, FirstFrameAdapter
from adapters.realsense import CameraFeed, Device
from backend.camera_server import CAMERAS, PairCapture, make_handler, next_capture_number, open_feeds


class FakeCamera:
    def __init__(self, value):
        self.value, self.connects, self.disconnects = value, 0, 0

    def connect(self):
        self.connects += 1

    def read(self):
        return np.full((24, 32, 3), self.value, dtype=np.uint8)

    def disconnect(self):
        self.disconnects += 1


class CameraServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.feeds = {}
        for index, serial in enumerate(CAMERAS):
            camera = FakeCamera(30 + index*100)
            feed = CameraFeed(Device(serial, 'FAKE', 'test'), 32, 24, 30, camera=camera)
            feed.connect()
            self.feeds[serial] = feed
            with feed.condition:
                self.assertTrue(feed.condition.wait_for(lambda:feed.frame_count>0, timeout=2))

    def tearDown(self):
        for feed in self.feeds.values():
            feed.stop()
        self.temp.cleanup()

    def test_shared_feed_connections_and_shutdown(self):
        for feed in self.feeds.values():
            self.assertEqual(feed.camera.connects, 1)
            self.assertTrue(feed.status()['connected'])
            jpeg, frame_id, stopped = feed.wait_for_frame(0)
            self.assertGreater(frame_id,0)
            self.assertEqual(Image.open(io.BytesIO(jpeg)).size,(32,24))
            feed.stop()
            self.assertEqual(feed.camera.disconnects,1)
            self.assertFalse(feed.thread.is_alive())

    def test_pair_lossless_capture_continues_existing_number(self):
        original = self.output/'20_A.png'
        original.write_bytes(b'preserve')
        result = PairCapture(self.feeds,self.output).capture()
        self.assertEqual(result['number'],21)
        self.assertEqual(original.read_bytes(),b'preserve')
        for index,path in enumerate(result['paths']):
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((0,0)),(30+index*100,)*3)
        self.assertEqual(next_capture_number(self.output),22)

    def test_pair_write_failure_rolls_back_only_new_files(self):
        original=self.output/'1_A.png';original.write_bytes(b'preserve')
        original_open=Path.open
        def failing_open(path,*args,**kwargs):
            if path.name=='2_W.png' and args and args[0]=='xb':
                raise OSError('test write failure')
            return original_open(path,*args,**kwargs)
        with patch.object(Path,'open',new=failing_open),self.assertRaises(OSError):
            PairCapture(self.feeds,self.output).capture()
        self.assertFalse((self.output/'2_A.png').exists())
        self.assertEqual(original.read_bytes(),b'preserve')

    def test_http_compatibility_preview_and_first_frame_adapter(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.feeds,self.output))
        server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            config={'mode':'legacy_http','camera_url':f'http://127.0.0.1:{server.server_port}',
                    'capture_root':str(self.output)}
            adapter=CameraAdapter(config)
            devices=adapter.cameras()
            self.assertEqual(len(devices),2)
            data,mime,sequence=adapter.frame(next(iter(CAMERAS)))
            self.assertEqual(mime,'image/jpeg')
            self.assertGreater(int(sequence),0)
            self.assertEqual(Image.open(io.BytesIO(data)).size,(32,24))
            frames=FirstFrameAdapter(adapter).capture(next(iter(CAMERAS)))
            self.assertEqual(len(frames),2)
            self.assertIsNone(frames[0][2]['capture_timestamp'])
            with urllib.request.urlopen(config['camera_url']+'/health') as response:
                self.assertEqual(json.load(response)['service'],'experiment_console.camera')
            self.assertTrue(all(feed.camera.connects==1 for feed in self.feeds.values()))
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_stopped_camera_cannot_save_a_pair(self):
        next(iter(self.feeds.values())).stop()
        with self.assertRaises(RuntimeError):
            PairCapture(self.feeds,self.output).capture()
        self.assertFalse(list(self.output.glob('*.png')))

    def test_missing_device_does_not_open_any_camera(self):
        with patch('backend.camera_server.discover_devices',return_value=[]),patch('backend.camera_server.CameraFeed') as factory:
            with self.assertRaisesRegex(RuntimeError,'未找到'):
                open_feeds(640,480,30)
            factory.assert_not_called()

    def test_second_connect_failure_releases_first_feed(self):
        devices=[Device(serial,'FAKE','test') for serial in CAMERAS]
        first,second=Mock(),Mock()
        second.connect.side_effect=RuntimeError('missing second device')
        with patch('backend.camera_server.discover_devices',return_value=devices),patch('backend.camera_server.CameraFeed',side_effect=[first,second]):
            with self.assertRaises(RuntimeError):open_feeds(640,480,30)
        first.stop.assert_called_once();second.stop.assert_called_once()

    def test_interrupt_during_startup_releases_already_opened_feeds(self):
        devices = [Device(serial, 'FAKE', 'test') for serial in CAMERAS]
        first, second = Mock(), Mock()
        second.connect.side_effect = KeyboardInterrupt()
        with patch('backend.camera_server.discover_devices', return_value=devices), \
             patch('backend.camera_server.CameraFeed', side_effect=[first, second]):
            with self.assertRaises(KeyboardInterrupt):
                open_feeds(640, 480, 30)
        first.stop.assert_called_once()
        second.stop.assert_called_once()
