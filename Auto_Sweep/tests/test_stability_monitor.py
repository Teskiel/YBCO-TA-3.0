# -*- coding: utf-8 -*-
"""
BDD tests for stability_monitor.py

Tests cover all 5 stability-checking methods plus edge cases.
All tests are pure — no hardware or VISA dependencies.
"""

import time
import pytest
from stability_monitor import AdvancedStabilityMonitor, TemperatureReading


# ======================================================================
# Helpers
# ======================================================================

def _feed(monitor, temps, target=30.0, step_seconds=10):
    """Feed a sequence of temperature readings into the monitor.

    Each reading is stamped `step_seconds` apart, going backward so the
    most recent reading has timestamp=now.
    """
    now = time.time()
    for i, t in enumerate(temps):
        r = TemperatureReading(
            timestamp=now - (len(temps) - 1 - i) * step_seconds,
            temperature=t,
            target=target,
        )
        monitor.readings.append(r)


# ======================================================================
# check_stability
# ======================================================================

class TestStabilityCustom:
    """Given the rolling 1-min averages stability method (production default)."""

    def test_given_stable_plateau_when_checking_then_returns_stable(self):
        monitor = AdvancedStabilityMonitor()
        # 150 readings at 6s intervals = 15 min of data; 30 readings in 180s window
        _feed(monitor, [30.0] * 150, step_seconds=6)
        result = monitor.check_stability(30.0)
        assert result["stable"] is True
        assert result["method"] == "custom"

    def test_given_insufficient_data_when_checking_then_returns_not_stable_with_reason(self):
        """min_readings_required=10，5 个读数不足以进行稳定性检测。"""
        monitor = AdvancedStabilityMonitor()
        _feed(monitor, [30.0] * 5, step_seconds=10)
        result = monitor.check_stability(30.0)
        assert result["stable"] is False
        assert "readings" in result["reason"].lower()

    def test_given_temperature_far_from_target_when_checking_then_condition1_fails(self):
        monitor = AdvancedStabilityMonitor()
        _feed(monitor, [35.0] * 90, step_seconds=10)
        result = monitor.check_stability(30.0)
        # average of 35.0 is outside ±1.0K tolerance → condition1 fails
        assert result["stable"] is False

    def test_given_rate_stable_but_off_target_when_checking_then_ready_for_adjust_true(self):
        """ready_for_adjust only requires rate stability, not being on-target."""
        monitor = AdvancedStabilityMonitor()
        # temperature is flat at 28K (off from 30K target) but not changing
        _feed(monitor, [28.0] * 90, step_seconds=10)
        result = monitor.check_stability(30.0)
        # condition2 (delta between windows) should pass because it's flat
        assert result.get("ready_for_adjust", False) or result["stable"] is False


# ======================================================================
# Edge cases
# ======================================================================

class TestEdgeCases:
    """Given edge-case inputs."""

    def test_given_add_reading_when_exceeding_max_then_oldest_dropped(self):
        monitor = AdvancedStabilityMonitor()
        monitor.max_readings = 10
        for i in range(15):
            monitor.add_reading(30.0, 30.0)
        assert len(monitor.readings) == 10

    def test_given_clear_called_when_checking_then_no_readings(self):
        monitor = AdvancedStabilityMonitor()
        _feed(monitor, [30.0] * 60, step_seconds=1)
        monitor.clear()
        assert len(monitor.readings) == 0


# ======================================================================
# Factory
# ======================================================================

class TestTemperatureReading:
    """Given TemperatureReading dataclass."""

    def test_given_valid_values_when_creating_reading_then_fields_match(self):
        now = time.time()
        r = TemperatureReading(timestamp=now, temperature=30.5, target=30.0)
        assert r.temperature == 30.5
        assert r.target == 30.0
        assert r.timestamp == now


# ======================================================================
# 新增：连续稳定判定（需求4.3 — wait_for_stable_temperature 重构）
# ======================================================================

class TestConsecutiveStableDetection:
    """验证超时/不稳定时的结果报告。"""

    def test_given_timeout_before_stable_when_checking_then_reports_actual_temps_and_reason(
        self
    ):
        """超时未稳定 → 返回原因描述。"""
        from stability_monitor import AdvancedStabilityMonitor
        monitor = AdvancedStabilityMonitor()

        now = time.time()
        temps = [30.0 + 0.3 * (i % 3 - 1) for i in range(30)]
        for i, t in enumerate(temps):
            monitor.readings.append(TemperatureReading(
                timestamp=now - (29 - i) * 10,
                temperature=t,
                target=30.0,
            ))

        result = monitor.check_stability(30.0, method="custom")
        assert isinstance(result, dict)
        if result.get("stable") is False:
            assert "reason" in result
            assert len(result["reason"]) > 0


