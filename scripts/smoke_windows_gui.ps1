param(
    [Parameter(Mandatory = $true)][string]$BundleRoot
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $BundleRoot).Path
$gui = Join-Path $root "radiolab-gui\radiolab-gui.exe"
if (-not (Test-Path -LiteralPath $gui)) { throw "GUI 可执行文件不存在：$gui" }

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class RadioLabWindowProbe {
    public delegate bool Callback(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumWindows(Callback callback, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr window, Callback callback, IntPtr data);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr window, StringBuilder text, int length);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);

    static string Text(IntPtr window) {
        var value = new StringBuilder(4096);
        GetWindowText(window, value, value.Capacity);
        return value.ToString();
    }
    public static IntPtr Find(uint process, string prefix) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((window, data) => {
            uint owner;
            GetWindowThreadProcessId(window, out owner);
            if (owner == process && Text(window).StartsWith(prefix, StringComparison.Ordinal)) found = window;
            return true;
        }, IntPtr.Zero);
        return found;
    }
    public static string[] Children(IntPtr window) {
        var values = new List<string>();
        EnumChildWindows(window, (child, data) => {
            string value = Text(child);
            if (value.Length > 0) values.Add(value);
            return true;
        }, IntPtr.Zero);
        return values.ToArray();
    }
}
'@

$oldPath = $env:Path
$env:Path = [Environment]::SystemDirectory + ";" + $env:WINDIR
$process = $null
try {
    $database = Join-Path $env:TEMP ("radiolab-gui-smoke-" + [guid]::NewGuid().ToString("N") + ".sqlite3")
    $process = Start-Process -FilePath $gui -ArgumentList @("--database", $database) -PassThru -WindowStyle Hidden
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 200
        $process.Refresh()
        if ($process.HasExited) { throw "GUI 在创建窗口前退出，退出码 $($process.ExitCode)" }
        $crash = [RadioLabWindowProbe]::Find([uint32]$process.Id, "Unhandled exception in script")
        if ($crash -ne [IntPtr]::Zero) {
            throw ("GUI 启动异常：" + ([RadioLabWindowProbe]::Children($crash) -join "；"))
        }
        $window = [RadioLabWindowProbe]::Find([uint32]$process.Id, "RadioLab")
    } while ($window -eq [IntPtr]::Zero -and [DateTime]::UtcNow -lt $deadline)
    if ($window -eq [IntPtr]::Zero) { throw "GUI 在 20 秒内没有创建主窗口" }
    if (-not [RadioLabWindowProbe]::PostMessage($window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
        throw "无法请求关闭 GUI 主窗口"
    }
    if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0) {
        throw "GUI 未正常关闭；退出码 $($process.ExitCode)"
    }
    Write-Output "GUI 主窗口创建并关闭，退出码 0；未进行设备/PTT 操作。"
} finally {
    $env:Path = $oldPath
    if ($null -ne $process) {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
}
