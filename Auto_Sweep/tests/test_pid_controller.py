# -*- coding: utf-8 -*-
"""
BDD tests for pid_controller.py

Tests temperature-zone PID selection and setpoint overshoot calculation.
"""

import pytest
from pid_controller import SmartPIDController


class TestGetParamsForTemperature:
    """Given SmartPIDController.get_params_for_temperature()."""

    def test_given_target_10K_when_getting_params_then_returns_low_temp_pid(self):
        params = SmartPIDController.get_params_for_temperature(10.0)
        assert params["p"] == 100.0
        assert params["i"] == 5.0
        assert params["d"] == 0.0

    def test_given_target_exactly_20K_when_getting_params_then_returns_low_temp_pid(self):
        """<= bound check: 20.0 is in low_temp zone."""
        params = SmartPIDController.get_params_for_temperature(20.0)
        assert params["p"] == 100.0
        assert params["i"] == 5.0

    def test_given_target_30K_when_getting_params_then_returns_medium_temp_pid(self):
        params = SmartPIDController.get_params_for_temperature(30.0)
        assert params["p"] == 100.0
        assert params["i"] == 0.0
        assert params["d"] == 0.0

    def test_given_target_exactly_40K_when_getting_params_then_returns_medium_temp_pid(self):
        """<= bound check: 40.0 is in medium_temp zone."""
        params = SmartPIDController.get_params_for_temperature(40.0)
        assert params["p"] == 100.0
        assert params["i"] == 0.0

    def test_given_target_77K_when_getting_params_then_returns_high_temp_pid(self):
        params = SmartPIDController.get_params_for_temperature(77.0)
        assert params["p"] == 150.0
        assert params["i"] == 0.0
        assert params["d"] == 0.0

    def test_given_target_300K_when_getting_params_then_returns_high_temp_pid(self):
        params = SmartPIDController.get_params_for_temperature(300.0)
        assert params["p"] == 150.0


class TestCalculateAdjustedSetpoint:
    """Given SmartPIDController.calculate_adjusted_setpoint()."""

    def test_given_target_below_20K_when_calculating_then_setpoint_equals_target(self):
        """Below 20K: no overshoot, setpoint = target exactly."""
        sp = SmartPIDController.calculate_adjusted_setpoint(10.0, 9.0)
        assert sp == 10.0

    def test_given_target_15K_when_calculating_then_no_overshoot_regardless_of_error(self):
        """Even with large error below 20K, no overshoot."""
        sp = SmartPIDController.calculate_adjusted_setpoint(15.0, 5.0)
        assert sp == 15.0

    def test_given_target_30K_when_calculating_then_applies_overshoot(self):
        """Above 20K: overshoot = max(1.0, delta * 0.5)."""
        # delta = 30 - 28 = 2, overshoot = max(1.0, 2*0.5) = max(1.0, 1.0) = 1.0
        # setpoint = 30 + 1.0 = 31.0
        sp = SmartPIDController.calculate_adjusted_setpoint(30.0, 28.0)
        assert sp > 30.0  # overshoot applied
        assert sp == pytest.approx(31.0, rel=1e-6)

    def test_given_large_error_when_calculating_then_overshoot_scales(self):
        """delta = 50-30 = 20, overshoot = max(1.0, 20*0.5) = 10.0."""
        sp = SmartPIDController.calculate_adjusted_setpoint(50.0, 30.0)
        assert sp == 50.0 + 10.0  # 60.0


class TestBoundaryConditions:
    """Given boundary values at zone edges."""

    def test_given_target_at_low_threshold_when_getting_params_then_low_zone(self):
        """19.999K should be low_temp (<=20)."""
        params = SmartPIDController.get_params_for_temperature(19.999)
        assert params["i"] == 5.0  # low zone has I term

    def test_given_target_at_20_001_when_getting_params_then_medium_zone(self):
        """20.001K should be medium_temp."""
        params = SmartPIDController.get_params_for_temperature(20.001)
        assert params["i"] == 0.0  # medium zone has no I term

    def test_given_every_zone_boundary_when_checking_then_no_crash(self):
        """Scan through the full temperature range — should never crash."""
        for t in [1, 5, 10, 19, 20, 21, 30, 39, 40, 41, 50, 77, 100, 200, 300]:
            params = SmartPIDController.get_params_for_temperature(t)
            assert "p" in params and "i" in params and "d" in params
            sp = SmartPIDController.calculate_adjusted_setpoint(t, t - 2)
            assert sp >= t  # setpoint should never be below target
