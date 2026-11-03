"""收发事务；真实发送需由 CLI 显式解锁。"""
import math
import time
from enum import StrEnum
from threading import Event, Lock, Thread, Timer
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .afsk import AFSKStreamScanner, DecodeResult, SAMPLE_RATE, decode_afsk, modulate_frame
from .audio import AudioDevice, AudioError, AudioLevel, SoundDeviceAudio, audio_level, load_wav, save_wav
from .config import Config, validate_on_air
from .history import HistoryService, make_entry
from .packet import encode_text
from .ptt import AIOCSerialPTT, PTTBackend, TX_FAULT, TX_LOCK


class TxPhase(StrEnum):
    """受控发射的可观察阶段；只有发送服务报告真实进展。"""

    PTT_KEYED = "ptt_keyed"
    PLAYING = "playing"
    TAIL = "tail"


class SendService:
    def __init__(self, audio=None, ptt: PTTBackend | None = None, history: HistoryService | None = None,
                 sleep=time.sleep, clock=time.monotonic) -> None:
        self.audio = audio or SoundDeviceAudio()
        self.ptt = ptt
        self.history = history or HistoryService()
        self.sleep = sleep
        self.clock = clock

    def send_pcm(self, samples: np.ndarray, *, config: Config, wav_path: str | Path,
                 mode: str, text: str | None = None, sequence: int | None = None,
                 image_path: str | Path | None = None, on_air: bool = False,
                 output_identity: AudioDevice | None = None,
                 cancel_event: Event | None = None,
                 on_phase: Callable[[TxPhase], None] | None = None) -> Path:
        path = Path(wav_path)

        def entry(operation: str, *, success: bool = True, reason: str | None = None):
            return make_entry(mode, operation, device=config.tx_device if operation == "send" else None,
                              wav_path=path, text=text, sequence=sequence, success=success, reason=reason,
                              firmware=config.firmware, sample_rate=SAMPLE_RATE,
                              gain=config.gain if operation == "send" else None,
                              frequency_hz=config.frequency_hz, power_setting=config.power_setting,
                              payload_bytes=len(text.encode("utf-8")) if text is not None else None,
                              image_path=image_path, ptt_port=config.ptt_port if operation == "send" else None)

        try:
            path = save_wav(path, samples)
        except Exception as exc:
            self.history.append(entry("send" if on_air else "encode", success=False, reason=str(exc)))
            raise

        if not on_air:
            self.history.append(entry("encode"))
            return path
        try:
            validate_on_air(config)
            if output_identity is not None and str(config.tx_device) not in (
                    str(output_identity.index), output_identity.name):
                raise ValueError("所选 TX 输出与配置 audio.tx_device 不一致；刷新设备并核对配置")
            output_device = output_identity or config.tx_device
            total = len(samples) / SAMPLE_RATE + config.pre_delay_s + config.tail_delay_s
            if total > config.max_tx_seconds:
                raise ValueError(f"发射总时长 {total:.1f} s 超过配置上限 {config.max_tx_seconds:.1f} s")
            validate_output = getattr(self.audio, "validate_output", None)
            if validate_output is not None:
                validate_output(output_device, SAMPLE_RATE)
        except Exception as exc:
            self.history.append(entry("send", success=False, reason=str(exc)))
            raise
        if not TX_LOCK.acquire(blocking=False):
            error = RuntimeError("已有发射事务正在执行")
            self.history.append(entry("send", success=False, reason=str(error)))
            raise error
        if TX_FAULT.is_set():
            TX_LOCK.release()
            error = RuntimeError("PTT 释放状态不明；本进程拒绝继续发射。请先检查手台和串口，排除卡键后重启应用")
            self.history.append(entry("send", success=False, reason=str(error)))
            raise error
        ptt = None
        release_lock = Lock()
        released = False
        timed_out = Event()
        cancelled = Event()
        done = Event()
        watchdog_errors: list[BaseException] = []
        timer = None
        timer_started = False
        cancel_monitor = None

        def release_once():
            nonlocal released
            with release_lock:
                if ptt is not None and not released:
                    ptt.release()
                    released = True

        def watchdog():
            timed_out.set()
            try:
                release_once()
            except BaseException as exc:
                TX_FAULT.set()
                watchdog_errors.append(exc)
            finally:
                stop = getattr(self.audio, "stop", None)
                if stop is not None:
                    try:
                        stop()
                    except BaseException as exc:
                        watchdog_errors.append(exc)

        def monitor_cancel():
            while not done.wait(0.02):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled.set()
                    try:
                        release_once()
                    except BaseException as exc:
                        TX_FAULT.set()
                        watchdog_errors.append(exc)
                    finally:
                        stop = getattr(self.audio, "stop", None)
                        if stop is not None:
                            try:
                                stop()
                            except BaseException as exc:
                                watchdog_errors.append(exc)
                    return

        def check_running(stage: str, start: float) -> None:
            if cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise InterruptedError(f"用户在{stage}请求停止发射")
            if timed_out.is_set() or self.clock() - start > config.max_tx_seconds:
                raise TimeoutError(f"{stage}超过连续发射上限")

        def unlock_after_callbacks(threads) -> None:
            """清理线程若暂未退出，保持发射互斥直至旧线程全部结束。"""
            for thread in threads:
                thread.join()
            TX_LOCK.release()

        failure: BaseException | None = None
        release_failure: BaseException | None = None
        try:
            start = self.clock()
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("用户在按下 PTT 前取消发射")
            ptt = self.ptt or AIOCSerialPTT(config.ptt_port)
            ptt.key()
            timer = Timer(config.max_tx_seconds, watchdog)
            timer.daemon = True
            timer.start()
            timer_started = True
            if cancel_event is not None:
                cancel_monitor = Thread(target=monitor_cancel, daemon=True)
                cancel_monitor.start()
            if on_phase is not None:
                on_phase(TxPhase.PTT_KEYED)
                check_running("PTT 状态回调", start)
            self.sleep(config.pre_delay_s)
            check_running("预延时", start)
            if on_phase is not None:
                on_phase(TxPhase.PLAYING)
                check_running("播放前状态回调", start)
            self.audio.play(samples, device=output_device, sample_rate=SAMPLE_RATE, gain=config.gain)
            check_running("播放", start)
            if on_phase is not None:
                on_phase(TxPhase.TAIL)
                check_running("结束前状态回调", start)
            self.sleep(config.tail_delay_s)
            check_running("结束延时", start)
        except BaseException as exc:
            failure = exc
        finally:
            done.set()
            try:
                if timer is not None:
                    timer.cancel()
            except BaseException as exc:
                watchdog_errors.append(exc)
            try:
                # key() 在控制线切换中出错也尝试释放。
                release_once()
            except BaseException as exc:
                TX_FAULT.set()
                release_failure = exc
            callbacks = ([timer] if timer_started else []) + ([cancel_monitor] if cancel_monitor is not None else [])
            try:
                for thread in callbacks:
                    thread.join(timeout=1.0)
                pending = [thread for thread in callbacks if thread.is_alive()]
                if pending:
                    watchdog_errors.append(RuntimeError("旧发射清理线程未结束；发射互斥保持至其退出"))
                    Thread(target=unlock_after_callbacks, args=(pending,), daemon=True).start()
                else:
                    TX_LOCK.release()
            except BaseException as exc:
                # 无法确认旧回调已停止时，保留互斥锁，避免下一次发送被旧回调干扰。
                watchdog_errors.append(exc)
        if timed_out.is_set() and failure is None:
            failure = TimeoutError("连续发射达到上限")
        if cancelled.is_set() and failure is None:
            failure = InterruptedError("用户请求停止发射")

        if failure is not None or release_failure is not None or watchdog_errors:
            reasons = [str(error) for error in (failure, release_failure, *watchdog_errors) if error is not None]
            if cancelled.is_set() and not any("停止发射" in reason for reason in reasons):
                reasons.insert(0, "用户请求停止发射")
            reason = "；".join(reasons)
            self.history.append(entry("send", success=False, reason=reason))
            if release_failure is not None or watchdog_errors:
                cause = release_failure or watchdog_errors[0]
                raise RuntimeError(f"发送后 PTT/音频清理未完成：{reason}") from cause
            raise failure
        self.history.append(entry("send"))
        return path

    def send_text(self, text: str, *, config: Config = Config(), sequence: int = 0,
                  wav_path: str | Path = "runtime/text.wav", on_air: bool = False,
                  output_identity: AudioDevice | None = None,
                  cancel_event: Event | None = None,
                  on_phase: Callable[[TxPhase], None] | None = None) -> Path:
        try:
            pcm = modulate_frame(encode_text(text, sequence))
        except ValueError as exc:
            self.history.append(make_entry("text", "send" if on_air else "encode", wav_path=wav_path,
                                           sequence=sequence, success=False, reason=str(exc)))
            raise
        return self.send_pcm(pcm, config=config,
                             wav_path=wav_path, mode="text", text=text, sequence=sequence, on_air=on_air,
                             output_identity=output_identity, cancel_event=cancel_event, on_phase=on_phase)


class ReceiveService:
    def __init__(self, audio=None, history: HistoryService | None = None) -> None:
        self.audio = audio or SoundDeviceAudio()
        self.history = history or HistoryService()

    def decode_file(self, path: str | Path):
        try:
            from soundfile import info
            source_rate = info(path).samplerate
            result = decode_afsk(load_wav(path))
            message = result.frame.text
        except Exception as exc:
            self.history.append(make_entry("text", "decode", wav_path=path, crc_ok=False, success=False, reason=str(exc)))
            raise
        self.history.append(make_entry("text", "decode", wav_path=path, text=message,
                                       sequence=result.frame.sequence, crc_ok=True,
                                       sample_rate=source_rate, payload_bytes=len(result.frame.payload)))
        return result

    def record(self, seconds: float, path: str | Path, *, config: Config) -> Path:
        try:
            samples = self.audio.record(seconds, device=config.rx_device, sample_rate=SAMPLE_RATE)
            output = save_wav(path, samples)
            level = audio_level(samples)
        except Exception as exc:
            self.history.append(make_entry("audio", "record", device=config.rx_device, wav_path=path, success=False, reason=str(exc)))
            raise
        note = "电平偏低，检查 RX 输入路径" if level.low_level else None
        self.history.append(make_entry("audio", "record", device=config.rx_device, wav_path=output,
                                       firmware=config.firmware, sample_rate=SAMPLE_RATE,
                                       frequency_hz=config.frequency_hz, power_setting=config.power_setting,
                                       reason=note))
        return output

    def record_until(self, seconds: float, path: str | Path, *, config: Config,
                     stop_event, device_identity: AudioDevice | None = None) -> Path:
        """接收 Worker 使用的可停止录音；所有录音仍写入同一历史接口。"""
        try:
            samples = self.audio.record_stream(seconds, device=device_identity or config.rx_device,
                                               sample_rate=SAMPLE_RATE, stop_event=stop_event)
            output = save_wav(path, samples)
            level = audio_level(samples)
        except Exception as exc:
            self.history.append(make_entry("audio", "record", device=config.rx_device,
                                           wav_path=path, success=False, reason=str(exc)))
            raise
        note = "电平偏低，检查 RX 输入路径" if level.low_level else None
        self.history.append(make_entry("audio", "record", device=config.rx_device,
                                       wav_path=output, firmware=config.firmware,
                                       sample_rate=SAMPLE_RATE, frequency_hz=config.frequency_hz,
                                       power_setting=config.power_setting, reason=note))
        return output

    def listen_text(self, seconds: float, path: str | Path, *, device: AudioDevice,
                    stop_event: Event, on_frame: Callable[[DecodeResult], None] | None = None,
                    on_level: Callable[[AudioLevel], None] | None = None
                    ) -> tuple[Path, list[DecodeResult]]:
        """边采集边扫描文本帧，保留整段 PCM16 WAV 供复核。"""
        output = Path(path)
        scanner = AFSKStreamScanner()
        decoded: list[DecodeResult] = []
        recorded = 0
        last_level_sample = 0

        def accept(result: DecodeResult) -> None:
            if result.frame.kind != 1:
                return
            self.history.append(make_entry("text", "decode", device=device.name,
                                           wav_path=output, text=result.frame.text,
                                           sequence=result.frame.sequence, crc_ok=True,
                                           sample_rate=SAMPLE_RATE,
                                           payload_bytes=len(result.frame.payload)))
            decoded.append(result)
            if on_frame is not None:
                on_frame(result)

        try:
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 120:
                raise ValueError("监听最长时长须在 (0, 120] s")
            if output.suffix.lower() != ".wav" or output.is_dir():
                raise ValueError(f"监听录音输出必须是 .wav 文件：{output}")
            if stop_event.is_set():
                raise InterruptedError("监听尚未开始就已停止")
            if output.exists():
                raise ValueError(f"监听录音目标已存在：{output}；请换路径保留原件")
            validate = getattr(self.audio, "validate_input", None)
            if validate is not None:
                validate(device, SAMPLE_RATE)
            output.parent.mkdir(parents=True, exist_ok=True)
            with sf.SoundFile(output, mode="x", samplerate=SAMPLE_RATE, channels=1,
                              subtype="PCM_16", format="WAV") as wav:
                for block in self.audio.input_blocks(seconds, device=device,
                                                     sample_rate=SAMPLE_RATE, stop_event=stop_event):
                    block = np.asarray(block, dtype=np.float32)
                    if (block.ndim != 1 or not block.size or not np.all(np.isfinite(block))
                            or np.max(np.abs(block)) > 1):
                        raise AudioError("输入设备返回无效或超量程的监听 PCM 块")
                    wav.write(block)
                    recorded += len(block)
                    if on_level is not None and recorded - last_level_sample >= SAMPLE_RATE // 5:
                        on_level(audio_level(block))
                        last_level_sample = recorded
                    for result in scanner.push(block):
                        accept(result)
                for result in scanner.finish():
                    accept(result)
                if on_level is not None and recorded > last_level_sample:
                    on_level(audio_level(block))
            if recorded == 0:
                raise AudioError("监听未收到音频样本；检查所选输入设备后重试")
        except Exception as exc:
            self.history.append(make_entry("audio", "record", device=device.name, wav_path=output,
                                           success=False, reason=str(exc), sample_rate=SAMPLE_RATE))
            raise
        self.history.append(make_entry("audio", "record", device=device.name, wav_path=output,
                                       sample_rate=SAMPLE_RATE,
                                       reason=f"自动监听结束；有效文本帧 {len(decoded)} 条"))
        return output, decoded

    def replay_file(self, path: str | Path, *, output_device: str | int | AudioDevice,
                    gain: float = 0.05, cancel_event: Event | None = None) -> float:
        """在显式选择的本地输出回放 WAV；不接触 PTT。"""
        device_label = output_device.name if isinstance(output_device, AudioDevice) else output_device
        monitor_done = Event()
        monitor_errors: list[BaseException] = []
        monitor = None

        def stop_on_cancel() -> None:
            while not monitor_done.wait(0.02):
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        self.audio.stop()
                    except BaseException as exc:
                        monitor_errors.append(exc)
                    return

        try:
            if not isinstance(gain, (int, float)) or not math.isfinite(gain) or not 0 < gain <= 0.2:
                raise ValueError("回放增益须在 (0, 0.2]，建议从 0.05 开始")
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("用户在开始前取消本地回放")
            samples = load_wav(path)
            if float(np.max(np.abs(samples))) * gain > 0.25:
                raise ValueError("回放音频幅度过高；降低 --gain 后重试")
            try:
                if cancel_event is not None:
                    monitor = Thread(target=stop_on_cancel, daemon=True)
                    monitor.start()
                self.audio.play(samples, device=output_device, sample_rate=SAMPLE_RATE, gain=gain)
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("用户停止本地回放")
            finally:
                monitor_done.set()
                try:
                    self.audio.stop()
                finally:
                    if monitor is not None:
                        monitor.join(timeout=1.0)
            if monitor_errors:
                raise RuntimeError(f"本地回放停止不完整：{monitor_errors[0]}")
        except BaseException as exc:
            reason = "用户中断" if isinstance(exc, KeyboardInterrupt) else str(exc)
            if cancel_event is not None and cancel_event.is_set() and "用户停止" not in reason:
                reason = f"用户停止本地回放；{reason}"
            self.history.append(make_entry("audio", "replay", device=device_label, wav_path=path,
                                           gain=gain, success=False, reason=reason))
            raise
        self.history.append(make_entry("audio", "replay", device=device_label, wav_path=path,
                                       sample_rate=SAMPLE_RATE, gain=gain))
        return len(samples) / SAMPLE_RATE
