# 验收记录

验证日期：2026-10-16。主项目现有 Python 3.10 环境；本机 Chrome + Playwright（测试工具临时安装到 /tmp，未修改主项目依赖）。没有启动真实相机、机器人、VLA 或 IDM 模型。

## 自动化结果

最终命令（从 experiment_console 执行）：

```bash
../examples/realRobots/xArm7/lerobot_xarm7/.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=/tmp/experiment-console-browser \
  ../examples/realRobots/xArm7/lerobot_xarm7/.venv/bin/python -m tests.browser_check
node --check frontend/app.js
python3 -m compileall -q backend adapters tests
```

- 后端 **8/8 通过**，最终运行 8.596 秒。
- Chrome 交互回归 **通过**，无 JavaScript 错误。
- JavaScript / Python 语法检查通过。
- LeRobot/datasets 上游出现 NumPy 标量转换弃用警告，不影响结果；未修改上游。

测试初次在沙箱内因禁止 socket 创建而失败；获准后运行回环 HTTP 服务验证通过。没有将最初环境错误当作产品成功。

| 测试 | 验证内容 |
| --- | --- |
| test_pages_preview_snapshot_timestamps | 页面资源、合成相机 PNG、首帧保存、JSON、尺寸、采集/保存时间分离、monotonic |
| test_upload_validation_and_path_boundary | PNG 上传、损坏图片、未知相机、空任务、资源路径越界、跨源写入、404 |
| test_idm_inputs_history_and_vla | 显式 Mock、当前/目标图校验、state、拒绝 real、错误记录、无伪指标、VLA 准备状态 |
| test_shared_camera_and_concurrent_no_overwrite | 6 路并发请求独立路径，复用同一个 CameraAdapter |
| test_dataset_delegation | Dataset 测试替身下 Episode 起点/视角选择和 HTTP 首帧资源；未知 Episode/视角错误 |
| test_legacy_http_reuses_original_pair_png | 旧 HTTP 契约测试服务；一个 capture 请求得到两张字节一致 PNG，时间字段未知为 null，断服务报 503 |
| test_output_cannot_overlap_dataset | 拒绝输出目录和数据集重叠 |
| test_actual_local_dataset_first_frame | **实际已安装 LeRobot** 创建临时两帧本地 v3 图片数据集、保存/关闭后重新读取 Episode 0，返回 32×24，像素 (40,40,40) 与首帧一致 |

Chrome 测试覆盖三页导航、实际 img 解码、首帧保存、图片上传、0/50/75/100% opacity、显示/隐藏、数据集/Episode/视角选择、VLA 准备、IDM 缺输入错误、双观测上传、Mock 结果/历史、390px 移动布局及浏览器错误。浏览器数据集端使用明确的测试夹具；它不等于真实用户数据集验证。实际 LeRobot 库由独立测试验证。

## 实现状态与限制

| 状态 | 功能 |
| --- | --- |
| 已实现且已测试 | 三页统一导航、共享基础设施、合成相机、原图/JSON、上传、前端叠加、错误处理、并发无覆盖、Mock/记录、实际临时 LeRobot 数据集首帧读取 |
| 已实现但未经过真实硬件验证 | 原双 RealSense HTTP 适配、MJPEG 中继、旧服务采集后读取共享路径、真实相机原图存档；仅验证旧 HTTP 契约替身 |
| 已实现但未覆盖全部实际数据格式 | LeRobot 本地 v3 图片数据集通过；用户视频数据集、不同 LeRobot 版本、设备断线恢复仍需实测；v2 元数据不兼容会报错 |
| 仅完成 Adapter 接口/准备功能 | VLAAdapter 只准备；真实 VLA 输入映射、状态与 checkpoint 契约未接通。IDMAdapter 可替换接口 + 显式 Mock，未接真实模型 |
| 尚未实现 | 真实 VLA/IDM 模型推理、IDM 模型特定多帧输入、Ground Truth 误差评估、旧相机链路真实帧/设备/monotonic 时间和内参接入、跨机无共享目录图片下载、正式 Submodule 注册 |

原首帧应用不提供时间信息，不能不修改它就恢复历史采集时间。控制台保留 null 和 limitation，而非伪造。若未来出现可暴露时间/内参的服务，替换适配器即可，不改共享页面。

真实拍照请求依旧是双相机成组，选择器不是单相机快门。若原服务已保存而控制台后续读取/元数据写入失败，原 PNG 仍保留；不回滚或删除原服务文件，页面会显示失败。控制台本身的存储故障可能留下不完整的新组，不会覆盖既有组。

未进行模型性能测试、真机运动测试或硬件同步准确性测试。没有真实 GT，不报告 MAE 等评估指标。

## Git 与变更边界

`git -C experiment_console rev-parse --show-toplevel` 指向 experiment_console 自身。独立 .git、无远端、无提交；未注册为 Submodule。

主仓库最终状态仍只有开始时已有的 xArm 子模块修改及新增 `?? experiment_console/`。主仓库业务代码、.gitmodules、索引与分支历史未修改。未 commit/push/reset/clean。

## 后续目录迁移

用户要求迁移后，实际首帧目录已移动到 experiment_console/WM_fisrtFrame；原路径保留兼容软链接。迁入 54 个原文件（48 张 PNG、2 个 ZIP、README、2 个 CSV、锁文件），未覆盖既有文件。default/legacy 配置的 capture_root/output_dir 均指向新目录；新增 run_camera.sh 显式指定原服务保存位置。原业务源码不变。历史首轮 Git 状态记录不包含这次经用户授权的目录迁移。

## 单终端与 IDM 输入增量验证

- 后端最终 **14/14 通过**（7.302 秒），包含原 8 项、新增 5 项相机服务生命周期测试和 1 项实际视频首帧提取测试。
- 生命周期使用 Mock 子进程，覆盖已有服务复用、不重复启动、配置保存路径传递、仅关闭自己启动的服务、启动失败重试及拒绝本地启动远端服务；没有开启真实相机。
- 视频测试用 OpenCV 生成两帧不同颜色的临时 AVI，经实际 HTTP 视频上传/解码接口验证返回的是第一帧，JSON 元数据存在；损坏视频报错，临时上传文件被清理。
- Chrome 回归通过，新增真实 HTML5 图片拖放、上传生成的 Episode 视频并提取首帧作为叠加参考图的交互。无 JavaScript 错误。
- 两台真实 RealSense 的自动连接、硬件释放与实际 MP4/MOV 等用户文件仍未在真实设备/文件上验证。解码能力以当前 OpenCV 安装为准。
- legacy 配置默认自动启动相机，datasets 默认为空；DEMO 配置仍不连接真实相机。原仓库业务源码未修改，机器人动作执行接口仍不存在。

## 实时对比增量验证

- Python 后端 **15/15 通过**（9.634 秒）：增加 `/api/frame` 合成 PNG、原 MJPEG 首帧解析与字节一致性、缺少采集进度和损坏流错误测试。
- `node tests/comparison.test.mjs`：**4/4 通过**，包括全 RGB 容差、精确 99% 边界、未四舍五入的 98.999999% 不通过、独立新帧计数、冻结失效、上下文切换。
- 原 Chrome 页面回归通过，照片拖放、Episode 视频提取、叠加透明度及 IDM Mock 保持可用。
- 新 Chrome 实时验证回归通过：两个等大画面左右显示；实际浏览器 canvas 对比 HTTP 提供的可控 PNG，100%/99% 通过、98.99% 不通过；0%/100% 透明度不改变评分；冻结超过 2 秒、断流、尺寸不符、更换参考图和相机均取消通过；停止验证清空状态。无 JavaScript 错误。
- 指标固定为全分辨率同位置 RGB 容差匹配率（每通道 ≤10/255），连续 3 个新序号帧均 ≥99% 才通过。参考图无自动配准或重采样，差异图亮色显示超容差像素。
- 新功能仍未经过真实硬件验证；像素匹配不是机器人位姿精度，少于画面 1% 的差异区域可能仍满足阈值。未执行机器人动作或真实模型推理。

## CSV 网页回放增量验证（当前状态）

用户已明确要求从网页执行真实机械臂动作，因此本次新增受控 CSV 回放接口；前文“没有动作执行接口”是此前阶段的历史状态，当前以本节为准。

- **26/26 Python 测试通过**（9.806 秒）；4/4 JavaScript 图像判定测试仍通过。
- 新增 11 项测试覆盖：原字节 CSV 保留与 10 列提取、时间戳频率、真实原脚本 dry-run、真实 worker 参数委托（仍带 dry-run）、只读预检的模拟控制器、错误/暂停保护、非法数值/旋转/夹爪/步进、重复启动、预检期间取消、文件 SHA-256、DEMO 拒绝真实执行、请求令牌/同源/Host、日志、停止失败与重试。
- 实际 `WM_fisrtFrame/egg000055/raw.csv` 经网页同一服务转换链路验证：156 帧、15Hz、10.4s、最大相邻位移 38.1828mm；原脚本 dry-run 成功。输出使用临时目录，源文件未改动。
- Chrome 原功能回归通过。新增 `tests.browser_replay` 通过：HTML5 CSV 拖放 → 实际转换/离线校验 → 摘要/下载 → 勾选明确数据含义 → 点击执行 → 运行日志 → 停止；还验证重复按钮禁用、模拟下发完成、预检失败及坏 CSV。
- Chrome 的控制器与回放进程均为明确 Fake，日志标注 MOCK。没有真实 SDK 连接、实际使能、夹爪动作或机械臂运动。worker 委托验证使用 `--dry-run`，在原程序进入硬件分支前返回。
- **已实现但未经过真实硬件验证**：网页执行、TCP 起始偏差预检、原 SDK guard、网络断开下的软件停止/控制器状态确认、关闭服务时停止。软件停止依赖网络/SDK/进程，不是硬件急停。
- 输出保存在 `WM_fisrtFrame/replays/<job_id>/`，每份 CSV/NPY/摘要使用独立目录，执行日志与状态使用独立 run_id。回放由操作人确认后直接启动，不再做控制器状态/TCP 起始位置预检。
- 正常完成仅显示“指令下发完成（未确认最终到位）”，没有伪造碰撞、运动学或物理到位验证。单任务锁只约束本控制台进程；需关闭其他外部遥操/控制程序。

复现浏览器新增用例（安装可选 Playwright，使用本机 Chrome）：

```bash
python3 -m tests.browser_replay
```
