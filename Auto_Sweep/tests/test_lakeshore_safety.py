# -*- coding: utf-8 -*-
"""
BDD tests for LakeShore cooling-safety logic in LakeShoreWorker.set_setpoint().

Safety rule (one-directional):
  If actual_temp > target_setpoint + 20 K  →  heater OFF,
  poll until actual_temp - target_setpoint < 20 K,
  then heater → Medium, then write SETP.
  If actual_temp <= target_setpoint + 20 K → no intervention.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from ui.workers import LakeShoreWorker

from ui.workers import LakeShoreWorker
from PyQt5.QtCore import QObject


# =========================================================================
# Helpers — build a LakeShoreWorker with a mocked controller
# =========================================================================

def _make_worker_with_controller(initial_temp_a=50.0, initial_temp_b=295.0):
    """Return (worker, mock_ctrl) where mock_ctrl is a MagicMock
    standing in for a LakeShore335 instance."""
    from ui.workers import LakeShoreWorker

    mock_ctrl = MagicMock()
    # get_temperature(channel) returns the canned value
    temps = {"A": initial_temp_a, "B": initial_temp_b}

    def _get_temp(channel="A"):
        return temps.get(channel, 0.0)

    mock_ctrl.get_temperature.side_effect = _get_temp

    worker = LakeShoreWorker()
    worker._controller = mock_ctrl
    return worker, mock_ctrl, temps


# =========================================================================
# TestClass: Safety NOT triggered (normal heating or small diff)
# =========================================================================

class TestSafetyNotTriggered:
    """Given a LakeShoreWorker, when setpoint change is safe, no heater intervention."""

    def test_given_actual_below_target_when_setpoint_requested_then_no_safety(
        self,
    ):
        """actual=50K, target=55K → actual < target, heater stays as-is."""
        worker, mock_ctrl, temps = _make_worker_with_controller(
            initial_temp_a=50.0
        )

        worker.set_setpoint(1, 55.0)

        # heater should NOT have been changed
        assert not mock_ctrl.set_heater_range.called
        # setpoint should be written directly
        mock_ctrl.set_temperature.assert_called_once_with(55.0, 1)

    def test_given_actual_slightly_above_target_within_20k_when_setpoint_requested_then_no_safety(
        self,
    ):
        """actual=55K, target=50K → diff=5K (<20K), no safety."""
        worker, mock_ctrl, temps = _make_worker_with_controller(
            initial_temp_a=55.0
        )

        worker.set_setpoint(1, 50.0)

        assert not mock_ctrl.set_heater_range.called
        mock_ctrl.set_temperature.assert_called_once_with(50.0, 1)

    def test_given_actual_exactly_20k_above_target_when_setpoint_requested_then_no_safety(
        self,
    ):
        """actual=30K, target=10K → diff=20K (not strictly > 20), no safety."""
        worker, mock_ctrl, temps = _make_worker_with_controller(
            initial_temp_a=30.0
        )

        worker.set_setpoint(1, 10.0)

        assert not mock_ctrl.set_heater_range.called
        mock_ctrl.set_temperature.assert_called_once_with(10.0, 1)


# =========================================================================
# TestClass: Safety TRIGGERED (actual > target + 20K)
# =========================================================================

class TestSafetyTriggered:
    """Given a LakeShoreWorker, when actual >> target, heater turns OFF first."""

    def test_given_actual_77k_target_10k_when_setpoint_requested_then_heater_off_first(
        self,
    ):
        """actual=77K, target=10K → diff=67K > 20K → heater OFF."""
        worker, mock_ctrl, temps = _make_worker_with_controller(
            initial_temp_a=77.0
        )

        # The safety loop polls get_temperature every 2s.
        # Simulate: initial 77K → after 1st poll → 25K → after 2nd → 29K
        # We need actual - target < 20 for the loop to exit, i.e. actual < 30K.
        poll_values = [77.0, 60.0, 40.0, 29.0]  # decreasing toward 29K
        call_count = [0]

        def _poll_temp(channel="A"):
            idx = min(call_count[0], len(poll_values) - 1)
            val = poll_values[idx]
            call_count[0] += 1
            return val

        mock_ctrl.get_temperature.side_effect = _poll_temp

        worker.set_setpoint(1, 10.0)

        # Should have set heater OFF first: set_heater_range(1, 0)
        off_calls = [
            c for c in mock_ctrl.set_heater_range.call_args_list
            if c == ((1, 0),) or c == ((1, 0), {})
        ]
        assert len(off_calls) >= 1

        # After loop exits, should set heater Medium: set_heater_range(1, 2)
        med_calls = [
            c for c in mock_ctrl.set_heater_range.call_args_list
            if c == ((1, 2),) or c == ((1, 2), {})
        ]
        assert len(med_calls) >= 1

        # Finally should write setpoint
        mock_ctrl.set_temperature.assert_called_with(10.0, 1)

    def test_given_large_step_loop_2_when_setpoint_requested_then_channel_b_used(
        self,
    ):
        """Loop 2 uses channel B temperature."""
        worker, mock_ctrl, temps = _make_worker_with_controller(
            initial_temp_b=200.0
        )
        # target=100K, actual=200K → diff=100K > 20K → safety for loop 2
        poll_values = [200.0, 150.0, 119.0]  # cooling to < 120K
        call_count = [0]

        def _poll_temp(channel="B"):
            idx = min(call_count[0], len(poll_values) - 1)
            val = poll_values[idx]
            call_count[0] += 1
            return val

        mock_ctrl.get_temperature.side_effect = _poll_temp

        worker.set_setpoint(2, 100.0)

        # Heater OFF on output 2
        assert any(
            c == ((2, 0),) or c == ((2, 0), {})
            for c in mock_ctrl.set_heater_range.call_args_list
        )
        # Heater Medium on output 2
        assert any(
            c == ((2, 2),) or c == ((2, 2), {})
            for c in mock_ctrl.set_heater_range.call_args_list
        )
        mock_ctrl.set_temperature.assert_called_with(100.0, 2)


# =========================================================================
# TestClass: Error handling
# =========================================================================

class TestHeaterRangeAlwaysMedium:
    """验证 heater range 一律使用 Medium (2)。"""

    def test_given_set_heater_range_any_value_when_called_then_always_medium(self):
        """无论传入什么值，set_heater_range 都应强制使用 Medium=2。"""
        from unittest.mock import MagicMock
        from ui.workers import LakeShoreWorker

        worker = LakeShoreWorker()
        mock_ctrl = MagicMock()
        worker._controller = mock_ctrl

        # 尝试设置 Low (1)
        worker.set_heater_range(1, 1)
        mock_ctrl.set_heater_range.assert_called_with(1, 2)  # 强制 Medium

        # 尝试设置 High (3)
        worker.set_heater_range(1, 3)
        mock_ctrl.set_heater_range.assert_called_with(1, 2)  # 强制 Medium

        # 尝试设置 Medium (2)
        worker.set_heater_range(1, 2)
        mock_ctrl.set_heater_range.assert_called_with(1, 2)  # 保持 Medium


class TestHeaterRangeAlwaysMedium:
    """验证 heater range 一律使用 Medium (2)。"""

    def test_given_set_heater_range_any_value_when_called_then_always_medium(self):
        """无论传入什么值，set_heater_range 都应强制使用 Medium=2。"""
        from unittest.mock import MagicMock
        from ui.workers import LakeShoreWorker

        worker = LakeShoreWorker()
        mock_ctrl = MagicMock()
        worker._controller = mock_ctrl

        # 尝试设置 Low (1)
        worker.set_heater_range(1, 1)
        mock_ctrl.set_heater_range.assert_called_with(1, 2)  # 强制 Medium

        # 尝试设置 High (3)
        worker.set_heater_range(1, 3)
        mock_ctrl.set_heater_range.assert_called_with(1, 2)  # 强制 Medium

        # 尝试设置 Medium (2)
        worker.set_heater_range(1, 2)
        mock_ctrl.set_heater_range.assert_called_with(1, 2)  # 保持 Medium


class TestSafetyErrorHandling:
    """Given disconnected LakeShoreWorker, set_setpoint emits error."""

    def test_given_no_controller_when_setpoint_requested_then_error_emitted(self):
        worker = LakeShoreWorker()
        worker._controller = None

        errors = []
        worker.error.connect(lambda msg: errors.append(msg))

        worker.set_setpoint(1, 50.0)
        assert len(errors) == 1
        assert "not connected" in errors[0].lower()
