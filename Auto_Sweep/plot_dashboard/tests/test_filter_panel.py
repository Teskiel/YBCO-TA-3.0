# -*- coding: utf-8 -*-
"""FilterPanel 的集成测试。

测试 Pv/Pl 开关按钮的创建、切换行为和信号发射。
使用 QApplication fixture。
"""

import pytest
from PyQt5.QtCore import Qt


# Pv 和 Pl 的完整可选值（硬编码，来自 GUI 定义）
PV_VALUES = list(range(-50, -5, 5))   # 9 个值
PL_VALUES = list(range(18))            # 18 个值


class TestFilterPanelInitialState:
    """面板初始化状态的测试。"""

    @pytest.fixture
    def panel(self, qapp):
        from plot_dashboard.filter_panel import FilterPanel
        return FilterPanel()

    def test_given_unpopulated_panel_when_created_then_pv_buttons_exist(self, panel):
        assert len(panel._pv_buttons) == 9

    def test_given_unpopulated_panel_when_created_then_pl_buttons_exist(self, panel):
        assert len(panel._pl_buttons) == 18

    def test_given_unpopulated_panel_when_created_then_no_pv_selected(self, panel):
        assert panel.get_selected_pv() == []

    def test_given_unpopulated_panel_when_created_then_no_pl_selected(self, panel):
        assert panel.get_selected_pl() == []

    def test_given_unpopulated_panel_when_created_then_tr_spinboxes_exist(self, panel):
        assert panel._tr_start.value() >= 4.0
        assert panel._tr_end.value() <= 100.0

    def test_given_panel_when_created_then_default_mode_is_overlay(self, panel):
        from plot_dashboard.filter_panel import ViewMode
        assert panel.get_view_mode() == ViewMode.OVERLAY

    def test_given_pv_button_when_created_then_shows_correct_label(self, panel):
        labels = [btn.text() for btn in panel._pv_buttons.values()]
        for dbm in PV_VALUES:
            assert f"{dbm:+d}" in labels or str(dbm) in labels


class TestFilterPanelInteraction:
    """按钮交互与信号发射测试。"""

    @pytest.fixture
    def panel(self, qapp):
        from plot_dashboard.filter_panel import FilterPanel
        p = FilterPanel()
        return p

    def test_given_pv_button_when_clicked_then_becomes_selected(self, panel):
        btn = panel._pv_buttons[-25]
        btn.click()
        assert -25 in panel.get_selected_pv()

    def test_given_selected_pv_when_clicked_again_then_deselected(self, panel):
        btn = panel._pv_buttons[-25]
        btn.click()
        btn.click()
        assert -25 not in panel.get_selected_pv()

    def test_given_pl_button_when_clicked_then_becomes_selected(self, panel):
        btn = panel._pl_buttons[5]
        btn.click()
        assert 5 in panel.get_selected_pl()

    def test_given_selected_pl_when_clicked_again_then_deselected(self, panel):
        btn = panel._pl_buttons[5]
        btn.click()
        btn.click()
        assert 5 not in panel.get_selected_pl()

    def test_given_pv_toggle_when_changing_then_filters_changed_emitted(self, panel):
        from PyQt5.QtTest import QSignalSpy
        spy = QSignalSpy(panel.filters_changed)
        panel._pv_buttons[-25].click()
        assert len(spy) == 1

    def test_given_pl_toggle_when_changing_then_filters_changed_emitted(self, panel):
        from PyQt5.QtTest import QSignalSpy
        spy = QSignalSpy(panel.filters_changed)
        panel._pl_buttons[5].click()
        assert len(spy) == 1

    def test_given_tr_spinbox_when_changing_then_filters_changed_emitted(self, panel):
        from PyQt5.QtTest import QSignalSpy
        spy = QSignalSpy(panel.filters_changed)
        panel._tr_start.setValue(10.0)
        assert len(spy) == 1

    def test_given_multiple_pv_when_selected_then_all_in_list(self, panel):
        for dbm in [-50, -25, -10]:
            panel._pv_buttons[dbm].click()
        assert set(panel.get_selected_pv()) == {-50, -25, -10}

    def test_given_multiple_pl_when_selected_then_all_in_list(self, panel):
        for mw in [0, 8, 17]:
            panel._pl_buttons[mw].click()
        assert set(panel.get_selected_pl()) == {0, 8, 17}

    def test_given_tr_range_when_get_filters_then_bounds_correct(self, panel):
        panel._tr_start.setValue(6.0)
        panel._tr_end.setValue(20.0)
        filters = panel.get_filters()
        assert filters["temp_min_k"] == pytest.approx(6.0)
        assert filters["temp_max_k"] == pytest.approx(20.0)

    def test_given_view_mode_button_when_clicked_then_mode_toggles(self, panel):
        from plot_dashboard.filter_panel import ViewMode
        assert panel.get_view_mode() == ViewMode.OVERLAY
        panel._btn_grid.click()
        assert panel.get_view_mode() == ViewMode.GRID
        panel._btn_overlay.click()
        assert panel.get_view_mode() == ViewMode.OVERLAY


class TestFilterPanelSelection:
    """批量选择/清除功能测试。"""

    @pytest.fixture
    def panel(self, qapp):
        from plot_dashboard.filter_panel import FilterPanel
        return FilterPanel()

    def test_given_some_selected_when_select_all_pv_then_all_nine_selected(self, panel):
        panel.select_all_pv()
        assert len(panel.get_selected_pv()) == 9

    def test_given_all_selected_when_clear_pv_then_none_selected(self, panel):
        panel.select_all_pv()
        panel.clear_pv()
        assert panel.get_selected_pv() == []

    def test_given_some_selected_when_select_all_pl_then_all_18_selected(self, panel):
        panel.select_all_pl()
        assert len(panel.get_selected_pl()) == 18

    def test_given_all_selected_when_clear_pl_then_none_selected(self, panel):
        panel.select_all_pl()
        panel.clear_pl()
        assert panel.get_selected_pl() == []

    def test_given_select_all_both_when_get_filters_then_full_ranges(self, panel):
        panel.select_all_pv()
        panel.select_all_pl()
        f = panel.get_filters()
        assert set(f["vna_powers"]) == set(PV_VALUES)
        assert set(f["laser_powers"]) == set(PL_VALUES)
