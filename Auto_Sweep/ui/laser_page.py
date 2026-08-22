# -*- coding: utf-8 -*-
"""
Laser control detail page.

Features:
  - Connection bar with address, status light, model
  - 3×6 power toggle grid (buttons 0–17 mW)
  - Selected sequence display
  - Wavelength input (1520–1570 nm)
  - Preset save / load / delete
  - Quick actions: Output ON, Output OFF, Physical Off
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import StatusLight


# ---------------------------------------------------------------------------
# Connect/Disconnect 按钮样式（与 dashboard_page.py 保持一致）
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
# PowerToggleGrid
# ---------------------------------------------------------------------------

class PowerToggleGrid(QWidget):
    """3 rows × 6 columns of toggle-able power buttons (0–17 mW)."""

    selection_changed = pyqtSignal(list)  # sorted list of selected mW values

    STYLE_OFF = """
        QPushButton {
            background-color: #34495e; color: #ecf0f1;
            border: 1px solid #2c3e50; border-radius: 6px;
            font-size: 14px; font-weight: bold;
            min-width: 60px; min-height: 42px;
        }
        QPushButton:hover {
            background-color: #3d566e;
        }
    """
    STYLE_ON = """
        QPushButton {
            background-color: #27ae60; color: white;
            border: 1px solid #1e8449; border-radius: 6px;
            font-size: 14px; font-weight: bold;
            min-width: 60px; min-height: 42px;
        }
        QPushButton:hover {
            background-color: #2ecc71;
        }
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._buttons: dict[int, QPushButton] = {}
        self._selected: set[int] = set()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setSpacing(6)

        values = list(range(18))  # 0–17
        for i, mw in enumerate(values):
            row, col = divmod(i, 6)
            btn = QPushButton(f"{mw:2d}")
            btn.setCheckable(False)
            btn.clicked.connect(lambda checked, v=mw: self._toggle(v))
            btn.setStyleSheet(self.STYLE_OFF)
            self._buttons[mw] = btn
            grid.addWidget(btn, row, col)

        layout.addLayout(grid)

        # info row
        info_row = QHBoxLayout()
        self._info_label = QLabel("Selected: —  (0 powers)")
        self._info_label.setStyleSheet("color: #bdc3c7; font-size: 12px;")
        clear_btn = QPushButton("Clear All")
        all_btn = QPushButton("Select All")
        clear_btn.clicked.connect(self.clear_all)
        all_btn.clicked.connect(self.select_all)
        info_row.addWidget(self._info_label)
        info_row.addStretch()
        info_row.addWidget(clear_btn)
        info_row.addWidget(all_btn)
        layout.addLayout(info_row)

    def _toggle(self, mw: int):
        if mw in self._selected:
            self._selected.discard(mw)
            self._buttons[mw].setStyleSheet(self.STYLE_OFF)
        else:
            self._selected.add(mw)
            self._buttons[mw].setStyleSheet(self.STYLE_ON)
        self._emit()

    def _emit(self):
        seq = sorted(self._selected)
        text = ", ".join(str(v) for v in seq) if seq else "—"
        self._info_label.setText(
            f"Selected: {text}  ({len(seq)} powers)")
        self.selection_changed.emit(seq)

    def clear_all(self):
        for mw in list(self._selected):
            self._selected.discard(mw)
            self._buttons[mw].setStyleSheet(self.STYLE_OFF)
        self._emit()

    def select_all(self):
        for mw in range(18):
            self._selected.add(mw)
            self._buttons[mw].setStyleSheet(self.STYLE_ON)
        self._emit()

    def set_selection(self, values: list):
        """Programmatically set selected powers (e.g. from preset)."""
        self.clear_all()
        for mw in values:
            if 0 <= mw <= 17:
                self._selected.add(mw)
                self._buttons[mw].setStyleSheet(self.STYLE_ON)
        self._emit()

    def get_selection(self) -> list:
        return sorted(self._selected)


# ---------------------------------------------------------------------------
# LaserPage
# ---------------------------------------------------------------------------

class LaserPage(QWidget):
    """Laser detail control page."""

    back_clicked = pyqtSignal()

    # outgoing requests
    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()
    power_set_requested = pyqtSignal(float)
    wavelength_set_requested = pyqtSignal(float)
    output_on_requested = pyqtSignal()
    output_off_requested = pyqtSignal()
    physical_off_requested = pyqtSignal()
    status_refresh_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._connected = False
        self._model = "—"
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 16)

        # ---- back button ----
        back_row = QHBoxLayout()
        back_btn = QPushButton("← Back to Dashboard")
        back_btn.clicked.connect(self.back_clicked.emit)
        back_row.addWidget(back_btn)
        back_row.addStretch()
        title = QLabel("Laser Control")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        back_row.addWidget(title)
        back_row.addStretch()
        root.addLayout(back_row)

        # ---- connection ----
        conn_box = QGroupBox("Connection")
        conn_layout = QFormLayout(conn_box)
        self._addr_label = QLabel("TCPIP0::169.254.77.29::INSTR")
        self._status_light = StatusLight(14)
        self._model_label = QLabel("—")
        status_row = QHBoxLayout()
        status_row.addWidget(self._status_light)
        status_row.addWidget(self._model_label)
        status_row.addStretch()
        conn_layout.addRow("Address:", self._addr_label)
        conn_layout.addRow("Status:", status_row)

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

        # ---- power grid ----
        power_box = QGroupBox("Power Sweep Sequence  (click to toggle)")
        power_layout = QVBoxLayout(power_box)
        self._power_grid = PowerToggleGrid()
        power_layout.addWidget(self._power_grid)
        root.addWidget(power_box)

        # ---- wavelength ----
        wave_box = QGroupBox("Wavelength")
        wave_layout = QHBoxLayout(wave_box)
        wave_layout.addWidget(QLabel("Set:"))
        self._wave_spin = QDoubleSpinBox()
        self._wave_spin.setRange(1520, 1570)
        self._wave_spin.setDecimals(3)
        self._wave_spin.setValue(1550)
        self._wave_spin.setSuffix(" nm")
        self._wave_spin.setSingleStep(0.1)
        wave_layout.addWidget(self._wave_spin)
        self._current_wave_label = QLabel("Current: — nm")
        self._current_wave_label.setStyleSheet("color: #7f8c8d;")
        wave_layout.addWidget(self._current_wave_label)
        wave_layout.addStretch()
        self._apply_wave_btn = QPushButton("Apply Wavelength")
        self._apply_wave_btn.clicked.connect(
            lambda: self.wavelength_set_requested.emit(self._wave_spin.value()))
        wave_layout.addWidget(self._apply_wave_btn)
        root.addWidget(wave_box)

        # ---- quick actions ----
        action_box = QGroupBox("Quick Actions")
        action_layout = QHBoxLayout(action_box)
        self._on_btn = QPushButton("Output ON")
        self._off_btn = QPushButton("Output OFF")
        self._physical_btn = QPushButton("⚠ Physical Off")
        self._on_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        self._off_btn.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 8px;")
        self._physical_btn.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; padding: 8px;")
        self._on_btn.clicked.connect(self.output_on_requested.emit)
        self._off_btn.clicked.connect(self.output_off_requested.emit)
        self._physical_btn.clicked.connect(self._confirm_physical_off)
        self._on_btn.setEnabled(False)
        self._off_btn.setEnabled(False)
        self._physical_btn.setEnabled(False)
        action_layout.addWidget(self._on_btn)
        action_layout.addWidget(self._off_btn)
        action_layout.addWidget(self._physical_btn)
        root.addWidget(action_box)

        root.addStretch()

    def _confirm_physical_off(self):
        reply = QMessageBox.warning(
            self, "Confirm Physical Shutdown",
            "This will physically turn off the laser.\n"
            "Only use in emergency situations.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.physical_off_requested.emit()

    # ---- public API ----

    def set_connected(self, model: str):
        """已连接：绿灯，Connect 灰色禁用，Disconnect 红色启用。"""
        self._connected = True
        self._model = model
        self._status_light.set_green()
        self._model_label.setText(model)
        self._on_btn.setEnabled(True)
        self._off_btn.setEnabled(True)
        self._physical_btn.setEnabled(True)
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
        self._on_btn.setEnabled(False)
        self._off_btn.setEnabled(False)
        self._physical_btn.setEnabled(False)
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

    def update_status(self, status: dict):
        """Update UI from a status dict (from LaserController.get_status())."""
        if status.get("wavelength_nm") and status["wavelength_nm"] > 0:
            self._current_wave_label.setText(f"Current: {status['wavelength_nm']:.3f} nm")

    def get_power_sequence(self) -> list:
        return self._power_grid.get_selection()

    def set_power_sequence(self, values: list):
        self._power_grid.set_selection(values)

    def get_wavelength(self) -> float:
        return self._wave_spin.value()

    def set_wavelength(self, nm: float):
        self._wave_spin.setValue(nm)

    def set_address(self, addr: str):
        self._addr_label.setText(addr)
