"""PyInstaller 桌面入口。只有用户点击操作时才使用音频或 PTT。"""

from radiolab.gui import main


if __name__ == "__main__":
    raise SystemExit(main())
