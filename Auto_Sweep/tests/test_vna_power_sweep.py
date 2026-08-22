# -*- coding: utf-8 -*-
"""
BDD 测试 — VNA 功率区间扫描 Widget + 并行合并逻辑

验证新增的 VnaPowerRangeWidget（起止/间隔式功率设置）与现有
VnaPowerGrid（按钮点选式）的并行工作：

  - VnaPowerRangeWidget 区间生成逻辑
  - 两套系统 dBm 值并集去重合并
  - VNAPage.get_all_settings() / set_all_settings() 集成

命名规范: test_given_<前置条件>_when_<动作>_then_<预期结果>
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication once per test module."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# =========================================================================
# TestClass: VnaPowerRangeWidget 基本行为
# =========================================================================

class TestVnaPowerRangeWidget:
    """验证 VnaPowerRangeWidget 的区间生成和设置读写。"""

    @pytest.fixture
    def widget(self, qapp):
        """Given a freshly-constructed VnaPowerRangeWidget."""
        from ui.vna_page import VnaPowerRangeWidget
        w = VnaPowerRangeWidget()
        yield w

    def test_given_range_minus55_to_minus45_step2_when_get_powers_then_6_values(
        self, widget
    ):
        """-55 到 -45 dBm，步长 2 → 6 个值：[-55, -53, -51, -49, -47, -45]。"""
        powers = widget.get_powers()
        assert len(powers) == 6
        assert powers == [-55, -53, -51, -49, -47, -45]

    def test_given_range_minus50_to_minus40_step5_when_get_powers_then_3_values(
        self, widget
    ):
        """-50 到 -40 dBm，步长 5 → [-50, -45, -40]。"""
        widget._start_spin.setValue(-50)
        widget._stop_spin.setValue(-40)
        widget._step_spin.setValue(5)
        powers = widget.get_powers()
        assert len(powers) == 3
        assert powers == [-50, -45, -40]

    def test_given_start_greater_than_stop_when_get_powers_then_empty(
        self, widget
    ):
        """start > stop → 空列表。"""
        widget._start_spin.setValue(-30)
        widget._stop_spin.setValue(-50)
        powers = widget.get_powers()
        assert powers == []

    def test_given_disabled_checkbox_when_get_powers_then_empty(
        self, widget
    ):
        """Enable 取消勾选 → 返回空列表（仅使用按钮选择值）。"""
        widget._enabled_check.setChecked(False)
        powers = widget.get_powers()
        assert powers == []

    def test_given_range_when_get_settings_then_dict_has_all_fields(
        self, widget
    ):
        """get_settings() 返回 {enabled, start_dbm, stop_dbm, step_db}。"""
        s = widget.get_settings()
        assert s["enabled"] is True
        assert s["start_dbm"] == -55
        assert s["stop_dbm"] == -45
        assert s["step_db"] == 2

    def test_given_settings_dict_when_set_settings_then_widget_restored(
        self, widget
    ):
        """set_settings() 恢复所有控件状态。"""
        widget.set_settings({
            "enabled": False,
            "start_dbm": -20,
            "stop_dbm": -10,
            "step_db": 5,
        })
        assert widget._enabled_check.isChecked() is False
        assert widget._start_spin.value() == -20
        assert widget._stop_spin.value() == -10
        assert widget._step_spin.value() == 5
        assert widget.get_powers() == []

    def test_given_default_values_when_created_then_from_config(
        self, widget
    ):
        """默认值应从 config.py 读取。"""
        import config
        assert widget._start_spin.value() == config.vna_power_range_default_start_dbm
        assert widget._stop_spin.value() == config.vna_power_range_default_stop_dbm
        assert widget._step_spin.value() == config.vna_power_range_default_step_db


# =========================================================================
# TestClass: 功率合并逻辑（纯数据测试）
# =========================================================================

class TestVnaPowerMerge:
    """验证按钮点选值与区间生成值的并集去重合并逻辑。"""

    @staticmethod
    def _merge(button_vals, range_vals):
        """模拟 VNAPage.get_all_settings() 的合并逻辑。"""
        return sorted(set(button_vals + range_vals))

    def test_given_button_vals_and_range_vals_when_merging_then_union_sorted_deduped(
        self
    ):
        """按钮值 + 区间值 → 并集、升序、无重复。"""
        button = [-45, -35, -25]
        range_vals = [-55, -53, -51, -49, -47, -45]
        merged = self._merge(button, range_vals)
        assert merged == [-55, -53, -51, -49, -47, -45, -35, -25]
        # -45 出现在两套中 → 不应重复
        assert merged.count(-45) == 1

    def test_given_button_only_when_range_empty_then_equals_button(
        self
    ):
        """区间为空 → 仅返回按钮值。"""
        button = [-45, -35, -25]
        merged = self._merge(button, [])
        assert merged == button

    def test_given_range_only_when_button_empty_then_equals_range(
        self
    ):
        """按钮为空 → 仅返回区间值。"""
        range_vals = [-55, -53, -51, -49, -47, -45]
        merged = self._merge([], range_vals)
        assert merged == range_vals

    def test_given_overlapping_values_when_merging_then_no_duplicates(
        self
    ):
        """两套系统有大量重叠值 → 每个值只出现一次。"""
        button = [-50, -45, -40, -35, -30]
        range_vals = [-50, -45, -40]
        merged = self._merge(button, range_vals)
        assert merged == [-50, -45, -40, -35, -30]
        assert len(merged) == len(set(merged))

    def test_given_minus55_to_minus45_step2_then_contains_all_6_values(
        self
    ):
        """验证目标区间所有值都在合并结果中。"""
        button = [-45]
        range_vals = [-55, -53, -51, -49, -47, -45]
        merged = self._merge(button, range_vals)
        for v in range_vals:
            assert v in merged


# =========================================================================
# TestClass: VNAPage 集成测试
# =========================================================================

class TestVnaPowerSweepIntegration:
    """验证 VNAPage.get_all_settings() / set_all_settings() 对两套系统的集成。"""

    @pytest.fixture
    def vna_page(self, qapp):
        """Given a freshly-constructed VNAPage."""
        from ui.vna_page import VNAPage
        page = VNAPage()
        yield page

    def test_given_page_when_get_all_settings_then_power_dbm_is_merged(
        self, vna_page
    ):
        """get_all_settings() 的 power_dbm 应为按钮值 + 区间值的并集。"""
        # 清除按钮选择
        vna_page._power_grid.clear_all()
        # 设置按钮值为 [-45, -35]
        vna_page._power_grid.set_selection([-45, -35])
        # 设置区间范围为 -55 到 -45，步长 2
        vna_page._power_range.set_settings({
            "enabled": True,
            "start_dbm": -55,
            "stop_dbm": -45,
            "step_db": 2,
        })

        settings = vna_page.get_all_settings()
        power_dbm = settings["power_dbm"]

        # 合并结果：区间 [-55,-53,-51,-49,-47,-45] + 按钮 [-45,-35]
        assert -55 in power_dbm
        assert -35 in power_dbm
        # -45 重叠，不应重复
        assert power_dbm.count(-45) == 1
        # 升序
        assert power_dbm == sorted(power_dbm)

    def test_given_page_when_set_all_settings_then_range_widget_restored(
        self, vna_page
    ):
        """set_all_settings() 应恢复 power_range_settings 和 power_dbm_button。"""
        vna_page.set_all_settings({
            "power_dbm_button": [-45, -35],
            "power_range_settings": {
                "enabled": False,
                "start_dbm": -30,
                "stop_dbm": -10,
                "step_db": 5,
            },
        })

        assert vna_page._power_grid.get_selection() == [-45, -35]
        assert vna_page._power_range._enabled_check.isChecked() is False
        assert vna_page._power_range._start_spin.value() == -30
        assert vna_page._power_range._stop_spin.value() == -10

    def test_given_default_page_when_get_all_settings_then_power_range_settings_included(
        self, vna_page
    ):
        """默认 VNAPage 的 get_all_settings() 应包含 power_range_settings 字段。"""
        settings = vna_page.get_all_settings()
        assert "power_range_settings" in settings
        assert "power_dbm_button" in settings
        rs = settings["power_range_settings"]
        assert rs["enabled"] is True
        import config
        assert rs["start_dbm"] == config.vna_power_range_default_start_dbm
