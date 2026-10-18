"""Hardware-free integration checks for the combined workbench."""
import json
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
        for name in ('training-stage', 'checkpoints-stage', 'console-stage', 'tr-editor', 'vla-evaluation'):
            self.assertIn(name, parser.ids)
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
