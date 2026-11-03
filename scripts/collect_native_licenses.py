"""核对 Windows 包内 Conda 原生文件，并收集对应包缓存的许可材料。"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from hashlib import file_digest
import json
from pathlib import Path
import re
import shutil


BUNDLES = (
    ("CLI", "radiolab", Path("cli/radiolab/COLLECT-00.toc")),
    ("GUI", "radiolab-gui", Path("gui/radiolab-gui/COLLECT-00.toc")),
)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest().upper()


def _installed_owners(prefix: Path) -> dict[str, tuple[dict, str | None]]:
    owners: dict[str, tuple[dict, str | None]] = {}
    metadata_dir = prefix / "conda-meta"
    if not metadata_dir.is_dir():
        raise ValueError(f"找不到 Conda 包元数据：{metadata_dir}")
    for metadata_file in metadata_dir.glob("*.json"):
        record = json.loads(metadata_file.read_text(encoding="utf-8"))
        cache_paths = Path(record.get("extracted_package_dir", "")) / "info" / "paths.json"
        expected = {}
        if cache_paths.is_file():
            paths = json.loads(cache_paths.read_text(encoding="utf-8")).get("paths", ())
            expected = {item["_path"].replace("\\", "/").casefold(): item.get("sha256")
                        for item in paths}
        for relative in record.get("files", ()):
            source = (prefix / relative).resolve()
            owners[str(source).casefold()] = (record, expected.get(relative.replace("\\", "/").casefold()))
    return owners


def collect_native_licenses(output_root: Path, build_root: Path, conda_prefix: Path) -> list[str]:
    """只认实际打包清单；源文件哈希不符就失败，缺许可文本列为待核。"""
    output_root, build_root, conda_prefix = (
        Path(output_root).resolve(), Path(build_root).resolve(), Path(conda_prefix).resolve())
    notices = output_root / "third_party_licenses"
    if not notices.is_dir():
        raise ValueError(f"先生成第三方许可目录：{notices}")
    owners = _installed_owners(conda_prefix)
    rows: list[tuple[str, str, str, str]] = []
    packages: dict[tuple[str, str, str], dict] = {}
    counts: Counter[tuple[str, str, str]] = Counter()
    for label, bundle_name, toc_relative in BUNDLES:
        toc = build_root / toc_relative
        if not toc.is_file():
            raise ValueError(f"缺少 PyInstaller 实际打包清单：{toc}")
        entries = ast.literal_eval(toc.read_text(encoding="utf-8"))[0]
        internal = (output_root / bundle_name / "_internal").resolve()
        for destination, source_name, kind in entries:
            if kind not in {"BINARY", "EXTENSION"}:
                continue
            source = Path(source_name).resolve()
            if not source.is_relative_to(conda_prefix):
                continue
            owned = owners.get(str(source).casefold())
            if owned is None:
                raise ValueError(f"包内 Conda 原生文件找不到所属包：{source}")
            owner, expected_digest = owned
            if not expected_digest:
                raise ValueError(f"Conda 包缓存缺少原生文件 SHA-256：{source}")
            target = (internal / destination).resolve()
            if not target.is_relative_to(internal) or not target.is_file():
                raise ValueError(f"包内原生文件不存在或路径越界：{target}")
            digest = _sha256(source)
            if digest != expected_digest.upper():
                raise ValueError(f"Conda 原生文件与包缓存哈希不符：{source}")
            if _sha256(target) != digest:
                raise ValueError(f"包内原生文件与 Conda 来源哈希不符：{target}")
            key = (owner["name"], owner["version"], owner["build"])
            packages[key] = owner
            counts[(label, *key)] += 1
            rows.append((label, destination.replace("\\", "/"), owner["name"], digest))

    missing: list[str] = []
    package_rows = []
    for (name, version, build), record in sorted(packages.items()):
        if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in (name, version, build)):
            raise ValueError(f"Conda 包名/版本含不安全字符：{name} {version} {build}")
        package_dir = notices / f"conda-{name}-{version}-{build}"
        package_dir.mkdir(exist_ok=False)
        cache = Path(record.get("extracted_package_dir", "")) / "info" / "licenses"
        license_files = sorted(path for path in cache.rglob("*") if path.is_file()) if cache.is_dir() else []
        for index, source in enumerate(license_files, 1):
            shutil.copyfile(source, package_dir / f"{index:02d}-{source.name}")
        if not license_files:
            missing.append(f"Conda {name} {version}")
        package_rows.append(
            (name, version, build, record.get("license", "未声明"),
             counts[("CLI", name, version, build)], counts[("GUI", name, version, build)],
             len(license_files), record.get("url", "未记录来源 URL")))

    inventory = [
        "# Conda 原生文件清单（构建快照）", "",
        "按 PyInstaller COLLECT 清单核对包内文件、已安装来源与 Conda 包缓存 paths.json 的 SHA-256；许可名称来自本机 Conda 元数据，",
        "许可文本来自对应包缓存的 info/licenses。此清单只证明文件来源与材料收集，不构成分发许可结论。", "",
        "| Conda 包 | 版本 / 构建 | 元数据许可 | CLI / GUI 文件数 | 许可文件 | 来源包 |",
        "|---|---|---|---:|---:|---|",
    ]
    inventory.extend(
        f"| {name} | {version} / {build} | {license_id} | {cli} / {gui} | {count} | {url} |"
        for name, version, build, license_id, cli, gui, count, url in package_rows)
    inventory.extend(("", "| 包 | 包内文件 | Conda 包 | SHA-256 |", "|---|---|---|---|"))
    inventory.extend(f"| {label} | {destination} | {name} | {digest} |"
                     for label, destination, name, digest in rows)
    (notices / "CONDA_NATIVE_BINARIES.md").write_text("\n".join(inventory) + "\n", encoding="utf-8")

    summary = [
        "", "## Conda 原生运行库", "",
        "包内文件、已安装来源与 Conda 包缓存 SHA-256 逐个一致；详细文件、包版本、来源与哈希见 [CONDA_NATIVE_BINARIES.md](CONDA_NATIVE_BINARIES.md)。",
        "许可标签来自本机 Conda 元数据，文本从对应缓存的 info/licenses 收集；",
        "Microsoft/Qt/其他组件的实际分发条件仍须人工核对。", "",
    ]
    summary.extend(f"- {name} {version} ({build})：{license_id}；收集 {count} 个许可文件"
                   for name, version, build, license_id, _, _, count, _ in package_rows)
    manifest = notices / "THIRD_PARTY_NOTICES.md"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(summary) + "\n")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="核对包内 Conda 原生文件及许可材料")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("build_root", type=Path)
    parser.add_argument("conda_prefix", type=Path)
    args = parser.parse_args()
    missing = collect_native_licenses(args.output_root, args.build_root, args.conda_prefix)
    print("Conda 原生文件和来源 SHA-256 已核对；缺许可文本：" + (", ".join(missing) or "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
