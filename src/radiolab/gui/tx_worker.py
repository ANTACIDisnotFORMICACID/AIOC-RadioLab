"""GUI 发送侧后台任务；离线编码与受控发射共用 SendService。"""
from pathlib import Path
import sqlite3
from threading import Event

from PySide6.QtCore import QThread, Signal

from radiolab.audio import AudioDevice, list_audio_devices, list_serial_ports
from radiolab.config import Config, validate_on_air
from radiolab.history import make_entry
from radiolab.service import SendService, TxPhase
from radiolab.sstv import render_robot36_pcm
from radiolab.storage import SQLiteHistoryService


class TextEncodeWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, message: str, sequence: int, path: Path,
                 store: SQLiteHistoryService) -> None:
        super().__init__()
        self.message = message
        self.sequence = sequence
        self.path = path
        self.store = store

    def run(self) -> None:
        try:
            output = SendService(history=self.store).send_text(
                self.message, config=Config(), sequence=self.sequence,
                wav_path=self.path, on_air=False)
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(output))


class SSTVEncodeWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, image: Path, path: Path, store: SQLiteHistoryService) -> None:
        super().__init__()
        self.image = image
        self.path = path
        self.store = store

    def run(self) -> None:
        try:
            pcm = render_robot36_pcm(self.image)
        except (ValueError, OSError, RuntimeError) as exc:
            try:
                self.store.append(make_entry("sstv", "encode", image_path=self.image,
                                             wav_path=self.path, success=False, reason=str(exc)))
            except (ValueError, OSError, sqlite3.DatabaseError) as history_error:
                self.failed.emit(f"{exc}；历史写入也失败：{history_error}")
            else:
                self.failed.emit(str(exc))
            return
        try:
            output = SendService(history=self.store).send_pcm(
                pcm, config=Config(), wav_path=self.path, mode="sstv",
                image_path=self.image, on_air=False)
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(output))


class OnAirWorker(QThread):
    """显式发射任务；再次确认设备身份，所有 PTT 生命周期交给 SendService。"""

    succeeded = Signal(str)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, mode: str, path: Path, config: Config, output: AudioDevice,
                 store: SQLiteHistoryService, *, message: str = "", sequence: int = 0,
                 image: Path | None = None) -> None:
        super().__init__()
        self.mode = mode
        self.path = path
        self.config = config
        self.output = output
        self.store = store
        self.message = message
        self.sequence = sequence
        self.image = image
        self.cancel_event = Event()

    def stop(self) -> None:
        self.cancel_event.set()

    def _report_phase(self, phase: TxPhase) -> None:
        descriptions = {
            TxPhase.PTT_KEYED: "PTT 已按下；正在等待预延时",
            TxPhase.PLAYING: "PTT 已按下；正在播放完整音频",
            TxPhase.TAIL: "音频已播放；PTT 保持至尾延时结束",
        }
        self.status.emit(descriptions[phase])

    def run(self) -> None:
        entered_service = False
        try:
            validate_on_air(self.config)
            if str(self.config.tx_device) not in (str(self.output.index), self.output.name):
                raise ValueError("所选 TX 输出与配置 audio.tx_device 不一致")
            devices, audio_error = list_audio_devices()
            if audio_error:
                raise ValueError(audio_error)
            if not any(d.index == self.output.index and d.name == self.output.name and d.outputs > 0
                       for d in devices):
                raise ValueError(f"输出设备 {self.output.name!r} 已变化；刷新设备并重新选择")
            ports, serial_error = list_serial_ports()
            if serial_error:
                raise ValueError(serial_error)
            if self.config.ptt_port.casefold() not in {port.casefold() for port, _ in ports}:
                raise ValueError(f"PTT 串口 {self.config.ptt_port} 已消失；刷新设备并检查接线")
            if self.path.exists():
                raise ValueError(f"目标 WAV 已存在：{self.path}；请换路径保留原件")
            if self.cancel_event.is_set():
                raise InterruptedError("用户在按下 PTT 前取消发射")
            sender = SendService(history=self.store)
            if self.mode == "text":
                self.status.emit("已通过预检；正在执行受控文本发射")
                entered_service = True
                output = sender.send_text(self.message, config=self.config,
                                          sequence=self.sequence, wav_path=self.path,
                                          on_air=True, output_identity=self.output,
                                          cancel_event=self.cancel_event, on_phase=self._report_phase)
            elif self.mode == "sstv":
                if self.image is None:
                    raise ValueError("SSTV 发射需要图片")
                self.status.emit("正在由内置算法生成 Robot36 音频；尚未按下 PTT")
                pcm = render_robot36_pcm(self.image, on_air=True)
                if self.cancel_event.is_set():
                    raise InterruptedError("用户在按下 PTT 前取消发射")
                self.status.emit("已通过预检；正在执行受控 Robot36 发射")
                entered_service = True
                output = sender.send_pcm(pcm, config=self.config, wav_path=self.path,
                                         mode="sstv", image_path=self.image, on_air=True,
                                         output_identity=self.output, cancel_event=self.cancel_event,
                                         on_phase=self._report_phase)
            else:
                raise ValueError(f"不支持的发射模式：{self.mode}")
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            if not entered_service:
                try:
                    self.store.append(make_entry(self.mode, "send", device=self.output.name,
                                                 wav_path=self.path, image_path=self.image,
                                                 text=self.message if self.mode == "text" else None,
                                                 sequence=self.sequence if self.mode == "text" else None,
                                                 ptt_port=self.config.ptt_port,
                                                 success=False, reason=str(exc)))
                except (ValueError, OSError, sqlite3.DatabaseError) as history_error:
                    self.failed.emit(f"{exc}；历史写入也失败：{history_error}")
                    return
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(output))
