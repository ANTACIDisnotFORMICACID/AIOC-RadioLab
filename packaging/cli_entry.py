"""PyInstaller 命令行入口。构建和 --help 均不访问无线电设备。"""

from radiolab.cli import main



if __name__ == "__main__":
    raise SystemExit(main())