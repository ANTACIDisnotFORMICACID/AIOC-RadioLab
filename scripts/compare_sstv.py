"""离线 SSTV 跨项目对照；第三方程序只在独立 Python 环境中运行。

用法：python scripts/compare_sstv.py --oracle-python runtime/sstv_compare_env/Scripts/python.exe
输出保存在 runtime 下；脚本不会访问声卡、串口或 PTT。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy.signal import hilbert
import soundfile as sf

from radiolab.audio import save_wav
from radiolab.sstv import decode_sstv_file, render_sstv_pcm
from radiolab.sstv_codec import MODES


RUST_MODES = {
    "robot36": "ROBOT_36", "robot72": "ROBOT_72",
    "martin1": "MARTIN_1", "martin2": "MARTIN_2",
    "scottie1": "SCOTTIE_1", "scottie2": "SCOTTIE_2",
}
PYSSTV_MODES = {
    "robot36": "Robot36", "martin1": "MartinM1", "martin2": "MartinM2",
    "scottie1": "ScottieS1", "scottie2": "ScottieS2",
}
OPEN_MODES = {
    "robot36": "robot_36", "martin1": "martin_m1", "martin2": "martin_m2",
    "scottie1": "scottie_s1", "scottie2": "scottie_s2",
}
# Dayton 论文给出的显示尺寸、VIS 与不含 VIS 头的近似扫描时长。
PAPER = {
    "robot36": (8, 320, 240, 36.0), "robot72": (12, 320, 240, 72.0),
    "martin1": (44, 320, 256, 114.3), "martin2": (40, 320, 256, 58.06),
    "scottie1": (60, 320, 256, 109.6), "scottie2": (56, 320, 256, 71.1),
}
PAPER_LINES = {
    "robot36": (150.0, 9.0, None), "robot72": (300.0, 9.0, None),
    "martin1": (446.446, 4.862, None), "martin2": (226.798, 4.862, None),
    "scottie1": (428.220, 9.0, 138.240), "scottie2": (277.692, 9.0, 88.064),
}
RUST_PROBE = r"""
import json, sys, sstv
from PIL import Image
operation, mode_name, source, destination = sys.argv[1:5]
mode = getattr(sstv.Mode, mode_name)
if operation == "encode":
    with Image.open(source) as opened:
        image = opened.convert("RGB")
        sstv.encode_to_wav_file(image, destination, mode, sample_rate=48000)
    print(json.dumps({"mode": str(mode), "output": destination}))
elif operation == "decode":
    images = list(sstv.decode_from_wav(source))
    if len(images) != 1:
        raise RuntimeError(f"expected one image, got {len(images)}")
    image = images[0]
    metadata = {"mode": str(image.info.get("sstv_mode")),
                "complete": image.info.get("sstv_complete"),
                "size": image.size}
    image.save(destination)
    print(json.dumps(metadata))
else:
    raise ValueError(operation)
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def external(python: Path, operation: str, mode: str, source: Path, output: Path) -> dict:
    result = subprocess.run(
        (str(python), "-I", "-c", RUST_PROBE, operation, RUST_MODES[mode],
         str(source), str(output)), capture_output=True, text=True, timeout=240,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"独立 sstv {operation} {mode} 失败：{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def pysstv_encode(python: Path, mode: str, source: Path, output: Path) -> None:
    # PySSTV 的 MartinM2 接收 160 像素宽输入；不缩放会截取 320 像素图片的左半边。
    result = subprocess.run(
        (str(python), "-I", "-m", "pysstv", "--mode", PYSSTV_MODES[mode],
         "--rate", "48000", "--bits", "16", "--resize", str(source), str(output)),
        capture_output=True, text=True, timeout=240, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"独立 PySSTV 编码 {mode} 失败：{result.stdout}\n{result.stderr}")


def open_sstv_cli(python: Path, operation: str, mode: str, source: Path, output: Path) -> dict:
    executable = python.parent / f"open-sstv-{operation}.exe"
    if not executable.is_file():
        raise ValueError(f"独立 Open-SSTV 工具不存在：{executable}")
    command = ((str(executable), str(source), "--mode", OPEN_MODES[mode], "-o", str(output))
               if operation == "encode" else (str(executable), str(source), "-o", str(output)))
    result = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    if result.returncode:
        raise RuntimeError(f"独立 Open-SSTV {operation} {mode} 失败：{result.stdout}\n{result.stderr}")
    if operation == "decode" and (f"mode={OPEN_MODES[mode]}" not in result.stdout or
                                  f"vis=0x{PAPER[mode][0]:02x}" not in result.stdout):
        raise ValueError(f"Open-SSTV 报告的模式或 VIS 不符：{result.stdout}")
    return {"tool_output": result.stdout.strip()}


def metrics(reference: Path, actual: Path) -> dict:
    with Image.open(reference) as expected_image, Image.open(actual) as actual_image:
        if actual_image.size != expected_image.size:
            raise ValueError(f"图像尺寸 {actual_image.size}，预期 {expected_image.size}")
        expected = np.asarray(expected_image.convert("RGB"), dtype=np.float32)
        received = np.asarray(actual_image.convert("RGB"), dtype=np.float32)
    delta = np.abs(received - expected)
    return {
        "mae": round(float(delta.mean()), 3),
        "max_row_mae": round(float(delta.mean((1, 2)).max()), 3),
        "rgb_mae": [round(float(value), 3) for value in delta.mean((0, 1))],
    }


def wav_info(path: Path) -> dict:
    info = sf.info(path)
    return {"duration_s": round(info.duration, 6), "sample_rate": info.samplerate,
            "channels": info.channels, "subtype": info.subtype, "sha256": sha256(path)}


def paper_audit(path: Path, mode: str) -> dict:
    """直接从 WAV 的短时 FFT 读取 VIS，独立核对论文时长与同步音。"""
    vis_expected, width, height, paper_body = PAPER[mode]
    spec = MODES[mode]
    if (spec.vis, spec.width, spec.height) != (vis_expected, width, height):
        raise ValueError(f"{mode} 内置模式表与 Dayton 文档不一致")
    line_ms, sync_ms, scottie_scan_ms = PAPER_LINES[mode]
    if abs(spec.line_ms - line_ms) > .001:
        raise ValueError(f"{mode} 内置行时长 {spec.line_ms}ms，与论文 {line_ms}ms 不同")
    info = wav_info(path)
    if info["sample_rate"] != 48000 or info["channels"] != 1 or info["subtype"] != "PCM_16":
        raise ValueError(f"{mode} WAV 格式错误：{info}")
    body = info["duration_s"] - .910 - .250 - (.009 if mode.startswith("scottie") else 0)
    if abs(body - paper_body) > .080:
        raise ValueError(f"{mode} 扫描时长 {body:.3f}s，论文约 {paper_body:.3f}s")
    head, rate = sf.read(path, frames=48000, dtype="float32")

    def peak(center_ms: float, width_ms: float = 20) -> float:
        left = round((center_ms - width_ms / 2) * rate / 1000)
        right = round((center_ms + width_ms / 2) * rate / 1000)
        segment = head[left:right]
        spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment)), n=16384))
        return float(np.fft.rfftfreq(16384, 1 / rate)[np.argmax(spectrum)])

    fixed = ((150, 1900, 20), (305, 1200, 6), (460, 1900, 20),
             (625, 1200, 20), (895, 1200, 20))
    for center, expected, width_ms in fixed:
        measured = peak(center, width_ms)
        if abs(measured - expected) > 70:
            raise ValueError(f"{mode} VIS {center}ms 音频 {measured:.1f}Hz，预期 {expected}Hz")
    bits = [int(abs(peak(655 + 30 * bit) - 1100) < 80) for bit in range(7)]
    parity = int(abs(peak(865) - 1100) < 80)
    vis = sum(value << bit for bit, value in enumerate(bits))
    if vis != vis_expected or (sum(bits) + parity) % 2:
        raise ValueError(f"{mode} WAV 的 VIS 或奇偶校验错误：0x{vis:02x}")
    sync_checks = []
    for row in (0, height // 2, height - 1):
        if scottie_scan_ms is None:
            center_ms = 910 + row * line_ms + sync_ms / 2
        else:
            # Scottie 的常规同步位于蓝扫描之后；首行之前另有 9ms 起始同步。
            center_ms = 910 + 9 + row * line_ms + 3 + 2 * scottie_scan_ms + sync_ms / 2
        left = round((center_ms - 1.7) * rate / 1000)
        segment, _ = sf.read(path, start=left, frames=round(3.4 * rate / 1000), dtype="float32")
        spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment)), n=16384))
        measured = float(np.fft.rfftfreq(16384, 1 / rate)[np.argmax(spectrum)])
        if abs(measured - 1200) > 75:
            raise ValueError(f"{mode} 第 {row} 行同步音 {measured:.1f}Hz，预期 1200Hz")
        sync_checks.append(round(measured, 1))
    return {"measured_vis": f"0x{vis:02X}", "body_s": round(body, 6),
            "paper_body_s": paper_body, "leader_hz": round(peak(150), 1),
            "line_sync_hz_first_middle_last": sync_checks}


def make_colorbars(path: Path) -> None:
    colors = ((0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0),
              (0, 0, 255), (0, 255, 255), (255, 0, 255), (255, 255, 0))
    chart = np.empty((256, 320, 3), dtype=np.uint8)
    for index, color in enumerate(colors):
        chart[:, index * 40:(index + 1) * 40] = color
    chart[64:67] = (255, 255, 255)
    chart[128:131] = (0, 0, 0)
    chart[192:195] = (255, 255, 255)
    Image.fromarray(chart, "RGB").save(path)


def robot_luma_black_white_hz(path: Path, mode: str) -> list[float]:
    """色条第一行黑/白 Y 音：比较 Dayton 限幅与常见全幅色彩缩放。"""
    scan_ms = 88 if mode == "robot36" else 138
    rate = sf.info(path).samplerate
    values = []
    for x in (20, 60):
        center_s = .922 + scan_ms / 1000 * (x + .5) / 320
        start = round((center_s - .003) * rate)
        samples, _ = sf.read(path, start=start, frames=round(.006 * rate), dtype="float64")
        analytic = hilbert(samples)
        frequency = np.angle(analytic[1:] * np.conjugate(analytic[:-1])) * rate / (2 * np.pi)
        values.append(round(float(np.median(frequency[50:-50])), 1))
    return values


def contact_sheet(folder: Path, rows: list[dict]) -> None:
    labels = ("Input", "RadioLab TX / Rust RX", "Rust TX / RadioLab RX", "PySSTV TX / RadioLab RX")
    canvas = Image.new("RGB", (4 * 330, len(rows) * 295 + 35), "#1b1b1b")
    draw = ImageDraw.Draw(canvas)
    for column, label in enumerate(labels):
        draw.text((column * 330 + 5, 8), label, fill="white")
    for row, item in enumerate(rows):
        top = row * 295 + 35
        for column, name in enumerate(("input.png", "native_rust.png", "rust_native.png", "pysstv_native.png")):
            path = folder / item["mode"] / name
            if path.is_file():
                with Image.open(path) as source:
                    canvas.paste(source.convert("RGB"), (column * 330 + 5, top + 24))
            draw.text((column * 330 + 5, top + 5), item["mode"], fill="white")
    canvas.save(folder / "contact_sheet.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-python", type=Path, required=True,
                        help="独立环境中安装 sstv、PySSTV（可选 Open-SSTV）的 Python")
    parser.add_argument("-o", "--output", type=Path, default=Path("runtime/sstv_comparison"))
    parser.add_argument("--modes", nargs="+", choices=tuple(MODES), default=list(MODES))
    parser.add_argument("--fixture", choices=("example", "colorbars"), default="example")
    parser.add_argument("--open-sstv", action="store_true", help="额外运行独立 Open-SSTV 对照；不支持 Robot72")
    args = parser.parse_args()
    oracle = args.oracle_python.resolve()
    if not oracle.is_file():
        parser.error(f"独立 Python 不存在：{oracle}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = (Path("examples/test_image.png") if args.fixture == "example"
              else output / "colorbars.png").resolve()
    if args.fixture == "colorbars":
        make_colorbars(source)
    if not source.is_file():
        parser.error(f"基准图片不存在：{source}")
    packages = ("sstv", "PySSTV", "open-sstv") if args.open_sstv else ("sstv", "PySSTV")
    probe = subprocess.run((str(oracle), "-I", "-c",
                            "import importlib.metadata as m,json,sys; print(json.dumps({x:m.version(x) for x in sys.argv[1:]}))",
                            *packages),
                           capture_output=True, text=True, timeout=30, check=True)
    manifest = {"fixture": args.fixture, "reference": str(source), "reference_sha256": sha256(source),
                "oracle_python": str(oracle), "oracle_versions": json.loads(probe.stdout),
                "modes": [], "failures": []}

    for mode in args.modes:
        spec = MODES[mode]
        folder = output / mode
        folder.mkdir(parents=True, exist_ok=True)
        reference = folder / "input.png"
        with Image.open(source) as image:
            image.convert("RGB").resize((spec.width, spec.height)).save(reference)
        row = {"mode": mode, "vis": f"0x{spec.vis:02X}",
               "paper_body_s": PAPER[mode][3],
               "input_sha256": sha256(reference), "routes": {}}
        manifest["modes"].append(row)

        def attempt(name: str, operation) -> None:
            try:
                row["routes"][name] = operation()
                print(f"{mode:9s} {name:22s} DONE {row['routes'][name].get('mae', '')}", flush=True)
            except Exception as exc:
                row["routes"][name] = {"error": str(exc)}
                manifest["failures"].append({"mode": mode, "route": name, "error": str(exc)})
                print(f"{mode:9s} {name:22s} FAIL {exc}", flush=True)
            (output / "results.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        native_wav = folder / "native.wav"
        def encode_native() -> dict:
            save_wav(native_wav, render_sstv_pcm(reference, mode))
            result = {**wav_info(native_wav), **paper_audit(native_wav, mode)}
            if args.fixture == "colorbars" and mode.startswith("robot"):
                result["luma_black_white_hz"] = robot_luma_black_white_hz(native_wav, mode)
            return result
        attempt("native_encode", encode_native)

        def decode_with_rust(wav: Path, picture: str) -> dict:
            image = folder / picture
            result = external(oracle, "decode", mode, wav, image)
            if result["mode"] != f"Mode.{RUST_MODES[mode]}" or result["complete"] is not True:
                raise ValueError(f"独立解码模式或完整性错误：{result}")
            return {**result, **metrics(reference, image), "image_sha256": sha256(image)}

        def decode_native(wav: Path, picture: str) -> dict:
            image = folder / picture
            report = image.with_suffix(".log")
            decode_sstv_file(wav, image, mode=mode, report_path=report)
            if f"vis=0x{spec.vis:02x}" not in report.read_text(encoding="utf-8"):
                raise ValueError(f"自写解码 VIS 错误：{report}")
            return {**metrics(reference, image), "image_sha256": sha256(image)}

        if native_wav.is_file():
            attempt("native_to_native_control", lambda: decode_native(native_wav, "native_native.png"))
            attempt("native_to_rust", lambda: decode_with_rust(native_wav, "native_rust.png"))
            if args.open_sstv and mode in OPEN_MODES:
                def native_to_open() -> dict:
                    image = folder / "native_open.png"
                    result = open_sstv_cli(oracle, "decode", mode, native_wav, image)
                    return {**result, **metrics(reference, image), "image_sha256": sha256(image)}
                attempt("native_to_open", native_to_open)

        rust_wav = folder / "rust.wav"
        def encode_with_rust() -> dict:
            result = external(oracle, "encode", mode, reference, rust_wav)
            return {**result, **wav_info(rust_wav)}
        attempt("rust_encode", encode_with_rust)

        if rust_wav.is_file():
            attempt("rust_to_native", lambda: decode_native(rust_wav, "rust_native.png"))
            attempt("rust_to_rust_control", lambda: decode_with_rust(rust_wav, "rust_rust.png"))

        if args.open_sstv and mode in OPEN_MODES:
            open_wav = folder / "open.wav"
            def encode_with_open() -> dict:
                result = open_sstv_cli(oracle, "encode", mode, reference, open_wav)
                result.update(wav_info(open_wav))
                if args.fixture == "colorbars" and mode == "robot36":
                    result["luma_black_white_hz"] = robot_luma_black_white_hz(open_wav, mode)
                return result
            attempt("open_encode", encode_with_open)
            if open_wav.is_file():
                attempt("open_to_native", lambda: decode_native(open_wav, "open_native.png"))
                attempt("open_to_rust", lambda: decode_with_rust(open_wav, "open_rust.png"))

        if mode in PYSSTV_MODES:
            pysstv_wav = folder / "pysstv.wav"
            attempt("pysstv_encode", lambda: (pysstv_encode(oracle, mode, reference, pysstv_wav),
                                              wav_info(pysstv_wav))[1])
            if pysstv_wav.is_file():
                attempt("pysstv_to_native", lambda: decode_native(pysstv_wav, "pysstv_native.png"))
                attempt("pysstv_to_rust", lambda: decode_with_rust(pysstv_wav, "pysstv_rust.png"))

    contact_sheet(output, manifest["modes"])
    print(f"结果：{output / 'results.json'}；失败 {len(manifest['failures'])}")
    return 1 if manifest["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
