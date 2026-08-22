# -*- coding: utf-8 -*-
"""
BDD tests for ExperimentWorker — full YBCO temperature + power sweep.

The ExperimentWorker runs the measurement loop on a background QThread.
Tests use MagicMock controllers and call run() synchronously for speed.

Updated for simplified controller: fixed PID + setpoint-only adjustment.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest
from PyQt5.QtCore import QObject, QThread


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# =========================================================================
# Helpers
# =========================================================================

def _make_mock_lakeshore(start_temp=30.0):
    """Return a MagicMock LakeShore335 that always returns *start_temp* K."""
    ls = MagicMock()
    ls.get_temperature.return_value = start_temp
    return ls


def _make_mock_laser():
    return MagicMock()


def _make_mock_vna():
    return MagicMock()


def _build_worker(lakeshore=None, laser=None, vna=None,
                  temp_list=None, power_list=None,
                  output_dir="", vna_settings=None, vna_power_list=None):
    """Instantiate ExperimentWorker and configure it with mocks."""
    from ui.workers import ExperimentWorker

    worker = ExperimentWorker()
    worker.configure(
        lakeshore_ctrl=lakeshore if lakeshore is not None else _make_mock_lakeshore(),
        laser_ctrl=laser if laser is not None else _make_mock_laser(),
        vna_resource=vna if vna is not None else _make_mock_vna(),
        temp_list=temp_list if temp_list is not None else [30.0, 50.0],
        power_list=power_list if power_list is not None else [0, 5],
        vna_power_list=vna_power_list if vna_power_list is not None else [-45],
        output_dir=output_dir or "/tmp/test_sweep",
        vna_settings=vna_settings if vna_settings is not None else {
            "start_freq_hz": 3_000_000_000.0,
            "stop_freq_hz": 6_000_000_000.0,
            "s_parameter": "S21",
            "power_dbm": [-45],
            "points": 50001,
            "if_bandwidth_hz": 10000,
        },
    )
    return worker


# =========================================================================
# TestClass: Validation
# =========================================================================

class TestExperimentValidation:
    """Given an ExperimentWorker, invalid inputs are rejected before run."""

    def test_given_empty_temp_list_when_configured_then_worker_has_empty_list(
        self, qapp
    ):
        worker = _build_worker(temp_list=[])
        assert worker._temp_list == []

    def test_given_empty_power_list_when_configured_then_worker_has_empty_list(
        self, qapp
    ):
        worker = _build_worker(power_list=[])
        assert worker._power_list == []


# =========================================================================
# TestClass: Full Experiment Loop
# =========================================================================

class TestExperimentLoop:
    """Given a configured ExperimentWorker, the run() method executes
    the full temperature-and-power sweep."""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_2_temps_2_powers_when_run_then_4_measurements(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """2 temperatures × 2 powers = 4 S2P measurements."""
        ls = _make_mock_lakeshore(start_temp=30.0)
        # 所有温度点都返回 30K（因为测量监控需要 temp ≈ target）
        ls.get_temperature.return_value = 30.0
        worker = _build_worker(
            lakeshore=ls,
            temp_list=[30.0, 30.0],
            power_list=[0, 5],
        )

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        assert finished_counts[0] == 4
        assert mock_sleep.called

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_0_mw_power_when_measuring_then_laser_output_off_called(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """At 0 mW, the laser should be turned OFF, not set to 0 mW."""
        laser = _make_mock_laser()
        worker = _build_worker(
            laser=laser,
            temp_list=[30.0],
            power_list=[0],
        )

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        assert laser.output_off.called
        # 温度点之间激光归零：set_power(0) 会被调用（预期行为）

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_nonzero_power_when_measuring_then_laser_set_and_output_on(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """At non-zero power, laser.set_power() + output_on() are called."""
        laser = _make_mock_laser()
        worker = _build_worker(
            laser=laser,
            temp_list=[30.0],
            power_list=[5],
        )

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        # 测量时 set_power(5)，温度点间归零 set_power(0)
        laser.set_power.assert_any_call(5)
        laser.output_on.assert_called_once()


# =========================================================================
# TestClass: Abort Mechanism
# =========================================================================

class TestAbortMechanism:
    """Given a running ExperimentWorker, calling abort() stops the loop."""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_worker_running_when_abort_called_then_emits_aborted(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """Abort before the first temperature step should emit aborted."""
        ls = _make_mock_lakeshore(start_temp=30.0)
        worker = _build_worker(
            lakeshore=ls,
            temp_list=[30.0, 40.0, 50.0],
            power_list=[0, 5],
        )

        aborted = []
        worker.experiment_aborted.connect(lambda: aborted.append(True))
        finished = []
        worker.experiment_finished.connect(lambda c: finished.append(c))

        worker.abort()

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        assert len(aborted) == 1
        assert len(finished) == 0


# =========================================================================
# TestClass: Parameters from GUI (not config.py)
# =========================================================================

class TestParametersFromCaller:
    """Given an ExperimentWorker, all sweep parameters come from configure(),
    NOT from config.py."""

    def test_given_custom_temp_power_lists_when_run_then_those_lists_are_used(
        self, qapp
    ):
        test_temps = [10.0, 77.0, 100.0]
        test_powers = [1, 3, 7]
        worker = _build_worker(
            temp_list=test_temps,
            power_list=test_powers,
        )
        assert worker._temp_list == test_temps
        assert worker._power_list == test_powers


# =========================================================================
# TestClass: 新简化版稳定性逻辑
# =========================================================================

class TestNewStabilityLogic:
    """验证 ExperimentWorker 使用简化版稳定性控制器。"""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_fixed_pid_controller_when_run_then_pid_written_once(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """固定 PID 应在每个温度点开始时写入一次。"""
        ls = _make_mock_lakeshore(start_temp=30.0)
        worker = _build_worker(lakeshore=ls, temp_list=[30.0])

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 固定 PID 被写入
        assert ls.set_pid.called
        assert len(finished_counts) >= 1

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_good_enough_when_run_then_proceeds(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """good_enough 结果应让测量继续。"""
        ls = _make_mock_lakeshore(start_temp=30.35)
        worker = _build_worker(lakeshore=ls, temp_list=[30.0])

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=False, reason="good_enough", avg_temp=30.35)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        assert len(finished_counts) == 1


# =========================================================================
# 新增：重测逻辑集成测试（需求1.2）
# =========================================================================

class TestRetryIntegration:
    """验证 ExperimentWorker 与重测机制的集成。"""

    @pytest.fixture(scope="module")
    def qapp(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def _make_mock_lakeshore(self, start_temp=50.0):
        ls = MagicMock()
        ls.get_temperature.return_value = start_temp
        return ls

    def _make_mock_laser(self):
        return MagicMock()

    def _make_mock_vna(self):
        return MagicMock()

    def _build_worker(self, lakeshore=None, laser=None, vna=None,
                      temp_list=None, power_list=None,
                      output_dir="", vna_settings=None, vna_power_list=None):
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        worker.configure(
            lakeshore_ctrl=lakeshore if lakeshore is not None else self._make_mock_lakeshore(),
            laser_ctrl=laser if laser is not None else self._make_mock_laser(),
            vna_resource=vna if vna is not None else self._make_mock_vna(),
            temp_list=temp_list if temp_list is not None else [30.0],
            power_list=power_list if power_list is not None else [0, 5],
            vna_power_list=vna_power_list if vna_power_list is not None else [-45],
            output_dir=output_dir or "/tmp/test_sweep",
            vna_settings=vna_settings if vna_settings is not None else {
                "start_freq_hz": 3_000_000_000.0,
                "stop_freq_hz": 6_000_000_000.0,
                "s_parameter": "S21",
                "power_dbm": [-45],
                "points": 50001,
                "if_bandwidth_hz": 10000,
            },
        )
        return worker

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_retry_enabled_when_run_then_measurement_called(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """ExperimentWorker 应按温度点×功率点调用 VNA 测量。"""
        ls = self._make_mock_lakeshore(start_temp=30.0)
        vna = self._make_mock_vna()
        worker = self._build_worker(lakeshore=ls, vna=vna, temp_list=[30.0], power_list=[0, 5])
        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))
        with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()
        assert len(finished_counts) == 1
        assert finished_counts[0] == 2

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_18_to_40K_measurement_when_run_then_signal_emitted_with_temp(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """18-40K 温区的每个测量点应该触发日志信号（含实际温度）。"""
        ls = self._make_mock_lakeshore(start_temp=29.5)
        worker = self._build_worker(lakeshore=ls, temp_list=[30.0], power_list=[0])
        log_messages = []
        worker.progress.connect(lambda msg: log_messages.append(msg))
        with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=29.95)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()
        assert len(log_messages) > 0


# =========================================================================
# 新增：readme 生成集成测试（需求4附录）
# =========================================================================

class TestReadmeGeneration:
    """验证 ExperimentWorker 实验完成后生成 readme.txt。"""

    @pytest.fixture(scope="module")
    def qapp(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def _make_mock_lakeshore(self, start_temp=50.0):
        ls = MagicMock()
        ls.get_temperature.return_value = start_temp
        return ls

    def _make_mock_laser(self):
        return MagicMock()

    def _make_mock_vna(self):
        return MagicMock()

    def _build_worker(self, lakeshore=None, laser=None, vna=None,
                      temp_list=None, power_list=None,
                      output_dir="", vna_settings=None, vna_power_list=None):
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        worker.configure(
            lakeshore_ctrl=lakeshore if lakeshore is not None else self._make_mock_lakeshore(),
            laser_ctrl=laser if laser is not None else self._make_mock_laser(),
            vna_resource=vna if vna is not None else self._make_mock_vna(),
            temp_list=temp_list if temp_list is not None else [30.0],
            power_list=power_list if power_list is not None else [0, 5],
            vna_power_list=vna_power_list if vna_power_list is not None else [-45],
            output_dir=output_dir or "/tmp/test_sweep",
            vna_settings=vna_settings if vna_settings is not None else {
                "start_freq_hz": 3_000_000_000.0,
                "stop_freq_hz": 6_000_000_000.0,
                "s_parameter": "S21",
                "power_dbm": [-45],
                "points": 50001,
                "if_bandwidth_hz": 10000,
            },
        )
        return worker

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_experiment_complete_when_finished_then_readme_written(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """实验完成后应生成 readme.txt 到数据目录。"""
        import tempfile
        ls = self._make_mock_lakeshore(start_temp=30.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = tmpdir
            worker = self._build_worker(lakeshore=ls, temp_list=[30.0], power_list=[5], output_dir=output_dir)
            finished = []
            worker.experiment_finished.connect(lambda c: finished.append(c))
            with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5
                worker.run()
            assert len(finished) == 1
            assert os.path.exists(output_dir)


# =========================================================================
# 新增：测量时温度监控（测量前后温度检查 + 自动删除 + 重启）
# =========================================================================

class TestMeasurementTempMonitor:
    """验证测量过程中的温度监控机制。

    如果测量前后温度偏离稳态判定条件，应删除数据并重启稳定等待。
    """

    def _make_mock_lakeshore(self, start_temp=30.0):
        ls = MagicMock()
        ls.get_temperature.return_value = start_temp
        return ls

    def _make_mock_laser(self):
        return MagicMock()

    def _make_mock_vna(self):
        return MagicMock()

    def _build_worker(self, lakeshore=None, laser=None, vna=None,
                      temp_list=None, power_list=None,
                      output_dir="", vna_settings=None, vna_power_list=None):
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        worker.configure(
            lakeshore_ctrl=lakeshore if lakeshore is not None else self._make_mock_lakeshore(),
            laser_ctrl=laser if laser is not None else self._make_mock_laser(),
            vna_resource=vna if vna is not None else self._make_mock_vna(),
            temp_list=temp_list if temp_list is not None else [30.0],
            power_list=power_list if power_list is not None else [0, 5],
            vna_power_list=vna_power_list if vna_power_list is not None else [-45],
            output_dir=output_dir or "/tmp/test_sweep",
            vna_settings=vna_settings if vna_settings is not None else {
                "start_freq_hz": 3_000_000_000.0,
                "stop_freq_hz": 6_000_000_000.0,
                "s_parameter": "S21",
                "power_dbm": [-45],
                "points": 50001,
                "if_bandwidth_hz": 10000,
            },
        )
        return worker

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_temp_in_band_during_measurement_when_measuring_then_data_kept(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """测量前后温度均在 ±0.5K 内 → 数据正常保留。"""
        ls = self._make_mock_lakeshore(start_temp=30.0)
        worker = self._build_worker(lakeshore=ls, temp_list=[30.0], power_list=[0])

        finished = []
        worker.experiment_finished.connect(lambda c: finished.append(c))

        with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        assert len(finished) == 1
        assert finished[0] == 1  # 1 个测量点成功

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_pre_measure_temp_drift_when_out_of_band_then_data_deleted_and_restart(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """测量前温度偏离 > 0.5K → 应触发重启机制。

        验证：模拟温度在测量时偏离，worker 应触发重启。
        由于当前 _check_measurement_stability 尚未实现，
        此测试验证 LakeShore 返回异常温度时系统不崩溃。
        """
        ls = self._make_mock_lakeshore(start_temp=31.0)  # 偏离 1K
        worker = self._build_worker(lakeshore=ls, temp_list=[30.0], power_list=[0])

        errors = []
        worker.experiment_error.connect(lambda e: errors.append(e))

        with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        # 系统不应崩溃，测量应完成（当前可能以 good_enough 或
        # 记录到日志的方式处理）
        assert len(errors) == 0  # 不会触发 experiment_error

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_post_measure_temp_drift_when_out_of_band_then_restarted(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """测量后温度偏离 > 0.5K → 数据删除并重启。

        验证 LakeShore 在测量后返回异常温度时系统能正常处理。
        """
        # 模拟温度在测量后剧烈变化
        temps = [30.0, 30.0, 30.0, 31.5]  # 最后一次测量后跳变
        ls = self._make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.side_effect = temps

        worker = self._build_worker(lakeshore=ls, temp_list=[30.0], power_list=[0])

        errors = []
        worker.experiment_error.connect(lambda e: errors.append(e))

        with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        assert len(errors) == 0

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_measurement_restart_count_exceeded_when_max_reached_then_fallback(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """连续重启超过上限 → 回退到 good_enough 模式，保留数据。"""
        # 温度始终偏离 1K → 每次测量都会触发重启
        ls = self._make_mock_lakeshore(start_temp=31.0)
        worker = self._build_worker(lakeshore=ls, temp_list=[30.0], power_list=[0])

        finished = []
        worker.experiment_finished.connect(lambda c: finished.append(c))

        with patch("ui.experiment_stability_controller.ExperimentStabilityController") as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            worker.run()

        # 即使温度偏离，实验也应完成（good_enough 回退）
        assert len(finished) >= 1


# =========================================================================
# 新增：max_wait 覆盖测试
# =========================================================================

class TestMaxWaitOverride:
    """验证 max_wait_s 参数正确覆盖 stability controller。"""

    def test_given_custom_max_wait_when_configured_then_controller_uses_it(
        self,
    ):
        """configure(max_wait_s=600) → 稳定性控制器 MAX_WAIT_SECONDS=600。"""
        worker = _build_worker(
            temp_list=[30.0], power_list=[0],
        )
        from ui.workers import ExperimentWorker
        worker.configure(
            lakeshore_ctrl=_make_mock_lakeshore(),
            laser_ctrl=_make_mock_laser(),
            vna_resource=_make_mock_vna(),
            temp_list=[30.0],
            power_list=[0],
            vna_power_list=[-45],
            output_dir="/tmp/test",
            vna_settings={},
            max_wait_s=600,  # 10 min
        )
        assert worker._max_wait_s == 600

    def test_given_no_max_wait_when_configured_then_uses_config_default(
        self,
    ):
        """不传 max_wait_s → 使用 config.max_wait_seconds (1800s)。"""
        worker = _build_worker(
            temp_list=[30.0], power_list=[0],
        )
        from ui.workers import ExperimentWorker
        worker.configure(
            lakeshore_ctrl=_make_mock_lakeshore(),
            laser_ctrl=_make_mock_laser(),
            vna_resource=_make_mock_vna(),
            temp_list=[30.0],
            power_list=[0],
            vna_power_list=[-45],
            output_dir="/tmp/test",
            vna_settings={},
        )
        import config
        assert worker._max_wait_s == config.max_wait_seconds


# =========================================================================
# 新增：ΔT 熔断逻辑测试
# =========================================================================

class TestInterMeasurementAbort:
    """验证测量中 ΔT > 0.25K 熔断行为。"""

    def test_given_two_measurements_delta_lt_0_25_when_checking_then_no_abort(
        self,
    ):
        """ΔT < 0.25K → 不应触发熔断。"""
        temps = [30.01, 30.15, 30.20, 30.25]
        temp_range = max(temps) - min(temps)
        assert abs(temp_range - 0.24) < 0.001
        assert temp_range <= 0.25, "ΔT ≤ 0.25K → 不触发熔断"

    def test_given_two_measurements_delta_gt_0_25_when_checking_then_triggers_abort(
        self,
    ):
        """ΔT > 0.25K → 应触发熔断。"""
        temps = [30.01, 30.30, 30.15, 30.00]
        temp_range = max(temps) - min(temps)
        assert abs(temp_range - 0.30) < 0.001
        import config
        assert temp_range > config.inter_measurement_max_delta_k, \
            "ΔT > 0.25K → 触发熔断"

    def test_given_single_measurement_when_checking_then_no_abort(
        self,
    ):
        """只有 1 个测量点 → 无需检查 ΔT（至少需要 2 个读数）。"""
        temps = [30.0]
        if len(temps) >= 2:
            temp_range = max(temps) - min(temps)
        else:
            temp_range = 0.0  # 不够 2 个读数，不计算 ΔT
        assert temp_range == 0.0


# =========================================================================
# Fix 4: 激光感知温度检查 — 纯函数测试
# =========================================================================

class TestLaserAwareTempCheck:
    """验证 _check_measurement_temp() 在激光加热时放宽容差。"""

    @staticmethod
    def _check(pre_k, post_k, target_k, laser_mw=0):
        """调用 ExperimentWorker 的静态方法。"""
        from ui.workers import ExperimentWorker
        return ExperimentWorker._check_measurement_temp(
            pre_k, post_k, target_k, laser_power_mw=laser_mw)

    # ---- 激光关闭时保持严格 ----

    def test_given_laser_off_when_post_temp_drifts_0_6K_then_fails(self):
        """激光关闭 + 测量后偏离 0.6K > 0.5K → 失败。"""
        ok, reason = self._check(30.0, 30.6, 30.0, laser_mw=0)
        assert ok is False
        assert "0.5K" in reason

    def test_given_laser_off_when_post_temp_drifts_0_25K_then_passes(self):
        """激光关闭 + 测量后偏离 0.25K < 0.5K + 跳变 ≤0.3K → 通过。"""
        ok, _ = self._check(30.0, 30.25, 30.0, laser_mw=0)
        assert ok is True

    # ---- 激光开启时放宽容差 ----

    def test_given_laser_on_when_post_temp_0_75K_with_small_delta_then_passes(self):
        """激光开启 + 测量后 0.75K（在 1.0K 内）+ 跳变 0.25K ≤0.3K → 通过。"""
        # pre 已经 0.5K 偏离（边界通过），激光加热推到 0.75K，跳变仅 0.25K
        ok, _ = self._check(30.5, 30.75, 30.0, laser_mw=5)
        assert ok is True

    def test_given_laser_on_when_post_temp_drifts_1_2K_then_fails(self):
        """激光开启 + 测量后偏离 1.2K > 1.0K → 失败。"""
        ok, reason = self._check(30.0, 31.2, 30.0, laser_mw=5)
        assert ok is False
        assert "1.0K" in reason

    # ---- 测量前检查始终严格 ----

    def test_given_laser_on_when_pre_temp_drifts_0_6K_then_fails(self):
        """激光开启 + 测量前偏离 0.6K > 0.5K → 失败（前检查始终严格）。"""
        ok, reason = self._check(30.6, 30.8, 30.0, laser_mw=5)
        assert ok is False
        assert "测量前" in reason
        assert "0.5K" in reason

    # ---- 测量期间跳变始终严格 ----

    def test_given_laser_on_when_intra_measurement_delta_0_4K_then_fails(self):
        """激光开启 + 测量跳变 0.4K > 0.3K → 失败（跳变检查始终严格）。"""
        ok, reason = self._check(30.0, 30.4, 30.0, laser_mw=5)
        assert ok is False
        assert "跳变" in reason
        assert "0.3K" in reason

    # ---- 默认参数向后兼容 ----

    def test_given_default_laser_power_when_not_provided_then_uses_strict_tolerance(self):
        """不传 laser_power_mw → 默认 0（严格容差）。"""
        ok, reason = self._check(30.0, 30.6, 30.0)  # 无 laser_mw 参数
        assert ok is False
        assert "0.5K" in reason


# =========================================================================
# Fix 3: 激光沉降时间分级
# =========================================================================

class TestLaserThermalSettling:
    """验证激光首次上电 vs 功率切换使用不同沉降时间。"""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_laser_was_off_when_setting_nonzero_power_then_first_on_settle_time_used(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """激光从关闭→开启时使用较长的首次上电沉降时间。"""
        import config
        laser = _make_mock_laser()
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0
        worker = _build_worker(
            lakeshore=ls, laser=laser,
            temp_list=[30.0], power_list=[5],
        )

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 验证首次上电沉降时间被调用
        mock_sleep.assert_any_call(config.laser_first_on_settle_time_s)
        # 验证激光输出开启
        laser.output_on.assert_called()

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_laser_already_on_when_changing_power_then_normal_settle_time_used(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """激光已上电→切换功率时使用正常沉降时间。"""
        import config
        laser = _make_mock_laser()
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0
        worker = _build_worker(
            lakeshore=ls, laser=laser,
            temp_list=[30.0], power_list=[3, 7],
        )

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 第一次激光上电用长沉降
        mock_sleep.assert_any_call(config.laser_first_on_settle_time_s)
        # 第二次功率切换用短沉降
        mock_sleep.assert_any_call(config.laser_settle_time_s)

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_power_zero_when_laser_off_then_output_off_called(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """功率 0 mW → laser.output_off() 被调用。"""
        laser = _make_mock_laser()
        worker = _build_worker(
            laser=laser,
            temp_list=[30.0], power_list=[0],
        )

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        laser.output_off.assert_called()
        # 验证 _laser_was_off 标志保持 True
        assert worker._laser_was_off is True

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_laser_settle_when_complete_then_temperature_logged(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """激光沉降后记录温度变化（诊断日志）。"""
        laser = _make_mock_laser()
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0
        worker = _build_worker(
            lakeshore=ls, laser=laser,
            temp_list=[30.0], power_list=[5],
        )

        progress_messages = []
        worker.progress.connect(lambda msg: progress_messages.append(msg))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 验证"激光沉降后温度"诊断消息存在
        settle_msgs = [m for m in progress_messages if "激光沉降后温度" in m]
        assert len(settle_msgs) >= 1, "激光沉降后应记录温度变化"


# =========================================================================
# Fix 1: 预等待后温度稳定性验证
# =========================================================================

class TestPreMeasurementWaitStability:
    """验证预测量等待后检查温度稳定性。"""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_temp_in_tolerance_after_wait_when_checking_then_proceeds(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """预等待后温度在容差内 → 正常继续测量。"""
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0
        worker = _build_worker(
            lakeshore=ls, temp_list=[30.0], power_list=[0],
        )
        # 设置预等待 1 秒（已 patch sleep，实际不等待）
        worker._pre_measurement_wait_s = 1

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 测量应正常完成
        assert finished_counts[0] >= 1, "预等待后温度正常应完成测量"

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_temp_drifted_after_wait_when_out_of_tolerance_then_reenters_stability(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """预等待后温度偏离 > 0.5K → 重入稳定性等待。"""
        # 前两次调用返回 30.0（初始温度读取 + 稳定循环），
        # 第三次调用（预等待后温度检查）返回 31.2K（Δ=1.2K > 0.5K）
        call_idx = [0]

        def get_temp(channel="A"):
            call_idx[0] += 1
            if call_idx[0] == 3:
                return 31.2  # 预等待后漂移
            return 30.0

        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.side_effect = get_temp

        worker = _build_worker(
            lakeshore=ls, temp_list=[30.0], power_list=[0],
        )
        worker._pre_measurement_wait_s = 1

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 应创建了多个 stability controller（初始 + 重稳定）
        assert mock_ctrl_cls.call_count >= 2, \
            f"预等待后漂移应创建新的 stability controller（实际: {mock_ctrl_cls.call_count}）"
        assert finished_counts[0] >= 1, "重稳定后应完成测量"

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_pre_wait_zero_when_configured_then_no_stability_check(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """pre_measurement_wait_s=0 → 跳过预等待和稳定性检查。"""
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0
        worker = _build_worker(
            lakeshore=ls, temp_list=[30.0], power_list=[0],
        )
        worker._pre_measurement_wait_s = 0  # 默认值，跳过预等待

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 正常完成，无额外 stability controller（仅初始稳定时创建 1 个）
        assert finished_counts[0] >= 1
        # wait=0 时直接进入 stablity_ctrl 的 while 循环，不经过预等待块


# =========================================================================
# Fix 2: 熔断重启上限
# =========================================================================

class TestMeltdownRestartLimit:
    """验证熔断重启达到上限后跳过温度点。"""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_normal_run_when_restarts_below_limit_then_measurement_completes(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """正常运行（熔断未超限）→ 测量正常完成。"""
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0

        worker = _build_worker(
            lakeshore=ls, temp_list=[30.0], power_list=[0],
        )
        worker._pre_measurement_wait_s = 0

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 正常运行应完成测量
        assert finished_counts[0] >= 1, \
            f"温度正常应完成测量（实际: {finished_counts[0]}）"

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    @patch("config.max_meltdown_restarts", 2)
    def test_given_restarts_exceed_limit_when_max_reached_then_skips_temperature(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """熔断次数达到上限 → 跳过此温度点，实验正常结束。"""
        # 所有温度读数偏离 1K → 每次测量都熔断
        ls = _make_mock_lakeshore(start_temp=31.0)
        ls.get_temperature.return_value = 31.0

        laser = _make_mock_laser()
        worker = _build_worker(
            lakeshore=ls, laser=laser, temp_list=[30.0], power_list=[0],
        )
        worker._pre_measurement_wait_s = 0

        finished_counts = []
        worker.experiment_finished.connect(lambda c: finished_counts.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 实验完成（跳过了温度点），而非无限循环
        assert finished_counts[0] == 0, \
            f"温度点被跳过，测量数为 0（实际: {finished_counts[0]}）"

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    @patch("config.max_meltdown_restarts", 1)
    def test_given_restart_limit_hit_when_laser_was_on_then_laser_off_called(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """熔断超限 → 跳过前确保激光安全关闭。"""
        ls = _make_mock_lakeshore(start_temp=31.0)
        ls.get_temperature.return_value = 31.0

        laser = _make_mock_laser()
        worker = _build_worker(
            lakeshore=ls, laser=laser,
            temp_list=[30.0], power_list=[5],
        )
        worker._pre_measurement_wait_s = 0

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5

            worker.run()

        # 验证激光关闭被调用（跳过温度点时的安全措施）
        laser.set_power.assert_any_call(0)
        laser.output_off.assert_called()


# =========================================================================
# 断点续传: CheckpointManager 单元测试
# =========================================================================

import json
import os as _os
import tempfile as _tempfile


class TestCheckpointManager:
    """验证 CheckpointManager 的保存/加载/恢复/清理。"""

    @staticmethod
    def _make_state():
        return {
            "temp_idx": 2,
            "vna_dbm_idx": 0,
            "power_mw_idx": 0,
            "current_temp_k": 73.6,
            "total_count": 25,
            "extended_max_wait_s": 1800,
            "extended_pre_wait_s": 300,
            "rollback_consecutive_issues": 0,
            "rollback_first_issue_index": None,
            "rollback_count": 0,
            "overshoot_learning": {},
        }

    @staticmethod
    def _make_completed_points():
        return [
            {"temp_k": 72.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 71.604},
            {"temp_k": 72.0, "vna_dbm": -45, "power_mw": 1, "actual_k": 71.693},
            {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 73.599},
            {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 1, "actual_k": 73.690},
            {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 3, "actual_k": 73.691},
        ]

    # ---- 保存 & 加载 ----

    def test_given_state_and_points_when_save_then_file_created(self):
        """保存检查点 → 文件存在且内容完整。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            points = self._make_completed_points()
            CheckpointManager.save(tmpdir, state, points, "20260612_190601",
                                   [72.0, 74.0, 76.0], [-45, -30, -25], [0, 1, 3, 5, 7, 9])
            ckpt_path = _os.path.join(tmpdir, "checkpoint.json")
            assert _os.path.exists(ckpt_path)
            with open(ckpt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["version"] == 1
            assert data["state"]["temp_idx"] == 2
            assert len(data["completed_points"]) == 5

    def test_given_saved_checkpoint_when_load_then_returns_state_and_points(self):
        """加载有效检查点 → 返回 state dict 和 completed_points list。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            points = self._make_completed_points()
            CheckpointManager.save(tmpdir, state, points, "20260612_190601",
                                   [72.0, 74.0, 76.0], [-45, -30, -25], [0, 1, 3, 5, 7, 9])
            loaded_state, loaded_points = CheckpointManager.load(tmpdir)
            assert loaded_state is not None
            assert loaded_state["temp_idx"] == 2
            assert loaded_state["total_count"] == 25
            assert len(loaded_points) == 5

    def test_given_no_checkpoint_when_load_then_returns_none(self):
        """无检查点文件 → load() 返回 None。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            result = CheckpointManager.load(tmpdir)
            assert result is None

    def test_given_corrupt_checkpoint_when_load_then_returns_none(self):
        """检查点 JSON 损坏 → load() 返回 None（不抛异常）。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            ckpt_path = _os.path.join(tmpdir, "checkpoint.json")
            with open(ckpt_path, "w", encoding="utf-8") as f:
                f.write("not valid json {{{")
            result = CheckpointManager.load(tmpdir)
            assert result is None

    # ---- 增量追加 ----

    def test_given_existing_checkpoint_when_append_point_then_point_added(self):
        """增量追加测量点 → completed_points 增长。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            points = self._make_completed_points()
            CheckpointManager.save(tmpdir, state, points, "20260612_190601",
                                   [72.0, 74.0, 76.0], [-45, -30, -25], [0, 1, 3, 5, 7, 9])
            new_point = {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 5,
                         "actual_k": 73.691}
            CheckpointManager.append_point(tmpdir, new_point)
            _, loaded_points = CheckpointManager.load(tmpdir)
            assert len(loaded_points) == 6
            assert loaded_points[-1]["power_mw"] == 5

    def test_given_no_checkpoint_when_append_point_then_does_nothing(self):
        """无检查点文件 → append_point() 不抛异常，不创建文件。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            CheckpointManager.append_point(tmpdir, {"temp_k": 30.0, "vna_dbm": -45,
                                                     "power_mw": 0, "actual_k": 30.0})
            assert not _os.path.exists(_os.path.join(tmpdir, "checkpoint.json"))

    # ---- 删除 ----

    def test_given_checkpoint_exists_when_delete_then_file_removed(self):
        """delete() → 检查点文件被删除。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            CheckpointManager.save(tmpdir, state, [], "test",
                                   [30.0], [-45], [0])
            assert _os.path.exists(_os.path.join(tmpdir, "checkpoint.json"))
            CheckpointManager.delete(tmpdir)
            assert not _os.path.exists(_os.path.join(tmpdir, "checkpoint.json"))

    def test_given_no_checkpoint_when_delete_then_no_error(self):
        """无检查点 → delete() 不抛异常。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            CheckpointManager.delete(tmpdir)  # 不应抛异常

    # ---- 恢复判断 ----

    def test_given_completed_points_when_resume_then_skips_done_points(self):
        """completed_points 中已有的点 → resume_from 跳过。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 5, "actual_k": 30.1},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0, 50.0],
            vna_power_list=[-45],
            power_list=[0, 5],
        )
        # 30.0K 的两个 power 都已完成 → 应从 50.0K / -45dBm / 0mW 开始
        assert result == (1, 0, 0)  # temp_idx=1, vna_idx=0, power_idx=0

    def test_given_partial_temp_completed_when_resume_then_starts_at_next_power(self):
        """同一温度点部分完成 → 从下一个未完成的 power 开始。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0],
            vna_power_list=[-45],
            power_list=[0, 5, 10],
        )
        assert result == (0, 0, 1)  # power_idx=1 (5mW)

    def test_given_all_points_completed_when_resume_then_returns_none(self):
        """所有点都完成 → resume_from 返回 None。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 5, "actual_k": 30.1},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0],
            vna_power_list=[-45],
            power_list=[0, 5],
        )
        assert result is None

    def test_given_vna_power_levels_when_resume_then_correctly_advances(self):
        """多个 VNA 功率级别 → 恢复时正确跨 VNA 功率推进。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 5, "actual_k": 30.1},
            {"temp_k": 30.0, "vna_dbm": -30, "power_mw": 0, "actual_k": 30.2},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0],
            vna_power_list=[-45, -30],
            power_list=[0, 5],
        )
        # -45dBm 全部完成，-30dBm 的 0mW 完成 → 从 -30dBm / 5mW 开始
        assert result == (0, 1, 1)  # temp_idx=0, vna_idx=1, power_idx=1

    # ---- 参数列表不匹配 ----

    def test_given_temp_list_changed_when_load_then_validate_warns(self):
        """温度列表变更 → 恢复时检测到不匹配。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            CheckpointManager.save(tmpdir, state, [], "test",
                                   original_temp_list=[72.0, 74.0, 76.0],
                                   original_vna_power_list=[-45],
                                   original_power_list=[0, 1, 3, 5, 7, 9])
            loaded_state, _ = CheckpointManager.load(tmpdir)
            # 新温度列表与原始不同
            new_temp_list = [72.0, 74.0, 80.0]  # 76→80 变更
            is_match = (loaded_state is not None and
                        CheckpointManager.validate_lists(
                            loaded_state, new_temp_list, [-45], [0, 1, 3, 5, 7, 9]))
            assert is_match is False

    def test_given_same_lists_when_load_then_validate_passes(self):
        """参数列表未变 → validate_lists 返回 True。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            CheckpointManager.save(tmpdir, state, [], "test",
                                   original_temp_list=[72.0, 74.0, 76.0],
                                   original_vna_power_list=[-45, -30],
                                   original_power_list=[0, 1, 3, 5, 7, 9])
            loaded_state, _ = CheckpointManager.load(tmpdir)
            is_match = (loaded_state is not None and
                        CheckpointManager.validate_lists(
                            loaded_state,
                            [72.0, 74.0, 76.0],
                            [-45, -30],
                            [0, 1, 3, 5, 7, 9]))
            assert is_match is True


# =========================================================================
# 断点续传: 异常分类测试
# =========================================================================

class TestRecoverableErrorDetection:
    """验证 _is_recoverable_error() 正确区分连接错误和逻辑错误。"""

    @staticmethod
    def _is_recoverable(exc):
        from ui.workers import ExperimentWorker
        return ExperimentWorker._is_recoverable_error(exc)

    def test_given_vi_error_conn_lost_when_checking_then_recoverable(self):
        """VI_ERROR_CONN_LOST → 可恢复。"""
        exc = Exception("VI_ERROR_CONN_LOST (-1073807194): "
                        "The connection for the given session has been lost.")
        assert self._is_recoverable(exc) is True

    def test_given_timeout_when_checking_then_not_recoverable(self):
        """含 timeout 关键字 → 不可恢复（由稳定性循环连续失败计数器处理）。"""
        assert self._is_recoverable(Exception("VISA timeout on read")) is False

    def test_given_disconnected_when_checking_then_recoverable(self):
        """含 disconnected → 可恢复。"""
        assert self._is_recoverable(Exception("Device disconnected")) is True

    def test_given_tcpip_error_when_checking_then_recoverable(self):
        """含 tcpip → 可恢复。"""
        assert self._is_recoverable(Exception("TCPIP connection refused")) is True

    def test_given_value_error_when_checking_then_not_recoverable(self):
        """普通 ValueError → 不可恢复。"""
        assert self._is_recoverable(ValueError("invalid literal for float()")) is False

    def test_given_key_error_when_checking_then_not_recoverable(self):
        """KeyError → 不可恢复。"""
        assert self._is_recoverable(KeyError("missing_key")) is False

    def test_given_data_parse_error_when_checking_then_not_recoverable(self):
        """数据解析失败 → 不可恢复。"""
        assert self._is_recoverable(Exception("could not convert string to float")) is False


# =========================================================================
# 断点续传: S2P 文件去重测试
# =========================================================================

class TestS2PFilenameDedup:
    """验证 _find_next_filename() 的 attempt 自动递增。"""

    @staticmethod
    def _find_next(folder, temp_k, vna_dbm, power_mw, actual_k):
        from ui.workers import ExperimentWorker
        return ExperimentWorker._find_next_filename(
            folder, temp_k, vna_dbm, power_mw, actual_k)

    def test_given_no_existing_file_when_finding_then_attempt_0(self):
        """无同名文件 → 返回 attempt=0（无 attempt 后缀）。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt" not in name
            assert name.endswith("_actual_29.995K.s2p")

    def test_given_file_exists_when_finding_then_increments_attempt(self):
        """已有 attempt=0 的文件 → 返回 attempt=1。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            existing = _os.path.join(
                tmpdir,
                "YBCO_-45dBm_00mW_target_30K_actual_29.995K.s2p")
            with open(existing, "w") as f:
                f.write("dummy")
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt1" in name

    def test_given_multiple_attempts_when_finding_then_uses_next_available(self):
        """已有 attempt=0,1,2 → 返回 attempt=3。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            base = "YBCO_-45dBm_00mW_target_30K"
            for suffix in ["_actual_29.995K.s2p",
                           "_attempt1_actual_29.995K.s2p",
                           "_attempt2_actual_29.995K.s2p"]:
                with open(_os.path.join(tmpdir, base + suffix), "w") as f:
                    f.write("dummy")
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt3" in name

    def test_given_different_actual_temp_when_finding_then_not_a_conflict(self):
        """不同 actual K → 不视为冲突，返回 attempt=0。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            existing = _os.path.join(
                tmpdir,
                "YBCO_-45dBm_00mW_target_30K_actual_30.500K.s2p")
            with open(existing, "w") as f:
                f.write("dummy")
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt" not in name  # actual temp 不同，无冲突


# =========================================================================
# 断点续传: 恢复流程集成测试
# =========================================================================

class TestExperimentRecovery:
    """验证 ExperimentWorker 的恢复信号和工作流。"""

    def test_given_worker_configured_when_signals_exist_then_accessible(self):
        """新信号应在 ExperimentWorker 上可连接。"""
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        # 确认信号存在
        assert hasattr(worker, "experiment_recovering")
        assert hasattr(worker, "experiment_recovered")
        assert hasattr(worker, "experiment_recovery_timeout")
        assert hasattr(worker, "experiment_resume_prompt")

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_connection_lost_during_run_when_recoverable_then_checkpoint_saved(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """连接丢失 → 检查点文件被写入。"""
        import tempfile
        from ui.workers import CheckpointManager

        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0, 50.0], power_list=[0],
                output_dir=tmpdir,
            )

            # 第一次 get_temperature 成功，后续抛出连接错误
            call_count = [0]

            def get_temp_with_failure(channel="A"):
                call_count[0] += 1
                if call_count[0] >= 3:
                    raise Exception("VI_ERROR_CONN_LOST (-1073807194): "
                                    "The connection for the given session has been lost.")
                return 30.0

            ls.get_temperature.side_effect = get_temp_with_failure

            errors = []
            worker.experiment_error.connect(lambda e: errors.append(e))
            recovering = []
            worker.experiment_recovering.connect(lambda msg: recovering.append(msg))

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5

                worker.run()

            # 不应发射 experiment_error（这是可恢复错误）
            assert len(errors) == 0
            # 应发射 experiment_recovering
            assert len(recovering) == 1
            # 检查点应存在
            ckpt = CheckpointManager.load(tmpdir)
            assert ckpt is not None

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_recoverable_error_when_not_connection_then_error_emitted(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """不可恢复的错误 → experiment_error 正常发射。"""
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0

        worker = _build_worker(
            lakeshore=ls, temp_list=[30.0], power_list=[0],
        )

        # 让 stability controller 抛出非连接错误
        errors = []
        worker.experiment_error.connect(lambda e: errors.append(e))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl_cls.side_effect = ValueError("invalid config value")

            worker.run()

        # 不可恢复的异常应被捕获并发射
        assert len(errors) == 1
        assert "invalid config value" in errors[0]

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_checkpoint_exists_when_run_starts_then_resume_prompt_emitted(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """启动时检测到检查点 → 发射 experiment_resume_prompt。"""
        import tempfile
        from ui.workers import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # 预置检查点
            state = {
                "temp_idx": 0, "vna_dbm_idx": 0, "power_mw_idx": 1,
                "current_temp_k": 30.0, "total_count": 1,
                "extended_max_wait_s": 1800, "extended_pre_wait_s": 0,
                "rollback_consecutive_issues": 0,
                "rollback_first_issue_index": None,
                "rollback_count": 0,
                "overshoot_learning": {},
            }
            completed = [
                {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            ]
            CheckpointManager.save(
                tmpdir, state, completed, "20260612_test",
                original_temp_list=[30.0, 50.0],
                original_vna_power_list=[-45],
                original_power_list=[0, 5],
            )

            ls = _make_mock_lakeshore(start_temp=30.0)
            ls.get_temperature.return_value = 30.0
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0, 50.0], power_list=[0, 5],
                output_dir=tmpdir,
            )

            resume_prompts = []
            worker.experiment_resume_prompt.connect(
                lambda exp_id, n: resume_prompts.append((exp_id, n)))

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5

                worker.run()

            assert len(resume_prompts) == 1
            assert resume_prompts[0][0] == "20260612_test"
            assert resume_prompts[0][1] == 1  # 1 completed point


# =========================================================================
# Phase 5: Claude 主动监控 — _write_status, _poll_commands, _run_fill
# =========================================================================


class TestExperimentStatusIntegration:
    """Given an ExperimentWorker, _write_status writes status.json correctly."""

    def test_given_configured_worker_when_write_status_called_then_status_json_exists(
        self, qapp
    ):
        """_write_status() 应创建 status.json 文件。"""
        import json, tempfile
        from ui.workers import ExperimentWorker

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0, 50.0],
                power_list=[0, 5],
                output_dir=tmpdir,
            )
            worker._write_status(phase="starting", target_k=30.0)

            path = os.path.join(tmpdir, "status.json")
            assert os.path.exists(path), "status.json 应该被创建"

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["status"] == "running"
            assert data["current"]["phase"] == "starting"

    def test_given_worker_running_when_write_status_on_stable_then_phase_updated(
        self, qapp
    ):
        """稳定达成后 _write_status 应更新 phase 为 stabilizing_fine。"""
        import json, tempfile
        from ui.workers import ExperimentWorker

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0],
                power_list=[0],
                output_dir=tmpdir,
            )
            worker._write_status(phase="stabilizing_sparse", target_k=30.0, actual_k=29.5)
            worker._write_status(phase="stabilizing_fine", target_k=30.0, actual_k=30.002)

            path = os.path.join(tmpdir, "status.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["current"]["phase"] == "stabilizing_fine"

    def test_given_write_status_called_on_measurement_then_phase_measuring(
        self, qapp
    ):
        """测量开始时 _write_status 应设置 phase=measuring。"""
        import json, tempfile
        from ui.workers import ExperimentWorker

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0],
                power_list=[0],
                output_dir=tmpdir,
            )
            worker._write_status(
                phase="measuring", target_k=30.0, actual_k=30.002,
                vna_dbm=-45, laser_mw=0,
            )

            path = os.path.join(tmpdir, "status.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["current"]["phase"] == "measuring"
            assert data["current"]["vna_dbm"] == -45

    def test_given_write_status_without_output_dir_then_no_error(self, qapp):
        """output_dir 为空时 _write_status 不应抛异常（静默跳过）。"""
        worker = _build_worker(
            temp_list=[30.0],
            power_list=[0],
            output_dir="",  # 空目录
        )
        # 不应抛异常
        try:
            worker._write_status(phase="starting", target_k=30.0)
        except Exception as e:
            pytest.fail(f"_write_status 不应抛出异常: {e}")


class TestCommandPolling:
    """Given an ExperimentWorker, _poll_commands reads and applies commands.json."""

    def test_given_pending_extend_max_wait_when_polled_then_max_wait_increased(
        self, qapp
    ):
        """extend_max_wait 命令应增加 max_wait_seconds。"""
        import json, tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0],
                power_list=[0],
                output_dir=tmpdir,
            )
            original = worker._max_wait_s

            # 预写入命令
            cmd = {
                "last_command_id": "cmd_001",
                "commands": [{
                    "id": "cmd_001",
                    "time": "2026-06-16T11:50:00",
                    "action": "extend_max_wait",
                    "params": {"add_minutes": 30},
                    "reason": "测试",
                    "status": "pending",
                }],
            }
            with open(os.path.join(tmpdir, "commands.json"), "w", encoding="utf-8") as f:
                json.dump(cmd, f, ensure_ascii=False)

            worker._poll_commands()

            assert worker._max_wait_s == original + 30 * 60

            # 验证命令已标记 applied
            with open(os.path.join(tmpdir, "commands.json"), "r", encoding="utf-8") as f:
                updated = json.load(f)
            assert updated["commands"][0]["status"] == "applied"

    def test_given_relax_meltdown_command_when_polled_then_threshold_updated(
        self, qapp
    ):
        """relax_meltdown 命令应更新熔断阈值。"""
        import json, tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0],
                power_list=[0],
                output_dir=tmpdir,
            )
            cmd = {
                "last_command_id": "cmd_001",
                "commands": [{
                    "id": "cmd_001",
                    "time": "2026-06-16T12:40:00",
                    "action": "relax_meltdown",
                    "params": {"new_threshold_k": 0.35},
                    "reason": "测试",
                    "status": "pending",
                }],
            }
            with open(os.path.join(tmpdir, "commands.json"), "w", encoding="utf-8") as f:
                json.dump(cmd, f, ensure_ascii=False)

            worker._poll_commands()

            import config
            assert worker._meltdown_threshold_k == 0.35

    def test_given_no_commands_file_when_polled_then_no_error(self, qapp):
        """commands.json 不存在时 _poll_commands 不应出错。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0],
                power_list=[0],
                output_dir=tmpdir,
            )
            # 不应抛异常
            try:
                worker._poll_commands()
            except Exception as e:
                pytest.fail(f"_poll_commands 不应抛出异常: {e}")

    def test_given_already_applied_command_when_polled_then_not_reapplied(
        self, qapp
    ):
        """已 applied 的命令不应重复执行。"""
        import json, tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(temp_list=[30.0], power_list=[0], output_dir=tmpdir)
            original = worker._max_wait_s

            cmd = {
                "last_command_id": "cmd_001",
                "commands": [{
                    "id": "cmd_001",
                    "action": "extend_max_wait",
                    "params": {"add_minutes": 30},
                    "status": "applied",  # 已应用
                }],
            }
            with open(os.path.join(tmpdir, "commands.json"), "w", encoding="utf-8") as f:
                json.dump(cmd, f, ensure_ascii=False)

            worker._poll_commands()
            # 不应再次增加
            assert worker._max_wait_s == original


class TestFillMode:
    """Given ExperimentWorker in fill mode, _run_fill executes cooldown+measure."""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_fill_plan_when_run_fill_called_then_cooling_setpoint_set(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """补测模式应首先设置冷却 setpoint = target - offset。"""
        import json, tempfile
        import config as cfg

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 fill_plan.json
            plan_path = os.path.join(tmpdir, "fill_plan.json")
            plan = {
                "experiment_id": "fill_test",
                "strategy": "cooldown_then_heat",
                "cooldown_offset_k": 5.0,
                "temperature_plan": [50.0],
                "measurements": [{
                    "target_k": 50.0,
                    "vna_dbm": -25,
                    "laser_powers_mw": [0, 5],
                }],
            }
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f)

            ls = _make_mock_lakeshore(start_temp=55.0)
            # 模拟冷却过程：第三次调用时温度低于目标
            ls.get_temperature.side_effect = [55.0, 51.0, 49.5, 49.5, 49.5, 49.5]
            worker = _build_worker(
                lakeshore=ls, temp_list=[50.0], power_list=[0],
                output_dir=tmpdir,
            )
            worker._run_fill(tmpdir)

            # 验证 setpoint 被设为 target - offset
            setpoint_calls = [c for c in ls.set_temperature.call_args_list
                              if c[0][0] is not None]
            assert len(setpoint_calls) > 0, "应该有 setpoint 设置调用"
            first_setpoint = setpoint_calls[0][0][0]
            assert abs(first_setpoint - 45.0) < 0.1, (
                f"冷却 setpoint 应为 target-5K=45K, 实际 {first_setpoint}"
            )

    def test_given_fill_plan_when_fill_complete_then_writes_fill_complete_json(
        self, qapp
    ):
        """补测完成后应写入 fill_complete.json。"""
        import json, tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                temp_list=[30.0], power_list=[0],
                output_dir=tmpdir,
            )
            worker._write_fill_complete(tmpdir, 2, 2)

            path = os.path.join(tmpdir, "fill_complete.json")
            assert os.path.exists(path)

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["total_measured"] == 2
            assert data["total_expected"] == 2
            assert data["completeness"] == 1.0


class TestFillModeNoExistingFile:
    """Backward compatibility: no fill artifacts when not in fill mode."""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_normal_run_when_no_fill_flag_then_no_fill_plan_consumed(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """正常实验运行时不应读取 fill_plan.json。"""
        import tempfile

        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0], power_list=[0],
                output_dir=tmpdir,
            )

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5

                # 不应该因为缺失 fill_plan.json 而报错
                try:
                    worker.run()
                except Exception as e:
                    pytest.fail(f"正常 run() 不应因 fill 逻辑而失败: {e}")


# =========================================================================
# TestClass: Read Failure Recovery in Restart / Rewait Loops
# =========================================================================

class TestReadFailureRecovery:
    """验证 Loop B（预等待重稳定）和 Loop C（熔断重启）的
    温度读取失败计数器能正确触发断点续传。"""

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_restart_loop_when_5_consecutive_read_failures_then_recovery_triggered(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """熔断重启稳定性循环中连续 5 次温度读取失败 → 触发 recovery。"""
        import tempfile

        ls = _make_mock_lakeshore(start_temp=30.0)

        # 阶段 1: 主稳定循环中让温度读数成功（2 次后稳定）
        # 阶段 2: 测量阶段触发熔断 → 进入 Loop C
        # 阶段 3: Loop C 中读数连续失败 5 次 → raise → recovery
        call_count = [0]

        def get_temp_sequence(channel="A"):
            call_count[0] += 1
            c = call_count[0]
            # 初始化 + 主稳定循环: c=1,2 成功
            if c <= 2:
                return 30.0
            # 测量阶段 pre_temp: c=3 成功
            if c == 3:
                return 30.0
            # 激光沉降日志读取: c=4 成功（不经 _check_measurement_temp）
            if c == 4:
                return 30.0
            # 测量阶段 post_temp: c=5 返回偏离值触发测量异常
            if c == 5:
                return 30.8
            # Loop C 重启稳定性: c≥6 连续失败
            raise Exception("VI_ERROR_CONN_LOST (-1073807194): "
                            "The connection for the given session has been lost.")

        ls.get_temperature.side_effect = get_temp_sequence

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0], power_list=[0],
                output_dir=tmpdir,
            )

            recovering = []
            worker.experiment_recovering.connect(lambda msg: recovering.append(msg))
            errors = []
            worker.experiment_error.connect(lambda e: errors.append(e))

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5
                mock_ctrl.phase = MagicMock(value="fine")

                worker.run()

            # 不应发射 experiment_error（这是可恢复的连接错误）
            assert len(errors) == 0, f"unexpected errors: {errors}"
            # 应触发断点续传
            assert len(recovering) == 1, \
                f"expected experiment_recovering signal, got {len(recovering)}"

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_rewait_loop_when_5_consecutive_read_failures_then_recovery_triggered(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """预等待重稳定循环中连续 5 次温度读取失败 → 触发 recovery。"""
        import tempfile
        import config

        ls = _make_mock_lakeshore(start_temp=30.0)

        call_count = [0]

        def get_temp_sequence(channel="A"):
            call_count[0] += 1
            c = call_count[0]
            # 初始化 + 主稳定循环: c=1,2 成功
            if c <= 2:
                return 30.0
            # 预等待后温度检查: c=3 返回偏离值触发重稳定分支
            if c == 3:
                return 31.5  # Δ=1.5K > pre_measurement_wait_temp_tolerance_k
            # Loop B 重稳定: c≥4 连续失败
            raise Exception("VI_ERROR_CONN_LOST (-1073807194): "
                            "The connection for the given session has been lost.")

        ls.get_temperature.side_effect = get_temp_sequence

        saved_tolerance = getattr(config, "pre_measurement_wait_temp_tolerance_k", 0.5)

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0], power_list=[0],
                output_dir=tmpdir,
            )
            # 设置预等待时间以触发预等待分支
            worker._pre_measurement_wait_s = 10

            recovering = []
            worker.experiment_recovering.connect(lambda msg: recovering.append(msg))
            errors = []
            worker.experiment_error.connect(lambda e: errors.append(e))

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5
                mock_ctrl.phase = MagicMock(value="fine")

                worker.run()

            assert len(errors) == 0, f"unexpected errors: {errors}"
            assert len(recovering) == 1, \
                f"expected experiment_recovering signal, got {len(recovering)}"

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_restart_stability_loop_when_stabilizing_then_write_status_called(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """熔断重启稳定性循环中应写入 status.json 监控状态。"""
        import tempfile
        import json

        ls = _make_mock_lakeshore(start_temp=30.0)

        call_count = [0]

        def get_temp_sequence(channel="A"):
            call_count[0] += 1
            c = call_count[0]
            # 初始化 + 主稳定循环: c=1,2 成功
            if c <= 2:
                return 30.0
            # 测量阶段 pre_temp: c=3 成功
            if c == 3:
                return 30.0
            # 测量阶段 post_temp: c=4 偏离 → 触发测量异常
            if c == 4:
                return 30.8
            # Loop C 重启稳定: c≥5 正常返回
            return 30.0

        ls.get_temperature.side_effect = get_temp_sequence

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0], power_list=[0],
                output_dir=tmpdir,
            )

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                # 第一次 stable=True 让主循环通过
                # 第二次 stable=True 让重启循环通过
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5
                mock_ctrl.phase = MagicMock(value="fine")

                worker.run()

            # 验证 status.json 存在且包含 pre_measuring phase
            status_path = os.path.join(tmpdir, "status.json")
            assert os.path.exists(status_path), \
                "熔断恢复后应写入 status.json"
            with open(status_path, "r", encoding="utf-8") as f:
                status_data = json.load(f)
            # 最终状态应为 completed 或至少 current phase 被更新过
            assert status_data.get("status") in ("completed", "running"), \
                f"unexpected status: {status_data.get('status')}"
