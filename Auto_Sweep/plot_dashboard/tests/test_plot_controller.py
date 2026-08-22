# -*- coding: utf-8 -*-
"""PlotController 集成测试。

验证 scan→filter→plot 数据管道。
"""

import numpy as np
import pytest

from plot_dashboard.tests.conftest import make_synthetic_trace, make_trace_set


class TestPlotControllerScan:
    """扫描逻辑测试。"""

    @pytest.fixture
    def controller(self, qapp):
        from plot_dashboard.plot_controller import PlotController
        return PlotController()

    def test_given_controller_when_created_then_no_traces(self, controller):
        assert controller.trace_count == 0

    def test_given_traces_when_set_directly_then_count_matches(self, controller):
        traces = make_trace_set(temp_list=[(6, 6.5), (8, 8.5)])
        controller.set_traces(traces)
        assert controller.trace_count == len(traces)

    def test_given_traces_when_set_then_summary_updated(self, controller, qapp):
        from PyQt5.QtTest import QSignalSpy
        spy = QSignalSpy(controller.summary_updated)
        traces = make_trace_set(temp_list=[(6, 6.5)])
        controller.set_traces(traces)
        assert len(spy) == 1

    def test_given_traces_when_set_then_apply_filters_called(self, controller, qapp):
        from PyQt5.QtTest import QSignalSpy
        spy = QSignalSpy(controller.traces_updated)
        traces = make_trace_set(temp_list=[(6, 6.5)])
        controller.set_traces(traces)
        # 设置数据后自动应用当前筛选
        assert len(spy) >= 1


class TestPlotControllerFilter:
    """筛选逻辑测试。"""

    @pytest.fixture
    def controller(self, qapp):
        from plot_dashboard.plot_controller import PlotController
        c = PlotController()
        c.set_traces(make_trace_set(
            temp_list=[(6, 6.5), (8, 8.5), (10, 10.5)],
            pv_list=[-25, -35, -45],
            pl_list=[0, 1, 3, 5, 7, 9],
        ))
        return c

    def test_given_all_data_when_filter_by_single_pv_then_only_matching_returned(
        self, controller
    ):
        filters = {
            "vna_powers": [-25],
            "laser_powers": [0, 1, 3, 5, 7, 9],
            "temp_min_k": 4.0,
            "temp_max_k": 100.0,
            "view_mode": None,
        }
        result = controller.apply_filters(filters)
        # 3 temp × 1 pv × 6 pl = 18 traces
        assert len(result) == 18
        assert all(t.vna_power_dbm == -25 for t in result)

    def test_given_all_data_when_filter_by_tr_range_then_only_in_range_returned(
        self, controller
    ):
        filters = {
            "vna_powers": [-25, -35, -45],
            "laser_powers": [0, 1, 3, 5, 7, 9],
            "temp_min_k": 7.0,
            "temp_max_k": 9.0,
            "view_mode": None,
        }
        result = controller.apply_filters(filters)
        # Only Tr=8K (actual 8.5K) in range
        assert len(result) > 0
        for t in result:
            assert 7.0 <= t.temp_k <= 9.0

    def test_given_all_data_when_filter_by_pv_and_pl_then_intersection_correct(
        self, controller
    ):
        filters = {
            "vna_powers": [-25],
            "laser_powers": [3, 5],
            "temp_min_k": 4.0,
            "temp_max_k": 100.0,
            "view_mode": None,
        }
        result = controller.apply_filters(filters)
        # 3 temp × 1 pv × 2 pl = 6
        assert len(result) == 6
        for t in result:
            assert t.vna_power_dbm == -25
            assert t.laser_power_mw in (3, 5)

    def test_given_restrictive_filter_when_no_match_then_empty_list(self, controller):
        filters = {
            "vna_powers": [-10],  # not in data
            "laser_powers": [0],
            "temp_min_k": 4.0,
            "temp_max_k": 100.0,
            "view_mode": None,
        }
        result = controller.apply_filters(filters)
        assert result == []

    def test_given_filter_change_when_applied_then_traces_updated_emitted(
        self, controller, qapp
    ):
        from PyQt5.QtTest import QSignalSpy
        spy = QSignalSpy(controller.traces_updated)
        filters = {
            "vna_powers": [-25],
            "laser_powers": [0],
            "temp_min_k": 4.0,
            "temp_max_k": 100.0,
            "view_mode": None,
        }
        controller.apply_filters(filters)
        assert len(spy) == 1


class TestPlotControllerSummary:
    """ScanSummary 准确性测试。"""

    @pytest.fixture
    def controller(self, qapp):
        from plot_dashboard.plot_controller import PlotController
        return PlotController()

    def test_given_mixed_data_when_set_then_summary_has_correct_stats(
        self, controller
    ):
        traces = make_trace_set(
            temp_list=[(6, 6.5), (10, 10.5)],
            pv_list=[-25, -35],
            pl_list=[0, 5],
        )
        controller.set_traces(traces)
        summary = controller.summary

        assert summary.total_files == 8  # 2×2×2
        assert summary.vna_powers == {-25, -35}
        assert summary.laser_powers == {0, 5}
        assert summary.temp_min_k == pytest.approx(6.5)
        assert summary.temp_max_k == pytest.approx(10.5)
