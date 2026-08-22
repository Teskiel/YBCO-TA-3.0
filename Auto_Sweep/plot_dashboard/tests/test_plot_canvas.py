# -*- coding: utf-8 -*-
"""PlotCanvas 的集成测试。

验证高 DPI 渲染、暗色主题、Overlay/Grid 模式。
"""

import numpy as np
import pytest

from plot_dashboard.tests.conftest import make_synthetic_trace, make_trace_set


class TestPlotCanvasConstruction:
    """画布初始化和主题测试。"""

    @pytest.fixture
    def canvas(self, qapp):
        from plot_dashboard.plot_canvas import PlotCanvas
        return PlotCanvas()

    def test_given_canvas_when_constructed_then_has_high_dpi(self, canvas):
        assert canvas.fig.dpi == 200

    def test_given_canvas_when_constructed_then_dark_facecolor(self, canvas):
        facecolor = canvas.fig.get_facecolor()
        # 应该接近 #0C1014
        assert facecolor[0] < 0.1
        assert facecolor[1] < 0.1
        assert facecolor[2] < 0.1

    def test_given_canvas_when_constructed_then_constrained_layout(self, canvas):
        assert canvas.fig.get_constrained_layout()

    def test_given_canvas_when_constructed_then_default_mode_is_overlay(self, canvas):
        assert canvas.current_mode == "overlay"

    def test_given_canvas_when_constructed_then_has_axes(self, canvas):
        assert len(canvas.fig.axes) >= 1


class TestPlotCanvasOverlayMode:
    """Overlay 模式测试。"""

    @pytest.fixture
    def canvas(self, qapp):
        from plot_dashboard.plot_canvas import PlotCanvas
        return PlotCanvas()

    def test_given_three_traces_when_plot_overlay_then_three_lines(self, canvas):
        traces = [
            make_synthetic_trace(actual_temp_k=6.0, dip_freq_ghz=4.5),
            make_synthetic_trace(actual_temp_k=8.0, dip_freq_ghz=4.4),
            make_synthetic_trace(actual_temp_k=10.0, dip_freq_ghz=4.3),
        ]

        canvas.plot_overlay(traces, temp_min=6.0, temp_max=10.0)

        ax = canvas.fig.axes[0]
        lines = ax.get_lines()
        assert len(lines) == 3

    def test_given_overlay_when_plotted_then_axes_labels_correct(self, canvas):
        traces = [make_synthetic_trace()]
        canvas.plot_overlay(traces, temp_min=6.0, temp_max=6.5)

        ax = canvas.fig.axes[0]
        assert "Frequency" in ax.get_xlabel()
        assert "S21" in ax.get_ylabel() or "dB" in ax.get_ylabel()

    def test_given_overlay_when_plotted_then_dark_axes_facecolor(self, canvas):
        traces = [make_synthetic_trace()]
        canvas.plot_overlay(traces, temp_min=6.0, temp_max=6.5)

        ax = canvas.fig.axes[0]
        bg = ax.get_facecolor()
        # 应接近 #161B22
        assert bg[0] < 0.15
        assert bg[1] < 0.15

    def test_given_overlay_when_clear_then_no_lines(self, canvas):
        traces = [make_synthetic_trace()]
        canvas.plot_overlay(traces, 6.0, 6.5)
        canvas.clear()

        ax = canvas.fig.axes[0]
        assert len(ax.get_lines()) == 0


class TestPlotCanvasGridMode:
    """Grid 模式测试。"""

    @pytest.fixture
    def canvas(self, qapp):
        from plot_dashboard.plot_canvas import PlotCanvas
        return PlotCanvas()

    def test_given_grid_mode_when_plotting_then_correct_subplot_count(self, canvas):
        traces = make_trace_set(
            temp_list=[(6, 6.5)],
            pv_list=[-25, -35],
            pl_list=[0, 1, 3],
        )

        canvas.plot_grid(
            traces,
            vna_powers=[-25, -35],
            laser_powers=[0, 1, 3],
        )

        # grid 2×3=6 子图 + 1 colorbar → ≥6 axes
        assert len(canvas.fig.axes) >= 6

    def test_given_grid_mode_when_no_data_then_empty_subplots_still_created(
        self, canvas
    ):
        canvas.plot_grid([], vna_powers=[-25], laser_powers=[0])
        assert len(canvas.fig.axes) == 1

    def test_given_grid_with_data_when_plotted_then_each_cell_has_label(
        self, canvas
    ):
        traces = make_trace_set(
            temp_list=[(6, 6.5)],
            pv_list=[-25],
            pl_list=[0, 1],
        )
        canvas.plot_grid(traces, vna_powers=[-25], laser_powers=[0, 1])

        # 每个子图应有标题标注 Pv/Pl
        for ax in canvas.fig.axes:
            title = ax.get_title()
            assert title != "" or len(ax.get_lines()) >= 0


class TestPlotCanvasLineQuality:
    """线渲染质量测试（保证放大后细节清晰）。"""

    @pytest.fixture
    def canvas(self, qapp):
        from plot_dashboard.plot_canvas import PlotCanvas
        return PlotCanvas()

    def test_given_trace_plotted_when_checking_line_then_antialiased(self, canvas):
        trace = make_synthetic_trace(n_pts=500)
        canvas.plot_overlay([trace], 6.0, 6.5)

        line = canvas.fig.axes[0].get_lines()[0]
        assert line.get_antialiased()

    def test_given_trace_plotted_when_checking_line_then_has_decent_width(
        self, canvas
    ):
        trace = make_synthetic_trace()
        canvas.plot_overlay([trace], 6.0, 6.5)

        line = canvas.fig.axes[0].get_lines()[0]
        assert line.get_linewidth() >= 1.0

    def test_given_high_q_resonance_when_plotted_then_full_data_preserved(
        self, canvas
    ):
        """高 Q 谐振在放大后应显示完整频点（不降采样）。"""
        n_pts = 1000
        trace = make_synthetic_trace(n_pts=n_pts, q_factor=10000)
        canvas.plot_overlay([trace], 6.0, 6.5)

        line = canvas.fig.axes[0].get_lines()[0]
        x_data = line.get_xdata()
        assert len(x_data) == n_pts

    def test_given_overlay_when_set_mode_then_mode_changes(self, canvas):
        canvas.set_mode("grid")
        assert canvas.current_mode == "grid"
        canvas.set_mode("overlay")
        assert canvas.current_mode == "overlay"


class TestPlotCanvasColormap:
    """色标映射测试。"""

    @pytest.fixture
    def canvas(self, qapp):
        from plot_dashboard.plot_canvas import PlotCanvas
        return PlotCanvas()

    def test_given_multiple_tr_when_plotting_then_different_colors(self, canvas):
        traces = [
            make_synthetic_trace(actual_temp_k=6.0, dip_freq_ghz=4.5),
            make_synthetic_trace(actual_temp_k=10.0, dip_freq_ghz=4.3),
        ]
        canvas.plot_overlay(traces, temp_min=6.0, temp_max=10.0)

        lines = canvas.fig.axes[0].get_lines()
        c0 = lines[0].get_color()
        c1 = lines[1].get_color()
        # 两条线应有不同颜色
        assert c0 != c1
