"""从当前构建环境收集第三方许可材料；缺项写入清单，不推定分发合规。"""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import shutil
import sys


# 与 pyproject.toml 的运行依赖及 Windows 目录包所用可选依赖保持一致。
RUNTIME_PACKAGES = (
    "numpy", "scipy", "soundfile", "cffi", "pycparser", "sounddevice",
    "pyserial", "Pillow", "PySide6-Essentials", "shiboken6",
)
QT_PACKAGES = {"pyside6-essentials", "shiboken6"}


def _license_label(distribution: metadata.Distribution) -> str:
    expression = distribution.metadata.get("License-Expression")
    if expression:
        return expression[:120]
    declared = distribution.metadata.get("License", "")
    if declared and "\n" not in declared and len(declared) <= 120:
        return declared
    classifiers = distribution.metadata.get_all("Classifier") or ()
    for classifier in classifiers:
        if classifier.startswith("License ::"):
            return classifier.rsplit("::", 1)[-1].strip()
    return "见随包许可文本" if declared else "未声明"


def _license_paths(distribution: metadata.Distribution, *, qt_package: bool) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for item in distribution.files or ():
        relative = str(item).replace("\\", "/")
        filename = Path(relative).name.lower()
        if not ("/licenses/" in relative.lower() or
                filename.startswith(("license", "copying", "notice"))):
            continue
        if qt_package and filename == "licenseref-qt-commercial.txt":
            continue  # 商业许可参考文件不能当成 LGPL/GPL 全文。
        source = Path(distribution.locate_file(item))
        if source.is_file():
            found.append((relative, source))
    return found


def collect_licenses(output_root: Path, packages: tuple[str, ...] = RUNTIME_PACKAGES,
                     lookup=metadata.distribution) -> list[str]:
    """写入逐包许可文件与清单，返回仍需人工核对的包名。"""
    root = output_root / "third_party_licenses"
    root.mkdir(parents=True, exist_ok=False)
    rows = []
    missing = []
    for requested in packages:
        try:
            dist = lookup(requested)
        except metadata.PackageNotFoundError:
            missing.append(requested)
            rows.append((requested, "未安装", "未知", "未收集：构建环境缺少包"))
            continue
        name = dist.metadata.get("Name", requested)
        version = dist.version
        license_id = _license_label(dist)
        qt_package = requested.casefold() in QT_PACKAGES
        sources = _license_paths(dist, qt_package=qt_package)
        if qt_package and version == "6.11.3":
            license_dir = Path(__file__).resolve().parents[1] / "docs" / "licenses"
            for license_name in ("LGPL-3.0-only", "GPL-3.0-only", "GPL-2.0-only"):
                fallback = license_dir / f"Qt-6.11.3-{license_name}.txt"
                if fallback.is_file():
                    sources.append((f"PySide6 6.11.3 source LICENSES/{license_name}.txt", fallback))
        if requested.casefold() == "pyserial" and version == "3.5" and not sources:
            fallback = Path(__file__).resolve().parents[1] / "docs" / "licenses" / "pyserial-3.5-LICENSE.txt"
            if fallback.is_file():
                sources.append(("pyserial v3.5 upstream LICENSE.txt", fallback))
        package_dir = root / f"{requested.lower()}-{version}"
        package_dir.mkdir()
        for index, (_, source) in enumerate(sources, 1):
            shutil.copyfile(source, package_dir / f"{index:02d}-{source.name}")
        if qt_package:
            missing.append(name)
            status = (f"已收集 {len(sources)} 个许可文本；Qt 对应源码/替换说明待核"
                      if len(sources) == 3 else "本版本开源许可全文不全；Qt 源码/替换说明待核")
        elif sources:
            status = f"已收集 {len(sources)} 个文件"
        else:
            missing.append(name)
            status = "未找到完整许可文件，需人工核对"
        rows.append((name, version, license_id, status))

    python_license = next((path for path in (
        Path(sys.base_prefix) / "LICENSE_PYTHON.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
    ) if path.is_file()), None)
    if python_license is None:
        missing.append("Python runtime")
        rows.append(("Python runtime", sys.version.split()[0], "PSF", "未找到运行时许可文件"))
    else:
        shutil.copyfile(python_license, root / "Python-runtime-license.txt")
        rows.append(("Python runtime", sys.version.split()[0], "PSF", "已收集运行时许可文件"))

    lines = [
        "# 第三方许可材料（构建环境快照）", "",
        "本清单由构建脚本按当前 Python 环境生成，仅列出已知运行依赖；",
        "文件收集不等于完成 Qt 或其他组件的分发义务，也不代表 RadioLab 已可对外发布。", "",
        "| 组件 | 版本 | 包元数据许可 | 文件状态 |", "|---|---|---|---|",
    ]
    lines.extend(f"| {name} | {version} | {license_id} | {status} |"
                 for name, version, license_id, status in rows)
    lines.extend((
        "", "Qt 开源许可及相应义务请核对：",
        "https://www.qt.io/development/open-source-lgpl-obligations", "",
        "Qt for Python 第三方许可证清单：",
        "https://doc.qt.io/qtforpython-6.11/licenses.html", "",
        "对外发布前还须确定 RadioLab 自身许可，并人工复核所有实际打包的原生组件。", "",
    ))
    (root / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="收集 Windows 预览包的第三方许可材料")
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    missing = collect_licenses(args.output_root)
    print(f"已生成第三方许可清单；待人工核对/补齐：{', '.join(missing) or '无自动发现缺项'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
