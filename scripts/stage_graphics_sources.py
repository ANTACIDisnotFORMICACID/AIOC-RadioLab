"""核对并暂存 Mesa 官方源码候选；不推断与 Qt DLL 的构建对应关系。"""

from __future__ import annotations

import argparse
import hashlib
import lzma
from pathlib import Path
import shutil
import tarfile


ARCHIVE = "mesa-11.2.2.tar.xz"
SHA256 = "40E148812388EC7C6D7B6657D5A16E2E8DABBA8B97DDFCEEA5197947647BDFB4"
SOURCE_URL = "https://archive.mesa3d.org/older-versions/11.x/11.2.2/mesa-11.2.2.tar.xz"
CHECKSUM_URL = "https://docs.mesa3d.org/relnotes/11.2.2.html"
REQUIRED = {"mesa-11.2.2/docs/COPYING", "mesa-11.2.2/src/gallium/drivers/llvmpipe/lp_screen.c"}
LLVM_ARCHIVE = "llvm-3.6.2.src.tar.xz"
LLVM_SHA256 = "F60DC158BFDA6822DE167E87275848969F0558B3134892FF54FCED87E4667B94"
LLVM_SIG_SHA256 = "B2E96FC46B7B13E5655B9BF9C97B812235F3C7AD61E5B10CB186B135EF8928B9"
LLVM_URL = "https://releases.llvm.org/3.6.2/llvm-3.6.2.src.tar.xz"
LLVM_SIGNER = "11E521D646982372EB577A1F8F0871F202119294"


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def _check_xz_tar(source: Path, required: set[str]) -> None:
    try:
        with lzma.open(source, "rb") as stream:
            while stream.read(1024 * 1024):
                pass
        with tarfile.open(source, "r:xz") as archive:
            names = set(archive.getnames())
    except (lzma.LZMAError, EOFError, tarfile.TarError, OSError) as exc:
        raise ValueError(f"源码归档损坏：{source}：{exc}") from exc
    if not required <= names:
        raise ValueError(f"源码归档缺少必要许可证或源文件：{source}")


def stage_mesa_source(cache: Path, output_root: Path, *, expected_hash: str = SHA256) -> Path:
    """只有哈希、XZ 流、归档结构和包内版本线索通过后才复制。"""
    source = cache / ARCHIVE
    if not source.is_file():
        raise ValueError(f"缺少 Mesa 源码归档：{source}；下载：{SOURCE_URL}")
    digest = _digest(source)
    if digest != expected_hash.upper():
        raise ValueError(f"Mesa 源码哈希不匹配：{source}；预期 {expected_hash}，实际 {digest}")
    _check_xz_tar(source, REQUIRED)

    library = output_root / "radiolab-gui" / "_internal" / "PySide6" / "opengl32sw.dll"
    if not library.is_file() or b"Mesa 11.2.2" not in library.read_bytes():
        raise ValueError(f"包内缺少 Mesa 11.2.2 版本线索：{library}；不能附上此候选归档")

    target = output_root / "graphics_source_candidates"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source, target / ARCHIVE)
    (target / "README.md").write_text(
        "# Mesa 11.2.2 源码候选\n\n"
        f"文件：`{ARCHIVE}`\n\nSHA-256：`{digest}`\n\n"
        f"官方归档：{SOURCE_URL}\n\n官方发布说明及校验值：{CHECKSUM_URL}\n\n"
        "包内 opengl32sw.dll 含 Mesa 11.2.2 版本字符串；这只是一条版本线索。"
        "尚未证明此归档是该 DLL 的完整对应源码，也未核对 Qt 补丁、构建参数、LLVM 依赖或分发条件。\n",
        encoding="utf-8",
    )
    return target


def stage_llvm_source(cache: Path, output_root: Path, *,
                      expected_hash: str = LLVM_SHA256,
                      expected_sig_hash: str = LLVM_SIG_SHA256) -> Path:
    """按已核对的官方归档与分离签名哈希暂存 LLVM 3.6.2 候选。"""
    source = cache / LLVM_ARCHIVE
    signature = cache / (LLVM_ARCHIVE + ".sig")
    for path, url in ((source, LLVM_URL), (signature, LLVM_URL + ".sig")):
        if not path.is_file():
            raise ValueError(f"缺少 LLVM 文件：{path}；下载：{url}")
    digest, sig_digest = _digest(source), _digest(signature)
    if digest != expected_hash.upper() or sig_digest != expected_sig_hash.upper():
        raise ValueError(f"LLVM 归档或签名哈希不匹配：{source} / {signature}")
    _check_xz_tar(source, {"llvm-3.6.2.src/LICENSE.TXT", "llvm-3.6.2.src/lib/Target/TargetMachine.cpp"})
    library = output_root / "radiolab-gui" / "_internal" / "PySide6" / "opengl32sw.dll"
    if not library.is_file() or b"LLVM 3.6.2" not in library.read_bytes():
        raise ValueError(f"包内缺少 LLVM 3.6.2 版本线索：{library}；不能附上此候选归档")

    target = output_root / "llvm_source_candidates"
    target.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source, target / source.name)
    shutil.copyfile(signature, target / signature.name)
    (target / "README.md").write_text(
        "# LLVM 3.6.2 源码候选\n\n"
        f"官方归档：{LLVM_URL}\n\n归档 SHA-256：`{digest}`\n\n"
        f"分离签名 SHA-256：`{sig_digest}`\n\n历史签名公钥指纹：`{LLVM_SIGNER}`\n\n"
        "签名日期 2015-07-11，公钥 2019-04-18 到期；原始归档的密码学校验通过，"
        "改动一字节后失败。当前公钥过期，未独立核实撤销状态。"
        "公钥指纹可在 LLVM 开发邮件列表的签名密钥讨论中核对。\n\n"
        "包内 opengl32sw.dll 含 LLVM 3.6.2 字符串，但 Qt 补丁、构建参数和完整对应源码未确认；"
        "不能据此宣称 DLL 由该归档直接构建，也不能据此通过对外分发许可。\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="将已核验的图形源码候选附到 Windows 目录包")
    parser.add_argument("cache", type=Path, help="存有官方 tar.xz 的缓存目录")
    parser.add_argument("output_root", type=Path, help="已生成的 Windows 目录包根目录")
    parser.add_argument("--source", choices=("mesa", "llvm"), default="mesa")
    args = parser.parse_args()
    stage = stage_mesa_source if args.source == "mesa" else stage_llvm_source
    print(f"{args.source} 源码候选：{stage(args.cache, args.output_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
