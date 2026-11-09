# RadioLab V0.3 软件预览

RadioLab 将不超过 32 字节的 UTF-8 文本封装为自定义帧，转换成 AFSK 音频，并可从录制的 WAV 恢复且验证 CRC。发给 AIOC 的是 **PCM 音频**，FM 调制由手台完成。自写 SSTV 六模式已与独立 Rust 项目双向离线互验，其中五模式还与 Open-SSTV 双向互验；Robot 模式颜色缩放有差异。AIOC 实机、双手台射频链路及 MMSSTV/Android 等接收端尚未验证。

## 下载与安装

从 [GitHub 下载源码 ZIP](https://github.com/ANTACIDisnotFORMICACID/AIOC-RadioLab/archive/refs/heads/master.zip)，解压后在项目根目录打开 Windows PowerShell。也可以通过 [Releases 页面](https://github.com/ANTACIDisnotFORMICACID/AIOC-RadioLab/releases)下载带版本号的源码归档。当前源码预览需要 Python 3.11 或更新版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install '.[gui,devices,sstv]'
.\.venv\Scripts\radiolab.exe text decode examples\hello_afsk.wav
.\.venv\Scripts\radiolab-gui.exe
```

若只需命令行离线编解码，可安装 `python -m pip install .`；音频设备、串口和图形界面由上面的可选依赖提供。首次启动前可先阅读 [GUI 快速操作](docs/gui_quickstart.md) 和 [发布门槛](docs/release_gate.md)。本机保留的旧 Windows 目录包对应早期代码，不能当作当前版本的安装包。

## 项目目录

| 路径 | 用途 |
|---|---|
| `src/radiolab/` | AFSK、SSTV、音频、PTT、历史及 CLI/GUI 的当前源码 |
| `tests/` | 自动化回归测试；`examples/` 存放公开的离线测试素材 |
| `packaging/`、`scripts/` | Windows 启动入口、打包和许可审计、SSTV 离线互验脚本 |
| `docs/` | 协议、操作、互验和发布门槛的详细记录 |
| `runtime/` | 本机生成文件与实验数据，已被 Git 忽略；其中 `sstv_comparison_*` 是文档引用的互验证据，`qt-source-6.11.3/` 和 `graphics-source-candidates/` 是打包用源码候选缓存 |
| `dist/windows-20261030-021535/` | 唯一保留的旧 Windows 预览包，早于当前 SSTV/GUI 重构；验证当前源码须重新打包 |
| `2026_10_29_ISS_ASFK/` | ISS AFSK 录音与分析记录，不属于应用运行依赖 |
| `.venv/` | 本机开发环境，可重建，已被 Git 忽略 |

`runtime/` 中的个人录音、历史记录和互验原始文件应按各自用途检查后清理；删除历史引用的媒体会在界面中显示为“文件缺失”。Python 缓存、`src/radiolab.egg-info/` 与临时编码输出可由安装或运行再次生成。

## 安装与离线演示

Windows PowerShell，Python 3.11+：

```powershell
python -m pip install -e '.[devices,sstv,test]'
radiolab --help
radiolab devices
radiolab text encode 'HELLO WORLD' -o runtime/hello.wav
radiolab text decode runtime/hello.wav
radiolab text decode examples/hello_afsk.wav
radiolab history list
python -m pytest -q
```

`text encode` 和不带 `--on-air` 的 `text send` 只生成文件，不打开串口或发射。WAV 为 48 kHz、单声道、16-bit PCM，固定 AFSK 峰值约 0.25（约 -12 dBFS）；实际发射还会乘配置中的 `gain`，默认 0.1。输出幅度不逐段归一化。`radiolab devices` 的设备编号会随插拔变化，运行发送前重新核对。

公开基准 [hello_afsk.wav](examples/hello_afsk.wav) 是纯算法生成的 `HELLO WORLD` 帧，可用来先确认安装和解码器；它不是实录或射频测试证据。

## 录音与受控发送

从 `config.example.toml` 复制配置，分别填写 TX 输出、RX 输入和串口。接收手台必须接独立录音输入，或用电脑麦克风录其扬声器；单个 AIOC 不提供双向 ACK。录音无需发送：

```powershell
radiolab receive record --seconds 45 -o runtime/rx.wav --config config.toml
radiolab text decode runtime/rx.wav
radiolab check --config config.toml
radiolab text send 'HELLO WORLD' --dry-run --config config.toml
```

`radiolab check` 会分别检查 TX 输出、RX 输入是否支持 48 kHz，以及 PTT 串口是否仍在设备列表；缺失时返回非零状态。它不打开串口或播放音频，也不证明台架和频率安全。

录音完成后显示峰值和 RMS（dBFS）；低电平、接近削波只作为音频提示，是否收到了有效文本仍以解码及 CRC 为准。若输入设备仅接受多声道采集，程序选用其支持的最少通道数并平均为单声道 WAV。接收解码可读取 8–192 kHz 单/双声道 PCM/浮点 WAV，并转换成 48 kHz 单声道。

要回放接收 WAV，先从 `radiolab devices` 确认**本地扬声器或耳机**的输出名称，然后显式运行 `radiolab audio replay runtime/rx.wav --output-device $speakerDevice --gain 0.05 --confirm-local-output`（先把 `$speakerDevice` 设为所选名称）。此命令不操作 PTT；所选输出必须未接电台或 VOX，系统音量仍需由操作者检查。回放结果会写入历史；目前只通过 FakeAudio 测试，未在真实输出设备播放。

真实发送命令另需 `--on-air`。程序仅在明确配置 TX 设备和 PTT 串口、确认固件 1.2+、完成**无辐射台架** PTT 验证、确认合规操作前提后才允许发射。默认示例配置全部锁定。发送前验证输出设备支持 48 kHz；发送时互斥、限制连续时长、播放异常或超时尽力释放 PTT。打开串口本身可能因驱动或固件产生控制线瞬态，软件无法保证所有物理故障都不会卡键。不要在未完成台架验证前连接会辐射的电台进行测试。当前没有执行任何真实发射。

## SSTV

```powershell
radiolab sstv encode examples/test_image.png -o runtime/sstv.wav
radiolab sstv send examples/test_image.png -o runtime/sstv-send.wav --dry-run
radiolab sstv decode runtime/sstv.wav -o runtime/sstv-decoded.png
```

现由 RadioLab 自写算法生成和解码 Robot36：48 kHz、单声道 16-bit PCM WAV，320×240 PNG。另提供 Robot72、Martin1/2、Scottie1/2 的 Python API。独立软件只用于离线交叉验证，不是运行依赖。自写编码器尚未完成硬件/RF 验收，真实 SSTV 发射保持锁定。

CLI/GUI 的 SSTV 编解码只调用内置算法，旧外部后端选择和程序路径入口已移除；旧 SQLite 工具路径设置不会被读取。

`sstv decode` 默认在输出图片旁保存 `.decode.log`，记录自写解码器识别的 VIS、模式、尺寸或失败原因，并关联历史。

## 来源与许可

RadioLab 源码以 [MIT 许可证](LICENSE)发布。随项目安装的第三方依赖和独立 Windows 包内的组件各自适用其许可证；Windows 包的收集清单与仍待核对事项见 [发布门槛](docs/release_gate.md)。

RadioLab 的协议、AFSK 与音频适配为独立编写，未复制其他项目源码。SSTV 模式时序依据 [JL Barber 的提案](https://www.classicsstv.com/downloads/daytonpaper.pdf)，算法独立编写；第三方软件仅用于离线互验，不属于运行依赖。

官方 AIOC [Getting Started](https://github.com/skuep/AIOC/wiki/Getting-Started) 说明：音频接口为单声道输入/输出，首选 48 kHz；固件 1.2.0 起串口 PTT 为 DTR=1、RTS=0，旧固件 DTR=1 即按下。当前代码仅允许经台架确认的 1.2+ 配置，不能把打开 COM 口视为安全。SSTV 模式时序依据 [JL Barber 的提案](https://www.classicsstv.com/downloads/daytonpaper.pdf)，算法独立编写，未复制其他 SSTV 项目源码。

JSONL 历史默认保存到 `runtime/history.jsonl`，文本内容也会保存；涉及隐私时请自行保护或删除该目录。历史文件引用若已删除，`history list` 会标记“文件缺失”。
发送预检、编码或解码失败会记录原因；尚未生成的 WAV 会明确记为不存在。

V0.2 的 SQLite 历史迁移已可离线试用：`radiolab history migrate --source runtime/history.jsonl -o runtime/history.sqlite3`，随后用 `radiolab history list --database runtime/history.sqlite3 --mode sstv` 查询。重复导入不会增加相同记录；JSONL 原件保留。当前收发服务仍写 JSONL，若继续操作，需再次执行迁移才能把新记录加入 SQLite。

V0.3 可用 `radiolab history export --database runtime/history.sqlite3 -o runtime/history-export.zip` 导出 SQLite 中的全部记录和现存 WAV、图片、解码日志；GUI 的“导出历史 ZIP”提供同样功能。ZIP 含 `entries.jsonl` 和 `manifest.json`，媒体只复制一次、附 SHA-256；缺失文件在清单中标出。目标 ZIP 必须是新路径，源数据库与媒体不会改写。导出包可能包含私人文本和原始文件路径，请妥善保管；目前没有从 ZIP 导回 SQLite 的功能。

V0.3 桌面终端预览可在独立虚拟环境安装并运行；当前包版本为 `0.3.0.dev0`，协议帧仍是 v0.1：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[gui,devices,sstv]'
.\.venv\Scripts\radiolab-gui.exe --database runtime/history.sqlite3 --source runtime/history.jsonl
```

窗口可筛选、搜索、复制记录，也可手动从 JSONL 导入 SQLite。SSTV 解码使用内置算法；选中已有 WAV 后按“解码选中 WAV”，结果与日志写入 SQLite 并显示图片；失败会显示原因。

“刷新设备”只枚举 TX 输出、RX 输入与串口，不自动选择或打开端点。选择明确的 RX 输入后，可指定最长录音时长与新的 `.wav` 路径，按“开始录音”，需要提前结束时按“停止录音”；已采集的 PCM 会保存为 48 kHz、单声道 PCM16 WAV 并写入 SQLite。选中 WAV 后按“解码文本 WAV”，只有完整帧和 CRC 通过且类型为 UTF-8 文本时才记为成功。关窗时若正在录音，会先请求停止并保存。启动时不访问音频、串口或 PTT，录音功能没有任何发射动作。CLI 的 JSONL 新记录不会自动同步到 GUI 的 SQLite。
V0.3 预览的“自动监听文本”使用明确选定的 RX 输入和最长时长（不超过 120 s），边采集边查找完整帧；通过 CRC 的文本会即时进入 SQLite 历史，重复的同序号同内容帧在 10 s 内只记一次。水平电平条约每 0.2 s 更新峰值、RMS（dBFS）及过低/接近削波提示；电平只用于检查输入，不能证明收到有效帧。“停止监听”或关窗后保留整段 48 kHz 单声道 PCM16 WAV，设备中途出错也保留已有的部分录音并记录失败原因。此功能目前只用 Fake 输入和合成 PCM 验证，尚未用真实手台接收录音验证；自动监听不会操作 PTT，也不会自动 ACK。

选中有 WAV 的历史记录后，也可选择独立的本地扬声器或耳机输出，设置回放增益（默认 0.05），确认该输出未接电台或 VOX，并在逐次确认框中同意后回放；“停止回放”或关窗会请求停止并记录结果。本地回放不操作 PTT，且拒绝使用当前选定的 TX 输出。此功能目前只通过 FakeAudio 测试，尚未在真实扬声器播放。

文本栏可填写消息和序号，按“生成文本 WAV”进行纯离线编码；结果记入 SQLite，不播放，也不按下 PTT。GUI 的“发射文本/发射图片”默认锁定：需先分别选择 TX 输出和串口，指定已台架确认的 `config.toml`（可用 `--config` 预填），勾选本次操作确认，并逐次在摘要对话框确认。后台任务还会重新枚举设备，使用 PTT 互斥、连续发射上限和异常释放；界面按实际事务显示 PTT 已按下、正在播放及尾延时状态，“停止发射”会请求停止音频与释放 PTT。自写 SSTV 编码器当前在算法层额外禁止真实发射，直到独立互通和实机门槛完成。软件不能保证进程强杀、USB 断电或硬件故障时必定释放；本机尚无 AIOC/串口，GUI 发射目前只经过 FakePTT/FakeAudio 测试，**未进行真实 RF 实测**。

V0.3 的“编辑配置”可把主界面已选的 TX 输出、RX 输入和 PTT 串口写入 TOML，并填写增益、延时、手台记录项。固件 1.2+、无辐射 PTT 台架验证与合规操作前提须由操作者如实勾选；新配置默认未确认。保存不打开设备，覆盖已有配置会先询问并保留 `.bak` 备份。即使保存了确认，真实发射仍需当次勾选与摘要确认。

“生成图像 WAV”按钮使用自写 Robot36 算法，只生成离线文件，不会播放或发射。
新记录包含 `run_id`、UTC/本地时间、方向、采样率和文件路径；可在配置的 `[radio]` 下填写人工确认的 `frequency_hz` 与 `power_setting` 供实测追溯。软件**不会**通过 AIOC 设置手台频率或功率，旧 JSONL 记录仍可读取。

真实双手台测试目前仅准备流程，未执行 RF 实测。

V0.3 的“图片库”从 SQLite 历史列出最近 500 条图片引用，相同路径只显示一次；缺失、过大或无法读取的图片明确标为不可用。选择可用图片只回填发送栏并显示预览，**不会编码或发射**。缩略图限制输入文件不超过 20 MB、尺寸不超过 2000 万像素。

“最近文件”从 SQLite 历史显示最近 WAV、图片、日志引用，按实际磁盘状态标记缺失。复制完整路径和打开所在文件夹都只在用户点击后执行；不会自动播放、解码、发射或删除文件。

## Windows 离线打包

在**干净的 Windows 虚拟环境**中构建，避免把 Conda 中与本项目无关的包一同收集。PyInstaller 只在本机生成目录包，默认输出为带时间戳的 `dist/windows-*`；已有输出时脚本会拒绝覆盖，也可用 `-OutputRoot` 指定新目录。打包、`--help` 和以下离线演示不会发射：

```powershell
python -m venv .venv-package
.\.venv-package\Scripts\python.exe -m pip install -e '.[gui,devices,sstv]' 'pyinstaller>=6.16,<7'
$outputRoot = ".\dist\windows-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\scripts\build_windows.ps1 -Python '.\.venv-package\Scripts\python.exe' -OutputRoot $outputRoot
Push-Location $outputRoot
.\radiolab\radiolab.exe --help
.\radiolab\radiolab.exe text encode 'HELLO WORLD' -o hello.wav
.\radiolab\radiolab.exe text decode hello.wav
.\radiolab\radiolab.exe text decode examples\hello_afsk.wav
.\radiolab\radiolab.exe sstv encode examples\test_image.png -o sstv.wav
Pop-Location
.\scripts\smoke_windows_gui.ps1 -BundleRoot $outputRoot
```

打包脚本会拒绝共享系统包的虚拟环境，并限制依赖搜索路径，避免从外部工具误收集不兼容 DLL。GUI 检查脚本实际创建并关闭隐藏主窗口，启动异常会报错；仅运行 GUI 的 `--help` 不足以证明窗口可用。CLI 与 GUI 是各自完整的目录，移动时需保留对应的 `_internal` 子目录。GUI 程序位于 `$outputRoot/radiolab-gui/radiolab-gui.exe`；启动时不打开声卡、串口或 PTT。示例配置和小测试图位于打包输出根目录。SSTV 编解码已内置；独立解码器只用于互通验证。当前已构建的旧目录包仍可能包含旧 SSTV 后端，需重新打包才会带入本次代码。旧包曾在本机 Windows 11 完成离线窗口启动、文本解码和 Robot36 WAV 生成，尚未在另一台电脑或真实无线电链路验收。

目录包包含构建环境生成的 `third_party_licenses/THIRD_PARTY_NOTICES.md` 和可收集的许可文件，包括与当前版本对应的 Qt 开源许可文本。使用 Conda Python 构建时，另生成 `CONDA_NATIVE_BINARIES.md`：按包内实际文件与 Conda 包缓存哈希核对来源，并收集对应许可文本。可选 `-QtSourceCache`、`-MesaSourceCache`、`-LLVMSourceCache` 可附上已核对的源码候选归档；Microsoft/Qt 原生库的实际分发条件、完整对应源码、实际换库运行和项目自身许可仍标为待完成，当前目录包仅供预览。

当前 `0.3.0.dev0` 为软件预览，不宣称 V1.0 或硬件兼容性已完成。
