#!/usr/bin/env python3
"""VLArmory training configuration adapter for the experiment console."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import threading
import sys


from omegaconf import OmegaConf

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z')


def check_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('ID 仅支持字母、数字、下划线和连字符，最长 96 字符')
    return value


def load_yaml(path):
    return OmegaConf.to_container(OmegaConf.load(path), resolve=True)


def inspect_source(repo, source):
    path = Path(source['location']).expanduser()
    path = (path if path.is_absolute() else repo / path).resolve()
    info = json.loads((path / 'meta/info.json').read_text())
    if (path / 'meta/modality.json').exists():
        modality = json.loads((path / 'meta/modality.json').read_text())
    else:
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from examples.realRobots.xArm7.recording_metadata import xarm17_modality
        modality = xarm17_modality(info)
    features = info['features']
    dim = features['action']['shape'][0]
    if features['observation.state']['shape'] != [dim]:
        raise ValueError('state/action 维度不一致')
    if dim == 17:
        fmt, field, cameras = 'xarm17', 'robot', ['third_camera', 'wrist_camera']
    elif dim == 8:
        fmt, field, cameras = 'xarm8', 'joint_positions', ['external_upper_camera', 'wrist_camera']
    else:
        raise ValueError(f'尚未接入 {dim} 维数据格式')
    for kind, key in [('state', 'observation.state'), ('action', 'action')]:
        item = modality[kind][field]
        if item['original_key'] != key or item['start'] != 0 or item['end'] != dim:
            raise ValueError(f'{kind} 字段映射不符合 {fmt} 格式')
    for camera in cameras:
        original = modality['video'][camera]['original_key']
        if original not in features:
            raise ValueError(f'缺少相机字段 {original}')
    return dict(source, location=str(path), format=fmt, episodes=info.get('total_episodes'), frames=info.get('total_frames'))


class Manager:
    def __init__(self, repo, root=None, mixtures=None):
        self.repo = Path(repo).resolve()
        self.root = Path(root) if root else self.repo / 'examples/realRobots/xArm7/experiments'
        self.lock = threading.Lock()
        self._mixtures = mixtures

    def mixtures(self):
        if self._mixtures is None:
            if str(self.repo) not in sys.path:
                sys.path.insert(0, str(self.repo))
            from starVLA.dataloader.gr00t_lerobot.registry import DATASET_NAMED_MIXTURES
            return DATASET_NAMED_MIXTURES
        return self._mixtures

    def resolve_data_mix(self, source, recipe):
        name = Path(source['location']).name
        matches = [key for key, entries in self.mixtures().items()
                   if len(entries) == 1 and entries[0][0] == name
                   and entries[0][2] == recipe['robot_type'] and entries[0][1] > 0]
        if not matches:
            raise ValueError(f"数据 {name} 尚未注册适用于 {recipe['robot_type']} 的 data_mix；请先按仓库原有方式登记数据")
        return sorted(matches)[0]

    def recipes(self):
        return load_yaml(self.root / 'recipes.yaml')['recipes']

    def sources(self):
        config = load_yaml(self.root / 'sources.yaml')
        sources = dict(config.get('sources') or {})
        data_root = config.get('data_root_dir')
        if data_root:
            data_root = Path(data_root).expanduser()
            data_root = data_root if data_root.is_absolute() else self.repo / data_root
            for path in sorted(data_root.glob('*_cleaned')):
                if (path / 'meta/info.json').is_file():
                    sources.setdefault(path.name, dict(name=path.name, location=str(path.resolve()),
                        task=path.name.removesuffix('_cleaned'), description='从默认数据根目录发现的清洗数据集'))
        extra = self.root / 'sources'
        if extra.exists():
            for p in sorted(extra.glob('*.yaml')):
                if p.stem in sources:
                    raise ValueError(f'重复数据来源 ID: {p.stem}')
                sources[p.stem] = load_yaml(p)
        return sources

    def records(self):
        root = self.root / 'saved'
        return [json.loads(p.read_text()) for p in sorted(root.glob('*/experiment.json'))] if root.exists() else []

    def catalog(self):
        recipes = []
        for key, recipe in self.recipes().items():
            # Catalog defaults do not require task-specific checkpoint/tokenizer env vars.
            cfg = OmegaConf.load(self.root / recipe['config'])
            recipes.append(dict(recipe, id=key, steps=cfg['trainer']['max_train_steps'],
                                batch=cfg['datasets']['vla_data']['per_device_batch_size'], seed=cfg.get('seed'),
                                action_horizon=cfg['framework'].get('action_horizon', cfg['framework'].get('chunk_size')),
                                gpu_count=recipe.get('gpu_count', 2), global_batch_size=recipe.get('global_batch_size')))
        sources = []
        for key, source in self.sources().items():
            try:
                inspected = inspect_source(self.repo, source)
                compatible = []
                for recipe_id, recipe in self.recipes().items():
                    if recipe['format'] != inspected['format']:
                        continue
                    try:
                        self.resolve_data_mix(inspected, recipe)
                        compatible.append(recipe_id)
                    except ValueError:
                        pass
                sources.append(dict(inspected, id=key, error=None, compatible_recipes=compatible))
            except (OSError, KeyError, ValueError) as exc:
                sources.append(dict(source, id=key, error=str(exc), format=None))
        return dict(recipes=recipes, sources=sources, experiments=self.records())

    def compose(self, request):
        eid = check_id(request['id'])
        recipe_id, source_id = request['recipe'], request['data']
        recipe = self.recipes()[recipe_id]
        source = inspect_source(self.repo, self.sources()[source_id])
        if source['format'] != recipe['format']:
            raise ValueError('数据格式与模型配方不兼容，请选择匹配的配方')
        steps = request['steps']
        if type(steps) is not int or not 1 <= steps <= 100000000:
            raise ValueError('训练步数必须是 1 到 100000000 之间的整数')
        recipe_path = self.root / recipe['config']
        cfg = OmegaConf.load(recipe_path)
        previous_steps = cfg.trainer.max_train_steps
        overrides_text = request.get('overrides', '').strip()
        overrides = OmegaConf.create(overrides_text or '{}')
        if not OmegaConf.is_dict(overrides):
            raise ValueError('高级参数必须是 YAML 映射')
        def flatten(obj, prefix=''):
            for key, value in obj.items():
                name = f'{prefix}.{key}' if prefix else key
                if OmegaConf.is_dict(value):
                    yield from flatten(value, name)
                else:
                    yield name, value
        for key, value in flatten(overrides):
            allowed = key == 'seed' or key.startswith('trainer.') or key in (
                'datasets.vla_data.per_device_batch_size', 'datasets.vla_data.num_workers',
                'datasets.vla_data.validation_ratio', 'datasets.vla_data.validation_seed',
                'framework.action_horizon', 'framework.chunk_size')
            if not allowed or key in ('trainer.max_train_steps', 'trainer.is_resume', 'trainer.resume_from_checkpoint'):
                raise ValueError(f'{key} 不允许在高级参数里覆盖')
            if OmegaConf.select(cfg, key, default='__missing__') == '__missing__':
                raise ValueError(f'配方里没有参数 {key}')
            OmegaConf.update(cfg, key, value)
        from backend.checkpoints import canonical_model
        cfg.run_root_dir = str(self.repo / 'results/Checkpoints' / canonical_model(recipe['model']))
        horizon_key = 'framework.chunk_size' if recipe['model'].upper() == 'ACT' else 'framework.action_horizon'
        horizon = request.get('action_horizon', OmegaConf.select(cfg, horizon_key))
        if horizon is not None:
            if type(horizon) is not int or not 1 <= horizon <= 10000:
                raise ValueError('Action chunk 必须是 1 到 10000 之间的整数')
            horizon_overrides = {k: v for k, v in flatten(overrides)
                                 if k in ('framework.action_horizon', 'framework.chunk_size')}
            if any(k != horizon_key for k in horizon_overrides):
                raise ValueError('请使用当前模型对应的 action chunk 参数')
            if 'action_horizon' in request and horizon_key in horizon_overrides and horizon_overrides[horizon_key] != horizon:
                raise ValueError('Action chunk 与高级参数冲突，请只设置一个值')
            OmegaConf.update(cfg, horizon_key, horizon)
            cfg.datasets.vla_data.action_horizon = '${' + horizon_key + '}'
        gpu_count = recipe.get('gpu_count', 2)
        cfg.run_id = eid
        cfg.trainer.max_train_steps = steps
        cfg.trainer.is_resume = False
        # Follow the total schedule when its original decay length equals total steps.
        decay = 'trainer.scheduler_specific_kwargs.decay_steps'
        if OmegaConf.select(cfg, decay) == previous_steps and not any(k == decay for k, _ in flatten(overrides)):
            OmegaConf.update(cfg, decay, steps)
        if cfg.trainer.get('num_warmup_steps', 0) >= steps:
            raise ValueError('训练步数需要大于 warmup 步数；可在高级参数中修改 trainer.num_warmup_steps')
        batch = cfg.datasets.vla_data.per_device_batch_size
        if type(batch) is not int or batch < 1:
            raise ValueError('per_device_batch_size 必须为正整数')
        accumulation = cfg.trainer.get('gradient_accumulation_steps', 1)
        if type(accumulation) is not int or accumulation < 1:
            raise ValueError('gradient_accumulation_steps 必须为正整数')
        global_batch = batch * gpu_count * accumulation
        if recipe.get('global_batch_size') is not None and global_batch != recipe['global_batch_size']:
            raise ValueError(f'此 base 要求 global batch={recipe["global_batch_size"]}；当前每卡 batch × {gpu_count} 卡 × 梯度累积 = {global_batch}')
        path = Path(source['location'])
        cfg.datasets.vla_data.data_root_dir = str(path.parent)
        cfg.datasets.vla_data.data_mix = self.resolve_data_mix(source, recipe)
        cfg.datasets.vla_data.pop('mixture_spec', None)
        text = OmegaConf.to_yaml(cfg, resolve=True)
        warnings = []
        checkpoint = cfg.trainer.get('pretrained_checkpoint')
        if checkpoint and not Path(checkpoint).exists():
            warnings.append('预训练权重路径不存在；训练前需要修正')
        target = self.root / 'saved' / eid / 'config.yaml'
        command = 'cd ' + shlex.quote(str(self.repo)) + ' && ACCELERATE_USE_DEEPSPEED=false .venv/bin/python -m torch.distributed.run --standalone --nnodes=1 --nproc_per_node=' + str(gpu_count) + ' starVLA/training/train_starvla.py --config_yaml ' + shlex.quote(str(target))
        record = dict(id=eid, recipe=recipe_id, data=source_id, steps=steps, overrides=overrides_text,
                      model=recipe['model'], source=source, recipe_name=recipe['name'],
                      action_horizon=horizon, gpu_count=gpu_count, global_batch_size=global_batch,
                      created_at=datetime.now(timezone.utc).isoformat(), config_path=str(target),
                      checkpoint_root=str(Path(cfg.run_root_dir) / eid),
                      config_sha256=hashlib.sha256(text.encode()).hexdigest(),
                      recipe_sha256=hashlib.sha256(recipe_path.read_bytes()).hexdigest(),
                      command=command, warnings=warnings)
        return record, text

    def save(self, request):
        with self.lock:
            record, text = self.compose(request)
            root = self.root / 'saved'
            root.mkdir(exist_ok=True)
            target = root / record['id']
            if target.exists():
                raise ValueError('实验 ID 已存在，请换一个 ID 保存为新实验；旧快照不会覆盖')
            stage = Path(tempfile.mkdtemp(prefix='.saving-', dir=root))
            try:
                (stage / 'config.yaml').write_text(text)
                (stage / 'experiment.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
                os.rename(stage, target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return record

    def add_source(self, request):
        with self.lock:
            sid = check_id(request['id'])
            if sid in self.sources():
                raise ValueError('数据来源 ID 已存在')
            source = dict(name=str(request['name']).strip(), location=str(request['location']).strip(),
                          task=str(request.get('task', '')).strip(), description=str(request.get('description', '')).strip())
            if not source['name']:
                raise ValueError('请填写来源名称')
            inspected = inspect_source(self.repo, source)
            compatible = [r for r in self.recipes().values() if r['format'] == inspected['format']]
            if not any(any(len(entries) == 1 and entries[0][0] == Path(inspected['location']).name and entries[0][2] == r['robot_type'] for entries in self.mixtures().values()) for r in compatible):
                raise ValueError('此数据尚无兼容的已注册 data_mix，请先在仓库原有 data_registry 中登记')
            root = self.root / 'sources'
            root.mkdir(exist_ok=True)
            with (root / (sid + '.yaml')).open('x') as stream:
                stream.write(OmegaConf.to_yaml(OmegaConf.create(source)))
            return dict(id=sid, **source)
