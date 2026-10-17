"""Console-owned feeds using the installed LeRobot public camera API."""
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class Device:
    serial: str
    name: str
    product_line: str


def discover_devices():
    import pyrealsense2 as rs
    return [Device(device.get_info(rs.camera_info.serial_number),
                   device.get_info(rs.camera_info.name),
                   device.get_info(rs.camera_info.product_line))
            for device in rs.context().query_devices()]


class CameraFeed:
    """One read thread per physical device, shared by all HTTP subscribers."""
    def __init__(self, device, width, height, fps, camera=None):
        self.device, self.width, self.height, self.fps = device, width, height, fps
        if camera is None:
            from lerobot.cameras import ColorMode
            from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig
            camera = RealSenseCamera(RealSenseCameraConfig(serial_number_or_name=device.serial,
                width=width, height=height, fps=fps, color_mode=ColorMode.BGR))
        self.camera = camera
        self.condition = threading.Condition()
        self.frame = None
        self.jpeg = None
        self.frame_id = self.frame_count = 0
        self.connected = self.stopped = False
        self.error = None
        self.stop_event = threading.Event()
        self.thread = None

    def connect(self):
        self.camera.connect()
        self.connected = True
        self.thread = threading.Thread(target=self._read, name=f'console-camera-{self.device.serial}', daemon=True)
        self.thread.start()

    def _read(self):
        import cv2
        period = 1 / self.fps
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                frame = self.camera.read()
                ok, jpeg = cv2.imencode('.jpg', frame)
                if not ok:
                    raise RuntimeError('JPEG 编码失败')
                with self.condition:
                    self.frame, self.jpeg = frame, jpeg.tobytes()
                    self.frame_id += 1
                    self.frame_count += 1
                    self.error = None
                    self.condition.notify_all()
            except Exception as exc:
                with self.condition:
                    self.error = f'{type(exc).__name__}: {exc}'
                    self.condition.notify_all()
                if self.stop_event.wait(.1):
                    break
            self.stop_event.wait(max(0, period - (time.monotonic() - started)))

    def status(self):
        with self.condition:
            return {'serial': self.device.serial, 'name': self.device.name,
                    'product_line': self.device.product_line, 'width': self.width,
                    'height': self.height, 'fps': self.fps, 'connected': self.connected,
                    'frame_count': self.frame_count, 'error': self.error}

    def wait_for_frame(self, last_id):
        with self.condition:
            self.condition.wait_for(lambda: self.frame_id > last_id or self.stopped, timeout=1)
            return self.jpeg, self.frame_id, self.stopped

    def stop(self):
        self.stop_event.set()
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2)
        if self.connected:
            try:
                self.camera.disconnect()
            finally:
                self.connected = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
