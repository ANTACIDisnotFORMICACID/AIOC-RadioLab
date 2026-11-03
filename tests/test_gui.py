from pathlib import Path
from threading import Event

import numpy as np
import pytest

from radiolab.history import make_entry
from radiolab.storage import SQLiteHistoryService


_APP = None
_WINDOWS = []


def _qapp():
    """GUI 测试共用一个 Qt 应用，避免测试间销毁/重建原生事件循环。"""
    global _APP
    from PySide6.QtWidgets import QApplication

    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _ready_air_config(tmp_path, *, tx_device: str = "7") -> Path:
    path = tmp_path / "air.toml"
    path.write_text(f"""
[audio]
tx_device = "{tx_device}"
gain = 0.1
[ptt]
port = "COM9"
firmware = "1.2+"
bench_verified = true
pre_delay_s = 0.0
tail_delay_s = 0.0
max_tx_seconds = 5.0
[safety]
legal_operation_confirmed = true
""", encoding="utf-8")
    return path


def test_gui_editor_saves_selected_devices_without_starting_rf(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from radiolab.audio import AudioDevice
    from radiolab.config import load_config, validate_on_air
    from radiolab.gui.app import HistoryWindow
    from radiolab.gui.config_editor import ConfigEditorDialog
    from PySide6.QtWidgets import QMessageBox

    _qapp()
    window = HistoryWindow(tmp_path / "history.sqlite3", tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    tx = AudioDevice(7, "Fake TX", 0, 1, 48000)
    rx = AudioDevice(8, "Fake RX", 1, 0, 48000)
    window.devices_ready([tx, rx], [("COM9", "fake")], "", "")
    window.tx_choice.setCurrentIndex(1)
    window.rx_choice.setCurrentIndex(1)
    window.ptt_choice.setCurrentIndex(1)
    window.air_check.setChecked(True)
    monkeypatch.setattr(ConfigEditorDialog, "exec", lambda self: (self.save() or self.result()))
    window.open_config_editor()
    path = tmp_path / "config.toml"
    assert window.config_input.text() == str(path)
    config = load_config(path)
    assert (config.tx_device, config.rx_device, config.ptt_port) == ("Fake TX", "Fake RX", "COM9")
    assert not config.ptt_bench_verified and not config.legal_operation_confirmed
    assert not window.air_check.isChecked()
    assert not window.air_text_button.isEnabled()

    dialog = ConfigEditorDialog(path, tx, rx, "COM9", window)
    _WINDOWS.append(dialog)
    dialog.firmware.setText("1.2+")
    dialog.bench.setChecked(True)
    dialog.legal.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog.save()
    validate_on_air(load_config(path))
    assert len(list(tmp_path.glob("config.toml.*.bak"))) == 1
    bad_path = tmp_path / "bad.toml"
    bad_path.write_text("[audio]\ngain = 'fast'\n", encoding="utf-8")
    window.config_input.setText(str(bad_path))
    window.open_config_editor()
    assert "无法读取配置" in window.air_status.text()


def test_gui_ignores_legacy_external_sstv_settings(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from radiolab.gui import app as gui_app

    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    with store._connect() as connection:
        connection.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_settings VALUES ('sstv_decoder', 'old.exe')")
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    assert not hasattr(window, "decoder")
    assert not hasattr(window, "choose_decoder_button")
    window.close()


def test_history_window_reads_sqlite_without_hardware(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.gui.app import HistoryWindow

    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("text", "decode", text="HELLO GUI"))
    store.append(make_entry("sstv", "decode", decoder_name="Open-SSTV", decoder_version="0.6.17"))
    app = _qapp()
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    assert window.table.rowCount() == 2
    assert not window.air_check.isChecked()
    assert not window.air_text_button.isEnabled() and not window.air_sstv_button.isEnabled()
    assert "Open-SSTV 0.6.17" in [window.table.item(row, 7).text() for row in range(2)]
    window.search.setText("OTHER")
    window.refresh_history()
    assert window.table.rowCount() == 0
    window.search.setText("HELLO")
    window.refresh_history()
    assert window.table.rowCount() == 1
    window.close()


def test_gui_decodes_selected_wav_in_worker_and_previews_image(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PIL import Image
    from PySide6.QtWidgets import QApplication
    from radiolab.gui.app import HistoryWindow
    from radiolab import sstv

    wav = tmp_path / "received.wav"
    wav.write_bytes(b"fake WAV for injected decoder")
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("audio", "record", wav_path=wav))
    def fake_decode(source, output, *, mode, report_path):
        assert source == wav and mode == "robot36"
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 240), "red").save(output)
        Path(report_path).write_text("mode=robot_36, vis=0x08", encoding="utf-8")
        return Path(output)

    monkeypatch.setattr(sstv, "decode_sstv_file", fake_decode)
    app = _qapp()
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.table.setCurrentCell(0, 5)
    window.start_decode_selected()
    assert window.worker is not None
    assert window.worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = store.list_recent()[0]
    assert row.mode == "sstv" and row.success and row.image_exists and row.report_exists
    assert window.table.rowCount() == 2
    assert window.preview.pixmap() is not None and not window.preview.pixmap().isNull()
    assert not window.preview.isHidden()
    assert "已解码" in window.status.text()
    window.close()


def test_gui_decoder_failure_is_recorded_without_old_image(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.gui.app import HistoryWindow
    from radiolab import sstv

    wav = tmp_path / "received.wav"
    wav.write_bytes(b"fake WAV for injected decoder")
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("audio", "record", wav_path=wav))
    monkeypatch.setattr(sstv, "decode_sstv_file", lambda *args, **kwargs:
                        (_ for _ in ()).throw(ValueError("VIS header not found")))
    app = _qapp()
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.table.setCurrentCell(0, 5)
    window.start_decode_selected()
    assert window.worker is not None
    assert window.worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = store.list_recent()[0]
    assert row.mode == "sstv" and not row.success and row.image_path is None
    assert "VIS header not found" in window.status.text()
    window.close()


def test_gui_enumerates_rx_tx_and_serial_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.audio import AudioDevice
    from radiolab.gui.app import HistoryWindow
    from radiolab.gui import rx_worker

    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: (
        [AudioDevice(3, "RX microphone", 1, 0, 48000),
         AudioDevice(7, "TX USB audio", 0, 1, 48000)], None))
    monkeypatch.setattr(rx_worker, "list_serial_ports", lambda: ([("COM9", "fake PTT")], None))
    app = _qapp()
    window = HistoryWindow(tmp_path / "history.sqlite3", tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    assert window.rx_choice.count() == 1 and window.tx_choice.count() == 1
    window.refresh_devices()
    assert window.probe_worker is not None
    assert window.probe_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    assert window.rx_choice.count() == 2 and window.tx_choice.count() == 2
    assert window.rx_choice.currentData() is None and window.tx_choice.currentData() is None
    assert window.local_choice.count() == 2 and window.local_choice.currentData() is None
    assert window.ptt_choice.itemData(1) == "COM9"
    window.start_record()
    assert window.record_worker is None and "选择 RX 输入" in window.record_status.text()
    window.close()


def test_gui_manual_stop_saves_partial_recording_without_hardware(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.audio import AudioDevice
    from radiolab.gui.app import HistoryWindow
    from radiolab.gui import rx_worker
    from radiolab import service

    selected = AudioDevice(3, "RX microphone", 1, 0, 48000)
    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: ([selected], None))
    started = Event()

    class FakeInput:
        def record_stream(self, seconds, *, device, sample_rate, stop_event):
            assert device == selected and sample_rate == 48000 and seconds == 2
            started.set()
            assert stop_event.wait(2)
            return np.full(2048, 0.05, dtype=np.float32)

    monkeypatch.setattr(service, "SoundDeviceAudio", FakeInput)
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [], "", "")
    window.rx_choice.setCurrentIndex(1)
    window.record_seconds.setValue(2.0)
    output = tmp_path / "manual-rx.wav"
    window.record_output.setText(str(output))
    window.start_record()
    assert started.wait(2)
    window.stop_record()
    assert window.record_worker is not None
    assert window.record_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert row.mode == "audio" and row.operation == "record" and row.success
    assert row.wav_exists and output.is_file()
    assert "录音已保存" in window.record_status.text()
    assert window.table.rowCount() == 1
    window.close()


def test_gui_rejects_changed_input_before_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.audio import AudioDevice
    from radiolab.gui.app import HistoryWindow
    from radiolab.gui import rx_worker

    selected = AudioDevice(3, "RX microphone", 1, 0, 48000)
    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: (
        [AudioDevice(3, "different microphone", 1, 0, 48000)], None))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [], "", "")
    window.rx_choice.setCurrentIndex(1)
    output = tmp_path / "should-not-exist.wav"
    window.record_output.setText(str(output))
    window.start_record()
    assert window.record_worker is not None
    assert window.record_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    assert not output.exists()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert not row.success and not row.wav_exists
    assert "已变化" in window.record_status.text()
    window.close()


def test_gui_manually_decodes_text_wav_without_hardware(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.afsk import modulate_frame
    from radiolab.audio import save_wav
    from radiolab.packet import encode_text
    from radiolab.gui.app import HistoryWindow

    pcm = np.concatenate((np.zeros(137, dtype=np.float32),
                          modulate_frame(encode_text("HELLO GUI", sequence=7))))
    wav = save_wav(tmp_path / "received-text.wav", pcm)
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("audio", "record", wav_path=wav))
    app = _qapp()
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.table.setCurrentCell(0, 5)
    window.start_text_decode_selected()
    assert window.text_worker is not None
    assert window.text_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = store.list_recent()[0]
    assert row.mode == "text" and row.success and row.text == "HELLO GUI"
    assert row.sequence == 7 and row.crc_ok is True
    assert "CRC 通过" in window.status.text()
    window.close()


def test_gui_text_encode_is_offline_and_writes_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.afsk import decode_afsk
    from radiolab.audio import load_wav
    from radiolab.gui.app import HistoryWindow
    from radiolab import service

    monkeypatch.setattr(service, "AIOCSerialPTT", lambda *args, **kwargs:
                        pytest.fail("离线编码触发了 PTT"))
    monkeypatch.setattr(service.SoundDeviceAudio, "play", lambda *args, **kwargs:
                        pytest.fail("离线编码触发了播放"))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    output = tmp_path / "hello.wav"
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.tx_message.setText("HELLO GUI")
    window.tx_sequence.setValue(9)
    window.tx_output.setText(str(output))
    window.start_text_encode()
    assert window.tx_worker is not None
    assert window.tx_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert row.mode == "text" and row.operation == "encode" and row.success
    assert row.text == "HELLO GUI" and row.sequence == 9 and row.wav_exists
    assert decode_afsk(load_wav(output)).frame.text == "HELLO GUI"
    assert "未发射" in window.tx_status.text()
    window.close()


def test_gui_sstv_encode_uses_same_offline_tx_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PIL import Image
    from PySide6.QtWidgets import QApplication
    from radiolab.gui.app import HistoryWindow
    from radiolab.gui import tx_worker
    from radiolab import service

    image = tmp_path / "test.png"
    Image.new("RGB", (320, 240), "red").save(image)
    monkeypatch.setattr(tx_worker, "render_robot36_pcm",
                        lambda image: np.zeros(48000, dtype=np.float32))
    monkeypatch.setattr(service, "AIOCSerialPTT", lambda *args, **kwargs:
                        pytest.fail("离线图像编码触发了 PTT"))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    output = tmp_path / "sstv.wav"
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.sstv_image.setText(str(image))
    window.sstv_output.setText(str(output))
    window.start_sstv_encode()
    assert window.tx_worker is not None
    assert window.tx_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert row.mode == "sstv" and row.operation == "encode" and row.success
    assert row.wav_exists and row.image_exists and output.is_file()
    assert "未发射" in window.sstv_status.text()
    assert window.encode_button.isEnabled() and window.sstv_encode_button.isEnabled()
    window.close()


def test_gui_on_air_requires_all_gates_and_rejection_opens_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice
    from radiolab.gui import app as gui_app

    questions = []

    class RejectMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            questions.append(args)
            return QMessageBox.StandardButton.No

    monkeypatch.setattr(gui_app, "QMessageBox", RejectMessageBox)
    app = _qapp()
    window = gui_app.HistoryWindow(tmp_path / "history.sqlite3", tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.start_on_air("text")
    assert window.tx_worker is None and "勾选" in window.air_status.text()
    selected = AudioDevice(7, "fake TX", 0, 1, 48000)
    window.devices_ready([selected], [("COM9", "fake PTT")], "", "")
    window.tx_choice.setCurrentIndex(1)
    window.ptt_choice.setCurrentIndex(1)
    window.air_check.setChecked(True)
    window.tx_message.setText("TEST")
    window.config_input.setText(str(_ready_air_config(tmp_path, tx_device="wrong")))
    window.start_on_air("text")
    assert "不一致" in window.air_status.text() and not questions
    window.config_input.setText(str(_ready_air_config(tmp_path)))
    window.start_on_air("sstv")
    assert "图片不存在" in window.air_status.text() and not questions
    window.start_on_air("text")
    assert questions and window.tx_worker is None
    assert "未打开串口" in window.air_status.text()
    assert not SQLiteHistoryService(tmp_path / "history.sqlite3").path.exists()
    window.close()


def test_gui_abort_requests_fake_ptt_release(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice
    from radiolab.gui import app as gui_app, tx_worker
    from radiolab.ptt import FakePTT
    from radiolab.service import SendService as CoreSendService

    selected = AudioDevice(7, "fake TX", 0, 1, 48000)
    monkeypatch.setattr(tx_worker, "list_audio_devices", lambda: ([selected], None))
    monkeypatch.setattr(tx_worker, "list_serial_ports", lambda: ([("COM9", "fake PTT")], None))

    class AcceptMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_app, "QMessageBox", AcceptMessageBox)
    started, stopped = Event(), Event()

    class BlockingOutput:
        def validate_output(self, device, sample_rate):
            assert device == selected

        def play(self, samples, *, device, sample_rate, gain):
            started.set()
            assert stopped.wait(2)

        def stop(self):
            stopped.set()

    ptt = FakePTT()
    monkeypatch.setattr(tx_worker, "SendService", lambda history: CoreSendService(
        audio=BlockingOutput(), ptt=ptt, history=history, sleep=lambda _: None))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [("COM9", "fake PTT")], "", "")
    window.tx_choice.setCurrentIndex(1)
    window.ptt_choice.setCurrentIndex(1)
    window.config_input.setText(str(_ready_air_config(tmp_path)))
    window.tx_message.setText("STOP")
    window.air_check.setChecked(True)
    window.start_on_air("text")
    assert started.wait(2)
    app.processEvents()
    assert "正在播放完整音频" in window.air_status.text()
    window.abort_on_air()
    assert isinstance(window.tx_worker, tx_worker.OnAirWorker)
    assert window.tx_worker.wait(3000), "停止请求后发送线程未退出"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert stopped.is_set() and ptt.events == ["key", "release"]
    assert not row.success and "停止发射" in row.reason
    assert not window.air_check.isChecked()
    window.close()


def test_gui_on_air_uses_fake_ptt_and_matching_audio_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice
    from radiolab.gui import app as gui_app, tx_worker
    from radiolab.ptt import FakePTT
    from radiolab.service import SendService as CoreSendService

    selected = AudioDevice(7, "fake TX", 0, 1, 48000)
    monkeypatch.setattr(tx_worker, "list_audio_devices", lambda: ([selected], None))
    monkeypatch.setattr(tx_worker, "list_serial_ports", lambda: ([("COM9", "fake PTT")], None))

    class AcceptMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_app, "QMessageBox", AcceptMessageBox)
    events = []

    class FakeOutput:
        def validate_output(self, device, sample_rate):
            events.append(("validate", device, sample_rate))

        def play(self, samples, *, device, sample_rate, gain):
            events.append(("play", device, sample_rate))

    ptt = FakePTT()
    monkeypatch.setattr(tx_worker, "SendService", lambda history: CoreSendService(
        audio=FakeOutput(), ptt=ptt, history=history, sleep=lambda _: None))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [("COM9", "fake PTT")], "", "")
    window.tx_choice.setCurrentIndex(1)
    window.ptt_choice.setCurrentIndex(1)
    window.config_input.setText(str(_ready_air_config(tmp_path)))
    window.tx_message.setText("FAKE RF")
    window.tx_output.setText(str(tmp_path / "fake-send.wav"))
    window.air_check.setChecked(True)
    window.start_on_air("text")
    assert isinstance(window.tx_worker, tx_worker.OnAirWorker)
    assert window.tx_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert row.mode == "text" and row.operation == "send" and row.success and row.wav_exists
    assert ptt.events == ["key", "release"]
    assert events == [("validate", selected, 48000), ("play", selected, 48000)]
    assert not window.air_check.isChecked() and not window.air_text_button.isEnabled()
    window.close()


def test_gui_sstv_on_air_uses_only_open_sstv_and_fake_ptt(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PIL import Image
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice
    from radiolab.gui import app as gui_app, tx_worker
    from radiolab.ptt import FakePTT
    from radiolab.service import SendService as CoreSendService

    selected = AudioDevice(7, "fake TX", 0, 1, 48000)
    monkeypatch.setattr(tx_worker, "list_audio_devices", lambda: ([selected], None))
    monkeypatch.setattr(tx_worker, "list_serial_ports", lambda: ([("COM9", "fake PTT")], None))
    monkeypatch.setattr(tx_worker, "render_robot36_pcm",
                        lambda image, *, on_air=False: np.zeros(48000, dtype=np.float32))

    class AcceptMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_app, "QMessageBox", AcceptMessageBox)

    class FakeOutput:
        def validate_output(self, device, sample_rate):
            assert device == selected

        def play(self, samples, *, device, sample_rate, gain):
            assert device == selected

    ptt = FakePTT()
    monkeypatch.setattr(tx_worker, "SendService", lambda history: CoreSendService(
        audio=FakeOutput(), ptt=ptt, history=history, sleep=lambda _: None))
    image = tmp_path / "test.png"
    Image.new("RGB", (320, 240), "green").save(image)
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [("COM9", "fake PTT")], "", "")
    window.tx_choice.setCurrentIndex(1)
    window.ptt_choice.setCurrentIndex(1)
    window.config_input.setText(str(_ready_air_config(tmp_path)))
    window.sstv_image.setText(str(image))
    window.air_check.setChecked(True)
    window.start_on_air("sstv")
    assert isinstance(window.tx_worker, tx_worker.OnAirWorker)
    assert window.tx_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert row.mode == "sstv" and row.operation == "send" and row.success
    assert row.image_exists and row.wav_exists and ptt.events == ["key", "release"]
    window.close()


def test_gui_on_air_rejects_missing_port_before_fake_ptt(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice
    from radiolab.gui import app as gui_app, tx_worker
    from radiolab.ptt import FakePTT
    from radiolab.service import SendService as CoreSendService

    selected = AudioDevice(7, "fake TX", 0, 1, 48000)
    monkeypatch.setattr(tx_worker, "list_audio_devices", lambda: ([selected], None))
    monkeypatch.setattr(tx_worker, "list_serial_ports", lambda: ([], None))

    class AcceptMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_app, "QMessageBox", AcceptMessageBox)
    ptt = FakePTT()
    monkeypatch.setattr(tx_worker, "SendService", lambda history: CoreSendService(
        ptt=ptt, history=history, sleep=lambda _: None))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [("COM9", "fake PTT")], "", "")
    window.tx_choice.setCurrentIndex(1)
    window.ptt_choice.setCurrentIndex(1)
    window.config_input.setText(str(_ready_air_config(tmp_path)))
    window.tx_message.setText("TEST")
    window.air_check.setChecked(True)
    window.start_on_air("text")
    assert isinstance(window.tx_worker, tx_worker.OnAirWorker)
    assert window.tx_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert not row.success and "已消失" in row.reason
    assert ptt.events == [] and not row.wav_exists
    window.close()


def test_gui_close_prioritizes_stopping_on_air_task(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.audio import AudioDevice
    from radiolab.config import Config
    from radiolab.gui.app import HistoryWindow
    from radiolab.gui.tx_worker import OnAirWorker

    stopped = []

    class PretendOnAir(OnAirWorker):
        def isRunning(self):
            return True

        def stop(self):
            stopped.append("tx")

    class PretendRecord:
        def isRunning(self):
            return True

        def stop(self):
            stopped.append("rx")

    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.show()
    window.tx_worker = PretendOnAir("text", tmp_path / "unused.wav", Config(),
                                    AudioDevice(7, "fake TX", 0, 1, 48000), SQLiteHistoryService(database))
    window.record_worker = PretendRecord()
    assert window.close() is False
    assert stopped == ["tx", "rx"]
    assert window._close_after_tx and window._close_after_record
    window.tx_worker = None
    window.record_worker = None
    window.close()


def test_gui_local_replay_requires_distinct_output_and_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice, save_wav
    from radiolab.gui import app as gui_app

    wav = save_wav(tmp_path / "received.wav", np.zeros(4800, dtype=np.float32))
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("audio", "record", wav_path=wav))
    questions = []

    class RejectMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            questions.append(args)
            return QMessageBox.StandardButton.No

    monkeypatch.setattr(gui_app, "QMessageBox", RejectMessageBox)
    app = _qapp()
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.table.setCurrentCell(0, 5)
    window.start_replay_selected()
    assert window.replay_worker is None and "选择本地" in window.replay_status.text()
    speaker = AudioDevice(8, "local speaker", 0, 2, 48000)
    window.devices_ready([speaker], [], "", "")
    window.local_choice.setCurrentIndex(1)
    window.tx_choice.setCurrentIndex(1)
    window.replay_check.setChecked(True)
    window.start_replay_selected()
    assert "与已选 TX 输出相同" in window.replay_status.text() and not questions
    window.tx_choice.setCurrentIndex(0)
    window.start_replay_selected()
    assert questions and window.replay_worker is None
    assert "已取消" in window.replay_status.text()
    assert len(store.list_recent()) == 1
    window.close()


def test_gui_local_replay_uses_fake_output_and_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice, save_wav
    from radiolab.gui import app as gui_app, rx_worker
    from radiolab import service

    speaker = AudioDevice(8, "local speaker", 0, 2, 48000)
    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: ([speaker], None))

    class AcceptMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_app, "QMessageBox", AcceptMessageBox)
    events = []

    class FakeOutput:
        def play(self, samples, *, device, sample_rate, gain):
            events.append(("play", device, sample_rate, gain))

        def stop(self):
            events.append(("stop",))

    monkeypatch.setattr(service, "SoundDeviceAudio", lambda: FakeOutput())
    monkeypatch.setattr(service, "AIOCSerialPTT", lambda *args, **kwargs:
                        pytest.fail("本地回放触发了 PTT"))
    wav = save_wav(tmp_path / "received.wav", np.zeros(4800, dtype=np.float32))
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("audio", "record", wav_path=wav))
    app = _qapp()
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([speaker], [], "", "")
    window.local_choice.setCurrentIndex(1)
    window.replay_check.setChecked(True)
    window.table.setCurrentCell(0, 5)
    window.start_replay_selected()
    assert window.replay_worker is not None
    assert window.replay_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = store.list_recent()[0]
    assert row.mode == "audio" and row.operation == "replay" and row.success
    assert row.device == speaker.name and row.gain == 0.05 and row.wav_exists
    assert events == [("play", speaker, 48000, 0.05), ("stop",)]
    assert not window.replay_check.isChecked()
    window.close()


def test_gui_stop_local_replay_releases_fake_output(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.audio import AudioDevice, save_wav
    from radiolab.gui import app as gui_app, rx_worker
    from radiolab import service

    speaker = AudioDevice(8, "local speaker", 0, 2, 48000)
    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: ([speaker], None))

    class AcceptMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args, **kwargs):
            return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(gui_app, "QMessageBox", AcceptMessageBox)
    started, stopped = Event(), Event()

    class BlockingOutput:
        def play(self, samples, *, device, sample_rate, gain):
            started.set()
            assert stopped.wait(2)

        def stop(self):
            stopped.set()

    monkeypatch.setattr(service, "SoundDeviceAudio", lambda: BlockingOutput())
    wav = save_wav(tmp_path / "received.wav", np.zeros(4800, dtype=np.float32))
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("audio", "record", wav_path=wav))
    app = _qapp()
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([speaker], [], "", "")
    window.local_choice.setCurrentIndex(1)
    window.replay_check.setChecked(True)
    window.table.setCurrentCell(0, 5)
    window.start_replay_selected()
    assert started.wait(2)
    window.stop_replay()
    assert window.replay_worker is not None
    assert window.replay_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = store.list_recent()[0]
    assert stopped.is_set() and not row.success
    assert "停止本地回放" in row.reason and not window.replay_check.isChecked()
    window.close()


def test_gui_auto_listen_detects_text_with_fake_input_and_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.afsk import modulate_frame
    from radiolab.audio import AudioDevice, load_wav
    from radiolab.gui import app as gui_app, rx_worker
    from radiolab.packet import encode_text
    from radiolab.service import ReceiveService as CoreReceiveService

    selected = AudioDevice(5, "fake RX", 1, 0, 48000)
    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: ([selected], None))
    signal = np.r_[np.zeros(41), modulate_frame(encode_text("LIVE GUI", 11)),
                   np.zeros(24000)].astype(np.float32)

    class FakeInput:
        def validate_input(self, device, sample_rate):
            assert device == selected and sample_rate == 48000

        def input_blocks(self, seconds, *, device, sample_rate, stop_event):
            for begin in range(0, len(signal), 2048):
                yield signal[begin:begin + 2048]

    monkeypatch.setattr(rx_worker, "ReceiveService", lambda history: CoreReceiveService(
        audio=FakeInput(), history=history))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.start_text_listen()
    assert window.listen_worker is None and "选择 RX 输入" in window.listen_status.text()
    window.devices_ready([selected], [], "", "")
    window.rx_choice.setCurrentIndex(1)
    window.start_text_listen()
    assert window.listen_worker is not None
    assert window.listen_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    rows = SQLiteHistoryService(database).list_recent()
    assert [(row.mode, row.operation, row.success) for row in rows] == [
        ("audio", "record", True), ("text", "decode", True)]
    assert rows[1].text == "LIVE GUI" and rows[1].sequence == 11 and rows[1].crc_ok
    assert len(load_wav(rows[0].wav_path)) == len(signal)
    assert "有效文本帧 1 条" in window.listen_status.text()
    assert "dBFS" in window.rx_level_text.text()
    assert window.listen_button.isEnabled() and not window.stop_listen_button.isEnabled()
    window.close()


def test_gui_auto_listen_manual_stop_saves_partial_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from radiolab.audio import AudioDevice, load_wav
    from radiolab.gui import app as gui_app, rx_worker
    from radiolab.service import ReceiveService as CoreReceiveService

    selected = AudioDevice(5, "fake RX", 1, 0, 48000)
    monkeypatch.setattr(rx_worker, "list_audio_devices", lambda: ([selected], None))
    started = Event()

    class FakeInput:
        def input_blocks(self, seconds, *, device, sample_rate, stop_event):
            started.set()
            yield np.zeros(2048, dtype=np.float32)
            assert stop_event.wait(2)

    monkeypatch.setattr(rx_worker, "ReceiveService", lambda history: CoreReceiveService(
        audio=FakeInput(), history=history))
    app = _qapp()
    database = tmp_path / "history.sqlite3"
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.devices_ready([selected], [], "", "")
    window.rx_choice.setCurrentIndex(1)
    window.start_text_listen()
    assert started.wait(2)
    window.stop_text_listen()
    assert window.listen_worker is not None
    assert window.listen_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    row = SQLiteHistoryService(database).list_recent()[0]
    assert row.mode == "audio" and row.success and row.wav_exists
    assert len(load_wav(row.wav_path)) == 2048
    assert "有效文本帧 0 条" in window.listen_status.text()
    assert window.rx_level_bar.value() == 0
    assert "电平偏低" in window.rx_level_text.text()
    window.close()


def test_gui_history_export_requires_confirmation_and_writes_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from zipfile import ZipFile
    from PySide6.QtWidgets import QApplication, QMessageBox
    from radiolab.gui import app as gui_app

    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"test media")
    store.append(make_entry("text", "decode", text="PRIVATE", wav_path=wav, crc_ok=True))
    destination = tmp_path / "history.zip"

    class FakeFileDialog:
        @staticmethod
        def getSaveFileName(*args):
            return str(destination), "ZIP 文件 (*.zip)"

    accepted = False

    class FakeMessageBox:
        StandardButton = QMessageBox.StandardButton

        @staticmethod
        def question(*args):
            return QMessageBox.StandardButton.Yes if accepted else QMessageBox.StandardButton.No

    monkeypatch.setattr(gui_app, "QFileDialog", FakeFileDialog)
    monkeypatch.setattr(gui_app, "QMessageBox", FakeMessageBox)
    app = _qapp()
    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    window.start_history_export()
    assert window.export_worker is None and not destination.exists()
    assert "已取消历史导出" in window.status.text()
    accepted = True
    window.start_history_export()
    assert window.export_worker is not None
    assert window.export_worker.wait(30000), "后台任务未在 30 s 内结束"
    app.processEvents()
    with ZipFile(destination) as archive:
        assert b"PRIVATE" in archive.read("entries.jsonl")
    assert "记录 1 条，媒体 1 个" in window.status.text()
    assert window.export_button.isEnabled() and wav.read_bytes() == b"test media"
    window.close()


def test_gui_gallery_lists_unique_images_and_only_fills_send_field(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PIL import Image
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication
    from radiolab.gui import app as gui_app
    from radiolab.gui.gallery import ImageGalleryDialog

    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    valid = tmp_path / "picture.png"
    Image.new("RGB", (320, 240), "blue").save(valid)
    missing = tmp_path / "missing.png"
    store.append(make_entry("sstv", "encode", image_path=valid))
    store.append(make_entry("sstv", "decode", image_path=valid))
    store.append(make_entry("sstv", "decode", image_path=missing))
    app = _qapp()
    gallery = ImageGalleryDialog(store)
    assert gallery.images.count() == 2
    assert "可用 1 张，不可用 1 张" in gallery.summary.text()
    assert gallery.images.item(0).data(Qt.ItemDataRole.UserRole) == str(missing)
    assert not (gallery.images.item(0).flags() & Qt.ItemFlag.ItemIsEnabled)
    gallery.images.setCurrentRow(1)
    assert gallery.use_button.isEnabled()
    gallery.use_selected()
    assert gallery.selected_path == valid and gallery.result() == 1

    window = gui_app.HistoryWindow(database, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)

    def select_in_modal():
        modal = QApplication.activeModalWidget()
        assert isinstance(modal, ImageGalleryDialog)
        modal.images.setCurrentRow(1)
        modal.use_selected()

    QTimer.singleShot(0, select_in_modal)
    window.open_image_gallery()
    assert window.sstv_image.text() == str(valid)
    assert "尚未编码或发射" in window.sstv_status.text()
    assert window.tx_worker is None
    window.close()


def test_gui_recent_files_copy_and_open_folder_are_manual(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from radiolab.gui import app as gui_app
    from radiolab.gui import recent

    wav = tmp_path / "rx.wav"
    wav.write_bytes(b"wav")
    store = SQLiteHistoryService(tmp_path / "history.sqlite3")
    store.append(make_entry("audio", "record", wav_path=wav))
    opened = []
    monkeypatch.setattr(recent.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    app = _qapp()
    dialog = recent.RecentFilesDialog(store)
    assert dialog.table.rowCount() == 1 and not opened
    dialog.table.selectRow(0)
    assert dialog.copy_button.isEnabled() and dialog.folder_button.isEnabled()
    dialog.copy_path()
    assert QApplication.clipboard().text() == str(wav) and not opened
    dialog.open_folder()
    assert len(opened) == 1 and Path(opened[0]) == tmp_path
    dialog.close()

    window = gui_app.HistoryWindow(store.path, tmp_path / "missing.jsonl")
    _WINDOWS.append(window)
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    window.open_recent_files()
    assert len(opened) == 1 and Path(opened[0]) == tmp_path
    window.close()
