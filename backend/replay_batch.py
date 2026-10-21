"""Sequential CSV replay and named dual-view exports through the local console API."""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from adapters.camera import utc


class ReplayBatch:
    def __init__(self, source, output, url='http://127.0.0.1:8089'):
        self.source, self.output = Path(source).resolve(), Path(output).resolve()
        self.url = url.rstrip('/')
        self.manifest = self.output / 'batch.json'
        self.data = None
        self.owned_job = None

    def request(self, route, payload=None):
        headers, raw = {}, None
        if payload is not None:
            status = self.request('/api/status')
            headers['X-Replay-Token'] = status['replay']['token']
            if isinstance(payload, bytes):
                raw = payload
                headers['Content-Type'] = 'text/csv'
            else:
                raw = json.dumps(payload).encode()
                headers['Content-Type'] = 'application/json'
        try:
            with urlopen(Request(self.url + route, data=raw, headers=headers), timeout=40) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f'{route}: {exc.read().decode(errors="replace")}') from exc

    def save(self):
        temp = self.manifest.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.manifest)

    def check_idle(self, previous=None):
        status = self.request('/api/replay/status')
        if status['status'] not in ('idle', 'completed', 'stopped', 'error'):
            raise RuntimeError('已有回放或停止流程尚未结束')
        if status.get('recording', {}).get('status') in ('preparing', 'waiting', 'recording', 'saving'):
            raise RuntimeError('上一段录制尚未收尾')
        if previous and (status.get('id') != previous or status['status'] != 'completed'):
            raise RuntimeError('回放被停止或被其他任务替换，终止批处理')
        control = self.request('/api/evaluation/control')
        if control.get('stopping') or any(job.get('running') for job in control.get('jobs', {}).values()):
            raise RuntimeError('评测控制台仍有运行中的任务')

    def prepare(self, expected_count=None):
        files = sorted(self.source.glob('*.csv'))
        if not files or expected_count is not None and len(files) != expected_count:
            raise ValueError(f'CSV 数量不符合预期：{len(files)}')
        self.check_idle()
        self.output.mkdir(parents=True, exist_ok=False)
        self.data = dict(source=str(self.source), output=str(self.output), created_at=utc(),
                         status='preparing', completed=0, items=[])
        self.save()
        try:
            for index, path in enumerate(files, 1):
                raw = path.read_bytes()
                report = self.request('/api/replay/upload', raw)
                self.data['items'].append(dict(name=path.name, source=str(path),
                    sha256=hashlib.sha256(raw).hexdigest(), job_id=report['id'],
                    frames=report['frames'], fps=report['fps'], duration_s=report['duration_s'],
                    start_xyz_mm=report['start_xyz_mm'], end_xyz_mm=report['end_xyz_mm'],
                    max_step_mm=report['max_step_mm'], status='prepared'))
                self.save()
                print(f'PREPARED {index}/{len(files)} {path.name} {report["duration_s"]:.2f}s', flush=True)
            self.data['status'] = 'prepared'
            self.save()
        except BaseException as exc:
            self.data.update(status='error', error=str(exc))
            self.save()
            raise
        return self.data

    def wait_for_recording(self, run, duration):
        deadline = time.monotonic() + duration + 150
        while True:
            if (self.output / 'STOP').exists():
                raise RuntimeError('发现 STOP 文件，终止批处理')
            status = self.request('/api/replay/status')
            if status.get('id') != run['id']:
                raise RuntimeError('回放任务被替换，终止批处理')
            if status['status'] in ('stopping', 'stopped', 'stop_error', 'error'):
                raise RuntimeError('回放停止或失败：' + str(status.get('error') or status['status']))
            recording = status.get('recording', {})
            if recording.get('status') in ('error', 'cancelled'):
                raise RuntimeError('录制失败：' + str(recording.get('error') or recording['status']))
            if status['status'] == 'completed' and status.get('ended_at') and recording.get('status') == 'saved':
                return status
            if time.monotonic() > deadline:
                raise RuntimeError('回放或视频收尾超时，终止批处理')
            time.sleep(.2)

    def export(self, item, status):
        import cv2
        views = status['recording']['views']
        if len(views) != 2 or any(view['frames'] <= 0 for view in views):
            raise RuntimeError('双视角视频缺失或没有录制帧')
        result = []
        for label in ('全景', '腕部'):
            matches = [view for view in views if view['label'] == label]
            if len(matches) != 1:
                raise RuntimeError('无法明确识别录制视角：' + label)
            view = matches[0]
            target = self.output / label / (Path(item['name']).stem + '.mp4')
            target.parent.mkdir(exist_ok=True)
            with Path(view['path']).open('rb') as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination)
            video = cv2.VideoCapture(str(target))
            try:
                if not video.isOpened() or int(video.get(cv2.CAP_PROP_FRAME_COUNT)) != view['frames'] or not video.read()[0]:
                    raise RuntimeError('导出视频验证失败：' + str(target))
            finally:
                video.release()
            result.append(dict(view=label, path=str(target), frames=view['frames'],
                               duration_s=view['duration_s']))
        return result

    def execute(self, resume=False):
        self.data = json.loads(self.manifest.read_text(encoding='utf-8'))
        resumable = resume and self.data['status'] in ('error', 'stopped')
        if not resumable and (self.data['status'] != 'prepared' or self.data['completed']):
            raise RuntimeError('仅可执行已经校验且尚未开始的批次，避免重复机械臂动作')
        if self.data['completed'] != sum(item['status'] == 'completed' for item in self.data['items']):
            raise RuntimeError('完成记录不一致，不能继续批处理')
        for item in self.data['items']:
            if hashlib.sha256(Path(item['source']).read_bytes()).hexdigest() != item['sha256']:
                raise RuntimeError('CSV 校验后发生变化：' + item['name'])
        self.check_idle()
        if resumable:
            self.data.setdefault('interruptions', []).append(dict(
                error=self.data.get('error'), ended_at=self.data.get('ended_at'), resumed_at=utc()))
            self.data.pop('error', None)
            self.data.pop('ended_at', None)
        self.data.update(status='running')
        self.data.setdefault('started_at', utc())
        self.save()
        previous = None
        try:
            for index, item in enumerate(self.data['items'], 1):
                if item['status'] == 'completed':
                    continue
                if (self.output / 'STOP').exists():
                    raise RuntimeError('发现 STOP 文件，终止批处理')
                self.check_idle(previous)
                self.owned_job = item['job_id']
                if item.get('run_id'):
                    item.setdefault('previous_attempts', []).append(dict(run_id=item.pop('run_id')))
                item['status'] = 'running'
                self.save()
                print(f'START {index}/{len(self.data["items"])} {item["name"]}', flush=True)
                run = self.request('/api/replay/start', dict(job_id=item['job_id'], confirmed=True, record_video=True))
                item['run_id'] = run['id']
                self.save()
                status = self.wait_for_recording(run, item['duration_s'])
                item['videos'] = self.export(item, status)
                item.update(status='completed', recording=status['recording'], ended_at=utc())
                self.data['completed'] += 1
                self.save()
                previous = run['id']
                print(f'SAVED {index}/{len(self.data["items"])} {item["name"]}', flush=True)
            self.data.update(status='completed', ended_at=utc())
            self.save()
            print('COMPLETED: ' + str(self.output), flush=True)
        except BaseException as exc:
            self.data.update(status='stopped' if isinstance(exc, KeyboardInterrupt) else 'error', error=str(exc), ended_at=utc())
            self.save()
            if self.owned_job:
                try:
                    status = self.request('/api/replay/status')
                    if status.get('job_id') == self.owned_job and status['status'] in ('checking', 'starting', 'running'):
                        self.request('/api/replay/stop', {})
                except Exception as stop_error:
                    self.data['stop_error'] = str(stop_error)
                    self.save()
            raise
        return self.data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:8089')
    parser.add_argument('--expected-count', type=int)
    parser.add_argument('--execute', action='store_true', help='执行已校验的批次；不指定时只校验，不启动机械臂')
    parser.add_argument('--resume', action='store_true', help='明确允许重试中断的 CSV，并跳过已经完成的 CSV')
    args = parser.parse_args()
    batch = ReplayBatch(args.source, args.output, args.url)
    if args.resume and not args.execute:
        parser.error('--resume 必须与 --execute 一起使用')
    batch.execute(resume=args.resume) if args.execute else batch.prepare(args.expected_count)


if __name__ == '__main__':
    main()
