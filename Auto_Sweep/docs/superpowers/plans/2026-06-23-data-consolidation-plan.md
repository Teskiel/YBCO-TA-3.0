# Data Consolidation Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `consolidate.py` to scan fragmented experiment runs, group by matching (Pv, Pl) parameters, merge contiguous temperature sweeps, deduplicate conflicts, clean far-target data, and produce a unified folder under `~merged/` with marker txt. Also write `manifest.json` at experiment start for future-proofing.

**Architecture:** Two independent changes: (1) ~15-line addition to `workers.py` that writes `manifest.json` when an experiment starts; (2) new standalone `consolidate.py` (~350 lines) that scans `experiment_data/`, groups runs, resolves conflicts per deterministic rules, merges directories, and cleans far-target files. No changes to other existing files.

**Tech Stack:** Python 3, stdlib only (json, os, re, shutil, datetime, hashlib, collections)

## Global Constraints

- No changes to existing files except `ui/workers.py` (manifest.json insertion)
- CLI only — no GUI integration
- All destructive actions (delete, move) require user confirmation unless `--yes` flag passed
- `--dry-run` flag reports all actions without executing
- Original fragment directories moved to `_fragments/`, never deleted
- Only far-target `.s2p` files may be deleted (when `|actual - target| > 1 K`)
- Marker `.txt` files are empty — filename IS the metadata

---

## File Map

| File | Responsibility |
|------|---------------|
| `ui/workers.py` (modify) | Write `manifest.json` at experiment start |
| `consolidate.py` (create) | Scan, group, dedup, clean, merge |
| `tests/test_consolidate.py` (create) | BDD tests for all consolidate logic |

---

### Task 1: Write `manifest.json` at Experiment Start

**Files:**
- Modify: `ui/workers.py:1677-1681`

**Interfaces:**
- Produces: `{output_dir}/manifest.json` with schema `{experiment_id, start_time, temperature_plan, vna_power_plan, laser_power_plan}`

- [ ] **Step 1: Locate insertion point in `_run_impl`**

Open `ui/workers.py` and confirm the insertion point is at line ~1681, right after the three `_log()` calls that print parameter lists. At this point:
- `self._output_dir` is set (from `configure()`)
- `logs/` directory already created (line 1652-1653)
- `self._temp_list`, `self._power_list` (laser), `self._vna_power_list` are available
- No measurements have started yet

- [ ] **Step 2: Add manifest writing code**

Insert after line 1681 (`_log(f"VNA 功率列表: {self._vna_power_list} dBm")`):

```python
            # ---- 写入 manifest.json（供数据整合工具使用） ----
            try:
                import json as _json
                manifest = {
                    "experiment_id": os.path.basename(self._output_dir),
                    "start_time": start_time.isoformat(),
                    "temperature_plan": self._temp_list,
                    "vna_power_plan": self._vna_power_list,
                    "laser_power_plan": self._power_list,
                }
                manifest_path = os.path.join(self._output_dir, "manifest.json")
                with open(manifest_path, "w", encoding="utf-8") as _f:
                    _json.dump(manifest, _f, ensure_ascii=False, indent=2)
            except Exception:
                pass  # manifest 写入失败不影响实验
```

- [ ] **Step 3: Verify by running existing tests**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_experiment_worker.py -v --timeout=30 2>&1 | tail -20
```

Expected: all existing tests still pass (manifest write is inside try/except and doesn't affect existing flow).

- [ ] **Step 4: Commit**

```bash
git add ui/workers.py
git commit -m "feat: write manifest.json at experiment start for data consolidation"
```

---

### Task 2: Scan Engine — Extract Parameters from Runs

**Files:**
- Create: `consolidate.py`
- Create: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: nothing (first task of new file)
- Produces: `scan_run(dir_path: str) -> RunInfo | None`, `RunInfo` dataclass with fields `{id, params_hash, target_temps, s2p_count, timestamp, has_manifest, s2p_files: list[S2PFile]}`

- [ ] **Step 1: Write failing tests for scan_run**

Create `tests/test_consolidate.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py -v 2>&1 | tail -15
```

Expected: all fail with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement scan_run and RunInfo**

Create `consolidate.py`:

```python
# -*- coding: utf-8 -*-
"""
实验数据整合工具 — 扫描、分组、去重、清理、合并

Usage:
    python consolidate.py              # 交互模式
    python consolidate.py --dry-run    # 预览，不执行
    python consolidate.py --yes        # 跳过确认，自动执行
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# =========================================================================
# Data types
# =========================================================================

@dataclass
class S2PFile:
    """单个 .s2p 文件的解析信息。"""
    path: str           # 相对于 run 根目录的路径
    vna_dbm: float
    laser_mw: float
    target_k: float
    actual_k: float
    mtime: float = 0.0  # 文件修改时间（用于冲突裁决）


@dataclass
class RunInfo:
    """一个实验运行目录的扫描结果。"""
    id: str                          # 目录名
    path: str                        # 完整路径
    params_hash: str = ""            # (sorted Pv, sorted Pl) 的 SHA256 前 12 位
    target_temps: set = field(default_factory=set)
    s2p_count: int = 0
    timestamp: Optional[datetime] = None
    has_manifest: bool = False
    has_status: bool = False
    s2p_files: list = field(default_factory=list)
    vna_power_plan: list = field(default_factory=list)
    laser_power_plan: list = field(default_factory=list)


# =========================================================================
# S2P filename parsing
# =========================================================================

_S2P_RE = re.compile(
    r"YBCO_(-?\d+)dBm_(\d+)mW_target_([\d.]+)K_actual_([\d.]+)K\.s2p$"
)


def parse_s2p_filename(filename: str) -> Optional[S2PFile]:
    """Parse an s2p filename into its measurement parameters.

    Returns None if the filename doesn't match the expected pattern.
    """
    m = _S2P_RE.match(filename)
    if not m:
        return None
    return S2PFile(
        path="",  # caller fills in
        vna_dbm=float(m.group(1)),
        laser_mw=float(m.group(2)),
        target_k=float(m.group(3)),
        actual_k=float(m.group(4)),
    )


# =========================================================================
# Special directories to skip during scanning
# =========================================================================

SKIP_DIRS = {"~merged", "_junk", "_fragments", "_archive"}


# =========================================================================
# Parameter extraction
# =========================================================================

def _make_params_hash(vna_power_plan: list, laser_power_plan: list) -> str:
    """Deterministic hash of (sorted Pv, sorted Pl) for grouping."""
    payload = json.dumps([
        sorted(vna_power_plan),
        sorted(laser_power_plan),
    ], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def scan_run(dir_path: str) -> Optional[RunInfo]:
    """Scan a single experiment output directory and extract its parameters.

    Parameter sources (priority order):
      1. manifest.json
      2. status.json or checkpoint.json
      3. Infer from directory structure (walk .s2p files)

    Returns None if the directory should be skipped (special dirs).
    """
    dir_name = os.path.basename(dir_path)

    # Skip special directories
    if dir_name in SKIP_DIRS:
        return None

    # Check for already-consolidated marker
    for fname in os.listdir(dir_path):
        if fname.endswith(".txt") and "__" in fname and "pts" in fname:
            return None  # already consolidated

    info = RunInfo(id=dir_name, path=dir_path)

    # --- Step 1: Try manifest.json ---
    manifest_path = os.path.join(dir_path, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                m = json.load(f)
            info.has_manifest = True
            info.vna_power_plan = m.get("vna_power_plan", [])
            info.laser_power_plan = m.get("laser_power_plan", [])
            info.target_temps = set(m.get("temperature_plan", []))
            info.params_hash = _make_params_hash(
                info.vna_power_plan, info.laser_power_plan)
            ts = m.get("start_time", "")
            if ts:
                try:
                    info.timestamp = datetime.fromisoformat(ts)
                except ValueError:
                    pass
        except (json.JSONDecodeError, OSError):
            pass

    # --- Step 2: Try status.json ---
    if not info.has_manifest:
        status_path = os.path.join(dir_path, "status.json")
        if os.path.exists(status_path):
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                info.has_status = True
                info.vna_power_plan = s.get("vna_power_plan", [])
                info.laser_power_plan = s.get("laser_power_plan", [])
                info.target_temps = set(s.get("temperature_plan", []))
                info.params_hash = _make_params_hash(
                    info.vna_power_plan, info.laser_power_plan)
                ts = s.get("start_time", "")
                if ts:
                    try:
                        info.timestamp = datetime.fromisoformat(ts)
                    except ValueError:
                        pass
            except (json.JSONDecodeError, OSError):
                pass

    # --- Step 2b: Try checkpoint.json (if status didn't have plans) ---
    if not info.has_manifest and not info.has_status:
        ckpt_path = os.path.join(dir_path, "checkpoint.json")
        if os.path.exists(ckpt_path):
            try:
                with open(ckpt_path, "r", encoding="utf-8") as f:
                    c = json.load(f)
                info.vna_power_plan = c.get("original_vna_power_list", [])
                info.laser_power_plan = c.get("original_power_list", [])
                info.target_temps = set(c.get("original_temp_list", []))
                info.params_hash = _make_params_hash(
                    info.vna_power_plan, info.laser_power_plan)
                ts = c.get("timestamp", "")
                if ts:
                    try:
                        info.timestamp = datetime.fromisoformat(ts)
                    except ValueError:
                        pass
            except (json.JSONDecodeError, OSError):
                pass

    # --- Step 3: Walk s2p files ---
    s2p_files = []
    target_temps_from_files = set()
    vna_powers = set()
    laser_powers = set()
    for root, dirs, files in os.walk(dir_path):
        for fname in files:
            if not fname.endswith(".s2p"):
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, dir_path)
            parsed = parse_s2p_filename(fname)
            if parsed is None:
                continue
            parsed.path = rel_path
            parsed.mtime = os.path.getmtime(full_path)
            s2p_files.append(parsed)
            target_temps_from_files.add(parsed.target_k)
            vna_powers.add(parsed.vna_dbm)
            laser_powers.add(parsed.laser_mw)

    info.s2p_files = s2p_files
    info.s2p_count = len(s2p_files)

    # --- Step 4: Infer from directory structure if metadata was missing ---
    if not info.has_manifest and not info.has_status and not info.params_hash:
        if vna_powers and laser_powers:
            info.vna_power_plan = sorted(vna_powers)
            info.laser_power_plan = sorted(laser_powers)
            info.params_hash = _make_params_hash(
                info.vna_power_plan, info.laser_power_plan)

    if target_temps_from_files:
        info.target_temps = target_temps_from_files

    # --- Step 5: Fallback timestamp from dir name ---
    if info.timestamp is None:
        # Try to parse YYYYMMDD_HHMMSS from directory name
        ts_match = re.match(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
                            dir_name)
        if ts_match:
            try:
                parts = [int(g) for g in ts_match.groups()]
                info.timestamp = datetime(*parts)
            except ValueError:
                pass

    return info
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestScanRun -v 2>&1 | tail -15
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add consolidate.py tests/test_consolidate.py
git commit -m "feat: add scan_run with manifest/status/dir inference"
```

---

### Task 3: Grouping Engine — Identify Fragments That Belong Together

**Files:**
- Modify: `consolidate.py`
- Modify: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: `scan_run()` → `RunInfo`
- Produces: `group_runs(runs: list[RunInfo]) -> list[list[RunInfo]]`, `is_junk(run: RunInfo) -> bool`

- [ ] **Step 1: Write failing tests for group_runs and is_junk**

Append to `tests/test_consolidate.py`:

```python

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestIsJunk tests/test_consolidate.py::TestGroupRuns -v 2>&1 | tail -15
```

Expected: fail with `ImportError` (functions not yet defined).

- [ ] **Step 3: Implement is_junk and group_runs**

Append to `consolidate.py`:

```python
# =========================================================================
# Junk classification
# =========================================================================

JUNK_MAX_S2P = 5


def is_junk(run: RunInfo) -> bool:
    """A run is junk if it has very few s2p files AND no metadata.

    These are typically failed starts, empty shells, or aborted runs
    that produced no meaningful data.
    """
    has_metadata = run.has_manifest or run.has_status
    return run.s2p_count <= JUNK_MAX_S2P and not has_metadata


# =========================================================================
# Grouping
# =========================================================================

def _infer_temp_step(temps: set) -> float:
    """Infer the temperature step size from a set of temperatures.

    Defaults to 2 K if inference is not possible.
    """
    if len(temps) < 2:
        return 2.0
    sorted_t = sorted(temps)
    diffs = [sorted_t[i + 1] - sorted_t[i] for i in range(len(sorted_t) - 1)]
    if not diffs:
        return 2.0
    # Use the most common diff as the step size
    from collections import Counter
    return Counter(diffs).most_common(1)[0][0]


def _temps_are_adjacent(a: set, b: set, max_gap_steps: int = 1) -> bool:
    """Check if two temperature sets are adjacent or overlapping.

    Adjacent means the gap between the closest temperatures across
    the two sets is <= max_gap_steps * step_size.
    """
    if not a or not b:
        # If one has no temps, still group — it might be a metadata-only fragment
        return True
    if a & b:
        return True  # overlapping
    a_sorted = sorted(a)
    b_sorted = sorted(b)
    step = min(_infer_temp_step(a), _infer_temp_step(b))
    max_gap = max_gap_steps * step
    # Check all pairwise gaps
    for ta in a_sorted:
        for tb in b_sorted:
            if abs(ta - tb) <= max_gap:
                return True
    return False


def group_runs(runs: list) -> list[list]:
    """Group RunInfo objects by matching params AND adjacent/overlapping temps.

    Returns a list of groups, where each group is a list of RunInfo.
    """
    if not runs:
        return []

    # Sort by timestamp for stable grouping
    sorted_runs = sorted(runs, key=lambda r: (
        r.params_hash,
        min(r.target_temps) if r.target_temps else float("inf"),
        r.timestamp.isoformat() if r.timestamp else "",
    ))

    groups = []
    for run in sorted_runs:
        placed = False
        for group in groups:
            rep = group[0]  # representative
            if rep.params_hash == run.params_hash:
                # Same parameters — check temperature adjacency
                if _temps_are_adjacent(rep.target_temps, run.target_temps):
                    # Merge target temps into the representative for
                    # transitive grouping (A-B adjacent, B-C adjacent → all one group)
                    rep.target_temps = rep.target_temps | run.target_temps
                    group.append(run)
                    placed = True
                    break
        if not placed:
            groups.append([run])

    return groups
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestIsJunk tests/test_consolidate.py::TestGroupRuns -v 2>&1 | tail -20
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add consolidate.py tests/test_consolidate.py
git commit -m "feat: add is_junk and group_runs for fragment identification"
```

---

### Task 4: Dedup & Far-target Cleanup Logic

**Files:**
- Modify: `consolidate.py`
- Modify: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: `RunInfo.s2p_files` (list of S2PFile)
- Produces: `resolve_conflicts(group: list[RunInfo]) -> tuple[list[S2PFile], list[str]]`, `clean_far_target(s2p_files: list[S2PFile]) -> tuple[list[S2PFile], list[S2PFile]]`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_consolidate.py`:

```python

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestResolveConflicts tests/test_consolidate.py::TestCleanFarTarget -v 2>&1 | tail -15
```

Expected: fail with ImportError.

- [ ] **Step 3: Implement resolve_conflicts and clean_far_target**

Append to `consolidate.py`:

```python
# =========================================================================
# Dedup: resolve same-(T, Pv, Pl) conflicts across fragments
# =========================================================================

FAR_TARGET_THRESHOLD_K = 1.0


def _deviation(f: S2PFile) -> float:
    """Absolute deviation from target temperature."""
    return abs(f.actual_k - f.target_k)


def _is_stable(f: S2PFile) -> bool:
    """A measurement is 'stable' if actual T is within 1 K of target."""
    return _deviation(f) <= FAR_TARGET_THRESHOLD_K


def _key(f: S2PFile) -> tuple:
    """Composite key for grouping: (target_k, vna_dbm, laser_mw)."""
    return (f.target_k, f.vna_dbm, f.laser_mw)


def resolve_conflicts(group: list) -> tuple:
    """Resolve conflicts where same (T, Pv, Pl) appears in multiple fragments.

    Args:
        group: list of RunInfo belonging to the same consolidation group.

    Returns:
        (kept: list[S2PFile], warnings: list[str])
    """
    # Collect all s2p files grouped by (T, Pv, Pl)
    from collections import defaultdict
    buckets = defaultdict(list)
    for run in group:
        for f in run.s2p_files:
            buckets[_key(f)].append(f)

    kept = []
    warnings = []

    for k, copies in buckets.items():
        if len(copies) == 1:
            kept.append(copies[0])
            continue

        stable = [c for c in copies if _is_stable(c)]
        unstable = [c for c in copies if not _is_stable(c)]

        if len(stable) == 1:
            # Exactly one stable — keep it
            kept.append(stable[0])
        elif len(stable) >= 2:
            # Multiple stable — keep the latest (by mtime)
            winner = max(stable, key=lambda c: c.mtime)
            kept.append(winner)
        else:
            # All unstable — keep closest, warn
            winner = min(copies, key=_deviation)
            kept.append(winner)
            tgt = k[0]
            warnings.append(
                f"⚠ T={tgt}K: all copies unstable "
                f"(best Δ={_deviation(winner):.2f}K)"
            )

    return kept, warnings


# =========================================================================
# Far-target cleanup
# =========================================================================

def clean_far_target(s2p_files: list) -> tuple:
    """Delete s2p files where |actual - target| > 1 K.

    Within each (T, Pv, Pl) leaf, keep only the closest measurement.
    If all are far-target, keep the closest and report as warning.

    Args:
        s2p_files: list of S2PFile in the merged dataset.

    Returns:
        (kept: list[S2PFile], removed: list[S2PFile])
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for f in s2p_files:
        buckets[_key(f)].append(f)

    kept = []
    removed = []

    for k, copies in buckets.items():
        closest = min(copies, key=_deviation)
        kept.append(closest)
        for c in copies:
            if c is not closest:
                removed.append(c)

    return kept, removed
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestResolveConflicts tests/test_consolidate.py::TestCleanFarTarget -v 2>&1 | tail -20
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add consolidate.py tests/test_consolidate.py
git commit -m "feat: add resolve_conflicts and clean_far_target dedup logic"
```

---

### Task 5: Merge Engine & CLI — Consolidate and Write Output

**Files:**
- Modify: `consolidate.py`
- Modify: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: all previous functions
- Produces: `build_consolidated_name(group: list[RunInfo], total_pts: int) -> str`, `merge_group(group, kept_files, base_dir) -> str`, `main()`

- [ ] **Step 1: Write failing tests for naming and merge**

Append to `tests/test_consolidate.py`:

```python

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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestBuildConsolidatedName tests/test_consolidate.py::TestIsAlreadyConsolidated -v 2>&1 | tail -15
```

Expected: fail.

- [ ] **Step 3: Implement naming, merge, and CLI**

Append to `consolidate.py`:

```python
# =========================================================================
# Consolidated folder naming
# =========================================================================

def build_consolidated_name(group: list, total_pts: int) -> str:
    """Build the consolidated folder name.

    Format: {first_date}-{last_date}__{minT}-{maxT}K__{N}pts
    """
    # Find date range
    timestamps = [r.timestamp for r in group if r.timestamp is not None]
    if timestamps:
        first = min(timestamps)
        last = max(timestamps)
        first_str = first.strftime("%Y%m%d")
        last_str = last.strftime("%m%d")  # full YYYYMMDD-MMDD
    else:
        # Fallback: use directory name prefix
        ids = sorted([r.id for r in group])
        first_str = ids[0][:8] if len(ids[0]) >= 8 else ids[0]
        last_str = ids[-1][4:8] if len(ids[-1]) >= 8 else ids[-1]

    # Temperature range (integer)
    all_temps = set()
    for r in group:
        all_temps.update(r.target_temps)
    if all_temps:
        min_t = int(min(all_temps))
        max_t = int(max(all_temps))
    else:
        min_t = max_t = 0

    return f"{first_str}-{last_str}__{min_t}-{max_t}K__{total_pts}pts"


def _is_already_consolidated(dir_path: str) -> bool:
    """Check if a directory looks like it's already consolidated.

    Detected by presence of a marker .txt file with __ pattern in name.
    """
    try:
        for fname in os.listdir(dir_path):
            if fname.endswith(".txt") and "__" in fname and "pts" in fname:
                return True
    except OSError:
        pass
    return False


# =========================================================================
# Merge execution
# =========================================================================

MERGED_DIR_NAME = "~merged"
FRAGMENTS_DIR_NAME = "_fragments"
JUNK_DIR_NAME = "_junk"


def _merge_group(group: list, kept_files: list,
                 base_dir: str, dry_run: bool = False) -> str:
    """Merge a group of fragments into a unified consolidated directory.

    Args:
        group: list of RunInfo to merge
        kept_files: deduplicated S2PFile list to include
        base_dir: experiment_data directory
        dry_run: if True, only report what would happen

    Returns:
        Path to the newly created consolidated directory.
    """
    name = build_consolidated_name(group, len(kept_files))
    merged_dir = os.path.join(base_dir, MERGED_DIR_NAME, name)

    if dry_run:
        return merged_dir

    os.makedirs(merged_dir, exist_ok=True)
    fragments_dir = os.path.join(merged_dir, FRAGMENTS_DIR_NAME)
    os.makedirs(fragments_dir, exist_ok=True)

    # Copy s2p files into the merged tree
    s2p_by_temp = {}
    for f in kept_files:
        s2p_by_temp.setdefault(f.target_k, []).append(f)

    for target_k, files in s2p_by_temp.items():
        temp_dir = os.path.join(merged_dir, f"{target_k:g}K")
        for f in files:
            vna_dir = os.path.join(temp_dir, f"{f.vna_dbm:g}dBm")
            laser_dir = os.path.join(vna_dir, f"{f.laser_mw:02.0f}mW")
            os.makedirs(laser_dir, exist_ok=True)
            src = os.path.join(
                base_dir, group[0].id, f.path) if not os.path.isabs(f.path) else f.path
            # f.path is relative to the run root; resolve source
            for run in group:
                candidate = os.path.join(run.path, f.path)
                if os.path.exists(candidate):
                    src = candidate
                    break
            dst_filename = (
                f"YBCO_{f.vna_dbm:g}dBm_{f.laser_mw:02.0f}mW"
                f"_target_{f.target_k:g}K_actual_{f.actual_k:.3f}K.s2p"
            )
            dst = os.path.join(laser_dir, dst_filename)
            import shutil
            shutil.copy2(src, dst)

    # Merge logs
    logs_merged = os.path.join(merged_dir, "logs")
    os.makedirs(logs_merged, exist_ok=True)
    for run in group:
        run_logs = os.path.join(run.path, "logs")
        if os.path.isdir(run_logs):
            for fname in os.listdir(run_logs):
                src = os.path.join(run_logs, fname)
                dst = os.path.join(logs_merged, fname)
                if os.path.exists(dst):
                    # Append run id to avoid collision
                    base, ext = os.path.splitext(fname)
                    dst = os.path.join(logs_merged, f"{base}_{run.id}{ext}")
                import shutil
                shutil.copy2(src, dst)

    # Copy metadata files
    for meta_file in ("manifest.json", "status.json", "checkpoint.json",
                       "fill_complete.json", "readme.txt"):
        for run in group:
            src = os.path.join(run.path, meta_file)
            if os.path.exists(src):
                dst = os.path.join(merged_dir, meta_file)
                import shutil
                shutil.copy2(src, dst)
                break  # only first found

    # Write marker txt (empty)
    marker_path = os.path.join(merged_dir, f"{name}.txt")
    with open(marker_path, "w", encoding="utf-8") as f:
        pass  # empty — name IS the metadata

    # Move original fragments into _fragments/
    for run in group:
        dst = os.path.join(fragments_dir, run.id)
        if os.path.exists(run.path):
            import shutil
            shutil.move(run.path, dst)

    return merged_dir


def _move_junk(junk_runs: list, base_dir: str, dry_run: bool = False):
    """Move junk runs to _junk/ directory."""
    junk_dir = os.path.join(base_dir, JUNK_DIR_NAME)
    if not dry_run:
        os.makedirs(junk_dir, exist_ok=True)
    for run in junk_runs:
        if dry_run:
            print(f"  [DRY-RUN] Move {run.id} → {JUNK_DIR_NAME}/")
        else:
            dst = os.path.join(junk_dir, run.id)
            if os.path.exists(run.path):
                import shutil
                shutil.move(run.path, dst)
                print(f"  Moved {run.id} → {JUNK_DIR_NAME}/")


# =========================================================================
# CLI
# =========================================================================

def main():
    import argparse
    import config

    parser = argparse.ArgumentParser(
        description="YBCO 实验数据整合工具 — 扫描、去重、合并碎片化数据")
    parser.add_argument("--base-dir",
                        default=getattr(config, "experiment_data_base_dir",
                                       os.path.join(
                                           os.path.dirname(__file__),
                                           "experiment_data")),
                        help="实验数据根目录")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不执行任何实际操作")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="跳过所有确认提示，自动执行")
    args = parser.parse_args()

    base_dir = os.path.abspath(args.base_dir)
    if not os.path.isdir(base_dir):
        print(f"错误: 目录不存在 — {base_dir}")
        return 1

    print(f"=== 扫描 {base_dir} ===")

    # --- Scan ---
    runs = []
    skipped = 0
    for entry in os.listdir(base_dir):
        full = os.path.join(base_dir, entry)
        if not os.path.isdir(full):
            # Skip files (zips etc)
            continue
        info = scan_run(full)
        if info is None:
            skipped += 1
            continue
        runs.append(info)

    print(f"Found {len(runs)} runs, {skipped} skipped "
          f"(already consolidated or special dirs)")

    # --- Junk ---
    junk = [r for r in runs if is_junk(r)]
    non_junk = [r for r in runs if not is_junk(r)]

    if junk:
        print(f"\n--- Junk Candidates ({len(junk)}) ---")
        for j in junk:
            print(f"  {j.id} ({j.s2p_count} s2p, "
                  f"manifest={j.has_manifest}, status={j.has_status})")

        if not args.yes:
            ans = input("\nMove all to _junk/? [Y/n]: ").strip().lower()
            if ans and ans != "y":
                print("Skipped junk cleanup.")
                junk = []
        if junk:
            _move_junk(junk, base_dir, dry_run=args.dry_run)
            if args.dry_run:
                print(f"  [DRY-RUN] Would move {len(junk)} runs to _junk/")
            else:
                print(f"  Moved {len(junk)} runs to _junk/")

    # --- Group ---
    groups = group_runs(non_junk)
    print(f"\n--- Consolidation Groups ({len(groups)}) ---")

    for i, group in enumerate(groups):
        # Determine total s2p after dedup
        kept, warnings = resolve_conflicts(group)
        kept, removed_far = clean_far_target(kept)

        name = build_consolidated_name(group, len(kept))
        all_temps = set()
        for r in group:
            all_temps.update(r.target_temps)
        min_t = int(min(all_temps)) if all_temps else 0
        max_t = int(max(all_temps)) if all_temps else 0

        pv = group[0].vna_power_plan
        pl = group[0].laser_power_plan
        pv_str = f"[{min(pv):g}..{max(pv):g}]" if pv else "?"
        pl_str = f"[{min(pl):g}..{max(pl):g}]" if pl else "?"

        print(f"\nGroup {chr(65+i)}: Pv={pv_str}, Pl={pl_str}, "
              f"T=[{min_t}..{max_t}] → {len(kept)}pts")
        print(f"  Fragments:")
        for r in sorted(group, key=lambda x: x.timestamp or datetime.min):
            rt = sorted(r.target_temps) if r.target_temps else []
            t_range = f"{min(rt):g}-{max(rt):g}K" if rt else "no data"
            print(f"    {r.id}  T={t_range}  {r.s2p_count} s2p")

        if warnings:
            print(f"  Warnings:")
            for w in warnings:
                print(f"    {w}")

        if removed_far:
            print(f"  Far-target to delete: {len(removed_far)} files (Δ > 1K)")

        print(f"  → {name}")

        if not args.yes:
            ans = input(f"  Merge? [Y/n/skip]: ").strip().lower()
            if ans == "skip":
                print("  Skipped.")
                continue
            if ans and ans != "y":
                print("  Skipped.")
                continue

        # Execute
        if args.dry_run:
            print(f"  [DRY-RUN] Would merge → {MERGED_DIR_NAME}/{name}")
            if removed_far:
                print(f"  [DRY-RUN] Would delete {len(removed_far)} far-target files")
        else:
            # Delete far-target files
            for f in removed_far:
                # Find and delete
                for run in group:
                    candidate = os.path.join(run.path, f.path)
                    if os.path.exists(candidate):
                        os.remove(candidate)
                        break

            merged_path = _merge_group(group, kept, base_dir)
            print(f"  Merged → {os.path.relpath(merged_path, base_dir)}")

    print(f"\n=== Done ===")
    merged_count = len(os.listdir(os.path.join(base_dir, MERGED_DIR_NAME))) \
        if os.path.isdir(os.path.join(base_dir, MERGED_DIR_NAME)) else 0
    print(f"Groups merged: {merged_count}")
    if junk:
        print(f"Junk moved: {len(junk)}")
    return 0


if __name__ == "__main__":
    exit(main())
```

- [ ] **Step 4: Run all tests**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py -v 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 5: Dry-run against real data**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python consolidate.py --dry-run 2>&1 | head -60
```

Expected: output shows scan results, junk candidates, and consolidation groups without making changes. Verify the output looks reasonable.

- [ ] **Step 6: Commit**

```bash
git add consolidate.py tests/test_consolidate.py
git commit -m "feat: add merge engine and CLI to consolidate.py"
```

---

### Task 6: Integration Test — End-to-End with Real Data

**Files:**
- Modify: `tests/test_consolidate.py`

- [ ] **Step 1: Write integration test with synthetic but realistic data**

Append to `tests/test_consolidate.py`:

```python

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

            # Fragment 1: T=10-20K
            run1 = os.path.join(base, "20260611_115038")
            os.makedirs(run1)
            os.makedirs(os.path.join(run1, "logs"))
            with open(os.path.join(run1, "logs", "log1.txt"), "w") as f:
                f.write("exp start")
            # Write manifest
            m = {
                "experiment_id": "20260611_115038",
                "start_time": "2026-06-11T11:50:38",
                "temperature_plan": [10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
                "vna_power_plan": [-55, -45, -35],
                "laser_power_plan": [0, 5, 9],
            }
            with open(os.path.join(run1, "manifest.json"), "w") as f:
                json.dump(m, f)
            # Create s2p files
            for t in [10.0, 12.0]:
                for pv in [-55, -45]:
                    for pl in [0, 5]:
                        d = os.path.join(run1, f"{t:g}K", f"{pv:g}dBm", f"{pl:02.0f}mW")
                        os.makedirs(d)
                        fname = (
                            f"YBCO_{pv:g}dBm_{pl:02.0f}mW"
                            f"_target_{t:g}K_actual_{t-0.01:.3f}K.s2p"
                        )
                        with open(os.path.join(d, fname), "w") as f:
                            f.write("! s2p data")

            # Fragment 2: T=22-30K (continues from 20K), with overlap at 22K
            run2 = os.path.join(base, "20260612_014432")
            os.makedirs(run2)
            os.makedirs(os.path.join(run2, "logs"))
            m2 = {
                "experiment_id": "20260612_014432",
                "start_time": "2026-06-12T01:44:32",
                "temperature_plan": [20.0, 22.0, 24.0, 26.0, 28.0, 30.0],
                "vna_power_plan": [-55, -45, -35],
                "laser_power_plan": [0, 5, 9],
            }
            with open(os.path.join(run2, "manifest.json"), "w") as f:
                json.dump(m2, f)
            # Overlap at T=20K (also in run1), stable in both
            for t in [20.0, 22.0]:
                for pv in [-55, -45]:
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

            # One group
            groups = group_runs(runs)
            assert len(groups) == 1
            assert len(groups[0]) == 2

            # Dedup
            kept, warnings = resolve_conflicts(groups[0])
            # 20K should be deduped (2 copies → 1), 10K/12K/22K distinct
            # Total: 10K(12 files) + 12K(12 files) + 20K(12 files) + 22K(12 files) = 48
            assert len(kept) == 48, f"Expected 48, got {len(kept)}"

            # Clean far-target (all should be stable in this test)
            kept2, removed = clean_far_target(kept)
            assert len(removed) == 0

            # Merge
            name = build_consolidated_name(groups[0], len(kept2))
            assert "20260611" in name
            assert "10-22K" in name
            assert "48pts" in name

            merged = _merge_group(groups[0], kept2, base)
            assert os.path.isdir(merged)

            # Verify marker txt
            marker = os.path.join(merged, f"{name}.txt")
            assert os.path.exists(marker)

            # Verify s2p files in merged
            s2p_count = 0
            for root, dirs, files in os.walk(merged):
                s2p_count += sum(1 for f in files if f.endswith(".s2p"))
            assert s2p_count == 48

            # Verify fragments moved
            frag_dir = os.path.join(merged, "_fragments")
            assert os.path.isdir(frag_dir)
            assert os.path.isdir(os.path.join(frag_dir, "20260611_115038"))
            assert os.path.isdir(os.path.join(frag_dir, "20260612_014432"))
```

- [ ] **Step 2: Run integration test**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py::TestEndToEnd -v 2>&1 | tail -15
```

Expected: 1 passed.

- [ ] **Step 3: Run full test suite**

```bash
cd D:\YBCO\VNAMeas\Auto_Sweep && python -m pytest tests/test_consolidate.py -v 2>&1
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_consolidate.py
git commit -m "test: add end-to-end integration test for consolidate pipeline"
```
