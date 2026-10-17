"""Read-only LeRobot facade; delegates decoding and episode indexing to LeRobot."""
import io
import threading
from pathlib import Path
from PIL import Image


class DatasetAdapter:
    def __init__(self, config):
        self.entries = config.get('datasets', {})
        self.cache = {}
        self.lock = threading.RLock()

    def load(self, key):
        if key not in self.entries:
            raise ValueError('未知数据集，请在配置中注册')
        if key not in self.cache:
            entry = self.entries[key]
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
            root = Path(entry['root']).resolve()
            if not (root / 'meta/info.json').is_file():
                raise ValueError('本地数据集缺少 meta/info.json；不自动下载')
            self.cache[key] = LeRobotDataset(repo_id=entry['repo_id'], root=root,
                                              download_videos=False)
        return self.cache[key]

    def describe(self, key):
        with self.lock:
            ds = self.load(key)
            return {'episodes': [int(row['episode_index']) for row in ds.meta.episodes],
                    'views': list(ds.meta.camera_keys)}

    def first_frame(self, key, episode, view):
        with self.lock:
            ds = self.load(key)
            if view not in ds.meta.camera_keys:
                raise ValueError('未知相机视角')
            rows = [r for r in ds.meta.episodes if int(r['episode_index']) == episode]
            if len(rows) != 1:
                raise ValueError('未知 Episode')
            row = rows[0]
            if 'dataset_from_index' not in row:
                raise ValueError('需要 LeRobot v3 dataset_from_index 元数据；旧版请通过独立适配器接入')
            frame = ds[int(row['dataset_from_index'])][view]
            array = frame.detach().cpu().numpy().transpose(1, 2, 0)
            image = Image.fromarray((array * 255).clip(0, 255).astype('uint8'))
            output = io.BytesIO()
            image.save(output, 'PNG')
            return output.getvalue()
