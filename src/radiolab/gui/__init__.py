"""V0.3 图形界面入口；命令行核心不依赖 PySide6。"""
import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radiolab-gui", description="RadioLab V0.3 桌面终端预览")
    parser.add_argument("--database", default="runtime/history.sqlite3", help="SQLite 历史路径")
    parser.add_argument("--source", default="runtime/history.jsonl", help="要手动导入的 JSONL 路径")
    parser.add_argument("--config", help="经无辐射台架确认的 TOML 配置；仅显式点击真实发射时读取")
    args = parser.parse_args(argv)
    try:
        from .app import run
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            parser.exit(2, "缺少 PySide6；运行 python -m pip install -e '.[gui]'\n")
        raise
    return run(args.database, args.source, args.config)
