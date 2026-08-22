# -*- coding: utf-8 -*-
"""S21Trace 和 ScanSummary 数据模型的单元测试。

BDD 命名: test_given_<前提>_when_<操作>_then_<预期>
"""

import numpy as np
import pytest


class TestS21Trace:
    """S21Trace 数据类的构造与属性测试。"""

    def test_given_valid_params_when_constructing_then_all_fields_stored(
        self,
    ):
        from plot_dashboard.data_model import S21Trace

        freq = np.linspace(3e9, 6e9, 201)
        s21 = np.random.randn(201) + 1j * np.random.randn(201)
        trace = S21Trace(
            file_path="/data/20260605_215526/6K/actual_6.452K/-25dBm/00mW/YBCO.s2p",
            timestamp="20260605_215526",
            target_temp_k=6.0,
            actual_temp_k=6.452,
            vna_power_dbm=-25,
            laser_power_mw=0,
            frequency_hz=freq,
            s21_db=20 * np.log10(np.abs(s21)),
        )

        assert trace.file_path == "/data/20260605_215526/6K/actual_6.452K/-25dBm/00mW/YBCO.s2p"
        assert trace.timestamp == "20260605_215526"
        assert trace.target_temp_k == 6.0
        assert trace.actual_temp_k == 6.452
        assert trace.vna_power_dbm == -25
        assert trace.laser_power_mw == 0
        assert len(trace.frequency_hz) == 201
        assert len(trace.s21_db) == 201

    def test_given_has_actual_temp_when_accessing_temp_k_then_returns_actual(
        self,
    ):
        from plot_dashboard.data_model import S21Trace

        trace = S21Trace(
            file_path="",
            timestamp="",
            target_temp_k=6.0,
            actual_temp_k=6.452,
            vna_power_dbm=-25,
            laser_power_mw=0,
            frequency_hz=np.array([]),
            s21_db=np.array([]),
        )

        assert trace.temp_k == 6.452

    def test_given_none_actual_temp_when_accessing_temp_k_then_falls_back_to_target(
        self,
    ):
        from plot_dashboard.data_model import S21Trace

        trace = S21Trace(
            file_path="",
            timestamp="",
            target_temp_k=6.0,
            actual_temp_k=None,
            vna_power_dbm=-25,
            laser_power_mw=0,
            frequency_hz=np.array([]),
            s21_db=np.array([]),
        )

        assert trace.temp_k == 6.0

    def test_given_negative_vna_power_when_constructing_then_stored_correctly(
        self,
    ):
        from plot_dashboard.data_model import S21Trace

        trace = S21Trace(
            file_path="",
            timestamp="",
            target_temp_k=30.0,
            actual_temp_k=None,
            vna_power_dbm=-50,
            laser_power_mw=17,
            frequency_hz=np.array([]),
            s21_db=np.array([]),
        )

        assert trace.vna_power_dbm == -50
        assert trace.laser_power_mw == 17

    def test_given_two_traces_with_same_params_when_comparing_then_equality_by_fields(
        self,
    ):
        from plot_dashboard.data_model import S21Trace

        freq = np.array([1e9, 2e9])
        s21 = np.array([-10.0, -20.0])
        t1 = S21Trace("a", "", 10.0, 9.5, -25, 0, freq, s21)
        t2 = S21Trace("a", "", 10.0, 9.5, -25, 0, freq, s21)

        assert t1.file_path == t2.file_path
        assert np.array_equal(t1.frequency_hz, t2.frequency_hz)

    def test_given_s21_trace_when_repr_then_includes_key_params(self):
        from plot_dashboard.data_model import S21Trace

        trace = S21Trace(
            file_path="/data/test.s2p",
            timestamp="20260101_000000",
            target_temp_k=30.0,
            actual_temp_k=29.8,
            vna_power_dbm=-25,
            laser_power_mw=5,
            frequency_hz=np.linspace(3e9, 6e9, 100),
            s21_db=np.zeros(100),
        )

        r = repr(trace)
        assert "30.0K" in r
        assert "29.8K" in r
        assert "-25dBm" in r
        assert "5mW" in r


class TestScanSummary:
    """ScanSummary 数据类的测试。"""

    def test_given_empty_scan_when_creating_summary_then_defaults_are_sensible(
        self,
    ):
        from plot_dashboard.data_model import ScanSummary

        s = ScanSummary()
        assert s.root_dir == ""
        assert s.total_files == 0
        assert s.vna_powers == set()
        assert s.laser_powers == set()
        assert s.temp_min_k is None
        assert s.temp_max_k is None

    def test_given_summary_with_data_when_adding_trace_then_stats_updated(
        self,
    ):
        from plot_dashboard.data_model import ScanSummary, S21Trace

        s = ScanSummary()
        trace = S21Trace(
            file_path="",
            timestamp="",
            target_temp_k=6.0,
            actual_temp_k=6.452,
            vna_power_dbm=-25,
            laser_power_mw=0,
            frequency_hz=np.array([]),
            s21_db=np.array([]),
        )

        s.add(trace)
        assert s.total_files == 1
        assert -25 in s.vna_powers
        assert 0 in s.laser_powers
        assert s.temp_min_k == pytest.approx(6.452)
        assert s.temp_max_k == pytest.approx(6.452)
