"""内置 SSTV 的文件编解码入口与输入输出校验。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from .afsk import SAMPLE_RATE
from .audio import load_wav, save_wav
from .sstv_codec import decode_pcm, encode_pixels, get_mode


def _image_pixels(image_path: Path, mode: str) -> np.ndarray:
    spec = get_mode(mode)
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("SSTV 图片处理需要 Pillow；运行 pip install -e '.[sstv]'") from exc
    try:
        with Image.open(image_path) as source:
            if source.width * source.height > 20_000_000:
                raise ValueError("图片超过 2000 万像素，请先缩小")
            source.load()
            image = source.convert("RGB").resize((spec.width, spec.height))
            return np.asarray(image, dtype=np.uint8)
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 SSTV 图片 {image_path}：{exc}") from exc


def render_sstv_pcm(image_path: str | Path, mode: str = "robot36", *,
                    on_air: bool = False) -> np.ndarray:
    """通用编码入口；校验 48 kHz、峰值、有限值和模式时长。"""
    spec = get_mode(mode)
    if on_air:
        raise ValueError("自写 SSTV 尚未完成实机验收，不能真实发射")
    pcm = np.asarray(encode_pixels(_image_pixels(Path(image_path), spec.name), spec.name))
    if (pcm.ndim != 1 or pcm.size == 0 or pcm.size > SAMPLE_RATE * 120 or
            not np.issubdtype(pcm.dtype, np.floating) or not np.all(np.isfinite(pcm)) or
            np.max(np.abs(pcm)) > 1):
        raise ValueError("SSTV 输出需为不超过 120 s 的有限、单声道浮点 PCM，峰值不超过 1")
    return pcm.astype(np.float32, copy=False)


def render_robot36_pcm(image_path: str | Path, *, on_air: bool = False) -> np.ndarray:
    return render_sstv_pcm(image_path, "robot36", on_air=on_air)


def validate_sstv_image(image_path: str | Path, mode: str = "robot36") -> Path:
    path = Path(image_path)
    spec = get_mode(mode)
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG" or image.size != (spec.width, spec.height):
                raise ValueError(f"{spec.name} 解码结果须为 {spec.width}×{spec.height} PNG：{path}")
    except (OSError, ImportError) as exc:
        raise ValueError(f"无法核验 SSTV 图片 {path}：{exc}") from exc
    return path


def validate_robot36_image(image_path: str | Path) -> Path:
    return validate_sstv_image(image_path, "robot36")


def decode_sstv_file(wav_path: str | Path, image_path: str | Path, *,
                     mode: str | None = None, report_path: str | Path | None = None) -> Path:
    """从 WAV 自动识别 VIS；指定 mode 时再核对模式，成功后原子替换 PNG。"""
    source = Path(wav_path)
    output = Path(image_path)
    report = Path(report_path) if report_path is not None else None
    paths = (source, output, report) if report is not None else (source, output)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("SSTV 输入 WAV、输出图片和解码日志必须使用不同路径")
    if output.suffix.lower() != ".png":
        raise ValueError(f"SSTV 解码输出须为 .png 文件：{output}")
    if not source.is_file():
        raise ValueError(f"SSTV 输入 WAV 不存在：{source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        samples = load_wav(source)
        decoded = decode_pcm(samples, SAMPLE_RATE, expected_mode=mode)
        try:
            from PIL import Image
        except ImportError as exc:
            raise ValueError("SSTV 图片处理需要 Pillow；运行 pip install -e '.[sstv]'") from exc
        with TemporaryDirectory(dir=output.parent) as directory:
            candidate = Path(directory) / output.name
            Image.fromarray(decoded.rgb, "RGB").save(candidate, format="PNG")
            validate_sstv_image(candidate, decoded.mode)
            if report is not None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(f"decoder=RadioLab native 0.1.0\nmode={decoded.mode}\n"
                                  f"vis=0x{decoded.vis:02x}\nleader_start_s={decoded.leader_start_s:.6f}\n"
                                  f"size={decoded.rgb.shape[1]}x{decoded.rgb.shape[0]}\n",
                                  encoding="utf-8")
            candidate.replace(output)
        return output
    except (ValueError, OSError, RuntimeError) as exc:
        if report is not None:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(f"decoder=RadioLab native 0.1.0\nerror={exc}\n", encoding="utf-8")
        raise


def robot36_pcm(image_path: str | Path) -> np.ndarray:
    """旧 API 的自写实现。"""
    return render_sstv_pcm(image_path, "robot36")


def encode_robot36(image_path: str | Path, wav_path: str | Path) -> tuple[Path, float]:
    pcm = robot36_pcm(image_path)
    return save_wav(wav_path, pcm), len(pcm) / SAMPLE_RATE


