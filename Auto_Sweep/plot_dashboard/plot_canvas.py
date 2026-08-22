# -*- coding: utf-8 -*-
"""高 DPI 绘图画布。

嵌入 PyQt5 的 matplotlib FigureCanvas，提供：
  - dpi=200 初始渲染精度，缩放时 AGG 矢量重绘
  - Overlay 模式：单图多条 Tr 曲线叠加，jet 色标
  - Grid 模式：Pv(行) × Pl(列) 子图矩阵
  - Deep Space Cyan 暗色主题
"""

from typing import List

import matplotlib
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from plot_dashboard.data_model import S21Trace

# =========================================================================
# 暗色主题样式常量
# =========================================================================

BG_FIG = "#0C1014"
BG_AXES = "#161B22"
FG_TEXT = "#E6EDF3"
FG_MUTED = "#8B949E"
GRID_COLOR = "#30363D"
ACCENT = "#22D3EE"


# =========================================================================
# PlotCanvas
# =========================================================================


class PlotCanvas(FigureCanvasQTAgg):
    """高 DPI matplotlib 画布，嵌入 PyQt5。

    缩放时 AGG 后端自动以当前视口分辨率重新栅格化矢量数据，
    确保放大后谐振谷底和 3dB 带宽细节清晰无锯齿。
    """

    def __init__(self, parent=None, dpi=200):
        self.fig = Figure(
            figsize=(12, 8),
            dpi=dpi,
            facecolor=BG_FIG,
            constrained_layout=True,
        )
        super().__init__(self.fig)
        self.setParent(parent)
        self._mode = "overlay"
        self._init_style()

    # ------------------------------------------------------------------
    # 样式初始化
    # ------------------------------------------------------------------

    def _init_style(self):
        """设置全局 matplotlib 样式，与 Qt 暗色主题一致。"""
        self.fig.patch.set_facecolor(BG_FIG)

        # 创建默认 axes 并应用样式
        ax = self.fig.add_subplot(111)
        self._style_axes(ax)

    def _style_axes(self, ax):
        """对单个 Axes 应用暗色主题。"""
        ax.set_facecolor(BG_AXES)
        ax.tick_params(colors=FG_TEXT, labelsize=9)
        ax.xaxis.label.set_color(FG_TEXT)
        ax.yaxis.label.set_color(FG_TEXT)
        ax.title.set_color(FG_TEXT)
        ax.spines["bottom"].set_color(GRID_COLOR)
        ax.spines["top"].set_color(GRID_COLOR)
        ax.spines["left"].set_color(GRID_COLOR)
        ax.spines["right"].set_color(GRID_COLOR)
        ax.grid(True, color=GRID_COLOR, linewidth=0.5, alpha=0.6)

    # ------------------------------------------------------------------
    # Overlay 模式
    # ------------------------------------------------------------------

    def plot_overlay(
        self,
        traces: List[S21Trace],
        temp_min: float,
        temp_max: float,
    ):
        """单图模式：所有 trace 叠加在同一 Axes，按 Tr 着色。

        Args:
            traces: 要绘制的 S21Trace 列表。
            temp_min: 色标下限 (K)。
            temp_max: 色标上限 (K)。
        """
        # 清空并重建 axes
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        self._style_axes(ax)

        if not traces:
            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel("|S21| (dB)")
            ax.set_title("No data — select filters and scan a directory")
            self.draw_idle()
            return

        temp_range = max(temp_max - temp_min, 0.01)
        norm = matplotlib.colors.Normalize(vmin=temp_min, vmax=temp_max)
        cmap = matplotlib.cm.jet

        for trace in traces:
            freq_ghz = trace.frequency_hz / 1e9
            color = cmap(norm(trace.temp_k))
            ax.plot(
                freq_ghz,
                trace.s21_db,
                color=color,
                linewidth=1.5,
                antialiased=True,
                alpha=0.85,
            )

        ax.set_xlabel("Frequency (GHz)")
        ax.set_ylabel("|S21| (dB)")
        ax.set_title(f"S21 Overlay — {len(traces)} traces")

        # 色标
        sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
        cbar = self.fig.colorbar(sm, ax=ax)
        cbar.set_label("Temperature (K)", color=FG_TEXT)
        cbar.ax.tick_params(colors=FG_TEXT)
        cbar.outline.set_edgecolor(GRID_COLOR)

        self.draw_idle()

    # ------------------------------------------------------------------
    # Grid 模式
    # ------------------------------------------------------------------

    def plot_grid(
        self,
        traces: List[S21Trace],
        vna_powers: List[int],
        laser_powers: List[int],
    ):
        """子图矩阵：行=Pv 值，列=Pl 值。

        每个格子叠加该 (Pv, Pl) 组合下的所有 Tr 曲线。

        Args:
            traces: 全部待显示 trace。
            vna_powers: 选中的 Pv 值列表（按升序排列）。
            laser_powers: 选中的 Pl 值列表（按升序排列）。
        """
        n_rows = max(len(vna_powers), 1)
        n_cols = max(len(laser_powers), 1)

        self.fig.clear()

        if not traces:
            ax = self.fig.add_subplot(111)
            self._style_axes(ax)
            ax.set_title("No data")
            self.draw_idle()
            return

        # 全局温度范围用于统一色标
        all_temps = [t.temp_k for t in traces]
        temp_min = min(all_temps) if all_temps else 4.0
        temp_max = max(all_temps) if all_temps else 100.0
        temp_range = max(temp_max - temp_min, 0.01)
        norm = matplotlib.colors.Normalize(vmin=temp_min, vmax=temp_max)
        cmap = matplotlib.cm.jet

        for row_idx, pv in enumerate(sorted(vna_powers)):
            for col_idx, pl in enumerate(sorted(laser_powers)):
                ax_idx = row_idx * n_cols + col_idx + 1
                ax = self.fig.add_subplot(n_rows, n_cols, ax_idx)
                self._style_axes(ax)

                # 筛选该格子的数据
                cell_traces = [
                    t for t in traces
                    if t.vna_power_dbm == pv and t.laser_power_mw == pl
                ]

                for trace in cell_traces:
                    freq_ghz = trace.frequency_hz / 1e9
                    color = cmap(norm(trace.temp_k))
                    ax.plot(
                        freq_ghz,
                        trace.s21_db,
                        color=color,
                        linewidth=1.2,
                        antialiased=True,
                        alpha=0.85,
                    )

                ax.set_title(
                    f"Pv={pv:+d} Pl={pl}mW",
                    fontsize=8,
                    color=FG_MUTED,
                )

                # 只在外围子图显示标签
                if row_idx == n_rows - 1:
                    ax.set_xlabel("GHz", fontsize=7, color=FG_MUTED)
                if col_idx == 0:
                    ax.set_ylabel("|S21| (dB)", fontsize=7, color=FG_MUTED)

        # 全局色标
        sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
        cbar = self.fig.colorbar(
            sm,
            ax=self.fig.axes,
            label="Temperature (K)",
            pad=0.02,
        )
        cbar.set_label("Temperature (K)", color=FG_TEXT)
        cbar.ax.tick_params(colors=FG_TEXT)
        cbar.outline.set_edgecolor(GRID_COLOR)

        self.fig.suptitle(
            f"S21 Grid — {len(traces)} traces",
            color=FG_TEXT,
            fontsize=13,
            fontweight="bold",
        )

        self.draw_idle()

    # ------------------------------------------------------------------
    # 模式切换
    # ------------------------------------------------------------------

    @property
    def current_mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str):
        """设置绘图模式: "overlay" 或 "grid"."""
        if mode in ("overlay", "grid"):
            self._mode = mode

    # ------------------------------------------------------------------
    # 清除
    # ------------------------------------------------------------------

    def clear(self):
        """清空画布。"""
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        self._style_axes(ax)
        self.draw_idle()
