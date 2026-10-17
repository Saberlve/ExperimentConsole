# 新增文件与完整目录

全部新增业务、配置、测试和文档均在 `experiment_console/`。本次目录迁移在原首帧目录位置保留兼容软链接；原业务源码未修改。

```text
experiment_console/
├── .git/  # 本地独立 Git 管理信息，未提交、未注册 Submodule
├── adapters/
│   ├── __init__.py
│   ├── camera.py
│   ├── camera_service.py
│   ├── dataset.py
│   ├── models.py
│   ├── video.py
│   ├── replay.py
├── backend/
│   ├── __init__.py
│   ├── server.py
│   ├── snapshot.py
│   ├── replay.py
│   ├── robot_replay.py
├── config/
│   ├── default.json
│   ├── legacy.example.json
├── docs/
│   ├── files.md
│   ├── phase0.md
│   ├── validation.md
├── frontend/
│   ├── app.js
│   ├── comparison.mjs
│   ├── replay.mjs
│   ├── index.html
│   ├── style.css
├── tests/
│   ├── __init__.py
│   ├── browser_check.py
│   ├── browser_alignment.py
│   ├── comparison.test.mjs
│   ├── test_console.py
│   ├── test_camera_service.py
│   ├── test_lerobot.py
│   ├── test_replay.py
│   ├── replay_fixtures.py
│   ├── browser_replay.py
├── .gitignore
├── README.md
├── requirements.txt
├── run.sh
├── run_camera.sh
├── WM_fisrtFrame/（迁入的首帧数据目录，整体忽略）
└── __pycache__/（运行时生成，已忽略，可能分布在 Python 各目录）
```

完整新增文件清单（不含 Git 自动管理文件和忽略的运行缓存）：

- `.gitignore`
- `README.md`
- `adapters/__init__.py`
- `adapters/camera.py`
- `adapters/camera_service.py`
- `adapters/dataset.py`
- `adapters/models.py`
- `adapters/video.py`
- `adapters/replay.py`
- `backend/__init__.py`
- `backend/server.py`
- `backend/snapshot.py`
- `backend/replay.py`
- `backend/robot_replay.py`
- `config/default.json`
- `config/legacy.example.json`
- `docs/files.md`
- `docs/phase0.md`
- `docs/validation.md`
- `frontend/app.js`
- `frontend/comparison.mjs`
- `frontend/replay.mjs`
- `frontend/index.html`
- `frontend/style.css`
- `requirements.txt`
- `run.sh`
- `run_camera.sh`
- `tests/__init__.py`
- `tests/browser_check.py`
- `tests/browser_alignment.py`
- `tests/comparison.test.mjs`
- `tests/test_console.py`
- `tests/test_camera_service.py`
- `tests/test_lerobot.py`
- `tests/test_replay.py`
- `tests/replay_fixtures.py`
- `tests/browser_replay.py`

首帧数据与默认运行输出目录 `WM_fisrtFrame/`、历史输出目录 `output/`、虚拟环境 `.venv/`、本地配置 `config/local.json`、大视频和动作数组已加入 .gitignore。测试输出位于临时目录并自动清理。
