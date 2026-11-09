# SSTV 六模式离线互验（2026-10-30）

## 范围与方法

依据 [Dayton SSTV 模式规范提案](https://www.classicsstv.com/downloads/daytonpaper.pdf)核对 VIS、图像尺寸、扫描时长及帧首/中/末行同步；分别用渐变文字图片和高对比色条做双向跨项目 WAV 编解码。每张图片的误差是相对于同尺寸输入图的 RGB 平均绝对误差（MAE，0–255）；它是画质指标，**不是**规范规定的合格线。

第三方工具只在 `runtime/sstv_compare_env` 独立环境运行，不是 RadioLab 运行依赖，也没有复制其源码：[`sstv` 0.2.0](https://github.com/unexcellent/sstv-py)（Rust 核心，MIT，六模式编码/解码）、[Open-SSTV 0.6.17](https://github.com/bucknova/Open-SSTV/tree/v0.6.17)（GPL-3.0-or-later，除 Robot72 外五模式编码/解码）、[PySSTV 0.5.7](https://github.com/dnet/pysstv)（MIT，除 Robot72 外五模式编码）。Open-SSTV v0.6.17 安装来自 `bc9cf25ad4ee351f26184552c3737a044160c8ae`。PySSTV 的 MartinM2 输入宽度是 160 像素，实验调用 `--resize`；不缩放就输入 320 像素会只发送左半图，曾导致两款解码器都测得约 39/255 MAE。这是对照输入设置错误，不计为编解码缺陷。

## 协议核对

本项目生成的六份 WAV 均为 48 kHz、单声道 PCM16。以下扫描时长不含 0.910 s VIS 头和 0.250 s 尾静音。短时 FFT 分别检查 VIS 1900/1200/1100/1300 Hz、偶校验，以及首行、中间行、末行的 1200 Hz 同步音；所测同步频率均在 **1198.2–1201.2 Hz**。独立 `sstv` 解码器对六模式均自动识别正确模式、正确尺寸并报告完整图像。

| 模式 | VIS | 实测扫描时长 s | Dayton 约值 s | 自写→Rust MAE | 自写→Open-SSTV MAE | Rust→自写 MAE | Open-SSTV→自写 MAE | PySSTV→自写 MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Robot36 | `0x08` | 36.000 | 36.0 | 10.711 | 10.830 | 7.623 | 7.616 | 7.605 |
| Robot72 | `0x0C` | 72.000 | 72.0 | 10.523 | — | 7.091 | — | — |
| Martin1 | `0x2C` | 114.290 | 114.3 | 0.603 | 0.477 | 0.520 | 0.431 | 0.431 |
| Martin2 | `0x28` | 58.060 | 58.06 | 1.057 | 1.024 | 1.080 | 0.940 | 1.118 |
| Scottie1 | `0x3C` | 109.624 | 109.6 | 0.642 | 0.501 | 0.548 | 0.463 | 2.857 |
| Scottie2 | `0x38` | 71.089 | 71.1 | 0.921 | 0.848 | 0.901 | 0.798 | 4.310 |

上表使用 `examples/test_image.png`。高对比色条的“自写编码→Rust / Open-SSTV 解码”MAE：Robot36 **20.684 / 20.632**，Robot72 **18.770 / 不支持**；Martin1 **1.507 / 1.737**，Martin2 **3.008 / 3.680**，Scottie1 **1.655 / 1.888**，Scottie2 **2.582 / 3.156**。自写 Robot36/72 的同程序色条闭环为 **6.048 / 3.202**；Robot 色条的跨项目颜色误差明显高于 RGB 模式。两种图均保留了模式识别、颜色条次序与行同步。

## Robot 颜色差异与结论

Robot 模式按 Dayton 附录 B 的 BT.601 限幅亮度公式编码；本项目 WAV 的黑/白条 Y 音实测约 **1550/2237 Hz**。Open-SSTV 同一色条约 **1500/2300 Hz**。因此独立解码器能恢复整张图，但两种亮度/色差缩放使像素值不一致。Dayton 附录 B 也指出不同实现采用的缩放不完全相同。实测频率与 SHA-256 另存于 `runtime/sstv_comparison_luma_20261030/results.json`。此结果支持**VIS、时序、行同步和图像结构互通**，不能据此声称 Robot 模式的颜色与所有接收端完全一致。

六模式协议与至少一个独立编解码器双向互通已完成；Open-SSTV 不支持 Robot72，故该模式只用 Rust `sstv` 双向核对。尚未验证 MMSSTV/Android、模拟音频链路、AIOC 台架或真实 RF；本项目 SSTV 真正发射继续锁定。今后若需要跨接收端的精确颜色，可增加明确命名的 Robot 色彩缩放配置，并分别保留 Dayton 与常见全幅缩放的回归。

## 复现与原始证据

```powershell
python -m venv runtime/sstv_compare_env
.\runtime\sstv_compare_env\Scripts\python.exe -m pip install sstv==0.2.0 'Pillow<12' PySSTV==0.5.7
.\runtime\sstv_compare_env\Scripts\python.exe -m pip install 'git+https://github.com/bucknova/Open-SSTV.git@v0.6.17'
.\.venv\Scripts\python.exe scripts\compare_sstv.py --oracle-python runtime\sstv_compare_env\Scripts\python.exe -o runtime\sstv_comparison_20261030 --open-sstv
.\.venv\Scripts\python.exe scripts\compare_sstv.py --oracle-python runtime\sstv_compare_env\Scripts\python.exe -o runtime\sstv_comparison_colorbars_20261030 --fixture colorbars --open-sstv
```

逐模式原始 WAV、PNG、日志和 SHA-256 在 `runtime/sstv_comparison_20261030/results.json` 与 `runtime/sstv_comparison_colorbars_20261030/results.json`；两目录各有 `contact_sheet.png`。这些文件在 `.gitignore` 的 `runtime/` 下，需保留该目录才能复查；实验脚本可重新生成。`RADIOLAB_RUST_SSTV_PYTHON`、`RADIOLAB_SSTV_ENCODER`、`RADIOLAB_SSTV_DECODER` 指向上述独立环境时，`pytest -q tests/test_sstv_compat.py` 实跑 **22 通过**。
