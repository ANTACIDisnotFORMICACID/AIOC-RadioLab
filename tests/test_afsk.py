import numpy as np
import pytest
import soundfile as sf
from pathlib import Path
from scipy.signal import resample_poly

from radiolab.afsk import (AMPLITUDE, SAMPLE_RATE, SAMPLES_PER_BIT, AFSKDecodeError,
                           AFSKStreamScanner, decode_afsk, frame_to_bits, modulate_afsk,
                           modulate_frame, scan_afsk)
from radiolab.audio import load_wav, save_wav
from radiolab.packet import encode_frame, encode_text


def test_duration_frequency_phase():
    pcm = modulate_afsk(np.array([1, 0, 1], dtype=np.uint8))
    assert len(pcm) == 3 * SAMPLES_PER_BIT
    assert np.max(np.abs(pcm)) <= AMPLITUDE + 1e-6
    for bit, expected in [(0, 1200), (1, 2200)]:
        segment = pcm[bit * 80:(bit + 1) * 80]
        freq = np.fft.rfftfreq(80, 1 / SAMPLE_RATE)[np.argmax(np.abs(np.fft.rfft(segment)))]
        assert abs(freq - expected) <= 200
    # 边界处相位连续：样本差不会产生超过单频最大斜率的跳变。
    assert abs(float(pcm[80] - pcm[79])) < 0.08


def test_wav_roundtrip(tmp_path):
    path = save_wav(tmp_path / "hello.wav", modulate_frame(encode_text("HELLO WORLD")))
    assert decode_afsk(load_wav(path)).frame.text == "HELLO WORLD"


def test_shipped_hello_wav_is_a_decodable_pcm16_reference():
    sample = Path(__file__).resolve().parents[1] / "examples" / "hello_afsk.wav"
    info = sf.info(sample)
    assert info.samplerate == SAMPLE_RATE
    assert info.channels == 1
    assert info.subtype == "PCM_16"
    assert info.frames == 32640
    result = decode_afsk(load_wav(sample))
    assert (result.frame.text, result.frame.sequence, result.crc_ok) == ("HELLO WORLD", 0, True)


def test_44100_stereo_wav_resamples_for_decode(tmp_path):
    pcm = modulate_frame(encode_text("44100 HZ"))
    shifted = np.r_[np.zeros(37, dtype=np.float32), pcm, np.zeros(220)]
    at_44100 = resample_poly(shifted, 147, 160)
    path = tmp_path / "stereo.wav"
    sf.write(path, np.column_stack((at_44100, at_44100)), 44100, subtype="PCM_16")
    assert decode_afsk(load_wav(path)).frame.text == "44100 HZ"


def test_windows_extensible_wav_decodes(tmp_path):
    path = tmp_path / "windows.wav"
    sf.write(path, modulate_frame(encode_text("WAVEX")), 48000, format="WAVEX", subtype="PCM_16")
    assert decode_afsk(load_wav(path)).frame.text == "WAVEX"


def test_random_1000_with_non_aligned_silence():
    rng = np.random.default_rng(20261028)
    for index in range(1000):
        data = rng.bytes(int(rng.integers(0, 33)))
        frame = encode_frame(data, index % 256, kind=2)
        prefix = np.zeros(int(rng.integers(1, 317)), dtype=np.float32)
        pcm = np.r_[prefix, modulate_frame(frame)]
        decoded = decode_afsk(pcm)
        assert decoded.frame.payload == data
        assert decoded.frame.sequence == index % 256


def test_awgn_regression():
    rng = np.random.default_rng(42)
    pcm = modulate_frame(encode_text("NOISE"))
    signal_power = np.mean(pcm * pcm)
    noisy = np.r_[np.zeros(43), pcm + rng.normal(0, np.sqrt(signal_power / 100), len(pcm))]
    assert decode_afsk(noisy).frame.text == "NOISE"


def test_stream_scanner_recovers_two_frames_across_chunks_and_deduplicates():
    rng = np.random.default_rng(20261028)
    first = modulate_frame(encode_text("FIRST", 7))
    second = modulate_frame(encode_text("SECOND", 8))
    recording = np.r_[rng.normal(0, 0.002, 5000), np.zeros(37), first,
                      np.zeros(12000), second, np.zeros(3000)].astype(np.float32)
    direct = scan_afsk(recording)
    assert [(row.frame.text, row.frame.sequence) for row in direct] == [("FIRST", 7), ("SECOND", 8)]
    scanner = AFSKStreamScanner()
    found = []
    for begin in range(0, recording.size, 4093):
        found.extend(scanner.push(recording[begin:begin + 4093]))
    found.extend(scanner.finish())
    assert [(row.frame.text, row.frame.sequence) for row in found] == [("FIRST", 7), ("SECOND", 8)]
    assert found[0].start_sample < found[1].start_sample
    repeated = np.r_[np.zeros(1000), first, np.zeros(24000)].astype(np.float32)
    assert scanner.push(repeated) == []


def test_stream_scanner_large_input_and_bad_blocks():
    scanner = AFSKStreamScanner()
    pcm = np.r_[np.zeros(3 * SAMPLE_RATE), modulate_frame(encode_text("LATE", 12)),
                np.zeros(SAMPLE_RATE)].astype(np.float32)
    assert [row.frame.text for row in scanner.push(pcm)] == ["LATE"]
    with pytest.raises(AFSKDecodeError):
        scanner.push(np.array([np.nan], dtype=np.float32))


def test_corrupt_audio_not_success():
    pcm = modulate_frame(encode_text("HELLO"))
    # 删除一整符号，使后续字节错位。
    bad = np.r_[pcm[:40 * 80], pcm[41 * 80:]]
    with pytest.raises(ValueError):
        decode_afsk(bad)


@pytest.mark.parametrize("samples", [np.array([], dtype=np.float32), np.array([np.nan]), np.zeros(1000)])
def test_bad_audio_rejected(samples):
    with pytest.raises(AFSKDecodeError):
        decode_afsk(samples)
