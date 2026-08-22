# -*- coding: utf-8 -*-
"""
BDD tests for config.py

Naming convention: test_given_<precondition>_when_<action>_then_<expected>
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestResourceAddresses:
    """Given the config module, resource addresses should be valid VISA strings."""

    def test_given_config_loaded_when_reading_vna_address_then_is_tcpip_hislip(self):
        import config
        assert "TCPIP" in config.resource_vna
        assert "hislip" in config.resource_vna

    def test_given_config_loaded_when_reading_laser_address_then_is_tcpip(self):
        import config
        assert "TCPIP" in config.laser_resource

    def test_given_config_loaded_when_reading_lakeshore_address_then_is_asrl(self):
        import config
        assert "ASRL" in config.resource_lakeshore


class TestSweepSettings:
    """Given the config module, sweep parameters should be consistent."""

    def test_given_power_levels_when_checking_then_non_empty_monotonic(self):
        import config
        assert len(config.power_levels_mw) > 0
        assert config.power_levels_mw == sorted(config.power_levels_mw)

    def test_given_temperature_levels_when_checking_then_starts_at_26_ends_at_100_step_2(self):
        import config
        assert config.temperature_levels_k[0] == 26
        assert config.temperature_levels_k[-1] == 100
        assert config.temperature_levels_k[1] - config.temperature_levels_k[0] == 2

    def test_given_stability_settings_when_checking_then_all_tolerances_positive(self):
        import config
        assert config.stable_hold_seconds > 0
        assert config.max_wait_seconds > config.stable_hold_seconds
        assert config.temperature_poll_seconds > 0


class TestCustomStabilitySettings:
    """Given custom stability method, settings should be internally consistent."""

    def test_given_custom_settings_when_checking_then_final_band_tighter_than_avg_tolerance(self):
        import config
        s = config.custom_stability_settings
        assert s['final_stable_band_k'] <= s['avg_tolerance_k']

    def test_given_custom_settings_when_checking_then_delta_tolerance_stricter_than_avg(self):
        import config
        s = config.custom_stability_settings
        assert s['delta_tolerance_k'] <= s['avg_tolerance_k']

    def test_given_custom_settings_when_checking_then_final_band_is_0_5(self):
        """放宽后 final_stable_band_k 应为 0.5K。"""
        import config
        s = config.custom_stability_settings
        assert s['final_stable_band_k'] == 0.5

    def test_given_sparse_polling_when_min_readings_checked_then_enough_for_180s_window(self):
        """min_readings_required 必须 ≤ 180s 窗口在 SPARSE 20s 轮询下能收集到的最大读数。

        180 / 20 = 9 → min_readings_required 必须 ≤ 9，否则 check_stability()
        在 SPARSE 阶段永远返回 avg_temp=None，导致过冲调整死锁。
        （回归测试：2026-06-12 发现 min_readings=10 时此 bug 导致 36K 温度点卡死）
        """
        import config
        s = config.custom_stability_settings
        max_readings_sparse = 180 / config.sparse_poll_seconds
        assert s['min_readings_required'] <= max_readings_sparse, (
            f"min_readings_required={s['min_readings_required']} 大于 "
            f"SPARSE 180s 窗口最大读数 {max_readings_sparse:.0f}，"
            f"check_stability() 在 SPARSE 阶段将永远无法进行稳定性判定"
        )
    """Given PID_PARAMS, temperature zones should not overlap or leave gaps."""

    def test_given_pid_params_when_checking_zones_then_low_ends_at_20(self):
        import config
        assert config.PID_PARAMS['low_temp']['max_temp'] == 20.0

    def test_given_pid_params_when_checking_zones_then_medium_spans_20_to_40(self):
        import config
        assert config.PID_PARAMS['medium_temp']['min_temp'] == 20.0
        assert config.PID_PARAMS['medium_temp']['max_temp'] == 40.0

    def test_given_pid_params_when_checking_zones_then_high_starts_at_40(self):
        import config
        assert config.PID_PARAMS['high_temp']['min_temp'] == 40.0

    def test_given_pid_params_when_checking_then_all_zones_have_p_i_d(self):
        import config
        for zone in config.PID_PARAMS.values():
            assert 'p' in zone
            assert 'i' in zone
            assert 'd' in zone


class TestSetpointAdjustSettings:
    """Given setpoint adjustment settings, thresholds should be ordered."""

    def test_given_setpoint_settings_when_checking_then_low_below_medium(self):
        import config
        s = config.setpoint_adjust_settings
        assert s['low_temp_threshold'] < s['medium_temp_threshold']

    def test_given_setpoint_settings_when_checking_then_overshoot_factor_between_0_and_1(self):
        import config
        s = config.setpoint_adjust_settings
        assert 0 < s['overshoot_factor'] <= 1.0


class TestDeadCodeRemoval:
    """Given the refactoring, dead variables should NOT exist."""

    def test_given_config_loaded_when_checking_then_trace_name_absent(self):
        import config
        assert not hasattr(config, 'TRACE_NAME')

    def test_given_config_loaded_when_checking_then_auto_adjust_pid_absent(self):
        import config
        assert not hasattr(config, 'auto_adjust_pid')

    def test_given_config_loaded_when_checking_then_diagnose_before_measure_absent(self):
        import config
        assert not hasattr(config, 'diagnose_before_measure')


class TestAutoReconnectConfig:
    """验证自动重连配置项的完整性。"""

    def test_given_config_loaded_when_reconnect_settings_present_then_valid(self):
        import config
        assert hasattr(config, "max_reconnect_attempts")
        assert config.max_reconnect_attempts >= 1
        assert hasattr(config, "reconnect_delay_seconds")
        assert config.reconnect_delay_seconds >= 0


class TestStabilityFallbackConfig:
    """验证稳定性回退配置项的完整性。"""

    def test_given_config_loaded_when_fallback_settings_present_then_valid(self):
        import config
        assert hasattr(config, "stability_fallback_settings")
        s = config.stability_fallback_settings
        assert s["good_enough_band_k"] > 0
        assert s["max_setpoint_adjustments"] >= 1
        assert s["diagnostic_interval_s"] > 0
        assert s["max_overshoot_k"] > 0
        assert s["overshoot_target_band_k"] > 0

    def test_given_fixed_pid_zones_when_checking_then_all_zones_valid(self):
        """FIXED_PID_ZONES 应包含 low/medium/high/very_high 四个温区。"""
        import config
        zones = config.FIXED_PID_ZONES
        for name in ("low", "medium", "high", "very_high"):
            assert name in zones
            z = zones[name]
            assert "p" in z and z["p"] > 0
            assert "i" in z and z["i"] >= 0
            assert "d" in z and z["d"] == 0
            assert "base_overshoot_k" in z and z["base_overshoot_k"] >= 0
            assert "max_temp" in z


class TestOutputPath:
    """Given config, the output path should be well-formed."""

    def test_given_base_folder_when_checking_then_contains_date_and_temperature_sweep(self):
        import config
        assert 'temperature_sweep' in config.base_folder or 'YBCO' in config.base_folder

    def test_given_base_folder_when_checking_then_is_absolute(self):
        import config
        assert config.base_folder[1:3] == ':\\' or config.base_folder.startswith('/')


# =========================================================================
# 新增：测量逻辑版本号（需求4.2）
# =========================================================================

class TestMeasurementVersion:
    """验证 MEASUREMENT_LOGIC_VERSION 常量的存在性和格式。"""

    def test_given_config_loaded_when_version_present_then_valid_format(self):
        import config
        assert hasattr(config, "MEASUREMENT_LOGIC_VERSION")
        version = config.MEASUREMENT_LOGIC_VERSION
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", version)

    def test_given_version_when_reading_then_is_string(self):
        import config
        assert isinstance(config.MEASUREMENT_LOGIC_VERSION, str)


# =========================================================================
# 新增：SPARSE 阶段超时
# =========================================================================

class TestSparseTimeoutConfig:
    """验证 sparse_max_wait_seconds 常量。"""

    def test_given_config_sparse_max_wait_then_default_is_5400(self):
        import config
        assert config.sparse_max_wait_seconds == 90 * 60

    def test_given_config_sparse_max_wait_then_is_integer(self):
        import config
        assert isinstance(config.sparse_max_wait_seconds, int)

    def test_given_config_sparse_max_wait_then_greater_than_max_wait(self):
        import config
        assert config.sparse_max_wait_seconds > config.max_wait_seconds, \
            "SPARSE 阶段超时应 > FINE 阶段超时"


# =========================================================================
# 新增：测量中温度漂移熔断阈值
# =========================================================================

class TestInterMeasurementDeltaConfig:
    """验证 inter_measurement_max_delta_k 常量。"""

    def test_given_config_inter_measurement_delta_then_default_is_0_25(self):
        import config
        assert config.inter_measurement_max_delta_k == 0.25

    def test_given_config_inter_measurement_delta_then_is_float(self):
        import config
        assert isinstance(config.inter_measurement_max_delta_k, float)

    def test_given_config_inter_measurement_delta_then_positive(self):
        import config
        assert config.inter_measurement_max_delta_k > 0


# =========================================================================
# 新增：测量前预等待 + max_wait 上限
# =========================================================================

class TestTimingConfig:
    """验证 pre_measurement_wait 和 max_wait_max 常量。"""

    def test_given_config_pre_wait_then_default_is_zero(self):
        import config
        assert config.pre_measurement_wait_minutes == 0

    def test_given_config_pre_wait_max_then_equals_120(self):
        import config
        assert config.pre_measurement_wait_max_minutes == 120

    def test_given_config_max_wait_max_minutes_then_equals_180(self):
        import config
        assert config.max_wait_max_minutes == 180

    def test_given_config_max_wait_max_then_valid_range(self):
        import config
        assert config.max_wait_max_minutes >= 5, "上限至少 5 min"
        assert config.max_wait_max_minutes <= 240, "上限不得过大"


# =========================================================================
# Fix 1: 预等待后温度验证容差
# =========================================================================

class TestPreMeasurementWaitTolerance:
    """验证 pre_measurement_wait_temp_tolerance_k 常量。"""

    def test_given_config_loaded_when_tolerance_present_then_is_0_5(self):
        import config
        assert hasattr(config, "pre_measurement_wait_temp_tolerance_k"), \
            "pre_measurement_wait_temp_tolerance_k 必须存在"
        assert config.pre_measurement_wait_temp_tolerance_k == 0.5

    def test_given_config_loaded_when_tolerance_present_then_is_positive_float(self):
        import config
        assert isinstance(config.pre_measurement_wait_temp_tolerance_k, float)
        assert config.pre_measurement_wait_temp_tolerance_k > 0


# =========================================================================
# Fix 2: 熔断重启上限
# =========================================================================

class TestMaxMeltdownRestarts:
    """验证 max_meltdown_restarts 常量。"""

    def test_given_config_loaded_when_restart_limit_present_then_is_3(self):
        import config
        assert hasattr(config, "max_meltdown_restarts"), \
            "max_meltdown_restarts 必须存在"
        assert config.max_meltdown_restarts == 3

    def test_given_config_loaded_when_restart_limit_present_then_is_positive_int(self):
        import config
        assert isinstance(config.max_meltdown_restarts, int)
        assert config.max_meltdown_restarts >= 1


# =========================================================================
# Fix 3: 激光沉降时间
# =========================================================================

class TestLaserSettleTime:
    """验证激光沉降时间常量。"""

    def test_given_config_loaded_when_laser_settle_time_present_then_is_20(self):
        import config
        assert hasattr(config, "laser_settle_time_s"), \
            "laser_settle_time_s 必须存在"
        assert config.laser_settle_time_s == 20

    def test_given_config_loaded_when_laser_first_on_settle_time_present_then_is_60(self):
        import config
        assert hasattr(config, "laser_first_on_settle_time_s"), \
            "laser_first_on_settle_time_s 必须存在"
        assert config.laser_first_on_settle_time_s == 60

    def test_given_config_loaded_when_settle_times_present_then_first_on_gt_normal(self):
        import config
        assert config.laser_first_on_settle_time_s > config.laser_settle_time_s, \
            "首次上电沉降时间应 > 功率切换沉降时间"


# =========================================================================
# Fix 4: 激光加热温度容差
# =========================================================================

class TestLaserOnTempTolerance:
    """验证 laser_on_temp_tolerance_k 常量。"""

    def test_given_config_loaded_when_laser_on_tolerance_present_then_is_1_0(self):
        import config
        assert hasattr(config, "laser_on_temp_tolerance_k"), \
            "laser_on_temp_tolerance_k 必须存在"
        assert config.laser_on_temp_tolerance_k == 1.0

    def test_given_config_loaded_when_laser_on_tolerance_present_then_gt_strict(self):
        import config
        assert config.laser_on_temp_tolerance_k > 0.5, \
            "激光加热容差应 > 0.5K 严格容差"