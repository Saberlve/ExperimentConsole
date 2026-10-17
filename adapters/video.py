"""Single uploaded episode clip -> its first decoded frame; no dataset rewrite."""
from pathlib import Path
import tempfile

MAX_VIDEO_BYTES = 256 * 1024 * 1024


def first_frame(stream, size, output_root):
    if size <= 0 or size > MAX_VIDEO_BYTES:
        raise ValueError('Episode 视频需要 1 byte～256 MiB；较大数据请使用已有数据集读取')
    import cv2
    # Bounded disk streaming, never buffer the whole video in RAM or keep the upload.
    with tempfile.TemporaryDirectory(prefix='.episode-', dir=output_root) as directory:
        path = Path(directory) / 'episode.mp4'
        remaining = size
        with path.open('xb') as target:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('视频上传不完整')
                target.write(chunk)
                remaining -= len(chunk)
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise ValueError('无法解码视频，请上传 MP4 / MOV / AVI / MKV Episode 视频')
            ok, frame = capture.read()
            if not ok or frame is None:
                raise ValueError('视频没有可解码的首帧')
            height, width = frame.shape[:2]
            if height * width > 20_000_000:
                raise ValueError('视频首帧超过 20M 像素上限')
            ok, encoded = cv2.imencode('.png', frame)
            if not ok:
                raise ValueError('视频首帧 PNG 编码失败')
            return encoded.tobytes(), {'kind': 'uploaded_episode_video',
                'frame_index': 0, 'timestamp_basis': 'first_decoded_frame',
                'capture_timestamp': None, 'width': width, 'height': height}
        finally:
            capture.release()
