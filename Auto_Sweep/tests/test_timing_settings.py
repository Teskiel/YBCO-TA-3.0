# -*- coding: utf-8 -*-
"""
BDD tests for Dashboard Timing Settings UI.

Naming convention: test_given_<precondition>_when_<action>_then_<expected>
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# =========================================================================
# TestTimingSettingsUI — 控件范围和默认值
# =========================================================================

class TestTimingSettingsUI:
    """验证 TemperatureSweepWidget 的 Timing Settings 控件。"""

    @pytest.fixture
    def sweep(self, qapp):
        from ui.dashboard_page import TemperatureSweepWidget
        widget = TemperatureSweepWidget()
        return widget

    def test_given_widget_when_created_then_pre_wait_spin_exists(self, sweep):
        assert hasattr(sweep, "_pre_wait_spin"), \
            "TemperatureSweepWidget 应有 _pre_wait_spin 控件"

    def test_given_widget_when_created_then_max_wait_spin_exists(self, sweep):
        assert hasattr(sweep, "_max_wait_spin"), \
            "TemperatureSweepWidget 应有 _max_wait_spin 控件"

    def test_given_widget_when_created_then_pre_wait_spin_range_0_to_120(self, sweep):
        assert sweep._pre_wait_spin.minimum() == 0
        assert sweep._pre_wait_spin.maximum() == 120

    def test_given_widget_when_created_then_max_wait_spin_range_5_to_config_max(self, sweep):
        from config import max_wait_max_minutes
        assert sweep._max_wait_spin.minimum() == 5
        assert sweep._max_wait_spin.maximum() == max_wait_max_minutes

    def test_given_defaults_when_get_timing_then_returns_zero_and_thirty(self, sweep):
        timing = sweep.get_timing_settings()
        assert timing["pre_measurement_wait_min"] == 0
        assert timing["max_wait_min"] == 30

    def test_given_custom_values_when_get_timing_then_returns_user_values(self, sweep):
        sweep._pre_wait_spin.setValue(5)
        sweep._max_wait_spin.setValue(60)
        timing = sweep.get_timing_settings()
        assert timing["pre_measurement_wait_min"] == 5
        assert timing["max_wait_min"] == 60

    def test_given_timing_changed_when_value_set_then_settings_changed_emitted(self, sweep):
        """修改 timing 值应触发 settings_changed 信号。"""
        signal_received = []

        def _on_changed():
            signal_received.append(True)

        sweep.settings_changed.connect(_on_changed)
        sweep._pre_wait_spin.setValue(10)
        assert len(signal_received) >= 1, \
            "pre_wait_spin 值变化应触发 settings_changed"

        sweep._max_wait_spin.setValue(45)
        assert len(signal_received) >= 2, \
            "max_wait_spin 值变化应触发 settings_changed"


# =========================================================================
# TestTimingSettingsPersistence — 设置持久化
# =========================================================================

class TestTimingSettingsPersistence:
    """验证 timing 设置通过 get_settings/set_settings 正确序列化。"""

    @pytest.fixture
    def sweep(self, qapp):
        from ui.dashboard_page import TemperatureSweepWidget
        widget = TemperatureSweepWidget()
        return widget

    def test_given_custom_timing_when_get_settings_then_included(self, sweep):
        sweep._pre_wait_spin.setValue(15)
        sweep._max_wait_spin.setValue(90)
        settings = sweep.get_settings()
        assert settings["pre_wait_min"] == 15
        assert settings["max_wait_min"] == 90

    def test_given_loaded_settings_when_set_then_spins_restored(self, sweep):
        sweep.set_settings({
            "mode": "fixed",
            "pre_wait_min": 20,
            "max_wait_min": 45,
        })
        timing = sweep.get_timing_settings()
        assert timing["pre_measurement_wait_min"] == 20
        assert timing["max_wait_min"] == 45

    def test_given_old_settings_without_timing_when_set_then_defaults_preserved(self, sweep):
        """向后兼容: 旧版 settings 无 timing 字段时不应崩溃。"""
        sweep._pre_wait_spin.setValue(10)
        sweep._max_wait_spin.setValue(40)
        sweep.set_settings({"mode": "fixed", "fixed_temps": [30, 50]})
        timing = sweep.get_timing_settings()
        assert timing["pre_measurement_wait_min"] == 10
        assert timing["max_wait_min"] == 40
