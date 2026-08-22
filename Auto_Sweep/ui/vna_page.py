# -*- coding: utf-8 -*-
"""
VNA control detail page — Keysight P5003A (PXI VNA).

Features:
  - Connection bar with address, status light, model
  - Frequency settings (Start / Stop / Center / Span, linked)
  - S-parameter selection (S21 / S12)
  - 3×3 power toggle grid (–50 to –10 dBm, 5 dBm steps)
  - Sweep points + IF bandwidth numeric inputs
  - Preset save / load / delete
  - Quick actions: Apply Settings, Single Sweep + Save S2P
"""

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
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
# VNA power toggle grid (–50 to –10 dBm, step 5)
# ---------------------------------------------------------------------------

class VnaPowerGrid(QWidget):
    """3×3 toggle grid for VNA source power selection (–50 … –10 dBm)."""

    selection_changed = pyqtSignal(list)

    STYLE_OFF = """
        QPushButton {
            background-color: #34495e; color: #ecf0f1;
            border: 1px solid #2c3e50; border-radius: 6px;
            font-size: 14px; font-weight: bold;
            min-width: 72px; min-height: 42px;
        }
        QPushButton:hover {
            background-color: #3d566e;
        }
    """
    STYLE_ON = """
        QPushButton {
            background-color: #2980b9; color: white;
            border: 1px solid #1a5276; border-radius: 6px;
            font-size: 14px; font-weight: bold;
            min-width: 72px; min-height: 42px;
        }
        QPushButton:hover {
            background-color: #3498db;
        }
    """

    POWER_VALUES = list(range(-50, -5, 5))  # –50, –45, …, –10

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._buttons: dict[int, QPushButton] = {}
        self._selected: set[int] = set()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setSpacing(6)

        for i, dbm in enumerate(self.POWER_VALUES):
            row, col = divmod(i, 3)
            btn = QPushButton(f"{dbm:+d}")
            btn.clicked.connect(lambda checked, v=dbm: self._toggle(v))
            btn.setStyleSheet(self.STYLE_OFF)
            self._buttons[dbm] = btn
            grid.addWidget(btn, row, col)

        layout.addLayout(grid)

        # info row
        info_row = QHBoxLayout()
        self._info_label = QLabel("Selected: —")
        self._info_label.setStyleSheet("color: #bdc3c7; font-size: 12px;")
        clear = QPushButton("Clear All")
        clear.clicked.connect(self.clear_all)
        info_row.addWidget(self._info_label)
        info_row.addStretch()
        info_row.addWidget(clear)
        layout.addLayout(info_row)

    def _toggle(self, dbm: int):
        if dbm in self._selected:
            self._selected.discard(dbm)
            self._buttons[dbm].setStyleSheet(self.STYLE_OFF)
        else:
            self._selected.add(dbm)
            self._buttons[dbm].setStyleSheet(self.STYLE_ON)
        self._emit()

    def _emit(self):
        seq = sorted(self._selected)
        text = ", ".join(f"{v:+d}" for v in seq) if seq else "—"
        self._info_label.setText(
            f"Selected: {text}  ({len(seq)} powers)")
        self.selection_changed.emit(seq)

    def clear_all(self):
        for dbm in list(self._selected):
            self._selected.discard(dbm)
            self._buttons[dbm].setStyleSheet(self.STYLE_OFF)
        self._emit()

    def set_selection(self, values: list):
        self.clear_all()
        for dbm in values:
            if dbm in self.POWER_VALUES:
                self._selected.add(dbm)
                self._buttons[dbm].setStyleSheet(self.STYLE_ON)
        self._emit()

    def get_selection(self) -> list:
        return sorted(self._selected)


# ---------------------------------------------------------------------------
# VnaPowerRangeWidget — 功率区间扫描设置（并行于按钮网格）
# ---------------------------------------------------------------------------

class VnaPowerRangeWidget(QWidget):
    """VNA source power range sweep — similar to TemperatureSweepWidget.

    Generates a list of dBm values from start/stop/step, used in parallel
    with the VnaPowerGrid toggle selection.  Both systems' values are
    merged (union, sorted, deduplicated) before being sent to the VNA.

    Default range: -55 to -45 dBm, step 2 dB (user's YBCO test config).
    """

    selection_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        import config

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # ---- enable checkbox ----
        self._enabled_check = QPushButton("☑ Power Range Sweep")
        self._enabled_check.setCheckable(True)
        self._enabled_check.setChecked(True)
        self._enabled_check.setStyleSheet("""
            QPushButton {
                text-align: left; font-weight: bold; color: #4ADE80;
                background: transparent; border: none; padding: 4px 0;
            }
            QPushButton:checked {
                color: #4ADE80;
            }
            QPushButton:!checked {
                color: #8B949E;
            }
        """)
        self._enabled_check.toggled.connect(self._on_changed)
        root.addWidget(self._enabled_check)

        # ---- range spinboxes ----
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("Start:"))
        self._start_spin = QSpinBox()
        self._start_spin.setRange(-60, 10)
        self._start_spin.setValue(config.vna_power_range_default_start_dbm)
        self._start_spin.setSuffix(" dBm")
        self._start_spin.valueChanged.connect(self._on_changed)
        row.addWidget(self._start_spin)

        row.addWidget(QLabel("Stop:"))
        self._stop_spin = QSpinBox()
        self._stop_spin.setRange(-60, 10)
        self._stop_spin.setValue(config.vna_power_range_default_stop_dbm)
        self._stop_spin.setSuffix(" dBm")
        self._stop_spin.valueChanged.connect(self._on_changed)
        row.addWidget(self._stop_spin)

        row.addWidget(QLabel("Step:"))
        self._step_spin = QSpinBox()
        self._step_spin.setRange(1, 20)
        self._step_spin.setValue(config.vna_power_range_default_step_db)
        self._step_spin.setSuffix(" dB")
        self._step_spin.valueChanged.connect(self._on_changed)
        row.addWidget(self._step_spin)

        row.addStretch()
        root.addLayout(row)

        # ---- preview ----
        self._preview_label = QLabel()
        self._preview_label.setStyleSheet(
            "color: #8B949E; font-size: 11px;"
            "font-family: 'JetBrains Mono', 'Consolas', monospace;")
        root.addWidget(self._preview_label)
        self._update_preview()

    def _on_changed(self):
        self._update_preview()
        self.selection_changed.emit()

    def _update_preview(self):
        powers = self.get_powers()
        if not powers:
            self._preview_label.setText("Range: — (disabled or empty)")
            return
        text = ", ".join(f"{v:+d}" for v in powers)
        self._preview_label.setText(f"Range: {text}  ({len(powers)} powers)")

    # ---- public API ----

    def get_powers(self) -> list:
        """Return the generated dBm list, or empty if disabled or invalid."""
        if not self._enabled_check.isChecked():
            return []
        start = self._start_spin.value()
        stop = self._stop_spin.value()
        step = self._step_spin.value()
        if step <= 0 or start > stop:
            return []
        result = []
        v = start
        while v <= stop + 0.001:
            result.append(v)
            v += step
        return result

    def get_settings(self) -> dict:
        return {
            "enabled": self._enabled_check.isChecked(),
            "start_dbm": self._start_spin.value(),
            "stop_dbm": self._stop_spin.value(),
            "step_db": self._step_spin.value(),
        }

    def set_settings(self, s: dict):
        if "enabled" in s:
            self._enabled_check.setChecked(bool(s["enabled"]))
        if "start_dbm" in s:
            self._start_spin.setValue(int(s["start_dbm"]))
        if "stop_dbm" in s:
            self._stop_spin.setValue(int(s["stop_dbm"]))
        if "step_db" in s:
            self._step_spin.setValue(int(s["step_db"]))
        self._update_preview()


# ---------------------------------------------------------------------------
# VNAPage
# ---------------------------------------------------------------------------

class VNAPage(QWidget):
    """VNA detail control page with full parameter access."""

    back_clicked = pyqtSignal()

    # connection
    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()

    # settings
    settings_apply_requested = pyqtSignal(dict)  # all settings in one dict
    single_sweep_requested = pyqtSignal(str)     # save_path

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._connected = False
        self._build_ui()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 16)

        # ---- back row ----
        back_row = QHBoxLayout()
        back_btn = QPushButton("← Back to Dashboard")
        back_btn.clicked.connect(self.back_clicked.emit)
        back_row.addWidget(back_btn)
        back_row.addStretch()
        title = QLabel("VNA Control")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        back_row.addWidget(title)
        back_row.addStretch()
        root.addLayout(back_row)

        # ---- connection ----
        root.addWidget(self._build_connection_box())

        # ---- frequency ----
        root.addWidget(self._build_frequency_box())

        # ---- S-parameter ----
        root.addWidget(self._build_sparam_box())

        # ---- power grid ----
        root.addWidget(self._build_power_box())

        # ---- sweep settings ----
        root.addWidget(self._build_sweep_box())

        # ---- quick actions ----
        root.addWidget(self._build_actions_box())

        root.addStretch()

    def _build_connection_box(self) -> QGroupBox:
        box = QGroupBox("Connection")
        layout = QFormLayout(box)
        self._addr_label = QLabel(
            "TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR")
        self._status_light = StatusLight(14)
        self._model_label = QLabel("—")
        sr = QHBoxLayout()
        sr.addWidget(self._status_light)
        sr.addWidget(self._model_label)
        sr.addStretch()
        layout.addRow("Address:", self._addr_label)
        layout.addRow("Status:", sr)

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
        layout.addRow("", btn_row)

        return box

    def _build_frequency_box(self) -> QGroupBox:
        box = QGroupBox("Frequency")
        layout = QFormLayout(box)

        # start / stop row
        ss_row = QHBoxLayout()
        ss_row.addWidget(QLabel("Start:"))
        self._start_spin = QDoubleSpinBox()
        self._start_spin.setRange(1.0, 14.0)
        self._start_spin.setDecimals(3)
        self._start_spin.setSuffix(" GHz")
        self._start_spin.setValue(3.0)
        self._start_spin.valueChanged.connect(self._on_start_changed)
        ss_row.addWidget(self._start_spin)
        ss_row.addSpacing(16)
        ss_row.addWidget(QLabel("Stop:"))
        self._stop_spin = QDoubleSpinBox()
        self._stop_spin.setRange(1.0, 14.0)
        self._stop_spin.setDecimals(3)
        self._stop_spin.setSuffix(" GHz")
        self._stop_spin.setValue(6.0)
        self._stop_spin.valueChanged.connect(self._on_stop_changed)
        ss_row.addWidget(self._stop_spin)
        ss_row.addStretch()
        layout.addRow(ss_row)

        # center / span row
        cs_row = QHBoxLayout()
        cs_row.addWidget(QLabel("Center:"))
        self._center_spin = QDoubleSpinBox()
        self._center_spin.setRange(1.0, 14.0)
        self._center_spin.setDecimals(3)
        self._center_spin.setSuffix(" GHz")
        self._center_spin.setValue(4.5)
        self._center_spin.valueChanged.connect(self._on_center_changed)
        cs_row.addWidget(self._center_spin)
        cs_row.addSpacing(16)
        cs_row.addWidget(QLabel("Span:"))
        self._span_spin = QDoubleSpinBox()
        self._span_spin.setRange(0.0, 14.0)
        self._span_spin.setDecimals(3)
        self._span_spin.setSuffix(" GHz")
        self._span_spin.setValue(3.0)
        self._span_spin.valueChanged.connect(self._on_span_changed)
        cs_row.addWidget(self._span_spin)
        cs_row.addStretch()
        layout.addRow(cs_row)

        return box

    # frequency sync logic: changing one pair updates the other
    _freq_syncing = False

    def _on_start_changed(self, ghz: float):
        if self._freq_syncing:
            return
        self._freq_syncing = True
        stop = self._stop_spin.value()
        self._center_spin.setValue((ghz + stop) / 2)
        self._span_spin.setValue(stop - ghz)
        self._freq_syncing = False

    def _on_stop_changed(self, ghz: float):
        if self._freq_syncing:
            return
        self._freq_syncing = True
        start = self._start_spin.value()
        self._center_spin.setValue((start + ghz) / 2)
        self._span_spin.setValue(ghz - start)
        self._freq_syncing = False

    def _on_center_changed(self, ghz: float):
        if self._freq_syncing:
            return
        self._freq_syncing = True
        span = self._span_spin.value()
        self._start_spin.setValue(ghz - span / 2)
        self._stop_spin.setValue(ghz + span / 2)
        self._freq_syncing = False

    def _on_span_changed(self, ghz: float):
        if self._freq_syncing:
            return
        self._freq_syncing = True
        center = self._center_spin.value()
        self._start_spin.setValue(center - ghz / 2)
        self._stop_spin.setValue(center + ghz / 2)
        self._freq_syncing = False

    def _build_sparam_box(self) -> QGroupBox:
        box = QGroupBox("S-Parameter")
        layout = QHBoxLayout(box)
        layout.addWidget(QLabel("Measurement:"))
        self._sparam_combo = QComboBox()
        self._sparam_combo.addItems(["S21", "S12"])
        layout.addWidget(self._sparam_combo)
        layout.addStretch()
        return box

    def _build_power_box(self) -> QGroupBox:
        box = QGroupBox("Source Power  (grid + range sweep, merged in parallel)")
        layout = QVBoxLayout(box)
        self._power_grid = VnaPowerGrid()
        layout.addWidget(self._power_grid)

        # ---- range sweep (parallel to grid) ----
        self._power_range = VnaPowerRangeWidget()
        self._power_range.selection_changed.connect(
            lambda: None)  # 预留信号，UI 联动可后续添加
        layout.addWidget(self._power_range)
        return box

    def _build_sweep_box(self) -> QGroupBox:
        box = QGroupBox("Sweep Settings")
        layout = QHBoxLayout(box)
        layout.addWidget(QLabel("Points:"))
        self._points_spin = QSpinBox()
        self._points_spin.setRange(2, 200001)
        self._points_spin.setValue(50001)
        self._points_spin.setSingleStep(100)
        layout.addWidget(self._points_spin)
        layout.addSpacing(24)
        layout.addWidget(QLabel("IF BW:"))
        self._ifbw_spin = QSpinBox()
        self._ifbw_spin.setRange(1, 1000000)
        self._ifbw_spin.setValue(10000)
        self._ifbw_spin.setSuffix(" Hz")
        self._ifbw_spin.setSingleStep(100)
        layout.addWidget(self._ifbw_spin)
        layout.addStretch()
        return box

    def _build_actions_box(self) -> QGroupBox:
        box = QGroupBox("Quick Actions")
        layout = QHBoxLayout(box)

        apply_btn = QPushButton("Apply All Settings")
        apply_btn.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 8px;")
        apply_btn.clicked.connect(self._on_apply_settings)
        apply_btn.setEnabled(False)
        self._apply_btn = apply_btn
        layout.addWidget(apply_btn)

        sweep_btn = QPushButton("Single Sweep → Save S2P")
        sweep_btn.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        sweep_btn.clicked.connect(self._on_single_sweep)
        sweep_btn.setEnabled(False)
        self._sweep_btn = sweep_btn
        layout.addWidget(sweep_btn)

        layout.addStretch()
        return box

    # ==================================================================
    # action handlers
    # ==================================================================

    def _on_apply_settings(self):
        settings = self.get_all_settings()
        self.settings_apply_requested.emit(settings)

    def _on_single_sweep(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save S2P File", "", "S2P Files (*.s2p);;All Files (*)")
        if path:
            self.single_sweep_requested.emit(path)

    # ==================================================================
    # get / set all settings
    # ==================================================================

    def get_all_settings(self) -> dict:
        button_powers = self._power_grid.get_selection()
        range_powers = self._power_range.get_powers()
        # 并行合并：按钮点选 + 区间生成 → 并集去重升序
        all_powers = sorted(set(button_powers + range_powers))
        return {
            "start_freq_hz": self._start_spin.value() * 1e9,
            "stop_freq_hz": self._stop_spin.value() * 1e9,
            "s_parameter": self._sparam_combo.currentText(),
            "power_dbm": all_powers,
            "power_dbm_button": button_powers,
            "power_range_settings": self._power_range.get_settings(),
            "points": self._points_spin.value(),
            "if_bandwidth_hz": self._ifbw_spin.value(),
        }

    def set_all_settings(self, settings: dict):
        """Apply a settings dict to all UI controls (e.g. from preset)."""
        if "start_freq_hz" in settings:
            self._freq_syncing = True
            self._start_spin.setValue(settings["start_freq_hz"] / 1e9)
            self._freq_syncing = False
        if "stop_freq_hz" in settings:
            self._freq_syncing = True
            self._stop_spin.setValue(settings["stop_freq_hz"] / 1e9)
            self._freq_syncing = False
        if "s_parameter" in settings:
            idx = self._sparam_combo.findText(settings["s_parameter"])
            if idx >= 0:
                self._sparam_combo.setCurrentIndex(idx)
        if "power_dbm_button" in settings:
            self._power_grid.set_selection(settings["power_dbm_button"])
        elif "power_dbm" in settings:
            # 向后兼容旧格式：仅有 power_dbm 时还原到按钮网格
            self._power_grid.set_selection(settings["power_dbm"])
        if "power_range_settings" in settings:
            self._power_range.set_settings(settings["power_range_settings"])
        if "points" in settings:
            self._points_spin.setValue(settings["points"])
        if "if_bandwidth_hz" in settings:
            self._ifbw_spin.setValue(settings["if_bandwidth_hz"])

    # ==================================================================
    # connection state
    # ==================================================================

    def set_connected(self, model: str):
        """已连接：绿灯，Connect 灰色禁用，Disconnect 红色启用。"""
        self._connected = True
        self._status_light.set_green()
        self._model_label.setText(model)
        self._apply_btn.setEnabled(True)
        self._sweep_btn.setEnabled(True)
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
        self._status_light.set_red()
        self._model_label.setText("—")
        self._apply_btn.setEnabled(False)
        self._sweep_btn.setEnabled(False)
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

    # ==================================================================
    # preset access
    # ==================================================================

    def get_power_sequence(self) -> list:
        return self._power_grid.get_selection()

    def set_power_sequence(self, values: list):
        self._power_grid.set_selection(values)

    def set_address(self, addr: str):
        self._addr_label.setText(addr)
