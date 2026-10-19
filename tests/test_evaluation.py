"""Layout and result persistence tests; never connect cameras or robots."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from pathlib import Path
from backend.server import Application
from tests import test_console as console
png = console.png


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=dict(mode='reference_only',output_dir=self.tmp.name,datasets={})
        self.app=Application(self.config);self.service=self.app.evaluation
        image=self.app.upload(png())
        self.suite=self.service.create_suite(dict(task='egg_pick',name='v1',criteria='放入容器',layouts=[dict(url=image['url'],label=f'layout {i+1}') for i in range(10)]))

    def run_record(self,model='ACT',suite=None):
        return self.service.create_run(dict(suite_id=(suite or self.suite)['id'],model=model,checkpoint='/ckpt/'+model,round='round-1'))

    def test_each_round_snapshots_edited_yaml_in_its_results_folder(self):
        import hashlib
        source = self.app.output / 'eval_configs/original.yaml'
        source.parent.mkdir()
        source.write_text('# original\nfps: 15\n')
        content = '# edited for this round\nfps: 10\n'
        payload = dict(suite_id=self.suite['id'], model='ACT', checkpoint='fixture', round='round-1',
                       eval_config=str(source), eval_config_content=content)
        first = self.service.create_run(payload)
        file = Path(first['eval_config'])
        self.assertEqual(file.parent, self.service.root / 'runs' / first['id'])
        self.assertEqual(file.name, 'egg_pick_round-1_eval.yaml')
        self.assertEqual(file.read_text(), content)
        self.assertEqual(first['eval_config_source'], str(source))
        self.assertEqual(first['eval_config_sha256'], hashlib.sha256(content.encode()).hexdigest())
        self.assertEqual(source.read_text(), '# original\nfps: 15\n')
        second = self.service.create_run(dict(payload, round='round-2', eval_config_content='fps: 5\n'))
        self.assertNotEqual(first['eval_config'], second['eval_config'])
        self.assertEqual(file.read_text(), content)
        fallback = dict(payload)
        del fallback['eval_config_content']
        third = self.service.create_run(fallback)
        self.assertEqual(Path(third['eval_config']).read_text(), source.read_text())
        count = len(self.service.catalog()['runs'])
        with self.assertRaisesRegex(ValueError, 'YAML 格式错误'):
            self.service.create_run(dict(payload, eval_config_content='fps: [broken'))
        self.assertEqual(len(self.service.catalog()['runs']), count)

    def transition(self,run,action,**kwargs):
        return self.service.transition(dict(run_id=run['id'],revision=run['revision'],action=action,**kwargs))

    def test_task_and_model_isolation(self):
        a=self.run_record();b=self.run_record('PI05')
        suite=self.service.create_suite(dict(task='pick_bar',name='v1',criteria='抬起',layouts=self.suite['layouts']))
        c=self.run_record('ACT',suite)
        a=self.transition(a,'start');a=self.transition(a,'result',result='success')
        self.assertEqual(a['metrics']['success'],1)
        self.assertEqual(self.service.summary(self.service.read('runs',b['id']))['metrics']['completed'],0)
        self.assertEqual(c['task'],'pick_bar')
        self.assertEqual(a['trials'][0]['sha256'],b['trials'][0]['sha256'])
        self.assertNotEqual(a['suite_id'],c['suite_id'])

    def test_progress_denominator_abort_and_reload(self):
        r=self.run_record()
        self.assertIsNone(r['metrics']['success_rate'])
        for result in ['success','failure','aborted']:
            r=self.transition(r,'start');r=self.transition(r,'result',result=result,note='log/path')
            r=self.transition(r,'next')
        self.assertEqual(r['metrics']['success_rate'],.5)
        self.assertEqual(r['metrics']['completed'],2)
        self.assertEqual(r['metrics']['aborted'],1)
        self.assertEqual(r['metrics']['pending'],7)
        restarted=Application(self.config).evaluation
        saved=restarted.summary(restarted.read('runs',r['id']))
        self.assertEqual(saved,r)
        self.assertIn('log/path',restarted.export_csv(r['id']))
        self.assertEqual(saved['cursor'],3)

    def test_transitions_reject_duplicates_and_unscored_next(self):
        r=self.run_record()
        with self.assertRaises(ValueError):self.transition(r,'next')
        with self.assertRaises(ValueError):self.transition(r,'result',result='success')
        old=copy.deepcopy(r);r=self.transition(r,'start')
        with self.assertRaises(ValueError):self.transition(old,'start')
        r=self.transition(r,'result',result='success')
        with self.assertRaises(ValueError):self.transition(r,'result',result='failure')
        self.assertEqual(self.service.read('runs',r['id'])['trials'][0]['status'],'success')

    def test_layouts_frozen_and_tampering_rejected(self):
        r=self.run_record()
        path=self.app.asset(r['trials'][0]['url']);path.write_bytes(png('blue'))
        with self.assertRaisesRegex(ValueError,'图片已变更'):self.transition(r,'start')
        self.assertEqual(self.service.read('runs',r['id'])['revision'],0)
        with self.assertRaises(ValueError):self.service.read('runs','../x')

    def test_camera_release_before_start_and_resume_after_result(self):
        self.app.config['mode']='legacy_http'
        self.app.config['camera_url']='http://127.0.0.1:8766'
        service=Mock()
        service.thread=None
        service.process.poll.return_value=0
        self.app.camera_service=service
        r=self.run_record()
        with patch('backend.server.app_camera_probe', side_effect=URLError(ConnectionRefusedError('closed'))):
            r=self.transition(r,'start')
        service.close.assert_called_once()
        self.assertFalse(self.app.preview_enabled)
        with self.assertRaisesRegex(ValueError,'先记录结果'):
            self.app.enable_preview()
        r=self.transition(r,'result',result='success')
        service.stop.is_set.return_value=True
        with patch('backend.server.CameraService') as factory:
            self.app.enable_preview()
            factory.return_value.start.assert_called_once()
        self.assertTrue(self.app.preview_enabled)

    def test_release_failure_does_not_start_or_stop_external_process(self):
        self.app.config['mode']='legacy_http'
        self.app.config['camera_url']='http://127.0.0.1:8766'
        service=Mock();service.thread=None;service.process=None
        self.app.camera_service=service
        r=self.run_record()
        with patch('backend.server.app_camera_probe') as probe:
            probe.return_value.__enter__=Mock();probe.return_value.__exit__=Mock()
            with self.assertRaisesRegex(ValueError,'外部服务'):
                self.transition(r,'start')
        self.assertEqual(self.service.read('runs',r['id'])['trials'][0]['status'],'pending')

    def test_no_hardware_in_reference_mode(self):
        self.assertEqual(self.app.camera.cameras(),[])
        self.assertIsNone(self.app.camera_service)
        self.assertFalse(self.app.replay.enabled)


    def test_named_suite_target_count_and_supplement(self):
        import io
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (16, 12), 'green').save(image, format='PNG')
        url = self.app.upload(image.getvalue())['url']
        payload = dict(task='egg_pick', name='egg_new', directory_name='egg_new',
                       layouts=[dict(url=url, label='布局 1')])
        suite = self.app.evaluation.create_suite(payload)
        self.assertEqual(suite['id'], 'egg_new')
        self.assertEqual(suite['target_count'], 15)
        self.assertEqual(suite['criteria'], '')
        self.assertTrue((self.app.output / 'vla_evaluations/suites/egg_new/record.json').is_file())
        with self.assertRaisesRegex(ValueError, '同名'):
            self.app.evaluation.create_suite(payload)
        with self.assertRaises(ValueError):
            self.app.evaluation.create_suite(dict(payload, directory_name='../escape'))
        update = dict(payload, suite_id=suite['id'], revision=0, target_count=2,
                      layouts=suite['layouts'] + [dict(url=url, label='布局 2')])
        result = self.app.evaluation.update_suite(update)
        self.assertEqual(len(result['layouts']), 2)
        self.assertEqual(result['target_count'], 2)
        for item in result['layouts']:
            self.assertTrue(self.app.asset(item['url']).is_file())
        with self.assertRaisesRegex(ValueError, '已更新'):
            self.app.evaluation.update_suite(update)
        self.app.evaluation.create_run(dict(suite_id=suite['id'], model='ACT', checkpoint='fixture', round='r1'))
        with self.assertRaisesRegex(ValueError, '已有评测轮次'):
            self.app.evaluation.update_suite(dict(update, revision=1))

class EvaluationHTTPTests(unittest.TestCase):
    setUp = console.ConsoleTests.setUp
    tearDown = console.ConsoleTests.tearDown
    request = console.ConsoleTests.request
    def test_layout_http_token_and_persistence(self):
        header={'X-Replay-Token':self.app.replay.token}
        image=self.request('/api/upload',png())[1]
        payload=dict(task='egg',name='v1',criteria='success rule',layouts=[dict(url=image['url'],label='one')])
        self.assertEqual(self.request('/api/evaluation/suites',payload)[0],403)
        code,suite=self.request('/api/evaluation/suites',payload,header);self.assertEqual(code,200)
        _,run=self.request('/api/evaluation/runs',dict(suite_id=suite['id'],model='ACT',checkpoint='checkpoint',round='r1'),header)
        self.assertEqual(run['mode'],'demo')
        self.assertEqual(self.request('/api/evaluation/run?id='+run['id'])[1]['id'],run['id'])
        self.assertEqual(self.request('/api/evaluation/export?id='+run['id'])[0],200)
        self.assertEqual(len(self.request('/api/evaluation/catalog')[1]['runs']),1)

if __name__=='__main__':unittest.main()
