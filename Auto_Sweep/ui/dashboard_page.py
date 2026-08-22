# -*- coding: utf-8 -*-
"""
Dashboard page — instrument overview + temperature sweep control.

Deep Space Cyan theme.  Features:
  - Three clickable DeviceCards with status lights
  - Large Connect / Disconnect buttons per device
  - Connect All button
  - Temperature sweep control: Fixed Points or Range Sweep (4–100 K)
  - Log panel
"""

import time
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import DeviceCard


# ---------------------------------------------------------------------------
# Connect/Disconnect 按钮样式常量
# ---------------------------------------------------------------------------

CONNECT_STYLE_GREEN = """
    QPushButton {
        background-color: #4ADE80; color: #0D1117;
        font-weight: bold; border: 1px solid #4ADE80; border-radius: 6px;
        min-width: 100px; min-height: 34px;
    }
    QPushButton:hover { background-color: #6EE7A1; }
    QPushButton:pressed { background-color: #22C55E; }
"""

CONNECT_STYLE_GRAY = """
    QPushButton {
        background-color: #30363D; color: #8B949E;
        font-weight: bold; border: 1px solid #30363D; border-radius: 6px;
        min-width: 100px; min-height: 34px;
    }
    QPushButton:hover { background-color: #484F58; }
"""

DISCONNECT_STYLE_RED = """
    QPushButton {
        background-color: #EF4444; color: white;
        font-weight: bold; border: 1px solid #EF4444; border-radius: 6px;
        min-width: 100px; min-height: 34px;
    }
    QPushButton:hover { background-color: #F87171; }
    QPushButton:pressed { background-color: #DC2626; }
"""

DISCONNECT_STYLE_GRAY = """
    QPushButton {
        background-color: #30363D; color: #8B949E;
        font-weight: bold; border: 1px solid #30363D; border-radius: 6px;
        min-width: 100px; min-height: 34px;
    }
    QPushButton:hover { background-color: #484F58; }
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

LASER_ADDRS = [
    "TCPIP0::K-N7779C-00108::inst0::INSTR",
    "TCPIP0::169.254.77.29::INSTR",
]

LAKESHORE_ADDRS = [
    "ASRL4::INSTR",
    "ASRL3::INSTR",
    "ASRL5::INSTR",
]

VNA_ADDRS = [
    "TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR",
    "TCPIP0::localhost::hislip_PXI10_CHASSIS2_SLOT1_INDEX0::INSTR",
]


def _ts() -> str:
    return time.strftime("[%H:%M:%S] ", time.localtime(time.time()))


# ---------------------------------------------------------------------------
# TemperatureSweepWidget
# ---------------------------------------------------------------------------

class TemperatureSweepWidget(QWidget):
    """Two-mode temperature sweep control (4–100 K).

    Fixed Points mode  — comma-separated list in a QLineEdit.
    Range Sweep mode   — start / stop / step QSpinBoxes + preview label.
    """

    settings_changed = pyqtSignal()  # emit when user edits anything

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # ---- mode radio buttons ----
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._fixed_radio = QRadioButton("Fixed Points")
        self._range_radio = QRadioButton("Range Sweep")
        self._fixed_radio.setChecked(True)
        self._fixed_radio.toggled.connect(self._on_mode_changed)
        self._range_radio.toggled.connect(self._on_mode_changed)
        mode_row.addWidget(self._fixed_radio)
        mode_row.addWidget(self._range_radio)
        mode_row.addStretch()
        root.addLayout(mode_row)

        # ---- fixed-mode input ----
        self._fixed_widget = QWidget()
        fixed_layout = QHBoxLayout(self._fixed_widget)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        fixed_layout.addWidget(QLabel("Temps (K):"))
        self._fixed_input = QLineEdit()
        self._fixed_input.setText("26, 30, 40, 50, 60, 70, 80, 90")
        self._fixed_input.setPlaceholderText("e.g. 30, 50, 77 (4–100 K, comma-separated)")
        self._fixed_input.textChanged.connect(self.settings_changed.emit)
        fixed_layout.addWidget(self._fixed_input, 1)
        root.addWidget(self._fixed_widget)

        # ---- range-mode input ----
        self._range_widget = QWidget()
        range_layout = QHBoxLayout(self._range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(QLabel("Start:"))
        self._start_spin = QSpinBox()
        self._start_spin.setRange(4, 100)
        self._start_spin.setValue(26)
        self._start_spin.setSuffix(" K")
        self._start_spin.valueChanged.connect(self._on_range_changed)
        range_layout.addWidget(self._start_spin)
        range_layout.addWidget(QLabel("Stop:"))
        self._stop_spin = QSpinBox()
        self._stop_spin.setRange(4, 100)
        self._stop_spin.setValue(100)
        self._stop_spin.setSuffix(" K")
        self._stop_spin.valueChanged.connect(self._on_range_changed)
        range_layout.addWidget(self._stop_spin)
        range_layout.addWidget(QLabel("Step:"))
        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, 50)
        self._step_spin.setValue(2)
        self._step_spin.setSuffix(" K")
        self._step_spin.valueChanged.connect(self._on_range_changed)
        range_layout.addWidget(self._step_spin)
        range_layout.addStretch()
        root.addWidget(self._range_widget)

        # ---- preview ----
        self._preview_label = QLabel()
        self._preview_label.setStyleSheet(
            "color: #8B949E; font-size: 11px; font-family: 'JetBrains Mono', 'Consolas', monospace;")
        root.addWidget(self._preview_label)

        # ---- timing settings ----
        import config
        timing_box = QGroupBox("Timing Settings")
        timing_layout = QVBoxLayout(timing_box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Pre-measurement wait:"))
        self._pre_wait_spin = QSpinBox()
        self._pre_wait_spin.setRange(0, config.pre_measurement_wait_max_minutes)
        self._pre_wait_spin.setValue(config.pre_measurement_wait_minutes)
        self._pre_wait_spin.setSuffix(" min")
        self._pre_wait_spin.setToolTip(
            "温度稳定判定通过后、开始扫激光功率前的等待时间（0=关闭）")
        self._pre_wait_spin.valueChanged.connect(self.settings_changed.emit)
        row1.addWidget(self._pre_wait_spin)
        row1.addStretch()
        timing_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Max stability wait:"))
        self._max_wait_spin = QSpinBox()
        self._max_wait_spin.setRange(5, config.max_wait_max_minutes)
        self._max_wait_spin.setValue(config.max_wait_seconds // 60)
        self._max_wait_spin.setSuffix(" min")
        self._max_wait_spin.setToolTip(
            "单温度点最长等待时间（超过后强制继续或超时）")
        self._max_wait_spin.valueChanged.connect(self.settings_changed.emit)
        row2.addWidget(self._max_wait_spin)
        row2.addStretch()
        timing_layout.addLayout(row2)

        root.addWidget(timing_box)

        # initial state
        self._on_mode_changed()
        self._update_preview()

    # ---- mode switching ----

    def _on_mode_changed(self):
        fixed = self._fixed_radio.isChecked()
        self._fixed_widget.setVisible(fixed)
        self._range_widget.setVisible(not fixed)
        self._update_preview()
        self.settings_changed.emit()

    def _on_range_changed(self):
        if self._range_radio.isChecked():
            self._update_preview()
        self.settings_changed.emit()

    def _update_preview(self):
        temps = self.get_temperatures()
        if not temps:
            self._preview_label.setText("Preview: — (no valid temperatures)")
            return
        n = len(temps)
        if n <= 8:
            text = ", ".join(str(t) for t in temps)
        else:
            text = (", ".join(str(t) for t in temps[:4])
                    + " ... "
                    + ", ".join(str(t) for t in temps[-4:]))
        self._preview_label.setText(f"Preview: {text}  ({n} points)")

    # ---- public API ----

    def get_mode(self) -> str:
        return "fixed" if self._fixed_radio.isChecked() else "range"

    def set_mode(self, mode: str):
        if mode == "range":
            self._range_radio.setChecked(True)
        else:
            self._fixed_radio.setChecked(True)

    def get_temperatures(self) -> list:
        """Return the resolved temperature list regardless of current mode."""
        if self._fixed_radio.isChecked():
            raw = self._fixed_input.text()
            temps = []
            for part in raw.replace(";", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    t = float(part)
                    if 4 <= t <= 100:
                        temps.append(t)
                except ValueError:
                    pass
            return sorted(set(temps))
        else:
            start = self._start_spin.value()
            stop = self._stop_spin.value()
            step = self._step_spin.value()
            if step <= 0 or start > stop:
                return []
            result = []
            t = start
            while t <= stop + 0.001:
                result.append(float(t))
                t += step
            return result

    def get_settings(self) -> dict:
        timing = self.get_timing_settings()
        return {
            "mode": self.get_mode(),
            "fixed_temps": self.get_temperatures() if self._fixed_radio.isChecked() else [],
            "range_start": self._start_spin.value(),
            "range_stop": self._stop_spin.value(),
            "range_step": self._step_spin.value(),
            "pre_wait_min": timing["pre_measurement_wait_min"],
            "max_wait_min": timing["max_wait_min"],
        }

    def get_timing_settings(self) -> dict:
        """Return current timing settings as (pre_wait_min, max_wait_min)."""
        return {
            "pre_measurement_wait_min": self._pre_wait_spin.value(),
            "max_wait_min": self._max_wait_spin.value(),
        }

    def set_settings(self, s: dict):
        if s.get("mode") == "range":
            self._range_radio.setChecked(True)
        else:
            self._fixed_radio.setChecked(True)
        if "range_start" in s:
            self._start_spin.setValue(int(s["range_start"]))
        if "range_stop" in s:
            self._stop_spin.setValue(int(s["range_stop"]))
        if "range_step" in s:
            self._step_spin.setValue(int(s["range_step"]))
        if "fixed_temps" in s and s["fixed_temps"]:
            self._fixed_input.setText(
                ", ".join(str(int(t)) if t == int(t) else str(t)
                           for t in s["fixed_temps"]))
        if "pre_wait_min" in s:
            self._pre_wait_spin.setValue(int(s["pre_wait_min"]))
        if "max_wait_min" in s:
            self._max_wait_spin.setValue(int(s["max_wait_min"]))
        self._update_preview()


# ---------------------------------------------------------------------------
# DashboardPage
# ---------------------------------------------------------------------------

class DashboardPage(QWidget):
    """Overview page — device cards, connection management, temperature sweep."""

    navigate_to = pyqtSignal(str)
    connect_all_requested = pyqtSignal()
    connect_device = pyqtSignal(str, str)
    disconnect_device = pyqtSignal(str)

    temp_sweep_changed = pyqtSignal()  # emitted when sweep settings change

    experiment_start_requested = pyqtSignal()
    experiment_abort_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cards: dict[str, DeviceCard] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(16)
        root.setContentsMargins(28, 20, 28, 20)

        # ---- title ----
        title = QLabel("YBCO Auto Sweep Control")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 22px; font-weight: bold; color: #E6EDF3;"
            "padding: 10px; background: transparent;"
        )
        root.addWidget(title)

        # ---- device cards row ----
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(24)

        devices = [
            ("laser",     "\U0001f4a1", "Laser",       LASER_ADDRS),
            ("lakeshore", "\U0001f321️", "LakeShore",   LAKESHORE_ADDRS),
            ("vna",       "\U0001f4e1", "VNA",         VNA_ADDRS),
        ]

        for key, icon, name, addrs in devices:
            col = QVBoxLayout()
            col.setSpacing(8)

            card = DeviceCard(icon, name)
            card.clicked.connect(lambda k=key: self.navigate_to.emit(k))
            self._cards[key] = card

            # address picker
            addr_combo = self._make_address_picker(key, addrs)

            # enlarged connect / disconnect buttons
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)
            c_btn = QPushButton("Connect")
            d_btn = QPushButton("Disconnect")
            c_btn.setMinimumWidth(100)
            c_btn.setMinimumHeight(34)
            d_btn.setMinimumWidth(100)
            d_btn.setMinimumHeight(34)
            c_btn.clicked.connect(lambda checked, k=key: self._on_connect_one(k))
            d_btn.clicked.connect(lambda checked, k=key: self.disconnect_device.emit(k))
            btn_row.addStretch()
            btn_row.addWidget(c_btn)
            btn_row.addWidget(d_btn)
            btn_row.addStretch()

            # 存储按钮引用，用于后续状态样式更新
            setattr(self, f"_{key}_connect_btn", c_btn)
            setattr(self, f"_{key}_disconnect_btn", d_btn)

            col.addWidget(card)
            col.addWidget(addr_combo)
            col.addLayout(btn_row)
            cards_layout.addLayout(col)

        root.addLayout(cards_layout)

        # ---- Connect All ----
        all_row = QHBoxLayout()
        self.connect_all_btn = QPushButton("\U0001f50c  Connect All")
        self.connect_all_btn.setMinimumHeight(44)
        self.connect_all_btn.setMinimumWidth(180)
        self.connect_all_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 15px; font-weight: bold; padding: 10px 28px;"
            "  border-color: #22D3EE; color: #22D3EE;"
            "}"
            "QPushButton:hover {"
            "  background-color: #1A2E3D; border-color: #67E8F9;"
            "}")
        self.connect_all_btn.clicked.connect(self.connect_all_requested.emit)

        self._count_label = QLabel("0/3 connected")
        self._count_label.setStyleSheet("color: #8B949E; font-size: 13px;")

        all_row.addStretch()
        all_row.addWidget(self.connect_all_btn)
        all_row.addSpacing(16)
        all_row.addWidget(self._count_label)
        all_row.addStretch()
        root.addLayout(all_row)

        # ---- temperature sweep ----
        temp_box = QGroupBox("Temperature Sweep  (experiment targets)")
        temp_layout = QVBoxLayout(temp_box)
        self._temp_sweep = TemperatureSweepWidget()
        self._temp_sweep.settings_changed.connect(self.temp_sweep_changed.emit)
        temp_layout.addWidget(self._temp_sweep)
        root.addWidget(temp_box)

        # ---- experiment control ----
        exp_box = QGroupBox("Experiment Control")
        exp_layout = QHBoxLayout(exp_box)
        self._start_exp_btn = QPushButton("▶  Start Measurement")
        self._start_exp_btn.setMinimumHeight(46)
        self._start_exp_btn.setMinimumWidth(200)
        self._start_exp_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 15px; font-weight: bold; padding: 12px 32px;"
            "  border-color: #4ADE80; color: #4ADE80;"
            "}"
            "QPushButton:hover {"
            "  background-color: #1A3D2A; border-color: #6EE7A1;"
            "}")
        self._start_exp_btn.clicked.connect(
            self.experiment_start_requested.emit)

        self._abort_exp_btn = QPushButton("■  Abort")
        self._abort_exp_btn.setMinimumHeight(46)
        self._abort_exp_btn.setMinimumWidth(140)
        self._abort_exp_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 15px; font-weight: bold; padding: 12px 24px;"
            "  border-color: #EF4444; color: #EF4444;"
            "}"
            "QPushButton:hover {"
            "  background-color: #3D1A1A; border-color: #F87171;"
            "}")
        self._abort_exp_btn.clicked.connect(
            self.experiment_abort_requested.emit)
        self._abort_exp_btn.setEnabled(False)

        exp_layout.addStretch()
        exp_layout.addWidget(self._start_exp_btn)
        exp_layout.addSpacing(12)
        exp_layout.addWidget(self._abort_exp_btn)
        exp_layout.addStretch()
        root.addWidget(exp_box)

        # ---- log ----
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(130)
        # 限制最大行数，防止 QTextDocument 无限累积导致 OOM
        import config
        self.log_text.document().setMaximumBlockCount(
            getattr(config, "log_max_blocks", 5000))
        log_layout.addWidget(self.log_text)
        root.addWidget(log_box)

    def _make_address_picker(self, key: str, addresses: list) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(addresses)
        combo.setCurrentIndex(0)
        combo.setMinimumWidth(180)
        setattr(self, f"_{key}_addr", combo)
        return combo

    def _on_connect_one(self, key: str):
        combo = getattr(self, f"_{key}_addr")
        addr = combo.currentText().strip()
        if addr:
            self._cards[key].set_connecting()
            self.connect_device.emit(key, addr)

    # ------------------------------------------------------------------
    # public update API
    # ------------------------------------------------------------------

    def set_device_connected(self, key: str, model: str):
        """已连接：绿灯，Connect 按钮灰色禁用，Disconnect 按钮红色启用。"""
        if key in self._cards:
            self._cards[key].set_connected(True)
            self._cards[key].set_model(model)

        c_btn = getattr(self, f"_{key}_connect_btn", None)
        d_btn = getattr(self, f"_{key}_disconnect_btn", None)
        if c_btn:
            c_btn.setStyleSheet(CONNECT_STYLE_GRAY)
            c_btn.setEnabled(False)
        if d_btn:
            d_btn.setStyleSheet(DISCONNECT_STYLE_RED)
            d_btn.setEnabled(True)

    def set_device_disconnected(self, key: str):
        """已断开：红灯，Connect 按钮绿色启用，Disconnect 按钮灰色禁用。"""
        if key in self._cards:
            self._cards[key].set_connected(False)
            self._cards[key].set_model("—")

        c_btn = getattr(self, f"_{key}_connect_btn", None)
        d_btn = getattr(self, f"_{key}_disconnect_btn", None)
        if c_btn:
            c_btn.setStyleSheet(CONNECT_STYLE_GREEN)
            c_btn.setEnabled(True)
        if d_btn:
            d_btn.setStyleSheet(DISCONNECT_STYLE_GRAY)
            d_btn.setEnabled(False)

    def set_device_error(self, key: str):
        """错误状态：红灯，Connect 按钮绿色启用（用户可手动重连），Disconnect 灰色。"""
        if key in self._cards:
            self._cards[key].set_connected(False)

        c_btn = getattr(self, f"_{key}_connect_btn", None)
        d_btn = getattr(self, f"_{key}_disconnect_btn", None)
        if c_btn:
            c_btn.setStyleSheet(CONNECT_STYLE_GREEN)
            c_btn.setEnabled(True)
        if d_btn:
            d_btn.setStyleSheet(DISCONNECT_STYLE_GRAY)
            d_btn.setEnabled(False)

    def set_device_connecting(self, key: str):
        """连接中：黄灯，两个按钮都禁用。"""
        if key in self._cards:
            self._cards[key].set_connecting()

        c_btn = getattr(self, f"_{key}_connect_btn", None)
        d_btn = getattr(self, f"_{key}_disconnect_btn", None)
        if c_btn:
            c_btn.setEnabled(False)
        if d_btn:
            d_btn.setEnabled(False)

    def set_connect_count(self, n: int):
        self._count_label.setText(f"{n}/3 connected")

    def log(self, message: str):
        self.log_text.append(_ts() + message)

    def get_address(self, key: str) -> str:
        combo = getattr(self, f"_{key}_addr", None)
        return combo.currentText().strip() if combo else ""

    def set_experiment_running(self, running: bool):
        self._start_exp_btn.setEnabled(not running)
        self._abort_exp_btn.setEnabled(running)

    def get_temp_sweep(self) -> TemperatureSweepWidget:
        return self._temp_sweep
