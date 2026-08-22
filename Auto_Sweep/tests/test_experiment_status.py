# -*- coding: utf-8 -*-
"""
BDD tests for experiment_status.py — 实验状态文件读写

Naming convention: test_given_<precondition>_when_<action>_then_<expected>
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStatusWriterBasic:
    """Given an output directory, ExperimentStatusWriter writes valid status.json."""

    def test_given_empty_output_dir_when_writing_initial_status_then_file_is_valid_json(self):
        """初始状态写入后应生成合法的 JSON 文件，包含必需字段。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial(
                experiment_id="test_001",
                temperature_plan=[40.0, 50.0, 60.0],
                vna_power_plan=[-45, -35, -25, -15],
                laser_power_plan=[0, 1, 3, 5, 7, 9],
                runtime_params={"max_wait_seconds": 1800},
            )

            path = os.path.join(tmpdir, "status.json")
            assert os.path.exists(path), "status.json 应该被创建"

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            assert data["experiment_id"] == "test_001"
            assert data["status"] == "running"
            assert data["temperature_plan"] == [40.0, 50.0, 60.0]
            assert data["vna_power_plan"] == [-45, -35, -25, -15]
            assert data["laser_power_plan"] == [0, 1, 3, 5, 7, 9]
            assert data["runtime_params"]["max_wait_seconds"] == 1800
            assert "start_time" in data
            assert "last_update" in data
            assert "current" in data
            assert data["completed"] == []
            assert data["issues"] == []
            assert data["skipped"] == []

    def test_given_writer_when_writing_then_uses_atomic_replace(self):
        """写入应使用原子替换（先 .tmp 后 os.replace），不留 .tmp 残留。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_002", [30.0], [-45], [0],
                                 {"max_wait_seconds": 1800})

            # 检查无 .tmp 文件残留
            all_files = os.listdir(tmpdir)
            tmp_files = [f for f in all_files if f.endswith(".tmp")]
            assert len(tmp_files) == 0, f"不应残留 .tmp 文件: {tmp_files}"
            assert "status.json" in all_files


class TestStatusWriterUpdateCurrent:
    """Given an ExperimentStatusWriter, update_current preserves other fields."""

    def test_given_existing_status_when_update_current_called_then_other_fields_preserved(self):
        """update_current 只更新 current 字段，不影响 temperature_plan 等。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_003", [40.0, 50.0], [-45, -35], [0, 5],
                                 {"max_wait_seconds": 1800})

            writer.update_current(
                temp_idx=1, target_k=50.0, actual_k=50.329,
                vna_dbm=-45, laser_mw=3, phase="measuring",
            )

            with open(os.path.join(tmpdir, "status.json"), "r", encoding="utf-8") as f:
                data = json.load(f)

            # current 字段已更新
            assert data["current"]["temp_idx"] == 1
            assert data["current"]["target_k"] == 50.0
            assert data["current"]["actual_k"] == 50.329
            assert data["current"]["vna_dbm"] == -45
            assert data["current"]["laser_mw"] == 3
            assert data["current"]["phase"] == "measuring"

            # 其他字段保持不变
            assert data["temperature_plan"] == [40.0, 50.0]
            assert data["vna_power_plan"] == [-45, -35]
            assert data["experiment_id"] == "test_003"
            assert data["status"] == "running"

    def test_given_update_current_multiple_times_when_reading_then_last_update_wins(self):
        """多次 update_current 应反映最新状态。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_multi", [40.0], [-45], [0, 1],
                                 {"max_wait_seconds": 1800})

            writer.update_current(0, 40.0, 40.002, -45, 0, "stabilizing_sparse")
            writer.update_current(0, 40.0, 40.001, -45, 0, "stabilizing_fine")
            writer.update_current(0, 40.0, 39.998, -45, 0, "measuring")

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert data["current"]["phase"] == "measuring"
            assert data["current"]["actual_k"] == 39.998


class TestStatusWriterCollections:
    """Given an ExperimentStatusWriter, collection methods append correctly."""

    def test_given_existing_status_when_add_completed_called_then_appended(self):
        """add_completed 应追加到 completed 列表。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_004", [40.0], [-45], [0, 1],
                                 {"max_wait_seconds": 1800})

            writer.add_completed(target_k=40.0, vna_dbm=-45,
                                 powers_mw=[0, 1], status="done")

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert len(data["completed"]) == 1
            assert data["completed"][0]["target_k"] == 40.0
            assert data["completed"][0]["vna_dbm"] == -45
            assert data["completed"][0]["powers_mw"] == [0, 1]
            assert data["completed"][0]["status"] == "done"

    def test_given_existing_status_when_add_issue_called_then_appended(self):
        """add_issue 应追加到 issues 列表，含时间戳和详细信息。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_005", [50.0], [-45], [0],
                                 {"max_wait_seconds": 1800})

            writer.add_issue(
                target_k=50.0,
                issue_type="meltdown",
                detail="max-min=0.346K > 0.25K",
                restart_count=1,
            )

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert len(data["issues"]) == 1
            issue = data["issues"][0]
            assert issue["target_k"] == 50.0
            assert issue["type"] == "meltdown"
            assert issue["detail"] == "max-min=0.346K > 0.25K"
            assert issue["restart_count"] == 1
            assert "time" in issue

    def test_given_existing_status_when_add_skipped_called_then_appended(self):
        """add_skipped 应追加到 skipped 列表。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_006", [50.0], [-45, -35, -25, -15], [0],
                                 {"max_wait_seconds": 1800})

            writer.add_skipped(
                target_k=50.0,
                reason="meltdown_limit",
                vna_power_remaining=[-25, -15],
            )

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert len(data["skipped"]) == 1
            skipped = data["skipped"][0]
            assert skipped["target_k"] == 50.0
            assert skipped["reason"] == "meltdown_limit"
            assert skipped["vna_power_remaining"] == [-25, -15]

    def test_given_multiple_changes_when_appending_then_accumulates_correctly(self):
        """多次追加 completed/issue/skipped 应正确累积。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_cum", [40.0, 50.0], [-45, -35], [0, 5],
                                 {"max_wait_seconds": 1800})

            writer.add_completed(40.0, -45, [0, 5], "done")
            writer.add_issue(50.0, "timeout", "Δ=3.0K", 0)
            writer.add_skipped(50.0, "meltdown_limit", [-35])
            writer.add_completed(40.0, -35, [0], "done")

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert len(data["completed"]) == 2
            assert len(data["issues"]) == 1
            assert len(data["skipped"]) == 1


class TestStatusWriterSetStatus:
    """Given an ExperimentStatusWriter, set_status changes experiment status."""

    def test_given_running_experiment_when_set_status_completed_then_status_changes(self):
        """set_status 应正确修改顶层 status 字段。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_007", [40.0], [-45], [0],
                                 {"max_wait_seconds": 1800})

            writer.set_status("completed")

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert data["status"] == "completed"

    def test_given_experiment_when_set_status_fill_running_then_reflected(self):
        """补测状态也应可设置。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_fill", [50.0], [-25], [0, 5],
                                 {"max_wait_seconds": 1800})

            writer.set_status("fill_running")

            with open(os.path.join(tmpdir, "status.json"), "r") as f:
                data = json.load(f)

            assert data["status"] == "fill_running"


class TestStatusReader:
    """Given a status.json written by ExperimentStatusWriter,
    ExperimentStatusReader reads it back correctly."""

    def test_given_written_status_when_reader_parses_then_roundtrip_consistent(self):
        """读写往返应保持数据一致。"""
        from experiment_status import ExperimentStatusWriter, ExperimentStatusReader

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExperimentStatusWriter(tmpdir)
            writer.write_initial("test_rw", [40.0, 50.0], [-45, -35], [0, 5, 7],
                                 {"max_wait_seconds": 1800, "meltdown_threshold_k": 0.25})
            writer.update_current(0, 40.0, 40.002, -45, 0, "measuring")
            writer.add_completed(40.0, -45, [0], "done")

            reader = ExperimentStatusReader(tmpdir)
            status = reader.read()

            assert status["experiment_id"] == "test_rw"
            assert status["status"] == "running"
            assert status["current"]["target_k"] == 40.0
            assert status["current"]["phase"] == "measuring"
            assert len(status["completed"]) == 1
            assert status["runtime_params"]["meltdown_threshold_k"] == 0.25

    def test_given_no_status_file_when_reader_called_then_returns_none(self):
        """目录中没有 status.json 时应返回 None。"""
        from experiment_status import ExperimentStatusReader

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = ExperimentStatusReader(tmpdir)
            result = reader.read()
            assert result is None


class TestStatusWriterResilience:
    """Given an ExperimentStatusWriter, write failures are handled gracefully."""

    def test_given_nonexistent_directory_when_writing_then_no_exception(self):
        """写入目标目录不存在时不应抛异常（优雅降级）。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent = os.path.join(tmpdir, "nonexistent", "deep", "path")
            writer = ExperimentStatusWriter(nonexistent)
            # 不应抛异常
            try:
                writer.write_initial("test_resilient", [30.0], [-45], [0],
                                     {"max_wait_seconds": 1800})
            except Exception as e:
                pytest.fail(f"write_initial 不应抛出异常: {e}")

    def test_given_write_failure_when_updating_then_no_exception(self):
        """写入失败时 update_current 也不应抛异常。"""
        from experiment_status import ExperimentStatusWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent = os.path.join(tmpdir, "nonexistent", "deep")
            writer = ExperimentStatusWriter(nonexistent)
            try:
                writer.update_current(0, 40.0, 40.0, -45, 0, "measuring")
            except Exception as e:
                pytest.fail(f"update_current 不应抛出异常: {e}")

    def test_given_status_write_disabled_when_writing_then_skips(self):
        """当 status_write_enabled=False 时，writer 应跳过所有写操作。"""
        import config
        from experiment_status import ExperimentStatusWriter

        original = config.status_write_enabled
        try:
            config.status_write_enabled = False
            with tempfile.TemporaryDirectory() as tmpdir:
                writer = ExperimentStatusWriter(tmpdir)
                writer.write_initial("test_disabled", [30.0], [-45], [0],
                                     {"max_wait_seconds": 1800})

                path = os.path.join(tmpdir, "status.json")
                assert not os.path.exists(path), (
                    "status_write_enabled=False 时不应写入文件"
                )

                # update_current 也应被跳过
                writer.update_current(0, 30.0, 30.0, -45, 0, "measuring")
                assert not os.path.exists(path)
        finally:
            config.status_write_enabled = original
