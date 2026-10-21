"""Read local LeRobot v3 video metadata without constructing a mutable dataset."""
import io
import json
import math
from pathlib import Path
import threading
import uuid

from PIL import Image


def inside(root, path):
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('训练集文件必须位于所选数据集目录内')
    return path


def metadata(requested, output, capture_root=None):
    import pyarrow.parquet as pq

    if not isinstance(requested, str) or not requested.strip():
        raise ValueError('请填写服务器上的 LeRobot v3 训练集路径')
    root = Path(requested.strip()).expanduser().resolve(strict=True)
    for destination in (output, capture_root):
        if destination:
            destination = Path(destination).resolve()
            if destination.is_relative_to(root) or root.is_relative_to(destination):
                raise ValueError('训练集与控制台输出、采集目录不能重叠；原始数据集仅以只读方式访问')
    info = json.loads(inside(root, root / 'meta/info.json').read_text())
    if not str(info.get('codebase_version', '')).startswith('v3.'):
        raise ValueError('仅支持 LeRobot v3 数据集')
    views = [key for key, feature in info.get('features', {}).items()
             if feature.get('dtype') == 'video']
    if not views:
        raise ValueError('训练集没有可读取的视频相机视角')
    columns = ['episode_index']
    for view in views:
        columns.extend(f'videos/{view}/{field}' for field in
                       ('chunk_index', 'file_index', 'from_timestamp', 'to_timestamp'))
    rows = []
    for file in sorted((root / 'meta/episodes').rglob('*.parquet')):
        rows.extend(pq.read_table(inside(root, file), columns=columns).to_pylist())
    if not rows or len(rows) != info.get('total_episodes'):
        raise ValueError('Episode 元数据数量与 total_episodes 不符，未生成不完整叠加图')
    ids = [int(row['episode_index']) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Episode 元数据存在重复编号')
    if not isinstance(info.get('video_path'), str):
        raise ValueError('训练集缺少 video_path 模板')
    return root, info, views, sorted(rows, key=lambda row: int(row['episode_index']))


def composite(root, info, rows, view, progress=lambda *_: None):
    import av
    import numpy as np

    accumulated = None
    container = None
    previous = None
    sources = []
    try:
        for index, row in enumerate(rows):
            prefix = f'videos/{view}/'
            start, end = float(row[prefix + 'from_timestamp']), float(row[prefix + 'to_timestamp'])
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
                raise ValueError(f'Episode {row["episode_index"]} 视频时间范围无效')
            file = inside(root, root / info['video_path'].format(
                video_key=view, chunk_index=int(row[prefix + 'chunk_index']),
                file_index=int(row[prefix + 'file_index'])))
            if file != previous:
                if container:
                    container.close()
                container = av.open(str(file), mode='r')
                previous = file
            stream = container.streams.video[0]
            # v3 packs episodes into shared videos: seek to this episode, not file zero.
            container.seek(max(0, math.floor(start / stream.time_base)), stream=stream,
                           backward=True, any_frame=False)
            first = None
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                timestamp = float(frame.pts * frame.time_base)
                if timestamp + 1e-6 < start:
                    continue
                if timestamp >= end - 1e-6:
                    break
                first = frame
                break
            if first is None:
                raise ValueError(f'Episode {row["episode_index"]} 无法读取首帧，未跳过该样本')
            if first.width * first.height > 20_000_000:
                raise ValueError('训练集视频帧超过 20M 像素上限')
            image = first.to_image().convert('RGB')
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            array = np.asarray(image, dtype=np.float64)
            if accumulated is None:
                accumulated = np.zeros_like(array)
            if accumulated.shape != array.shape:
                raise ValueError('各 Episode 首帧尺寸不同，不能直接叠加')
            accumulated += array
            sources.append(dict(episode_index=int(row['episode_index']),
                                video=str(file.relative_to(root)),
                                from_timestamp=start, decoded_timestamp=timestamp))
            progress(index + 1, len(rows))
        result = Image.fromarray(np.rint(accumulated / len(rows)).clip(0, 255).astype('uint8'))
        output = io.BytesIO()
        result.save(output, 'PNG')
        return output.getvalue(), dict(width=result.width, height=result.height, sources=sources)
    finally:
        if container:
            container.close()


class TrainingOverlay:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.jobs = {}

    def status(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('首帧叠加任务不存在，请重新生成')
            return dict(self.jobs[job_id])

    def start(self, payload):
        with self.lock:
            if any(job['state'] == 'running' for job in self.jobs.values()):
                raise ValueError('已有首帧叠加任务正在生成，请稍候')
            while len(self.jobs) >= 8:
                del self.jobs[next(iter(self.jobs))]
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = dict(id=job_id, state='running', completed=0, total=0)
        threading.Thread(target=self._run, args=(job_id, dict(payload)), daemon=True).start()
        return self.status(job_id)

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def _run(self, job_id, payload):
        try:
            root, info, views, rows = metadata(payload.get('root'), self.app.output,
                                              self.app.config.get('capture_root'))
            view = payload.get('view')
            if not view or view == 'auto':
                view = next((key for key in views if 'third' in key or 'exterior' in key), views[0])
            if view not in views:
                raise ValueError('所选相机视角不在训练集中，请重新选择')
            self._update(job_id, root=str(root), view=view, views=views, total=len(rows))
            png, details = composite(root, info, rows, view,
                                    lambda done, total: self._update(job_id, completed=done, total=total))
            # Only derived files in the console output are written. Source files are read-only.
            uploaded = self.app.upload(png)
            manifest = dict(root=str(root), view=view, episode_count=len(rows),
                            method='equal_weight_mean', weight_per_episode=1 / len(rows), **details)
            file = self.app.output / ('training_overlay_' + job_id + '.json')
            with file.open('x', encoding='utf-8') as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
            self._update(job_id, state='done', url=uploaded['url'],
                         manifest_url=self.app.url(file), width=details['width'], height=details['height'])
        except Exception as exc:
            self._update(job_id, state='error', error=str(exc))
