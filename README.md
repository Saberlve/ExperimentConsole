# 统一实验工作台

浅色工作台包含训练配置、检查点、真机评测、WM 首帧和 IDM 测试与回放。
全部网页和后端实现均在本仓库。使用方式、训练集成及检查点文件要求见 [工作台说明](docs/workbench.md)。

```bash
./run.sh --config config/default.json --port 8088
```

连接 VLArmory 时，由主仓库 `tools/run_experiment_console.sh` 传入 `--main-repo-root`
和模型进程配置。训练数据及结果保持在主仓库，程序本身无须位于它的 tools/ 下。

---

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

如果相机服务已经存在且保存路径一致，直接复用；已有服务的保存路径不一致会报错，不抢占端口。Ctrl+C 或 SIGTERM 关闭控制台时，会释放本控制台启动的相机子进程。自动启动使用本安装目录中的私有 `.camera-owner-token` 保存归属，控制台重启后仍能接管并释放遗留服务（即使尚未开启预览或相机连接异常）。手动启动且没有该凭据的外部服务不会被关闭。自动管理只支持本机 HTTP 地址，远程服务请自行启动并设置 `camera_autostart: false`。

`config/legacy.example.json` 已去掉虚构的数据集占位项，可直接运行。需要接入已有数据集时，可另建被忽略的 `config/local.json` 添加真实 datasets，并通过 `--config config/local.json` 启动。

原应用调查、14 项检查及 A/B/C/D 决策见 [docs/phase0.md](docs/phase0.md)。仍复用原相机程序，不复制采集实现；同一物理相机不能同时被其他采集程序占用。

从 IDM/WM 切到尚未选择评测轮次的真机评测页，会停止预览并释放后台相机服务。若从终端运行 VLA，也可在任意相机页面点击“释放相机供 VLA 使用”，等页面提示释放成功后再启动评测。需要重新预览时点击“开启 / 刷新相机”。如果提示仍有外部相机服务，请在启动该服务的终端退出它；控制台只关闭自己启动的相机进程。

真机评测中点击“开启 / 刷新相机”或左栏的“补拍 / 编辑选中的 suite”，管理工作区会平滑铺满页面并隐藏 VLA 评测区；点击“返回 VLA 评测”恢复原评测页面。左栏通过独立下拉框选择管理目标，右侧下拉框只负责选择评测 suite，双方选择互不联动；补拍、保存与删除其他 suite 不改变右侧选择。展开区上方的“训练集首帧 · 摆放参考”支持直接输入服务器上的 LeRobot v3 路径，点击“生成首帧叠加图”后，按每个 episode 的视频时间偏移读取同一相机视角的首帧，并等权合成为一张静态 PNG，帮助避开训练时的摆放位置。默认优先选择全景视角；读取后可切换视角再生成，点击结果图片可放大。进度和错误在区内显示；相机刷新、拍照或释放不会更新这张图。读取仅使用 PyArrow 和 PyAV，不实例化 LeRobotDataset，不下载、转换或修改原数据集；衍生 PNG 和来源 JSON 单独保存到控制台输出目录。输出/采集目录与原数据集重叠时拒绝处理，任一 episode 无法读取时不生成不完整结果。

IDM 页顶部提供“断开相机连接”，旁边显示断开结果；它关闭后台采集并释放设备。继续已有评测后，实时相机、参考图、透明度和叠加图显示在第 4 步内部。加载轮次开启预览前会等待正在进行的相机释放完成。

评测启动按钮下方常驻“本条评测 · 状态与日志”，显示启动请求等待时间、校验失败原因、客户端输出、日志文件路径和退出码，每 1.5 秒刷新。刷新会恢复当前轮次、当前样本、手动记录面板及尚未提交的备注和结果选择；不会重新启动机器人。

模型进程运行不代表已就绪。只有当前进程日志出现 `listening on`，页面才启用“启动本条评测”，后端也会检查这一条件。加载期间显示“等待 listening on”。模型端口连接和相机释放检查的超时会分别标明地址、失败步骤及客户端尚未启动。

启动评测前通过模型端口的 HTTP `/health` 检查服务，要求返回 `200` 和 `ok`，避免仅连接 TCP 后断开造成 WebSocket `opening handshake failed`。健康检查不进入策略连接处理，不会重置策略或执行推理。此更新需要控制台与 VLArmory 模型服务一起重新启动后生效；运行中的会话继续使用启动时加载的代码。

本机相机 HTTP 请求（localhost / 127.0.0.1 / ::1）始终直连，不使用环境中的 HTTP 代理，避免设备已退出时端口探测仍被代理拖至超时。

网页启动的客户端异常退出时，后端确认停止，保留该次退出码和日志，将当前布局恢复为待测并重新开启布局相机。页面自动同步进度，可重新对齐后重试同一张，也可选择成功、失败或中止并点击“结束本条”保存结果、复位进入下一张；不会自动跳过或重跑。错误恢复后刷新页面仍保留结果选择和备注。正常退出仍等待人工判定结果。停止未确认时保持启动锁定，允许保存人工判定，但不会自动复位或切换下一张。

加载轮次后，可展开“查看 / 修改历史结果”，选择已记录样本，更正成功、失败、中止及备注。保存后更新成功率、对比表和 JSON/CSV；不改变当前评测位置，也不触发停止、复位或推理，进行中的其他样本保持原状态。

“结束本条”按停止客户端、保存结果、断开布局相机、复位、切换下一张并重新连接相机的顺序执行。相机断开失败时保留本条结果，不复位或切换，支持重试；断开期间收到急停也不会继续启动复位。

“急停 / 停止评测”保留模型服务和当前评测页面。软件停止确认完成后，主动点击“结束本条”或“重试复位”可以解除本次软件锁定并复位；停止未确认仍需人工检查解锁。复位流程中再次急停会中断复位。“退出并保存评测结果”保存结果后关闭模型服务，下一轮需启动对应模型；正常结束本条并下一张保留模型服务。

已有轮次允许修改评测 YAML 后直接重试，无需重启模型服务。每次执行步数和机器人参数在下次启动客户端时生效；任务描述 `single_task` 和控制频率 `fps` 必须与训练配置一致；`action_chunk_size` 仍需匹配模型输出长度，或设为 `null` 自动读取。网页编辑内容会在启动时覆盖当前轮次的同一份配置，直接修改文件也会被采用。启动直接使用该文件，只保存最新配置，不再为每次启动保留配置副本。

当前条显示“正在评测”时，编辑 YAML 后启动按钮变为“应用修改并重启本条评测”。点击后先停止客户端并确认控制器停止，再保存配置、启动同一条评测；模型服务保持运行，原客户端日志及修改前配置保留在该条的 `restarted_attempts` 中。急停锁定、正在复位或另一条评测运行时禁止重启。

继续已有轮次后，“编辑当前轮次配置”显示在“继续已有评测”区域，默认收起。展开可编辑该轮次的现有 YAML，启动或重试本条评测时原地保存。

在布局测试集下拉框选择已有 suite，可点击“删除此 suite”。确认窗口显示任务、名称和布局数量，点击“确认删除”后才会删除；取消不作修改。已有评测轮次、结果和使用的布局图片保留，仍可继续历史评测；没有评测轮次引用的 suite 会连同其布局文件删除。

PI05 / ACT 的 EEF10 评测由客户端完成策略检查、设备连接和一次复位，然后开始推理；启动器不再额外启动复位进程。checkpoint 在模型服务启动时检查；同一模型服务会话内，每个轮次仅在首次启动评测时执行完整离线配置检查，后续条目、重试和复位不再重复启动离线检查子进程。修改执行参数后，客户端仍读取并解析最新 YAML；切换轮次或重新启动模型服务后重新检查，检查失败不会缓存为通过。RealSense 连接和预热并行执行，与其他相机的串行初始化重叠；保持现有预热时长和图像输入顺序。客户端日志保留已有的阶段及耗时，不新增分段计时。

通过 VLArmory 启动器运行四种模型的 EEF10 评测时，首次启动本条评测才创建私有设备会话并连接机械臂和触觉。条目结束、正常推理完成以及条目间复位都复用机械臂、夹爪反馈和触觉连接；停止动作后仅释放 RGB 给布局预览，清空旧 RGB 缓存。下条评测重新连接 RGB，并重新创建策略客户端、动作队列和 TE 状态；推理及图像处理留在设备进程内，不经本地 socket 转发图像。执行步数、频率、TE 等执行参数仍从最新 YAML 读取；机器人连接参数、相机配置或 RGB 映射变化时拒绝复用，需先退出评测会话。

结束本条会等待设备进程确认停止并释放 RGB，再确认控制器停止、保存结果和复位。条目包装进程异常退出会撤销该条动作权限；急停会终止设备进程，再确认控制器停止。退出并保存、关闭模型服务、最后一个浏览器页面退出或控制台/模型进程退出时释放机械臂和触觉。SDK 原生输出保存在 `processes/devices-*.log`，评测阶段和推理日志仍写入每条评测日志。终端直接运行和 joint 评测沿用原连接流程。修改代码不会热替换已运行控制台的模块；既有会话不会自动重启，新行为在下次正常启动控制台后启用。

模型由网页启动且仍在运行时，打开页面会自动接回对应评测会话。每个页面独立发送心跳；最后一个页面关闭 15 秒后，后端保存 JSON/CSV，停止模型服务并清理活动会话。刷新或另一个仍打开的页面会保持会话。若评测/复位仍在运行，后端先发送停止、把未完成样本记为中止，再停止模型；不会自动复位。浏览器异常退出而未发出关闭通知时，心跳失联 120 秒后清理。已保存的 suite 和历史结果保留；手动记录模式不管理外部模型进程。

真机评测页按五步操作：① 在“布局测试集管理”中选择并加载 suite，补拍、编辑与删除操作位于左侧“拍摄 / 管理 suite”，通过左侧独立下拉框选择目标，与右侧评测 suite 的选择互不影响；② 选择模型、检查点和评测 YAML，保存新轮次或加载已有轮次；③ 启动模型服务；④ 开启相机、按参考图布置场景，再启动本条评测（自动释放相机）；⑤ 选择本条结果，结束本条并复位进入下一张。页面顶部提示当前下一步。汇总表每个轮次的“删除”按钮可移除评测记录，确认弹窗会展示轮次及模型；正在评测或关联活动进程的轮次须先结束。删除记录保留布局测试集与其他轮次，刷新后不会恢复已删除轮次。加载轮次后配置区自动收起，可展开查看；YAML 编辑器和终端手动记录默认折叠。终端评测使用“在终端运行 VLA / 仅手动记录结果”中的操作。

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

需要保留回放视频时，先开启相机并等待全景、腕部两台相机连接，再打开 **回放时同时录制两个视角（全景 + 腕部）** 开关。开关默认关闭，浏览器会记住选择。点击执行后先验证双相机并准备视频文件，准备失败会阻止本次动作启动。机械臂先初始化夹爪并移动到首点，此时不录制；到达起点后，控制台采集最新双视角画面并启动录制，确认就绪后才开始下发 CSV 动作。录制不包含初始化夹爪和回起点过程，正常结束、停止或执行失败时均自动收尾；回起点期间停止或失败则取消录制。服务端录制不依赖当前浏览器页面是否打开。

固定保存到控制台输出目录下的 `idm_recordings/<本次回放ID>/`，通过 `tools/run_experiment_console.sh` 启动时为仓库的 `results/real_robot_eval/idm_recordings/`。每次执行使用新目录，包含 `view_1.mp4`、`view_2.mp4` 和 `recording.json`；元数据记录相机序列号、标签、帧数、补帧数和错误。页面显示本次完整路径及两个视频的下载入口。视频使用 15 FPS、MP4V 编码，录制相机缓存的原始预览画面（不含参考图叠加），两路采用共同主机时间轴，采集或传输变慢时重复上一帧维持时长；不代表两台相机硬件同步。录制中发生相机错误时显示录制失败并保留已写入的视频，机械臂回放继续按原流程执行。录制及视频收尾期间，不能断开后台相机或启动下一次回放。

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

## 仓库组织

本仓库独立管理网页、后端和测试；VLArmory 通过 Git 子模块引用，并由主仓库启动脚本传入本地路径和模型配置。

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

## IDM 视频拖入与录制

在 `#idm` 页面，将 MP4 / MOV / AVI / MKV 视频拖入独立的视频窗口，或点击选择视频，即可自动提取首帧并加载为对齐参考图。视频最大 256 MiB。

在“CSV 执行 · 自动录制”中只需填写录制类名，再按原流程上传并执行 CSV；执行时自动开始录制，执行完成、失败或点击“停止回放”后自动保存 MP4，无需手动开始或保存录像。录制类名会在浏览器中记住。录制使用当前选择的相机，未开启预览时自动连接相机；未选择相机时优先使用全景相机。录像就绪后才启动回放程序，包含初始化和移动至首点的过程。

每次启动控制台服务创建一个 `output_dir/recordings/启动时间_随机标识/` 目录，本次服务录制的视频均保存在此目录；同名视频按 `类名.mp4`、`类名_1.mp4`、`类名_2.mp4` 递增，不覆盖已有文件。页面显示实际保存目录及下载链接。

录制由后台执行，刷新页面不中断录像，仍可点击“停止回放”结束回放并自动保存；断开相机、正常退出服务（Ctrl+C / SIGTERM）也会结束并保存当前录像。录像使用当前相机的原始预览画面，以 15 FPS 编码，不包含参考图叠加。强制杀死进程或断电时无法保证 MP4 完整。

POST `/api/replay/start` 携带 `recording_name` 与可选 `camera_id`，后台管理录制的开始与保存；GET `/api/recording/status` 查看状态及本次服务的视频。录制底层接口 POST `/api/recording/start`、`/api/recording/stop` 继续保留，页面不再显示手动录制按钮。写请求使用 `X-Replay-Token`。

验证：`python -m unittest tests.test_recording tests.test_console -v`；`python -m tests.browser_recording`（需要 Playwright 和 Chrome）。

## Optional model process controls

Pass `--control-config /absolute/path/profiles.json` to enable server-owned argv profiles. The parent repository supplies model-specific commands; the console remains independent. HTTP requests select a configured model and existing checkpoint/YAML paths, never shell commands. Starts and stops use the existing local host/origin/token checks. Logs and the persistent software-stop latch live under the ignored output directory.

The UI supports starting/stopping the owned policy server and starting a previously marked layout trial. Stop kills the owned evaluation process group and invokes the configured controller-stop command; a failed or timed-out controller check is shown as unconfirmed. This is a software stop, not a replacement for the physical emergency switch. Explicit unlock only clears the launch latch and does not move the arm. Record the result after stopping, then reopen the layout camera. No automatic retry or restart.

### VLA 评测双视角录像（默认关闭）

在“启动本条评测”上方勾选“录制本条评测的双视角视频（15 FPS）”。
网页开关仅控制当前这一条，在启动请求中传入 `record_video`，不会修改评测 YAML。
切换到下一条时恢复配置默认值（缺省关闭）；同一轮次可以第一条录制、第二条不录制。
运行中不能切换，开始评测后会显示本条实际是否开启录像。

也可在评测 YAML 设置每条的默认值，网页开关可以覆盖：

```yaml
record_video: false  # 缺省 false；仅作为网页每条开关的初始值
```

控制台配置 `vla_record_video` 也可设置默认值（发布配置为 `false`），评测 YAML
中的显式值优先。终端单独运行评测时，可用 `video_dir` 指定一个尚不存在的
输出目录；缺省在 `videos/eval-<时间戳>/` 保存。

录像复用评测机器人已有的 RGB 采集缓存，按 `camera_keys` 的顺序保存第三人称和
腕部视角，不重复连接相机，不经过 HTTP/JPEG 预览或策略 observation 循环。
后台线程读取不可变帧引用，分别送给两个低优先级、单线程 FFmpeg H.264 编码进程。
内存不积压帧；编码跟不上时跳过源帧，并重复上一帧来保持两路共同的 15 FPS 时间轴。
这不是硬件曝光同步；CPU、内存带宽及写盘仍有开销，真机性能需实测。

每次启动使用独立目录，不覆盖重试视频：

```text
output/vla_evaluations/runs/<run_id>/videos/trial_<序号>_<时间戳>/
  view_1.mp4
  view_2.mp4
  recording.json
  view_1.ffmpeg.log
  view_2.ffmpeg.log
```

页面显示当前录像状态及两路视频链接；评测记录保存录像目录和历次尝试。
结束本条、退出或取消评测时，保留设备会话会释放录像资源并完成保存。
MP4 使用分片封装，进程被强制终止时已完成的片段仍可恢复，但最后不足一个 GOP
的画面可能丢失。录像依赖评测机 PATH 中含 libx264 的 `ffmpeg`；录像失败会记录
错误并继续评测，编码日志保存在同一目录。
