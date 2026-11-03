"""自写 SSTV 模式、边界与接口服务的离线回归。"""

from pathlib import Path

import numpy as np
import pytest

from radiolab import sstv
from radiolab.audio import save_wav
from radiolab.history import HistoryService
from radiolab.sstv_codec import MODES, decode_pcm, encode_pixels
from radiolab.sstv_rx import SSTVReceiveService


def pattern(mode: str) -> np.ndarray:
    spec = MODES[mode]
    image = np.empty((spec.height, spec.width, 3), dtype=np.uint8)
    image[:, :, 0] = np.linspace(10, 240, spec.width, dtype=np.uint8)
    image[:, :, 1] = np.arange(spec.height, dtype=np.uint8)[:, None]
    image[:, :, 2] = 120
    return image


@pytest.mark.parametrize("mode,vis", [("robot36", 8), ("robot72", 12),
                                      ("martin1", 44), ("martin2", 40),
                                      ("scottie1", 60), ("scottie2", 56)])
def test_six_modes_vis_timing_and_image_reconstruction(mode, vis):
    image = pattern(mode)
    pcm = encode_pixels(image, mode)
    spec = MODES[mode]
    expected_s = .910 + spec.height * spec.line_ms / 1000 + .250
    if mode.startswith("scottie"):
        expected_s += .009
    assert pcm.dtype == np.float32 and np.max(np.abs(pcm)) <= .251
    assert abs(len(pcm) / 48000 - expected_s) < .001
    result = decode_pcm(pcm, expected_mode=mode)
    assert result.mode == mode and result.vis == vis and result.rgb.shape == image.shape
    error = np.abs(result.rgb.astype(np.int16) - image.astype(np.int16))
    assert float(error.mean()) < 4 and float(error.mean((1, 2)).max()) < 6


def test_robot36_random_leading_silence_and_awgn():
    image = pattern("robot36")
    pcm = encode_pixels(image)
    rng = np.random.default_rng(20261030)
    lead = np.zeros(48000 + 37, dtype=np.float32)
    noisy = np.concatenate((lead, pcm))
    noisy += rng.normal(0, .005, noisy.size).astype(np.float32)
    result = decode_pcm(noisy)
    assert result.mode == "robot36" and abs(result.leader_start_s - len(lead) / 48000) < .003
    error = np.abs(result.rgb.astype(np.int16) - image.astype(np.int16))
    assert float(error.mean()) < 15


def test_vis_mismatch_truncation_and_invalid_pcm():
    pcm = encode_pixels(pattern("robot36"))
    with pytest.raises(ValueError, match="VIS"):
        decode_pcm(pcm, expected_mode="martin2")
    with pytest.raises(ValueError, match="截断"):
        decode_pcm(pcm[:-48000])
    with pytest.raises(ValueError, match="有限"):
        decode_pcm(np.array([np.nan], dtype=np.float32))
    with pytest.raises(ValueError, match="不支持"):
        encode_pixels(pattern("robot36"), "unknown")


def test_vis_parity_corruption_is_rejected():
    pcm = encode_pixels(pattern("robot36"))
    start, end = round(.850 * 48000), round(.880 * 48000)
    # Robot36 的奇偶位原为 1100 Hz，故意换成 1300 Hz。
    pcm[start:end] = .25 * np.sin(2 * np.pi * 1300 * np.arange(end - start) / 48000)
    with pytest.raises(ValueError, match="VIS|奇偶校验"):
        decode_pcm(pcm)


def test_native_encoder_rf_gate(tmp_path):
    from PIL import Image
    image = tmp_path / "image.png"
    Image.new("RGB", (320, 240), "blue").save(image)
    assert sstv.render_robot36_pcm(image).ndim == 1
    with pytest.raises(ValueError, match="不能真实发射"):
        sstv.render_robot36_pcm(image, on_air=True)


def test_native_receive_history_and_rejects_stale_paths(tmp_path):
    from PIL import Image
    image = tmp_path / "image.png"
    Image.fromarray(pattern("robot36")).save(image)
    wav = save_wav(tmp_path / "input.wav", sstv.render_robot36_pcm(image))
    history = HistoryService(tmp_path / "history.jsonl")
    output = tmp_path / "decoded.png"
    result = SSTVReceiveService(history=history).decode_file(wav, output)
    assert result.decoder_name == "RadioLab 自写 SSTV"
    assert result.decoder_version == "0.1.0"
    assert result.report_path.is_file() and "vis=0x08" in result.report_path.read_text(encoding="utf-8")
    assert history.list_recent()[0].success and history.list_recent()[0].image_exists
    assert Image.open(output).size == (320, 240)
    with pytest.raises(ValueError, match="不同路径"):
        SSTVReceiveService(history=history).decode_file(wav, wav)


def test_martin2_receive_service_uses_mode_dimensions(tmp_path):
    wav = save_wav(tmp_path / "martin2.wav", encode_pixels(pattern("martin2"), "martin2"))
    history = HistoryService(tmp_path / "history.jsonl")
    output = tmp_path / "martin2.png"
    result = SSTVReceiveService(history=history, mode="martin2").decode_file(wav, output)
    from PIL import Image
    assert result.image_path == output and Image.open(output).size == (320, 256)
    assert history.list_recent()[0].success


def test_direct_decode_failure_keeps_old_image_and_new_report(tmp_path):
    from PIL import Image
    wav = tmp_path / "input.wav"
    save_wav(wav, np.zeros(48000, dtype=np.float32))
    image = tmp_path / "existing.png"
    Image.new("RGB", (320, 240), "green").save(image)
    original = image.read_bytes()
    report = tmp_path / "report.log"
    report.write_text("stale", encoding="utf-8")
    with pytest.raises(ValueError, match="VIS"):
        sstv.decode_sstv_file(wav, image, report_path=report)
    assert image.read_bytes() == original
    assert "error=" in report.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="不同路径"):
        sstv.decode_sstv_file(wav, wav)


def test_failed_decode_cannot_reuse_stale_image_or_log(tmp_path, monkeypatch):
    from PIL import Image
    wav = tmp_path / "input.wav"
    wav.write_bytes(b"injected decoder input")
    output = tmp_path / "output.png"
    Image.new("RGB", (320, 240), "blue").save(output)
    old_image = output.read_bytes()
    report = output.with_suffix(".decode.log")
    report.write_text("previous run", encoding="utf-8")
    history = HistoryService(tmp_path / "history.jsonl")

    def failed_decode(wav_path, image_path, *, mode, report_path):
        raise ValueError("解码失败")

    monkeypatch.setattr(sstv, "decode_sstv_file", failed_decode)
    with pytest.raises(ValueError, match="解码失败"):
        SSTVReceiveService(history=history).decode_file(wav, output)
    assert output.read_bytes() == old_image and report.read_text(encoding="utf-8") == "previous run"
    assert not history.list_recent()[0].success and not history.list_recent()[0].report_exists

    def partial_decode(wav_path, image_path, *, mode, report_path):
        image_path.write_bytes(b"invalid PNG")
        report_path.write_text("new failure", encoding="utf-8")
        return image_path

    monkeypatch.setattr(sstv, "decode_sstv_file", partial_decode)
    with pytest.raises(ValueError, match="无法核验"):
        SSTVReceiveService(history=history).decode_file(wav, output)
    assert output.read_bytes() == old_image and report.read_text(encoding="utf-8") == "new failure"
    assert not history.list_recent()[0].success and history.list_recent()[0].report_exists
