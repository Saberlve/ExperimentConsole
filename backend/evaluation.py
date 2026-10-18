"""Task-specific immutable layout suites and manually labelled VLA evaluations.

No model, robot or camera control. Persistence is independent from the parent repo.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import uuid
from PIL import Image
from adapters.camera import utc


def text(value, name, limit=1024):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} 必须是非空文字（最多 {limit} 字符）')
    return value.strip()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('无效记录 ID')
    return value


class EvaluationService:
    def __init__(self, app):
        self.app = app
        self.root = app.output / 'vla_evaluations'
        self.lock = threading.RLock()

    def read(self, kind, key):
        return json.loads((self.root / kind / identifier(key) / 'record.json').read_text())

    def write(self, kind, record):
        folder = self.root / kind / record['id']
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix='.record-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, folder / 'record.json')
        finally:
            Path(tmp).unlink(missing_ok=True)

    def catalog(self):
        with self.lock:
            suites = [json.loads(p.read_text()) for p in sorted((self.root / 'suites').glob('*/record.json')) if re.fullmatch(r'[a-f0-9]{32}', p.parent.name)]
            runs = [self.summary(json.loads(p.read_text())) for p in sorted((self.root / 'runs').glob('*/record.json'))]
            return {'suites': sorted(suites, key=lambda x:x['created_at'], reverse=True),
                    'runs': sorted(runs, key=lambda x:x['created_at'], reverse=True)}

    def create_suite(self, payload):
        task = text(payload.get('task'), '任务', 256)
        name = text(payload.get('name'), '测试集名称', 256)
        criteria = text(payload.get('criteria'), '成功判定标准', 2048)
        layouts = payload.get('layouts')
        if not isinstance(layouts, list) or not 1 <= len(layouts) <= 100:
            raise ValueError('测试集需要 1～100 张布局图，建议每个任务固定 10 张')
        key = uuid.uuid4().hex
        parent = self.root / 'suites'
        parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.suite-', dir=parent))
        record = dict(id=key, task=task, name=name, criteria=criteria, created_at=utc(),
                      capture_mode=self.app.config['mode'], layouts=[])
        try:
            for index, item in enumerate(layouts):
                path = self.app.asset(item['url'])
                with Image.open(path) as image:
                    image.verify()
                body = path.read_bytes()
                dest = stage / f'{index+1:03d}.png'
                dest.write_bytes(body)
                record['layouts'].append(dict(index=index, label=text(item.get('label'), '布局名称', 256),
                    url=self.app.url(parent / key / dest.name), sha256=hashlib.sha256(body).hexdigest()))
            (stage / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
            with self.lock:
                os.rename(stage, parent / key)
            return record
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def create_run(self, payload):
        with self.lock:
            suite = self.read('suites', payload.get('suite_id'))
            record = dict(id=uuid.uuid4().hex, suite_id=suite['id'], task=suite['task'], suite_name=suite['name'],
                          criteria=suite['criteria'], model=text(payload.get('model'), '模型', 256),
                          checkpoint=text(payload.get('checkpoint'), 'checkpoint', 2048),
                          round=text(payload.get('round'), '轮次', 256),
                          eval_config=str(payload.get('eval_config', ''))[:2048],
                          created_at=utc(), mode=('demo' if suite['capture_mode']=='demo' or self.app.config['mode']=='demo' else self.app.config['mode']), revision=0, cursor=0,
                          trials=[dict(**layout, status='pending', started_at=None, finished_at=None, note='') for layout in suite['layouts']])
            self.write('runs', record)
            return self.summary(record)

    def summary(self, record):
        statuses = [t['status'] for t in record['trials']]
        success, failure = statuses.count('success'), statuses.count('failure')
        completed = success + failure
        return dict(record, metrics=dict(total=len(statuses), success=success, failure=failure,
                    aborted=statuses.count('aborted'), pending=statuses.count('pending'), running=statuses.count('running'),
                    completed=completed, success_rate=success/completed if completed else None,
                    all_scored=completed==len(statuses)))

    def transition(self, payload):
        with self.lock:
            record = self.read('runs', payload.get('run_id'))
            if type(payload.get('revision')) is not int or payload['revision'] != record['revision']:
                raise ValueError('记录已更新，请刷新后重试（避免重复记录或多个页面覆盖）')
            index = record['cursor']
            trial = record['trials'][index]
            action = payload.get('action')
            if action == 'start':
                if trial['status'] != 'pending':
                    raise ValueError('当前布局已开始或已记录')
                image = self.app.asset(trial['url'])
                if hashlib.sha256(image.read_bytes()).hexdigest() != trial['sha256']:
                    raise ValueError('布局图片已变更，请恢复原图或建立新测试集')
                if any(r['metrics']['running'] for r in self.catalog()['runs']):
                    raise ValueError('已有样本标记为开始，请先记录结果或中止')
                self.app.release_preview()
                trial.update(status='running', started_at=utc())
            elif action == 'result':
                if trial['status'] != 'running' or payload.get('result') not in ('success', 'failure', 'aborted'):
                    raise ValueError('先标记开始，再记录成功、失败或中止')
                note = payload.get('note', '')
                if not isinstance(note, str) or len(note) > 4096:
                    raise ValueError('备注必须是最多 4096 字符的文字')
                trial.update(status=payload['result'], note=note, finished_at=utc())
            elif action == 'next':
                if trial['status'] not in ('success', 'failure', 'aborted'):
                    raise ValueError('先记录当前布局结果，再切换下一张')
                if index+1 >= len(record['trials']):
                    raise ValueError('已经是最后一张布局')
                record['cursor'] += 1
            else:
                raise ValueError('未知操作')
            record['revision'] += 1
            self.write('runs', record)
            return self.summary(record)

    def export_csv(self, key):
        with self.lock:
            record = self.read('runs', key)
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['task','suite_id','model','checkpoint','round','mode','layout','label','image_sha256','status','started_at','finished_at','note'])
        def safe(v):
            s = str(v or '')
            return "'"+s if s.lstrip().startswith(('=', '+', '-', '@')) else s
        for t in record['trials']:
            writer.writerow([safe(v) for v in [record['task'],record['suite_id'],record['model'],record['checkpoint'],record['round'],record['mode'],t['index']+1,t['label'],t['sha256'],t['status'],t['started_at'],t['finished_at'],t['note']]])
        return '\ufeff'+stream.getvalue()
