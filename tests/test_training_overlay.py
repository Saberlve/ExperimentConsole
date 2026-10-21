"""Shared-video episode offsets, equal weights and immutable source datasets."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from backend.server import Application
from backend.training_overlay import composite, metadata


VIEW = 'observation.images.third_camera'


def make_dataset(root):
    (root / 'meta/episodes/chunk-000').mkdir(parents=True)
    video = root / 'videos' / VIEW / 'chunk-000/file-000.mp4'
    video.parent.mkdir(parents=True)
    with av.open(str(video), mode='w') as container:
        stream = container.add_stream('libx264rgb', rate=10)
        stream.width, stream.height, stream.pix_fmt = 32, 24, 'rgb24'
        stream.options = {'crf': '0', 'preset': 'ultrafast'}
        for color in ((255, 0, 0), (255, 255, 255), (0, 255, 0),
                      (255, 255, 255), (0, 0, 255), (255, 255, 255)):
            frame = av.VideoFrame.from_ndarray(np.full((24, 32, 3), color, dtype=np.uint8), format='rgb24')
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    info = dict(codebase_version='v3.0', total_episodes=3, fps=10,
                video_path='videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4',
                features={VIEW: dict(dtype='video')})
    (root / 'meta/info.json').write_text(json.dumps(info))
    rows = [dict(episode_index=i, **{f'videos/{VIEW}/chunk_index': 0,
            f'videos/{VIEW}/file_index': 0, f'videos/{VIEW}/from_timestamp': i * .2,
            f'videos/{VIEW}/to_timestamp': (i + 1) * .2}) for i in range(3)]
    pq.write_table(pa.Table.from_pylist(rows), root / 'meta/episodes/chunk-000/file-000.parquet')


def fingerprints(root):
    return {str(file.relative_to(root)): (file.stat().st_mtime_ns,
            hashlib.sha256(file.read_bytes()).hexdigest())
            for file in root.rglob('*') if file.is_file()}


class TrainingOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'dataset'
        make_dataset(self.root)
        self.output = self.base / 'output'

    def test_all_episode_offsets_equal_mean_and_readonly_source(self):
        before = fingerprints(self.root)
        for file in self.root.rglob('*'):
            if file.is_file():
                file.chmod(0o444)
        root, info, views, rows = metadata(str(self.root), self.output)
        updates = []
        png, details = composite(root, info, rows, views[0], lambda done, total: updates.append((done, total)))
        image = np.asarray(Image.open(io.BytesIO(png)))
        np.testing.assert_array_equal(image, np.full((24, 32, 3), 85, dtype=np.uint8))
        self.assertEqual([s['decoded_timestamp'] for s in details['sources']], [0., .2, .4])
        self.assertEqual(updates, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(before, fingerprints(self.root))

    def test_overlapping_outputs_and_unsupported_or_incomplete_metadata_rejected(self):
        for output in (self.root, self.root / 'output', self.root.parent):
            with self.assertRaisesRegex(ValueError, '不能重叠'):
                metadata(str(self.root), output)
        with self.assertRaisesRegex(ValueError, '不能重叠'):
            metadata(str(self.root), self.output, self.root / 'captures')
        file = self.root / 'meta/info.json'
        info = json.loads(file.read_text())
        info['total_episodes'] = 4
        file.write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError, '数量'):
            metadata(str(self.root), self.output)
        info['codebase_version'] = 'v2.1'
        file.write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError, 'v3'):
            metadata(str(self.root), self.output)

    def test_async_result_written_only_to_console_output_and_bad_path_reported(self):
        app = Application(dict(mode='demo', output_dir=str(self.output), camera_preview_on_start=False))
        before = fingerprints(self.root)

        def finish(job):
            deadline = time.monotonic() + 10
            while job['state'] == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
                job = app.training_overlay.status(job['id'])
            return job

        result = finish(app.training_overlay.start({'root': str(self.root)}))
        self.assertEqual(result['state'], 'done', result)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['view'], VIEW)
        self.assertTrue(app.asset(result['url']).is_relative_to(self.output))
        manifest = json.loads(app.asset(result['manifest_url']).read_text())
        self.assertEqual(manifest['weight_per_episode'], 1 / 3)
        self.assertEqual(len(manifest['sources']), 3)
        self.assertEqual(before, fingerprints(self.root))
        bad = finish(app.training_overlay.start({'root': str(self.root / 'missing')}))
        self.assertEqual(bad['state'], 'error')
        with self.assertRaisesRegex(ValueError, '不存在'):
            app.training_overlay.status('missing')

    def test_missing_episode_frame_and_video_path_escape_fail_without_partial_image(self):
        root, info, views, rows = metadata(str(self.root), self.output)
        rows[-1][f'videos/{VIEW}/from_timestamp'] = 20
        rows[-1][f'videos/{VIEW}/to_timestamp'] = 21
        with self.assertRaisesRegex(ValueError, '无法读取首帧'):
            composite(root, info, rows, VIEW)
        info['video_path'] = '../escape.mp4'
        with self.assertRaisesRegex(ValueError, '目录内'):
            composite(root, info, rows, VIEW)


if __name__ == '__main__':
    unittest.main()
