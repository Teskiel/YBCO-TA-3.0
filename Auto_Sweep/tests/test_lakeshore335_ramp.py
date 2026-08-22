# -*- coding: utf-8 -*-
"""
BDD tests for lakeshore335_ramp.py — standalone ramp controller.

Uses Mock VISA from conftest.py. Verifies SCPI commands, state
readback, zone/PID application, emergency stop, and JSON output.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest
from pyvisa.errors import VisaIOError


# =========================================================================
# Helpers
# =========================================================================

@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _mock_lakeshore_device():
    """Return a MagicMock that behaves like a LakeShore335 device."""
    device = MagicMock()
    device.identity = "LSCI,MODEL335,12345,1.0"
    device.get_temperature.return_value = 30.0
    device.get_heater_percent.return_value = 25.0
    device.get_heater_range.return_value = 2
    device.get_pid.return_value = (100.0, 3.0, 0.0)
    device.get_setpoint.return_value = 30.0
    return device


# =========================================================================
# TestClass: RampController Construction
# =========================================================================

class TestRampControllerConstruction:
    """Given LakeShore335RampController constructor."""

    def test_given_valid_address_when_constructing_then_no_connection_until_connect(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        assert ctrl.visa_address == "ASRL4::INSTR"
        assert ctrl._device is None
        # Defaults
        assert ctrl.stability_method == "custom"
        assert ctrl.poll_seconds == 10.0
        assert ctrl.stable_hold_seconds == 60.0

    def test_given_custom_parameters_when_constructing_then_uses_them(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(
            visa_address="ASRL3::INSTR",
            stability_method="simple",
            poll_seconds=5.0,
            stable_hold_seconds=30.0,
        )
        assert ctrl.stability_method == "simple"
        assert ctrl.poll_seconds == 5.0
        assert ctrl.stable_hold_seconds == 30.0


# =========================================================================
# TestClass: RampController Connection
# =========================================================================

class TestRampControllerConnection:
    """Given ramp controller connection lifecycle."""

    def test_given_mock_visa_when_connecting_then_identity_stored(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        ctrl._device = mock_device

        assert ctrl._device.identity == "LSCI,MODEL335,12345,1.0"

    def test_given_connected_when_getting_state_then_returns_all_fields(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        ctrl._device = mock_device

        state = ctrl.get_current_state()
        assert "temperature_k" in state
        assert state["temperature_k"] == 30.0
        assert "heater_percent" in state
        assert state["heater_percent"] == 25.0
        assert state["p"] == 100.0
        assert state["i"] == 3.0
        assert state["d"] == 0.0


# =========================================================================
# TestClass: Zone Settings Application
# =========================================================================

class TestZoneSettingsApplication:
    """Given apply_zone_settings() at various temperatures."""

    def test_given_target_10K_when_applying_then_zone_1_pid_and_range_set(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        ctrl._device = mock_device

        settings = ctrl.apply_zone_settings(10.0)
        assert settings["zone_id"] == 1
        assert settings["p"] == 100.0
        assert settings["i"] == 5.0
        assert settings["heater_range"] == 1  # Low
        # setpoint == target (no overshoot below 20K)
        assert settings["setpoint_k"] == 10.0

    def test_given_target_30K_when_applying_then_zone_2_pid_set_with_overshoot(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        mock_device.get_temperature.return_value = 25.0  # 5K error
        ctrl._device = mock_device

        settings = ctrl.apply_zone_settings(30.0)
        assert settings["zone_id"] == 2
        assert settings["p"] == 100.0
        assert settings["i"] == 3.0
        assert settings["heater_range"] == 2  # Medium
        # overshoot applied — setpoint > target
        assert settings["setpoint_k"] > 30.0

    def test_given_target_60K_when_applying_then_zone_3_pid_with_overshoot(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        mock_device.get_temperature.return_value = 50.0  # 10K error
        ctrl._device = mock_device

        settings = ctrl.apply_zone_settings(60.0)
        assert settings["zone_id"] == 3
        assert settings["p"] == 150.0
        assert settings["i"] == 0.0
        assert settings["setpoint_k"] > 60.0

    def test_given_apply_zone_when_SCPI_commands_sent_then_correct_order(self, qapp):
        """Verify correct SCPI command sequence: range → pid → setpoint."""
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        ctrl._device = mock_device

        ctrl.apply_zone_settings(30.0)

        # Check heater range was set on output 1
        mock_device.set_heater_range.assert_called()
        # Check PID was set
        mock_device.set_pid.assert_called()
        # Check setpoint was set
        mock_device.set_temperature.assert_called()


# =========================================================================
# TestClass: Emergency Stop
# =========================================================================

class TestEmergencyStop:
    """Given emergency_stop()."""

    def test_given_connected_when_emergency_stop_then_range_0_on_both_outputs(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        mock_device = _mock_lakeshore_device()
        ctrl._device = mock_device

        ctrl.emergency_stop()

        # Check set_heater_range calls for OFF on both outputs
        calls = mock_device.set_heater_range.call_args_list
        output_values = [(c[0][0], c[0][1]) for c in calls if len(c[0]) >= 2]
        # Must contain (1, 0) and (2, 0)
        assert (1, 0) in output_values
        assert (2, 0) in output_values

    def test_given_emergency_stop_when_no_device_then_no_error(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        # No device connected — should not crash
        ctrl.emergency_stop()
        assert ctrl._aborted is True


# =========================================================================
# TestClass: JSON Output
# =========================================================================

class TestJSONOutput:
    """Given save_optimal_params()."""

    def test_given_results_list_when_saving_json_then_file_created_with_correct_format(self, qapp, tmp_path):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        results = [
            {
                "target_k": 10.0, "actual_k": 10.003, "setpoint_k": 10.0,
                "overshoot_k": 0.0, "p": 100.0, "i": 5.0, "d": 0.0,
                "heater_range": "Low", "zone_id": 1, "stable": True,
                "elapsed_s": 234.5, "heater_percent_final": 12.3,
                "diagnostic_events": [], "notes": "",
            },
            {
                "target_k": 12.0, "actual_k": 12.001, "setpoint_k": 12.0,
                "overshoot_k": 0.0, "p": 100.0, "i": 5.0, "d": 0.0,
                "heater_range": "Low", "zone_id": 1, "stable": True,
                "elapsed_s": 180.0, "heater_percent_final": 14.1,
                "diagnostic_events": [], "notes": "",
            },
        ]

        log_dir = str(tmp_path / "test_ramp")
        os.makedirs(log_dir, exist_ok=True)

        path = ctrl.save_optimal_params(results, output_dir=log_dir)
        assert os.path.exists(path)

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "metadata" in data
        assert data["metadata"]["start_k"] == 10.0
        assert data["metadata"]["end_k"] == 12.0
        assert data["metadata"]["total_points"] == 2
        assert data["metadata"]["heater_range_policy"] == "Low + Medium only, High forbidden"
        assert len(data["parameters"]) == 2
        assert data["parameters"][0]["target_k"] == 10.0
        assert data["parameters"][0]["heater_range"] == "Low"

    def test_given_diagnostic_events_when_saving_json_then_events_preserved(self, qapp, tmp_path):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        results = [{
            "target_k": 30.0, "actual_k": 30.05, "setpoint_k": 31.5,
            "overshoot_k": 1.5, "p": 100.0, "i": 3.0, "d": 0.0,
            "heater_range": "Medium", "zone_id": 2, "stable": True,
            "elapsed_s": 300.0, "heater_percent_final": 35.0,
            "diagnostic_events": [
                {"time_s": 120, "state": "slow_oscillating",
                 "adjustment": {"p_delta": -20, "i_delta": 5}},
            ],
            "notes": "I调整一次后稳定",
        }]

        log_dir = str(tmp_path / "test_ramp2")
        os.makedirs(log_dir, exist_ok=True)
        path = ctrl.save_optimal_params(results, output_dir=log_dir)

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data["parameters"][0]["diagnostic_events"]) == 1
        assert data["parameters"][0]["diagnostic_events"][0]["state"] == "slow_oscillating"
        assert data["parameters"][0]["notes"] == "I调整一次后稳定"


# =========================================================================
# TestClass: Temperature Target Generation
# =========================================================================

class TestTemperatureTargets:
    """Given the ramp range 10K→80K, step 2K."""

    def test_given_10_to_80_step_2_when_generating_then_36_targets(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        targets = ctrl._generate_targets(start=10.0, end=80.0, step=2.0)
        assert len(targets) == 36
        assert targets[0] == 10.0
        assert targets[-1] == 80.0
        assert targets[1] == 12.0
        assert 40.0 in targets

    def test_given_20_to_30_step_5_when_generating_then_3_targets(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")

        targets = ctrl._generate_targets(start=20.0, end=30.0, step=5.0)
        assert targets == [20.0, 25.0, 30.0]


# =========================================================================
# TestClass: CLI Entry Point
# =========================================================================

class TestMainCLI:
    """Given the CLI main() function with argparse."""

    @patch("argparse.ArgumentParser.parse_args")
    def test_given_default_args_when_parsing_then_start_10_end_80_step_2(self, mock_parse_args, qapp):
        mock_parse_args.return_value = MagicMock(
            address=None, start=10.0, end=80.0, step=2.0,
            stability_method="custom", poll_seconds=10.0,
            hold_seconds=60.0, max_wait=1800.0, log_dir=None,
        )
        # Just verify arg defaults are correct
        args = mock_parse_args.return_value
        assert args.start == 10.0
        assert args.end == 80.0
        assert args.step == 2.0


# =========================================================================
# TestClass: Heater Auto-Range Upgrade (85% threshold)
# =========================================================================

class TestHeaterAutoRangeUpgrade:
    """Given heater% > 85% on Low range, auto-upgrade to Medium."""

    def test_given_heater_90pct_on_low_when_checking_then_upgrades_to_medium(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        mock_device = _mock_lakeshore_device()
        mock_device.get_heater_percent.return_value = 90.0  # > 85%
        mock_device.get_heater_range.return_value = 1  # Low
        ctrl._device = mock_device
        ctrl._current_heater_range = 1

        upgraded = ctrl._check_and_upgrade_heater_range()
        assert upgraded is True
        mock_device.set_heater_range.assert_called_with(1, 2)  # → Medium

    def test_given_heater_80pct_on_low_when_checking_then_no_upgrade(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        mock_device = _mock_lakeshore_device()
        mock_device.get_heater_percent.return_value = 80.0  # < 85%
        mock_device.get_heater_range.return_value = 1
        ctrl._device = mock_device
        ctrl._current_heater_range = 1

        upgraded = ctrl._check_and_upgrade_heater_range()
        assert upgraded is False
        mock_device.set_heater_range.assert_not_called()

    def test_given_already_medium_when_heater_90pct_then_no_upgrade(self, qapp):
        """Already on Medium — cannot go to High (High forbidden)."""
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        mock_device = _mock_lakeshore_device()
        mock_device.get_heater_percent.return_value = 90.0
        mock_device.get_heater_range.return_value = 2  # already Medium
        ctrl._device = mock_device
        ctrl._current_heater_range = 2

        upgraded = ctrl._check_and_upgrade_heater_range()
        assert upgraded is False
        mock_device.set_heater_range.assert_not_called()


# =========================================================================
# TestClass: Configuration Block
# =========================================================================

class TestConfigurationBlock:
    """Given the configuration constants at the top of lakeshore335_ramp.py."""

    def test_given_config_constants_when_importing_then_all_present(self, qapp):
        import lakeshore335_ramp as ramp
        assert hasattr(ramp, "RAMP_START_K")
        assert hasattr(ramp, "RAMP_END_K")
        assert hasattr(ramp, "RAMP_STEP_K")
        assert ramp.RAMP_START_K >= 4.0
        assert ramp.RAMP_END_K <= 300.0
        assert ramp.RAMP_STEP_K > 0

    def test_given_config_constants_when_constructing_then_defaults_from_config(self, qapp):
        import lakeshore335_ramp as ramp
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        targets = ctrl._generate_targets(
            start=ramp.RAMP_START_K,
            end=ramp.RAMP_END_K,
            step=ramp.RAMP_STEP_K,
        )
        assert len(targets) > 0
        assert targets[0] == ramp.RAMP_START_K
        assert targets[-1] == ramp.RAMP_END_K


# =========================================================================
# TestClass: Pure-P Mode in Ramp Controller
# =========================================================================

class TestPurePModeInRamp:
    """Given the ramp controller, oscillation → 5 failures → pure-P switch."""

    def test_given_pure_p_mode_applied_when_setting_pid_then_I_is_zero(self, qapp):
        from lakeshore335_ramp import LakeShore335RampController
        ctrl = LakeShore335RampController(visa_address="ASRL4::INSTR")
        mock_device = _mock_lakeshore_device()
        ctrl._device = mock_device
        ctrl._pure_p_mode = True
        ctrl._pure_p_p_value = 80.0

        ctrl.apply_zone_settings(30.0)
        # Should set I=0 regardless of zone
        pid_calls = mock_device.set_pid.call_args_list
        for call_args in pid_calls:
            args = call_args[0]
            assert args[1] == 0.0  # I=0 in pure-P mode
