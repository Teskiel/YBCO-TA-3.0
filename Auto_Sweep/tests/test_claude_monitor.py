# -*- coding: utf-8 -*-
"""
BDD tests for claude_monitor.py — Claude Code 侧监控脚本

测试四种模式: --check, --report, --intervene, --fill-plan
同时测试可导入的纯逻辑决策函数。
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# Helper: 创建带 status.json 的临时实验目录
# =========================================================================

def _make_status_dir(tmpdir, experiment_id="mon_001",
                     status="running", temp_plan=None,
                     vna_plan=None, laser_plan=None,
                     target_k=40.0, actual_k=40.002,
                     phase="measuring",
                     issues=None, skipped_list=None):
    """在 tmpdir 中写入 status.json，返回目录路径。"""
    from experiment_status import ExperimentStatusWriter

    writer = ExperimentStatusWriter(tmpdir)
    writer.write_initial(
        experiment_id=experiment_id,
        temperature_plan=temp_plan or [40.0, 50.0, 60.0],
        vna_power_plan=vna_plan or [-45, -35, -25, -15],
        laser_power_plan=laser_plan or [0, 1, 3, 5],
        runtime_params={"max_wait_seconds": 1800, "meltdown_threshold_k": 0.25},
    )
    writer.update_current(0, target_k, actual_k, vna_plan[0] if vna_plan else -45, 0, phase)
    if status != "running":
        writer.set_status(status)
    if issues:
        for iss in issues:
            writer.add_issue(**iss)
    if skipped_list:
        for sk in skipped_list:
            writer.add_skipped(**sk)
    return tmpdir


# =========================================================================
# --check 模式
# =========================================================================

class TestClaudeMonitorCheck:
    """Given an experiment directory, --check prints a status summary."""

    def test_given_running_experiment_when_check_then_prints_running(self):
        """运行中的实验应输出 'running' 和当前温度点。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_status_dir(tmpdir, phase="measuring", target_k=40.0)

            result = subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--check"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            assert result.returncode == 0
            assert "running" in result.stdout.lower() or "运行" in result.stdout

    def test_given_completed_experiment_when_check_then_prints_completed(self):
        """已完成的实验应输出 'completed'。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_status_dir(tmpdir, status="completed")

            result = subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--check"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            assert result.returncode == 0
            assert "completed" in result.stdout.lower() or "完成" in result.stdout

    def test_given_no_status_file_when_check_then_prints_error(self):
        """目录中无 status.json 时应报错但非零退出码。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--check"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            # 应该报错但退出码应为非零（因无状态文件）
            assert result.returncode != 0 or "无" in result.stdout or "error" in result.stdout.lower()


# =========================================================================
# --report 模式
# =========================================================================

class TestClaudeMonitorReport:
    """Given an experiment directory, --report outputs a markdown table."""

    def test_given_running_experiment_when_report_then_outputs_table(self):
        """--report 应输出包含温度点和状态的 markdown 文本。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_status_dir(tmpdir, phase="measuring", target_k=40.0)

            result = subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--report"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            assert result.returncode == 0
            assert len(result.stdout) > 50, "报告应有足够长度"
            assert "40" in result.stdout, "报告应包含温度信息"


# =========================================================================
# --intervene 模式
# =========================================================================

class TestClaudeMonitorIntervene:
    """Given an experiment directory, --intervene analyzes and writes commands."""

    def test_given_consecutive_timeouts_when_intervene_then_writes_extend_max_wait(self):
        """连续超时应写入 extend_max_wait 命令。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_status_dir(
                tmpdir,
                issues=[
                    {"target_k": 40.0, "issue_type": "timeout", "detail": "Δ=3K", "restart_count": 0},
                    {"target_k": 50.0, "issue_type": "timeout", "detail": "Δ=5K", "restart_count": 0},
                ],
            )

            result = subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--intervene"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            assert result.returncode == 0

            cmd_path = os.path.join(tmpdir, "commands.json")
            assert os.path.exists(cmd_path), "应生成 commands.json"

            with open(cmd_path, "r", encoding="utf-8") as f:
                commands = json.load(f)
            actions = [c["action"] for c in commands["commands"]]
            assert "extend_max_wait" in actions

    def test_given_meltdown_count_ge_2_when_intervene_then_writes_relax_meltdown(self):
        """单温度点熔断 >=2 次应写入 relax_meltdown 命令。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_status_dir(
                tmpdir,
                issues=[
                    {"target_k": 50.0, "issue_type": "meltdown", "detail": "max-min=0.35K", "restart_count": 1},
                    {"target_k": 50.0, "issue_type": "meltdown", "detail": "max-min=0.31K", "restart_count": 2},
                ],
            )

            subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--intervene"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )

            with open(os.path.join(tmpdir, "commands.json"), "r", encoding="utf-8") as f:
                commands = json.load(f)
            actions = [c["action"] for c in commands["commands"]]
            assert "relax_meltdown" in actions

    def test_given_command_already_pending_when_intervene_then_no_duplicate(self):
        """已存在未执行的同类命令时不应重复写入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _make_status_dir(
                tmpdir,
                issues=[
                    {"target_k": 40.0, "issue_type": "timeout", "detail": "Δ=3K", "restart_count": 0},
                    {"target_k": 50.0, "issue_type": "timeout", "detail": "Δ=5K", "restart_count": 0},
                ],
            )

            # 第一次介入
            subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--intervene"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )

            # 第二次介入：不应重复写入
            subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--intervene"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )

            with open(os.path.join(tmpdir, "commands.json"), "r", encoding="utf-8") as f:
                commands = json.load(f)
            extend_cmds = [c for c in commands["commands"] if c["action"] == "extend_max_wait"]
            assert len(extend_cmds) == 1, "不应重复写入 extend_max_wait 命令"


# =========================================================================
# --fill-plan 模式
# =========================================================================

class TestClaudeMonitorFillPlan:
    """Given an experiment directory, --fill-plan generates fill_plan.json."""

    def test_given_completed_experiment_when_fill_plan_then_generates_plan(self):
        """实验完成后应分析缺失并生成补测计划。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 status.json（有 completed 数据）但文件系统中只有部分 .s2p
            from experiment_status import ExperimentStatusWriter

            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("fp_001", [50.0], [-45, -25], [0, 5],
                                 {"max_wait_seconds": 1800})
            writer.update_current(0, 50.0, 50.0, -45, 0, "measuring")
            writer.add_completed(50.0, -45, [0, 5], "done")
            writer.set_status("completed")

            # 创建 -45dBm 的实际 S2P 文件，但缺 -25dBm
            s2p_dir = os.path.join(tmpdir, "50K", "-45dBm", "00mW")
            os.makedirs(s2p_dir, exist_ok=True)
            s2p_path = os.path.join(s2p_dir, "YBCO_-45dBm_00mW_target_50K_actual_50.100K.s2p")
            with open(s2p_path, "w") as f:
                f.write("! test\n")

            s2p_dir2 = os.path.join(tmpdir, "50K", "-45dBm", "05mW")
            os.makedirs(s2p_dir2, exist_ok=True)
            s2p_path2 = os.path.join(s2p_dir2, "YBCO_-45dBm_05mW_target_50K_actual_50.150K.s2p")
            with open(s2p_path2, "w") as f:
                f.write("! test\n")

            result = subprocess.run(
                [sys.executable, "claude_monitor.py", "--dir", tmpdir, "--fill-plan"],
                capture_output=True, text=True, encoding="utf-8",
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )
            assert result.returncode == 0

            plan_path = os.path.join(tmpdir, "fill_plan.json")
            assert os.path.exists(plan_path), "应生成 fill_plan.json"

            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
            assert len(plan["measurements"]) > 0, "应有缺失数据待补测"


# =========================================================================
# 纯逻辑函数测试 (importable, 非 CLI)
# =========================================================================

class TestClaudeMonitorDecisionRules:
    """测试可导入的决策函数的纯逻辑。"""

    def test_given_no_issues_when_analyzing_then_no_recommendations(self):
        """无异常时不应有干预建议。"""
        from claude_monitor import analyze_for_intervention

        issues = []
        skipped = []
        commands = analyze_for_intervention(issues, skipped, existing_actions=set())
        assert commands == []

    def test_given_consecutive_timeouts_when_analyzing_then_recommends_extend(self):
        """连续超时应建议 extend_max_wait。"""
        from claude_monitor import analyze_for_intervention

        issues = [
            {"target_k": 40.0, "type": "timeout", "detail": "", "restart_count": 0},
            {"target_k": 50.0, "type": "timeout", "detail": "", "restart_count": 0},
        ]
        commands = analyze_for_intervention(issues, [], existing_actions=set())
        assert any(c["action"] == "extend_max_wait" for c in commands)

    def test_given_meltdowns_at_same_temp_when_analyzing_then_recommends_relax(self):
        """同一温度点多次熔断应建议 relax_meltdown。"""
        from claude_monitor import analyze_for_intervention

        issues = [
            {"target_k": 50.0, "type": "meltdown", "detail": "", "restart_count": 1},
            {"target_k": 50.0, "type": "meltdown", "detail": "", "restart_count": 2},
        ]
        commands = analyze_for_intervention(issues, [], existing_actions=set())
        assert any(c["action"] == "relax_meltdown" for c in commands)

    def test_given_existing_action_when_analyzing_then_no_duplicate(self):
        """已存在的 action 不应重复推荐。"""
        from claude_monitor import analyze_for_intervention

        issues = [
            {"target_k": 40.0, "type": "timeout", "detail": "", "restart_count": 0},
            {"target_k": 50.0, "type": "timeout", "detail": "", "restart_count": 0},
        ]
        commands = analyze_for_intervention(
            issues, [], existing_actions={"extend_max_wait"},
        )
        assert not any(c["action"] == "extend_max_wait" for c in commands)
