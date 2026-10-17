import io
import json
import uuid
from pathlib import Path
from PIL import Image
from adapters.camera import utc


class SnapshotService:
    def __init__(self, adapter, output):
        self.adapter = adapter
        self.output = Path(output)

    def save(self, camera_id, task_id):
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256:
            raise ValueError('task_id 需要 1～256 字符')
        frames = self.adapter.capture(camera_id)
        group = self.output / 'snapshots' / uuid.uuid4().hex
        group.mkdir(parents=True)
        results = []
        for index, (serial, data, metadata) in enumerate(frames):
            image = Image.open(io.BytesIO(data))
            image.load()
            path = group / f'{index}.png'
            with path.open('xb') as stream:
                stream.write(data)
            record = {**metadata, 'camera_id': serial, 'task_id': task_id,
                      'selected_camera_id': camera_id, 'image_path': str(path.resolve()),
                      'width': image.width, 'height': image.height, 'save_timestamp': utc()}
            with path.with_suffix('.json').open('x') as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
            results.append(record)
        return {'frames': results, 'pair_capture': len(results) > 1}
