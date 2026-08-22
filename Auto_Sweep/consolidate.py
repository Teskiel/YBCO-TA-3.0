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

SKIP_DIRS = {"~merged", "_junk", "_fragments", "_archive",
              "accomplish", "accomplish_merged", "merged"}


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


# =========================================================================
# Junk classification
# =========================================================================

JUNK_MAX_S2P = 5


def is_junk(run: RunInfo) -> bool:
    """A run is junk if it has very few s2p files AND no metadata,
    OR if it has zero s2p files (empty shell, always junk).

    These are typically failed starts, empty shells, or aborted runs
    that produced no meaningful data.
    """
    if run.s2p_count == 0:
        return True  # empty shell — always junk, regardless of metadata
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
        # Strip timezone info to avoid comparing naive and aware datetimes
        first = min(t.replace(tzinfo=None) for t in timestamps)
        last = max(t.replace(tzinfo=None) for t in timestamps)
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
    import shutil

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
            # f.path is relative to the run root; resolve source
            src = None
            for run in group:
                candidate = os.path.join(run.path, f.path)
                if os.path.exists(candidate):
                    src = candidate
                    break
            if src is None:
                continue
            dst_filename = (
                f"YBCO_{f.vna_dbm:g}dBm_{f.laser_mw:02.0f}mW"
                f"_target_{f.target_k:g}K_actual_{f.actual_k:.3f}K.s2p"
            )
            dst = os.path.join(laser_dir, dst_filename)
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
                shutil.copy2(src, dst)

    # Copy metadata files
    for meta_file in ("manifest.json", "status.json", "checkpoint.json",
                      "fill_complete.json", "readme.txt"):
        for run in group:
            src = os.path.join(run.path, meta_file)
            if os.path.exists(src):
                dst = os.path.join(merged_dir, meta_file)
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
        for r in sorted(group, key=lambda x: (x.timestamp or datetime.min).replace(tzinfo=None)):
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
