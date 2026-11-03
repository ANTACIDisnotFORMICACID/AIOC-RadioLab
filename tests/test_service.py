from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace
import sys
import numpy as np
import pytest

from radiolab.audio import AudioDevice, AudioError, SoundDeviceAudio, audio_level, load_wav, save_wav
from radiolab.config import Config
from radiolab.history import HistoryService, make_entry
from radiolab.ptt import AIOCSerialPTT, FakePTT
from radiolab.service import ReceiveService, SendService, TxPhase


class FakeAudio:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def play(self, samples, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error


def ready_config():
    return Config(tx_device="fake", ptt_port="FAKE", firmware="1.2+", ptt_bench_verified=True,
                  legal_operation_confirmed=True, pre_delay_s=0, tail_delay_s=0)


def test_fake_ptt_normal_and_exception(tmp_path):
    for error in (None, RuntimeError("播放失败"), KeyboardInterrupt()):
        ptt = FakePTT()
        service = SendService(audio=FakeAudio(error), ptt=ptt, history=HistoryService(tmp_path / "history.jsonl"), sleep=lambda _: None)
        if error:
            with pytest.raises(type(error)):
                service.send_text("OK", config=ready_config(), wav_path=tmp_path / "a.wav", on_air=True)
        else:
            service.send_text("OK", config=ready_config(), wav_path=tmp_path / "a.wav", on_air=True)
        assert not ptt.keyed
        assert ptt.events == ["key", "release"]


def test_tx_phase_reports_actual_ptt_and_audio_progress(tmp_path):
    ptt = FakePTT()
    audio = FakeAudio()
    observed = []
    sender = SendService(audio=audio, ptt=ptt, history=HistoryService(tmp_path / "h.jsonl"),
                         sleep=lambda _: None)
    sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "ok.wav",
                     on_air=True, on_phase=lambda phase: observed.append((phase, ptt.keyed, audio.calls)))
    assert observed == [(TxPhase.PTT_KEYED, True, 0), (TxPhase.PLAYING, True, 0),
                        (TxPhase.TAIL, True, 1)]
    assert ptt.events == ["key", "release"]

    failed = []
    sender = SendService(audio=FakeAudio(RuntimeError("播放失败")), ptt=ptt,
                         history=HistoryService(tmp_path / "h.jsonl"), sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="播放失败"):
        sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "failed.wav",
                         on_air=True, on_phase=failed.append)
    assert failed == [TxPhase.PTT_KEYED, TxPhase.PLAYING]
    assert not ptt.keyed and ptt.events[-2:] == ["key", "release"]


def test_no_ptt_for_dry_run_and_invalid_config(tmp_path):
    ptt = FakePTT()
    service = SendService(audio=FakeAudio(), ptt=ptt, history=HistoryService(tmp_path / "h.jsonl"))
    service.send_text("OK", wav_path=tmp_path / "ok.wav")
    assert ptt.events == []
    with pytest.raises(ValueError):
        service.send_text("OK", config=Config(), wav_path=tmp_path / "ok.wav", on_air=True)
    assert ptt.events == []
    rejected = service.history.list_recent()[0]
    assert rejected.operation == "send" and not rejected.success and rejected.wav_exists
    assert "audio.tx_device" in rejected.reason


def test_preflight_timeout(tmp_path):
    ptt = FakePTT()
    service = SendService(ptt=ptt, history=HistoryService(tmp_path / "h.jsonl"))
    with pytest.raises(ValueError):
        service.send_text("OK", config=replace(ready_config(), max_tx_seconds=0.01),
                          wav_path=tmp_path / "ok.wav", on_air=True)
    assert ptt.events == []
    assert service.history.list_recent()[0].success is False


def test_ptt_construction_failure_releases_send_lock(tmp_path, monkeypatch):
    from radiolab import service as service_module
    from radiolab.ptt import TX_LOCK

    def fail_ptt(port):
        raise RuntimeError("PTT 初始化失败")

    monkeypatch.setattr(service_module, "AIOCSerialPTT", fail_ptt)
    service = SendService(audio=FakeAudio(), history=HistoryService(tmp_path / "h.jsonl"))
    with pytest.raises(RuntimeError, match="初始化失败"):
        service.send_text("OK", config=ready_config(), wav_path=tmp_path / "a.wav", on_air=True)
    assert TX_LOCK.acquire(blocking=False)
    TX_LOCK.release()
    assert service.history.list_recent()[0].reason == "PTT 初始化失败"


def test_oversize_text_failure_is_recorded_without_large_text(tmp_path):
    service = SendService(audio=FakeAudio(), history=HistoryService(tmp_path / "h.jsonl"))
    with pytest.raises(ValueError):
        service.send_text("中" * 11, wav_path=tmp_path / "missing.wav")
    entry = service.history.list_recent()[0]
    assert entry.operation == "encode" and not entry.success and entry.text is None
    assert not entry.wav_exists


def test_history_missing_path(tmp_path):
    history = HistoryService(tmp_path / "h.jsonl")
    entry = make_entry("text", "decode", wav_path=tmp_path / "missing.wav", success=False, reason="missing")
    history.append(entry)
    assert history.list_recent() == [entry]
    assert history.list_recent()[0].wav_exists is False


def test_send_wav_save_failure_is_logged_without_ptt(tmp_path):
    ptt = FakePTT()
    output_directory = tmp_path / "not-a-wav.wav"
    output_directory.mkdir()
    history = HistoryService(tmp_path / "h.jsonl")
    service = SendService(audio=FakeAudio(), ptt=ptt, history=history)
    with pytest.raises((OSError, RuntimeError, ValueError)):
        service.send_text("OK", config=ready_config(), wav_path=output_directory, on_air=True)
    row = history.list_recent()[0]
    assert row.operation == "send" and row.success is False and row.wav_exists is False
    assert ptt.events == []


def test_history_experiment_metadata_and_old_rows(tmp_path):
    import json

    history = HistoryService(tmp_path / "h.jsonl")
    old = {"time": "2026-10-28T00:00:00+00:00", "mode": "text", "operation": "decode",
           "device": None, "wav_path": None, "wav_exists": False, "text": None,
           "sequence": None, "crc_ok": False, "success": False, "reason": "old"}
    history.path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    entry = make_entry("text", "send", device="AIOC", wav_path=tmp_path / "missing.wav",
                       firmware="1.2+", sample_rate=48000, gain=0.1,
                       frequency_hz=123456789, power_setting="LOW", payload_bytes=2)
    history.append(entry)
    recent = history.list_recent()
    assert recent[0] == entry and recent[1].run_id is None
    assert len(entry.run_id) == 32 and entry.local_time.endswith("+08:00")
    assert entry.direction == "tx" and entry.frequency_hz == 123456789
    assert entry.power_setting == "LOW" and entry.payload_bytes == 2


def test_send_history_uses_confirmed_radio_metadata(tmp_path):
    config = replace(ready_config(), frequency_hz=123456789, power_setting="LOW")
    history = HistoryService(tmp_path / "h.jsonl")
    service = SendService(audio=FakeAudio(), ptt=FakePTT(), history=history, sleep=lambda _: None)
    service.send_text("OK", config=config, wav_path=tmp_path / "a.wav", on_air=True)
    row = history.list_recent()[0]
    assert row.sample_rate == 48000 and row.gain == config.gain
    assert row.firmware == "1.2+" and row.frequency_hz == 123456789
    assert row.payload_bytes == 2 and row.direction == "tx" and row.ptt_port == "FAKE"


def test_wav_rejects_invalid(tmp_path):
    with pytest.raises(AudioError):
        save_wav(tmp_path / "bad.wav", np.array([np.nan]))
    with pytest.raises(AudioError):
        load_wav(tmp_path / "missing.wav")


def test_audio_level_reports_silence_and_clipping():
    quiet = audio_level(np.zeros(4800, dtype=np.float32))
    assert quiet.low_level and quiet.rms_dbfs <= -200
    loud = audio_level(np.ones(4800, dtype=np.float32))
    assert not loud.low_level and loud.clipped_fraction == 1.0


def test_record_rejects_nonfinite_device_samples(monkeypatch):
    import sounddevice as sd
    monkeypatch.setattr("radiolab.audio._select", lambda *args: (10, 1))
    monkeypatch.setattr(sd, "rec", lambda frames, **kwargs: np.full((frames, 1), np.nan, dtype=np.float32))
    with pytest.raises(AudioError, match="输入设备 '10' 返回非有限"):
        SoundDeviceAudio().record(0.1, device="10")


def test_record_uses_supported_multichannel_input(monkeypatch):
    import sounddevice as sd

    monkeypatch.setattr("radiolab.audio.list_audio_devices",
                        lambda: ([AudioDevice(9, "four-channel input", 4, 0, 48000)], None))

    def check_input_settings(*, device, channels, samplerate):
        assert device == 9 and samplerate == 48000
        if channels != 4:
            raise ValueError("channel count not supported")

    def rec(frames, *, samplerate, channels, dtype, device, blocking):
        assert frames == 4800 and channels == 4 and device == 9 and blocking
        return np.tile(np.array([[0.0, 0.25, 0.5, 0.25]], dtype=np.float32), (frames, 1))

    monkeypatch.setattr(sd, "check_input_settings", check_input_settings)
    monkeypatch.setattr(sd, "rec", rec)
    SoundDeviceAudio().validate_input(9)
    mono = SoundDeviceAudio().record(0.1, device=9)
    assert mono.shape == (4800,) and np.allclose(mono, 0.25)


def test_ptt_serial_lines_are_prepared_before_open():
    events = []

    class SerialStub:
        def __init__(self, **kwargs):
            self.dtr = None
            self.rts = None
            self.port = None

        def open(self):
            events.append(("open", self.dtr, self.rts, self.port))

        def close(self):
            events.append(("close", self.dtr, self.rts))

    ptt = AIOCSerialPTT("FAKE", serial_factory=SerialStub)
    ptt.key()
    assert events == [("open", False, False, "FAKE")]
    assert ptt._serial.dtr is True and ptt._serial.rts is False
    ptt.release()
    assert events[-1] == ("close", False, False)


def test_ptt_release_attempts_rts_and_close_after_dtr_error():
    events = []

    class SerialStub:
        def __init__(self, **kwargs):
            self.opened = False
            self._dtr = True
            self._rts = True
            self.port = None

        @property
        def dtr(self):
            return self._dtr

        @dtr.setter
        def dtr(self, value):
            events.append(("dtr", value))
            if self.opened and value is False:
                raise OSError("DTR 写入失败")
            self._dtr = value

        @property
        def rts(self):
            return self._rts

        @rts.setter
        def rts(self, value):
            events.append(("rts", value))
            self._rts = value

        def open(self):
            self.opened = True

        def close(self):
            events.append(("close",))

    ptt = AIOCSerialPTT("FAKE", serial_factory=SerialStub)
    ptt.key()
    with pytest.raises(RuntimeError, match="PTT 释放不完整"):
        ptt.release()
    assert events[-2:] == [("rts", False), ("close",)]
    assert ptt._serial is not None  # 保留失败连接以便后续重试。


def test_ptt_release_can_retry_transient_line_error():
    class SerialStub:
        def __init__(self, **kwargs):
            self._dtr = False
            self.rts = False
            self.port = None
            self.opened = False
            self.fail_once = True

        @property
        def dtr(self):
            return self._dtr

        @dtr.setter
        def dtr(self, value):
            if self.opened and value is False and self.fail_once:
                self.fail_once = False
                raise OSError("瞬时 DTR 错误")
            self._dtr = value

        def open(self):
            self.opened = True

        def close(self):
            self.opened = False

    ptt = AIOCSerialPTT("FAKE", serial_factory=SerialStub)
    ptt.key()
    with pytest.raises(RuntimeError, match="瞬时 DTR 错误"):
        ptt.release()
    assert ptt._serial is not None
    ptt.release()
    assert ptt._serial is None


def test_ptt_key_cleanup_failure_is_retried_by_send_finally(tmp_path):
    events = []

    class SerialStub:
        def __init__(self, **kwargs):
            self.dtr = False
            self.rts = False
            self.port = None

        def open(self):
            raise OSError("打开端口失败")

        def close(self):
            events.append("close")
            if len(events) == 1:
                raise OSError("首次关闭失败")

    ptt = AIOCSerialPTT("FAKE", serial_factory=SerialStub)
    history = HistoryService(tmp_path / "history.jsonl")
    sender = SendService(audio=FakeAudio(), ptt=ptt, history=history, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="PTT 初始化失败"):
        sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "failed-open.wav", on_air=True)
    assert events == ["close", "close"] and ptt._serial is None
    assert not history.list_recent()[0].success


def test_ptt_key_init_error_still_attempts_close():
    events = []

    class SerialStub:
        def __init__(self, **kwargs):
            self.port = None

        @property
        def dtr(self):
            return True

        @dtr.setter
        def dtr(self, value):
            events.append("dtr")
            raise OSError("无法设置 DTR")

        @property
        def rts(self):
            return True

        @rts.setter
        def rts(self, value):
            events.append("rts")

        def close(self):
            events.append("close")

    ptt = AIOCSerialPTT("FAKE", serial_factory=SerialStub)
    with pytest.raises(RuntimeError, match="PTT 初始化失败"):
        ptt.key()
    assert events == ["dtr", "dtr", "rts", "close"]


def test_timeout_after_play_releases(tmp_path):
    ptt = FakePTT()
    ticks = iter([0.0, 0.0, 2.0])
    service = SendService(audio=FakeAudio(), ptt=ptt, history=HistoryService(tmp_path / "h.jsonl"),
                          sleep=lambda _: None, clock=lambda: next(ticks))
    with pytest.raises(TimeoutError):
        service.send_text("OK", config=replace(ready_config(), max_tx_seconds=1.0),
                          wav_path=tmp_path / "a.wav", on_air=True)
    assert ptt.events == ["key", "release"]


def test_pre_delay_timeout_skips_audio(tmp_path):
    ptt = FakePTT()
    audio = FakeAudio()
    ticks = iter([0.0, 2.0])
    service = SendService(audio=audio, ptt=ptt, history=HistoryService(tmp_path / "h.jsonl"),
                          sleep=lambda _: None, clock=lambda: next(ticks))
    with pytest.raises(TimeoutError, match="预延时"):
        service.send_text("OK", config=replace(ready_config(), max_tx_seconds=1.0),
                          wav_path=tmp_path / "a.wav", on_air=True)
    assert audio.calls == 0 and ptt.events == ["key", "release"]


def test_release_failure_cannot_be_logged_as_success(tmp_path, monkeypatch):
    from radiolab import service as service_module
    from radiolab.ptt import TX_LOCK

    fault = Event()
    monkeypatch.setattr(service_module, "TX_FAULT", fault)

    class FailingRelease(FakePTT):
        def release(self):
            self.events.append("release-failed")
            raise OSError("DTR 释放失败")

    ptt = FailingRelease()
    history = HistoryService(tmp_path / "h.jsonl")
    service = SendService(audio=FakeAudio(), ptt=ptt, history=history, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="清理未完成"):
        service.send_text("OK", config=ready_config(), wav_path=tmp_path / "a.wav", on_air=True)
    assert ptt.events == ["key", "release-failed"]
    assert history.list_recent()[0].success is False
    assert "DTR 释放失败" in history.list_recent()[0].reason
    assert fault.is_set()
    with pytest.raises(RuntimeError, match="PTT 释放状态不明"):
        service.send_text("SECOND", config=ready_config(), wav_path=tmp_path / "b.wav", on_air=True)
    assert ptt.events == ["key", "release-failed"]
    assert all(not row.success for row in history.list_recent())
    assert TX_LOCK.acquire(blocking=False)
    TX_LOCK.release()


def test_watchdog_firing_at_cleanup_is_not_logged_as_success(tmp_path, monkeypatch):
    from radiolab import service as service_module

    class FinishingTimer:
        def __init__(self, seconds, callback):
            self.callback = callback
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            self.callback()

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(service_module, "Timer", FinishingTimer)
    ptt = FakePTT()
    history = HistoryService(tmp_path / "h.jsonl")
    service = SendService(audio=FakeAudio(), ptt=ptt, history=history,
                          sleep=lambda _: None, clock=lambda: 0.0)
    with pytest.raises(TimeoutError, match="上限"):
        service.send_text("OK", config=ready_config(), wav_path=tmp_path / "a.wav", on_air=True)
    assert ptt.events == ["key", "release"]
    assert history.list_recent()[0].success is False


def test_stalled_phase_callback_is_watchdog_protected_and_skips_audio(tmp_path, monkeypatch):
    from radiolab import service as service_module

    entered, released = Event(), Event()

    class TriggeredTimer:
        def __init__(self, seconds, callback):
            self.callback = callback
            self.daemon = False
            self.thread = None

        def start(self):
            self.thread = Thread(target=lambda: (entered.wait(2.0), self.callback()))
            self.thread.start()

        def cancel(self):
            pass

        def join(self, timeout=None):
            self.thread.join(timeout)

        def is_alive(self):
            return self.thread.is_alive()

    class ObservedPTT(FakePTT):
        def release(self):
            super().release()
            released.set()

    monkeypatch.setattr(service_module, "Timer", TriggeredTimer)
    ptt, audio = ObservedPTT(), FakeAudio()
    history = HistoryService(tmp_path / "history.jsonl")
    sender = SendService(audio=audio, ptt=ptt, history=history, sleep=lambda _: None)

    def on_phase(phase):
        if phase == TxPhase.PTT_KEYED:
            entered.set()
            assert released.wait(2.0)

    with pytest.raises(TimeoutError, match="PTT 状态回调"):
        sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "stalled.wav",
                         on_air=True, on_phase=on_phase)
    assert audio.calls == 0 and ptt.events == ["key", "release"]
    assert history.list_recent()[0].success is False


def test_timer_cancel_error_still_releases_ptt(tmp_path, monkeypatch):
    from radiolab import service as service_module

    class FailingCancelTimer:
        def __init__(self, seconds, callback):
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            raise RuntimeError("定时器取消失败")

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(service_module, "Timer", FailingCancelTimer)
    ptt = FakePTT()
    history = HistoryService(tmp_path / "history.jsonl")
    sender = SendService(audio=FakeAudio(), ptt=ptt, history=history, sleep=lambda _: None)
    with pytest.raises(RuntimeError, match="定时器取消失败"):
        sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "cancel-error.wav", on_air=True)
    assert ptt.events == ["key", "release"] and not history.list_recent()[0].success


def test_old_watchdog_cannot_stop_next_transmission(tmp_path, monkeypatch):
    from radiolab import service as service_module
    from radiolab.ptt import TX_LOCK

    stopping, finish_stop = Event(), Event()

    class SlowStopAudio(FakeAudio):
        def stop(self):
            stopping.set()
            assert finish_stop.wait(3.0)

    class LateTimer:
        def __init__(self, seconds, callback):
            self.callback = callback
            self.daemon = False
            self.worker = None

        def start(self):
            pass

        def cancel(self):
            self.worker = Thread(target=self.callback)
            self.worker.start()
            assert stopping.wait(2.0)

        def join(self, timeout=None):
            self.worker.join(0.01 if timeout is not None else None)

        def is_alive(self):
            return self.worker.is_alive()

    monkeypatch.setattr(service_module, "Timer", LateTimer)
    ptt = FakePTT()
    history = HistoryService(tmp_path / "history.jsonl")
    sender = SendService(audio=SlowStopAudio(), ptt=ptt, history=history, sleep=lambda _: None)
    try:
        with pytest.raises(RuntimeError, match="清理未完成"):
            sender.send_text("FIRST", config=ready_config(), wav_path=tmp_path / "first.wav", on_air=True)
        assert stopping.is_set() and TX_LOCK.locked()
        with pytest.raises(RuntimeError, match="已有发射事务"):
            sender.send_text("SECOND", config=ready_config(), wav_path=tmp_path / "second.wav", on_air=True)
        assert ptt.events == ["key", "release"]
    finally:
        finish_stop.set()
    assert TX_LOCK.acquire(timeout=2.0)
    TX_LOCK.release()
    assert all(not row.success for row in history.list_recent())


def test_watchdog_releases_during_blocking_play(tmp_path):
    stopped = Event()

    class BlockingAudio:
        def play(self, samples, **kwargs):
            assert stopped.wait(2.0)

        def stop(self):
            stopped.set()

    ptt = FakePTT()
    service = SendService(audio=BlockingAudio(), ptt=ptt, history=HistoryService(tmp_path / "h.jsonl"), sleep=lambda _: None)
    with pytest.raises(TimeoutError):
        service.send_text("OK", config=replace(ready_config(), max_tx_seconds=1.0),
                          wav_path=tmp_path / "a.wav", on_air=True)
    assert stopped.is_set()
    assert not ptt.keyed


def test_user_cancel_during_blocking_play_releases_ptt_and_audio(tmp_path):
    started, stopped, cancel = Event(), Event(), Event()

    class BlockingAudio:
        def play(self, samples, **kwargs):
            started.set()
            assert stopped.wait(2.0)

        def stop(self):
            stopped.set()

    ptt = FakePTT()
    history = HistoryService(tmp_path / "history.jsonl")
    sender = SendService(audio=BlockingAudio(), ptt=ptt, history=history, sleep=lambda _: None)
    errors = []

    def transmit():
        try:
            sender.send_text("CANCEL", config=replace(ready_config(), max_tx_seconds=5.0),
                             wav_path=tmp_path / "cancel.wav", on_air=True,
                             cancel_event=cancel)
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=transmit)
    thread.start()
    assert started.wait(2.0)
    cancel.set()
    thread.join(timeout=3.0)
    assert not thread.is_alive() and stopped.is_set()
    assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
    assert ptt.events == ["key", "release"] and not ptt.keyed
    row = history.list_recent()[0]
    assert row.success is False and "停止发射" in row.reason


def test_pre_cancel_and_output_identity_mismatch_do_not_key_ptt(tmp_path):
    ptt = FakePTT()
    history = HistoryService(tmp_path / "history.jsonl")
    sender = SendService(audio=FakeAudio(), ptt=ptt, history=history)
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError, match="取消发射"):
        sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "cancel.wav",
                         on_air=True, cancel_event=cancel)
    with pytest.raises(ValueError, match="所选 TX 输出"):
        sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "mismatch.wav",
                         on_air=True, output_identity=AudioDevice(4, "wrong", 0, 1, 48000))
    assert ptt.events == []
    assert all(not row.success for row in history.list_recent())


def test_output_identity_is_checked_and_used_for_playback(tmp_path):
    chosen = AudioDevice(4, "fake", 0, 1, 48000)
    devices = []

    class CheckedAudio:
        def validate_output(self, device, sample_rate):
            devices.append(("validate", device, sample_rate))

        def play(self, samples, *, device, sample_rate, gain):
            devices.append(("play", device, sample_rate))

    ptt = FakePTT()
    sender = SendService(audio=CheckedAudio(), ptt=ptt,
                         history=HistoryService(tmp_path / "history.jsonl"), sleep=lambda _: None)
    sender.send_text("OK", config=ready_config(), wav_path=tmp_path / "checked.wav",
                     on_air=True, output_identity=chosen)
    assert devices == [("validate", chosen, 48000), ("play", chosen, 48000)]
    assert ptt.events == ["key", "release"]


def test_receive_record_fake_audio(tmp_path):
    class Recorder:
        def record(self, seconds, *, device, sample_rate):
            assert seconds == 0.1 and device == "fake-input" and sample_rate == 48000
            return np.zeros(4800, dtype=np.float32)

    history = HistoryService(tmp_path / "h.jsonl")
    service = ReceiveService(audio=Recorder(), history=history)
    path = service.record(0.1, tmp_path / "rx.wav", config=Config(rx_device="fake-input"))
    assert len(load_wav(path)) == 4800
    assert history.list_recent()[0].reason == "电平偏低，检查 RX 输入路径"


def test_text_service_rejects_valid_binary_test_frame(tmp_path):
    from radiolab.afsk import modulate_frame
    from radiolab.packet import encode_frame

    wav = save_wav(tmp_path / "binary.wav", modulate_frame(encode_frame(b"\xff", kind=2)))
    history = HistoryService(tmp_path / "history.jsonl")
    with pytest.raises(ValueError, match="不是文本类型"):
        ReceiveService(history=history).decode_file(wav)
    row = history.list_recent()[0]
    assert row.success is False and row.crc_ok is False and row.text is None


def test_stream_record_stop_keeps_pcm_and_closes_input(monkeypatch):
    stopped = Event()
    events = []

    class FakeStream:
        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *args):
            events.append("close")

        def read(self, frames):
            events.append(("read", frames))
            stopped.set()
            return np.full((frames, 1), 0.05, dtype=np.float32), False

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        check_input_settings=lambda **kwargs: None,
        InputStream=lambda **kwargs: FakeStream()))
    monkeypatch.setattr("radiolab.audio.list_audio_devices",
                        lambda: ([AudioDevice(7, "fake RX", 1, 0, 48000)], None))
    pcm = SoundDeviceAudio().record_stream(5, device=7, stop_event=stopped)
    assert pcm.shape == (2048,) and np.allclose(pcm, 0.05)
    assert events == ["open", ("read", 2048), "close"]
    with pytest.raises(AudioError, match="未找到"):
        SoundDeviceAudio().record_stream(5, device=AudioDevice(7, "stale RX", 1, 0, 48000))


def test_receive_record_until_writes_partial_wav_and_history(tmp_path):
    stopped = Event()

    class Recorder:
        def record_stream(self, seconds, *, device, sample_rate, stop_event):
            assert seconds == 45 and device == "fake RX" and sample_rate == 48000
            assert stop_event is stopped
            return np.full(2048, 0.01, dtype=np.float32)

    history = HistoryService(tmp_path / "history.jsonl")
    path = ReceiveService(audio=Recorder(), history=history).record_until(
        45, tmp_path / "partial.wav", config=Config(rx_device="fake RX"), stop_event=stopped)
    assert len(load_wav(path)) == 2048
    row = history.list_recent()[0]
    assert row.success and row.wav_exists and row.device == "fake RX"


def test_stream_overflow_releases_input(monkeypatch):
    events = []

    class OverflowStream:
        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *args):
            events.append("close")

        def read(self, count):
            return np.zeros((count, 1), dtype=np.float32), True

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        check_input_settings=lambda **kwargs: None,
        InputStream=lambda **kwargs: OverflowStream()))
    monkeypatch.setattr("radiolab.audio.list_audio_devices",
                        lambda: ([AudioDevice(7, "fake RX", 1, 0, 48000)], None))
    with pytest.raises(AudioError, match="缓冲区溢出"):
        SoundDeviceAudio().record_stream(0.2, device=7)
    assert events == ["open", "close"]


@pytest.mark.parametrize("error", [None, RuntimeError("播放失败"), KeyboardInterrupt()])
def test_replay_uses_explicit_output_and_stops(tmp_path, error):
    class LocalAudio:
        def __init__(self):
            self.events = []

        def play(self, samples, *, device, sample_rate, gain):
            self.events.append(("play", device, sample_rate, gain, len(samples)))
            if error:
                raise error

        def stop(self):
            self.events.append(("stop",))

    wav = save_wav(tmp_path / "rx.wav", np.zeros(4800, dtype=np.float32))
    backend = LocalAudio()
    history = HistoryService(tmp_path / "h.jsonl")
    service = ReceiveService(audio=backend, history=history)
    if error:
        with pytest.raises(type(error)):
            service.replay_file(wav, output_device="local-speaker")
    else:
        assert service.replay_file(wav, output_device="local-speaker") == 0.1
    assert backend.events == [("play", "local-speaker", 48000, 0.05, 4800), ("stop",)]
    assert history.list_recent()[0].success is (error is None)


def test_replay_rejects_gain_before_playback(tmp_path):
    class NeverPlay:
        def play(self, *args, **kwargs):
            pytest.fail("invalid gain reached playback")

    service = ReceiveService(audio=NeverPlay(), history=HistoryService(tmp_path / "h.jsonl"))
    with pytest.raises(ValueError, match="回放增益"):
        service.replay_file(tmp_path / "missing.wav", output_device="speaker", gain=1.0)
    assert service.history.list_recent()[0].success is False


def test_replay_cancel_stops_blocking_audio_and_logs_device_name(tmp_path):
    wav = save_wav(tmp_path / "rx.wav", np.zeros(4800, dtype=np.float32))
    started, stopped, cancel = Event(), Event(), Event()

    class BlockingAudio:
        def play(self, samples, *, device, sample_rate, gain):
            assert device == AudioDevice(8, "local speaker", 0, 2, 48000)
            started.set()
            assert stopped.wait(2)

        def stop(self):
            stopped.set()

    history = HistoryService(tmp_path / "history.jsonl")
    service = ReceiveService(audio=BlockingAudio(), history=history)
    errors = []

    def replay():
        try:
            service.replay_file(wav, output_device=AudioDevice(8, "local speaker", 0, 2, 48000),
                                cancel_event=cancel)
        except BaseException as exc:
            errors.append(exc)

    thread = Thread(target=replay)
    thread.start()
    assert started.wait(2)
    cancel.set()
    thread.join(timeout=3)
    assert not thread.is_alive() and stopped.is_set()
    assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
    row = history.list_recent()[0]
    assert not row.success and row.device == "local speaker" and "停止本地回放" in row.reason


def test_replay_pre_cancel_never_plays(tmp_path):
    class NeverPlay:
        def play(self, *args, **kwargs):
            pytest.fail("取消后仍播放")

    cancel = Event()
    cancel.set()
    history = HistoryService(tmp_path / "history.jsonl")
    with pytest.raises(InterruptedError, match="开始前取消"):
        ReceiveService(audio=NeverPlay(), history=history).replay_file(
            tmp_path / "missing.wav", output_device="local speaker", cancel_event=cancel)
    assert history.list_recent()[0].success is False


def test_replay_cli_requires_local_output_confirmation(monkeypatch, capsys):
    from radiolab import cli

    monkeypatch.setattr(cli, "ReceiveService", lambda: pytest.fail("replay service opened"))
    assert cli.main(["audio", "replay", "missing.wav", "--output-device", "speaker"]) == 2
    assert "--confirm-local-output" in capsys.readouterr().err


def test_listen_text_stream_saves_wav_and_only_valid_unique_frames(tmp_path):
    from radiolab.afsk import modulate_frame
    from radiolab.packet import encode_text

    device = AudioDevice(4, "fake RX", 1, 0, 48000)
    first = modulate_frame(encode_text("LIVE", 3))
    second = modulate_frame(encode_text("NEXT", 4))
    signal = np.r_[np.zeros(87), first, np.zeros(4800), first,
                   np.zeros(4800), second, np.zeros(24000)].astype(np.float32)

    class FakeInput:
        def validate_input(self, selected, sample_rate):
            assert selected == device and sample_rate == 48000

        def input_blocks(self, seconds, *, device, sample_rate, stop_event):
            for begin in range(0, len(signal), 2048):
                if stop_event.is_set():
                    return
                yield signal[begin:begin + 2048]

    history = HistoryService(tmp_path / "history.jsonl")
    seen = []
    levels = []
    output, results = ReceiveService(audio=FakeInput(), history=history).listen_text(
        4, tmp_path / "listen.wav", device=device, stop_event=Event(),
        on_frame=seen.append, on_level=levels.append)
    assert output.is_file() and len(load_wav(output)) == len(signal)
    assert [item.frame.text for item in results] == ["LIVE", "NEXT"]
    assert seen == results
    assert levels and all(np.isfinite(level.peak_dbfs) and np.isfinite(level.rms_dbfs)
                          for level in levels)
    rows = history.list_recent()
    assert rows[0].mode == "audio" and rows[0].success and rows[0].wav_exists
    assert [row.text for row in rows[1:]] == ["NEXT", "LIVE"]
    assert all(row.crc_ok and row.wav_exists for row in rows[1:])


def test_listen_text_stream_error_keeps_partial_wav_and_failure_reason(tmp_path):
    device = AudioDevice(4, "fake RX", 1, 0, 48000)

    class FailingInput:
        def input_blocks(self, seconds, *, device, sample_rate, stop_event):
            yield np.zeros(2048, dtype=np.float32)
            raise AudioError("输入中断")

    history = HistoryService(tmp_path / "history.jsonl")
    output = tmp_path / "partial.wav"
    with pytest.raises(AudioError, match="输入中断"):
        ReceiveService(audio=FailingInput(), history=history).listen_text(
            4, output, device=device, stop_event=Event())
    assert output.is_file() and len(load_wav(output)) == 2048
    row = history.list_recent()[0]
    assert not row.success and row.wav_exists and "输入中断" in row.reason


def test_listen_text_rejects_existing_wav_without_overwrite(tmp_path):
    device = AudioDevice(4, "fake RX", 1, 0, 48000)
    output = save_wav(tmp_path / "original.wav", np.zeros(480, dtype=np.float32))
    before = output.read_bytes()
    history = HistoryService(tmp_path / "history.jsonl")
    with pytest.raises(ValueError, match="已存在"):
        ReceiveService(history=history).listen_text(1, output, device=device, stop_event=Event())
    assert output.read_bytes() == before
    assert history.list_recent()[0].success is False


@pytest.mark.parametrize("bad", [np.array([np.nan], dtype=np.float32),
                                  np.array([1.1], dtype=np.float32)])
def test_listen_text_rejects_invalid_input_block(tmp_path, bad):
    device = AudioDevice(4, "fake RX", 1, 0, 48000)

    class BadInput:
        def input_blocks(self, seconds, *, device, sample_rate, stop_event):
            yield bad

    history = HistoryService(tmp_path / "history.jsonl")
    with pytest.raises(AudioError, match="无效或超量程"):
        ReceiveService(audio=BadInput(), history=history).listen_text(
            1, tmp_path / "bad.wav", device=device, stop_event=Event())
    assert history.list_recent()[0].success is False
