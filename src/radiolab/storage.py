"""V0.2 SQLite 历史存储；JSONL 原件在迁移时保持不变。"""
from dataclasses import asdict, dataclass, replace
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from zipfile import ZIP_DEFLATED, ZipFile

from .history import HistoryEntry


_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    run_id TEXT PRIMARY KEY,
    time_utc TEXT NOT NULL,
    mode TEXT NOT NULL,
    operation TEXT NOT NULL,
    success INTEGER NOT NULL,
    text_content TEXT,
    wav_path TEXT,
    image_path TEXT,
    entry_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_time ON history(time_utc DESC);
CREATE INDEX IF NOT EXISTS history_mode_time ON history(mode, time_utc DESC);
"""


@dataclass(frozen=True)
class ExportSummary:
    path: Path
    entries: int
    copied_files: int
    missing_files: int
    total_bytes: int


@dataclass(frozen=True)
class RecentFile:
    path: Path
    kind: str
    time: str
    exists: bool


class SQLiteHistoryService:
    def __init__(self, path: str | Path = "runtime/history.sqlite3") -> None:
        self.path = Path(path)
        if self.path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError(f"SQLite 历史路径需要 .db/.sqlite/.sqlite3 后缀：{self.path}")
        if self.path.is_dir():
            raise ValueError(f"SQLite 历史路径不能是目录：{self.path}")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.executescript(_SCHEMA)
        return connection

    @staticmethod
    def _insert(connection: sqlite3.Connection, entry: HistoryEntry) -> bool:
        if not entry.run_id:
            raise ValueError("SQLite 历史记录需要 run_id")
        serialized = json.dumps(asdict(entry), ensure_ascii=False)
        before = connection.total_changes
        connection.execute(
            "INSERT OR IGNORE INTO history "
            "(run_id, time_utc, mode, operation, success, text_content, wav_path, image_path, entry_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.run_id, entry.time, entry.mode, entry.operation, int(entry.success), entry.text,
             entry.wav_path, entry.image_path, serialized),
        )
        if connection.total_changes > before:
            return True
        previous = connection.execute("SELECT entry_json FROM history WHERE run_id = ?", (entry.run_id,)).fetchone()
        if previous is None or previous[0] != serialized:
            raise ValueError(f"历史 run_id {entry.run_id} 已存在但内容不同；请核对 JSONL 来源")
        return False

    def append(self, entry: HistoryEntry) -> None:
        with closing(self._connect()) as connection, connection:
            self._insert(connection, entry)

    def list_recent(self, limit: int = 50, *, mode: str | None = None,
                    search: str | None = None) -> list[HistoryEntry]:
        if limit < 1 or limit > 1000:
            raise ValueError("历史查询 limit 必须在 1..1000")
        if not self.path.is_file():
            return []
        where = []
        parameters: list[str | int] = []
        if mode:
            where.append("mode = ?")
            parameters.append(mode)
        if search:
            where.append("text_content LIKE ? ESCAPE '\\'")
            parameters.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        query = "SELECT entry_json FROM history"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY time_utc DESC, rowid DESC LIMIT ?"
        parameters.append(limit)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [HistoryEntry(**json.loads(row[0])) for row in rows]

    def list_image_entries(self, limit: int = 200) -> list[HistoryEntry]:
        """按时间列出有图片引用的记录；文件是否仍存在由界面实时检查。"""
        if limit < 1 or limit > 1000:
            raise ValueError("图片库 limit 必须在 1..1000")
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT entry_json FROM history WHERE image_path IS NOT NULL AND image_path != '' "
                "ORDER BY time_utc DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        return [HistoryEntry(**json.loads(row[0])) for row in rows]

    def list_recent_files(self, limit: int = 100) -> list[RecentFile]:
        """从最近历史中去重列出 WAV、图片、日志，实时核对文件存在性。"""
        if limit < 1 or limit > 500:
            raise ValueError("最近文件 limit 必须在 1..500")
        rows = self.list_recent(limit=1000)
        files: list[RecentFile] = []
        seen: set[Path] = set()
        for entry in rows:
            for kind, source in (("WAV", entry.wav_path), ("图片", entry.image_path),
                                 ("日志", entry.report_path)):
                if not source:
                    continue
                path = Path(source)
                key = path.resolve()
                if key in seen:
                    continue
                seen.add(key)
                files.append(RecentFile(path, kind, entry.local_time or entry.time, path.is_file()))
                if len(files) >= limit:
                    return files
        return files

    def migrate_jsonl(self, source: str | Path) -> int:
        """逐行导入，按 run_id 去重；旧行生成稳定 ID。任何坏行使本次导入回滚。"""
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"JSONL 历史文件不存在：{source}")
        inserted = 0
        if source.resolve() == self.path.resolve():
            raise ValueError("JSONL 来源与 SQLite 目标不能是同一路径")
        with closing(self._connect()) as connection, connection:
            with source.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        entry = HistoryEntry(**json.loads(line))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"JSONL 第 {number} 行无效：{exc}") from exc
                    if not entry.run_id:
                        digest = sha256(f"{number}:{line}".encode("utf-8")).hexdigest()[:32]
                        entry = replace(entry, run_id=f"legacy-{digest}")
                    inserted += self._insert(connection, entry)
        return inserted

    def export_bundle(self, destination: str | Path) -> ExportSummary:
        """导出全部 SQLite 历史和现存媒体；不修改原始数据库或文件。"""
        target = Path(destination)
        if target.suffix.lower() != ".zip" or target.is_dir():
            raise ValueError(f"历史导出目标必须是新的 .zip 文件：{target}")
        if target.exists():
            raise ValueError(f"历史导出目标已存在：{target}；请换路径保留原文件")
        if not self.path.is_file():
            raise ValueError(f"SQLite 历史不存在：{self.path}；先运行 history migrate")
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("SELECT entry_json FROM history ORDER BY time_utc, rowid").fetchall()
        entries = [HistoryEntry(**json.loads(row[0])) for row in rows]
        sources = {str(Path(path).resolve()) for entry in entries
                   for path in (entry.wav_path, entry.image_path, entry.report_path) if path}
        if target.resolve() in {Path(source) for source in sources} or target.resolve() == self.path.resolve():
            raise ValueError("历史导出目标不能与数据库或历史中的媒体文件同路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        copied: dict[str, str | None] = {}
        files = []
        total_bytes = 0
        created = False
        try:
            with ZipFile(target, mode="x", compression=ZIP_DEFLATED) as bundle:
                created = True
                for index, source_text in enumerate(sorted(sources), 1):
                    source = Path(source_text)
                    if not source.is_file():
                        copied[source_text] = None
                        files.append({"source_path": source_text, "archive_path": None,
                                      "exists": False, "bytes": 0, "sha256": None})
                        continue
                    archive_path = f"media/{index:06d}{source.suffix.lower()}"
                    digest = sha256()
                    size = 0
                    with source.open("rb") as input_file, bundle.open(archive_path, "w", force_zip64=True) as output_file:
                        while chunk := input_file.read(1024 * 1024):
                            output_file.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    copied[source_text] = archive_path
                    total_bytes += size
                    files.append({"source_path": source_text, "archive_path": archive_path,
                                  "exists": True, "bytes": size, "sha256": digest.hexdigest()})
                portable = []
                for entry in entries:
                    changes = {}
                    for field in ("wav", "image", "report"):
                        source_text = getattr(entry, f"{field}_path")
                        if source_text:
                            archive_path = copied[str(Path(source_text).resolve())]
                            changes[f"{field}_path"] = archive_path
                            changes[f"{field}_exists"] = archive_path is not None
                    portable.append(json.dumps(asdict(replace(entry, **changes)), ensure_ascii=False))
                bundle.writestr("entries.jsonl", "\n".join(portable) + ("\n" if portable else ""))
                bundle.writestr("manifest.json", json.dumps({
                    "format": "radiolab-history-export-v1",
                    "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                    "entries": len(entries), "files": files,
                }, ensure_ascii=False, indent=2))
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        return ExportSummary(target, len(entries), sum(item["exists"] for item in files),
                             sum(not item["exists"] for item in files), total_bytes)
