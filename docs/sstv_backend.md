# SSTV 自写算法与接口

时序依据 [JL Barber 的 Dayton SSTV 模式规范提案](https://www.classicsstv.com/downloads/daytonpaper.pdf)。[算法文件](../src/radiolab/sstv_codec.py)按三层组织：共享 DSP（相位连续音频、频率估计）、模式协议（VIS 与扫描时序）、图像重建（RGB/亮度色差）。只使用 NumPy、SciPy、Pillow 和项目已有的 WAV 读写；运行时不导入或启动其他 SSTV 项目。

| 模式 | VIS | 输出尺寸 | 当前状态 |
|---|---:|---:|---|
| Robot36 | `0x08` | 320×240 | 自写编解码；已与 Rust `sstv` 和 Open-SSTV 双向离线互验，颜色缩放存在差异 |
| Robot72 | `0x0C` | 320×240 | 自写编解码；已与 Rust `sstv` 双向离线互验，颜色缩放存在差异 |
| Martin1 / Martin2 | `0x2C` / `0x28` | 320×256 | 自写编解码；均已与 Rust `sstv` 和 Open-SSTV 双向离线互验 |
| Scottie1 / Scottie2 | `0x3C` / `0x38` | 320×256 | 自写编解码；均已与 Rust `sstv` 和 Open-SSTV 双向离线互验 |

逐模式 VIS、扫描时长、首/中/末行同步、双向 MAE 与原始 WAV 见 [六模式离线互验记录](sstv_interop_20261030.md)。跨软件离线通过不代表模拟音频或 RF 链路已验收。

接口层为 [sstv.py](../src/radiolab/sstv.py) 与 [sstv_rx.py](../src/radiolab/sstv_rx.py)：

- `render_sstv_pcm(image_path, mode)` 返回 48 kHz 单声道浮点 PCM；`render_robot36_pcm(image_path)` 是 Robot36 入口。
- `decode_sstv_file(wav_path, image_path, mode=...)` 校验 VIS 与指定模式，写出 PNG 和本次日志；不指定模式时可自动识别六种模式。
- `SSTVReceiveService(mode=...)` 负责隔离本次输出、校验图片并记录历史，默认使用 Robot36；失败不得把旧图片或旧日志当作本次成功结果。

运行路径只使用本项目算法；独立软件仅用于离线互验。CLI/GUI 已移除旧后端选择和外部程序路径入口，旧 SQLite 设置不会被读取。**真实 SSTV 发射仍在编码入口显式锁定**，直到独立接收端、硬件台架和授权 RF 验收分别完成。
