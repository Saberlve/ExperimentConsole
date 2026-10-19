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


def suite_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('无效测试集 ID')
    return value


def target_count(value=15):
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError('目标数量必须是 1～100 的整数')
    return value


def optional_criteria(value=''):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('判定说明必须为文字，且不超过 2048 字符')
    return value.strip()


def config_filename(task, round_name):
    def part(value):
        return re.sub(r'[^\w-]+', '_', value).strip('_')[:64] or 'eval'
    return f'{part(task)}_{part(round_name)}_eval.yaml'


def validate_eval_yaml(content):
    import yaml
    if not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > 262144:
        raise ValueError('评测 YAML 内容不能为空，且不得超过 256 KiB')
    try:
        config = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError('YAML 格式错误：' + str(exc)) from exc
    if not isinstance(config, dict):
        raise ValueError('评测 YAML 顶层必须是配置映射')
    return content


class EvaluationService:
    def __init__(self, app):
        self.app = app
        self.root = app.output / 'vla_evaluations'
        self.lock = threading.RLock()

    def layout_sources(self):
        roots = [Path(p).expanduser().resolve() for p in self.app.config.get(
            'first_frame_roots', [str(self.app.output / 'imported_images'), self.app.config.get('capture_root', str(self.app.output / 'snapshots'))])]
        groups = {}
        for root in dict.fromkeys(roots):
            if not root.is_dir():
                continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                folder = Path(directory)
                dirs[:] = sorted(d for d in dirs if not (folder / d).is_symlink())
                for name in files:
                    path = folder / name
                    if path.suffix.lower() not in ('.png', '.jpg', '.jpeg') or path.is_symlink() or 'overview' in path.stem.lower():
                        continue
                    if not path.resolve().is_relative_to(root):
                        continue
                    view = ''
                    if re.search(r'_A$', path.stem, re.I) or 'third_camera' in name:
                        view = '第三人称视角（全景）'
                    elif re.search(r'_W$', path.stem, re.I) or 'wrist_camera' in name:
                        view = '腕部视角'
                    elif folder.parent.name in ('VLA_test_fisrt', 'egg_pick'):
                        view = {'panorama': '第三人称视角（全景）', 'third_person': '第三人称视角（全景）',
                                'third person': '第三人称视角（全景）', 'wrist': '腕部视角',
                                'wrist_camera': '腕部视角'}.get(folder.name, '')
                    key = hashlib.sha256((str(folder) + '|' + view).encode()).hexdigest()
                    group = groups.setdefault(key, dict(id=key, directory=str(folder), view=view, files=[]))
                    if str(path) not in group['files']:
                        group['files'].append(str(path))
        for group in groups.values():
            group['files'].sort(key=lambda p: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', Path(p).name)])
        return sorted(groups.values(), key=lambda g:(g['directory'], g['view']))

    def source_catalog(self):
        return [dict(id=g['id'], directory=g['directory'], view=g['view'], count=len(g['files']))
                for g in self.layout_sources()]

    def load_source(self, key):
        group = next((g for g in self.layout_sources() if g['id'] == key), None)
        if group is None:
            raise ValueError('首帧目录不存在，请刷新目录列表')
        if len(group['files']) > 100:
            raise ValueError('此目录超过 100 张，请先拆分目录后再加载')
        layouts = []
        for filename in group['files']:
            path = Path(filename)
            url = self.app.upload(path.read_bytes())['url']
            layouts.append(dict(url=url, label=(group['view'] + ' / ' if group['view'] else '') + path.name))
        return dict(directory=group['directory'], view=group['view'], layouts=layouts)

    def read(self, kind, key):
        key = suite_identifier(key) if kind == 'suites' else identifier(key)
        record = json.loads((self.root / kind / key / 'record.json').read_text())
        if kind == 'suites' and record.get('deleted_at'):
            raise ValueError('suite 已删除，请刷新列表')
        return record

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
            suites = [json.loads(p.read_text()) for p in sorted((self.root / 'suites').glob('*/record.json')) if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', p.parent.name)]
            runs = [self.summary(json.loads(p.read_text())) for p in sorted((self.root / 'runs').glob('*/record.json'))]
            return {'suites': sorted((s for s in suites if not s.get('deleted_at')), key=lambda x:x['created_at'], reverse=True),
                    'suites_root': str((self.root / 'suites').resolve()),
                    'runs': sorted(runs, key=lambda x:x['created_at'], reverse=True)}

    def create_suite(self, payload):
        task = text(payload.get('task'), '任务', 256)
        name = text(payload.get('name'), '测试集名称', 256)
        criteria = optional_criteria(payload.get('criteria', ''))
        layouts = payload.get('layouts')
        if not isinstance(layouts, list) or not 1 <= len(layouts) <= 100:
            raise ValueError('测试集需要 1～100 张布局图，默认目标数量为 15 张')
        count = target_count(payload.get('target_count', 15))
        if len(layouts) > count:
            raise ValueError('布局图片超过目标数量，请调整目标数量或移除多余图片')
        key = suite_identifier(payload['directory_name']) if payload.get('directory_name') else uuid.uuid4().hex
        parent = self.root / 'suites'
        parent.mkdir(parents=True, exist_ok=True)
        if (parent / key).exists():
            raise ValueError('同名 suite 已存在，请选择补拍或更换名称')
        stage = Path(tempfile.mkdtemp(prefix='.suite-', dir=parent))
        record = dict(id=key, task=task, name=name, criteria=criteria, created_at=utc(),
                      capture_mode=self.app.config['mode'], target_count=count, revision=0, layouts=[])
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
                if (parent / key).exists():
                    raise ValueError('同名 suite 已存在，请选择补拍或更换名称')
                os.rename(stage, parent / key)
            return record
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def update_suite(self, payload):
        with self.lock:
            record = self.read('suites', payload.get('suite_id'))
            if any(r['suite_id'] == record['id'] for r in self.catalog()['runs']):
                raise ValueError('已有评测轮次使用此 suite，请另存新 suite 后补拍，以保留原评测布局')
            if type(payload.get('revision')) is not int or payload['revision'] != record.get('revision', 0):
                raise ValueError('suite 已更新，请重新加载后补拍')
            count = target_count(payload.get('target_count', 15))
            layouts = payload.get('layouts')
            if not isinstance(layouts, list) or not 1 <= len(layouts) <= count:
                raise ValueError('图片数量应为 1～目标数量，请调整数量或移除多余图片')
            # Stage all image copies before publishing an updated record.
            folder = self.root / 'suites' / record['id']
            old_paths = {self.app.asset(item['url']) for item in record['layouts']}
            staged = []
            try:
                for i, item in enumerate(layouts):
                    source = self.app.asset(item['url'])
                    with Image.open(source) as image:
                        image.verify()
                    body = source.read_bytes()
                    dest = folder / (uuid.uuid4().hex + '.png')
                    dest.write_bytes(body)
                    staged.append(dest)
                updated = dict(record, name=text(payload.get('name'), '测试集名称', 256),
                               task=text(payload.get('task'), '任务', 256),
                               criteria=optional_criteria(payload.get('criteria', record.get('criteria', ''))),
                               target_count=count, revision=record.get('revision', 0)+1,
                               layouts=[dict(index=i, label=text(item.get('label'), '布局名称', 256),
                                             url=self.app.url(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                                        for i,(item,path) in enumerate(zip(layouts,staged))])
                self.write('suites', updated)
            except Exception:
                for path in staged:
                    path.unlink(missing_ok=True)
                raise
            # Only remove old owned files after the new record is committed.
            for path in old_paths:
                if path.is_relative_to(folder):
                    path.unlink(missing_ok=True)
            return updated

    def delete_suite(self, payload):
        with self.lock:
            if payload.get('confirmed') is not True:
                raise ValueError('请再次确认删除 suite')
            record = self.read('suites', payload.get('suite_id'))
            if type(payload.get('revision')) is not int or payload['revision'] != record.get('revision', 0):
                raise ValueError('suite 已更新，请刷新列表后重新确认删除')
            referenced = any(r['suite_id'] == record['id'] for r in self.catalog()['runs'])
            if referenced:
                # Run records retain URLs into this folder. Remove the suite
                # from selection while keeping historical layouts available.
                record.update(deleted_at=utc(), revision=record.get('revision', 0) + 1)
                self.write('suites', record)
            else:
                shutil.rmtree(self.root / 'suites' / record['id'])
            return dict(deleted=True, suite_id=record['id'])

    def create_run(self, payload):
        with self.lock:
            suite = self.read('suites', payload.get('suite_id'))
            record = dict(id=uuid.uuid4().hex, suite_id=suite['id'], task=suite['task'], suite_name=suite['name'],
                          criteria=suite.get('criteria', ''), model=text(payload.get('model'), '模型', 256),
                          checkpoint=text(payload.get('checkpoint'), 'checkpoint', 2048),
                          round=text(payload.get('round'), '轮次', 256),
                          eval_config=str(payload.get('eval_config', ''))[:2048],
                          created_at=utc(), mode=('demo' if suite['capture_mode']=='demo' or self.app.config['mode']=='demo' else self.app.config['mode']), revision=0, cursor=0,
                          trials=[dict(**layout, status='pending', started_at=None, finished_at=None, note='') for layout in suite['layouts']])
            content = payload.get('eval_config_content')
            source = record['eval_config']
            if content is None and source:
                file = Path(source).expanduser().resolve()
                roots = [(self.app.output / 'eval_configs').resolve(), (self.root / 'runs').resolve()]
                if self.app.config.get('main_repo_root'):
                    roots.append(Path(self.app.config['main_repo_root']).resolve())
                roots.extend(Path(p['eval_config']).expanduser().resolve().parent
                             for p in self.app.control.options.get('models', {}).values() if p.get('eval_config'))
                if file.suffix.lower() not in ('.yaml', '.yml') or not any(file.is_relative_to(root) for root in roots):
                    raise ValueError('请选择已有评测 YAML 或提供编辑后的配置内容')
                if file.stat().st_size > 262144:
                    raise ValueError('评测配置文件超过 256 KiB')
                content = file.read_text(encoding='utf-8')
            folder = self.root / 'runs' / record['id']
            if content is not None:
                content = validate_eval_yaml(content)
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / config_filename(record['task'], record['round'])
                with dest.open('x', encoding='utf-8') as stream:
                    stream.write(content)
                record.update(eval_config=str(dest.resolve()), eval_config_source=source,
                              eval_config_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest())
            try:
                self.write('runs', record)
            except Exception:
                if folder.exists():
                    shutil.rmtree(folder)
                raise
            return self.summary(record)

    def update_run_config(self, record, content):
        with self.lock:
            content = validate_eval_yaml(content)
            folder = self.root / 'runs' / record['id']
            dest = folder / config_filename(record['task'], record['round'])
            fd, tmp = tempfile.mkstemp(dir=folder, prefix='.config-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, dest)
            finally:
                Path(tmp).unlink(missing_ok=True)
            record.update(eval_config=str(dest.resolve()),
                          eval_config_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
                          revision=record['revision'] + 1)
            self.write('runs', record)
            return record

    def summary(self, record):
        statuses = [t['status'] for t in record['trials']]
        success, failure = statuses.count('success'), statuses.count('failure')
        completed = success + failure
        return dict(record, metrics=dict(total=len(statuses), success=success, failure=failure,
                    aborted=statuses.count('aborted'), pending=statuses.count('pending'), running=statuses.count('running'),
                    completed=completed, success_rate=success/completed if completed else None,
                    all_scored=completed==len(statuses)))

    def save_results(self, key):
        with self.lock:
            record = self.read('runs', key)
            folder = self.root / 'runs' / record['id']
            stem = config_filename(record['task'], record['round']).removesuffix('_eval.yaml')
            paths = dict(json=str(folder / (stem + '_results.json')), csv=str(folder / (stem + '_results.csv')))
            record.update(result_files=paths, saved_at=utc(), revision=record['revision']+1)
            self.write('runs', record)
            for kind, content in (('json', json.dumps(self.summary(record), ensure_ascii=False, indent=2)),
                                  ('csv', self.export_csv(key))):
                fd, tmp = tempfile.mkstemp(dir=folder, prefix='.results-')
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(tmp, paths[kind])
                finally:
                    Path(tmp).unlink(missing_ok=True)
            return self.summary(record)

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
                trial.pop('start_error', None)
            elif action == 'result':
                failed_pending = trial['status'] == 'pending' and bool(trial.get('failed_attempts'))
                if (trial['status'] != 'running' and not failed_pending) or payload.get('result') not in ('success', 'failure', 'aborted'):
                    raise ValueError('先标记开始，再记录成功、失败或中止')
                note = payload.get('note', '')
                if not isinstance(note, str) or len(note) > 4096:
                    raise ValueError('备注必须是最多 4096 字符的文字')
                if failed_pending:
                    trial['started_at'] = trial['failed_attempts'][-1].get('started_at')
                trial.update(status=payload['result'], note=note, finished_at=utc())
                trial.pop('start_error', None)
            elif action == 'select':
                target = payload.get('trial_index')
                if type(target) is not int or not 0 <= target < len(record['trials']):
                    raise ValueError('请选择有效的测试小项')
                if any(t['status'] == 'running' for t in record['trials']):
                    raise ValueError('请先结束当前评测并保存结果，再选择测试小项')
                selected = record['trials'][target]
                if selected['status'] in ('success', 'failure', 'aborted'):
                    if payload.get('retest') is not True:
                        raise ValueError('此项已有结果，请选择重测')
                    previous = {k: v for k, v in selected.items() if k != 'previous_results'}
                    selected.setdefault('previous_results', []).append(previous)
                    selected.update(status='pending', started_at=None, finished_at=None, note='')
                    for key in ('log_path', 'reset_log_path', 'reset_error', 'start_error',
                                'failed_attempts', 'restarted_attempts', 'result_updated_at'):
                        selected.pop(key, None)
                record['cursor'] = target
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

    def update_result(self, payload):
        with self.lock:
            record = self.read('runs', payload.get('run_id'))
            if type(payload.get('revision')) is not int or payload['revision'] != record['revision']:
                raise ValueError('记录已更新，请刷新后重试（避免多个页面覆盖）')
            index = payload.get('trial_index')
            if type(index) is not int or not 0 <= index < len(record['trials']):
                raise ValueError('请选择已有结果的样本')
            trial = record['trials'][index]
            if trial['status'] not in ('success', 'failure', 'aborted'):
                raise ValueError('只能修改已记录的结果')
            result = payload.get('result')
            if result not in ('success', 'failure', 'aborted'):
                raise ValueError('请选择成功、失败或中止')
            note = payload.get('note', trial.get('note', ''))
            if not isinstance(note, str) or len(note) > 4096:
                raise ValueError('备注必须是最多 4096 字符的文字')
            trial.update(status=result, note=note, result_updated_at=utc())
            record['revision'] += 1
            self.write('runs', record)
            return self.save_results(record['id'])

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
