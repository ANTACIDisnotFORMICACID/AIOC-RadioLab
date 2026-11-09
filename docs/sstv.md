# SSTV 离线使用与独立互验

RadioLab 的 SSTV 编码和解码由[自写算法](sstv_backend.md)完成，运行时不需要 PySSTV 或 Open-SSTV。安装 `.[sstv]` 只增加图片处理依赖 Pillow。旧外部工具路径设置不再读取，CLI/GUI 不再提供后端选择。

```powershell
radiolab sstv encode examples/test_image.png -o runtime/sstv.wav
radiolab sstv decode runtime/sstv.wav -o runtime/decoded.png
```

这两条命令运行的是 Robot36，输出 WAV 为 48 kHz、单声道、16-bit PCM；PNG 为 320×240。其他五种模式可通过 `radiolab.sstv.render_sstv_pcm(image_path, mode)` 与 `decode_sstv_file(wav_path, image_path, mode=mode)` 调用；模式名见[接口表](sstv_backend.md)。

独立软件只用于**互验**：在另一环境安装 [Rust `sstv` Python 包](https://github.com/unexcellent/sstv-py)、[Open-SSTV](https://github.com/bucknova/Open-SSTV) 与 PySSTV，并按 [六模式实验记录](sstv_interop_20261030.md)运行脚本。六种模式均已与 Rust `sstv` 双向离线互验；除 Robot72 外的五种模式也与 Open-SSTV 双向互验。Robot36/72 的颜色缩放与独立软件存在差异，不能把可解码等同于像素完全一致或真实射频互通。

当前自写编码器**禁止真实发射**；AIOC/PTT 台架、双手台 RF、MMSSTV/Android 图像兼容性和受噪声实录音频仍待验证。
