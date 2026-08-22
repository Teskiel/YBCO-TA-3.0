# -*- coding: utf-8 -*-
"""
BDD tests for fill_planner.py — 日志解析、缺失分析、补测计划生成

Naming convention: test_given_<precondition>_when_<action>_then_<expected>

所有测试使用合成日志文本和临时目录，零硬件依赖。
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# 合成日志文本 helper
# =========================================================================

def _make_log_header(temps=None, vna_powers=None, laser_powers=None, output_dir="/data/test"):
    """构造一个最小但合法的日志头部。"""
    temps = temps or [40.0, 50.0, 60.0]
    vna_powers = vna_powers or [-45, -35, -25, -15]
    laser_powers = laser_powers or [0, 1, 3, 5, 7, 9, 11, 13, 15, 17]
    return (
        f"实验开始 — 输出目录: {output_dir}\n"
        f"温度列表: {temps}\n"
        f"激光功率列表: {laser_powers} mW\n"
        f"VNA 功率列表: {vna_powers} dBm\n"
    )


def _make_measurement_line(vna_dbm, power_mw, temp_k, actual_k=None):
    """构造一条测量日志行。"""
    ak = actual_k if actual_k is not None else temp_k
    return f"  Measuring {vna_dbm:+d} dBm / {power_mw} mW @ {ak:.3f} K\n"


def _make_stabilising_line(target_k):
    """构造一条开始稳定日志行。"""
    return f"→ Stabilising to {target_k:.1f} K ...\n"


def _make_meltdown_line(max_min_k, threshold_k=0.25):
    """构造一条熔断日志行。"""
    return f"  ⛔ 测量中温度漂移熔断: max-min={max_min_k:.3f}K > {threshold_k:.2f}K, 读数=...\n"


def _make_meltdown_restart_line(n):
    """构造一条熔断重启日志行。"""
    return f"  ⚠ 测量熔断 #{n} — 重新等待温度稳定（跳过预等待）\n"


def _make_meltdown_skip_line(target_k):
    """构造一条熔断跳过温度点日志行。"""
    return f"  ⛔ 熔断重启已达上限 (3/3)，跳过温度点 {target_k:.1f}K\n"


def _make_timeout_skip_line(target_k, avg_k, delta_k):
    """构造一条超时跳过日志行。"""
    return f"  超时跳过 @ {target_k:.1f}K — avg={avg_k:.3f}K, Δ={delta_k:.3f}K > 2.0K\n"


def _make_timeout_hard_fail_line(target_k):
    """构造一条超时硬失败日志行。"""
    return f"  跳过温度点 {target_k:.1f}K（超时硬失败）\n"


def _make_temp_done_line():
    return "  温度点完成，激光功率 → 0 mW（准备升温）\n"


def _make_experiment_complete_line(count):
    return f"Experiment complete — {count} measurements\n"


# =========================================================================
# 测试类
# =========================================================================


class TestFillPlannerParseLogHeader:
    """Given experiment log text, parse_experiment_log extracts plan info from header."""

    def test_given_empty_log_when_parsed_then_returns_empty_lists(self):
        """空日志应返回所有空列表。"""
        from fill_planner import parse_experiment_log

        result = parse_experiment_log("")
        assert result.temperature_plan == []
        assert result.vna_power_plan == []
        assert result.laser_power_plan == []
        assert result.completed == []
        assert result.skipped == []
        assert result.issues == []

    def test_given_log_with_header_when_parsed_then_extracts_plans(self):
        """标准日志头部应正确提取温度/VNA/激光计划列表。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(
            temps=[40.0, 50.0, 60.0],
            vna_powers=[-45, -35, -25, -15],
            laser_powers=[0, 1, 3, 5, 7, 9, 11, 13, 15, 17],
        )
        result = parse_experiment_log(log)

        assert result.temperature_plan == [40.0, 50.0, 60.0]
        assert result.vna_power_plan == [-45, -35, -25, -15]
        assert result.laser_power_plan == [0, 1, 3, 5, 7, 9, 11, 13, 15, 17]

    def test_given_log_header_when_parsed_then_extracts_output_dir(self):
        """应提取输出目录路径。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(output_dir="/data/experiment/test_001")
        result = parse_experiment_log(log)
        assert result.output_dir is not None


class TestFillPlannerParseMeasurements:
    """Given experiment log text, parse_experiment_log extracts completed measurements."""

    def test_given_log_with_measurements_when_parsed_then_extracts_completed(self):
        """测量行应被提取为 completed 条目。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(temps=[40.0], vna_powers=[-45], laser_powers=[0, 5])
        log += _make_stabilising_line(40.0)
        log += _make_measurement_line(-45, 0, 40.002)
        log += _make_measurement_line(-45, 5, 40.015)

        result = parse_experiment_log(log)

        assert len(result.completed) == 2
        assert result.completed[0]["vna_dbm"] == -45
        assert result.completed[0]["power_mw"] == 0
        assert result.completed[0]["target_k"] == 40.0
        assert result.completed[1]["power_mw"] == 5

    def test_given_log_with_multiple_temperatures_when_parsed_then_groups_by_temp(self):
        """多个温度点的测量应正确分组，各自关联到对应 target_k。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(temps=[40.0, 50.0], vna_powers=[-45], laser_powers=[0])
        log += _make_stabilising_line(40.0)
        log += _make_measurement_line(-45, 0, 40.002)
        log += _make_temp_done_line()
        log += _make_stabilising_line(50.0)
        log += _make_measurement_line(-45, 0, 50.100)

        result = parse_experiment_log(log)

        assert len(result.completed) == 2
        assert result.completed[0]["target_k"] == 40.0
        assert result.completed[1]["target_k"] == 50.0


class TestFillPlannerParseIssues:
    """Given experiment log text, parse_experiment_log extracts issues/skips."""

    def test_given_log_with_meltdown_skip_when_parsed_then_extracts_skipped(self):
        """熔断跳过应被记录在 skipped 列表中。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(temps=[50.0], vna_powers=[-25, -15], laser_powers=[0])
        log += _make_stabilising_line(50.0)
        log += _make_meltdown_skip_line(50.0)

        result = parse_experiment_log(log)

        assert len(result.skipped) == 1
        assert result.skipped[0]["target_k"] == 50.0
        assert result.skipped[0]["reason"] == "meltdown_limit"

    def test_given_log_with_timeout_hard_fail_when_parsed_then_extracts_skipped(self):
        """超时硬失败应被记录在 skipped 列表中。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(temps=[50.0], vna_powers=[-45], laser_powers=[0])
        log += _make_stabilising_line(50.0)
        log += _make_timeout_skip_line(50.0, 46.959, 3.041)
        log += _make_timeout_hard_fail_line(50.0)

        result = parse_experiment_log(log)

        assert len(result.skipped) >= 1
        timeout_skips = [s for s in result.skipped if s["reason"] == "timeout_hard_fail"]
        assert len(timeout_skips) >= 1

    def test_given_log_with_meltdown_events_when_parsed_then_extracts_restart_counts(self):
        """熔断事件应记录重启次数。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(temps=[50.0], vna_powers=[-45], laser_powers=[0, 1, 3])
        log += _make_stabilising_line(50.0)
        log += _make_meltdown_line(0.346)
        log += _make_meltdown_restart_line(1)

        result = parse_experiment_log(log)

        assert len(result.issues) >= 1
        meltdowns = [i for i in result.issues if i["type"] == "meltdown"]
        assert len(meltdowns) >= 1

    def test_given_log_with_multiple_meltdowns_at_same_temp_when_parsed_then_counts_correctly(self):
        """同一温度点的多次熔断应独立记录。"""
        from fill_planner import parse_experiment_log

        log = _make_log_header(temps=[50.0], vna_powers=[-45], laser_powers=[0, 1, 3, 5])
        log += _make_stabilising_line(50.0)
        log += _make_meltdown_line(0.346)
        log += _make_meltdown_restart_line(1)
        log += _make_meltdown_line(0.311)
        log += _make_meltdown_restart_line(2)
        log += _make_meltdown_line(0.259)
        log += _make_meltdown_restart_line(3)

        result = parse_experiment_log(log)

        meltdowns = [i for i in result.issues if i["type"] == "meltdown"]
        assert len(meltdowns) == 3


class TestFillPlannerDirectoryScan:
    """Given an experiment output directory, scan_s2p_files finds all .s2p files."""

    def test_given_directory_with_s2p_files_when_scanned_then_returns_file_map(self):
        """应扫描并解析所有 .s2p 文件名。"""
        from fill_planner import scan_s2p_files

        with tempfile.TemporaryDirectory() as tmpdir:
            folder = os.path.join(tmpdir, "40K", "-45dBm", "00mW")
            os.makedirs(folder, exist_ok=True)
            s2p_path = os.path.join(
                folder,
                "YBCO_-45dBm_00mW_target_40K_actual_40.002K.s2p",
            )
            with open(s2p_path, "w") as f:
                f.write("! Created: 2026-06-16\n")

            files = scan_s2p_files(tmpdir)
            assert len(files) == 1
            assert files[0]["target_k"] == 40.0
            assert files[0]["vna_dbm"] == -45
            assert files[0]["power_mw"] == 0

    def test_given_directory_with_multiple_s2p_files_when_scanned_then_counts_all(self):
        """应扫描到所有 .s2p 文件。"""
        from fill_planner import scan_s2p_files

        with tempfile.TemporaryDirectory() as tmpdir:
            for vna in [(-45, "00mW"), (-35, "01mW"), (-25, "03mW")]:
                dbm_str = f"{vna[0]:+d}dBm"
                folder = os.path.join(tmpdir, "40K", dbm_str, vna[1])
                os.makedirs(folder, exist_ok=True)
                s2p_path = os.path.join(
                    folder,
                    f"YBCO_{vna[0]:+d}dBm_{vna[1]}_target_40K_actual_40.010K.s2p",
                )
                with open(s2p_path, "w") as f:
                    f.write("! test data\n")

            files = scan_s2p_files(tmpdir)
            assert len(files) == 3

    def test_given_empty_directory_when_scanned_then_returns_empty_list(self):
        """空目录应返回空列表。"""
        from fill_planner import scan_s2p_files

        with tempfile.TemporaryDirectory() as tmpdir:
            result = scan_s2p_files(tmpdir)
            assert result == []

    def test_given_directory_with_non_s2p_files_when_scanned_then_ignores_them(self):
        """非 .s2p 文件应被忽略。"""
        from fill_planner import scan_s2p_files

        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "40K", "-45dBm", "00mW"), exist_ok=True)
            # 写入一个非 s2p 文件
            txt_path = os.path.join(tmpdir, "40K", "-45dBm", "00mW", "readme.txt")
            with open(txt_path, "w") as f:
                f.write("not s2p")

            files = scan_s2p_files(tmpdir)
            assert len(files) == 0


class TestFillPlannerComputeMissing:
    """Given expected vs actual, compute_missing finds gaps."""

    def test_given_all_measurements_present_when_computing_missing_then_returns_empty(self):
        """全部存在时返回空列表。"""
        from fill_planner import compute_missing

        expected = [(40.0, -45, 0), (40.0, -45, 5), (40.0, -35, 0)]
        actual = [(40.0, -45, 0), (40.0, -45, 5), (40.0, -35, 0)]
        missing = compute_missing(expected, actual)
        assert missing == []

    def test_given_partial_measurements_when_computing_missing_then_returns_only_gaps(self):
        """部分缺失时只返回缺失部分。"""
        from fill_planner import compute_missing

        expected = [
            (50.0, -45, 0), (50.0, -45, 1), (50.0, -45, 3),
            (50.0, -25, 0), (50.0, -25, 1), (50.0, -25, 3),
        ]
        actual = [
            (50.0, -45, 0), (50.0, -45, 1), (50.0, -45, 3),
        ]
        missing = compute_missing(expected, actual)

        assert len(missing) == 3
        assert all(m[0] == 50.0 and m[1] == -25 for m in missing)

    def test_given_completely_missing_when_computing_then_returns_all_expected(self):
        """全部缺失时返回所有期望条目。"""
        from fill_planner import compute_missing

        expected = [(60.0, -15, 0), (60.0, -15, 5)]
        actual = []
        missing = compute_missing(expected, actual)
        assert set(missing) == set(expected)

    def test_given_empty_expected_when_computing_missing_then_returns_empty(self):
        """期望为空时返回空列表。"""
        from fill_planner import compute_missing

        result = compute_missing([], [(40.0, -45, 0)])
        assert result == []


class TestFillPlannerGeneratePlan:
    """Given missing measurements, generate_fill_plan produces fill_plan.json."""

    def test_given_missing_measurements_when_generating_plan_then_output_is_valid_json(self):
        """应生成结构合法的 fill_plan.json。"""
        from fill_planner import generate_fill_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = [
                (50.0, -25, 0), (50.0, -25, 5),
                (50.0, -15, 0), (50.0, -15, 5),
            ]
            plan_path = generate_fill_plan(
                missing=missing,
                experiment_id="test_001",
                output_dir=tmpdir,
                cooldown_offset_k=5.0,
            )

            assert plan_path is not None
            assert os.path.exists(plan_path)

            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)

            assert plan["experiment_id"] == "test_001"
            assert plan["strategy"] == "cooldown_then_heat"
            assert plan["cooldown_offset_k"] == 5.0
            assert "measurements" in plan
            assert "temperature_plan" in plan
            assert 50.0 in plan["temperature_plan"]

            # 测量条目应按温度+VNA 分组
            vna_powers_in_plan = {m["vna_dbm"] for m in plan["measurements"]}
            assert vna_powers_in_plan == {-25, -15}

    def test_given_no_missing_measurements_when_generating_plan_then_returns_none(self):
        """无缺失时返回 None，不生成文件。"""
        from fill_planner import generate_fill_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_fill_plan([], "test", output_dir=tmpdir)
            assert result is None

    def test_given_single_temp_single_vna_when_generating_plan_then_structure_correct(self):
        """单个温度+单个VNA功率的补测计划结构正确。"""
        from fill_planner import generate_fill_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = [(50.0, -25, 0)]
            plan_path = generate_fill_plan(missing, "test_002", output_dir=tmpdir)

            with open(plan_path, "r") as f:
                plan = json.load(f)

            assert plan["temperature_plan"] == [50.0]
            assert len(plan["measurements"]) == 1
            assert plan["measurements"][0]["target_k"] == 50.0
            assert plan["measurements"][0]["vna_dbm"] == -25
            assert plan["measurements"][0]["laser_powers_mw"] == [0]


class TestFillPlannerRealLog:
    """Integration: parse a real experiment log file from the project."""

    def test_given_real_log_file_when_parsed_then_extracts_known_structure(self):
        """用真实日志文件 20260616_100438 验证解析结果结构正确。"""
        from fill_planner import parse_experiment_log

        real_log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "experiment_data", "20260616_100438", "logs",
            "experiment_log_20260616_100438.txt",
        )

        if not os.path.exists(real_log_path):
            pytest.skip(f"真实日志文件不存在: {real_log_path}")

        with open(real_log_path, "r", encoding="utf-8") as f:
            log_text = f.read()

        result = parse_experiment_log(log_text)

        # 基本结构验证
        assert len(result.temperature_plan) == 3
        assert result.temperature_plan == [40.0, 50.0, 60.0]
        assert len(result.vna_power_plan) == 4
        assert result.vna_power_plan == [-45, -35, -25, -15]
        assert len(result.laser_power_plan) == 10

        # 应该有完成的测量
        assert len(result.completed) > 0, "应有已完成的测量记录"

        # 50K应该有跳过记录
        skipped_50k = [s for s in result.skipped if s["target_k"] == 50.0]
        assert len(skipped_50k) >= 1, "50K应有跳过记录"

        # 应有熔断事件
        meltdown_issues = [i for i in result.issues if i["type"] == "meltdown"]
        assert len(meltdown_issues) > 0, "应有熔断事件"
