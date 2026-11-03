"""GUI 的设备检查与接收录音后台任务；模块导入不打开声卡或串口。"""
from pathlib import Path
import sqlite3
from threading import Event

from PySide6.QtCore import QThread, Signal

from radiolab.audio import AudioDevice, list_audio_devices, list_serial_ports
from radiolab.config import Config
from radiolab.history import make_entry
from radiolab.service import ReceiveService
from radiolab.storage import SQLiteHistoryService


class DeviceProbeWorker(QThread):
    ready = Signal(object, object, str, str)

    def run(self) -> None:
        devices, audio_error = list_audio_devices()
        ports, serial_error = list_serial_ports()
        self.ready.emit(devices, ports, audio_error or "", serial_error or "")


class RecordWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, device: AudioDevice, seconds: float, path: Path,
                 store: SQLiteHistoryService) -> None:
        super().__init__()
        self.device = device
        self.seconds = seconds
        self.path = path
        self.store = store
        self.stop_event = Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            devices, error = list_audio_devices()
            if error:
                raise ValueError(error)
            if not any(d.index == self.device.index and d.name == self.device.name and d.inputs > 0
                       for d in devices):
                raise ValueError(f"输入设备 {self.device.name!r} 已变化；刷新设备后重新选择")
        except (ValueError, OSError) as exc:
            try:
                self.store.append(make_entry("audio", "record", device=self.device.name,
                                             wav_path=self.path, success=False, reason=str(exc)))
            except (OSError, ValueError, sqlite3.DatabaseError) as history_error:
                self.failed.emit(f"{exc}；历史写入也失败：{history_error}")
            else:
                self.failed.emit(str(exc))
            return
        try:
            path = ReceiveService(history=self.store).record_until(
                self.seconds, self.path, config=Config(rx_device=self.device.index),
                stop_event=self.stop_event, device_identity=self.device)
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            saved = f"；WAV 已存在：{self.path}" if self.path.is_file() else ""
            self.failed.emit(f"{exc}{saved}")
        else:
            self.succeeded.emit(str(path))


class TextDecodeWorker(QThread):
    succeeded = Signal(str, int)
    failed = Signal(str)

    def __init__(self, wav: Path, store: SQLiteHistoryService) -> None:
        super().__init__()
        self.wav = wav
        self.store = store

    def run(self) -> None:
        try:
            result = ReceiveService(history=self.store).decode_file(self.wav)
            text = result.frame.text
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(text, result.frame.sequence)


class ReplayWorker(QThread):
    """显式选择本地输出后的 WAV 回放；不接触 PTT。"""

    succeeded = Signal(float)
    failed = Signal(str)

    def __init__(self, wav: Path, output: AudioDevice, gain: float,
                 store: SQLiteHistoryService) -> None:
        super().__init__()
        self.wav = wav
        self.output = output
        self.gain = gain
        self.store = store
        self.stop_event = Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            devices, error = list_audio_devices()
            if error:
                raise ValueError(error)
            if not any(d.index == self.output.index and d.name == self.output.name and d.outputs > 0
                       for d in devices):
                raise ValueError(f"本地输出设备 {self.output.name!r} 已变化；刷新设备并重新选择")
        except (ValueError, OSError) as exc:
            try:
                self.store.append(make_entry("audio", "replay", device=self.output.name,
                                             wav_path=self.wav, gain=self.gain,
                                             success=False, reason=str(exc)))
            except (OSError, ValueError, sqlite3.DatabaseError) as history_error:
                self.failed.emit(f"{exc}；历史写入也失败：{history_error}")
            else:
                self.failed.emit(str(exc))
            return
        try:
            duration = ReceiveService(history=self.store).replay_file(
                self.wav, output_device=self.output, gain=self.gain,
                cancel_event=self.stop_event)
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(duration)


class TextListenWorker(QThread):
    """独立 RX 输入的限时文本监听；实时发现帧并保留整段 WAV。"""

    frame_received = Signal(str, int)
    level = Signal(object)
    succeeded = Signal(str, int)
    failed = Signal(str)

    def __init__(self, device: AudioDevice, seconds: float, path: Path,
                 store: SQLiteHistoryService) -> None:
        super().__init__()
        self.device = device
        self.seconds = seconds
        self.path = path
        self.store = store
        self.stop_event = Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            devices, error = list_audio_devices()
            if error:
                raise ValueError(error)
            if not any(d.index == self.device.index and d.name == self.device.name and d.inputs > 0
                       for d in devices):
                raise ValueError(f"输入设备 {self.device.name!r} 已变化；刷新设备后重新选择")
        except (ValueError, OSError) as exc:
            try:
                self.store.append(make_entry("audio", "record", device=self.device.name,
                                             wav_path=self.path, success=False, reason=str(exc)))
            except (OSError, ValueError, sqlite3.DatabaseError) as history_error:
                self.failed.emit(f"{exc}；历史写入也失败：{history_error}")
            else:
                self.failed.emit(str(exc))
            return
        try:
            path, frames = ReceiveService(history=self.store).listen_text(
                self.seconds, self.path, device=self.device, stop_event=self.stop_event,
                on_frame=lambda result: self.frame_received.emit(result.frame.text,
                                                                  result.frame.sequence),
                on_level=self.level.emit)
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            saved = f"；部分 WAV 已保存：{self.path}" if self.path.is_file() else ""
            self.failed.emit(f"{exc}{saved}")
        else:
            self.succeeded.emit(str(path), len(frames))
