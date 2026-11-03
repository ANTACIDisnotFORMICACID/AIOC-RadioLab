param(
    [string]$Python = "python",
    [string]$OutputRoot = "",
    [string]$QtSourceCache = "",
    [string]$MesaSourceCache = "",
    [string]$LLVMSourceCache = ""
)

$ErrorActionPreference = "Stop"
if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
    throw "此脚本仅支持 Windows。"
}
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repo ("dist\windows-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$output = [IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $output) {
    throw "输出路径已存在：$output。请指定新的 -OutputRoot，避免覆盖已有构建。"
}
$work = Join-Path $repo (Join-Path "build" (Split-Path -Leaf $output))
if (Test-Path -LiteralPath $work) {
    throw "工作路径已存在：$work。请先人工核对旧构建，再清理或改用新的工作目录。"
}

$pythonPrefix = (& $Python -c "import sys; print(sys.prefix)").Trim()
if ($LASTEXITCODE -ne 0) { throw "无法检查打包 Python 环境。" }
$venvConfig = Join-Path $pythonPrefix "pyvenv.cfg"
if ((Test-Path -LiteralPath $venvConfig) -and
    (Select-String -LiteralPath $venvConfig -Pattern '^include-system-site-packages\s*=\s*true\s*$' -Quiet)) {
    throw "打包虚拟环境共享系统包：$pythonPrefix。请新建不带 --system-site-packages 的环境。"
}
$pythonExe = (& $Python -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0) { throw "无法定位打包 Python 可执行文件。" }
$pythonBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
if ($LASTEXITCODE -ne 0) { throw "无法定位打包 Python 基础环境。" }
# 只让打包分析看到 Python、Conda 基础库和 Windows 系统库，排除调用者 PATH 中的外部 ICU。
$bundleDirs = @((Split-Path -Parent $pythonExe), [Environment]::SystemDirectory, $env:WINDIR)
$baseBin = Join-Path $pythonBase "Library\bin"
if (Test-Path -LiteralPath $baseBin) {
    $bundleDirs += $baseBin
}
$bundlePath = $bundleDirs -join ";"
function Invoke-Bundler([string[]]$Options) {
    $originalPath = $env:Path
    try {
        $env:Path = $bundlePath
        & $Python -m PyInstaller @Options
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）。" }
    } finally {
        $env:Path = $originalPath
    }
}

Invoke-Bundler @("--version")

$spec = Join-Path $work "spec"
$cliWork = Join-Path $work "cli"
$guiWork = Join-Path $work "gui"
New-Item -ItemType Directory -Path $output, $spec, $cliWork, $guiWork | Out-Null

$cliOptions = @("--onedir", "--console", "--name", "radiolab",
    "--distpath", $output, "--workpath", $cliWork, "--specpath", $spec)
$cliOptions += (Join-Path $repo "packaging\cli_entry.py")
Invoke-Bundler $cliOptions

$guiOptions = @("--onedir", "--windowed", "--name", "radiolab-gui",
    "--distpath", $output, "--workpath", $guiWork, "--specpath", $spec)
$guiOptions += (Join-Path $repo "packaging\gui_entry.py")
Invoke-Bundler $guiOptions
if (Test-Path -LiteralPath (Join-Path $output "radiolab-gui\_internal\icuuc.dll")) {
    throw "GUI 包收集了外部 ICU DLL；请检查打包环境，避免 QtCore 加载不兼容的 ICU。"
}

Copy-Item -LiteralPath (Join-Path $repo "config.example.toml") -Destination $output
Copy-Item -LiteralPath (Join-Path $repo "README.md") -Destination $output
Copy-Item -LiteralPath (Join-Path $repo "docs") -Destination (Join-Path $output "docs") -Recurse
New-Item -ItemType Directory -Path (Join-Path $output "examples") | Out-Null
Copy-Item -LiteralPath (Join-Path $repo "examples\test_image.png") -Destination (Join-Path $output "examples")
Copy-Item -LiteralPath (Join-Path $repo "examples\hello_afsk.wav") -Destination (Join-Path $output "examples")
& $Python (Join-Path $repo "scripts\collect_licenses.py") $output
if ($LASTEXITCODE -ne 0) { throw "第三方许可清单生成失败。" }
if (Test-Path -LiteralPath (Join-Path $pythonBase "conda-meta")) {
    & $Python (Join-Path $repo "scripts\collect_native_licenses.py") $output $work $pythonBase
    if ($LASTEXITCODE -ne 0) { throw "Conda 原生文件或许可材料核对失败。" }
}
if ($QtSourceCache) {
    $qtRoot = Join-Path $output "radiolab-gui\_internal\PySide6"
    $qtFiles = @("Qt6Core.dll", "Qt6Svg.dll", "plugins\imageformats\qtiff.dll")
    foreach ($relative in $qtFiles) {
        $file = Join-Path $qtRoot $relative
        if (-not (Test-Path -LiteralPath $file)) { throw "源码候选版本核对失败：包内缺少 $file" }
        $version = (Get-Item -LiteralPath $file).VersionInfo.FileVersion
        if ($version -ne "6.11.3.0") { throw "源码候选仅适用于 Qt 6.11.3，包内 $relative 为 $version" }
    }
    $pysideVersion = (& $Python -c "from importlib.metadata import version; print(version('PySide6-Essentials'))").Trim()
    if ($LASTEXITCODE -ne 0 -or $pysideVersion -ne "6.11.3") {
        throw "源码候选仅适用于 PySide6-Essentials 6.11.3，构建环境为 $pysideVersion"
    }
    & $Python (Join-Path $repo "scripts\stage_qt_sources.py") $QtSourceCache $output
    if ($LASTEXITCODE -ne 0) { throw "Qt 源码候选归档核对失败。" }
    & $Python (Join-Path $repo "scripts\audit_qt_bundle.py") $output (Join-Path $output "qt_source_archives")
    if ($LASTEXITCODE -ne 0) { throw "Qt/PySide 包内原生文件与源码候选映射失败。" }
    & (Join-Path $repo "scripts\audit_qt_signatures.ps1") -BundleRoot $output
    if ($LASTEXITCODE -ne 0) { throw "Qt/PySide 外部运行库签名核对失败。" }
}
if ($MesaSourceCache) {
    & $Python (Join-Path $repo "scripts\stage_graphics_sources.py") $MesaSourceCache $output
    if ($LASTEXITCODE -ne 0) { throw "Mesa 源码候选归档核对失败。" }
}
if ($LLVMSourceCache) {
    & $Python (Join-Path $repo "scripts\stage_graphics_sources.py") $LLVMSourceCache $output --source llvm
    if ($LASTEXITCODE -ne 0) { throw "LLVM 源码候选归档核对失败。" }
}
Write-Host "完成：$output。CLI 和 GUI 分别位于 radiolab、radiolab-gui 子目录。"
