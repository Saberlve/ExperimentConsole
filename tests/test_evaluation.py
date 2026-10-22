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

    def test_generated_round_keeps_each_model_profile_and_does_not_follow_base_edits(self):
        import yaml
        for model, profile in (('ACT', 'xarm7_eef10'), ('pi05', 'xarm7_eef10'),
                               ('N0', 'xarm7_eef_n0vtla'), ('FTP1', 'xarm7_eef_ftp1')):
            with self.subTest(model=model):
                folder = self.app.output / 'eval_configs'
                folder.mkdir(exist_ok=True)
                base = folder / (model + '_base.yaml')
                content = 'deployment_profile: ' + profile + '\naction_space: eef10\nfps: 15\n'
                base.write_text(content)
                record = self.service.create_run(dict(suite_id=self.suite['id'], model=model,
                    checkpoint='/ckpt/' + model, round='profile-test', eval_config=str(base)))
                saved = Path(record['eval_config'])
                self.assertNotEqual(saved, base)
                self.assertEqual(saved.read_text(), content)
                base.write_text('deployment_profile: null\n')
                self.assertEqual(yaml.safe_load(saved.read_text())['deployment_profile'], profile)

    def run_record(self,model='ACT',suite=None):
        return self.service.create_run(dict(suite_id=(suite or self.suite)['id'],model=model,checkpoint='/ckpt/'+model,round='round-1'))

    def test_new_round_reads_nested_task_config_from_configured_root(self):
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        root = Path(external.name) / 'FTP1'
        source = root / 'charger_press' / 'FTP1_charger_press.yaml'
        source.parent.mkdir(parents=True)
        source.write_text('deployment_profile: xarm7_eef_ftp1\naction_space: eef10\nfps: 15\n')
        self.app.control.options['models'] = {'FTP1': dict(
            eval_config=str(root / 'egg_pick/default.yaml'), eval_config_root=str(root))}
        record = self.service.create_run(dict(suite_id=self.suite['id'],model='FTP1',
            checkpoint='/ckpt/FTP1',round='nested',eval_config=str(source)))
        self.assertEqual(Path(record['eval_config']).read_text(), source.read_text())
        self.assertEqual(record['eval_config_source'], str(source))

    def test_delete_run_removes_only_selected_record_and_survives_restart(self):
        run, other = self.run_record(), self.run_record('pi05')
        result = self.service.delete_run(dict(run_id=run['id'], revision=run['revision'], confirmed=True))
        self.assertTrue(result['deleted'])
        restarted = Application(self.config).evaluation
        self.assertEqual([r['id'] for r in restarted.catalog()['runs']], [other['id']])
        self.assertEqual(restarted.read('suites', self.suite['id'])['id'], self.suite['id'])
        self.assertTrue(all(self.app.asset(t['url']).is_file() for t in other['trials']))
        with self.assertRaisesRegex(ValueError, '评测记录已删除'):
            restarted.read('runs', run['id'])

    def test_delete_run_requires_confirmation_and_current_revision(self):
        run = self.run_record()
        for extra in (dict(revision=run['revision']), dict(revision=True, confirmed=True),
                      dict(revision=run['revision']+1, confirmed=True)):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.service.delete_run(dict(run_id=run['id'], **extra))
        self.assertEqual(len(self.service.catalog()['runs']), 1)

    def test_delete_run_rejects_running_trial_and_associated_process(self):
        run = self.run_record()
        payload = dict(run_id=run['id'], revision=run['revision'], confirmed=True)
        process = Mock()
        process.poll.return_value = None
        self.app.control.jobs['evaluation'] = dict(run_id=run['id'], process=process)
        with self.assertRaisesRegex(ValueError, '先停止'):
            self.service.delete_run(payload)
        self.app.control.jobs.clear()
        run = self.service.transition(dict(run_id=run['id'], revision=run['revision'], action='start'))
        with self.assertRaisesRegex(ValueError, '先结束'):
            self.service.delete_run(dict(payload, revision=run['revision']))

    def test_select_third_trial_preserves_other_items_and_retest_history(self):
        run = self.run_record()
        original = copy.deepcopy(run['trials'])
        def transition(**values):
            nonlocal run
            run = self.app.control.transition_trial(dict(run_id=run['id'], revision=run['revision'], **values))
        transition(action='select', trial_index=2)
        self.assertEqual(run['cursor'], 2)
        self.assertEqual(run['trials'], original)
        transition(action='start')
        with self.assertRaisesRegex(ValueError, '先结束'):
            transition(action='select', trial_index=4)
        transition(action='result', result='failure', note='first attempt')
        with self.assertRaisesRegex(ValueError, '重测'):
            transition(action='select', trial_index=2)
        transition(action='select', trial_index=2, retest=True)
        self.assertEqual(run['trials'][2]['status'], 'pending')
        self.assertEqual(run['trials'][2]['previous_results'][0]['status'], 'failure')
        self.assertEqual(run['trials'][2]['previous_results'][0]['note'], 'first attempt')
        for i in range(10):
            if i != 2:
                self.assertEqual(run['trials'][i], original[i])
        for index in (-1, 10, True, '3'):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, '有效'):
                transition(action='select', trial_index=index)

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

    def test_delete_suite_requires_confirmation_and_current_revision(self):
        payload = dict(suite_id=self.suite['id'], revision=0)
        folder = self.service.root / 'suites' / self.suite['id']
        for confirmed in (None, False, 'true', 1):
            with self.assertRaisesRegex(ValueError, '再次确认'):
                self.service.delete_suite(dict(payload, confirmed=confirmed))
        with self.assertRaisesRegex(ValueError, '已更新'):
            self.service.delete_suite(dict(payload, confirmed=True, revision=1))
        self.assertTrue(folder.is_dir())
        self.service.delete_suite(dict(payload, confirmed=True))
        self.assertFalse(folder.exists())
        self.assertEqual(self.service.catalog()['suites'], [])

    def test_delete_used_suite_preserves_history_and_blocks_new_runs(self):
        run = self.run_record()
        self.service.delete_suite(dict(suite_id=self.suite['id'], revision=0, confirmed=True))
        self.assertEqual(self.service.catalog()['suites'], [])
        self.assertEqual(self.service.catalog()['runs'][0]['id'], run['id'])
        for layout in run['trials']:
            self.assertTrue(self.app.asset(layout['url']).is_file())
        # Existing rounds can still advance using their saved layout data.
        started = self.transition(run, 'start')
        self.assertEqual(started['trials'][0]['status'], 'running')
        with self.assertRaisesRegex(ValueError, '已删除'):
            self.run_record()
        with self.assertRaisesRegex(ValueError, '已删除'):
            self.service.update_suite(dict(suite_id=self.suite['id'], revision=0))
        restarted = Application(self.config).evaluation
        self.assertEqual(restarted.catalog()['suites'], [])
        self.assertEqual(restarted.catalog()['runs'][0]['id'], run['id'])

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

    def test_update_previous_result_changes_metrics_and_exports_without_robot(self):
        run = self.run_record()
        run = self.transition(run, 'start')
        run = self.transition(run, 'result', result='failure', note='original note')
        original = dict(run['trials'][0])
        run = self.transition(run, 'next')
        run = self.transition(run, 'start')
        with patch.object(self.app, 'release_preview') as release, \
             patch.object(self.app, 'enable_preview') as preview, \
             patch.object(self.app.control, 'finish_trial') as finish:
            for result in ('success', 'aborted', 'failure'):
                run = self.service.update_result(dict(run_id=run['id'], revision=run['revision'],
                    trial_index=0, result=result, note='corrected note'))
                self.assertEqual(run['cursor'], 1)
                self.assertEqual(run['trials'][1]['status'], 'running')
                self.assertEqual(run['trials'][0]['started_at'], original['started_at'])
                self.assertEqual(run['trials'][0]['finished_at'], original['finished_at'])
                self.assertEqual(run['trials'][0]['note'], 'corrected note')
                self.assertEqual(run['metrics']['success'], int(result == 'success'))
                self.assertEqual(run['metrics']['completed'], int(result != 'aborted'))
                self.assertEqual(json.loads(Path(run['result_files']['json']).read_text())['trials'][0]['status'], result)
                self.assertIn(result, Path(run['result_files']['csv']).read_text())
                self.assertEqual(self.service.catalog()['runs'][0]['trials'][0]['status'], result)
            release.assert_not_called(); preview.assert_not_called(); finish.assert_not_called()

    def test_update_result_rejects_unrecorded_rows_invalid_input_and_stale_revision(self):
        run = self.run_record()
        payload = dict(run_id=run['id'], revision=run['revision'], trial_index=0, result='success')
        with self.assertRaisesRegex(ValueError, '只能修改已记录'):
            self.service.update_result(payload)
        run = self.transition(run, 'start')
        run = self.transition(run, 'result', result='failure')
        with self.assertRaisesRegex(ValueError, '记录已更新'):
            self.service.update_result(payload)
        payload['revision'] = run['revision']
        for changes in (dict(trial_index=-1), dict(trial_index=True), dict(trial_index=100),
                        dict(result='pending'), dict(note='a' * 4097)):
            with self.assertRaises(ValueError):
                self.service.update_result(dict(payload, **changes))
        self.assertEqual(self.service.read('runs', run['id'])['trials'][0]['status'], 'failure')

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
        deletion = dict(suite_id=suite['id'], revision=0, confirmed=True)
        self.assertEqual(self.request('/api/evaluation/suites/delete',deletion)[0],403)
        self.assertEqual(self.request('/api/evaluation/suites/delete',dict(deletion,confirmed=False),header)[0],400)
        self.assertEqual(self.request('/api/evaluation/suites/delete',deletion,header)[0],200)
        catalog = self.request('/api/evaluation/catalog')[1]
        self.assertEqual(catalog['suites'], [])
        self.assertEqual(catalog['runs'][0]['id'], run['id'])
        record = self.app.evaluation.read('runs', run['id'])
        record['trials'][0]['status'] = 'failure'
        self.app.evaluation.write('runs', record)
        correction = dict(run_id=run['id'], revision=record['revision'], trial_index=0, result='success')
        self.assertEqual(self.request('/api/evaluation/update-result',correction)[0],403)
        code, updated = self.request('/api/evaluation/update-result',correction,header)
        self.assertEqual(code,200)
        self.assertEqual(updated['metrics']['success'],1)
        self.assertEqual(updated['cursor'],record['cursor'])

if __name__=='__main__':unittest.main()
