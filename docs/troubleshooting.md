# 离线与设备故障排查

先在**不连接发射电台**的条件下检查软件路径。保留原始 WAV；不要把录音中有声音、GUI 电平条移动或设备预检通过当作收到有效消息。

## 1. 先解码已知基准

仓库的 `examples/hello_afsk.wav` 是纯算法生成的 48,000 sample/s、单声道 PCM16，时长 0.68 s，文本为 `HELLO WORLD`、序号 0。它不包含真实声卡或射频录音。

```powershell
radiolab text decode examples/hello_afsk.wav
radiolab text encode 'HELLO WORLD' -o runtime/new-hello.wav
radiolab text decode runtime/new-hello.wav
```

两次解码应显示 `CRC OK` 和 `HELLO WORLD`。若基准失败，先确认正在运行本仓库安装的 `radiolab`，查看 `radiolab --version` 和 `radiolab --help`，再运行 `python -m pytest -q`。不要用真实设备排查纯软件失败。

## 2. 设备能否被选择

`radiolab devices` 分别列出输入、输出和串口。设备编号会随插拔改变，按实际名称重新选择；TX 是 AIOC USB **输出**，RX 要接第二台手台的独立录音输入或用电脑麦克风采集其扬声器。`radiolab check --config config.toml` 只核对配置、48 kHz 音频能力与串口枚举，不打开 PTT，也不证明打开串口瞬间安全。若看不到设备，检查 Windows 声音设置、USB 枚举和线缆，再重新列设备。缺少 `sounddevice` 或 `pyserial` 时安装 `radiolab[devices]`。

## 3. 实录 WAV 解不出文本

先核实文件确实来自**第二台手台**，不是 AIOC 或 Windows 的软件回环。保留原文件并检查 `radiolab text decode runtime/rx.wav` 的错误信息：几乎静音时核对输入设备和增益；接近削波时降低输入电平；找不到前导码/同步字或 CRC 失败时检查是否录到完整的发射起止、失真和噪声。支持的 WAV 输入会被转换为内部 48 kHz 单声道；格式异常时应重新保存为常见 PCM WAV。CRC 未通过的内容不能当作成功接收。真实链路的操作顺序见 [硬件流程](hardware.md)。

## 4. Robot36 解码失败

先用 `radiolab sstv decode runtime/sstv.wav -o runtime/decoded.png` 调用内置解码器。`.decode.log` 记录检测到的 VIS、模式、尺寸或失败原因。检查原始 WAV 是否含完整 VIS 头、行扫描和足够的录音时长；当前 GUI 入口仅接受 Robot36。色彩或行同步不一致时保留 WAV 与日志，再用独立接收软件交叉检查。真实 SSTV 发射仍受实机验收门槛锁定。

## 5. PTT 与发射异常

不要通过反复运行 `--on-air` 试探串口。先按 [硬件流程](hardware.md) 在无辐射台架确认固件、打开瞬态、DTR/RTS 和释放行为。若 PTT 未释放，立即停止操作并人工处理设备；软件无法保证进程强杀、USB 断电或硬件故障时释放。
