"""Read checkpoint metadata without importing model or robot dependencies."""
import json
import os
from pathlib import Path
from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException
from yaml import YAMLError

MODELS = {'pi05': ('π05', 'pi05'), 'ACT': ('ACT', 'act'),
          'N0': ('N0', 'n0vtla'), 'FTP1': ('FTP1', 'ftp1')}


def canonical_model(value):
    aliases = {'pi05': 'pi05', 'π05': 'pi05', 'act': 'ACT',
               'n0': 'N0', 'n0vtla': 'N0', 'ftp1': 'FTP1'}
    try:
        return aliases[str(value).lower()]
    except KeyError:
        raise ValueError('请选择 π05、ACT、N0 或 FTP1') from None


def inspect_checkpoint(checkpoint, model):
    model = canonical_model(model)
    path = Path(checkpoint).expanduser().resolve()
    run = path.parent.parent
    issues, tokenizer = [], ''
    if not path.is_file() or path.suffix not in ('.pt', '.safetensors'):
        issues.append('权重文件不存在，或不是 .pt / .safetensors')
    if path.parent.name != 'checkpoints':
        issues.append('权重应位于完整训练目录的 checkpoints/ 下')
    config = run / 'config.yaml'
    statistics = run / 'dataset_statistics.json'
    try:
        cfg = OmegaConf.load(config)
        name = str(OmegaConf.select(cfg, 'framework.name', default='')).lower()
        if name != MODELS[model][1]:
            issues.append('config.yaml 中的模型类型与所选模型不一致')
        if model != 'ACT':
            key = 'framework.tokenizer.model_path' if model == 'pi05' else 'framework.tokenizer_path'
            configured = str(OmegaConf.select(cfg, key, default='') or '')
            shared = os.environ.get('PALIGEMMA_TOKENIZER_PATH', '')
            if shared:
                candidate = Path(shared).expanduser()
                if candidate.is_file():
                    tokenizer = str(candidate.resolve())
                else:
                    issues.append('PALIGEMMA_TOKENIZER_PATH 指向的共享 tokenizer 文件不存在：' + shared)
            elif configured and Path(configured).expanduser().is_file():
                tokenizer = str(Path(configured).expanduser().resolve())
            elif (run / 'paligemma_tokenizer.model').is_file():
                # Compatibility with old portable bundles; new runs use the shared env path.
                tokenizer = str((run / 'paligemma_tokenizer.model').resolve())
            else:
                issues.append('请设置 PALIGEMMA_TOKENIZER_PATH 指向共享的 paligemma_tokenizer.model；无需在每个检查点复制')
    except (OSError, ValueError, TypeError, KeyError, OmegaConfBaseException, YAMLError) as exc:
        issues.append('无法读取 config.yaml：' + str(exc))
    try:
        stats = json.loads(statistics.read_text())
        if not isinstance(stats, dict) or not stats:
            issues.append('dataset_statistics.json 必须包含训练时的归一化统计量')
    except (OSError, ValueError) as exc:
        issues.append('无法读取 dataset_statistics.json：' + str(exc))
    return dict(model=model, model_label=MODELS[model][0], run=run.name,
                name=path.name, path=str(path), run_dir=str(run),
                config_path=str(config), statistics_path=str(statistics),
                tokenizer_path=tokenizer, metadata_ready=not issues, issues=issues)


def catalog(repo=None, *, root=None):
    root = Path(root).resolve() if root is not None else Path(repo).resolve() / 'results/Checkpoints'
    models = []
    for model, (label, _) in MODELS.items():
        directory = root / model
        entries = []
        for run in sorted(directory.iterdir()) if directory.is_dir() else []:
            if not run.is_dir() or run.is_symlink():
                continue
            weights = run / 'checkpoints'
            if not weights.is_dir() or not weights.resolve().is_relative_to(root):
                continue
            for path in sorted(weights.iterdir()):
                if path.suffix in ('.safetensors', '.pt') and path.is_file() and path.resolve().is_relative_to(root):
                    entries.append(inspect_checkpoint(path, model))
        models.append(dict(id=model, label=label, directory=str(directory), checkpoints=entries))
    shared = os.environ.get('PALIGEMMA_TOKENIZER_PATH', '')
    return dict(root=str(root), models=models, shared_tokenizer_path=shared,
                shared_tokenizer_available=bool(shared and Path(shared).expanduser().is_file()))
