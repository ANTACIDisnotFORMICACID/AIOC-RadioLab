"""发布元数据与运行时版本保持一致。"""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import runpy
import subprocess
import sys
import tarfile
import tomllib
from email.message import Message
from io import BytesIO
from zipfile import ZipFile

import pytest

from radiolab import __version__


def test_package_and_runtime_versions_match():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__


def test_cli_decodes_on_non_unicode_windows_console(tmp_path):
    root = Path(__file__).resolve().parents[1]
    fixture = root / "examples" / "hello_afsk.wav"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "-c", "from radiolab.cli import main; raise SystemExit(main())",
         "text", "decode", str(fixture)],
        cwd=tmp_path, env=env, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "CRC OK" in result.stdout.decode("utf-8")
    assert "HELLO WORLD" in result.stdout.decode("utf-8")


def test_license_collector_copies_text_and_flags_qt_gap(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect_licenses.py"
    collect = runpy.run_path(str(script))["collect_licenses"]
    source = tmp_path / "source-license.txt"
    source.write_text("Example license text", encoding="utf-8")
    fake = SimpleNamespace(metadata={"Name": "Example", "License-Expression": "MIT"},
                           version="1.2", files=[Path("example-1.2.dist-info/licenses/LICENSE.txt")],
                           locate_file=lambda item: source)
    missing = collect(tmp_path / "bundle", packages=("Example",), lookup=lambda name: fake)
    collected = tmp_path / "bundle" / "third_party_licenses"
    assert "Example" not in missing
    assert (collected / "example-1.2" / "01-source-license.txt").read_text() == "Example license text"
    assert "Example | 1.2 | MIT | 已收集 1 个文件" in (
        collected / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    commercial = SimpleNamespace(metadata={"Name": "shiboken6", "License": "LGPL-3.0-only"},
                                 version="6.11.3",
                                 files=[Path("shiboken6-6.11.3.dist-info/licenses/LicenseRef-Qt-Commercial.txt")],
                                 locate_file=lambda item: source)
    missing = collect(tmp_path / "qt-bundle", packages=("shiboken6",),
                       lookup=lambda name: commercial)
    assert "shiboken6" in missing
    qt_files = list((tmp_path / "qt-bundle" / "third_party_licenses" / "shiboken6-6.11.3").iterdir())
    assert len(qt_files) == 3
    assert all("Commercial" not in path.name for path in qt_files)
    assert any("LGPL-3.0-only" in path.name for path in qt_files)


def test_pyserial_upstream_license_is_version_pinned(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect_licenses.py"
    collect = runpy.run_path(str(script))["collect_licenses"]
    fake = SimpleNamespace(metadata={"Name": "pyserial", "License": "BSD"},
                           version="3.5", files=[], locate_file=lambda item: None)
    missing = collect(tmp_path, packages=("pyserial",), lookup=lambda name: fake)
    assert "pyserial" not in missing
    license_file = tmp_path / "third_party_licenses" / "pyserial-3.5" / "01-pyserial-3.5-LICENSE.txt"
    assert license_file.is_file()
    assert "Chris Liechti" in license_file.read_text(encoding="utf-8")


def test_long_license_metadata_uses_classifier_label():
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect_licenses.py"
    label = runpy.run_path(str(script))["_license_label"]
    fields = Message()
    fields["License"] = "Copyright Example\nFull license paragraphs follow."
    fields["Classifier"] = "License :: OSI Approved :: BSD License"
    assert label(SimpleNamespace(metadata=fields)) == "BSD License"


def test_qt_source_stage_checks_hash_before_copy(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "stage_qt_sources.py"
    stage = runpy.run_path(str(script))["stage_source_archives"]
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "module.zip"
    with ZipFile(archive, "w") as handle:
        handle.writestr("module/CMakeLists.txt", "project(module)")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    output = tmp_path / "package"
    with pytest.raises(ValueError, match="哈希不匹配"):
        stage(cache, output, {archive.name: ("0" * 64, "https://example.invalid/module.zip")})
    assert not (output / "qt_source_archives").exists()

    staged = stage(cache, output, {archive.name: (digest, "https://example.invalid/module.zip")})
    assert (staged / archive.name).read_bytes() == archive.read_bytes()
    assert digest.upper() in (staged / "README.md").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        stage(cache, output, {archive.name: (digest, "https://example.invalid/module.zip")})


def test_mesa_candidate_requires_hash_structure_and_bundle_version(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "stage_graphics_sources.py"
    module = runpy.run_path(str(script))
    stage = module["stage_mesa_source"]
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / module["ARCHIVE"]
    with tarfile.open(archive, "w:xz") as handle:
        for name in module["REQUIRED"]:
            payload = b"candidate source"
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            handle.addfile(entry, BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    output = tmp_path / "bundle"
    library = output / "radiolab-gui" / "_internal" / "PySide6" / "opengl32sw.dll"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"Qt binary; Mesa 11.2.2; LLVM 3.6.2")
    with pytest.raises(ValueError, match="哈希不匹配"):
        stage(cache, output, expected_hash="0" * 64)
    assert not (output / "graphics_source_candidates").exists()
    library.write_bytes(b"unknown renderer")
    with pytest.raises(ValueError, match="版本线索"):
        stage(cache, output, expected_hash=digest)
    library.write_bytes(b"Qt binary; Mesa 11.2.2; LLVM 3.6.2")
    target = stage(cache, output, expected_hash=digest)
    assert (target / archive.name).read_bytes() == archive.read_bytes()
    assert "完整对应源码" in (target / "README.md").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        stage(cache, output, expected_hash=digest)


def test_llvm_candidate_requires_pinned_archive_and_signature(tmp_path):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                "scripts" / "stage_graphics_sources.py"))
    stage = module["stage_llvm_source"]
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / module["LLVM_ARCHIVE"]
    with tarfile.open(archive, "w:xz") as handle:
        for name in ("llvm-3.6.2.src/LICENSE.TXT", "llvm-3.6.2.src/lib/Target/TargetMachine.cpp"):
            payload = b"test source"
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            handle.addfile(entry, BytesIO(payload))
    signature = cache / (archive.name + ".sig")
    signature.write_bytes(b"test detached signature")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sig_digest = hashlib.sha256(signature.read_bytes()).hexdigest()
    output = tmp_path / "bundle"
    library = output / "radiolab-gui" / "_internal" / "PySide6" / "opengl32sw.dll"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"LLVM 3.6.2")
    with pytest.raises(ValueError, match="哈希不匹配"):
        stage(cache, output, expected_hash=digest, expected_sig_hash="0" * 64)
    assert not (output / "llvm_source_candidates").exists()
    target = stage(cache, output, expected_hash=digest, expected_sig_hash=sig_digest)
    assert (target / archive.name).read_bytes() == archive.read_bytes()
    assert (target / signature.name).read_bytes() == signature.read_bytes()


def test_qt_bundle_audit_rejects_unmapped_and_changed_binary(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "audit_qt_bundle.py"
    audit = runpy.run_path(str(script))["audit"]
    source = tmp_path / "sources"
    source.mkdir()
    for module in ("pyside-setup", "qtbase", "qtsvg", "qtimageformats"):
        with ZipFile(source / f"{module}-everywhere-src-6.11.3.zip", "w") as archive:
            if module == "qtbase":
                archive.writestr("qtbase-everywhere-src-6.11.3/src/corelib/CMakeLists.txt", "project(corelib)")
    internal = tmp_path / "bundle" / "radiolab-gui" / "_internal" / "PySide6"
    internal.mkdir(parents=True)
    installed = tmp_path / "installed"
    installed.mkdir()
    (internal / "Qt6Core.dll").write_bytes(b"qt-core")
    (installed / "Qt6Core.dll").write_bytes(b"qt-core")
    roots = {"PySide6": installed, "shiboken6": tmp_path / "empty"}
    assert audit(tmp_path / "bundle", source, roots) == (1, 1)
    assert "Qt6Core.dll" in (source / "QT_BINARY_SOURCE_MAP.md").read_text(encoding="utf-8")
    (internal / "opengl32sw.dll").write_bytes(b"renderer Mesa 11.2.2 LLVM 3.6.2")
    (installed / "opengl32sw.dll").write_bytes(b"renderer Mesa 11.2.2 LLVM 3.6.2")
    assert audit(tmp_path / "bundle", source, roots) == (2, 1)
    assert "Mesa 11.2.2" in (source / "QT_BINARY_SOURCE_MAP.md").read_text(encoding="utf-8")
    assert "LLVM 3.6.2" in (source / "QT_BINARY_SOURCE_MAP.md").read_text(encoding="utf-8")
    (internal / "Qt6Core.dll").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="wheel 不一致"):
        audit(tmp_path / "bundle", source, roots)
    (internal / "Qt6Core.dll").write_bytes(b"qt-core")
    (internal / "Qt6Mystery.dll").write_bytes(b"unknown")
    (installed / "Qt6Mystery.dll").write_bytes(b"unknown")
    with pytest.raises(ValueError, match="未知 Qt 原生文件"):
        audit(tmp_path / "bundle", source, roots)


def test_conda_native_files_match_bundle_and_collect_license(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect_native_licenses.py"
    collect = runpy.run_path(str(script))["collect_native_licenses"]
    prefix, build, output = (tmp_path / name for name in ("prefix", "build", "bundle"))
    source = prefix / "Library" / "bin" / "example.dll"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"native-binary")
    cache = tmp_path / "package-cache"
    license_file = cache / "info" / "licenses" / "LICENSE"
    license_file.parent.mkdir(parents=True)
    license_file.write_text("Example license", encoding="utf-8")
    (cache / "info" / "paths.json").write_text(json.dumps({"paths": [{
        "_path": "Library/bin/example.dll",
        "sha256": hashlib.sha256(b"native-binary").hexdigest(),
    }]}), encoding="utf-8")
    metadata_dir = prefix / "conda-meta"
    metadata_dir.mkdir()
    (metadata_dir / "example.json").write_text(json.dumps({
        "name": "example", "version": "1.0", "build": "test_0", "license": "MIT",
        "url": "https://example.invalid/example.conda",
        "files": ["Library/bin/example.dll"], "extracted_package_dir": str(cache),
    }), encoding="utf-8")
    for section, name in (("cli", "radiolab"), ("gui", "radiolab-gui")):
        toc = build / section / name / "COLLECT-00.toc"
        toc.parent.mkdir(parents=True)
        toc.write_text(repr(([("example.dll", str(source), "BINARY")],)), encoding="utf-8")
        target = output / name / "_internal" / "example.dll"
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
    notices = output / "third_party_licenses"
    notices.mkdir()
    missing = collect(output, build, prefix)
    assert missing == []
    assert (notices / "conda-example-1.0-test_0" / "01-LICENSE").read_text() == "Example license"
    inventory = (notices / "CONDA_NATIVE_BINARIES.md").read_text(encoding="utf-8")
    assert "1 / 1" in inventory and hashlib.sha256(b"native-binary").hexdigest().upper() in inventory
    assert "Conda 原生运行库" in (notices / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    (output / "radiolab-gui" / "_internal" / "example.dll").write_bytes(b"changed")
    with pytest.raises(ValueError, match="哈希不符"):
        collect(output, build, prefix)
    source.write_bytes(b"changed")
    (output / "radiolab" / "_internal" / "example.dll").write_bytes(b"changed")
    with pytest.raises(ValueError, match="包缓存哈希不符"):
        collect(output, build, prefix)
