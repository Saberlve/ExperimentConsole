# WM / VLA / IDM 实验控制台

一个 Web 项目、三个导航页面、同一套相机/保存/数据集服务。默认 DEMO，无相机与机器人依赖。真实模式新增 IDM CSV 手动回放；仅上传/转换不连接机械臂，点击明确的执行按钮后才运行。

## 启动（从主仓库根目录）

系统 Python 已有 Pillow 时：

```bash
cd experiment_console
./run.sh
```

浏览器打开 http://127.0.0.1:8088 。默认使用合成相机，所有保存结果标注 synthetic。没有 Pillow 时可在本目录创建虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
CONSOLE_PYTHON="$PWD/.venv/bin/python" ./run.sh
```

也可复用已检查可用的主项目 Python，从主仓库根目录运行：

```bash
cd experiment_console
CONSOLE_PYTHON="$PWD/../examples/realRobots/xArm7/lerobot_xarm7/.venv/bin/python" ./run.sh
```

默认仅绑定回环地址。本地工具没有用户认证；若需要远程访问请通过已有受控代理/SSH 转发，勿直接作为公网服务使用。

## 首帧目录迁移

原 `examples/realRobots/xArm7/lerobot_xarm7/WM_fisrtFrame/` 已完整移动到本仓库 `WM_fisrtFrame/`，保留原拼写。旧路径是兼容软链接，原启动器的默认输出仍会落到新目录。图像、ZIP、CSV 与原说明全部保留，该数据目录整体加入 .gitignore。

仅在需要手动独立调试相机服务时，从主仓库根目录运行：

```bash
./experiment_console/run_camera.sh --host 127.0.0.1 --port 8766
```

该脚本只包装原启动器，默认输出到新目录；可通过 MAIN_REPO_ROOT 和 CAMERA_PYTHON 指定主项目及相机 Python 环境，CAMERA_OUTPUT_DIR 指定保存路径（自动模式由 capture_root 配置提供）。若旧相机服务正在运行，请重启后使用新配置；控制台亦需重启以重新读取配置。

## 单终端启动真实双相机（未做硬件验证）

从主仓库根目录，只运行下面这组命令，无需另开相机终端：

```bash
cd /usr/share/EmbodiedAI/VLArmory
CONSOLE_PYTHON="$PWD/examples/realRobots/xArm7/lerobot_xarm7/.venv/bin/python" \
  ./experiment_console/run.sh --config config/legacy.example.json --port 8088
```

打开 http://127.0.0.1:8088 。真实模式默认 `camera_autostart: true`，自动启动原双 RealSense 首帧服务，连接 `148522072685`（全景）和 `242622070583`（腕部）。启动期间页面仍可上传参考图；相机连接后自动加载预览。未连接/被占用会显示错误，可插好设备后点击“刷新相机”重试。启动页面不会自动执行机器人动作；只有在 IDM 页面点击真实回放按钮后才连接控制器。

如果相机服务已经存在且保存路径一致，直接复用；已有服务的保存路径不一致会报错，不抢占端口。Ctrl+C 关闭控制台时，只释放本控制台启动的相机子进程；原本已经存在的外部服务不会被关闭。自动管理只支持本机 HTTP 地址，远程服务请自行启动并设置 `camera_autostart: false`。

`config/legacy.example.json` 已去掉虚构的数据集占位项，可直接运行。需要接入已有数据集时，可另建被忽略的 `config/local.json` 添加真实 datasets，并通过 `--config config/local.json` 启动。

原应用调查、14 项检查及 A/B/C/D 决策见 [docs/phase0.md](docs/phase0.md)。仍复用原相机程序，不复制采集实现；同一物理相机不能同时被其他采集程序占用。

`camera_url` 是原 HTTP 服务；`capture_root` 是该服务返回路径的允许根目录，必须同机可读或同路径挂载；`output_dir` 是新结果目录，默认同样为 `WM_fisrtFrame`（包含 snapshots/、references/ 和测试记录）。相对路径始终相对于 experiment_console 根目录。服务不可用返回 503，不悄悄降级为 Mock；切到 DEMO 必须显式改 mode。

旧接口固定拍摄双相机，选择器只控制预览。控制台保留整组 PNG，字节来自旧服务原始图，不来自 JPEG 预览/浏览器截图。原服务输出保存在 WM_fisrtFrame/，控制台另外存档到 WM_fisrtFrame/snapshots/随机组号，并写 JSON；这样不依赖原目录未来编号或移走文件。双设备曝光同步不受保证。

## 数据与时间戳

每张图 JSON 含 camera_id、selected_camera_id、task_id、image_path、width、height、capture_timestamp、save_timestamp、device_timestamp、device_timestamp_domain、monotonic_timestamp_ns、clock_domain、camera_intrinsics。旧 HTTP 服务没有的采集信息显式为 null，并记录限制；save_timestamp 是控制台写入时间，不能当采集时间，也不假设远端时钟同步。DEMO 的采集时间与 monotonic 仅对应合成帧生成。

上传图片保存为 RGB PNG 参考资源，不修改原文件。上传只接受可解码的 PNG/JPG（12 MiB/20M 像素上限）。存储使用随机路径独占创建，任务名不会成为文件路径。输出、图像、视频和本地配置被忽略。原始数据集始终只读，配置加载时拒绝 output_dir/capture_root 与注册数据集目录重叠。

## 页面与共享组件

- WM First Frame：相机选择、RGB 预览、任务 ID、拍照、原始 PNG/JSON 查看、重新拍摄；也可使用共享的数据集/参考图选择与独立预览，无叠加。
- VLA Test：共享相机/拍照，上传和切换参考图，数据集/Episode/视角首帧选择；0～100% 透明度默认 50%，可隐藏参考图；只提供安全测试准备。
- IDM Test：完整共享 VLA 对齐能力；独立上传 current/target，state JSON 校验，显式 Mock，展示输入、零动作占位、耗时、状态、错误和持久测试记录。无真实 IDM、Ground Truth 或误差指标。

`CameraViewer`、`ImageOverlay`、`ReferenceImageSelector`、`DatasetSelector`、`SnapshotButton` 在 frontend/app.js 中共用。参考图位于不透明实时图之上，CSS opacity=alpha，实现 `(1-alpha)*Live+alpha*Reference`；隐藏等效 alpha=0。不同宽高比会提示并按实时画幅拉伸。叠加没有后端接口，从不作为保存或推理输入。切换页面不重连硬件；浏览器只有一个共享实时画面，原服务才是硬件所有者。

## IDM 首帧输入

1. 在 IDM 页面“参考图与数据集”区域选择 PNG/JPG，或直接拖入一张首帧照片；加载后显示参考图并支持透明度叠加。
2. 选择“上传一段 Episode 视频”，支持本机解码器可读的 MP4/MOV/AVI/MKV（上限 256 MiB）。后端流式写临时文件、提取第一张可解码帧、保存 PNG 与来源 JSON，随后清理上传的视频。首帧自动加入参考图选择器，不覆盖已有图。
3. 对于已有 LeRobot 数据集，仍可选择数据集、Episode、相机视角，按 Episode 起始索引提取首帧；这能正确处理多个 Episode 合并在一个视频文件中的情况。独立上传视频则把整个文件视为一段 Episode，提取文件的第一张可解码帧。

提取出的参考图不自动填入 IDM current/target。视频帧编号不是硬件采集时间；元数据 capture_timestamp 保留 null。当前 IDM 推理仍为明确标识的 Mock。

## 实时对比与 99% 验证

进入 IDM Test 或 VLA Test，在“当前相机”选视角，上传/拖入参考图片，或从 Episode 提取。上方两栏等大显示实时相机原始预览和固定参考图；下方保留独立叠加图与差异图（窄屏自动上下排列）。实时原始预览不受透明度影响，叠加图的透明度由左侧滑块控制。

点击“开始实时验证”后，程序在浏览器中对每个新预览帧与参考图逐像素比较：

```text
像素匹配：同位置 R、G、B 各通道差值都 ≤ 10（0～255）
匹配率 = 匹配像素数 / 全图像素数 × 100%
通过：连续 3 个不同帧序号的画面匹配率均 ≥ 99%
```

阈值固定 99%，使用未四舍五入分数判定；显示保留三位小数并向下截断，避免未达标分数被显示为 99%。黑色差异图表示匹配，亮色表示不匹配。不会自动缩放、裁剪、平移或配准图像来抬高分数；相机和参考图必须分辨率一致。评分使用完整像素，透明度只影响叠加显示，不影响评分、原图保存或模型输入。

相机切换、参考图更换、加载失败、尺寸不符、相机错误、停止验证都会立即撤销通过；帧序号不变化超过 2 秒也会失效。WM 页面不执行对齐验证。DEMO 下分数只对应合成画面；真实模式预览是原服务缓存 JPEG，压缩/噪声/曝光会影响结果。

此“99%”是明确容差下的**全画面匹配像素占比**，不是 SSIM、物体姿态误差或物体位置精度。小于画面 1% 的区域发生变化仍可能满足全图阈值；需要准确判断特定物体位置时应另行定义 ROI/几何评估。当前页面会显示像素指标含义，不把它描述为机器人位姿验证。

为可靠检测冻结预览，CameraAdapter 从原服务的 frame_count 读取采集进度，并从已有 MJPEG 流取一帧缓存 JPEG，通过 `/api/frame` 提供给浏览器（约每 300ms 发起下一次请求，实际频率取决于网络与计算耗时）。没有新增硬件采集线程，没有反复调用拍照/保存接口。frame_count 是读取缓存前观察到的序号，不是与 JPEG 绑定的硬件曝光时间；未将它保存为采集时间。图片和序号响应默认不缓存，连续相同序号不会累计通过次数。

## LeRobot 数据集配置

配置 datasets 中键是 UI 名称，值为已存在的本地 root 和真实 repo_id。需要安装主项目已使用的 `lerobot==0.4.3`；推荐复用主项目环境，或在独立环境安装该依赖。适配器通过正常包导入调用，无 sys.path 改动；主仓库位置无需硬编码。

DatasetAdapter 使用 LeRobotDataset 的元数据和解码，按 Episode.dataset_from_index 读取指定 camera_key 的第一帧，不假设所有 Episode 从视频第零秒开始。只支持当前检查的 LeRobot v3 元数据契约（含 dataset_from_index），缺少字段明确报错。需要本地数据及视频齐全；本工具不提供远端数据集下载/写入功能。底层库行为取决于安装版本，缺本地文件时应先补齐。配置变化后重启服务。

## 模型接入边界

VLAAdapter.prepare 已实现，不调用会运动的 uf_starvla_eval；当前没有真实推理按钮。后续可使用策略服务纯推理客户端，先确定 checkpoint、相机映射、状态维度、unnorm_key 和输入 schema，不导入机器人 eval。

IDMAdapter.infer 是可替换接口，当前只允许 payload.mode=mock。current_image/target_image 是本控制台资源 URL，state 是有限数值数组。实际模型接入必须替换该契约：若模型需要多帧序列、不同机器人状态或其他条件，应同时增加 UI/验证，不把对齐参考首帧擅自当作目标观测。Mock 零向量无物理语义，不能回放。模型 Mock 没有动作语义；实际动作回放使用独立的 CSV 回放接口，不能将 Mock 输出传入回放。

## CSV 拖放与网页执行机械臂回放

按前面的单终端命令使用 `config/legacy.example.json` 启动，进入 **IDM Test → IDM 动作 CSV · 转换与机械臂回放**：

1. 将 `raw.csv`、`ema05.csv` 或 `gt.csv` 拖入动作 CSV 区域，也可点击选择。照片和视频仍放在各自的参考图区域。
2. 自动按表头提取 `tcp_x_m,tcp_y_m,tcp_z_m,r11,r21,r31,r12,r22,r32,feedback_closure`，根据 `timestamp_s` 推断固定频率。原行号/时间列不作为动作，原 CSV 会原字节保存。
3. 查看帧数、Hz、时长、首尾 XYZ、范围、最大相邻位移和离线检查日志；可下载兼容的 NPY。上传不会触发机械臂运动。
4. 确认数据是基坐标系绝对 TCP、位置 m、夹爪闭合比例 0 全开/1 全闭后，勾选对应说明，再点击 **执行真实机械臂动作**。该点击会操作夹爪、移动到第一个完整位姿并开始回放。
5. 查看实时日志；需要中断时点击 **停止回放**。软件停止依赖进程、SDK 和网络，无法代替实体停止装置。断网/停止未确认会明确报错，不显示已停止。

只接受带上述命名列和至少两帧的 UTF-8 CSV（支持 BOM，最大 12 MiB）。不规则/倒序时间、NaN/Inf、无效 6D 旋转、超范围夹爪都会拒绝；不自动猜测增量动作、反归一化、修改频率或平滑轨迹。原脚本的高度/步进数值告警在网页入口会阻止执行；当前限制包括 z 不低于 -5mm、相邻位移不超过 40mm、TCP 半径默认不超过 1000mm。这些数值限制不等于环境碰撞或运动学验证。

点击执行后立即直接运行回放脚本，不做控制器状态、故障码或 TCP 起始位置/姿态检查。先操作夹爪、50mm/s 接近首点、mode 7 连续下发等运动逻辑来自回放脚本本身。频率来自 CSV，不因相机匹配分数而变化；99% 验证不会自动启动动作，也不会因执行过程中场景变化自动停止。

真实回放配置：

```json
"replay": {
  "enabled": true,
  "robot_ip": "192.168.1.245",
  "max_tcp_radius_mm": 1000
}
```

`legacy.example.json` 已启用上述入口，DEMO 配置始终禁用真实执行。控制器 IP 从服务端配置读取，页面只显示。SDK 和回放 Python 推荐使用现有 xArm 子项目 `.venv`；可配置 `replay.main_repo_root`（主仓库绝对路径）、`replay.python` 或环境变量 `MAIN_REPO_ROOT` / `REPLAY_PYTHON`。Web 服务所在 Python 也需安装 xArm SDK 供停止接口使用，因此推荐前述 CONSOLE_PYTHON 命令。

同一服务一次只允许一个回放，停止期间不能重复启动。刷新网页会继续显示当前运行状态，不会自动再执行；重新启动服务后需重新上传 CSV。正常关闭控制台会请求停止本服务活动中的回放。请关闭其他遥操/控制进程：控制台的任务锁不能约束独立的外部程序。

原 CSV、NPY、摘要、完整日志和运行状态保存在 `WM_fisrtFrame/replays/<任务ID>/`。动作文件 SHA-256 校验不符时拒绝执行。下发完成显示“指令下发完成（未确认最终到位）”，不伪造真实机械臂位置验证。

回放写入接口需要当前进程令牌及同源请求，默认只允许 localhost/127.0.0.1/::1 Host。该控制台未提供账户认证；远端使用优先 SSH 端口转发到本机地址。需要变更允许 Host 时由服务端 `replay.allowed_hosts` 配置，勿直接暴露公网。

## API

| 方法 / 路径 | 用途 |
| --- | --- |
| GET /api/status、/api/cameras | 模式与相机列表 |
| GET /api/preview?camera=ID | DEMO PNG / 旧服务 MJPEG（兼容入口） |
| GET /api/frame?camera=ID | 单张原服务缓存预览帧，X-Frame-Id 为采集进度序号 |
| POST /api/snapshot | JSON camera_id/task_id，返回原图及元数据 |
| POST /api/upload | 请求体是 PNG/JPG 原字节，返回资源 URL |
| POST /api/video-first-frame | 视频原字节（≤256 MiB），返回首帧资源与来源 |
| GET /api/datasets、/api/dataset?key=NAME | 配置列表、Episode/视角 |
| GET /api/reference?key=NAME&episode=N&view=KEY | 数据集首帧资源 |
| POST /api/vla/prepare | 仅准备和接口状态 |
| POST /api/idm | 显式 Mock，输入独立 current_image/target_image/state |
| GET /api/records | 最近 100 条 IDM 成功/错误记录 |
| POST /api/replay/upload | CSV 原字节，自动转换及原脚本离线校验 |
| POST /api/replay/start | 已转换 job_id、confirmed=true，明确启动真实动作 |
| POST /api/replay/stop | 请求停止当前回放 |
| GET /api/replay/status | 当前回放状态、预检结果与最近日志 |

错误为 JSON error/type。记录默认保存在 WM_fisrtFrame/idm_records.jsonl，可随时从记录接口查看；没有虚构评估分数。

## 测试

从主仓库根目录：

```bash
cd experiment_console
../examples/realRobots/xArm7/lerobot_xarm7/.venv/bin/python -m unittest discover -s tests -v
node --check frontend/app.js
node tests/comparison.test.mjs
```

详细结果及边界见 [docs/validation.md](docs/validation.md)。测试服务只绑定临时回环端口。没有启动物理相机、机器人、VLA/IDM 模型。

浏览器回归使用可选 Playwright 和本机 Chrome：

```bash
python3 -m pip install playwright
python3 -m tests.browser_check
python3 -m tests.browser_alignment
python3 -m tests.browser_replay
```

该脚本自建临时服务/输出，无需常驻应用；不会下载浏览器。可用 CHROME_BIN 指向已有 Chrome。

## Git 独立管理与未来 Submodule

本目录已有自己的 .git，是**本地嵌套独立仓库，尚未正式注册 Submodule**。没有远端、提交、push、gitlink；主仓库 .gitmodules 和索引没有更改。原有 xArm 子模块修改保持原状。

未来有真实远端后，需要用户先审阅并提交本目录，再设置真实 origin/推送；随后在主仓库登记 submodule path/url、160000 gitlink，并使用 `git submodule absorbgitdirs experiment_console` 迁移嵌套 .git 到主仓库管理位置。具体步骤应根据当时索引和远端状态选择，不能在无首个提交/真实 URL 时伪造完成。主仓库添加时必须按 gitlink 管理，避免把文件逐个加入主仓库。本任务未执行这些提交或索引操作。

完整目录及文件清单见 [docs/files.md](docs/files.md)。

## 多任务、多模型的布局评测

`VLA Test` 新增任务布局测试集和人工结果记录。评测仍使用原终端命令，网页不启动策略、模型或机器人动作。

1. 为每个任务填写任务名称、测试集名称/版本和成功标准，批量上传提前拍好的 10 张布局图，或开启相机逐张拍摄加入草稿。上传默认按文件名排序，可调整顺序。保存后图片、顺序、成功标准冻结；更改布局应创建新测试集。
2. 选择任务和测试集，填写模型、具体 checkpoint、轮次、可选评测配置路径。不同模型复用同一个测试集，但各有独立记录。同一模型换 checkpoint 或重测也新建一轮。
3. 点击“开启布局相机”，按当前参考图恢复物体摆放。主项目入口默认不自动打开设备，首次由用户显式开启。
4. 点击“已摆好：释放相机并标记开始”。浏览器停止预览，服务关闭其拥有的相机进程并确认退出，随后才记录开始。此时在原终端启动本样本评测。不能释放相机时不会开始记录；外部相机进程不会被本控制台强杀，应在其所属终端关闭。
5. 评测结束后标记成功、失败或中止，可填写备注或日志路径；点击下一张后重新开启布局相机。先结束终端评测，让它释放相机。控制台无法判断独立终端的机器人进程是否仍在运行。
6. 刷新或重启后选择任务、轮次并点击“加载 / 刷新评测记录”继续。JSON/CSV 可导出；模型比较按任务展示，同时保留测试集 ID、checkpoint 和轮次，不混算不同布局版本。

成功率 = 成功 /（成功 + 失败）。待测、进行中和中止均单独统计；没有判定样本时显示“—”。只测了一部分会明确显示进度，并不作为完整测试集结果。中止不自动算失败，重测应新建轮次。结果不可覆盖，重复请求和过期页面请求会被拒绝。

默认保存到 `output_dir/vla_evaluations/`，包含不可变布局图片及 SHA-256、测试集记录、每轮样本结果与时间。页面上的“开始”是人工记录时间，不是终端进程真实启动时间。`mode=demo` 的记录明确标识 DEMO。

推荐真机配置 `config/vla_evaluation.json`：相机按按钮开启/释放，CSV 机器人回放禁用。现有相机实现默认全景 serial `148522072685`、腕部 serial `242622070583`，实际设备应核对 `backend/camera_server.py` 的 `CAMERAS`。`config/reference_only.json` 用于只看图片、不使用相机的场景。

在独立仓库运行：

```bash
./run.sh --config config/vla_evaluation.json --port 8088
```

新增 API：GET `/api/evaluation/catalog`、`/run?id=...`、`/export?id=...`、`/camera`；POST `/suites`、`/runs`、`/transition`、`/camera/enable`、`/camera/release`（均位于 `/api/evaluation` 下）。写请求需要当前服务的 `X-Replay-Token`，与已有回放接口使用相同同源保护。每次状态变更携带 `run_id` 和 `revision`，防止多页面覆盖。

硬件无关验证：`python -m unittest tests.test_evaluation tests.test_camera_service tests.test_console -v`。真实相机释放/重连仍需在机器人现场验证。
