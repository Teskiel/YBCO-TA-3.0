# -*- coding: utf-8 -*-
"""筛选面板 — Pv/Pl 开关按钮 + 温度范围 + 视图模式。

提供完整的实验参数筛选控件：
  - VNA 功率 Pv: 3×3 开关按钮（−50~−10 dBm，步进 5）
  - 激光功率 Pl: 3×6 开关按钮（0~17 mW）
  - 温度范围 Tr: 起始/终止 QDoubleSpinBox
  - 视图模式: Overlay / Grid 切换
"""

from enum import Enum
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# =========================================================================
# 常量：Pv / Pl 完整可选值（来自 GUI 测试代码，非数据反推）
# =========================================================================

PV_VALUES = list(range(-50, -5, 5))   # −50, −45, …, −10  (9 个)
PL_VALUES = list(range(18))            # 0, 1, …, 17        (18 个)


class ViewMode(Enum):
    OVERLAY = "overlay"   # 单图 Tr 叠加
    GRID = "grid"         # Pv×Pl 子图矩阵


# =========================================================================
# 按钮样式（与项目 Deep Space Cyan 主题一致）
# =========================================================================

STYLE_OFF = """
    QPushButton {
        background-color: #34495e; color: #ecf0f1;
        border: 1px solid #2c3e50; border-radius: 6px;
        font-size: 12px; font-weight: bold;
        min-width: 52px; min-height: 34px;
    }
    QPushButton:hover {
        background-color: #3d566e;
    }
"""

STYLE_ON = """
    QPushButton {
        background-color: #22D3EE; color: #0C1014;
        border: 1px solid #06B6D4; border-radius: 6px;
        font-size: 12px; font-weight: bold;
        min-width: 52px; min-height: 34px;
    }
    QPushButton:hover {
        background-color: #67E8F9;
    }
"""

STYLE_MODE_OFF = """
    QPushButton {
        background-color: #34495e; color: #8B949E;
        border: 1px solid #30363D; border-radius: 6px;
        font-size: 12px; font-weight: bold;
        min-width: 72px; min-height: 30px;
    }
    QPushButton:hover {
        background-color: #3d566e; color: #E6EDF3;
    }
"""

STYLE_MODE_ON = """
    QPushButton {
        background-color: #22D3EE; color: #0C1014;
        border: 1px solid #06B6D4; border-radius: 6px;
        font-size: 12px; font-weight: bold;
        min-width: 72px; min-height: 30px;
    }
"""


# =========================================================================
# FilterPanel
# =========================================================================


class FilterPanel(QWidget):
    """实验参数筛选面板。

    每当任何筛选条件变化时发射 filters_changed(dict)。
    """

    filters_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pv_buttons: Dict[int, QPushButton] = {}
        self._pl_buttons: Dict[int, QPushButton] = {}
        self._selected_pv: set = set()
        self._selected_pl: set = set()
        self._view_mode = ViewMode.OVERLAY
        self._build_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(0, 0, 0, 0)

        # ---- 视图模式 ----
        root.addWidget(self._build_view_mode_box())

        # ---- VNA 功率 (Pv) ----
        root.addWidget(self._build_pv_box())

        # ---- 激光功率 (Pl) ----
        root.addWidget(self._build_pl_box())

        # ---- 温度范围 (Tr) ----
        root.addWidget(self._build_tr_box())

        root.addStretch()

    def _build_view_mode_box(self) -> QGroupBox:
        box = QGroupBox("View Mode")
        layout = QHBoxLayout(box)
        layout.setSpacing(6)

        self._btn_overlay = QPushButton("Overlay")
        self._btn_overlay.setStyleSheet(STYLE_MODE_ON)
        self._btn_overlay.clicked.connect(lambda: self._set_view_mode(ViewMode.OVERLAY))

        self._btn_grid = QPushButton("Grid")
        self._btn_grid.setStyleSheet(STYLE_MODE_OFF)
        self._btn_grid.clicked.connect(lambda: self._set_view_mode(ViewMode.GRID))

        layout.addWidget(self._btn_overlay)
        layout.addWidget(self._btn_grid)
        layout.addStretch()
        return box

    def _build_pv_box(self) -> QGroupBox:
        box = QGroupBox("VNA Power Pv (dBm)")
        layout = QVBoxLayout(box)
        layout.setSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(4)
        for i, dbm in enumerate(PV_VALUES):
            row, col = divmod(i, 3)
            btn = QPushButton(f"{dbm:+d}")
            btn.setStyleSheet(STYLE_OFF)
            btn.clicked.connect(lambda checked, v=dbm: self._toggle_pv(v))
            self._pv_buttons[dbm] = btn
            grid.addWidget(btn, row, col)
        layout.addLayout(grid)

        # 全选 / 清除 行
        row = QHBoxLayout()
        sel_all = QPushButton("All")
        sel_all.clicked.connect(self.select_all_pv)
        clear = QPushButton("None")
        clear.clicked.connect(self.clear_pv)
        row.addWidget(sel_all)
        row.addWidget(clear)
        row.addStretch()
        layout.addLayout(row)

        return box

    def _build_pl_box(self) -> QGroupBox:
        box = QGroupBox("Laser Power Pl (mW)")
        layout = QVBoxLayout(box)
        layout.setSpacing(4)

        grid = QGridLayout()
        grid.setSpacing(4)
        for i, mw in enumerate(PL_VALUES):
            row, col = divmod(i, 6)
            btn = QPushButton(f"{mw:2d}")
            btn.setStyleSheet(STYLE_OFF)
            btn.clicked.connect(lambda checked, v=mw: self._toggle_pl(v))
            self._pl_buttons[mw] = btn
            grid.addWidget(btn, row, col)
        layout.addLayout(grid)

        # 全选 / 清除 行
        row = QHBoxLayout()
        sel_all = QPushButton("All")
        sel_all.clicked.connect(self.select_all_pl)
        clear = QPushButton("None")
        clear.clicked.connect(self.clear_pl)
        row.addWidget(sel_all)
        row.addWidget(clear)
        row.addStretch()
        layout.addLayout(row)

        return box

    def _build_tr_box(self) -> QGroupBox:
        box = QGroupBox("Temperature Tr (K)")
        layout = QFormLayout(box)

        self._tr_start = QDoubleSpinBox()
        self._tr_start.setRange(4.0, 300.0)
        self._tr_start.setDecimals(1)
        self._tr_start.setValue(4.0)
        self._tr_start.setSuffix(" K")
        self._tr_start.valueChanged.connect(self._emit_filters)

        self._tr_end = QDoubleSpinBox()
        self._tr_end.setRange(4.0, 300.0)
        self._tr_end.setDecimals(1)
        self._tr_end.setValue(100.0)
        self._tr_end.setSuffix(" K")
        self._tr_end.valueChanged.connect(self._emit_filters)

        layout.addRow("Start:", self._tr_start)
        layout.addRow("End:", self._tr_end)
        return box

    # ------------------------------------------------------------------
    # 开关逻辑
    # ------------------------------------------------------------------

    def _toggle_pv(self, dbm: int):
        if dbm in self._selected_pv:
            self._selected_pv.discard(dbm)
            self._pv_buttons[dbm].setStyleSheet(STYLE_OFF)
        else:
            self._selected_pv.add(dbm)
            self._pv_buttons[dbm].setStyleSheet(STYLE_ON)
        self._emit_filters()

    def _toggle_pl(self, mw: int):
        if mw in self._selected_pl:
            self._selected_pl.discard(mw)
            self._pl_buttons[mw].setStyleSheet(STYLE_OFF)
        else:
            self._selected_pl.add(mw)
            self._pl_buttons[mw].setStyleSheet(STYLE_ON)
        self._emit_filters()

    def _set_view_mode(self, mode: ViewMode):
        self._view_mode = mode
        self._btn_overlay.setStyleSheet(
            STYLE_MODE_ON if mode == ViewMode.OVERLAY else STYLE_MODE_OFF)
        self._btn_grid.setStyleSheet(
            STYLE_MODE_ON if mode == ViewMode.GRID else STYLE_MODE_OFF)
        self._emit_filters()

    def _emit_filters(self):
        self.filters_changed.emit(self.get_filters())

    # ------------------------------------------------------------------
    # 批量操作
    # ------------------------------------------------------------------

    def select_all_pv(self):
        for dbm in PV_VALUES:
            self._selected_pv.add(dbm)
            self._pv_buttons[dbm].setStyleSheet(STYLE_ON)
        self._emit_filters()

    def clear_pv(self):
        self._selected_pv.clear()
        for dbm in PV_VALUES:
            self._pv_buttons[dbm].setStyleSheet(STYLE_OFF)
        self._emit_filters()

    def select_all_pl(self):
        for mw in PL_VALUES:
            self._selected_pl.add(mw)
            self._pl_buttons[mw].setStyleSheet(STYLE_ON)
        self._emit_filters()

    def clear_pl(self):
        self._selected_pl.clear()
        for mw in PL_VALUES:
            self._pl_buttons[mw].setStyleSheet(STYLE_OFF)
        self._emit_filters()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_selected_pv(self) -> List[int]:
        return sorted(self._selected_pv)

    def get_selected_pl(self) -> List[int]:
        return sorted(self._selected_pl)

    def get_view_mode(self) -> ViewMode:
        return self._view_mode

    def get_filters(self) -> dict:
        """返回当前筛选状态。"""
        return {
            "vna_powers": self.get_selected_pv(),
            "laser_powers": self.get_selected_pl(),
            "temp_min_k": self._tr_start.value(),
            "temp_max_k": self._tr_end.value(),
            "view_mode": self._view_mode,
        }
