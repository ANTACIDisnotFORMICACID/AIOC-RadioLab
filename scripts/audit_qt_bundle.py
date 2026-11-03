"""核对目录包中的 Qt/PySide 原生文件与已安装 wheel、源码候选目录。"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
from pathlib import Path
import re
from zipfile import ZipFile


VERSION = "6.11.3"
ARCHIVES = {
    "pyside": f"pyside-setup-everywhere-src-{VERSION}.zip",
    "qtbase": f"qtbase-everywhere-src-{VERSION}.zip",
    "qtsvg": f"qtsvg-everywhere-src-{VERSION}.zip",
    "qtimageformats": f"qtimageformats-everywhere-src-{VERSION}.zip",
}
NATIVE_SUFFIXES = {".dll", ".pyd"}


def candidate(relative: Path) -> tuple[str, str] | None:
    """返回源码归档及可在其中核对的目录；None 表示尚无候选。"""
    parts = relative.parts
    name = relative.name.lower()
    if name == "opengl32sw.dll" or name.startswith(("msvcp140", "vcruntime140")):
        return None
    if parts[0] == "shiboken6":
        if name == "shiboken.pyd":
            return "pyside", "sources/shiboken6/shibokenmodule/"
        if name == "shiboken6.abi3.dll":
            return "pyside", "sources/shiboken6/libshiboken/"
    if parts[0] != "PySide6":
        raise ValueError(f"未知 Qt 原生文件：{relative}")
    modules = {"Qt6Core.dll": "corelib", "Qt6Gui.dll": "gui",
               "Qt6Network.dll": "network", "Qt6Widgets.dll": "widgets"}
    if relative.name in modules:
        return "qtbase", f"src/{modules[relative.name]}/"
    if relative.name == "Qt6Svg.dll":
        return "qtsvg", "src/svg/"
    if relative.name == "pyside6.abi3.dll":
        return "pyside", "sources/pyside6/libpyside/"
    if relative.suffix.lower() == ".pyd" and relative.stem.startswith("Qt"):
        return "pyside", f"sources/pyside6/PySide6/{relative.stem}/"
    if len(parts) == 4 and parts[1] == "plugins":
        group, plugin = parts[2], relative.stem.lower()
        if group == "imageformats":
            if plugin in {"qgif", "qico", "qjpeg"}:
                return "qtbase", f"src/plugins/imageformats/{plugin[1:]}/"
            if plugin in {"qicns", "qtga", "qtiff", "qwbmp", "qwebp"}:
                return "qtimageformats", f"src/plugins/imageformats/{plugin[1:]}/"
            if plugin == "qsvg":
                return "qtsvg", "src/plugins/imageformats/svg/"
        plugin_dirs = {
            ("generic", "qtuiotouchplugin"): "generic/tuiotouch",
            ("iconengines", "qsvgicon"): "iconengines/svgiconengine",
            ("networkinformation", "qnetworklistmanager"): "networkinformation/networklistmanager",
            ("platforms", "qdirect2d"): "platforms/direct2d",
            ("platforms", "qminimal"): "platforms/minimal",
            ("platforms", "qoffscreen"): "platforms/offscreen",
            ("platforms", "qwindows"): "platforms/windows",
            ("styles", "qmodernwindowsstyle"): "styles/modernwindows",
            ("tls", "qcertonlybackend"): "tls/certonly",
            ("tls", "qopensslbackend"): "tls/openssl",
            ("tls", "qschannelbackend"): "tls/schannel",
        }
        key = (group, plugin)
        if key in plugin_dirs:
            archive = "qtsvg" if group == "iconengines" else "qtbase"
            return archive, f"src/plugins/{plugin_dirs[key]}/"
    raise ValueError(f"未知 Qt 原生文件：{relative}；请先核对来源再扩充映射")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def external_hint(relative: Path, path: Path) -> str:
    """只记录可观察线索；文件名或嵌入字符串不能证明构建来源。"""
    if relative.name.lower() == "opengl32sw.dll":
        versions = sorted(set(re.findall(rb"\b(?:Mesa|LLVM) \d+\.\d+\.\d+\b", path.read_bytes())))
        label = ", ".join(version.decode("ascii") for version in versions) or "版本未识别"
        return f"Qt 文档归于 Mesa llvmpipe；包内字符串 {label}。准确源码/补丁待核对"
    return "文件名符合 Microsoft Visual C++ 运行库；签名及分发条件须单独核对"


def audit(bundle_root: Path, archive_root: Path,
          installed_roots: dict[str, Path] | None = None) -> tuple[int, int]:
    """只写清单；源码目录存在表示候选，不表示与二进制完整对应。"""
    if installed_roots is None:
        for package in ("PySide6-Essentials", "shiboken6"):
            actual = metadata.version(package)
            if actual != VERSION:
                raise ValueError(f"{package} 需为 {VERSION}，构建环境实际为 {actual}")
        installed_roots = {
            name: Path(metadata.distribution(dist).locate_file(name))
            for name, dist in (("PySide6", "PySide6-Essentials"), ("shiboken6", "shiboken6"))
        }
    archive_names: dict[str, set[str]] = {}
    for key, filename in ARCHIVES.items():
        path = archive_root / filename
        if not path.is_file():
            raise ValueError(f"缺少源码候选归档：{path}")
        with ZipFile(path) as archive:
            archive_names[key] = set(archive.namelist())

    internal = bundle_root / "radiolab-gui" / "_internal"
    native = sorted(
        path for name in ("PySide6", "shiboken6")
        for path in (internal / name).rglob("*")
        if path.is_file() and path.suffix.lower() in NATIVE_SUFFIXES
    )
    if not native:
        raise ValueError(f"未找到 Qt/PySide 原生文件：{internal}")
    rows: list[str] = []
    candidate_count = 0
    missing_source: list[str] = []
    for path in native:
        relative = path.relative_to(internal)
        wheel_path = installed_roots[relative.parts[0]].joinpath(*relative.parts[1:])
        if not wheel_path.is_file() or sha256(path) != sha256(wheel_path):
            raise ValueError(f"包内文件与已安装 wheel 不一致：{relative}；请核对构建来源")
        result = candidate(relative)
        if result is None:
            source = "未覆盖：" + external_hint(relative, path)
            missing_source.append(relative.as_posix())
        else:
            key, directory = result
            prefix = f"{ARCHIVES[key].removesuffix('.zip')}/{directory}"
            if not any(name.startswith(prefix) and name != prefix for name in archive_names[key]):
                raise ValueError(f"候选源码目录不存在：{ARCHIVES[key]} / {directory}（{relative}）")
            source = f"`{ARCHIVES[key]}` / `{directory}`（候选）"
            candidate_count += 1
        rows.append(f"| `{relative.as_posix()}` | `{sha256(path)}` | {source} |")

    lines = [
        f"# Qt/PySide {VERSION} 原生文件与源码候选映射", "",
        "本清单扫描 `radiolab-gui/_internal/PySide6` 和 `shiboken6` 下全部 DLL/PYD；",
        "每个包内文件均与当前构建环境中已安装 wheel 的 SHA-256 一致。",
        "源码列仅验证候选归档存在相应源码目录，不证明实际构建选项、补丁或第三方源码完整对应。", "",
        f"原生文件 {len(native)} 个；有模块源码候选 {candidate_count} 个；待单独核对 {len(missing_source)} 个。", "",
        "| 包内文件 | SHA-256 | 源码候选 / 缺口 |", "|---|---|---|", *rows, "",
        "待单独核对：" + ("、".join(f"`{p}`" for p in missing_source) if missing_source else "无") + "。", "",
        "还需核对 Qt 所带第三方代码、完整对应源码、分发条件，以及实际换库后的运行结果。", "",
    ]
    report = archive_root / "QT_BINARY_SOURCE_MAP.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return len(native), candidate_count


def main() -> int:
    parser = argparse.ArgumentParser(description="核对 Qt/PySide 包内原生文件与源码候选目录")
    parser.add_argument("bundle_root", type=Path)
    parser.add_argument("archive_root", type=Path)
    args = parser.parse_args()
    total, candidates = audit(args.bundle_root, args.archive_root)
    print(f"Qt 原生文件 {total} 个，源码目录候选 {candidates} 个；见 QT_BINARY_SOURCE_MAP.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
