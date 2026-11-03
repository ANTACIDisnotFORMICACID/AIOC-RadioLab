"""最近文件只读清单；复制路径和定位文件夹均由用户手动触发。"""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from radiolab.storage import RecentFile, SQLiteHistoryService


class RecentFilesDialog(QDialog):
    def __init__(self, store: SQLiteHistoryService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("RadioLab · 最近文件")
        self.resize(900, 500)
        self.entries: list[RecentFile] = store.list_recent_files(limit=200)
        layout = QVBoxLayout(self)
        self.status = QLabel("最近 200 个历史文件引用；状态按当前磁盘检查")
        layout.addWidget(self.status)
        self.table = QTableWidget(len(self.entries), 4)
        self.table.setHorizontalHeaderLabels(("最近时间", "类型", "文件", "状态"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(3, 90)
        self.table.horizontalHeader().setSectionResizeMode(2, self.table.horizontalHeader().ResizeMode.Stretch)
        for row, entry in enumerate(self.entries):
            values = (entry.time[:19], entry.kind, entry.path.name,
                      "存在" if entry.exists else "缺失")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(entry.path))
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("复制完整路径")
        self.folder_button = QPushButton("打开所在文件夹")
        self.close_button = QPushButton("关闭")
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.folder_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(self.update_selection)
        self.copy_button.clicked.connect(self.copy_path)
        self.folder_button.clicked.connect(self.open_folder)
        self.close_button.clicked.connect(self.reject)
        self.update_selection()

    def current_file(self) -> RecentFile | None:
        row = self.table.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def update_selection(self) -> None:
        entry = self.current_file()
        self.copy_button.setEnabled(entry is not None)
        self.folder_button.setEnabled(entry is not None and entry.path.parent.is_dir())

    def copy_path(self) -> None:
        entry = self.current_file()
        if entry is None:
            self.status.setText("先选择一个文件")
            return
        QApplication.clipboard().setText(str(entry.path))
        self.status.setText(f"已复制完整路径：{entry.path}")

    def open_folder(self) -> None:
        entry = self.current_file()
        if entry is None:
            self.status.setText("先选择一个文件")
            return
        folder = Path(entry.path).parent
        if not folder.is_dir():
            self.status.setText(f"所在文件夹已不存在：{folder}")
            self.update_selection()
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self.status.setText(f"已请求打开文件夹：{folder}")
        else:
            self.status.setText(f"系统未能打开文件夹：{folder}；可复制完整路径手动查找")
