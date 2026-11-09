# Qt 源码候选归档与库替换核对

这份说明只用于核对当前 **Windows 目录包、Qt/PySide 6.11.3**。它不是发布许可合规证明。Qt 官方要求发行方控制下的对应源码提供安排，也要求用户能替换库并运行程序；仅给 Qt 上游链接不够。[Qt 开源许可 FAQ](https://www.qt.io/faq/qt-open-source-licensing)

## 当前包内文件

`radiolab-gui/_internal/PySide6/` 下实际有 `Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`、`Qt6Network.dll`、`Qt6Svg.dll`、`pyside6.abi3.dll`，以及 `QtCore.pyd`、`QtGui.pyd`、`QtWidgets.pyd`、`QtNetwork.pyd`。`radiolab-gui/_internal/shiboken6/Shiboken.pyd` 也在包内。`PySide6/plugins/` 下还有 `platforms`、`imageformats`、`iconengines`、`networkinformation`、`styles`、`tls`、`generic` 等 DLL。当前抽查的 Qt 核心、SVG 与 TIFF 插件的 Windows 文件版本均为 `6.11.3.0`。

对应的官方源码候选集合：

| 包内组件 | 官方 6.11.3 源码归档 |
|---|---|
| PySide6/Shiboken 绑定 | [pyside-setup](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/) |
| QtCore/Gui/Widgets/Network 与大部分平台插件 | [qtbase](https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/) |
| Qt6Svg 与 SVG 插件 | [qtsvg](https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/) |
| 部分图像格式插件 | [qtimageformats](https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/) |

已从官方取得四个 ZIP 并验证其完整性与 SHA-256；本机文件位于忽略的 `runtime/qt-source-6.11.3/`。其他机器可从上表对应的官方目录下载**同名 6.11.3 ZIP**到该目录；[暂存脚本](../scripts/stage_qt_sources.py)内固定了四个直接下载 URL 与预期哈希。构建时传入 `-QtSourceCache` 才会在目录包根部生成 `qt_source_archives/`，其中 `README.md` 记录每个 ZIP 的官方来源和哈希。版本不匹配或哈希错误会失败；默认预览构建不附上约 103 MB 的源码候选归档。

[原生文件审计脚本](../scripts/audit_qt_bundle.py)还会在附源码包的构建中生成 `QT_BINARY_SOURCE_MAP.md`：逐项核对 GUI 包内 PySide6/Shiboken6 的 DLL/PYD 与构建环境 wheel 的 SHA-256，并确认候选 ZIP 中有对应模块源码目录。当前本机预览包核对 **41 个**原生文件，其中 **32 个**有模块源码目录候选；余下 **9 个**为包内 `MSVCP140*`/`VCRUNTIME140*` 与 `opengl32sw.dll`，须单独核对来源与许可。源码目录存在和文件哈希匹配均不证明完整对应源码，图像格式等第三方代码也未逐一核对。新增未知文件时审计会失败，需先确认来源再扩充映射。

[Windows 签名审计脚本](../scripts/audit_qt_signatures.ps1)对这 9 个文件另生成 `NATIVE_SIGNATURES.md`。当前包内 9 个签名均由 Windows 判为有效：6 个 VC 运行库由 Microsoft 签名，`MSVCP140_1.dll`、`MSVCP140_2.dll` 与 `opengl32sw.dll` 由 Qt 签名；8 个 VC 文件的版本信息均为 `14.44.35211.0`。Qt [Windows 图形文档](https://doc.qt.io/qt-6/windows-graphics.html)说明预编译包的 `opengl32sw.dll` 是 Mesa llvmpipe；本包文件中能找到 `Mesa 11.2.2` 与 `LLVM 3.6.2` 字符串。已取得 Mesa [官方归档](https://archive.mesa3d.org/older-versions/11.x/11.2.2/)中的 `mesa-11.2.2.tar.xz`，SHA-256 与[官方发布说明](https://docs.mesa3d.org/relnotes/11.2.2.html)一致，归档含 llvmpipe 源码和许可文件。构建时可用 `-MesaSourceCache` 选择附入 `graphics_source_candidates/`；脚本会核对哈希、XZ/归档完整性及包内版本字符串。本机缓存位于忽略的 `runtime/graphics-source-candidates/`。

LLVM [官方 3.6.2 源码与分离签名](https://releases.llvm.org/3.6.2/)也已取得。签名指向历史公钥 `11E521D646982372EB577A1F8F0871F202119294`，该指纹见 [LLVM 开发邮件列表的签名密钥讨论](https://lists.llvm.org/pipermail/llvm-dev/2017-August/116388.html)；公钥从 Ubuntu keyserver 取得并核对完整指纹。签名日期为 2015-07-11，所取得公钥的到期日为 2019-04-18：使用 PGPy 0.6.0 校验原始归档的签名密码学结果为真，修改归档一字节后为假；通用验证结果因公钥**现在已过期**而返回失败，且没有独立核实撤销状态。归档 SHA-256 为 `F60DC158BFDA6822DE167E87275848969F0558B3134892FF54FCED87E4667B94`，分离签名 SHA-256 为 `B2E96FC46B7B13E5655B9BF9C97B812235F3C7AD61E5B10CB186B135EF8928B9`。构建时可用 `-LLVMSourceCache` 可选附入 `llvm_source_candidates/`；脚本按这两个固定哈希、归档完整性与包内版本线索核对，不会在构建时重复执行 PGP 验证。**没有证据证明**包内 DLL 由未修改的 Mesa/LLVM 官方归档构建；Qt 补丁、构建参数和完整对应源码仍缺。签名、文件版本与内嵌字符串都不是完整对应源码的证明。

Microsoft [VC 运行库分发说明](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)要求发行方核对 Visual Studio 许可条款；Qt 签名的两个 VC 文件也不能因此推定可由 RadioLab 自由再分发。签名审计只记录身份线索，不清除对外分发许可门槛。

```powershell
& .\scripts\build_windows.ps1 -Python 'C:\path\to\python.exe' `
  -OutputRoot '.\dist\windows-with-qt-source' `
  -QtSourceCache '.\runtime\qt-source-6.11.3' `
  -MesaSourceCache '.\runtime\graphics-source-candidates' `
  -LLVMSourceCache '.\runtime\graphics-source-candidates'
```

## 库替换测试步骤

1. 完整复制 `radiolab-gui` 目录到可写的测试目录，先确认原包可以启动并正常关窗。
2. 关闭程序后，把自行构建且 ABI/架构/依赖匹配的 Qt DLL 和相关插件放入测试副本中的**相同相对路径**。修改 PySide/Shiboken 绑定时，同步替换对应 `.pyd`/DLL。保留原副本用于恢复。
3. 在测试副本运行 `radiolab-gui.exe --help`，再启动 GUI、检查窗口、图片、解码和正常退出；记录实际加载的 DLL 路径及结果。若库不兼容，恢复原副本。不要用这项测试触发 RF 发射。

目前只验证了原版目录包及文件位置，**没有自行构建并换入修改版 Qt 库**，因此用户替换后能否运行仍待实测。候选源码集合也尚未核对包内全部原生依赖、构建选项、第三方代码及可能的补丁。对外发布前还需确定 RadioLab 自身许可，完整核对对应源码与分发条件；[Qt 的说明](https://www.qt.io/development/open-source-lgpl-obligations)不能由这份操作说明代替。
