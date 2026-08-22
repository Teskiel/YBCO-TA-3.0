# -*- coding: utf-8 -*-
"""
LakeShore 335 control detail page.

Optimised over Lakeshore335_output.py:
  - Large-format temperature readouts
  - Heater range as radio buttons (more intuitive than dropdown)
  - Loop 1 & Loop 2 controls side-by-side
  - Setpoint + PID combined into one Loop Control group
  - Integrated PresetBar
  - Prominent red Emergency button
  - StatusLight colour coding
"""

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import StatusLight


# ---------------------------------------------------------------------------
# Connect/Disconnect 按钮样式
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

RANGE_LABELS = ["Off", "Low", "Medium", "High"]
RANGE_COLORS = ["#95a5a6", "#f39c12", "#e67e22", "#e74c3c"]


def _fmt_temp(value, unit="K", decimals=4):
    if value is None:
        return f"-- {unit}"
    return f"{value:.{decimals}f} {unit}"


def _fmt_pid(p, i, d):
    """Format PID values compactly: '100/5/0' or '--' if None."""
    if p is None:
        return "--"
    return f"{p:g}/{i:g}/{d:g}"


# ---------------------------------------------------------------------------
# LakeShorePage
# ---------------------------------------------------------------------------

class LakeShorePage(QWidget):
    """LakeShore 335 detail control page."""

    back_clicked = pyqtSignal()

    # outgoing requests
    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()
    setpoint_requested = pyqtSignal(int, float)       # loop, kelvin
    pid_requested = pyqtSignal(int, float, float, float)  # loop, P, I, D
    heater_range_requested = pyqtSignal(int, int)     # output, range_level
    all_heaters_off_requested = pyqtSignal()
    read_pid_requested = pyqtSignal(int)              # loop

    # preset
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._connected = False
        self._model = "—"
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 16)

        # back row
        back_row = QHBoxLayout()
        back_btn = QPushButton("← Back to Dashboard")
        back_btn.clicked.connect(self.back_clicked.emit)
        back_row.addWidget(back_btn)
        back_row.addStretch()
        title = QLabel("LakeShore 335 Control")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        back_row.addWidget(title)
        back_row.addStretch()
        root.addLayout(back_row)

        # ---- connection ----
        conn_box = QGroupBox("Connection")
        conn_layout = QFormLayout(conn_box)
        self._addr_label = QLabel("ASRL4::INSTR")
        self._status_light = StatusLight(14)
        self._model_label = QLabel("—")
        sr = QHBoxLayout()
        sr.addWidget(self._status_light)
        sr.addWidget(self._model_label)
        sr.addStretch()
        conn_layout.addRow("Address:", self._addr_label)
        conn_layout.addRow("Status:", sr)

        # Connect/Disconnect 按钮行
        btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("Connect")
        self._disconnect_btn = QPushButton("Disconnect")
        self._connect_btn.setStyleSheet(CONNECT_STYLE_GREEN)
        self._disconnect_btn.setStyleSheet(DISCONNECT_STYLE_GRAY)
        self._disconnect_btn.setEnabled(False)
        self._connect_btn.clicked.connect(
            lambda: self.connect_requested.emit(self._addr_label.text()))
        self._disconnect_btn.clicked.connect(self.disconnect_requested.emit)
        btn_row.addWidget(self._connect_btn)
        btn_row.addWidget(self._disconnect_btn)
        btn_row.addStretch()
        conn_layout.addRow("", btn_row)

        root.addWidget(conn_box)

        # ---- live readings (two large panels side-by-side) ----
        read_box = QGroupBox("Live Readings  (auto-refresh 1 s)")
        read_layout = QHBoxLayout(read_box)
        read_layout.setSpacing(20)

        self._panel_a = self._make_readout_panel("Input A")
        self._panel_b = self._make_readout_panel("Input B")
        read_layout.addWidget(self._panel_a)
        read_layout.addWidget(self._panel_b)
        root.addWidget(read_box)

        # ---- loop controls (side-by-side) ----
        loop_row = QHBoxLayout()
        loop_row.setSpacing(16)
        loop_row.addWidget(self._make_loop_control(1))
        loop_row.addWidget(self._make_loop_control(2))
        root.addLayout(loop_row)

        # ---- emergency ----
        self._emergency_btn = QPushButton("⚠  EMERGENCY: All Heaters OFF")
        self._emergency_btn.setStyleSheet(
            "background-color: #c0392b; color: white; font-weight: bold;"
            "font-size: 14px; padding: 12px; border-radius: 4px;"
        )
        self._emergency_btn.clicked.connect(self._confirm_all_off)
        self._emergency_btn.setEnabled(False)
        root.addWidget(self._emergency_btn)

        # ---- log ----
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(120)
        self._log_text.setStyleSheet("font-size: 11px; font-family: Consolas, monospace;")
        root.addWidget(self._log_text)

    def _make_readout_panel(self, name: str) -> QGroupBox:
        box = QGroupBox(name)
        layout = QVBoxLayout(box)
        layout.setSpacing(4)

        temp_label = QLabel("-- K")
        temp_label.setAlignment(Qt.AlignCenter)
        temp_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(temp_label)

        details = QFormLayout()
        sp_label = QLabel("Setpoint: —")
        pid_label = QLabel("PID: —")
        heater_label = QLabel("Heater: —")
        range_label = QLabel("Range: —")
        details.addRow("Setpoint:", sp_label)
        details.addRow("PID:", pid_label)
        details.addRow("Heater:", heater_label)
        details.addRow("Range:", range_label)
        layout.addLayout(details)

        setattr(self, f"_{name.replace(' ', '_').lower()}_temp", temp_label)
        setattr(self, f"_{name.replace(' ', '_').lower()}_sp", sp_label)
        setattr(self, f"_{name.replace(' ', '_').lower()}_pid", pid_label)
        setattr(self, f"_{name.replace(' ', '_').lower()}_heater", heater_label)
        setattr(self, f"_{name.replace(' ', '_').lower()}_range", range_label)
        return box

    def _make_loop_control(self, loop: int) -> QGroupBox:
        box = QGroupBox(f"Loop {loop} Control")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        # setpoint
        sp_row = QHBoxLayout()
        sp_row.addWidget(QLabel("Setpoint:"))
        spin = QDoubleSpinBox()
        spin.setRange(0, 2000)
        spin.setDecimals(4)
        spin.setSuffix(" K")
        spin.setSingleStep(0.1)
        sp_row.addWidget(spin, 1)
        apply_sp = QPushButton("Apply")
        apply_sp.clicked.connect(lambda: self.setpoint_requested.emit(loop, spin.value()))
        sp_row.addWidget(apply_sp)
        layout.addLayout(sp_row)

        # PID
        pid_layout = QHBoxLayout()
        pid_layout.addWidget(QLabel("PID:"))
        p = QDoubleSpinBox(); p.setRange(0, 10000); p.setDecimals(1)
        i = QDoubleSpinBox(); i.setRange(0, 10000); i.setDecimals(1)
        d = QDoubleSpinBox(); d.setRange(0, 10000); d.setDecimals(1)
        p.setPrefix("P=")
        i.setPrefix("I=")
        d.setPrefix("D=")
        pid_layout.addWidget(p)
        pid_layout.addWidget(i)
        pid_layout.addWidget(d)
        apply_pid = QPushButton("Set")
        apply_pid.clicked.connect(
            lambda: self.pid_requested.emit(loop, p.value(), i.value(), d.value()))
        pid_layout.addWidget(apply_pid)
        layout.addLayout(pid_layout)

        # heater range radio buttons
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Heater Range:"))
        radios = []
        for level, name in enumerate(RANGE_LABELS):
            rb = QRadioButton(name)
            if level == 2:  # 默认 Medium (一律使用 Med 档位)
                rb.setChecked(True)
            rb.toggled.connect(
                lambda checked, lvl=level: (
                    self.heater_range_requested.emit(loop, lvl) if checked else None))
            radios.append(rb)
            range_row.addWidget(rb)
        range_row.addStretch()
        layout.addLayout(range_row)

        # store widgets
        setattr(self, f"_loop{loop}_sp", spin)
        setattr(self, f"_loop{loop}_p", p)
        setattr(self, f"_loop{loop}_i", i)
        setattr(self, f"_loop{loop}_d", d)
        setattr(self, f"_loop{loop}_radios", radios)

        return box

    def _confirm_all_off(self):
        reply = QMessageBox.warning(
            self, "Confirm Heater Off",
            "Set BOTH heater output ranges to OFF?\n\n"
            "This will stop all temperature control.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.all_heaters_off_requested.emit()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_connected(self, model: str):
        """已连接：绿灯，Connect 灰色禁用，Disconnect 红色启用。"""
        self._connected = True
        self._model = model
        self._status_light.set_green()
        self._model_label.setText(model)
        self._emergency_btn.setEnabled(True)
        # 按钮样式
        if hasattr(self, "_connect_btn"):
            self._connect_btn.setStyleSheet(CONNECT_STYLE_GRAY)
            self._connect_btn.setEnabled(False)
        if hasattr(self, "_disconnect_btn"):
            self._disconnect_btn.setStyleSheet(DISCONNECT_STYLE_RED)
            self._disconnect_btn.setEnabled(True)

    def set_disconnected(self):
        """已断开：红灯，Connect 绿色启用，Disconnect 灰色禁用。"""
        self._connected = False
        self._model = "—"
        self._status_light.set_red()
        self._model_label.setText("—")
        self._emergency_btn.setEnabled(False)
        # 按钮样式
        if hasattr(self, "_connect_btn"):
            self._connect_btn.setStyleSheet(CONNECT_STYLE_GREEN)
            self._connect_btn.setEnabled(True)
        if hasattr(self, "_disconnect_btn"):
            self._disconnect_btn.setStyleSheet(DISCONNECT_STYLE_GRAY)
            self._disconnect_btn.setEnabled(False)

    def set_connecting(self):
        """连接中：黄灯，两个按钮都禁用。"""
        self._status_light.set_yellow()
        if hasattr(self, "_connect_btn"):
            self._connect_btn.setEnabled(False)
        if hasattr(self, "_disconnect_btn"):
            self._disconnect_btn.setEnabled(False)

    def update_reading(self, r):
        """Accept a LakeShoreReading and refresh all live values."""
        # Input A
        self._input_a_temp.setText(_fmt_temp(r.temperature_a, decimals=4))
        self._input_a_sp.setText(f"Setpoint: {_fmt_temp(r.setpoint_1)}")
        pid_a = f"PID: {_fmt_pid(r.pid_p1, r.pid_i1, r.pid_d1)}"
        self._input_a_pid.setText(pid_a)
        self._input_a_heater.setText(f"Heater: {_fmt_temp(r.heater_1, '%', 1)}")
        self._input_a_range.setText(f"Range: {RANGE_LABELS[r.range_1] if r.range_1 is not None else '—'}")

        # Input B
        self._input_b_temp.setText(_fmt_temp(r.temperature_b, decimals=4))
        self._input_b_sp.setText(f"Setpoint: {_fmt_temp(r.setpoint_2)}")
        pid_b = f"PID: {_fmt_pid(r.pid_p2, r.pid_i2, r.pid_d2)}"
        self._input_b_pid.setText(pid_b)
        self._input_b_heater.setText(f"Heater: {_fmt_temp(r.heater_2, '%', 1)}")
        self._input_b_range.setText(f"Range: {RANGE_LABELS[r.range_2] if r.range_2 is not None else '—'}")

    def log(self, message: str):
        import time
        ts = time.strftime("[%H:%M:%S] ", time.localtime(time.time()))
        self._log_text.append(ts + message)

    def get_loop_settings(self, loop: int) -> dict:
        """Return current UI values for a loop (used when saving preset)."""
        sp = getattr(self, f"_loop{loop}_sp")
        p  = getattr(self, f"_loop{loop}_p")
        i  = getattr(self, f"_loop{loop}_i")
        d  = getattr(self, f"_loop{loop}_d")
        radios = getattr(self, f"_loop{loop}_radios")
        active_range = next(
            (idx for idx, rb in enumerate(radios) if rb.isChecked()), 0)
        return {
            "setpoint_k": sp.value(),
            "pid": {"p": p.value(), "i": i.value(), "d": d.value()},
            "heater_range": active_range,
        }

    def set_loop_settings(self, loop: int, settings: dict):
        """Apply saved settings to UI widgets."""
        sp = getattr(self, f"_loop{loop}_sp")
        p  = getattr(self, f"_loop{loop}_p")
        i  = getattr(self, f"_loop{loop}_i")
        d  = getattr(self, f"_loop{loop}_d")
        radios = getattr(self, f"_loop{loop}_radios")

        sp.setValue(settings.get("setpoint_k", 0))
        pid = settings.get("pid", {})
        p.setValue(pid.get("p", 10))
        i.setValue(pid.get("i", 20))
        d.setValue(pid.get("d", 0))
        rng = settings.get("heater_range", 0)
        if 0 <= rng < len(radios):
            radios[rng].setChecked(True)

    def set_address(self, addr: str):
        self._addr_label.setText(addr)
