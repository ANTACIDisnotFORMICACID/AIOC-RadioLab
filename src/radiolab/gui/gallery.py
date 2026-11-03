"""从 SQLite 历史构建只读图片库；选择图片只回填路径。"""
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from radiolab.storage import SQLiteHistoryService


class ImageGalleryDialog(QDialog):
    def __init__(self, store: SQLiteHistoryService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("RadioLab · 图片库")
        self.resize(780, 540)
        self.selected_path: Path | None = None
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.images = QListWidget()
        self.images.setViewMode(QListWidget.ViewMode.IconMode)
        self.images.setIconSize(QSize(144, 108))
        self.images.setGridSize(QSize(180, 150))
        self.images.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.images.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.images, 1)
        actions = QHBoxLayout()
        self.use_button = QPushButton("用于图片发送栏")
        self.use_button.setEnabled(False)
        self.copy_button = QPushButton("复制图片路径")
        self.copy_button.setEnabled(False)
        self.close_button = QPushButton("关闭")
        actions.addWidget(self.use_button)
        actions.addWidget(self.copy_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.images.itemSelectionChanged.connect(self.update_selection)
        self.use_button.clicked.connect(self.use_selected)
        self.copy_button.clicked.connect(self.copy_selected_path)
        self.close_button.clicked.connect(self.reject)
        self.load_entries(store)

    def load_entries(self, store: SQLiteHistoryService) -> None:
        seen: set[Path] = set()
        usable = 0
        missing = 0
        for entry in store.list_image_entries(limit=500):
            path = Path(entry.image_path)
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            image = None
            reason = "文件已缺失"
            if path.is_file():
                try:
                    if path.stat().st_size > 20_000_000:
                        reason = "文件超过 20 MB"
                    else:
                        reader = QImageReader(str(path))
                        reader.setAutoTransform(True)
                        size = reader.size()
                        if not size.isValid() or size.width() * size.height() > 20_000_000:
                            reason = "图片尺寸无效或超过 2000 万像素"
                        else:
                            reader.setScaledSize(size.scaled(QSize(144, 108),
                                                             Qt.AspectRatioMode.KeepAspectRatio))
                            image = reader.read()
                            if image.isNull():
                                reason = f"无法读取图片：{reader.errorString()}"
                                image = None
                except OSError as exc:
                    reason = f"无法检查图片：{exc}"
            label = f"{path.name}\n{entry.operation} · {entry.local_time or entry.time[:19]}"
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)) if image is not None else QIcon(), label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            if image is None:
                missing += 1
                item.setText(label + " [不可用]")
                item.setToolTip(f"{path}\n{reason}")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                usable += 1
                item.setToolTip(f"{path}\n点击“用于图片发送栏”只回填路径，不发射")
            self.images.addItem(item)
        self.summary.setText(f"图片库：可用 {usable} 张，不可用 {missing} 张；图片来自历史，选择后不会发射")

    def current_path(self) -> Path | None:
        item = self.images.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return None
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        return path if path.is_file() else None

    def update_selection(self) -> None:
        enabled = self.current_path() is not None
        self.use_button.setEnabled(enabled)
        self.copy_button.setEnabled(enabled)

    def use_selected(self) -> None:
        path = self.current_path()
        if path is None:
            self.summary.setText("图片已不可用；刷新历史后重试")
            self.update_selection()
            return
        self.selected_path = path
        self.accept()

    def copy_selected_path(self) -> None:
        path = self.current_path()
        if path is None:
            self.summary.setText("先选择一张可用图片")
            return
        QApplication.clipboard().setText(str(path))
        self.summary.setText(f"已复制图片路径：{path}")
