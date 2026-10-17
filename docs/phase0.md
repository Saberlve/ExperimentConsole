# Phase 0：只读调查与复用决策

调查日期：2026-10-16。路径均相对主仓库。检查期间未启动硬件。

## Git 与目录

主仓库已有两个 submodule：`examples/realRobots/xArm7/lerobot_xarm7`、`tools/lerobot_dataset_cleaner`。开始时前者已显示修改（检出提交与主仓库记录不同），保留原状。未找到适用的 AGENTS.md。

## 首帧应用检查（14 项）

| 检查项 | 实际发现 |
| --- | --- |
| 位置 | 启动器 `examples/realRobots/xArm7/lerobot_xarm7/scripts/camera_snapshot_web.py`；实现 `src/lerobot_robot_ufactory/scripts/uf_camera_snapshot_web.py`（相对于该 xArm 子仓库） |
| 前端 | Python 字节串 PAGE 内嵌原生 HTML/CSS/JavaScript，无构建工具 |
| Web 页面 | `/`，默认端口 8766；已有双画面与采集按钮 |
| 相机画面 | 复用 `uf_realsense_view.py` 的 `_CameraFeed`、`_PreviewState`、`_PreviewHandler`，每设备一个读线程，MJPEG `/stream?serial=...` |
| SDK | `discover_devices` 通过 pyrealsense2 扫描；采集使用 LeRobot RealSenseCamera，SDK 由该封装管理；BGR 内存帧 |
| 相机选择 | 首帧页固定显示两个指定序列号，不提供单独拍照选择；通用 realsense viewer 有选择控件 |
| 拍照保存 | `PairCapture.capture()` 同时等待两路新 frame_id，复制原始 frame，cv2 PNG 无损编码；文件锁编号，独占创建；普通失败回滚本组 |
| 时间戳 | 只有内部 frame_id/frame_count，首帧链路未保留/暴露采集时间、设备时间或 monotonic 帧时间 |
| JSON 元数据 | 无；返回 JSON 仅 number、paths，没有帧元数据文件 |
| 数据集 | 首帧页面没有数据集功能 |
| 多相机 | 固定 `148522072685`（A）、`242622070583`（W），缺任一设备启动失败；请求不是硬件曝光同步 |
| 复用接口 | GET `/api/cameras`、GET `/stream?serial=...`、POST `/api/capture`；Python PairCapture 依赖内部 state，HTTP 耦合最小 |
| 后台服务 | 必须单独启动原服务，且不能与其他进程争抢同一相机 |
| 显示组件 | 原应用直接创建 img 标签，不是可导入组件；复用 MJPEG 接口，新建共享 CameraViewer |

默认保存到 `WM_fisrtFrame/`（原目录拼写），可用 `--output-dir` 改到新子仓库输出目录。旧服务没有图片下载 API；HTTP 返回服务器绝对文件路径，因此控制台保存结果需要同机或同路径共享挂载。

## A/B/C/D 方案

A：直接复用原服务设备枚举、RGB 预览、独占相机连接、新帧等待、原分辨率 PNG、双路成组保存、编号和文件锁。

B：CameraAdapter 代理设备与 MJPEG；FirstFrameAdapter 调用原 POST 并在允许的共享 capture_root 内读取原 PNG；SnapshotService 原字节存档并追加 JSON。旧服务依然固定拍摄双路，控制台选择仅控制预览，界面明确提示。

C：新增导航、共享选择器、任务 ID、结果浏览、上传、前端透明叠加、LeRobot facade、元数据旁车、IDM Mock 与记录。旧接口没有的帧时间/内参保留 null，不能追溯补造。相机设备时间与帧时间的真实接入尚未实现。

D：已有页面可在独立地址直接使用，功能不足以承载任务元数据、数据集和叠加；采用 HTTP 接口集成，不嵌入固定双相机页面，不修改旧代码。

## 数据集接口

`examples/.../scripts/compare_act.py` 已使用 `lerobot.datasets.lerobot_dataset.LeRobotDataset`，通过 `meta.episodes` 的 `dataset_from_index` 定位 Episode 起点，取 CHW RGB Tensor。新适配器调用同一个已安装库、复用解析与解码，不复制源码、不改 sys.path。当前支持包含 dataset_from_index 的本地 LeRobot v3 元数据；旧版会明确报兼容性错误。

`starVLA/dataloader/gr00t_lerobot/video.py` 有多后端解码，但模块导入依赖 torch/torchvision/av 等；不直接加载整套训练 Dataset。`tools/lerobot_dataset_cleaner/backend/app.py` 是可写标注/导出 FastAPI 服务，虽有 v3 元数据读取，未提供独立首帧 PNG 接口，直接导入会引入状态和较重依赖。选择 LeRobot 库最少耦合。

## VLA / IDM

`deployment/model_server/README.md` 说明纯策略 WebSocket 与 GR00T ZMQ 服务；输入需要 examples、状态、相机映射、unnorm_key，输出 data.actions。`uf_starvla_eval.py` 会 make_robot/connect/reset/send_action，不能作为安全控制台调用入口。它引用的 `lerobot_robot_ufactory.utils.starvla_ws_client` 在当前文件树中不存在。VLAAdapter 仅实现准备契约，未接入真实推理。

全项目 Python 检索 IDM/inverse dynamics：发现 `scripts/idm/replay_IDM_output.py` 及已有输出文件；该工具读取 7D TCP-RPY-mm / 10D TCP-rot6d-m 动作并实际回放，不是 IDM 预测器，控制台完全不导入/执行。没有发现可确认输入契约和权重的 IDM 模型入口。IDMAdapter 仅有显式 Mock；要求独立上传 current/target 和数值 state。Mock 输出零向量没有动作单位或物理意义，没有伪造 Ground Truth/误差。
