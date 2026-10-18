"""Hardware-free regression tests for experiment config persistence and consumers."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from omegaconf import OmegaConf
from backend.training import Manager


class ExperimentManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.root = self.repo / 'experiments'
        (self.root / 'recipes').mkdir(parents=True)
        cfg = dict(framework={'name':'ACT'}, trainer={'max_train_steps':5000,'num_warmup_steps':10,
                   'is_resume':False,'scheduler_specific_kwargs':{'decay_steps':5000}}, seed=42,
                   datasets={'vla_data':{'per_device_batch_size':4}})
        OmegaConf.save(OmegaConf.create(cfg), self.root/'recipes/act.yaml')
        OmegaConf.save(OmegaConf.create({'recipes':{'act':{'name':'ACT','model':'ACT','format':'xarm17',
                      'robot_type':'xarm7_joint_eef','config':'recipes/act.yaml'}}}), self.root/'recipes.yaml')
        for name in ('one','two'):
            meta=self.repo/name/'meta';meta.mkdir(parents=True)
            features={'action':{'shape':[17]},'observation.state':{'shape':[17]},'camera_a':{},'camera_b':{}}
            (meta/'info.json').write_text(json.dumps({'features':features,'total_episodes':5}))
            (meta/'modality.json').write_text(json.dumps({'state':{'robot':{'start':0,'end':17,'original_key':'observation.state'}},
                'action':{'robot':{'start':0,'end':17,'original_key':'action'}},
                'video':{'third_camera':{'original_key':'camera_a'},'wrist_camera':{'original_key':'camera_b'}}}))
        OmegaConf.save(OmegaConf.create({'sources':{n:{'name':n,'location':n} for n in ('one','two')}}),self.root/'sources.yaml')
        self.mixtures={n+'_registered':[(n,1.,'xarm7_joint_eef')] for n in ('one','two')}
        self.manager=Manager(self.repo,self.root,mixtures=self.mixtures)
        self.request=dict(id='run_a',recipe='act',data='one',steps=1000,overrides='seed: 7')

    def test_source_switch_and_steps_reach_config(self):
        request=dict(self.request,data='two')
        _,text=self.manager.compose(request);cfg=OmegaConf.create(text)
        self.assertEqual(cfg.datasets.vla_data.data_root_dir,str(self.repo))
        self.assertEqual(cfg.datasets.vla_data.data_mix,'two_registered')
        self.assertNotIn('mixture_spec',cfg.datasets.vla_data)
        self.assertEqual(cfg.trainer.max_train_steps,1000)
        self.assertEqual(cfg.trainer.scheduler_specific_kwargs.decay_steps,1000)
        self.assertEqual(cfg.seed,7)
        self.assertEqual(cfg.run_root_dir, str(self.repo / "results/Checkpoints/ACT"))

    def test_saved_config_is_immutable_and_reloadable(self):
        self.manager.save(self.request)
        path=self.root/'saved/run_a/config.yaml';before=path.read_bytes()
        cfg=OmegaConf.load(self.root/'recipes/act.yaml');cfg.seed=999;OmegaConf.save(cfg,self.root/'recipes/act.yaml')
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(Manager(self.repo,self.root).records()[0]['id'],'run_a')
        with self.assertRaises(ValueError): self.manager.save(self.request)
        self.assertEqual(path.read_bytes(),before)

    def test_invalid_requests_never_save(self):
        for changes in ({'id':'../escape'},{'steps':True},{'steps':5},
                        {'overrides':'run_id: other'},{'overrides':'trainer:\n  imaginary: 1'},
                        {'overrides':'datasets:\n  vla_data:\n    mixture_spec: []'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.manager.save(dict(self.request,**changes))
        self.assertFalse((self.root/'saved').exists())

    def test_source_registration_and_duplicate(self):
        source={'id':'new','name':'New source','location':'two','task':'other'}
        self.manager.add_source(source)
        self.assertEqual(self.manager.sources()['new']['location'],'two')
        with self.assertRaises(ValueError):self.manager.add_source(source)
        _,text=self.manager.compose(dict(self.request,data='new'))
        self.assertEqual(OmegaConf.create(text).datasets.vla_data.data_mix,'two_registered')

    def test_incompatible_source(self):
        path=self.root/'recipes.yaml';obj=OmegaConf.load(path);obj.recipes.act.format='xarm8';OmegaConf.save(obj,path)
        with self.assertRaises(ValueError):self.manager.compose(self.request)

    def test_unregistered_dataset_rejected(self):
        self.manager._mixtures = {}
        with self.assertRaisesRegex(ValueError, 'data_mix'):
            self.manager.compose(self.request)
        with self.assertRaisesRegex(ValueError, 'data_mix'):
            self.manager.add_source(dict(id='new', name='new', location='two'))

    def test_original_training_and_inference_accept_generated_yaml(self):
        # Execute actual consumer functions with lightweight dependencies, no model/GPU imports.
        import os
        root=os.environ.get('MAIN_REPO_ROOT')
        if not root:
            self.skipTest('MAIN_REPO_ROOT is required for optional VLArmory consumer integration')
        repo=Path(root).resolve()
        cfg=OmegaConf.create(self.manager.compose(dict(self.request,data='two'))[1])
        def extract(path,name,namespace):
            tree=ast.parse((repo/path).read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            code=compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])),str(path),'exec')
            exec(code,namespace);return namespace[name]
        shared={'DATASET_NAMED_MIXTURES':self.mixtures}
        inference=extract(Path('deployment/model_server/policy_norm_processor.py'),'_resolve_robot_type',dict(shared))
        self.assertEqual(inference(OmegaConf.to_container(cfg)),'xarm7_joint_eef')
        from unittest.mock import Mock
        factory=Mock(side_effect=lambda root,name,robot_type,**kwargs:(str(root),name,robot_type))
        ns=dict(shared,Path=Path,logger=Mock(),make_LeRobotSingleDataset=factory,LeRobotMixtureDataset=lambda datasets,**kwargs:datasets)
        train=extract(Path('starVLA/dataloader/lerobot_datasets.py'),'get_vla_dataset',ns)
        result=train(cfg.datasets.vla_data)
        self.assertEqual(result, [((str(self.repo),'two','xarm7_joint_eef'),1.0)])


    def configure_base_fixture(self, model='ACT'):
        cfg = OmegaConf.load(self.root/'recipes/act.yaml')
        key = 'chunk_size' if model == 'ACT' else 'action_horizon'
        cfg.framework = {'name': model, key: 25}
        cfg.datasets.vla_data.action_horizon = '${framework.' + key + '}'
        cfg.datasets.vla_data.per_device_batch_size = 24 if model == 'ACT' else 12
        cfg.trainer.gradient_accumulation_steps = 1
        OmegaConf.save(cfg, self.root/'recipes/act.yaml')
        recipes = OmegaConf.load(self.root/'recipes.yaml')
        recipes.recipes.act.model = model
        recipes.recipes.act.gpu_count = 1 if model == 'ACT' else 2
        recipes.recipes.act.global_batch_size = 24
        OmegaConf.save(recipes, self.root/'recipes.yaml')

    def test_task_chunk_updates_model_loader_and_command(self):
        for model in ('ACT', 'PI05'):
            with self.subTest(model=model):
                self.configure_base_fixture(model)
                record, text = self.manager.compose(dict(self.request, action_horizon=15))
                cfg = OmegaConf.create(text)
                key = 'chunk_size' if model == 'ACT' else 'action_horizon'
                self.assertEqual(cfg.framework[key], 15)
                self.assertEqual(cfg.datasets.vla_data.action_horizon, 15)
                self.assertEqual(record['global_batch_size'], 24)
                self.assertIn('--nproc_per_node=' + ('1' if model == 'ACT' else '2'), record['command'])
                self.assertEqual(self.manager.catalog()['recipes'][0]['action_horizon'], 25)
                self.assertEqual(OmegaConf.load(self.root/'recipes/act.yaml').framework[key], 25)

    def test_invalid_chunk_and_global_batch_rejected(self):
        self.configure_base_fixture()
        for value in (0, -1, True, 1.5, 10001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.compose(dict(self.request, action_horizon=value))
        with self.assertRaisesRegex(ValueError, 'global batch'):
            self.manager.compose(dict(self.request, overrides='datasets:\n  vla_data:\n    per_device_batch_size: 12'))
        with self.assertRaisesRegex(ValueError, '冲突'):
            self.manager.compose(dict(self.request, action_horizon=15, overrides='framework:\n  chunk_size: 12'))
        self.assertFalse((self.root/'saved').exists())

    def test_advanced_chunk_updates_loader(self):
        self.configure_base_fixture()
        _, text = self.manager.compose(dict(self.request, overrides='framework:\n  chunk_size: 12'))
        cfg = OmegaConf.create(text)
        self.assertEqual(cfg.framework.chunk_size, 12)
        self.assertEqual(cfg.datasets.vla_data.action_horizon, 12)

    def test_default_root_discovers_only_cleaned_datasets(self):
        import shutil
        for name in ('alpha_cleaned', 'beta_cleaned', 'alpha_test'):
            shutil.copytree(self.repo/'one', self.repo/name)
        OmegaConf.save(OmegaConf.create({'data_root_dir': str(self.repo), 'sources': {}}), self.root/'sources.yaml')
        self.manager._mixtures = {name: [(name, 1., 'xarm7_joint_eef')]
                                  for name in ('alpha_cleaned', 'beta_cleaned')}
        catalog = self.manager.catalog()
        self.assertEqual([s['id'] for s in catalog['sources']], ['alpha_cleaned', 'beta_cleaned'])
        self.assertTrue(all(s['compatible_recipes'] == ['act'] for s in catalog['sources']))
        _, text = self.manager.compose(dict(self.request, data='beta_cleaned'))
        self.assertEqual(OmegaConf.create(text).datasets.vla_data.data_mix, 'beta_cleaned')

if __name__=='__main__':unittest.main()
