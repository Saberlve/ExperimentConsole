"""Console camera HTTP service is the sole owner of real cameras."""
import io
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from PIL import Image, ImageDraw


def utc():
    return datetime.now(timezone.utc).isoformat()


class CameraAdapter:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()

    def request(self, path, post=False):
        request = urllib.request.Request(self.config['camera_url'].rstrip('/') + path,
                                         method='POST' if post else 'GET')
        return urllib.request.urlopen(request, timeout=8)

    def cameras(self):
        if self.config['mode'] == 'reference_only':
            return []
        if self.config['mode'] == 'demo':
            return [{'serial': 'demo', 'label': 'DEMO 合成相机', 'width': 640,
                     'height': 480, 'connected': True, 'error': None}]
        with self.request('/api/cameras') as response:
            return json.load(response)['devices']

    def frame(self, camera_id):
        """Read one original-service cached preview frame, never capture/save a snapshot."""
        if self.config['mode'] == 'demo':
            if camera_id != 'demo':
                raise ValueError('未知相机')
            data, metadata = self.demo_frame()
            return data, 'image/png', str(metadata['monotonic_timestamp_ns'])
        device = next((d for d in self.cameras() if d['serial'] == camera_id), None)
        if device is None:
            raise ValueError('未知相机')
        if not device.get('connected') or device.get('error'):
            raise ValueError('相机未连接或采集异常')
        if not isinstance(device.get('frame_count'), int) or device['frame_count'] <= 0:
            raise ValueError('旧相机服务尚未发布可验证的新帧计数')
        from urllib.parse import quote
        with self.request('/stream?serial=' + quote(camera_id, safe='')) as response:
            # Original service sends a Content-Length for each MJPEG part.
            length = None
            for _ in range(16):
                line = response.readline(1024)
                if not line:
                    raise ValueError('相机预览流已关闭')
                if line.lower().startswith(b'content-length:'):
                    length = int(line.split(b':', 1)[1])
                if line in (b'\r\n', b'\n') and length is not None:
                    break
            if length is None or not 0 < length <= 32 * 1024 * 1024:
                raise ValueError('相机预览帧格式无效')
            data = response.read(length)
            if len(data) != length:
                raise ValueError('相机预览帧不完整')
        # Sequence observed just before reading the cached JPEG; not an exposure timestamp.
        return data, 'image/jpeg', str(device['frame_count'])

    def demo_frame(self):
        # Synthetic acquisition timestamps belong only to this generated image.
        with self.lock:
            stamp, mono = utc(), time.monotonic_ns()
            image = Image.new('RGB', (640, 480), '#183149')
            draw = ImageDraw.Draw(image)
            draw.rectangle((160, 120, 480, 360), outline='#55dabb', width=5)
            draw.text((24, 24), 'DEMO / SYNTHETIC CAMERA', fill='white')
            draw.text((24, 440), stamp, fill='white')
            output = io.BytesIO()
            image.save(output, 'PNG')
            return output.getvalue(), {'capture_timestamp': stamp,
                'monotonic_timestamp_ns': mono, 'clock_domain': 'console_host',
                'device_timestamp': None, 'device_timestamp_domain': None,
                'camera_intrinsics': None, 'synthetic': True}


class FirstFrameAdapter:
    def __init__(self, camera):
        self.camera = camera
        self.lock = threading.Lock()

    def capture(self, camera_id):
        if self.camera.config['mode'] == 'demo':
            if camera_id != 'demo':
                raise ValueError('未知相机')
            data, metadata = self.camera.demo_frame()
            return [(camera_id, data, metadata)]
        devices = self.camera.cameras()
        if camera_id not in [d['serial'] for d in devices]:
            raise ValueError('未知相机')
        # Original capture always saves BOTH cameras. Never recapture from JPEG preview.
        with self.lock, self.camera.request('/api/capture', post=True) as response:
            result = json.load(response)
            root = self.camera.config['capture_root']
            from pathlib import Path
            root = Path(root).resolve()
            frames = []
            for name in result['paths']:
                path = Path(name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('旧服务返回路径不在 capture_root 中；检查共享目录配置')
                suffix = path.stem[-1]
                matching = [d for d in devices if d.get('suffix') == suffix]
                if len(matching) != 1:
                    raise ValueError('无法映射旧服务相机文件后缀')
                frames.append((matching[0]['serial'], path.read_bytes(), {
                    'capture_timestamp': None, 'device_timestamp': None,
                    'device_timestamp_domain': None, 'monotonic_timestamp_ns': None,
                    'camera_intrinsics': None, 'clock_domain': 'unknown',
                    'source_path': str(path), 'source_group': result['number'],
                    'timestamp_limitation': 'Existing service does not expose acquisition timestamps'}))
            return frames
