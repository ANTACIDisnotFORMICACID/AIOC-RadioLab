"""设备与 WAV 边界。导入模块不访问声卡。"""
from dataclasses import dataclass
from math import gcd
import math
from pathlib import Path
from threading import Event

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .afsk import SAMPLE_RATE


class AudioError(ValueError):
    pass


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    inputs: int
    outputs: int
    default_sample_rate: float


@dataclass(frozen=True)
class AudioLevel:
    """相对于满量程的录音电平；不能单凭电平判断是否有有效消息。"""

    peak_dbfs: float
    rms_dbfs: float
    clipped_fraction: float
    low_level: bool


def audio_level(samples: np.ndarray) -> AudioLevel:
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim != 1 or x.size == 0 or not np.all(np.isfinite(x)):
        raise AudioError("电平分析需要非空、有限的单声道音频")
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    dbfs = lambda value: 20 * np.log10(max(value, 1e-12))
    return AudioLevel(float(dbfs(peak)), float(dbfs(rms)),
                      float(np.mean(np.abs(x) >= 0.99)), rms < 10 ** (-50 / 20))


def list_audio_devices() -> tuple[list[AudioDevice], str | None]:
    try:
        import sounddevice as sd
        devices = [AudioDevice(i, d["name"], d["max_input_channels"], d["max_output_channels"], d["default_samplerate"])
                   for i, d in enumerate(sd.query_devices())]
        return devices, None
    except Exception as exc:
        return [], f"音频设备不可用：{exc}"


def list_serial_ports() -> tuple[list[tuple[str, str]], str | None]:
    try:
        from serial.tools import list_ports
        return [(p.device, p.description) for p in list_ports.comports()], None
    except Exception as exc:
        return [], f"串口枚举不可用：{exc}"


def _select(device: str | int | AudioDevice | None, direction: str,
            sample_rate: int = SAMPLE_RATE) -> tuple[int, int]:
    if device is None or str(device).strip() == "":
        raise AudioError(f"必须明确指定 {direction} 音频设备；先运行 radiolab devices")
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise AudioError("缺少 sounddevice；安装 radiolab[devices]") from exc
    devices, error = list_audio_devices()
    if error:
        raise AudioError(error)
    matches = [d for d in devices if
               ((d.index == device.index and d.name == device.name) if isinstance(device, AudioDevice)
                else (str(d.index) == str(device) or d.name == str(device)))
               and (d.outputs if direction == "输出" else d.inputs) > 0]
    if len(matches) != 1:
        raise AudioError(f"{direction}设备 {device!r} 未找到或名称不唯一；运行 radiolab devices 查看索引")
    selected = matches[0]
    if direction == "输出":
        try:
            sd.check_output_settings(device=selected.index, channels=1, samplerate=sample_rate)
        except Exception as exc:
            raise AudioError(f"输出设备 {selected.name!r} 不支持单声道 {sample_rate} sample/s：{exc}") from exc
        return selected.index, 1
    last_error = None
    for channels in range(1, selected.inputs + 1):
        try:
            sd.check_input_settings(device=selected.index, channels=channels, samplerate=sample_rate)
            return selected.index, channels
        except Exception as exc:
            last_error = exc
    raise AudioError(f"输入设备 {selected.name!r} 没有可用的 {sample_rate} sample/s 通道配置：{last_error}")


def save_wav(path: str | Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    x = np.asarray(samples)
    if sample_rate != SAMPLE_RATE or x.ndim != 1 or x.size == 0 or not np.all(np.isfinite(x)) or np.max(np.abs(x)) > 1:
        raise AudioError("WAV 需要 48000 sample/s、非空有限单声道 PCM，峰值不得超过 1")
    path = Path(path)
    if path.suffix.lower() != ".wav" or path.is_dir():
        raise AudioError(f"WAV 输出路径 {path} 必须是 .wav 文件，不能是目录")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(path, x, sample_rate, subtype="PCM_16")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise AudioError(f"无法保存 WAV {path}：{exc}；检查路径和写入权限") from exc
    return path


def load_wav(path: str | Path) -> np.ndarray:
    """接收 WAV 转为 48 kHz 单声道；发送文件始终由 save_wav 固定格式。"""
    try:
        info = sf.info(path)
        if info.format not in {"WAV", "WAVEX"} or info.subtype not in {"PCM_U8", "PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"}:
            raise AudioError("仅接受 PCM 或浮点 WAV；请先转换文件格式")
        if not 8000 <= info.samplerate <= 192000 or info.channels not in (1, 2) or info.frames > info.samplerate * 120:
            raise AudioError("WAV 需为 8–192 kHz、单/双声道、最长 120 s；请先转换")
        data, _ = sf.read(path, dtype="float32", always_2d=True)
    except (OSError, RuntimeError) as exc:
        raise AudioError(f"无法读取 WAV {path}：{exc}") from exc
    if data.size == 0 or not np.all(np.isfinite(data)):
        raise AudioError("WAV 为空或包含非有限样本")
    mono = data.mean(axis=1)
    if info.samplerate != SAMPLE_RATE:
        divisor = gcd(info.samplerate, SAMPLE_RATE)
        mono = resample_poly(mono, SAMPLE_RATE // divisor, info.samplerate // divisor)
    if not np.all(np.isfinite(mono)):
        raise AudioError("WAV 重采样后包含非有限样本")
    return mono.astype(np.float32)


class SoundDeviceAudio:
    def validate_output(self, device: str | int, sample_rate: int = SAMPLE_RATE) -> None:
        _select(device, "输出", sample_rate)

    def validate_input(self, device: str | int, sample_rate: int = SAMPLE_RATE) -> None:
        _select(device, "输入", sample_rate)

    def play(self, samples: np.ndarray, *, device: str | int | AudioDevice,
             sample_rate: int = SAMPLE_RATE, gain: float = 1.0) -> None:
        if not 0 < gain <= 1:
            raise AudioError("gain 必须在 (0, 1]，建议先低电平校准")
        index, _ = _select(device, "输出", sample_rate)
        import sounddevice as sd
        try:
            sd.play(np.asarray(samples, dtype=np.float32) * gain, samplerate=sample_rate, device=index, blocking=True)
        except Exception as exc:
            raise AudioError(f"输出设备 {device!r} 播放失败：{exc}；检查设备连接和采样率") from exc

    def stop(self) -> None:
        import sounddevice as sd
        sd.stop()

    def record(self, seconds: float, *, device: str | int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
        if not 0 < seconds <= 120 or round(seconds * sample_rate) < 1:
            raise AudioError("录音时长必须至少覆盖 1 个样本且不超过 120 s")
        index, channels = _select(device, "输入", sample_rate)
        import sounddevice as sd
        try:
            frames = round(seconds * sample_rate)
            samples = sd.rec(frames, samplerate=sample_rate, channels=channels,
                             dtype="float32", device=index, blocking=True)
        except Exception as exc:
            raise AudioError(f"输入设备 {device!r} 录音失败：{exc}；检查设备连接和采样率") from exc
        if samples.shape != (frames, channels) or not np.all(np.isfinite(samples)):
            raise AudioError(f"输入设备 {device!r} 返回非有限或不完整的录音样本；请检查输入连接，或改用其他音频接口后重试")
        if np.max(np.abs(samples)) > 1:
            raise AudioError(f"输入设备 {device!r} 录音超出 PCM 满量程；请降低输入增益后重试")
        return samples.mean(axis=1)

    def record_stream(self, seconds: float, *, device: str | int | AudioDevice,
                      sample_rate: int = SAMPLE_RATE,
                      stop_event: Event | None = None) -> np.ndarray:
        """按短块采样；停止请求后保留已采集样本，适合 GUI 的手动停止。"""
        if (not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                or not 0 < seconds <= 120 or sample_rate != SAMPLE_RATE):
            raise AudioError("流式录音需要 0–120 s 的有效时长与 48000 sample/s")
        total = round(seconds * sample_rate)
        if total < 1:
            raise AudioError("录音时长至少要覆盖 1 个样本")
        index, channels = _select(device, "输入", sample_rate)
        import sounddevice as sd
        chunks: list[np.ndarray] = []
        captured = 0
        try:
            with sd.InputStream(samplerate=sample_rate, channels=channels,
                                dtype="float32", device=index) as stream:
                while captured < total and not (stop_event is not None and stop_event.is_set()):
                    count = min(2048, total - captured)
                    block, overflowed = stream.read(count)
                    block = np.asarray(block, dtype=np.float32)
                    if overflowed:
                        raise AudioError(f"输入设备 {device!r} 录音缓冲区溢出；检查采样率或系统负载")
                    if block.shape != (count, channels) or not np.all(np.isfinite(block)):
                        raise AudioError(f"输入设备 {device!r} 返回非有限或不完整录音样本")
                    if np.max(np.abs(block)) > 1:
                        raise AudioError(f"输入设备 {device!r} 超出 PCM 满量程；降低输入增益")
                    chunks.append(block.mean(axis=1))
                    captured += count
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"输入设备 {device!r} 流式录音失败：{exc}；检查连接和采样率") from exc
        if not chunks:
            raise AudioError("录音停止前未采集到样本；请重新开始并等待输入")
        return np.concatenate(chunks)

    def input_blocks(self, seconds: float, *, device: str | int | AudioDevice,
                     sample_rate: int = SAMPLE_RATE, stop_event: Event | None = None):
        """持续给出已校验的单声道块；供文本监听边录边解码。"""
        if (not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
                or not 0 < seconds <= 120 or sample_rate != SAMPLE_RATE):
            raise AudioError("监听最长时长须在 (0, 120] s，采样率为 48000 sample/s")
        index, channels = _select(device, "输入", sample_rate)
        import sounddevice as sd
        captured = 0
        total = round(seconds * sample_rate)
        try:
            with sd.InputStream(samplerate=sample_rate, channels=channels,
                                dtype="float32", device=index) as stream:
                while captured < total and not (stop_event is not None and stop_event.is_set()):
                    count = min(2048, total - captured)
                    block, overflowed = stream.read(count)
                    block = np.asarray(block, dtype=np.float32)
                    if overflowed or block.shape != (count, channels) or not np.all(np.isfinite(block)):
                        raise AudioError(f"输入设备 {device!r} 监听溢出或返回无效样本；检查连接和系统负载")
                    if np.max(np.abs(block)) > 1:
                        raise AudioError(f"输入设备 {device!r} 超出 PCM 满量程；降低输入增益")
                    captured += count
                    yield block.mean(axis=1)
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"输入设备 {device!r} 监听失败：{exc}；检查连接和采样率") from exc
