param(
    [Parameter(Mandatory = $true)][string]$BundleRoot
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $BundleRoot).Path
$internal = Join-Path $root 'radiolab-gui\_internal'
$files = @(
    'PySide6\MSVCP140.dll',
    'PySide6\MSVCP140_1.dll',
    'PySide6\MSVCP140_2.dll',
    'PySide6\opengl32sw.dll',
    'PySide6\VCRUNTIME140.dll',
    'PySide6\VCRUNTIME140_1.dll',
    'shiboken6\MSVCP140.dll',
    'shiboken6\VCRUNTIME140.dll',
    'shiboken6\VCRUNTIME140_1.dll'
)
$qtSigned = @(
    'PySide6\MSVCP140_1.dll',
    'PySide6\MSVCP140_2.dll',
    'PySide6\opengl32sw.dll'
)
$lines = @(
    '# Qt/PySide 包内外部运行库签名',
    '',
    '此表仅核对当前 Windows 对包内文件的 Authenticode 签名和文件版本。',
    '有效签名不证明源码、构建选项、许可或再分发权利。',
    '',
    '| 包内文件 | 签名主体 | 文件版本 | SHA-256 |',
    '|---|---|---|---|'
)
foreach ($relative in $files) {
    $file = Join-Path $internal $relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "待核对的原生文件不存在：$file"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    $publisher = if ($relative -in $qtSigned) { 'O=The QT Company Oy' }
                 else { 'O=Microsoft Corporation' }
    if ($signature.Status -ne 'Valid' -or
        $null -eq $signature.SignerCertificate -or
        -not $signature.SignerCertificate.Subject.Contains($publisher)) {
        throw "原生文件签名未通过：$relative，状态 $($signature.Status)，预期 $publisher"
    }
    $version = (Get-Item -LiteralPath $file).VersionInfo.FileVersion
    if (-not $version) { $version = '无' }
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    $subject = $signature.SignerCertificate.GetNameInfo(
        [System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
    $lines += "| ``$relative`` | $subject | $version | ``$hash`` |"
}
$lines += ''
$lines += 'Mesa 源码版本、Qt 构建补丁，以及 Microsoft VC 运行库的分发条件仍须分别核对。'
$lines += ''
$report = Join-Path $root 'qt_source_archives\NATIVE_SIGNATURES.md'
if (-not (Test-Path -LiteralPath (Split-Path -Parent $report) -PathType Container)) {
    throw "缺少 Qt 源码归档目录：$(Split-Path -Parent $report)"
}
Set-Content -LiteralPath $report -Value $lines -Encoding utf8
Write-Host "原生文件签名核对：$($files.Count) 个有效；见 $report"
