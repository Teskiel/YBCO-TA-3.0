# -*- coding: utf-8 -*-
"""data_scanner 路径解析与目录扫描的单元测试。

测试 parse_trace_path() 的 regex 提取逻辑和 scan_experiment_dir()
的目录遍历行为。使用 tmp_path 创建合成目录树。
"""

import os

import numpy as np
import pytest


# =========================================================================
# parse_trace_path 测试
# =========================================================================


class TestParseTracePath:
    """路径参数提取的正则表达式测试。"""

    @pytest.fixture
    def parser(self):
        from plot_dashboard.data_scanner import parse_trace_path
        return parse_trace_path

    def test_given_well_formed_path_when_parsing_then_extracts_all_params(
        self, parser
    ):
        path = (
            r"D:\data\experiment_data\20260605_215526"
            r"\6K\actual_6.452K\-25dBm\00mW\YBCO.s2p"
        )
        result = parser(path)

        assert result is not None
        assert result["timestamp"] == "20260605_215526"
        assert result["target_temp_k"] == pytest.approx(6.0)
        assert result["actual_temp_k"] == pytest.approx(6.452)
        assert result["vna_power_dbm"] == -25
        assert result["laser_power_mw"] == 0

    def test_given_path_without_actual_temp_when_parsing_then_actual_is_none(
        self, parser
    ):
        path = (
            r"D:\data\experiment_data\20260605_215526"
            r"\6K\-25dBm\00mW\YBCO.s2p"
        )
        result = parser(path)

        assert result is not None
        assert result["target_temp_k"] == pytest.approx(6.0)
        assert result["actual_temp_k"] is None

    def test_given_nonexistent_path_when_parsing_then_returns_none(self, parser):
        result = parser("not_a_valid_data_path")
        assert result is None

    def test_given_path_without_timestamp_when_parsing_then_returns_none(
        self, parser
    ):
        # 路径中有 K/dBm/mW 但没有 experiment_data 前缀 → 缺少 timestamp
        result = parser(r"C:\random\6K\actual_6.452K\-25dBm\00mW\file.s2p")
        assert result is None

    def test_given_pv_negative_50_when_parsing_then_extracts_correctly(
        self, parser
    ):
        path = (
            r"D:\data\experiment_data\20260605_215526"
            r"\10K\actual_9.941K\-50dBm\00mW\YBCO.s2p"
        )
        result = parser(path)
        assert result is not None
        assert result["vna_power_dbm"] == -50

    def test_given_laser_power_17_when_parsing_then_extracts_correctly(
        self, parser
    ):
        path = (
            r"D:\data\experiment_data\20260605_215526"
            r"\10K\actual_9.941K\-25dBm\17mW\YBCO.s2p"
        )
        result = parser(path)
        assert result is not None
        assert result["laser_power_mw"] == 17

    def test_given_two_digit_laser_power_when_parsing_then_preserves_leading_zero(
        self, parser
    ):
        path = (
            r"D:\data\experiment_data\20260605_215526"
            r"\6K\actual_6.452K\-25dBm\03mW\YBCO.s2p"
        )
        result = parser(path)
        assert result is not None
        assert result["laser_power_mw"] == 3  # int 转换去掉前导零

    def test_given_forward_slash_path_when_parsing_then_still_works(self, parser):
        path = "D:/data/experiment_data/20260605_215526/6K/actual_6.452K/-25dBm/00mW/YBCO.s2p"
        result = parser(path)

        assert result is not None
        assert result["target_temp_k"] == pytest.approx(6.0)
        assert result["vna_power_dbm"] == -25

    def test_given_high_target_temp_when_parsing_then_extracts_correctly(
        self, parser
    ):
        path = (
            r"D:\data\experiment_data\20260605_215526"
            r"\100K\actual_99.85K\-25dBm\00mW\YBCO.s2p"
        )
        result = parser(path)
        assert result is not None
        assert result["target_temp_k"] == pytest.approx(100.0)
        assert result["actual_temp_k"] == pytest.approx(99.85)


# =========================================================================
# scan_experiment_dir 测试
# =========================================================================


class TestScanExperimentDir:
    """目录扫描的集成测试（使用 tmp_path 合成文件）。"""

    @staticmethod
    def _make_s2p(tmp_path, rel_path: str) -> str:
        """在 tmp_path 下创建目录结构和最小 .s2p 文件。

        返回文件的绝对路径。
        """
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        # 写入最小 Touchstone 格式：! 版本 # Hz S dB R 50, 一行数据
        content = (
            "! Created by test\n"
            "# Hz S dB R 50\n"
            "3000000000 -20.0 0.0 -20.0 0.0 -20.0 0.0 -20.0 0.0\n"
            "6000000000 -25.0 0.0 -25.0 0.0 -25.0 0.0 -25.0 0.0\n"
        )
        full.write_text(content)
        return str(full)

    def test_given_empty_directory_when_scanning_then_returns_empty_list(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        traces = scan_experiment_dir(str(tmp_path))
        assert traces == []

    def test_given_single_valid_s2p_when_scanning_then_returns_one_trace(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        self._make_s2p(
            tmp_path,
            "experiment_data/20260605_215526/6K/actual_6.452K/-25dBm/00mW/YBCO.s2p",
        )

        traces = scan_experiment_dir(str(tmp_path / "experiment_data"))

        assert len(traces) == 1
        t = traces[0]
        assert t.timestamp == "20260605_215526"
        assert t.target_temp_k == pytest.approx(6.0)
        assert t.actual_temp_k == pytest.approx(6.452)
        assert t.vna_power_dbm == -25
        assert t.laser_power_mw == 0
        assert len(t.frequency_hz) == 2
        assert len(t.s21_db) == 2

    def test_given_multiple_temperatures_when_scanning_then_all_found(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        for tr in [6, 8, 10]:
            self._make_s2p(
                tmp_path,
                f"experiment_data/20260605_215526/{tr}K/actual_{tr+0.5}K/-25dBm/00mW/YBCO.s2p",
            )

        traces = scan_experiment_dir(str(tmp_path / "experiment_data"))
        assert len(traces) == 3

    def test_given_multiple_vna_powers_when_scanning_then_all_found(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        for pv in [-50, -25, -10]:
            self._make_s2p(
                tmp_path,
                f"experiment_data/20260605_215526/6K/actual_6.5K/{pv}dBm/00mW/YBCO.s2p",
            )

        traces = scan_experiment_dir(str(tmp_path / "experiment_data"))
        assert len(traces) == 3
        found = {t.vna_power_dbm for t in traces}
        assert found == {-50, -25, -10}

    def test_given_multiple_laser_powers_when_scanning_then_all_found(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        for pl in [0, 1, 3, 5, 7, 9, 17]:
            self._make_s2p(
                tmp_path,
                f"experiment_data/20260605_215526/6K/actual_6.5K/-25dBm/{pl:02d}mW/YBCO.s2p",
            )

        traces = scan_experiment_dir(str(tmp_path / "experiment_data"))
        assert len(traces) == 7

    def test_given_no_experiment_data_prefix_when_scanning_then_still_works(
        self, tmp_path
    ):
        """扫描用户直接选择的实验目录（如 experiment_data/20260605_215526/）。"""
        from plot_dashboard.data_scanner import scan_experiment_dir

        self._make_s2p(
            tmp_path,
            "20260605_215526/6K/actual_6.452K/-25dBm/00mW/YBCO.s2p",
        )

        traces = scan_experiment_dir(str(tmp_path))
        assert len(traces) == 1

    def test_given_non_s2p_files_when_scanning_then_ignored(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        # 创建一个 .txt 文件，不应被扫描
        d = tmp_path / "20260605_215526" / "notes"
        d.mkdir(parents=True)
        (d / "readme.txt").write_text("hello")

        traces = scan_experiment_dir(str(tmp_path))
        assert traces == []

    def test_given_corrupt_s2p_when_scanning_then_skip_and_continue(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir

        # 合法文件
        self._make_s2p(
            tmp_path,
            "20260605_215526/6K/actual_6.452K/-25dBm/00mW/good.s2p",
        )
        # 损坏文件
        bad_dir = tmp_path / "20260605_215526/6K/actual_6.452K/-25dBm/01mW"
        bad_dir.mkdir(parents=True)
        (bad_dir / "bad.s2p").write_text("garbage not s2p")

        traces = scan_experiment_dir(str(tmp_path))
        # 损坏文件被跳过，只返回合法文件
        assert len(traces) == 1
        assert traces[0].laser_power_mw == 0

    def test_given_multiple_timestamps_when_scanning_then_summary_collects_all(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir, ScanSummary

        for ts in ["20260605_215526", "20260606_092046"]:
            self._make_s2p(
                tmp_path,
                f"{ts}/6K/actual_6.5K/-25dBm/00mW/YBCO.s2p",
            )

        traces = scan_experiment_dir(str(tmp_path))
        timestamps = {t.timestamp for t in traces}
        assert timestamps == {"20260605_215526", "20260606_092046"}

    def test_given_scan_when_returning_summary_then_stats_correct(
        self, tmp_path
    ):
        from plot_dashboard.data_scanner import scan_experiment_dir, ScanSummary

        for tr in [6, 10]:
            for pv in [-25, -35]:
                self._make_s2p(
                    tmp_path,
                    f"20260605_215526/{tr}K/actual_{tr+0.5}K/{pv}dBm/00mW/YBCO.s2p",
                )

        summary = ScanSummary()
        traces = scan_experiment_dir(str(tmp_path))
        for t in traces:
            summary.add(t)

        assert summary.total_files == 4
        assert summary.vna_powers == {-25, -35}
        assert summary.laser_powers == {0}
        # temp range: actual = target + 0.5
        assert summary.temp_min_k == pytest.approx(6.5)
        assert summary.temp_max_k == pytest.approx(10.5)
