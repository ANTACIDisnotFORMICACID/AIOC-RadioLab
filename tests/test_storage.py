from dataclasses import asdict
import json
from hashlib import sha256
from zipfile import ZipFile

import pytest

from radiolab.cli import main
from radiolab.history import make_entry
from radiolab.storage import SQLiteHistoryService


def test_jsonl_migration_is_idempotent_and_searchable(tmp_path, capsys):
    source = tmp_path / "history.jsonl"
    old = {"time": "2026-10-28T00:00:00+00:00", "mode": "text", "operation": "decode",
           "device": None, "wav_path": str(tmp_path / "missing.wav"), "wav_exists": False,
           "text": "A_B%", "sequence": 1, "crc_ok": True, "success": True, "reason": None}
    current = make_entry("sstv", "decode", image_path=tmp_path / "missing.png",
                         decoder_name="Open-SSTV", decoder_version="0.6.17")
    source.write_text(json.dumps(old) + "\n" + json.dumps(asdict(current)) + "\n", encoding="utf-8")
    original = source.read_bytes()
    database = tmp_path / "history.sqlite3"

    assert main(["history", "migrate", "--source", str(source), "-o", str(database)]) == 0
    store = SQLiteHistoryService(database)
    assert len(store.list_recent()) == 2
    assert store.list_recent(search="A_B%")[0].text == "A_B%"
    assert store.list_recent(search="A_BX") == []
    assert store.list_recent(mode="sstv")[0].decoder_version == "0.6.17"
    assert main(["history", "migrate", "--source", str(source), "-o", str(database)]) == 0
    assert len(store.list_recent()) == 2 and source.read_bytes() == original
    assert store.list_recent(mode="text")[0].run_id.startswith("legacy-")
    assert main(["history", "list", "--database", str(database), "--mode", "sstv"]) == 0
    assert "Open-SSTV 0.6.17" in capsys.readouterr().out


def test_jsonl_migration_bad_line_rolls_back(tmp_path):
    source = tmp_path / "history.jsonl"
    source.write_text(json.dumps(asdict(make_entry("text", "decode"))) + "\nnot json\n", encoding="utf-8")
    store = SQLiteHistoryService(tmp_path / "history.sqlite3")
    with pytest.raises(ValueError, match="第 2 行"):
        store.migrate_jsonl(source)
    assert store.list_recent() == []


def test_sqlite_run_id_conflict_is_rejected(tmp_path):
    store = SQLiteHistoryService(tmp_path / "history.sqlite3")
    first = make_entry("text", "decode", text="A")
    store.append(first)
    from dataclasses import replace
    with pytest.raises(ValueError, match="内容不同"):
        store.append(replace(first, text="B"))
    assert store.list_recent()[0].text == "A"


def test_history_export_bundle_is_portable_and_marks_missing_media(tmp_path):
    wav = tmp_path / "received.wav"
    wav.write_bytes(b"RIFF test audio")
    image = tmp_path / "decoded.png"
    image.write_bytes(b"PNG test image")
    missing = tmp_path / "missing.decode.log"
    store = SQLiteHistoryService(tmp_path / "history.sqlite3")
    first = make_entry("text", "decode", text="私密文本", wav_path=wav,
                       sequence=3, crc_ok=True)
    second = make_entry("sstv", "decode", wav_path=wav, image_path=image,
                        report_path=missing, decoder_name="Open-SSTV")
    store.append(first)
    store.append(second)
    original_wav = wav.read_bytes()
    destination = tmp_path / "export.zip"
    summary = store.export_bundle(destination)
    assert (summary.entries, summary.copied_files, summary.missing_files,
            summary.total_bytes) == (2, 2, 1, len(wav.read_bytes()) + len(image.read_bytes()))
    with ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entries = [json.loads(line) for line in archive.read("entries.jsonl").decode().splitlines()]
        assert manifest["format"] == "radiolab-history-export-v1"
        assert len(entries) == 2 and entries[0]["text"] == "私密文本"
        assert entries[0]["wav_path"] == entries[1]["wav_path"]
        assert entries[1]["report_path"] is None and entries[1]["report_exists"] is False
        assert archive.read(entries[0]["wav_path"]) == original_wav
        assert archive.read(entries[1]["image_path"]) == image.read_bytes()
        assert sum(not file["exists"] for file in manifest["files"]) == 1
        for file in manifest["files"]:
            if file["exists"]:
                assert sha256(archive.read(file["archive_path"])).hexdigest() == file["sha256"]
    assert wav.read_bytes() == original_wav
    with pytest.raises(ValueError, match="已存在"):
        store.export_bundle(destination)


def test_history_export_requires_existing_database(tmp_path):
    store = SQLiteHistoryService(tmp_path / "missing.sqlite3")
    with pytest.raises(ValueError, match="SQLite 历史不存在"):
        store.export_bundle(tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()


def test_history_export_cli_writes_zip_without_hardware(tmp_path, capsys):
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoryService(database)
    store.append(make_entry("text", "decode", text="HELLO", crc_ok=True))
    output = tmp_path / "history.zip"
    assert main(["history", "export", "--database", str(database), "-o", str(output)]) == 0
    assert output.is_file() and "已导出 1 条" in capsys.readouterr().out
    with ZipFile(output) as archive:
        assert len(archive.read("entries.jsonl").splitlines()) == 1


def test_image_entries_query_includes_missing_and_ignores_nonimage_rows(tmp_path):
    store = SQLiteHistoryService(tmp_path / "history.sqlite3")
    image = tmp_path / "photo.png"
    image.write_bytes(b"image")
    store.append(make_entry("text", "decode", text="NO IMAGE"))
    store.append(make_entry("sstv", "encode", image_path=image))
    store.append(make_entry("sstv", "decode", image_path=tmp_path / "missing.png"))
    rows = store.list_image_entries()
    assert [row.operation for row in rows] == ["decode", "encode"]
    assert rows[0].image_path.endswith("missing.png") and rows[1].image_path == str(image)
    with pytest.raises(ValueError, match="limit"):
        store.list_image_entries(1001)


def test_recent_files_deduplicates_and_checks_current_existence(tmp_path):
    store = SQLiteHistoryService(tmp_path / "history.sqlite3")
    wav = tmp_path / "audio.wav"
    image = tmp_path / "image.png"
    wav.write_bytes(b"audio")
    image.write_bytes(b"image")
    missing = tmp_path / "missing.log"
    store.append(make_entry("sstv", "encode", wav_path=wav, image_path=image))
    store.append(make_entry("sstv", "decode", wav_path=wav, image_path=image,
                            report_path=missing))
    files = store.list_recent_files()
    assert [(item.kind, item.path.name, item.exists) for item in files] == [
        ("WAV", "audio.wav", True), ("图片", "image.png", True),
        ("日志", "missing.log", False)]
    image.unlink()
    assert store.list_recent_files()[1].exists is False
    assert len(store.list_recent_files(limit=2)) == 2
    with pytest.raises(ValueError, match="limit"):
        store.list_recent_files(0)
