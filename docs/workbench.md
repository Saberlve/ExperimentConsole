# VLArmory 实验工作台

训练配置与 Experiment Console 共用一个网页、一个服务和浅色界面。

```bash
cd /usr/share/EmbodiedAI/VLArmory
./tools/run_experiment_console.sh --port 8089
```

全部页面、后端和测试均位于 ExperimentConsole 子仓库。主仓库脚本只负责传入数据位置和模型启动配置。
首页不打开相机、不加载模型；原有训练命令、相机释放、评测及停止流程保持可用。

页面包括训练配置、检查点、真机评测、WM 首帧、IDM 测试与回放。
训练配置仍只生成 YAML，不启动训练。旧实验与评测记录沿用原位置，无需迁移。
新生成的配置将 `run_root_dir` 指向 `results/Checkpoints/<模型>/`。
保存时的实验 ID 成为其下的训练目录名称。

## 检查点归档

四个目录是 `results/Checkpoints/pi05`（网页显示 π05）、`ACT`、`N0`、`FTP1`。
每个目录中放多次训练的完整输出目录：

```text
results/Checkpoints/
  pi05/
    egg_pick_v1/
      config.yaml
      dataset_statistics.json
      paligemma_tokenizer.model
      checkpoints/
        steps_25000_model.safetensors
        steps_30000_model.safetensors
  ACT/
  N0/
  FTP1/
```

四种模型都必须使用与权重对应的训练时 `config.yaml` 和 `dataset_statistics.json`，
以及其 `checkpoints/` 下的 `.safetensors` 或 `.pt` 权重。名称不要求固定为
`model.safetensors`。不要只复制 `checkpoints/` 子目录或单个权重文件。

| 模型目录 | config.yaml 中 framework.name | 其他内容 |
| --- | --- | --- |
| pi05 | pi05（大小写不敏感） | PaliGemma tokenizer；原配置键 framework.tokenizer.model_path |
| ACT | ACT | 不需要 PaliGemma tokenizer；保留配置引用的本地资源 |
| N0 | N0VTLA | PaliGemma tokenizer；原配置键 framework.tokenizer_path |
| FTP1 | FTP1 | PaliGemma tokenizer；训练时触觉传感器 schema 应保留在配置中，必要的外部资源也须可读 |

建议将 `paligemma_tokenizer.model` 放在每次训练目录中。启动服务时，工作台优先
使用这里的文件，仅通过启动参数覆盖 tokenizer 路径，不改动保存的训练配置。
若原配置中的 tokenizer 路径在此服务器上仍然有效，也可以继续使用它。
同一训练的所有 checkpoint 可以共享这份 config、统计量和 tokenizer。

复制的是 VLArmory 训练输出目录时，保持上述相对层级即可。原始官方/上游模型
下载目录（例如仅有 params/、Hugging Face config.json 或裸 model.safetensors）
不能仅靠放进此目录就变成可用的 VLArmory checkpoint；需要先按对应模型的
转换/导出流程生成匹配的完整权重、训练配置与归一化统计量。不要用其他任务的
统计量填空。

只做推理/真机评测时，优化器状态、训练日志、wandb 文件、原始训练数据不是
这一加载器的必需文件。继续训练则需保留原训练器要求的恢复状态与资源。
配置中的其他绝对路径若来自另一台机器，需要一并检查。

“检查点”页会按模型扫描完整训练目录。文件与元数据齐全的记录可带入真机评测；
它不表示权重张量、环境依赖或真机行为已经验证。外部路径仍可手填，启动前执行
同样的配置、统计量、模型类型及 tokenizer 文件检查。

## 独立运行与验证

独立克隆后安装 requirements.txt，从子仓库运行 `./run.sh --config config/default.json`。
不指定主仓库时，相机、WM、IDM 和评测功能可独立使用；训练页提示尚未配置。
在 VLArmory 中运行时，通过 `--main-repo-root /path/to/VLArmory` 指定训练配方和检查点根目录。
模型进程配置通过 `--control-config /path/to/profiles.json` 显式传入。

从子仓库根目录验证：

```bash
MAIN_REPO_ROOT=/path/to/VLArmory python -m unittest discover -s tests -v
python -m tests.browser_workbench
```

实验配方、数据登记、已保存实验和评测结果仍存放在主仓库原位置。

## 按 base 创建任务实验

ACT / π0.5 配方引用主仓库的 base YAML。训练页可选择数据、训练步数和 Action chunk，
后端同步模型与数据加载器的预测长度。ACT 默认单卡，π0.5 默认两卡；两者 global batch 均为24。
高级参数支持验证比例及 seed；违反 base 的 global batch 约束或相互冲突的 chunk 设置会报错。
保存的快照不受后续 base 修改影响。工作台仍只生成配置和命令，不启动训练。
