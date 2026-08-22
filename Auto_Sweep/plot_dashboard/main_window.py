# -*- coding: utf-8 -*-
"""Plot Dashboard 主窗口。

布局：左侧筛选面板 + 右侧 matplotlib 画布 + 顶部工具栏。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT

from plot_dashboard.filter_panel import FilterPanel, ViewMode
from plot_dashboard.plot_canvas import PlotCanvas
from plot_dashboard.plot_controller import PlotController


class PlotDashboardMainWindow(QMainWindow):
    """画图专用 Dashboard 主窗口。

    独立于 Auto_Sweep 主 GUI，可单独启动用于实验后数据分析。
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("YBCO KID Plot Dashboard")
        self.resize(1400, 900)

        # ---- 核心组件 ----
        self._controller = PlotController(self)
        self._filter_panel = FilterPanel()
        self._plot_canvas = PlotCanvas()

        self._build_ui()
        self._wire_signals()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 8, 12, 8)

        # ---- 工具栏 ----
        root.addLayout(self._build_toolbar())

        # ---- 主布局：左筛选 + 右画布 ----
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：FilterPanel
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._filter_panel)
        splitter.addWidget(left_widget)

        # 右侧：PlotCanvas + 导航工具栏
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # matplotlib 导航工具栏
        self._nav_toolbar = NavigationToolbar2QT(self._plot_canvas, self)
        self._nav_toolbar.setStyleSheet(
            "background-color: #161B22; border: none;"
        )
        right_layout.addWidget(self._nav_toolbar)
        right_layout.addWidget(self._plot_canvas)

        splitter.addWidget(right_widget)

        # 比例：左侧 300px，右侧自适应
        splitter.setSizes([300, 1100])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

        # ---- 状态栏 ----
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "background-color: #161B22; color: #8B949E;"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready — Open a directory to begin.")

    def _build_toolbar(self) -> QHBoxLayout:
        """构建顶部工具栏。"""
        layout = QHBoxLayout()
        layout.setSpacing(8)

        # Open Dir
        self._btn_open = QPushButton("Open Dir")
        self._btn_open.setStyleSheet("""
            QPushButton {
                background-color: #22D3EE; color: #0C1014;
                border-radius: 6px; font-weight: bold;
                min-width: 90px; min-height: 34px;
            }
            QPushButton:hover {
                background-color: #67E8F9;
            }
        """)
        self._btn_open.clicked.connect(self._on_open_directory)
        layout.addWidget(self._btn_open)

        layout.addStretch()

        # 标题
        title = QLabel("Plot Dashboard")
        title.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #E6EDF3;"
        )
        layout.addWidget(title)
        layout.addStretch()

        # 占位 — QFactor
        self._btn_qfactor = QPushButton("Q Factor (soon)")
        self._btn_qfactor.setEnabled(False)
        self._btn_qfactor.setStyleSheet("""
            QPushButton {
                background-color: #34495e; color: #8B949E;
                border-radius: 6px; font-size: 11px;
                min-width: 100px; min-height: 28px;
            }
        """)
        layout.addWidget(self._btn_qfactor)

        return layout

    # ------------------------------------------------------------------
    # 信号连线
    # ------------------------------------------------------------------

    def _wire_signals(self):
        # FilterPanel → Controller
        self._filter_panel.filters_changed.connect(
            self._on_filters_changed
        )

        # Controller → PlotCanvas
        self._controller.traces_updated.connect(
            self._on_traces_updated
        )

        # Controller → StatusBar
        self._controller.status_message.connect(
            self._status_bar.showMessage
        )

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------

    def _on_open_directory(self):
        """打开目录选择对话框并触发扫描。"""
        # 默认从 experiment_data 目录开始
        import os
        default_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "experiment_data",
        )
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~")

        path = QFileDialog.getExistingDirectory(
            self,
            "Select Experiment Directory",
            default_dir,
        )
        if path:
            self._controller.scan_directory(path)

    def _on_filters_changed(self, filters: dict):
        """当 FilterPanel 状态变化时，重新筛选并绘图。"""
        self._controller.apply_filters(filters)

    def _on_traces_updated(self, traces: list):
        """当筛选后的 trace 列表更新时，触发画布重绘。"""
        if not traces:
            self._plot_canvas.clear()
            self._status_bar.showMessage("No traces match current filters.")
            return

        filters = self._filter_panel.get_filters()
        view_mode = filters.get("view_mode", ViewMode.OVERLAY)
        pv_list = sorted(set(t.vna_power_dbm for t in traces))
        pl_list = sorted(set(t.laser_power_mw for t in traces))

        all_temps = [t.temp_k for t in traces]
        temp_min = min(all_temps)
        temp_max = max(all_temps)

        if view_mode == ViewMode.GRID:
            self._plot_canvas.plot_grid(traces, pv_list, pl_list)
        else:
            self._plot_canvas.plot_overlay(traces, temp_min, temp_max)

        n_pv = len(pv_list)
        n_pl = len(pl_list)
        self._status_bar.showMessage(
            f"Showing {len(traces)} traces "
            f"({n_pv} Pv × {n_pl} Pl, "
            f"Tr {temp_min:.1f}–{temp_max:.1f} K)"
        )
