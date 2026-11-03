"""TOML 配置；只有经台架确认的串口控制方案可用于 RF。"""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
from uuid import uuid4


@dataclass(frozen=True)
class Config:
    tx_device: str | int | None = None
    rx_device: str | int | None = None
    ptt_port: str | None = None
    firmware: str | None = None
    ptt_bench_verified: bool = False
    legal_operation_confirmed: bool = False
    gain: float = 0.1
    pre_delay_s: float = 0.3
    tail_delay_s: float = 0.2
    max_tx_seconds: float = 60.0
    frequency_hz: int | None = None  # 只记录人工设置，不控制手台
    power_setting: str | None = None


def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"无法读取配置 {path}：{exc}") from exc
    audio = data.get("audio", {})
    ptt = data.get("ptt", {})
    safety = data.get("safety", {})
    radio = data.get("radio", {})
    if not all(isinstance(section, dict) for section in (audio, ptt, safety, radio)):
        raise ValueError("配置的 [audio]、[ptt]、[safety]、[radio] 必须是 TOML 表")
    return Config(audio.get("tx_device"), audio.get("rx_device"), ptt.get("port"),
                  ptt.get("firmware"), ptt.get("bench_verified", False),
                  safety.get("legal_operation_confirmed", False), audio.get("gain", 0.1),
                  ptt.get("pre_delay_s", 0.3), ptt.get("tail_delay_s", 0.2),
                  ptt.get("max_tx_seconds", 60.0), radio.get("frequency_hz"),
                  radio.get("power_setting"))


def validate_config_values(config: Config) -> None:
    """检查编辑器可保存的字段；不把未完成台架确认当作错误。"""
    for name in ("tx_device", "rx_device"):
        value = getattr(config, name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int))
                                  or len(str(value)) > 256 or isinstance(value, int) and value < 0):
            raise ValueError(f"{name} 必须是设备名称或非负索引")
    for name, limit in (("ptt_port", 128), ("firmware", 32), ("power_setting", 64)):
        value = getattr(config, name)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError(f"{name} 必须是不超过 {limit} 字符的文本")
    if not isinstance(config.ptt_bench_verified, bool) or not isinstance(config.legal_operation_confirmed, bool):
        raise ValueError("台架与合规确认必须是布尔值")
    if config.frequency_hz is not None and (isinstance(config.frequency_hz, bool)
                                             or not isinstance(config.frequency_hz, int)
                                             or config.frequency_hz <= 0):
        raise ValueError("frequency_hz 必须是正整数 Hz")
    for name in ("gain", "pre_delay_s", "tail_delay_s", "max_tx_seconds"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数值")
    if not 0 < config.gain <= 0.25 or config.pre_delay_s < 0 or config.tail_delay_s < 0 or not 0 < config.max_tx_seconds <= 60:
        raise ValueError("增益、延时或 60 s 连续发射上限无效")


def save_config(path: str | Path, config: Config, *, overwrite: bool = False) -> Path:
    """保存应用可编辑的 TOML 字段；覆盖前由调用方确认，保留旧文件备份。"""
    target = Path(path).expanduser()
    if target.suffix.lower() != ".toml" or target.is_dir() or target.is_symlink():
        raise ValueError(f"配置路径必须是普通 .toml 文件：{target}")
    if target.exists() and not overwrite:
        raise ValueError(f"配置已存在：{target}；确认后才可覆盖")
    validate_config_values(config)

    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ["# RadioLab 设备配置；保存文件不会打开串口或发射。", "[audio]"]
    for key, value in (("tx_device", config.tx_device), ("rx_device", config.rx_device)):
        if value is not None:
            lines.append(f"{key} = {quote(value) if isinstance(value, str) else value}")
    lines.extend((f"gain = {config.gain}", "", "[ptt]"))
    for key, value in (("port", config.ptt_port), ("firmware", config.firmware)):
        if value is not None:
            lines.append(f"{key} = {quote(value)}")
    lines.extend((f"bench_verified = {str(config.ptt_bench_verified).lower()}",
                  f"pre_delay_s = {config.pre_delay_s}", f"tail_delay_s = {config.tail_delay_s}",
                  f"max_tx_seconds = {config.max_tx_seconds}", "", "[safety]",
                  f"legal_operation_confirmed = {str(config.legal_operation_confirmed).lower()}",
                  "", "[radio]"))
    if config.frequency_hz is not None:
        lines.append(f"frequency_hz = {config.frequency_hz}")
    if config.power_setting is not None:
        lines.append(f"power_setting = {quote(config.power_setting)}")
    content = "\n".join(lines) + "\n"
    try:
        tomllib.loads(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_name(f"{target.name}.{uuid4().hex[:8]}.bak")
            shutil.copy2(target, backup)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=target.parent,
                                         prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"无法保存配置 {target}：{exc}") from exc
    return target


def validate_on_air(config: Config) -> None:
    for name in ("gain", "pre_delay_s", "tail_delay_s", "max_tx_seconds"):
        if isinstance(getattr(config, name), bool) or not isinstance(getattr(config, name), (int, float)):
            raise ValueError(f"配置 {name} 必须是数值")
        if not math.isfinite(getattr(config, name)):
            raise ValueError(f"配置 {name} 必须是有限数值")
    if not isinstance(config.ptt_bench_verified, bool) or not isinstance(config.legal_operation_confirmed, bool):
        raise ValueError("bench_verified 与 legal_operation_confirmed 必须是布尔值")
    if (isinstance(config.tx_device, bool) or not isinstance(config.tx_device, (str, int))
            or not str(config.tx_device).strip() or not isinstance(config.ptt_port, str)
            or not config.ptt_port.strip()):
        raise ValueError("真实发射需在配置中明确指定 audio.tx_device 与 ptt.port")
    if config.firmware != "1.2+" or not config.ptt_bench_verified:
        raise ValueError("需确认 AIOC 固件为 1.2+，并先在无辐射台架验证 PTT 线状态")
    if not config.legal_operation_confirmed:
        raise ValueError("需先确认合规频率、操作权限和接线，再设置 safety.legal_operation_confirmed=true")
    if config.frequency_hz is not None and (isinstance(config.frequency_hz, bool)
                                             or not isinstance(config.frequency_hz, int)
                                             or config.frequency_hz <= 0):
        raise ValueError("radio.frequency_hz 必须是正整数 Hz；仅作记录，不会设置手台")
    if config.power_setting is not None and (not isinstance(config.power_setting, str)
                                             or len(config.power_setting) > 64):
        raise ValueError("radio.power_setting 必须是不超过 64 字符的说明")
    if not 0 < config.gain <= 0.25 or config.pre_delay_s < 0 or config.tail_delay_s < 0 or not 0 < config.max_tx_seconds <= 60:
        raise ValueError("发射增益/延时/60 s 上限配置无效")
