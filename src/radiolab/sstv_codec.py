"""自写 SSTV 算法：共享 DSP、模式协议、图像重建。

时序依据 JL Barber, Proposal for SSTV Mode Specifications (Dayton, 2000)。
只处理内存中的 PCM/像素，不访问声卡、串口，也不调用其他 SSTV 项目。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi

import numpy as np
from scipy.ndimage import binary_closing, uniform_filter1d
from scipy.signal import hilbert, resample_poly


SAMPLE_RATE = 48_000
DECODE_RATE = 24_000
BLACK_HZ = 1_500.0
WHITE_HZ = 2_300.0
AMPLITUDE = 0.25


@dataclass(frozen=True)
class SSTVMode:
    name: str
    vis: int
    width: int
    height: int
    family: str
    scan_ms: float
    sync_ms: float
    porch_ms: float

    @property
    def line_ms(self) -> float:
        if self.family == "robot36":
            return 9 + 3 + 88 + 4.5 + 1.5 + 44
        if self.family == "robot72":
            return 9 + 3 + 138 + 4.5 + 1.5 + 69 + 4.5 + 1.5 + 69
        if self.family == "martin":
            return self.sync_ms + 4 * self.porch_ms + 3 * self.scan_ms
        return 9 + 3 * 1.5 + 3 * self.scan_ms


# 协议层：VIS 和时序均按论文表格；320×256 模式包含其所说的 16 行头区域。
MODES = {
    "robot36": SSTVMode("robot36", 8, 320, 240, "robot36", 88, 9, 3),
    "robot72": SSTVMode("robot72", 12, 320, 240, "robot72", 138, 9, 3),
    "martin1": SSTVMode("martin1", 44, 320, 256, "martin", 146.432, 4.862, 0.572),
    "martin2": SSTVMode("martin2", 40, 320, 256, "martin", 73.216, 4.862, 0.572),
    "scottie1": SSTVMode("scottie1", 60, 320, 256, "scottie", 138.240, 9, 1.5),
    "scottie2": SSTVMode("scottie2", 56, 320, 256, "scottie", 88.064, 9, 1.5),
}
VIS_MODES = {mode.vis: mode for mode in MODES.values()}


def get_mode(name: str) -> SSTVMode:
    try:
        return MODES[name.lower().replace("-", "").replace("_", "")]
    except (KeyError, AttributeError) as exc:
        raise ValueError(f"不支持的 SSTV 模式：{name}；可用：{', '.join(MODES)}") from exc


def _value_hz(values: np.ndarray) -> np.ndarray:
    return BLACK_HZ + np.clip(values, 0, 255) * ((WHITE_HZ - BLACK_HZ) / 255.0)


def _hz_value(freq: np.ndarray) -> np.ndarray:
    return np.clip((freq - BLACK_HZ) * (255.0 / (WHITE_HZ - BLACK_HZ)), 0, 255)


# 共享 DSP 层：全程维护相位和累计样本位置，短脉冲不会逐段舍入漂移。
class _ToneBuilder:
    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self.position = 0.0
        self.phase = 0.0
        self.parts: list[np.ndarray] = []

    def _append(self, duration_ms: float, frequency: float | np.ndarray) -> None:
        end = self.position + duration_ms * self.sample_rate / 1000.0
        count = round(end) - round(self.position)
        self.position = end
        if count <= 0:
            raise ValueError("SSTV 时序短于一个采样点")
        if np.isscalar(frequency):
            hz = np.full(count, frequency, dtype=np.float64)
        else:
            pixels = np.asarray(frequency, dtype=np.float64)
            indices = np.minimum((np.arange(count) * pixels.size // count), pixels.size - 1)
            hz = _value_hz(pixels[indices])
        phase = self.phase + np.cumsum(hz, dtype=np.float64) * (2 * pi / self.sample_rate)
        self.parts.append((AMPLITUDE * np.sin(phase)).astype(np.float32))
        self.phase = float(phase[-1] % (2 * pi))

    def tone(self, duration_ms: float, hz: float) -> None:
        self._append(duration_ms, hz)

    def scan(self, duration_ms: float, values: np.ndarray) -> None:
        self._append(duration_ms, values)

    def pcm(self) -> np.ndarray:
        return np.concatenate(self.parts)


def _emit_vis(builder: _ToneBuilder, vis: int) -> None:
    builder.tone(300, 1900)
    builder.tone(10, 1200)
    builder.tone(300, 1900)
    builder.tone(30, 1200)
    for bit in range(7):
        builder.tone(30, 1100 if (vis >> bit) & 1 else 1300)
    builder.tone(30, 1100 if vis.bit_count() % 2 else 1300)
    builder.tone(30, 1200)


def _rgb_to_yuv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # 论文附录 B 的 BT.601 系数，输入为非线性 8 bit RGB。
    red, green, blue = (rgb[:, :, i].astype(np.float32) for i in range(3))
    y = 16 + (65.738 * red + 129.057 * green + 25.064 * blue) / 256
    ry = 128 + (112.439 * red - 94.154 * green - 18.285 * blue) / 256
    by = 128 + (-37.945 * red - 74.494 * green + 112.439 * blue) / 256
    return y, ry, by


def encode_pixels(rgb: np.ndarray, mode: str = "robot36") -> np.ndarray:
    """把目标尺寸 RGB 数组编码成 48 kHz 相位连续的单声道 PCM。"""
    spec = get_mode(mode)
    image = np.asarray(rgb)
    if image.shape != (spec.height, spec.width, 3) or image.dtype != np.uint8:
        raise ValueError(f"{spec.name} 图像必须是 {spec.width}×{spec.height} 的 uint8 RGB")
    builder = _ToneBuilder()
    _emit_vis(builder, spec.vis)
    if spec.family.startswith("robot"):
        y, ry, by = _rgb_to_yuv(image)
        if spec.family == "robot36":
            for row in range(spec.height):
                builder.tone(9, 1200)
                builder.tone(3, 1500)
                builder.scan(88, y[row])
                builder.tone(4.5, 1500 if row % 2 == 0 else 2300)
                builder.tone(1.5, 1900)
                pair = row - row % 2
                chroma = ry if row % 2 == 0 else by
                builder.scan(44, (chroma[pair] + chroma[pair + 1]) / 2)
        else:
            for row in range(spec.height):
                builder.tone(9, 1200)
                builder.tone(3, 1500)
                builder.scan(138, y[row])
                builder.tone(4.5, 1500)
                builder.tone(1.5, 1900)
                builder.scan(69, ry[row])
                builder.tone(4.5, 2300)
                builder.tone(1.5, 1500)
                builder.scan(69, by[row])
    elif spec.family == "martin":
        for row in image:
            builder.tone(spec.sync_ms, 1200)
            builder.tone(spec.porch_ms, 1500)
            for channel in (1, 2, 0):  # Green, Blue, Red
                builder.scan(spec.scan_ms, row[:, channel])
                builder.tone(spec.porch_ms, 1500)
    else:
        builder.tone(9, 1200)  # Scottie 只有第一行之前的额外起始同步。
        for row in image:
            builder.tone(1.5, 1500)
            builder.scan(spec.scan_ms, row[:, 1])
            builder.tone(1.5, 1500)
            builder.scan(spec.scan_ms, row[:, 2])
            builder.tone(9, 1200)
            builder.tone(1.5, 1500)
            builder.scan(spec.scan_ms, row[:, 0])
    pcm = builder.pcm()
    return np.concatenate((pcm, np.zeros(round(0.25 * SAMPLE_RATE), dtype=np.float32)))


@dataclass(frozen=True)
class DecodedSSTV:
    mode: str
    vis: int
    rgb: np.ndarray
    leader_start_s: float


def _instantaneous_frequency(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    from scipy.signal import butter, sosfiltfilt

    bandpass = butter(3, (950, 2450), btype="bandpass", fs=sample_rate, output="sos")
    filtered = sosfiltfilt(bandpass, samples)
    analytic = hilbert(filtered)
    frequency = np.empty(samples.size, dtype=np.float32)
    frequency[0] = 0
    frequency[1:] = np.angle(analytic[1:] * np.conjugate(analytic[:-1])) * sample_rate / (2 * pi)
    frequency = uniform_filter1d(frequency, size=3)
    return frequency, np.abs(analytic).astype(np.float32)


def _find_vis(frequency: np.ndarray, amplitude: np.ndarray, sample_rate: int) -> tuple[SSTVMode, int]:
    bin_size = sample_rate // 1000
    bins = len(frequency) // bin_size
    if bins < 920:
        raise ValueError("SSTV WAV 不含完整 VIS 头")
    f_ms = np.median(frequency[:bins * bin_size].reshape(bins, bin_size), axis=1)
    a_ms = np.median(amplitude[:bins * bin_size].reshape(bins, bin_size), axis=1)
    threshold = max(float(np.max(a_ms)) * 0.06, 0.001)
    leader = binary_closing((np.abs(f_ms - 1900) < 100) & (a_ms > threshold),
                            structure=np.ones(5, dtype=bool))
    edges = np.diff(np.r_[False, leader, False].astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    long_runs = [(int(start), int(end)) for start, end in zip(starts, ends) if end - start >= 260]
    for first, _ in long_runs:
        for second, _ in long_runs:
            if not 302 <= second - first <= 318:
                continue
            near = second * bin_size
            lo, hi = max(1, near - 5 * bin_size), min(len(frequency) - 1, near + 5 * bin_size)
            transition = lo + int(np.argmax(np.diff(frequency[lo:hi]))) + 1
            leader_start = transition - round(0.310 * sample_rate)
            if leader_start < -round(0.002 * sample_rate):
                continue
            leader_start = max(0, leader_start)

            def tone_at(ms: float, width_ms: float = 10) -> float:
                center = leader_start + round(ms * sample_rate / 1000)
                half = round(width_ms * sample_rate / 2000)
                return float(np.median(frequency[center - half:center + half]))

            if not (abs(tone_at(315, 8) - 1900) < 100 and
                    abs(tone_at(625, 8) - 1200) < 100 and
                    abs(tone_at(895, 8) - 1200) < 100):
                continue
            bits = [1 if abs(tone_at(655 + bit * 30) - 1100) < 80 else
                    0 if abs(tone_at(655 + bit * 30) - 1300) < 80 else -1
                    for bit in range(7)]
            parity_freq = tone_at(865)
            parity = (1 if abs(parity_freq - 1100) < 80 else
                      0 if abs(parity_freq - 1300) < 80 else -1)
            if -1 in bits or parity < 0 or (sum(bits) + parity) % 2:
                continue
            vis = sum(bit << offset for offset, bit in enumerate(bits))
            if vis not in VIS_MODES:
                raise ValueError(f"不支持的 SSTV VIS 码：0x{vis:02X}")
            return VIS_MODES[vis], leader_start
    raise ValueError("未找到有效 SSTV VIS 头或奇偶校验失败")


def _scan(frequency: np.ndarray, start: float, duration_ms: float,
          width: int, sample_rate: int) -> np.ndarray:
    centers = start + (np.arange(width, dtype=np.float64) + 0.5) * duration_ms * sample_rate / (1000 * width)
    left = max(0, int(np.floor(centers[0])) - 1)
    right = min(len(frequency), int(np.ceil(centers[-1])) + 2)
    return _hz_value(np.interp(centers, np.arange(left, right), frequency[left:right])).astype(np.float32)


def _yuv_to_rgb(y: np.ndarray, ry: np.ndarray, by: np.ndarray) -> np.ndarray:
    yy, rr, bb = y - 16, ry - 128, by - 128
    red = (298.082 * yy + 408.583 * rr) / 256
    green = (298.082 * yy - 208.120 * rr - 100.291 * bb) / 256
    blue = (298.082 * yy + 516.411 * bb) / 256
    return np.clip(np.rint(np.stack((red, green, blue), axis=2)), 0, 255).astype(np.uint8)


def decode_pcm(samples: np.ndarray, sample_rate: int = SAMPLE_RATE,
               expected_mode: str | None = None) -> DecodedSSTV:
    """从含前置静默的单声道 PCM 恢复 VIS、行扫描和 RGB 图像。"""
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim != 1 or data.size == 0 or not np.all(np.isfinite(data)):
        raise ValueError("SSTV 解码需要非空、有限的单声道 PCM")
    if not 8_000 <= sample_rate <= 192_000 or data.size > sample_rate * 300:
        raise ValueError("SSTV PCM 须为 8–192 kHz 且不超过 300 s")
    if sample_rate != DECODE_RATE:
        from math import gcd
        divisor = gcd(sample_rate, DECODE_RATE)
        data = resample_poly(data, DECODE_RATE // divisor, sample_rate // divisor)
    frequency, amplitude = _instantaneous_frequency(data, DECODE_RATE)
    spec, leader_start = _find_vis(frequency, amplitude, DECODE_RATE)
    if expected_mode is not None and spec.name != get_mode(expected_mode).name:
        raise ValueError(f"SSTV VIS 为 {spec.name} (0x{spec.vis:02X})，预期 {expected_mode}")
    body_start = leader_start + round(0.910 * DECODE_RATE)
    if spec.family == "scottie":
        # 论文列出首行前额外 9 ms 同步；部分现有编码器省略它。
        probe = frequency[body_start + round(0.002 * DECODE_RATE):
                          body_start + round(0.007 * DECODE_RATE)]
        if probe.size and abs(float(np.median(probe)) - 1200) < 100:
            body_start += round(0.009 * DECODE_RATE)
    end = body_start + round(spec.line_ms * spec.height * DECODE_RATE / 1000)
    if len(frequency) + round(0.002 * DECODE_RATE) < end:
        raise ValueError(f"{spec.name} 扫描数据截断：需要 {spec.height} 行")

    def start_of_line(row: int) -> float:
        return body_start + row * spec.line_ms * DECODE_RATE / 1000

    if spec.family.startswith("robot"):
        y = np.empty((spec.height, spec.width), dtype=np.float32)
        ry = np.empty_like(y)
        by = np.empty_like(y)
        for row in range(spec.height):
            line = start_of_line(row)
            y[row] = _scan(frequency, line + 12 * DECODE_RATE / 1000,
                           spec.scan_ms, spec.width, DECODE_RATE)
            chroma_start = line + (12 + spec.scan_ms + 6) * DECODE_RATE / 1000
            if spec.family == "robot36":
                chroma = _scan(frequency, chroma_start, 44, spec.width, DECODE_RATE)
                if row % 2 == 0:
                    ry[row:row + 2] = chroma
                else:
                    by[row - 1:row + 1] = chroma
            else:
                ry[row] = _scan(frequency, chroma_start, 69, spec.width, DECODE_RATE)
                by[row] = _scan(frequency, chroma_start + (69 + 6) * DECODE_RATE / 1000,
                                69, spec.width, DECODE_RATE)
        rgb = _yuv_to_rgb(y, ry, by)
    else:
        rgb = np.empty((spec.height, spec.width, 3), dtype=np.uint8)
        for row in range(spec.height):
            line = start_of_line(row)
            if spec.family == "martin":
                green = line + (spec.sync_ms + spec.porch_ms) * DECODE_RATE / 1000
                blue = green + (spec.scan_ms + spec.porch_ms) * DECODE_RATE / 1000
                red = blue + (spec.scan_ms + spec.porch_ms) * DECODE_RATE / 1000
            else:
                green = line + 1.5 * DECODE_RATE / 1000
                blue = green + (spec.scan_ms + 1.5) * DECODE_RATE / 1000
                red = blue + (spec.scan_ms + 9 + 1.5) * DECODE_RATE / 1000
            for channel, start in ((0, red), (1, green), (2, blue)):
                rgb[row, :, channel] = np.clip(np.rint(_scan(frequency, start, spec.scan_ms,
                                                            spec.width, DECODE_RATE)), 0, 255).astype(np.uint8)
    return DecodedSSTV(spec.name, spec.vis, rgb, leader_start / DECODE_RATE)
