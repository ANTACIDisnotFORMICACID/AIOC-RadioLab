"""历史导出与 SSTV 解码后台任务。"""
from pathlib import Path
import sqlite3

from PySide6.QtCore import QThread, Signal

from radiolab.sstv_rx import SSTVReceiveService
from radiolab.storage import SQLiteHistoryService


class SSTVDecodeWorker(QThread):
    succeeded = Signal(str, str)
    failed = Signal(str)

    def __init__(self, wav: Path, image: Path, store: SQLiteHistoryService) -> None:
        super().__init__()
        self.wav = wav
        self.image = image
        self.store = store

    def run(self) -> None:
        try:
            result = SSTVReceiveService(history=self.store).decode_file(self.wav, self.image)
        except (ValueError, OSError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(result.image_path), str(result.report_path))


class HistoryExportWorker(QThread):
    succeeded = Signal(str, int, int, int)
    failed = Signal(str)

    def __init__(self, store: SQLiteHistoryService, output: Path) -> None:
        super().__init__()
        self.store = store
        self.output = output

    def run(self) -> None:
        try:
            result = self.store.export_bundle(self.output)
        except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(result.path), result.entries,
                                result.copied_files, result.missing_files)
