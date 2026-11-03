"""设备配置编辑器；只保存 TOML，不访问声卡或串口。"""
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from radiolab.audio import AudioDevice
from radiolab.config import Config, load_config, save_config, validate_config_values


class ConfigEditorDialog(QDialog):
    def __init__(self, path: str | Path, tx: AudioDevice | None,
                 rx: AudioDevice | None, port: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设备与发射配置")
        self.setMinimumWidth(560)
        self.saved_path: Path | None = None
        initial = Path(path).expanduser()
        existing = load_config(initial) if initial.is_file() else Config()
        validate_config_values(existing)
        self.tx_device = tx.name if tx else existing.tx_device
        self.rx_device = rx.name if rx else existing.rx_device
        self.ptt_port = port if port else existing.ptt_port

        root = QVBoxLayout(self)
        root.addWidget(QLabel("保存配置不打开设备。TX/RX/PTT 以主界面当前选择为准；未选择时保留原值。"))
        form = QFormLayout()
        path_row = QHBoxLayout()
        self.path_input = QLineEdit(str(initial))
        browse = QPushButton("选择路径")
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(browse)
        form.addRow("TOML 路径", path_row)
        form.addRow("TX 输出", QLabel(str(self.tx_device) if self.tx_device is not None else "未选择"))
        form.addRow("RX 输入", QLabel(str(self.rx_device) if self.rx_device is not None else "未选择"))
        form.addRow("PTT 串口", QLabel(self.ptt_port or "未选择"))

        self.firmware = QLineEdit(existing.firmware or "")
        self.firmware.setPlaceholderText("仅台架确认固件 1.2+ 后填写 1.2+")
        form.addRow("AIOC 固件", self.firmware)
        self.bench = QCheckBox("已在无辐射台架验证打开瞬态、DTR/RTS 与释放")
        self.bench.setChecked(existing.ptt_bench_verified)
        form.addRow("PTT 台架", self.bench)
        self.legal = QCheckBox("已核对本人操作权限、合法频率、手台和接线")
        self.legal.setChecked(existing.legal_operation_confirmed)
        form.addRow("操作前提", self.legal)
        self.gain = self._spin(0.001, 0.25, existing.gain, 0.01)
        form.addRow("TX 增益", self.gain)
        self.pre_delay = self._spin(0, 10, existing.pre_delay_s, 0.1)
        self.pre_delay.setSuffix(" s")
        form.addRow("PTT 预延时", self.pre_delay)
        self.tail_delay = self._spin(0, 10, existing.tail_delay_s, 0.1)
        self.tail_delay.setSuffix(" s")
        form.addRow("PTT 尾延时", self.tail_delay)
        self.max_seconds = self._spin(0.1, 60, existing.max_tx_seconds, 1)
        self.max_seconds.setSuffix(" s")
        form.addRow("连续发射上限", self.max_seconds)
        self.frequency = QLineEdit(str(existing.frequency_hz) if existing.frequency_hz else "")
        self.frequency.setPlaceholderText("可选，仅记录 Hz；不会设置手台")
        form.addRow("手台频率", self.frequency)
        self.power = QLineEdit(existing.power_setting or "")
        self.power.setPlaceholderText("可选，仅记录手台功率档位")
        form.addRow("功率档位", self.power)
        root.addLayout(form)
        root.addWidget(QLabel("保存的确认仍需在每次真实发射前重新核对并逐次确认；未做台架验证请保持未勾选。"))
        buttons = QHBoxLayout()
        save = QPushButton("保存配置")
        cancel = QPushButton("取消")
        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(cancel)
        root.addLayout(buttons)
        browse.clicked.connect(self.choose_path)
        save.clicked.connect(self.save)
        cancel.clicked.connect(self.reject)

    @staticmethod
    def _spin(low: float, high: float, value: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setValue(value)
        return spin

    def choose_path(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(self, "保存 RadioLab 配置", self.path_input.text(),
                                                  "TOML 配置 (*.toml)")
        if selected:
            self.path_input.setText(selected)

    def save(self) -> None:
        target = Path(self.path_input.text().strip()).expanduser()
        if target.exists():
            answer = QMessageBox.question(self, "覆盖配置", f"要覆盖 {target} 吗？旧文件会另存为 .bak。",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            frequency = int(self.frequency.text().strip()) if self.frequency.text().strip() else None
            config = Config(self.tx_device, self.rx_device, self.ptt_port,
                            self.firmware.text().strip() or None, self.bench.isChecked(),
                            self.legal.isChecked(), self.gain.value(), self.pre_delay.value(),
                            self.tail_delay.value(), self.max_seconds.value(), frequency,
                            self.power.text().strip() or None)
            self.saved_path = save_config(target, config, overwrite=target.exists())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "配置未保存", str(exc))
            return
        self.accept()
