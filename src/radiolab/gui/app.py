"""SQLite 历史与离线 SSTV 解码窗口；启动时不触碰音频、串口或 PTT。"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QTableWidgetItem

from radiolab.audio import AudioDevice, AudioLevel
from radiolab.afsk import BAUD
from radiolab.config import load_config, validate_on_air
from radiolab.packet import encode_text
from radiolab.storage import SQLiteHistoryService
from .gallery import ImageGalleryDialog
from .config_editor import ConfigEditorDialog
from .history_worker import HistoryExportWorker, SSTVDecodeWorker
from .window_layout import WindowLayout
from .recent import RecentFilesDialog
from .rx_worker import DeviceProbeWorker, RecordWorker, ReplayWorker, TextDecodeWorker, TextListenWorker
from .tx_worker import OnAirWorker, SSTVEncodeWorker, TextEncodeWorker


class HistoryWindow(WindowLayout, QMainWindow):
    def __init__(self, database: str | Path, source: str | Path,
                 config_path: str | Path | None = None) -> None:
        super().__init__()
        self.store = SQLiteHistoryService(database)
        self.source = Path(source)
        self.worker: SSTVDecodeWorker | None = None
        self.probe_worker: DeviceProbeWorker | None = None
        self.record_worker: RecordWorker | None = None
        self.listen_worker: TextListenWorker | None = None
        self.replay_worker: ReplayWorker | None = None
        self.text_worker: TextDecodeWorker | None = None
        self.tx_worker: TextEncodeWorker | SSTVEncodeWorker | OnAirWorker | None = None
        self.export_worker: HistoryExportWorker | None = None
        self._close_after_record = False
        self._close_after_listen = False
        self._close_after_replay = False
        self._close_after_tx = False
        self._close_after_export = False
        self.build_layout(config_path)

    def set_tx_busy(self, busy: bool) -> None:
        self.encode_button.setEnabled(not busy)
        self.sstv_encode_button.setEnabled(not busy)
        self.air_check.setEnabled(not busy)
        enabled_air = not busy and self.air_check.isChecked()
        self.air_text_button.setEnabled(enabled_air)
        self.air_sstv_button.setEnabled(enabled_air)
        self.abort_tx_button.setEnabled(busy and isinstance(self.tx_worker, OnAirWorker))

    def air_toggle_changed(self, _checked: bool) -> None:
        self.set_tx_busy(self.tx_worker is not None and self.tx_worker.isRunning())

    def open_config_editor(self) -> None:
        if self.tx_worker is not None and self.tx_worker.isRunning():
            self.air_status.setText("发送任务进行中，结束后再编辑设备配置")
            return
        chosen = self.config_input.text().strip()
        path = Path(chosen).expanduser() if chosen else self.store.path.parent / "config.toml"
        try:
            dialog = ConfigEditorDialog(path, self.tx_choice.currentData(),
                                        self.rx_choice.currentData(), self.ptt_choice.currentData(), self)
        except (ValueError, OSError) as exc:
            self.air_status.setText(f"无法读取配置：{exc}")
            return
        if dialog.exec() and dialog.saved_path is not None:
            self.config_input.setText(str(dialog.saved_path))
            self.air_check.setChecked(False)
            self.air_status.setText(f"配置已保存：{dialog.saved_path}；尚未打开设备，真实发射仍需逐次确认")

    def start_on_air_text(self) -> None:
        self.start_on_air("text")

    def start_on_air_sstv(self) -> None:
        self.start_on_air("sstv")

    def start_text_encode(self) -> None:
        if self.tx_worker is not None and self.tx_worker.isRunning():
            self.tx_status.setText("文本 WAV 正在生成")
            return
        supplied = self.tx_output.text().strip()
        path = (Path(supplied).expanduser() if supplied else self.store.path.parent / "encoded" /
                f"text-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.wav")
        if path.suffix.lower() != ".wav" or path.is_dir():
            self.tx_status.setText(f"文本输出须为 .wav 文件：{path}")
            return
        if path.exists():
            self.tx_status.setText(f"目标 WAV 已存在，请换路径以保留原件：{path}")
            return
        self.tx_worker = TextEncodeWorker(self.tx_message.text(), self.tx_sequence.value(), path, self.store)
        self.tx_worker.succeeded.connect(self.text_encode_succeeded)
        self.tx_worker.failed.connect(self.text_encode_failed)
        self.tx_worker.finished.connect(self.text_encode_finished)
        self.set_tx_busy(True)
        self.tx_status.setText("正在生成 AFSK WAV；不会播放或操作 PTT")
        self.tx_worker.start()

    def text_encode_succeeded(self, path: str) -> None:
        self.refresh_history()
        self.tx_status.setText(f"文本 WAV 已生成：{path}；未发射")

    def text_encode_failed(self, error: str) -> None:
        self.refresh_history()
        self.tx_status.setText(f"文本 WAV 生成失败：{error}")

    def text_encode_finished(self) -> None:
        if isinstance(self.tx_worker, OnAirWorker):
            self.air_check.setChecked(False)
        self.set_tx_busy(False)
        if self._close_after_tx:
            QTimer.singleShot(0, self.close)

    def start_sstv_encode(self) -> None:
        if self.tx_worker is not None and self.tx_worker.isRunning():
            self.sstv_status.setText("已有发送侧编码任务正在进行")
            return
        image = Path(self.sstv_image.text().strip()).expanduser()
        if not image.is_file():
            self.sstv_status.setText(f"图片不存在：{image}；先选择实际图片文件")
            return
        supplied = self.sstv_output.text().strip()
        path = (Path(supplied).expanduser() if supplied else self.store.path.parent / "encoded" /
                f"sstv-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.wav")
        if path.suffix.lower() != ".wav" or path.is_dir():
            self.sstv_status.setText(f"图像音频输出须为 .wav 文件：{path}")
            return
        if path.exists():
            self.sstv_status.setText(f"目标 WAV 已存在，请换路径以保留原件：{path}")
            return
        self.tx_worker = SSTVEncodeWorker(image, path, self.store)
        self.tx_worker.succeeded.connect(self.sstv_encode_succeeded)
        self.tx_worker.failed.connect(self.sstv_encode_failed)
        self.tx_worker.finished.connect(self.text_encode_finished)
        self.set_tx_busy(True)
        self.sstv_status.setText("正在生成 Robot36 WAV；不会播放或操作 PTT")
        self.tx_worker.start()

    def sstv_encode_succeeded(self, path: str) -> None:
        self.refresh_history()
        self.sstv_status.setText(f"Robot36 WAV 已生成：{path}；未发射")

    def sstv_encode_failed(self, error: str) -> None:
        self.refresh_history()
        self.sstv_status.setText(f"Robot36 WAV 生成失败：{error}")

    def open_image_gallery(self) -> None:
        try:
            gallery = ImageGalleryDialog(self.store, self)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            self.sstv_status.setText(f"图片库读取失败：{exc}")
            return
        if gallery.exec() and gallery.selected_path is not None:
            self.sstv_image.setText(str(gallery.selected_path))
            self.show_image_path(gallery.selected_path)
            self.sstv_status.setText(f"已选择图片：{gallery.selected_path}；尚未编码或发射")

    def open_recent_files(self) -> None:
        try:
            dialog = RecentFilesDialog(self.store, self)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            self.status.setText(f"最近文件读取失败：{exc}")
            return
        dialog.exec()

    def start_on_air(self, mode: str) -> None:
        if self.tx_worker is not None and self.tx_worker.isRunning():
            self.air_status.setText("已有发送任务正在执行")
            return
        if self.replay_worker is not None and self.replay_worker.isRunning():
            self.air_status.setText("先停止本地回放，再执行真实发射")
            return
        if self.listen_worker is not None and self.listen_worker.isRunning():
            self.air_status.setText("先停止自动监听，再执行真实发射")
            return
        if not self.air_check.isChecked():
            self.air_status.setText("真实发射需要先勾选本次操作确认")
            return
        output = self.tx_choice.currentData()
        port = self.ptt_choice.currentData()
        if not isinstance(output, AudioDevice) or not isinstance(port, str):
            self.air_status.setText("先刷新设备并分别选择 TX 音频输出与 PTT 串口")
            return
        path_text = self.config_input.text().strip()
        if not path_text:
            self.air_status.setText("真实发射需要指定经台架确认的 config.toml")
            return
        try:
            config = load_config(path_text)
            validate_on_air(config)
            if str(config.tx_device) not in (str(output.index), output.name):
                raise ValueError("所选 TX 输出与配置 audio.tx_device 不一致")
            if config.ptt_port.casefold() != port.casefold():
                raise ValueError("所选 PTT 串口与配置 ptt.port 不一致")
            if mode == "text":
                frame = encode_text(self.tx_message.text(), self.tx_sequence.value())
                duration = f"约 {len(frame) * 8 / BAUD + config.pre_delay_s + config.tail_delay_s:.1f} s"
                supplied = self.tx_output.text().strip()
                image = None
            elif mode == "sstv":
                image = Path(self.sstv_image.text().strip()).expanduser()
                if not image.is_file():
                    raise ValueError(f"图片不存在：{image}")
                duration = "约 37 s；编码完成后仍会检查 60 s 上限"
                supplied = self.sstv_output.text().strip()
            else:
                raise ValueError(f"不支持的发射模式：{mode}")
            path = (Path(supplied).expanduser() if supplied else self.store.path.parent / "transmissions" /
                    f"{mode}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.wav")
            if path.suffix.lower() != ".wav" or path.is_dir() or path.exists():
                raise ValueError(f"发射 WAV 需是尚不存在的 .wav 文件：{path}")
        except (ValueError, OSError) as exc:
            self.air_status.setText(f"真实发射预检失败：{exc}")
            return
        summary = (f"此操作会真的按下串口 PTT 并播放 PCM。\n模式：{mode}；预计时长：{duration}\n"
                   f"TX 输出：{output.index}: {output.name}\nPTT 串口：{port}\n"
                   f"配置：{path_text}\n音频增益：{config.gain}；连续发射上限：{config.max_tx_seconds} s\n"
                   f"手台频率（仅记录）：{config.frequency_hz or '未填'} Hz；功率档位：{config.power_setting or '未填'}\n"
                   "请再次确认手台设置、操作权限和无辐射 PTT 台架结果。继续真实发射？")
        answer = QMessageBox.question(self, "确认真实发射", summary,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            self.air_status.setText("已取消；未打开串口，也未发射")
            return
        self.tx_worker = OnAirWorker(mode, path, config, output, self.store,
                                     message=self.tx_message.text(), sequence=self.tx_sequence.value(),
                                     image=image)
        self.tx_worker.status.connect(self.air_status.setText)
        self.tx_worker.succeeded.connect(self.on_air_succeeded)
        self.tx_worker.failed.connect(self.on_air_failed)
        self.tx_worker.finished.connect(self.text_encode_finished)
        self.set_tx_busy(True)
        self.air_status.setText("已确认；后台任务正在再次检查设备，尚未按下 PTT")
        self.tx_worker.start()

    def abort_on_air(self) -> None:
        if isinstance(self.tx_worker, OnAirWorker) and self.tx_worker.isRunning():
            self.tx_worker.stop()
            self.air_status.setText("已请求停止发射；正在等待音频停止并释放 PTT")

    def on_air_succeeded(self, path: str) -> None:
        self.refresh_history()
        self.air_status.setText(f"受控发射事务结束：{path}；接收端结果仍需独立验证")

    def on_air_failed(self, error: str) -> None:
        self.refresh_history()
        self.air_status.setText(f"发射任务失败：{error}；请检查电台 PTT 状态")

    def refresh_devices(self) -> None:
        if self.probe_worker is not None and self.probe_worker.isRunning():
            return
        self.tx_choice.clear()
        self.tx_choice.addItem("检查中", None)
        self.rx_choice.clear()
        self.rx_choice.addItem("检查中", None)
        self.ptt_choice.clear()
        self.ptt_choice.addItem("检查中", None)
        self.local_choice.clear()
        self.local_choice.addItem("检查中", None)
        self.refresh_devices_button.setEnabled(False)
        self.device_status.setText("正在检查音频设备和串口；不打开端口")
        self.probe_worker = DeviceProbeWorker()
        self.probe_worker.ready.connect(self.devices_ready)
        self.probe_worker.finished.connect(self.probe_finished)
        self.probe_worker.start()

    def devices_ready(self, audio_devices: list[AudioDevice], ports: list[tuple[str, str]],
                      audio_error: str, serial_error: str) -> None:
        self.tx_choice.clear()
        self.tx_choice.addItem("选择 TX 输出", None)
        self.rx_choice.clear()
        self.rx_choice.addItem("选择 RX 输入", None)
        self.local_choice.clear()
        self.local_choice.addItem("选择本地输出", None)
        for device in audio_devices:
            if device.outputs:
                self.tx_choice.addItem(f"{device.index}: {device.name}", device)
                self.local_choice.addItem(f"{device.index}: {device.name}", device)
            if device.inputs:
                self.rx_choice.addItem(f"{device.index}: {device.name}", device)
        self.ptt_choice.clear()
        self.ptt_choice.addItem("选择 PTT 串口", None)
        for port, description in ports:
            self.ptt_choice.addItem(f"{port}: {description}", port)
        errors = "; ".join(error for error in (audio_error, serial_error) if error)
        summary = f"输入 {self.rx_choice.count() - 1}、输出 {self.tx_choice.count() - 1}、串口 {len(ports)}；仅枚举"
        self.device_status.setText(f"{summary}；{errors}" if errors else summary)

    def probe_finished(self) -> None:
        self.refresh_devices_button.setEnabled(True)

    def start_record(self) -> None:
        if self.record_worker is not None and self.record_worker.isRunning():
            self.record_status.setText("录音正在进行")
            return
        if self.replay_worker is not None and self.replay_worker.isRunning():
            self.record_status.setText("先停止本地回放，再开始录音")
            return
        if self.listen_worker is not None and self.listen_worker.isRunning():
            self.record_status.setText("先停止自动监听，再开始手动录音")
            return
        device = self.rx_choice.currentData()
        if not isinstance(device, AudioDevice):
            self.record_status.setText("先刷新设备并明确选择 RX 输入")
            return
        supplied = self.record_output.text().strip()
        path = (Path(supplied).expanduser() if supplied else self.store.path.parent / "recordings" /
                f"rx-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.wav")
        if path.suffix.lower() != ".wav" or path.is_dir():
            self.record_status.setText(f"录音输出须为 .wav 文件：{path}")
            return
        if path.exists():
            self.record_status.setText(f"录音目标已存在，请换一个路径以保留原始 WAV：{path}")
            return
        self.record_worker = RecordWorker(device, self.record_seconds.value(), path, self.store)
        self.record_worker.succeeded.connect(self.record_succeeded)
        self.record_worker.failed.connect(self.record_failed)
        self.record_worker.finished.connect(self.record_finished)
        self.record_button.setEnabled(False)
        self.stop_record_button.setEnabled(True)
        self.record_status.setText(f"正在录音：{device.name} → {path}")
        self.record_worker.start()

    def stop_record(self) -> None:
        if self.record_worker is None or not self.record_worker.isRunning():
            self.record_status.setText("当前没有正在进行的录音")
            return
        self.record_worker.stop()
        self.record_status.setText("正在停止录音并保存已采集的 PCM")

    def record_succeeded(self, path: str) -> None:
        self.refresh_history()
        self.record_status.setText(f"录音已保存：{path}；可在历史中选择 WAV 解码")

    def record_failed(self, error: str) -> None:
        self.refresh_history()
        self.record_status.setText(f"录音失败：{error}")

    def record_finished(self) -> None:
        self.record_button.setEnabled(True)
        self.stop_record_button.setEnabled(False)
        if self._close_after_record:
            QTimer.singleShot(0, self.close)

    def start_text_listen(self) -> None:
        if self.listen_worker is not None and self.listen_worker.isRunning():
            self.listen_status.setText("文本监听正在进行")
            return
        if self.record_worker is not None and self.record_worker.isRunning():
            self.listen_status.setText("先停止手动录音，再开始文本监听")
            return
        if self.replay_worker is not None and self.replay_worker.isRunning():
            self.listen_status.setText("先停止本地回放，再开始文本监听")
            return
        if isinstance(self.tx_worker, OnAirWorker) and self.tx_worker.isRunning():
            self.listen_status.setText("真实发射进行中，不能同时自动监听")
            return
        device = self.rx_choice.currentData()
        if not isinstance(device, AudioDevice):
            self.listen_status.setText("先刷新设备并明确选择 RX 输入")
            return
        path = (self.store.path.parent / "recordings" /
                f"listen-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}.wav")
        self.listen_worker = TextListenWorker(device, self.record_seconds.value(), path, self.store)
        self.listen_worker.frame_received.connect(self.listen_frame_received)
        self.listen_worker.level.connect(self.listen_level)
        self.listen_worker.succeeded.connect(self.listen_succeeded)
        self.listen_worker.failed.connect(self.listen_failed)
        self.listen_worker.finished.connect(self.listen_finished)
        self.listen_button.setEnabled(False)
        self.stop_listen_button.setEnabled(True)
        self.rx_level_bar.setValue(0)
        self.rx_level_text.setText("RX 输入电平：正在测量；仅用于检查接线和输入增益")
        self.listen_status.setText(f"正在监听 {device.name}；有效帧将自动写入历史，录音：{path}")
        self.listen_worker.start()

    def stop_text_listen(self) -> None:
        if self.listen_worker is not None and self.listen_worker.isRunning():
            self.listen_worker.stop()
            self.listen_status.setText("已请求停止监听；正在保存原始 WAV")

    def listen_frame_received(self, message: str, sequence: int) -> None:
        self.refresh_history()
        self.listen_status.setText(f"文本接收成功（CRC 通过，序号 {sequence}）：{message}")

    def listen_level(self, level: AudioLevel) -> None:
        peak = max(-80.0, min(0.0, level.peak_dbfs))
        self.rx_level_bar.setValue(round(peak + 80))
        warning = ("接近削波，请降低输入增益" if level.peak_dbfs > -1 or level.clipped_fraction > 0
                   else "电平偏低，请检查 RX 输入" if level.low_level else "输入正常")
        self.rx_level_text.setText(
            f"RX 峰值 {level.peak_dbfs:.1f} dBFS · RMS {level.rms_dbfs:.1f} dBFS；{warning}；CRC 才判定有效帧")

    def listen_succeeded(self, path: str, count: int) -> None:
        self.refresh_history()
        self.listen_status.setText(f"监听结束：有效文本帧 {count} 条；录音已保存：{path}")

    def listen_failed(self, error: str) -> None:
        self.refresh_history()
        self.listen_status.setText(f"文本监听失败：{error}")

    def listen_finished(self) -> None:
        self.listen_button.setEnabled(True)
        self.stop_listen_button.setEnabled(False)
        if self._close_after_listen:
            QTimer.singleShot(0, self.close)

    def start_replay_selected(self) -> None:
        if self.replay_worker is not None and self.replay_worker.isRunning():
            self.replay_status.setText("本地回放正在进行")
            return
        if self.record_worker is not None and self.record_worker.isRunning():
            self.replay_status.setText("先停止录音，再回放本地 WAV")
            return
        if self.listen_worker is not None and self.listen_worker.isRunning():
            self.replay_status.setText("先停止自动监听，再回放本地 WAV")
            return
        if isinstance(self.tx_worker, OnAirWorker) and self.tx_worker.isRunning():
            self.replay_status.setText("真实发射进行中，不能同时本地回放")
            return
        output = self.local_choice.currentData()
        if not isinstance(output, AudioDevice):
            self.replay_status.setText("先刷新设备并明确选择本地扬声器或耳机")
            return
        tx_output = self.tx_choice.currentData()
        if isinstance(tx_output, AudioDevice) and tx_output == output:
            self.replay_status.setText("本地回放输出与已选 TX 输出相同；请选择独立的扬声器或耳机")
            return
        if not self.replay_check.isChecked():
            self.replay_status.setText("先确认所选输出未连接电台或 VOX")
            return
        row = self.table.currentRow()
        item = self.table.item(row, 5) if row >= 0 else None
        wav = Path(item.data(Qt.ItemDataRole.UserRole + 1)) if item and item.data(Qt.ItemDataRole.UserRole + 1) else None
        if wav is None or not wav.is_file():
            self.replay_status.setText("先选择一条 WAV 文件存在的历史记录")
            return
        question = (f"即将以增益 {self.replay_gain.value():.2f} 在 {output.index}: {output.name} 播放\n"
                    f"{wav}\n请确认输出没有连接电台或 VOX，系统音量已调低。继续本地回放？")
        answer = QMessageBox.question(self, "确认本地回放", question,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            self.replay_status.setText("已取消本地回放；未打开输出设备")
            return
        self.replay_worker = ReplayWorker(wav, output, self.replay_gain.value(), self.store)
        self.replay_worker.succeeded.connect(self.replay_succeeded)
        self.replay_worker.failed.connect(self.replay_failed)
        self.replay_worker.finished.connect(self.replay_finished)
        self.replay_button.setEnabled(False)
        self.stop_replay_button.setEnabled(True)
        self.replay_check.setEnabled(False)
        self.replay_status.setText(f"正在本地回放：{output.name}；不操作 PTT")
        self.replay_worker.start()

    def stop_replay(self) -> None:
        if self.replay_worker is not None and self.replay_worker.isRunning():
            self.replay_worker.stop()
            self.replay_status.setText("已请求停止本地回放")

    def replay_succeeded(self, duration: float) -> None:
        self.refresh_history()
        self.replay_status.setText(f"本地回放完成：{duration:.2f} s；未操作 PTT")

    def replay_failed(self, error: str) -> None:
        self.refresh_history()
        self.replay_status.setText(f"本地回放失败或已停止：{error}")

    def replay_finished(self) -> None:
        self.replay_button.setEnabled(True)
        self.stop_replay_button.setEnabled(False)
        self.replay_check.setEnabled(True)
        self.replay_check.setChecked(False)
        if self._close_after_replay:
            QTimer.singleShot(0, self.close)

    def refresh_history(self) -> None:
        try:
            rows = self.store.list_recent(limit=500, mode=self.mode.currentData(),
                                          search=self.search.text().strip() or None)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            self.status.setText(f"读取历史失败：{exc}")
            return
        self.table.setRowCount(0)
        for entry in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            wav = entry.wav_path or ""
            image = entry.image_path or ""
            report = entry.report_path or ""
            try:
                local_time = datetime.fromisoformat(entry.local_time or entry.time).astimezone(
                    timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                local_time = entry.local_time or entry.time
            decoder = f"{entry.decoder_name} {entry.decoder_version or 'unknown'}" if entry.decoder_name else ""
            values = (local_time, entry.mode, entry.operation,
                      "成功" if entry.success else "失败", entry.text or entry.reason or "",
                      wav + (" [缺失]" if wav and not Path(wav).is_file() else ""),
                      image + (" [缺失]" if image and not Path(image).is_file() else ""), decoder,
                      report + (" [缺失]" if report and not Path(report).is_file() else ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, entry.run_id)
                if column in (5, 6, 8):
                    item.setData(Qt.ItemDataRole.UserRole + 1, (wav, image, report)[(5, 6, 8).index(column)])
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.status.setText(f"显示 {len(rows)} 条 · {self.store.path}" if self.store.path.is_file()
                            else "SQLite 历史尚不存在；可从 JSONL 导入")

    def show_selected_image(self, row: int, *_: int) -> None:
        item = self.table.item(row, 6) if row >= 0 else None
        path = Path(item.data(Qt.ItemDataRole.UserRole + 1)) if item and item.data(Qt.ItemDataRole.UserRole + 1) else None
        if path is None or not path.is_file():
            self.preview.setPixmap(QPixmap())
            self.preview.hide()
            return
        if path.stat().st_size > 20_000_000:
            self.preview.setText("图片超过 20 MB；请从文件路径查看")
            self.preview.show()
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview.setText(f"无法预览图片：{path}")
            self.preview.show()
            return
        self.preview.setPixmap(pixmap.scaled(320, 240, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        self.preview.setToolTip(str(path))
        self.preview.show()

    def start_decode_selected(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("SSTV 解码正在进行，请等待结果")
            return
        row = self.table.currentRow()
        item = self.table.item(row, 5) if row >= 0 else None
        wav = Path(item.data(Qt.ItemDataRole.UserRole + 1)) if item and item.data(Qt.ItemDataRole.UserRole + 1) else None
        if wav is None or not wav.is_file():
            self.status.setText("先选择一条 WAV 文件存在的记录")
            return
        output = self.store.path.parent / "decoded" / f"{wav.stem[:48]}-{uuid4().hex[:8]}.png"
        self.worker = SSTVDecodeWorker(wav, output, self.store)
        self.worker.succeeded.connect(self.decode_succeeded)
        self.worker.failed.connect(self.decode_failed)
        self.worker.finished.connect(self.decode_finished)
        self.decode_button.setEnabled(False)
        self.status.setText(f"正在从 WAV 解码 Robot36：{wav}")
        self.worker.start()

    def start_text_decode_selected(self) -> None:
        if self.text_worker is not None and self.text_worker.isRunning():
            self.status.setText("文本解码正在进行，请等待结果")
            return
        row = self.table.currentRow()
        item = self.table.item(row, 5) if row >= 0 else None
        wav = Path(item.data(Qt.ItemDataRole.UserRole + 1)) if item and item.data(Qt.ItemDataRole.UserRole + 1) else None
        if wav is None or not wav.is_file():
            self.status.setText("先选择一条 WAV 文件存在的记录")
            return
        self.text_worker = TextDecodeWorker(wav, self.store)
        self.text_worker.succeeded.connect(self.text_decode_succeeded)
        self.text_worker.failed.connect(self.text_decode_failed)
        self.text_worker.finished.connect(self.text_decode_finished)
        self.text_decode_button.setEnabled(False)
        self.status.setText(f"正在从 WAV 解码文本：{wav}")
        self.text_worker.start()

    def text_decode_succeeded(self, message: str, sequence: int) -> None:
        self.refresh_history()
        self.status.setText(f"文本接收成功（CRC 通过，序号 {sequence}）：{message}")

    def text_decode_failed(self, error: str) -> None:
        self.refresh_history()
        self.status.setText(f"文本解码失败：{error}")

    def text_decode_finished(self) -> None:
        self.text_decode_button.setEnabled(True)

    def decode_succeeded(self, image: str, report: str) -> None:
        self.refresh_history()
        self.show_image_path(Path(image))
        self.status.setText(f"已解码：{image}；日志：{report}")

    def decode_failed(self, error: str) -> None:
        self.refresh_history()
        self.status.setText(f"SSTV 解码失败：{error}；日志如已生成可在历史中查看")

    def decode_finished(self) -> None:
        self.decode_button.setEnabled(True)

    def show_image_path(self, path: Path) -> None:
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self.preview.setPixmap(pixmap.scaled(320, 240, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
                self.preview.setToolTip(str(path))
                self.preview.show()

    def closeEvent(self, event) -> None:
        if isinstance(self.tx_worker, OnAirWorker) and self.tx_worker.isRunning():
            self.tx_worker.stop()
            self._close_after_tx = True
            self.air_status.setText("正在停止发射并释放 PTT，完成后关闭窗口")
            if self.record_worker is not None and self.record_worker.isRunning():
                self.record_worker.stop()
                self._close_after_record = True
            event.ignore()
            return
        if self.replay_worker is not None and self.replay_worker.isRunning():
            self.replay_worker.stop()
            self._close_after_replay = True
            self.replay_status.setText("正在停止本地回放，完成后关闭窗口")
            event.ignore()
            return
        if self.record_worker is not None and self.record_worker.isRunning():
            self.record_worker.stop()
            self._close_after_record = True
            self.record_status.setText("正在停止录音并保存 WAV，完成后关闭窗口")
            event.ignore()
            return
        if self.listen_worker is not None and self.listen_worker.isRunning():
            self.listen_worker.stop()
            self._close_after_listen = True
            self.listen_status.setText("正在停止监听并保存 WAV，完成后关闭窗口")
            event.ignore()
            return
        if self.probe_worker is not None and self.probe_worker.isRunning():
            self.device_status.setText("设备检查仍在运行，完成后可关闭")
            event.ignore()
            return
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("解码任务仍在运行；完成后再关闭窗口")
            event.ignore()
            return
        if self.text_worker is not None and self.text_worker.isRunning():
            self.status.setText("文本解码任务仍在运行；完成后再关闭窗口")
            event.ignore()
            return
        if self.tx_worker is not None and self.tx_worker.isRunning():
            self.tx_status.setText("WAV 生成仍在运行；完成后再关闭窗口")
            event.ignore()
            return
        if self.export_worker is not None and self.export_worker.isRunning():
            self._close_after_export = True
            self.status.setText("历史 ZIP 正在导出；完成后关闭窗口")
            event.ignore()
            return
        super().closeEvent(event)

    def migrate_history(self) -> None:
        try:
            count = self.store.migrate_jsonl(self.source)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.refresh_history()
        self.status.setText(f"导入 {count} 条；JSONL 原文件保留：{self.source}")

    def start_history_export(self) -> None:
        if self.export_worker is not None and self.export_worker.isRunning():
            self.status.setText("历史 ZIP 正在导出")
            return
        active = (self.record_worker, self.listen_worker, self.replay_worker,
                  self.tx_worker, self.worker, self.text_worker)
        if any(worker is not None and worker.isRunning() for worker in active):
            self.status.setText("先完成正在运行的收发、回放或解码任务，再导出历史")
            return
        if not self.store.path.is_file():
            self.status.setText("SQLite 历史尚不存在；先生成记录或从 JSONL 导入")
            return
        suggested = str(self.store.path.parent / "radiolab-history.zip")
        chosen, _ = QFileDialog.getSaveFileName(self, "导出历史 ZIP", suggested, "ZIP 文件 (*.zip)")
        if not chosen:
            self.status.setText("已取消历史导出")
            return
        output = Path(chosen).expanduser()
        if output.suffix.lower() != ".zip" or output.is_dir() or output.exists():
            self.status.setText(f"导出目标必须是尚不存在的 .zip 文件：{output}")
            return
        answer = QMessageBox.question(
            self, "确认导出历史",
            f"将导出全部 SQLite 历史及现存 WAV、图片和日志到：\n{output}\n"
            "ZIP 含文本内容和原始文件路径，可能涉及隐私。确认导出？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("已取消历史导出；未创建 ZIP")
            return
        self.export_worker = HistoryExportWorker(self.store, output)
        self.export_worker.succeeded.connect(self.history_export_succeeded)
        self.export_worker.failed.connect(self.history_export_failed)
        self.export_worker.finished.connect(self.history_export_finished)
        self.export_button.setEnabled(False)
        self.status.setText("正在后台导出历史 ZIP；原始数据库和媒体保持不变")
        self.export_worker.start()

    def history_export_succeeded(self, path: str, entries: int, copied: int, missing: int) -> None:
        self.status.setText(f"历史已导出：{path}；记录 {entries} 条，媒体 {copied} 个，缺失 {missing} 个")

    def history_export_failed(self, error: str) -> None:
        self.status.setText(f"历史导出失败：{error}")

    def history_export_finished(self) -> None:
        self.export_button.setEnabled(True)
        if self._close_after_export:
            QTimer.singleShot(0, self.close)

    def copy_selected_cell(self) -> None:
        item = self.table.currentItem()
        if item is None:
            self.status.setText("先选中表格中的一个单元格")
            return
        QApplication.clipboard().setText(item.text())
        self.status.setText("已复制选中单元格")


def run(database: str | Path, source: str | Path, config_path: str | Path | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families() and os.name == "nt":
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
        if font_path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                app.setFont(QFont(families[0], 10))
    window = HistoryWindow(database, source, config_path)
    window.show()
    return app.exec()
