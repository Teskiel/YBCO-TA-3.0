# -*- coding: utf-8 -*-
"""绘图控制器 — 协调 scan → filter → plot 数据流。

非可视 QObject，持有全部 S21Trace 数据，响应 FilterPanel
的筛选信号并推送到 PlotCanvas 重绘。
"""

from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from plot_dashboard.data_model import S21Trace, ScanSummary
from plot_dashboard.data_scanner import scan_experiment_dir


class PlotController(QObject):
    """扫描、筛选、分发的中心协调器。

    信号:
        traces_updated(list)   → PlotCanvas 重绘
        summary_updated(dict)  → FilterPanel 更新范围
        status_message(str)    → 状态栏消息
    """

    traces_updated = pyqtSignal(list)
    summary_updated = pyqtSignal(object)   # ScanSummary
    status_message = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._all_traces: List[S21Trace] = []
        self._summary = ScanSummary()

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def trace_count(self) -> int:
        return len(self._all_traces)

    @property
    def summary(self) -> ScanSummary:
        return self._summary

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def scan_directory(self, root_dir: str):
        """扫描目录，加载所有 S2P 文件。

        Args:
            root_dir: 实验数据目录的绝对路径。
        """
        self.status_message.emit(f"Scanning {root_dir} ...")
        try:
            traces = scan_experiment_dir(root_dir)
        except Exception as e:
            self.status_message.emit(f"Scan failed: {e}")
            return

        if not traces:
            self.status_message.emit("No .s2p files found in selected directory.")
            return

        self.set_traces(traces)
        self.status_message.emit(
            f"Loaded {len(traces)} traces from {root_dir}"
        )

    def set_traces(self, traces: List[S21Trace]):
        """直接设置 traces（用于测试或非目录来源）。"""
        self._all_traces = traces

        # 重建统计摘要
        self._summary = ScanSummary()
        for t in traces:
            self._summary.add(t)

        self.summary_updated.emit(self._summary)

        # 默认全选
        self.apply_filters({
            "vna_powers": sorted(self._summary.vna_powers),
            "laser_powers": sorted(self._summary.laser_powers),
            "temp_min_k": self._summary.temp_min_k or 4.0,
            "temp_max_k": self._summary.temp_max_k or 100.0,
            "view_mode": None,
        })

    # ------------------------------------------------------------------
    # 筛选
    # ------------------------------------------------------------------

    def apply_filters(self, filters: dict) -> List[S21Trace]:
        """根据筛选条件过滤 trace 列表。

        Args:
            filters: FilterPanel.get_filters() 返回的 dict。

        Returns:
            匹配的 S21Trace 列表。
        """
        pv_list = filters.get("vna_powers", [])
        pl_list = filters.get("laser_powers", [])
        tr_min = filters.get("temp_min_k", 4.0)
        tr_max = filters.get("temp_max_k", 300.0)

        result = [
            t for t in self._all_traces
            if t.vna_power_dbm in pv_list
            and t.laser_power_mw in pl_list
            and tr_min <= t.temp_k <= tr_max
        ]

        self.traces_updated.emit(result)
        return result
