"""Optional real LeRobot round trip, generated locally without hardware or downloads."""
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from adapters.dataset import DatasetAdapter


class LeRobotRoundTrip(unittest.TestCase):
    def test_actual_local_dataset_first_frame(self):
        try:
            import numpy as np
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
        except ImportError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'fixture'
            dataset = LeRobotDataset.create(repo_id='local/console-fixture', fps=10,
                root=root, use_videos=False,
                features={'observation.images.front': {'dtype': 'image', 'shape': (24, 32, 3),
                          'names': ['height', 'width', 'channels']},
                          'action': {'dtype': 'float32', 'shape': (1,), 'names': ['action']}})
            for color in (40, 180):
                dataset.add_frame({'observation.images.front': np.full((24, 32, 3), color, dtype=np.uint8),
                                   'action': np.array([0.0], dtype=np.float32), 'task': 'fixture'})
            dataset.save_episode()
            dataset.finalize()
            adapter = DatasetAdapter({'datasets': {'fixture': {'root': str(root), 'repo_id': 'local/console-fixture'}}})
            description = adapter.describe('fixture')
            self.assertEqual(description['episodes'], [0])
            self.assertEqual(description['views'], ['observation.images.front'])
            data = adapter.first_frame('fixture', 0, 'observation.images.front')
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.size, (32, 24))
                self.assertEqual(image.getpixel((0, 0)), (40, 40, 40))


if __name__ == '__main__':
    unittest.main()
