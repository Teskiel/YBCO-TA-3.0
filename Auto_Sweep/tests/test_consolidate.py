# -*- coding: utf-8 -*-
"""BDD tests for consolidate.py — 实验数据整合工具"""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestScanRun:
    """Given an experiment output directory, scan_run extracts parameters."""

    def test_given_manifest_when_scan_then_params_from_manifest(self):
        """有 manifest.json 时直接从 manifest 读取参数计划。"""
        from consolidate import scan_run, RunInfo

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "20260623_155113")
            os.makedirs(run_dir)
            manifest = {
                "experiment_id": "20260623_155113",
                "start_time": "2026-06-23T15:51:13",
                "temperature_plan": [50.0, 60.0, 70.0],
                "vna_power_plan": [-55, -45],
                "laser_power_plan": [0, 5, 9],
            }
            with open(os.path.join(run_dir, "manifest.json"), "w") as f:
                json.dump(manifest, f)

            info = scan_run(run_dir)
            assert info is not None
            assert info.id == "20260623_155113"
            assert info.params_hash is not None
            assert 50.0 in info.target_temps
            assert 70.0 in info.target_temps
            assert info.timestamp is not None

    def test_given_status_json_when_scan_then_params_from_status(self):
        """有 status.json 但无 manifest 时从 status 读取。"""
        from consolidate import scan_run

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "20260617_193744")
            os.makedirs(run_dir)
            status = {
                "experiment_id": "20260617_193744",
                "start_time": "2026-06-17T19:37:44",
                "temperature_plan": [40.0, 50.0],
                "vna_power_plan": [-45, -35],
                "laser_power_plan": [0, 1, 3],
            }
            with open(os.path.join(run_dir, "status.json"), "w") as f:
                json.dump(status, f)

            info = scan_run(run_dir)
            assert info is not None
            assert info.id == "20260617_193744"
            assert info.target_temps == {40.0, 50.0}

    def test_given_no_metadata_when_scan_then_infer_from_dirs(self):
        """无任何元数据时从目录结构反推。"""
        from consolidate import scan_run

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "20260606_092046")
            os.makedirs(run_dir)
            os.makedirs(os.path.join(run_dir, "50K", "-55dBm", "00mW"))
            with open(os.path.join(run_dir, "50K", "-55dBm", "00mW",
                                     "YBCO_-55dBm_00mW_target_50K_actual_49.994K.s2p"), "w") as f:
                f.write("")

            info = scan_run(run_dir)
            assert info is not None
            assert 50.0 in info.target_temps
            assert info.s2p_count == 1
            assert not info.has_manifest

    def test_given_empty_dir_when_scan_then_returns_runinfo(self):
        """空目录仍然返回 RunInfo（后续归类为 junk）。"""
        from consolidate import scan_run

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "empty_run")
            os.makedirs(run_dir)

            info = scan_run(run_dir)
            assert info is not None
            assert info.s2p_count == 0
            assert not info.has_manifest


class TestIsJunk:
    """is_junk 判定空壳/失败启动。"""

    def test_given_no_s2p_and_no_metadata_when_is_junk_then_true(self):
        from consolidate import is_junk, RunInfo
        info = RunInfo(id="empty", path="/tmp/empty")
        info.s2p_count = 0
        info.has_manifest = False
        info.has_status = False
        assert is_junk(info) is True

    def test_given_few_s2p_and_no_metadata_when_is_junk_then_true(self):
        from consolidate import is_junk, RunInfo
        info = RunInfo(id="tiny", path="/tmp/tiny")
        info.s2p_count = 3
        info.has_manifest = False
        info.has_status = False
        assert is_junk(info) is True

    def test_given_few_s2p_but_has_manifest_when_is_junk_then_false(self):
        from consolidate import is_junk, RunInfo
        info = RunInfo(id="tiny_but_real", path="/tmp/tiny")
        info.s2p_count = 2
        info.has_manifest = True
        assert is_junk(info) is False

    def test_given_many_s2p_when_is_junk_then_false(self):
        from consolidate import is_junk, RunInfo
        info = RunInfo(id="big", path="/tmp/big")
        info.s2p_count = 50
        info.has_manifest = False
        assert is_junk(info) is False


class TestGroupRuns:
    """group_runs 按 (Pv, Pl) 一致 + 温度相邻/重叠 分组。"""

    def test_given_same_params_contiguous_temps_when_group_then_one_group(self):
        """相同参数、温度互补的碎片归为一组。"""
        from consolidate import group_runs, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.params_hash = "abc123"
        r1.target_temps = {10.0, 12.0, 14.0}
        r1.vna_power_plan = [-55, -45]
        r1.laser_power_plan = [0, 5]

        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.params_hash = "abc123"
        r2.target_temps = {16.0, 18.0, 20.0}
        r2.vna_power_plan = [-55, -45]
        r2.laser_power_plan = [0, 5]

        groups = group_runs([r1, r2])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_given_different_params_when_group_then_separate_groups(self):
        """不同参数分开。"""
        from consolidate import group_runs, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.params_hash = "aaa111"
        r1.target_temps = {50.0}
        r1.vna_power_plan = [-55, -45]
        r1.laser_power_plan = [0, 5]

        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.params_hash = "bbb222"
        r2.target_temps = {50.0}
        r2.vna_power_plan = [-45, -35]  # different VNA range
        r2.laser_power_plan = [0, 5]

        groups = group_runs([r1, r2])
        assert len(groups) == 2

    def test_given_same_params_large_temp_gap_when_group_then_separate(self):
        """温度差距超过 1 个 step 的数据分开。"""
        from consolidate import group_runs, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.params_hash = "same"
        r1.target_temps = {10.0, 12.0}
        r1.vna_power_plan = [-55]
        r1.laser_power_plan = [0]

        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.params_hash = "same"
        r2.target_temps = {50.0, 52.0}  # gap from 12 to 50 >> 2 steps
        r2.vna_power_plan = [-55]
        r2.laser_power_plan = [0]

        groups = group_runs([r1, r2])
        assert len(groups) == 2  # gap too large, separate groups


class TestResolveConflicts:
    """resolve_conflicts 处理同一 T 点多次测量的去重。"""

    def test_given_both_stable_when_resolve_then_keep_later(self):
        """两份都稳定 → 取时间戳晚的。"""
        from consolidate import resolve_conflicts, S2PFile, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.s2p_files = [
            S2PFile(path="a.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=49.9, mtime=100.0),
        ]
        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.s2p_files = [
            S2PFile(path="b.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=50.1, mtime=200.0),
        ]

        kept, _ = resolve_conflicts([r1, r2])
        assert len(kept) == 1
        assert kept[0].path == "b.s2p"  # later, both stable

    def test_given_one_stable_one_unstable_when_resolve_then_keep_stable(self):
        """一稳一不稳 → 取稳的。"""
        from consolidate import resolve_conflicts, S2PFile, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.s2p_files = [
            S2PFile(path="stable.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=50.2, mtime=100.0),
        ]
        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.s2p_files = [
            S2PFile(path="unstable.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=53.0, mtime=200.0),
        ]

        kept, warnings = resolve_conflicts([r1, r2])
        assert len(kept) == 1
        assert kept[0].path == "stable.s2p"
        assert len(warnings) == 0  # stable pick, no warning needed

    def test_given_both_unstable_when_resolve_then_keep_closer(self):
        """都不稳 → 取偏差更小的，标记 warning。"""
        from consolidate import resolve_conflicts, S2PFile, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.s2p_files = [
            S2PFile(path="far.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=55.0, mtime=100.0),
        ]
        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.s2p_files = [
            S2PFile(path="farther.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=60.0, mtime=200.0),
        ]

        kept, warnings = resolve_conflicts([r1, r2])
        assert len(kept) == 1
        assert kept[0].path == "far.s2p"  # 55 vs 60: 55 is closer
        assert len(warnings) == 1
        assert "unstable" in warnings[0].lower()

    def test_given_different_temps_when_resolve_then_keep_both(self):
        """不同 T 点不冲突。"""
        from consolidate import resolve_conflicts, S2PFile, RunInfo

        r1 = RunInfo(id="run1", path="/tmp/run1")
        r1.s2p_files = [
            S2PFile(path="t50.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=49.9, mtime=100.0),
        ]
        r2 = RunInfo(id="run2", path="/tmp/run2")
        r2.s2p_files = [
            S2PFile(path="t52.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=52.0, actual_k=52.0, mtime=200.0),
        ]

        kept, warnings = resolve_conflicts([r1, r2])
        assert len(kept) == 2


class TestCleanFarTarget:
    """clean_far_target 删除远离目标温度的 s2p 文件。"""

    def test_given_far_target_files_when_clean_then_removed(self):
        from consolidate import clean_far_target, S2PFile

        files = [
            S2PFile(path="good.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=50.1, mtime=100.0),
            S2PFile(path="far.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=53.0, mtime=200.0),
        ]

        kept, removed = clean_far_target(files)
        assert len(kept) == 1
        assert kept[0].path == "good.s2p"
        assert len(removed) == 1
        assert removed[0].path == "far.s2p"

    def test_given_all_far_when_clean_then_keep_closest(self):
        """全都远靶 → 保留最近的一个，标记 warning。"""
        from consolidate import clean_far_target, S2PFile

        files = [
            S2PFile(path="a.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=55.0, mtime=100.0),
            S2PFile(path="b.s2p", vna_dbm=-55, laser_mw=0,
                    target_k=50.0, actual_k=60.0, mtime=200.0),
        ]

        kept, removed = clean_far_target(files)
        assert len(kept) == 1
        assert kept[0].path == "a.s2p"
        assert len(removed) == 1


class TestBuildConsolidatedName:
    """文件夹命名规则: {first_date}-{last_date}__{minT}-{maxT}K__{N}pts"""

    def test_given_single_run_when_build_name_then_same_date(self):
        from consolidate import build_consolidated_name, RunInfo
        from datetime import datetime

        r = RunInfo(id="20260618_150520", path="/tmp/x")
        r.timestamp = datetime(2026, 6, 18, 15, 5, 20)
        r.target_temps = {40.0, 50.0, 60.0}

        name = build_consolidated_name([r], 120)
        assert name == "20260618-0618__40-60K__120pts"

    def test_given_multi_run_when_build_name_then_date_range(self):
        from consolidate import build_consolidated_name, RunInfo
        from datetime import datetime

        r1 = RunInfo(id="20260611_115038", path="/tmp/x")
        r1.timestamp = datetime(2026, 6, 11, 11, 50, 38)
        r1.target_temps = {10.0, 20.0}

        r2 = RunInfo(id="20260614_012513", path="/tmp/x")
        r2.timestamp = datetime(2026, 6, 14, 1, 25, 13)
        r2.target_temps = {70.0, 80.0}

        name = build_consolidated_name([r1, r2], 376)
        assert name == "20260611-0614__10-80K__376pts"
        assert name.endswith(".txt") is False  # name only, .txt added later


class TestIsAlreadyConsolidated:
    """已整合的目录被跳过。"""

    def test_given_marker_txt_when_check_then_consolidated(self):
        from consolidate import _is_already_consolidated
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Create a marker-like txt file
            marker = os.path.join(
                tmp, "20260611-0614__10-80K__376pts.txt")
            with open(marker, "w") as f:
                pass  # empty file

            assert _is_already_consolidated(tmp) is True

    def test_given_no_marker_when_check_then_not_consolidated(self):
        from consolidate import _is_already_consolidated
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            assert _is_already_consolidated(tmp) is False


class TestEndToEnd:
    """完整流程: 扫描 → 分组 → 去重 → 清理 → 合并。"""

    def test_given_fragmented_runs_when_full_pipeline_then_merged(self):
        """模拟 accomplish/ 场景: 多个碎片, 温度互补, 有重叠。"""
        from consolidate import (
            scan_run, is_junk, group_runs,
            resolve_conflicts, clean_far_target,
            build_consolidated_name, _merge_group,
        )
        import tempfile, shutil

        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "experiment_data")
            os.makedirs(base)

            # Fragment 1: T=10-18K (5 temps, 3 Pv x 2 Pl = 30 files)
            run1 = os.path.join(base, "20260611_115038")
            os.makedirs(run1)
            os.makedirs(os.path.join(run1, "logs"))
            with open(os.path.join(run1, "logs", "log1.txt"), "w") as f:
                f.write("exp start")
            m = {
                "experiment_id": "20260611_115038",
                "start_time": "2026-06-11T11:50:38",
                "temperature_plan": [10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
                "vna_power_plan": [-55, -45, -35],
                "laser_power_plan": [0, 5, 9],
            }
            with open(os.path.join(run1, "manifest.json"), "w") as f:
                json.dump(m, f)
            for t in [10.0, 12.0, 14.0, 16.0, 18.0]:
                for pv in [-55, -45, -35]:
                    for pl in [0, 5]:
                        d = os.path.join(run1, f"{t:g}K", f"{pv:g}dBm", f"{pl:02.0f}mW")
                        os.makedirs(d)
                        fname = (
                            f"YBCO_{pv:g}dBm_{pl:02.0f}mW"
                            f"_target_{t:g}K_actual_{t-0.01:.3f}K.s2p"
                        )
                        with open(os.path.join(d, fname), "w") as f:
                            f.write("! s2p data")

            # Fragment 2: T=18-22K (3 temps, 3 Pv x 2 Pl = 18 files)
            # Overlap at T=18K (also in run1) for dedup verification
            run2 = os.path.join(base, "20260612_014432")
            os.makedirs(run2)
            os.makedirs(os.path.join(run2, "logs"))
            m2 = {
                "experiment_id": "20260612_014432",
                "start_time": "2026-06-12T01:44:32",
                "temperature_plan": [18.0, 20.0, 22.0, 24.0, 26.0, 28.0],
                "vna_power_plan": [-55, -45, -35],
                "laser_power_plan": [0, 5, 9],
            }
            with open(os.path.join(run2, "manifest.json"), "w") as f:
                json.dump(m2, f)
            for t in [18.0, 20.0, 22.0]:
                for pv in [-55, -45, -35]:
                    for pl in [0, 5]:
                        d = os.path.join(run2, f"{t:g}K", f"{pv:g}dBm", f"{pl:02.0f}mW")
                        os.makedirs(d)
                        fname = (
                            f"YBCO_{pv:g}dBm_{pl:02.0f}mW"
                            f"_target_{t:g}K_actual_{t+0.02:.3f}K.s2p"
                        )
                        with open(os.path.join(d, fname), "w") as f:
                            f.write("! s2p data")

            # --- Run pipeline ---
            runs = []
            for entry in os.listdir(base):
                info = scan_run(os.path.join(base, entry))
                if info:
                    runs.append(info)

            assert len(runs) == 2

            # No junk
            junk = [r for r in runs if is_junk(r)]
            assert len(junk) == 0

            # One group (temps adjacent: 10-18 and 18-22, gap <= 1 step)
            groups = group_runs(runs)
            assert len(groups) == 1
            assert len(groups[0]) == 2

            # Dedup: 18K appears in both runs → 6 deduped, 42 kept
            kept, warnings = resolve_conflicts(groups[0])
            # 5 temps from run1 (10,12,14,16,18) + 2 unique from run2 (20,22) = 7
            # 7 temps * 3 Pv * 2 Pl = 42
            assert len(kept) == 42, f"Expected 42, got {len(kept)}"

            # Clean far-target (all stable in this test)
            kept2, removed = clean_far_target(kept)
            assert len(removed) == 0

            # Merge
            name = build_consolidated_name(groups[0], len(kept2))
            assert "20260611" in name
            assert "10-22K" in name
            assert "42pts" in name

            merged = _merge_group(groups[0], kept2, base)
            assert os.path.isdir(merged)

            # Verify marker txt
            marker = os.path.join(merged, f"{name}.txt")
            assert os.path.exists(marker)

            # Verify s2p files in merged (excluding _fragments originals)
            s2p_count = 0
            for root, dirs, files in os.walk(merged):
                # Skip the _fragments sub-tree (contains raw originals)
                if "_fragments" in root.split(os.sep):
                    continue
                s2p_count += sum(1 for f in files if f.endswith(".s2p"))
            assert s2p_count == 42

            # Verify fragments moved
            frag_dir = os.path.join(merged, "_fragments")
            assert os.path.isdir(frag_dir)
            assert os.path.isdir(os.path.join(frag_dir, "20260611_115038"))
            assert os.path.isdir(os.path.join(frag_dir, "20260612_014432"))
