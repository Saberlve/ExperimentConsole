"""Hardware-free integration checks for the combined workbench."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from omegaconf import OmegaConf

from backend.checkpoints import MODELS, catalog, inspect_checkpoint
from backend.workbench import (
    Application, CONSOLE, index_page, load_config, make_workbench_handler)


class CheckpointCatalogTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'PALIGEMMA_TOKENIZER_PATH': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def checkpoint(self, model='ACT'):
        run = self.repo / 'results/Checkpoints' / model / 'trial'
        (run / 'checkpoints').mkdir(parents=True)
        path = run / 'checkpoints/steps_25_model.safetensors'
        path.write_bytes(b'fixture metadata only; never loaded')
        cfg = dict(framework=dict(name=MODELS[model][1]))
        OmegaConf.save(OmegaConf.create(cfg), run / 'config.yaml')
        (run / 'dataset_statistics.json').write_text(json.dumps({'fixture': {'action': {'mean': [0]}}}))
        if model != 'ACT':
            (run / 'paligemma_tokenizer.model').write_bytes(b'fixture; never loaded')
        return path

    def test_four_models_and_tokenizer_resolution(self):
        for model in MODELS:
            path = self.checkpoint(model)
            entry = inspect_checkpoint(path, model)
            self.assertTrue(entry['metadata_ready'], entry['issues'])
            self.assertEqual(bool(entry['tokenizer_path']), model != 'ACT')
        data = catalog(self.repo)
        self.assertEqual(len(data['models']), 4)
        self.assertEqual(sum(len(m['checkpoints']) for m in data['models']), 4)

    def test_incomplete_mismatched_or_malformed_metadata(self):
        path = self.checkpoint('N0')
        (path.parent.parent / 'paligemma_tokenizer.model').unlink()
        self.assertFalse(inspect_checkpoint(path, 'N0')['metadata_ready'])
        self.assertFalse(inspect_checkpoint(path, 'ACT')['metadata_ready'])
        (path.parent.parent / 'config.yaml').write_text('framework: [broken')
        self.assertFalse(catalog(self.repo)['models'][2]['checkpoints'][0]['metadata_ready'])
        (path.parent.parent / 'dataset_statistics.json').write_text('not JSON')
        self.assertFalse(inspect_checkpoint(path, 'N0')['metadata_ready'])

    def test_no_external_symlink_weight_is_listed(self):
        path = self.checkpoint()
        external = self.repo / 'external.safetensors'
        external.write_bytes(b'fixture')
        (path.parent / 'escape.safetensors').symlink_to(external)
        entries = catalog(self.repo)['models'][1]['checkpoints']
        self.assertEqual([x['name'] for x in entries], [path.name])

    def test_three_models_share_one_environment_asset_and_act_ignores_it(self):
        shared = self.repo / 'shared/paligemma_tokenizer.model'
        shared.parent.mkdir()
        shared.write_bytes(b'fixture metadata only; never loaded')
        for model in MODELS:
            path = self.checkpoint(model)
            (path.parent.parent / 'paligemma_tokenizer.model').unlink(missing_ok=True)
        with patch.dict(os.environ, {'PALIGEMMA_TOKENIZER_PATH': str(shared)}):
            data = catalog(self.repo)
        paths = []
        for model in data['models']:
            entry = model['checkpoints'][0]
            self.assertTrue(entry['metadata_ready'], entry['issues'])
            if model['id'] == 'ACT':
                self.assertEqual(entry['tokenizer_path'], '')
            else:
                paths.append(entry['tokenizer_path'])
        self.assertEqual(paths, [str(shared)] * 3)
        self.assertEqual(data['shared_tokenizer_path'], str(shared))
        self.assertTrue(data['shared_tokenizer_available'])

    def test_bad_shared_environment_does_not_silently_use_another_vocabulary(self):
        n0 = self.checkpoint('N0')
        act = self.checkpoint('ACT')
        with patch.dict(os.environ, {'PALIGEMMA_TOKENIZER_PATH': str(self.repo / 'missing.model')}):
            self.assertFalse(inspect_checkpoint(n0, 'N0')['metadata_ready'])
            self.assertTrue(inspect_checkpoint(act, 'ACT')['metadata_ready'])



class WorkbenchHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.manager = Mock(repo=self.repo)
        self.manager.catalog.return_value = {'recipes': [], 'sources': [], 'experiments': []}
        self.manager.compose.return_value = ({'id': 'example'}, 'seed: 42\n')
        config = load_config(CONSOLE / 'config/default.json', str(self.repo / 'output'))
        config['camera_preview_on_start'] = False
        self.app = Application(config)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_workbench_handler(self.app, self.manager))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return response.read()

    def test_one_document_contains_both_workflows_and_unique_ids(self):
        from html.parser import HTMLParser
        class IdParser(HTMLParser):
            def __init__(self):
                super().__init__(); self.ids = []
            def handle_starttag(self, tag, attrs):
                self.ids.extend(v for k, v in attrs if k == 'id')
        html = self.get('/').decode()
        parser = IdParser(); parser.feed(html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        for name in ('training-stage', 'checkpoints-stage', 'console-stage', 'tr-editor', 'tr-horizon', 'vla-evaluation'):
            self.assertIn(name, parser.ids)
        # Check the page actually served, rather than only the unused HTML fragment.
        import re
        bindings = re.findall(r"\$\('([A-Za-z][A-Za-z0-9]*)'\)",
                              (CONSOLE / 'frontend/training.mjs').read_text())
        for name in bindings:
            self.assertIn('tr-' + name, parser.ids, f'Missing training DOM binding: {name}')
        self.assertNotIn('<iframe', html)
        self.assertIn('workbench.css', html)
        self.assertIn(b'window.consoleReady=main()', self.get('/app.js'))

    def test_existing_and_training_apis_share_server(self):
        self.assertEqual(json.loads(self.get('/api/status'))['mode'], 'demo')
        self.assertEqual(json.loads(self.get('/api/training/catalog'))['recipes'], [])
        self.assertEqual(len(json.loads(self.get('/api/checkpoints'))['models']), 4)
        self.assertIn('runs', json.loads(self.get('/api/evaluation/catalog')))
        for path in ('/workbench.mjs', '/training.mjs', '/workbench.css', '/evaluation.mjs'):
            self.assertTrue(self.get(path))

    def test_evaluation_yaml_preview_and_file_boundaries(self):
        from urllib.parse import quote
        file = self.repo / 'eval.yaml'
        file.write_text('task: egg_pick\nsteps: 100\n')
        result = json.loads(self.get('/api/evaluation/config?path=' + quote(str(file))))
        self.assertEqual(result, dict(path=str(file), content=file.read_text()))
        for path in (self.repo / 'secrets.json', self.repo.parent / 'outside.yaml'):
            with self.assertRaises(HTTPError) as error:
                self.get('/api/evaluation/config?path=' + quote(str(path)))
            self.assertEqual(error.exception.code, 400)

    def test_save_evaluation_config_versions_validation_and_auth(self):
        from urllib.parse import quote
        def save(content, authorized=True):
            headers = {'Content-Type': 'application/json'}
            if authorized:
                headers['X-Replay-Token'] = self.app.replay.token
            request = Request(self.base + '/api/evaluation/config',
                              json.dumps(dict(model='ACT', content=content)).encode(), headers=headers)
            with urlopen(request) as response:
                return json.load(response)
        content = '# retained comment\nfps: 15\nrobot:\n  robot_ip: 192.168.1.245\n'
        with self.assertRaises(HTTPError) as error:
            save(content, authorized=False)
        self.assertEqual(error.exception.code, 403)
        for invalid in ('fps: [broken', '- list', ''):
            with self.assertRaises(HTTPError) as error:
                save(invalid)
            self.assertEqual(error.exception.code, 400)
        first = save(content)
        second = save('fps: 10\n')
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(Path(first['path']).read_text(), content)
        self.assertTrue(Path(first['path']).is_relative_to(self.app.output / 'eval_configs/ACT'))
        preview = json.loads(self.get('/api/evaluation/config?path=' + quote(first['path'])))
        self.assertEqual(preview, first)
        self.app.control.options['models'] = {'ACT': {}}
        self.assertIn(first['path'], self.app.control.status()['models']['ACT']['eval_configs'])

    def test_nested_evaluation_config_can_be_read_from_configured_root(self):
        from urllib.parse import quote
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        root = Path(external.name) / 'FTP1'
        file = root / 'charger_press' / 'FTP1_charger_press.yaml'
        file.parent.mkdir(parents=True)
        file.write_text('fps: 15\n')
        self.app.control.options['models'] = {'FTP1': dict(
            eval_config=str(root / 'egg_pick/default.yaml'), eval_config_root=str(root))}
        result = json.loads(self.get('/api/evaluation/config?path=' + quote(str(file))))
        self.assertEqual(result['content'], file.read_text())
        outside = Path(external.name) / 'outside.yaml'
        outside.write_text('fps: 15\n')
        with self.assertRaises(HTTPError) as error:
            self.get('/api/evaluation/config?path=' + quote(str(outside)))
        self.assertEqual(error.exception.code, 400)

    def test_saved_first_frame_sources_load_sort_and_save_suite(self):
        from PIL import Image
        folder = self.repo / 'first_frames'
        folder.mkdir()
        for name in ('10_A.png', '2_A.png', '2_W.png'):
            Image.new('RGB', (16, 12), 'green').save(folder / name)
        (folder / 'escape.png').symlink_to(folder / '2_A.png')
        self.app.config['first_frame_roots'] = [str(folder)]
        sources = json.loads(self.get('/api/evaluation/sources'))
        source = next(s for s in sources if s['view'] == '第三人称视角（全景）')
        self.assertEqual(source['count'], 2)
        self.assertEqual(next(s for s in sources if s['view'] == '腕部视角')['count'], 1)
        body = json.dumps(dict(source_id=source['id'])).encode()
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + '/api/evaluation/sources/load', body))
        self.assertEqual(error.exception.code, 403)
        request = Request(self.base + '/api/evaluation/sources/load', body,
                          headers={'X-Replay-Token': self.app.replay.token, 'Content-Type': 'application/json'})
        with urlopen(request) as response:
            loaded = json.load(response)
        self.assertEqual([t['label'].split(' / ')[-1] for t in loaded['layouts']], ['2_A.png', '10_A.png'])
        for layout in loaded['layouts']:
            self.assertTrue(self.app.asset(layout['url']).is_file())
        suite = self.app.evaluation.create_suite(dict(task='egg_pick', name='v1', criteria='放入蛋托', layouts=loaded['layouts']))
        self.assertEqual(len(suite['layouts']), 2)
        old_id = suite['id']
        (self.app.evaluation.root / 'suites' / old_id).rename(self.app.evaluation.root / 'suites/egg_pick')
        suite['id'] = 'egg_pick'
        for layout in suite['layouts']:
            layout['url'] = layout['url'].replace(old_id, 'egg_pick')
        self.app.evaluation.write('suites', suite)
        self.assertEqual(self.app.evaluation.read('suites', 'egg_pick')['id'], 'egg_pick')
        self.assertEqual(self.app.evaluation.catalog()['suites'][0]['id'], 'egg_pick')
        run = self.app.evaluation.create_run(dict(suite_id='egg_pick', model='ACT', checkpoint='fixture', round='round-1'))
        self.assertEqual(run['suite_id'], 'egg_pick')
        self.assertTrue(self.app.asset(run['trials'][0]['url']).is_file())
        with self.assertRaises(ValueError):
            self.app.evaluation.read('suites', '../outside')
        with self.assertRaises(ValueError):
            self.app.evaluation.load_source('../../outside')

    def test_explicit_first_frame_root_excludes_capture_directory(self):
        from PIL import Image
        root = self.repo / 'VLA_test_fisrt'
        folder = root / 'third_person'
        folder.mkdir(parents=True)
        Image.new('RGB', (16, 12), 'green').save(folder / '2.png')
        other = self.repo / 'other_captures'
        other.mkdir()
        Image.new('RGB', (16, 12), 'red').save(other / '1_W.png')
        self.app.config.update(first_frame_roots=[str(root)], capture_root=str(other))
        sources = self.app.evaluation.source_catalog()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['directory'], str(folder))
        self.assertEqual(sources[0]['view'], '第三人称视角（全景）')

    def test_training_write_requires_same_origin_token(self):
        def request(headers):
            return Request(self.base + '/api/training/preview', b'{}', headers={'Content-Type': 'application/json', **headers})
        for headers in ({}, {'X-Replay-Token': self.app.replay.token, 'Origin': 'https://elsewhere.invalid'}):
            with self.assertRaises(HTTPError) as error:
                urlopen(request(headers))
            self.assertEqual(error.exception.code, 403)
        with urlopen(request({'X-Replay-Token': self.app.replay.token, 'Origin': self.base})) as response:
            self.assertEqual(json.load(response)['config'], 'seed: 42\n')
        self.manager.compose.assert_called_once_with({})


if __name__ == '__main__':
    unittest.main()
