"""追加式 JSONL；缺失 WAV 显式标记。"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class HistoryEntry:
    time: str
    mode: str
    operation: str
    device: str | int | None
    wav_path: str | None
    wav_exists: bool
    text: str | None
    sequence: int | None
    crc_ok: bool | None
    success: bool
    reason: str | None
    run_id: str | None = None
    local_time: str | None = None
    direction: str | None = None
    firmware: str | None = None
    sample_rate: int | None = None
    gain: float | None = None
    frequency_hz: int | None = None
    power_setting: str | None = None
    payload_bytes: int | None = None
    image_path: str | None = None
    image_exists: bool | None = None
    report_path: str | None = None
    report_exists: bool | None = None
    ptt_port: str | None = None
    decoder_name: str | None = None
    decoder_version: str | None = None
    decoder_version_source: str | None = None


def make_entry(mode: str, operation: str, *, device: str | int | None = None,
               wav_path: str | Path | None = None, text: str | None = None,
               sequence: int | None = None, crc_ok: bool | None = None,
               success: bool = True, reason: str | None = None,
               firmware: str | None = None, sample_rate: int | None = None,
               gain: float | None = None, frequency_hz: int | None = None,
               power_setting: str | None = None, payload_bytes: int | None = None,
               image_path: str | Path | None = None,
               report_path: str | Path | None = None,
               ptt_port: str | None = None, decoder_name: str | None = None,
               decoder_version: str | None = None,
               decoder_version_source: str | None = None) -> HistoryEntry:
    path = str(Path(wav_path).resolve()) if wav_path is not None else None
    image = str(Path(image_path).resolve()) if image_path is not None else None
    report = str(Path(report_path).resolve()) if report_path is not None else None
    now = datetime.now(timezone.utc)
    direction = "tx" if operation == "send" else "rx" if operation in ("record", "decode") else "offline"
    return HistoryEntry(now.isoformat(), mode, operation, device,
                        path, bool(path and Path(path).is_file()), text, sequence, crc_ok, success, reason,
                        uuid4().hex, now.astimezone(timezone(timedelta(hours=8))).isoformat(), direction,
                        firmware, sample_rate, gain, frequency_hz, power_setting, payload_bytes,
                        image, bool(image and Path(image).is_file()) if image is not None else None,
                        report, bool(report and Path(report).is_file()) if report is not None else None,
                        ptt_port, decoder_name, decoder_version, decoder_version_source)


class HistoryService:
    def __init__(self, path: str | Path = "runtime/history.jsonl") -> None:
        self.path = Path(path)

    def append(self, entry: HistoryEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def list_recent(self, limit: int = 50) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            rows = [HistoryEntry(**json.loads(line)) for line in handle if line.strip()]
        return rows[-limit:][::-1]
