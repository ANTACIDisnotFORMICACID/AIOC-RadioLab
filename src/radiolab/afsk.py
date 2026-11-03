"""48000 sample/s、600 bit/s 的连续相位 AFSK；不访问硬件。"""
from dataclasses import dataclass

import numpy as np

from .packet import MAX_PAYLOAD, MIN_FRAME_SIZE, PREAMBLE, SYNC, PacketError, PacketFrame, decode_frame

SAMPLE_RATE = 48000
BAUD = 600
MARK_HZ = 1200
SPACE_HZ = 2200
AMPLITUDE = 0.25  # 峰值约 -12 dBFS，整个文件固定幅度
SAMPLES_PER_BIT = SAMPLE_RATE // BAUD
_PREFIX_BITS = np.unpackbits(np.frombuffer(PREAMBLE + SYNC, dtype=np.uint8))


@dataclass(frozen=True)
class DecodeResult:
    frame: PacketFrame
    start_sample: int
    sample_rate: int
    crc_ok: bool = True


class AFSKDecodeError(ValueError):
    """未找到经过 CRC 校验的帧。"""


def frame_to_bits(frame: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(frame, dtype=np.uint8))


def modulate_afsk(bits: np.ndarray, *, sample_rate: int = SAMPLE_RATE, baud: int = BAUD) -> np.ndarray:
    if sample_rate != SAMPLE_RATE or baud != BAUD:
        raise ValueError("v0.1 仅支持 48000 sample/s 和 600 bit/s")
    bits = np.asarray(bits)
    if bits.ndim != 1 or bits.size == 0 or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("bits 必须是一维非空 0/1 数组")
    freq = np.repeat(np.where(bits == 1, MARK_HZ, SPACE_HZ), SAMPLES_PER_BIT)
    phase = np.cumsum(np.r_[0.0, freq[:-1]]) * (2 * np.pi / sample_rate)
    return (AMPLITUDE * np.sin(phase)).astype(np.float32)


def modulate_frame(frame: bytes) -> np.ndarray:
    decode_frame(frame)  # 不生成无效帧音频
    return modulate_afsk(frame_to_bits(frame))


def _demod_bits(samples: np.ndarray, start: int) -> np.ndarray:
    count = (len(samples) - start) // SAMPLES_PER_BIT
    if count < MIN_FRAME_SIZE * 8:
        return np.empty(0, dtype=np.uint8)
    windows = samples[start:start + count * SAMPLES_PER_BIT].reshape(count, SAMPLES_PER_BIT)
    t = np.arange(SAMPLES_PER_BIT) / SAMPLE_RATE
    # 每个符号独立测量两频能量；发送端相位仍然连续。
    mark = np.abs(windows @ np.exp(-2j * np.pi * MARK_HZ * t)) ** 2
    space = np.abs(windows @ np.exp(-2j * np.pi * SPACE_HZ * t)) ** 2
    return (mark > space).astype(np.uint8)


def _valid_frames(bits: np.ndarray, sample_start: int):
    prefix_len = len(_PREFIX_BITS)
    if len(bits) < MIN_FRAME_SIZE * 8:
        return
    # 先用 sync 限定候选，再严格校验全部 32 字节前导码。
    sync = _PREFIX_BITS[-16:]
    hits = np.flatnonzero(np.convolve(bits.astype(np.int16), (2 * sync - 1)[::-1], mode="valid") == int(sync.sum()))
    for sync_at in hits:
        begin = int(sync_at) - 256
        if begin < 0 or not np.array_equal(bits[begin:begin + prefix_len], _PREFIX_BITS):
            continue
        header_end = begin + 38 * 8
        if header_end > len(bits):
            continue
        header = np.packbits(bits[begin:header_end]).tobytes()
        length = header[37]
        if length > MAX_PAYLOAD:
            continue
        end = begin + (MIN_FRAME_SIZE + length) * 8
        if end > len(bits):
            continue
        try:
            frame = decode_frame(np.packbits(bits[begin:end]).tobytes())
        except PacketError:
            continue
        yield DecodeResult(frame, sample_start + begin * SAMPLES_PER_BIT, SAMPLE_RATE)


def _find_frame(bits: np.ndarray, sample_start: int) -> DecodeResult | None:
    return next(_valid_frames(bits, sample_start), None)


def decode_afsk(samples: np.ndarray, *, sample_rate: int = SAMPLE_RATE, baud: int = BAUD) -> DecodeResult:
    if sample_rate != SAMPLE_RATE or baud != BAUD:
        raise AFSKDecodeError("v0.1 仅支持 48000 sample/s、600 bit/s；请先重采样")
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim != 1 or x.size == 0 or not np.all(np.isfinite(x)) or x.size > SAMPLE_RATE * 120:
        raise AFSKDecodeError("音频必须为有限的单声道数组，长度不超过 120 s")
    peak = float(np.max(np.abs(x)))
    if peak < 1e-5:
        raise AFSKDecodeError("音频为空或几乎静音")
    active = np.flatnonzero(np.abs(x) > max(1e-5, peak * 0.02))
    onset = max(0, int(active[0]) - 2)
    # 快路径：静音后紧接帧。完整搜索处理起点不明与采样偏移。
    candidates = [onset + d for d in range(8)]
    candidates += [onset + d for d in range(-SAMPLES_PER_BIT, 2 * SAMPLES_PER_BIT) if onset + d >= 0 and onset + d not in candidates]
    for start in candidates:
        result = _find_frame(_demod_bits(x, start), start)
        if result is not None:
            return result
    raise AFSKDecodeError("未找到前导码、同步字及 CRC 均有效的帧")


def scan_afsk(samples: np.ndarray) -> list[DecodeResult]:
    """在任意起点的 PCM 中查找全部 CRC 有效帧，用于分块监听。"""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim != 1 or x.size == 0 or x.size > SAMPLE_RATE * 3 or not np.all(np.isfinite(x)):
        raise AFSKDecodeError("扫描音频需为 3 s 内的非空有限 48 kHz 单声道 PCM")
    if np.max(np.abs(x)) < 1e-5:
        return []
    candidates = []
    for offset in range(SAMPLES_PER_BIT):
        candidates.extend(_valid_frames(_demod_bits(x, offset), offset))
    candidates.sort(key=lambda result: result.start_sample)
    unique = []
    for result in candidates:
        if not any(abs(result.start_sample - old.start_sample) < SAMPLES_PER_BIT and
                   result.frame == old.frame for old in unique):
            unique.append(result)
    return unique


class AFSKStreamScanner:
    """以最多 2 s 的滚动 PCM 扫描；返回新帧并抑制 10 s 内同序号同内容重发。"""

    def __init__(self) -> None:
        self._buffer = np.empty(0, dtype=np.float32)
        self._total_samples = 0
        self._last_scan = 0
        self._recent: dict[tuple[int, int, bytes], int] = {}

    def push(self, samples: np.ndarray) -> list[DecodeResult]:
        block = np.asarray(samples, dtype=np.float32)
        if block.ndim != 1 or not block.size or not np.all(np.isfinite(block)):
            raise AFSKDecodeError("监听输入必须为非空有限单声道 PCM 块")
        found = []
        for begin in range(0, block.size, SAMPLE_RATE // 2):
            part = block[begin:begin + SAMPLE_RATE // 2]
            self._total_samples += part.size
            self._buffer = np.concatenate((self._buffer, part))[-2 * SAMPLE_RATE:]
            if self._total_samples - self._last_scan < SAMPLE_RATE // 2:
                continue
            found.extend(self._scan_current())
        return found

    def finish(self) -> list[DecodeResult]:
        """输入结束时检查不足半秒的尾块。"""
        return self._scan_current() if self._total_samples > self._last_scan else []

    def _scan_current(self) -> list[DecodeResult]:
        self._last_scan = self._total_samples
        buffer_start = self._total_samples - self._buffer.size
        found = []
        for result in scan_afsk(self._buffer):
            absolute = buffer_start + result.start_sample
            key = (result.frame.kind, result.frame.sequence, result.frame.payload)
            last = self._recent.get(key)
            if last is not None and absolute - last < 10 * SAMPLE_RATE:
                continue
            self._recent[key] = absolute
            found.append(DecodeResult(result.frame, absolute, SAMPLE_RATE))
        self._recent = {key: sample for key, sample in self._recent.items()
                        if self._total_samples - sample < 10 * SAMPLE_RATE}
        return found
