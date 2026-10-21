"""Record both cached camera previews on a shared host timeline, without owning devices."""
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from adapters.camera import utc


class ReplayRecording:
    FPS = 15

    def __init__(self, camera, directory, output, ready):
        self.camera, self.directory = camera, Path(directory)
        self.output, self.ready = Path(output), ready
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.writers = []
        self.images = []
        self.started = None
        self.stopped = None
        self.data = dict(enabled=True, status='preparing', directory=str(self.directory),
                         fps=self.FPS, views=[], error=None,
                         timing='共同主机时间轴；读取相机缓存预览，非硬件同步；掉帧时重复上一帧')

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def _image(self, serial):
        import cv2
        import numpy as np
        raw, _, sequence = self.camera.frame(serial)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f'相机 {serial} 的视频帧无法解码')
        if image.shape[0] % 2 or image.shape[1] % 2:
            raise ValueError('MP4 录制要求相机画面宽高为偶数')
        return image, sequence

    def prepare(self):
        try:
            import cv2
            self.ready()
            devices = self.camera.cameras()
            if (len(devices) != 2 or len({d['serial'] for d in devices}) != 2
                    or any(not d.get('connected') or d.get('error') for d in devices)):
                raise ValueError('双视角录制需要两台已连接且正常采集的相机，请先开启 / 刷新相机')
            self.directory.mkdir(parents=True, exist_ok=False)
            # Fetch both views concurrently, including the initial readiness check.
            with ThreadPoolExecutor(max_workers=2) as pool:
                images = list(pool.map(self._image, [d['serial'] for d in devices]))
            for index, (device, (image, sequence)) in enumerate(zip(devices, images), 1):
                height, width = image.shape[:2]
                path = self.directory / f'view_{index}.mp4'
                writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'),
                                         self.FPS, (width, height))
                if not writer.isOpened():
                    writer.release()
                    raise ValueError('无法创建 MP4 视频，请检查 OpenCV 编码器和保存目录')
                self.writers.append(writer)
                self.images.append(image)
                with self.lock:
                    self.data['views'].append(dict(serial=device['serial'],
                        label=device.get('label') or device['serial'], path=str(path),
                        url='/assets/' + path.relative_to(self.output).as_posix(),
                        width=width, height=height, frames=0, repeated_frames=0,
                        last_sequence=sequence))
            with self.lock:
                self.data['status'] = 'waiting'
        except Exception as exc:
            with self.lock:
                self.data.update(status='error', error=str(exc))
            self._finish()
            raise

    def start(self):
        try:
            self.ready()
            # Discard readiness-check images, which predate the initial approach.
            with ThreadPoolExecutor(max_workers=2) as pool:
                images = list(pool.map(self._image, [v['serial'] for v in self.data['views']]))
            for (image, _), previous in zip(images, self.images):
                if image.shape != previous.shape:
                    raise ValueError('录制过程中相机分辨率发生变化')
            self.images = [image for image, _ in images]
            with self.lock:
                for view, (_, sequence) in zip(self.data['views'], images):
                    view['last_sequence'] = sequence
            self.started = time.monotonic()
            self._write(self.images)
            with self.lock:
                self.data.update(status='recording', started_at=utc())
            self.thread = threading.Thread(target=self._record, daemon=True)
            self.thread.start()
        except Exception as exc:
            with self.lock:
                self.data.update(status='error', error=str(exc))
            self._finish()
            raise

    def _write(self, images, repeated=False):
        for writer, image in zip(self.writers, images):
            writer.write(image)
        with self.lock:
            for view in self.data['views']:
                view['frames'] += 1
                if repeated:
                    view['repeated_frames'] += 1

    def _record(self):
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                while not self.stop.is_set():
                    with self.lock:
                        count = self.data['views'][0]['frames']
                        serials = [v['serial'] for v in self.data['views']]
                    if self.stop.wait(max(0, self.started + count/self.FPS - time.monotonic())):
                        break
                    images = list(pool.map(self._image, serials))
                    if self.stop.is_set():
                        break
                    for (image, _), previous in zip(images, self.images):
                        if image.shape != previous.shape:
                            raise ValueError('录制过程中相机分辨率发生变化')
                    index = int((time.monotonic()-self.started)*self.FPS)
                    while count < index:
                        self._write(self.images, repeated=True)
                        count += 1
                    self._write([image for image, _ in images])
                    with self.lock:
                        for view, (_, sequence) in zip(self.data['views'], images):
                            if sequence == view['last_sequence']:
                                view['repeated_frames'] += 1
                            view['last_sequence'] = sequence
                    self.images = [image for image, _ in images]
        except Exception as exc:
            with self.lock:
                self.data.update(status='error', error=str(exc))
        finally:
            self._finish()

    def _finish(self):
        try:
            if self.started is not None:
                # Preserve elapsed playback duration even when the last HTTP read was slow.
                end = self.stopped or time.monotonic()
                target = max(1, math.ceil((end-self.started)*self.FPS))
                while self.data['views'][0]['frames'] < target:
                    self._write(self.images, repeated=True)
        except Exception as exc:
            with self.lock:
                self.data.update(status='error', error='视频收尾失败：' + str(exc))
        finally:
            for writer in self.writers:
                writer.release()
        with self.lock:
            if self.data['status'] != 'error':
                self.data['status'] = 'saved' if self.started is not None else 'cancelled'
            self.data['ended_at'] = utc()
            for view in self.data['views']:
                view['duration_s'] = view['frames']/self.FPS
            if self.directory.exists():
                try:
                    (self.directory / 'recording.json').write_text(
                        json.dumps(self.data, ensure_ascii=False, indent=2))
                except OSError as exc:
                    self.data.update(status='error', error='录制信息保存失败：' + str(exc))

    def close(self):
        self.stopped = self.stopped or time.monotonic()
        self.stop.set()
        with self.lock:
            if self.data['status'] == 'recording':
                self.data['status'] = 'saving'
        if self.thread:
            self.thread.join()
        elif self.data['status'] in ('preparing', 'waiting'):
            self._finish()
