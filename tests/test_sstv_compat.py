"""独立项目仅用于离线互验，不是 RadioLab 的运行依赖。"""

import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from radiolab.audio import save_wav
from radiolab.sstv import decode_sstv_file, render_sstv_pcm


REFERENCE = Path(__file__).resolve().parents[1] / "examples" / "test_image.png"
EXTERNAL_MODES = {
    "robot36": "robot_36", "martin1": "martin_m1", "martin2": "martin_m2",
    "scottie1": "scottie_s1", "scottie2": "scottie_s2",
}
RUST_MODES = {
    "robot36": "ROBOT_36", "robot72": "ROBOT_72", "martin1": "MARTIN_1",
    "martin2": "MARTIN_2", "scottie1": "SCOTTIE_1", "scottie2": "SCOTTIE_2",
}
VIS = {"robot36": 8, "robot72": 12, "martin1": 44,
       "martin2": 40, "scottie1": 60, "scottie2": 56}


def _mae(actual: Path, mode: str) -> tuple[float, float]:
    height = 240 if mode.startswith("robot") else 256
    with Image.open(REFERENCE) as source, Image.open(actual) as result:
        assert result.format == "PNG" and result.size == (320, height)
        expected = np.asarray(source.convert("RGB").resize((320, height)), dtype=np.float32)
        image = np.asarray(result.convert("RGB"), dtype=np.float32)
    delta = np.abs(expected - image)
    return float(delta.mean()), float(delta.mean((1, 2)).max())


@pytest.mark.parametrize("mode", EXTERNAL_MODES)
def test_native_encode_external_decode(tmp_path, mode):
    executable = os.environ.get("RADIOLAB_SSTV_DECODER")
    if not executable:
        pytest.skip("设置 RADIOLAB_SSTV_DECODER 为独立 Open-SSTV 解码器路径")
    wav = save_wav(tmp_path / "native.wav", render_sstv_pcm(REFERENCE, mode))
    image = tmp_path / "external.png"
    result = subprocess.run((executable, str(wav), "-o", str(image)), capture_output=True,
                            text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"mode={EXTERNAL_MODES[mode]}" in result.stdout
    assert f"vis=0x{VIS[mode]:02x}" in result.stdout
    mae, row = _mae(image, mode)
    assert mae < (12 if mode == "robot36" else 5) and row < 30


@pytest.mark.parametrize("mode", EXTERNAL_MODES)
def test_external_encode_native_decode(tmp_path, mode):
    executable = os.environ.get("RADIOLAB_SSTV_ENCODER")
    if not executable:
        pytest.skip("设置 RADIOLAB_SSTV_ENCODER 为独立 Open-SSTV 编码器路径")
    wav = tmp_path / "external.wav"
    result = subprocess.run((executable, str(REFERENCE), "--mode", EXTERNAL_MODES[mode],
                             "-o", str(wav)), capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    image = decode_sstv_file(wav, tmp_path / "native.png", mode=mode)
    mae, row = _mae(image, mode)
    assert mae < (12 if mode == "robot36" else 5) and row < 30


@pytest.mark.parametrize("mode", RUST_MODES)
def test_native_mode_with_independent_rust_decoder(tmp_path, mode):
    interpreter = os.environ.get("RADIOLAB_RUST_SSTV_PYTHON")
    if not interpreter:
        pytest.skip("设置 RADIOLAB_RUST_SSTV_PYTHON 为独立 sstv 0.2.0 环境的 Python 路径")
    wav = save_wav(tmp_path / "native.wav", render_sstv_pcm(REFERENCE, mode))
    image = tmp_path / "rust.png"
    probe = """
import json, sys, sstv
images = list(sstv.decode_from_wav(sys.argv[1]))
if len(images) != 1:
    raise RuntimeError(f"expected one image, got {len(images)}")
result = images[0]
print(json.dumps({"mode": str(result.info.get("sstv_mode")),
                  "complete": result.info.get("sstv_complete"), "size": result.size}))
result.save(sys.argv[2])
"""
    result = subprocess.run((interpreter, "-I", "-c", probe, str(wav), str(image)),
                            capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"complete": true' in result.stdout.lower()
    assert f'Mode.{RUST_MODES[mode]}' in result.stdout
    mae, row = _mae(image, mode)
    assert mae < (15 if mode.startswith("robot") else 5) and row < 35


@pytest.mark.parametrize("mode", RUST_MODES)
def test_independent_rust_encoder_native_decode(tmp_path, mode):
    interpreter = os.environ.get("RADIOLAB_RUST_SSTV_PYTHON")
    if not interpreter:
        pytest.skip("设置 RADIOLAB_RUST_SSTV_PYTHON 为独立 sstv 0.2.0 环境的 Python 路径")
    wav = tmp_path / "rust.wav"
    probe = """
import sys, sstv
from PIL import Image
mode = getattr(sstv.Mode, sys.argv[3])
with Image.open(sys.argv[1]) as source:
    image = source.convert('RGB').resize((mode.image_width, mode.image_height))
    sstv.encode_to_wav_file(image, sys.argv[2], mode, sample_rate=48000)
"""
    result = subprocess.run((interpreter, "-I", "-c", probe, str(REFERENCE), str(wav), RUST_MODES[mode]),
                            capture_output=True, text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    image = decode_sstv_file(wav, tmp_path / "native.png", mode=mode)
    mae, row = _mae(image, mode)
    assert mae < (12 if mode.startswith("robot") else 5) and row < 35
