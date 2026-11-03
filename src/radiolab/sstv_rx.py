"""SSTV 接收事务：隔离本次输出并记录图片、日志与历史。"""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from . import sstv
from .history import HistoryService, make_entry
from .storage import SQLiteHistoryService


@dataclass(frozen=True)
class SSTVDecodeResult:
    image_path: Path
    report_path: Path
    decoder_version: str
    decoder_version_source: str
    decoder_name: str = "RadioLab 自写 SSTV"


class SSTVReceiveService:
    def __init__(self, history: HistoryService | SQLiteHistoryService | None = None,
                 mode: str = "robot36") -> None:
        self.history = history or HistoryService()
        self.mode = mode

    def decode_file(self, wav_path: str | Path, image_path: str | Path, *,
                    report_path: str | Path | None = None) -> SSTVDecodeResult:
        output = Path(image_path)
        report = Path(report_path) if report_path is not None else output.with_suffix(".decode.log")
        version = "0.1.0"
        source = "built-in algorithm"
        fresh_report = False
        try:
            wav = Path(wav_path)
            if not wav.is_file():
                raise ValueError(f"SSTV 输入 WAV 不存在：{wav}")
            if len({wav.resolve(), output.resolve(), report.resolve()}) != 3:
                raise ValueError("SSTV 输入 WAV、输出图片和解码日志必须使用不同路径")
            output.parent.mkdir(parents=True, exist_ok=True)
            report.parent.mkdir(parents=True, exist_ok=True)
            # 每次给后端全新路径；旧图片/日志不能充当本次解码结果。
            with TemporaryDirectory(dir=output.parent) as image_dir, \
                    TemporaryDirectory(dir=report.parent) as report_dir:
                staged_image = Path(image_dir) / output.name
                staged_report = Path(report_dir) / report.name
                try:
                    image = sstv.decode_sstv_file(wav, staged_image, mode=self.mode, report_path=staged_report)
                    sstv.validate_sstv_image(image, self.mode)
                    if not staged_report.is_file():
                        raise ValueError(f"解码后端未生成本次日志：{report}")
                    staged_report.replace(report)
                    fresh_report = True
                    staged_image.replace(output)
                    image = output
                except (ValueError, OSError, RuntimeError):
                    if staged_report.is_file():
                        staged_report.replace(report)
                        fresh_report = True
                    raise
        except (ValueError, OSError, RuntimeError) as exc:
            self.history.append(make_entry("sstv", "decode", wav_path=wav_path,
                                           report_path=report if fresh_report else None,
                                           success=False, reason=str(exc), decoder_name="RadioLab 自写 SSTV",
                                           decoder_version=version, decoder_version_source=source))
            raise
        self.history.append(make_entry("sstv", "decode", wav_path=wav_path, image_path=image,
                                       report_path=report, reason="SSTV 图像通过后端校验",
                                       decoder_name="RadioLab 自写 SSTV", decoder_version=version,
                                       decoder_version_source=source))
        return SSTVDecodeResult(image, report, version, source)
