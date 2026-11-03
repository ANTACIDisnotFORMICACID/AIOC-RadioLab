"""把经哈希核对的 Qt 6.11.3 官方源码候选归档附到预览目录包。"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
from zipfile import ZipFile, BadZipFile


VERSION = "6.11.3"
BASE_URL = "https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/"
SOURCES = {
    "pyside-setup-everywhere-src-6.11.3.zip": (
        "32D9A76CBF85B27916C8B1594B77FF50BC195AB7DF09ABAD725B1BE2CBA21065",
        "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.3-src/"
        "pyside-setup-everywhere-src-6.11.3.zip",
    ),
    "qtbase-everywhere-src-6.11.3.zip": (
        "97472E0013FC687B625A22D34074EC01196038638E893EBDBB9D1E048924E543",
        BASE_URL + "qtbase-everywhere-src-6.11.3.zip",
    ),
    "qtsvg-everywhere-src-6.11.3.zip": (
        "245A9AE444486950D7D920E931FEC283F50751F2C9062A5270DEFAD22A0CD81C",
        BASE_URL + "qtsvg-everywhere-src-6.11.3.zip",
    ),
    "qtimageformats-everywhere-src-6.11.3.zip": (
        "28244A0CA04FECDA8DA6B9A3B60FFA11AEEAFF38C1AFCE8C96068AC7E954E510",
        BASE_URL + "qtimageformats-everywhere-src-6.11.3.zip",
    ),
}


def stage_source_archives(cache: Path, output_root: Path,
                          sources: dict[str, tuple[str, str]] = SOURCES) -> Path:
    """先逐一验证完整性，全部通过后才复制；不覆盖已有归档目录。"""
    checked = []
    for filename, (expected_hash, url) in sources.items():
        source = cache / filename
        if not source.is_file():
            raise ValueError(f"缺少 Qt 源码归档：{source}；来源：{url}")
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest().upper()
        if digest != expected_hash.upper():
            raise ValueError(f"Qt 源码归档哈希不匹配：{source}；预期 {expected_hash}，实际 {digest}")
        try:
            with ZipFile(source) as archive:
                bad = archive.testzip()
        except BadZipFile as exc:
            raise ValueError(f"Qt 源码归档不是有效 ZIP：{source}") from exc
        if bad is not None:
            raise ValueError(f"Qt 源码归档损坏：{source} 内的 {bad}")
        checked.append((source, digest, url))

    target = output_root / "qt_source_archives"
    target.mkdir(parents=True, exist_ok=False)
    for source, _, _ in checked:
        shutil.copyfile(source, target / source.name)
    lines = [
        f"# Qt {VERSION} 源码候选归档", "",
        "下列归档从 Qt 官方下载并在复制前核对 SHA-256 与 ZIP 完整性。",
        "它们覆盖当前识别出的 PySide、QtBase、QtSvg、QtImageFormats 模块；",
        "尚未证明与包内所有原生文件的构建配置、第三方依赖和修改完全对应。", "",
        "| 文件 | SHA-256 | 上游来源 |", "|---|---|---|",
    ]
    lines.extend(f"| {source.name} | `{digest}` | {url} |" for source, digest, url in checked)
    lines.extend((
        "", "发布前须核对完整对应源码、用户替换库的可运行路径和分发条件；",
        "附带这些归档本身不表示已满足 Qt 许可义务。", "",
    ))
    (target / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="把核验过的 Qt 源码归档附到目录包")
    parser.add_argument("cache", type=Path, help="本地官方源码 ZIP 缓存目录")
    parser.add_argument("output_root", type=Path, help="已生成的 Windows 目录包根目录")
    args = parser.parse_args()
    print(f"Qt 源码候选归档：{stage_source_archives(args.cache, args.output_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
