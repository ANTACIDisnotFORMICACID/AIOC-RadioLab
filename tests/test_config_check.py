from radiolab import cli
from radiolab.config import Config, load_config, save_config
import pytest


def test_check_validates_tx_rx_separately_without_opening_serial(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("""
[audio]
tx_device = 0
rx_device = 1
[ptt]
port = "COM9"
firmware = "1.2+"
bench_verified = true
[safety]
legal_operation_confirmed = true
""", encoding="utf-8")
    checked = []

    class FakeAudio:
        def validate_output(self, device, rate):
            checked.append(("TX", device, rate))

        def validate_input(self, device, rate):
            checked.append(("RX", device, rate))

    monkeypatch.setattr(cli, "SoundDeviceAudio", FakeAudio)
    monkeypatch.setattr(cli, "list_serial_ports", lambda: ([("COM9", "fake")], None))
    assert cli.main(["check", "--config", str(config)]) == 0
    assert checked == [("TX", 0, 48000), ("RX", 1, 48000)]
    assert "PTT 串口 COM9：已枚举；未打开端口" in capsys.readouterr().out


def test_check_rejects_missing_devices(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[audio]\ntx_device = 'missing'\nrx_device = 'missing'\n", encoding="utf-8")

    class FakeAudio:
        def validate_output(self, device, rate):
            raise ValueError("TX 不存在")

        def validate_input(self, device, rate):
            raise ValueError("RX 不存在")

    monkeypatch.setattr(cli, "SoundDeviceAudio", FakeAudio)
    monkeypatch.setattr(cli, "list_serial_ports", lambda: ([], None))
    assert cli.main(["check", "--config", str(config)]) == 2
    output = capsys.readouterr().out
    assert "TX 不存在" in output and "RX 不存在" in output and "PTT 串口" in output


def test_malformed_section_gets_clear_error(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('audio = "wrong"\n', encoding="utf-8")
    try:
        load_config(config)
    except ValueError as exc:
        assert "TOML 表" in str(exc)
    else:
        raise AssertionError("错误的 TOML 结构没有被拒绝")


def test_gui_config_roundtrip_and_overwrite_backup(tmp_path):
    path = tmp_path / "config.toml"
    first = Config(tx_device="7: USB 音频", rx_device=3, ptt_port="COM9",
                   firmware="1.2+", gain=0.08, frequency_hz=145500000,
                   power_setting="低功率")
    assert save_config(path, first) == path
    assert load_config(path) == first
    with pytest.raises(ValueError, match="确认后才可覆盖"):
        save_config(path, Config())
    second = Config(tx_device=7, rx_device=3, ptt_port="COM9", firmware="1.2+",
                    ptt_bench_verified=True, legal_operation_confirmed=True)
    save_config(path, second, overwrite=True)
    assert load_config(path) == second
    backups = list(tmp_path.glob("config.toml.*.bak"))
    assert len(backups) == 1 and load_config(backups[0]) == first


@pytest.mark.parametrize("bad", [Config(gain=float("nan")), Config(tx_device=True),
                                  Config(max_tx_seconds=61), Config(frequency_hz=-1)])
def test_config_save_rejects_invalid_values(tmp_path, bad):
    with pytest.raises(ValueError):
        save_config(tmp_path / "config.toml", bad)
    assert not (tmp_path / "config.toml").exists()
