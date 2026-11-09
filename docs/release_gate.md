# V0.3 预览包与 V1.0 发布门槛

当前应用版本为 `0.3.0.dev0`，Packet 帧协议仍为 v0.1。项目源码采用 MIT 许可证；源码包与依赖一起安装供软件预览。旧 Windows 目录包只供本机离线评估，不能代表当前代码；测试通过、文件生成与真实设备/射频成功是三种不同证据。

| 门槛 | 当前证据 | 状态 |
|---|---|---|
| 源码包与离线文本闭环 | 当前源码构建 sdist/wheel，`twine check` 通过；本机开发环境解出公开 `examples/hello_afsk.wav` 的 `HELLO WORLD`、CRC OK | 本机软件已验，异机安装待验 |
| GUI 与历史 | FakeAudio/FakePTT 测试、SQLite 历史、自写 Robot36 离线解码图片 | 软件已验，实机待验 |
| 便携目录包 | 旧版本曾在干净虚拟环境构建，并在限制 PATH 后通过包内 CLI 与 GUI 启动检查；当前源码尚未重新构建目录包 | 旧版本机已验，当前版待验 |
| AIOC 音频/PTT | 无辐射台架确认固件、开口瞬态、DTR/RTS、录放设备与释放 | 待用户设备实测 |
| 双手台 RF | 独立 RX 输入保存原始 WAV，文本 CRC 或 SSTV 图像由第二台手台接收 | 待用户授权实测 |
| 其他 SSTV 接收端 | 六种自写模式与 Rust `sstv` 0.2.0 双向离线互验，五种与 Open-SSTV 0.6.17 双向互验；VIS/时长/首中末行同步与 Dayton 文档一致。Robot36/72 与常见软件的颜色缩放仍有差异，MMSSTV/Android 与 RF 待检查，详见[实验记录](sstv_interop_20261030.md) | 离线协议已验，颜色/实机待验 |
| 对外分发许可 | 项目源码 MIT 许可证已入库；旧目录包收集第三方许可清单与 Qt 源码候选归档。Microsoft/Qt 分发条件、完整对应源码及实际换库运行仍需核对 | 源码许可已标明，独立 Windows 包待人工复核 |

本机构建环境的 `PySide6-Essentials`、`shiboken6` 包元数据标记为 LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only 多许可；其 wheel 只有商业许可参考文件。仓库现保存了 [Qt 官方 PySide6 6.11.3 源码包](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/)内的 LGPLv3/GPLv2/GPLv3 全文，打包时按版本复制。Qt 官方说明了 [Qt for Python 的许可选项](https://doc.qt.io/qtforpython-6.11/index.html)和 [LGPL 分发时需核对的义务](https://www.qt.io/development/open-source-lgpl-obligations)。[PyInstaller 的例外条款](https://pyinstaller.org/en/stable/license.html)允许分发所构建的应用，但不免除依赖本身的许可条件。这里仅列出需核对的发布事项，不作法律结论。RadioLab 未复制原 FM 项目或 Open-SSTV 的源码；当前 SSTV 运行不需要独立安装 Open-SSTV。

打包脚本现在从构建环境收集已安装依赖的许可文件与 Python 运行时许可，并列出仍待核对的项目。pyserial 3.5 的 wheel 未包含完整许可文件，仓库保存了从[该版本上游 LICENSE.txt](https://github.com/pyserial/pyserial/blob/v3.5/LICENSE.txt)取得的文本，SHA-256 为 `F91CB9813DE6A5B142B8F7F2DEDE630B5134160AEDAEAF55F4D6A7E2593CA3F3`，仅在实际打包版本为 3.5 时使用。Qt 的商业许可参考文件不会被当成 LGPL/GPL 全文。清单是核对材料，仍须核查实际打包的 Qt/原生组件、源码获取、用户替换方式及发布条件。

可选的 [Qt 源码候选与库替换核对](qt_source_replacement.md)列出本机目录包内的动态库、四份已验哈希的官方源码归档及未来换库的测试步骤。Qt 官方 [FAQ](https://www.qt.io/faq/qt-open-source-licensing)明确说明，仅指向其网站的源码链接不足以代替发行方控制下的源码提供安排；附带候选归档也仍需检查是否覆盖实际二进制的完整对应源码。当前尚未换入修改版库实测，不能把门槛标为通过。

附源码的本机包现可生成逐文件 `QT_BINARY_SOURCE_MAP.md`：41 个 PySide6/Shiboken6 原生文件与已安装 wheel 哈希一致，32 个找到模块源码目录候选；9 个 MSVC 运行库或 `opengl32sw.dll` 尚无确认的对应源码映射。Mesa 11.2.2 官方归档已按发布说明 SHA-256 核验；LLVM 3.6.2 官方归档和历史分离签名已取得并完成密码学核对，但签名公钥现已过期，撤销状态未独立核实。两份归档可选随包附为**候选**，仍不证明 Qt DLL 的准确源码或许可条件。此清单不覆盖 Qt 内部第三方依赖或实际构建选项，发布门槛状态不因此改变。

这 9 个文件现另有 Windows 签名清单：6 个 VC 文件由 Microsoft 签名，2 个 VC 文件及 `opengl32sw.dll` 由 Qt 签名，均显示有效；后者内嵌 Mesa 11.2.2/LLVM 3.6.2 版本字符串。仍缺准确源码、补丁/构建记录与 Microsoft 分发条件核对，签名不等于许可通过。

发布前须在另一台未装开发环境的 Windows 电脑上按 [README](../README.md) 复现安装、GUI、离线 WAV 与错误提示，并根据实际硬件测试填补上表。没有 AIOC 或操作授权时保持“待验”，不得把 Fake 测试写成 RF 成功。

本机曾有旧目录包仅通过 `--help` 检查，实际打开 GUI 时因从外部 Poppler 路径误收集 `icuuc.dll` 而失败。当前构建脚本限制依赖搜索范围，并用 [GUI 启动检查](../scripts/smoke_windows_gui.ps1)验证真实窗口创建与关闭；旧包不可作为 GUI 可用证据。当前构建基于 Conda Python，包内的 Conda 原生文件有单独的 `CONDA_NATIVE_BINARIES.md`：记录实际文件、包版本、来源包和 SHA-256，复制缓存中的 `info/licenses` 文本。[Conda 包规范](https://docs.conda.io/projects/conda/en/stable/user-guide/concepts/pkg-specs.html)说明这些元数据和文件的含义；收集材料不代替 Microsoft、Qt 与其他组件的分发条件核对，异机可运行性也仍待实测。
