# Data Consolidation Tool — Design Spec

**Date:** 2026-06-23
**Status:** approved

## 1. Problem

`experiment_data/` contains 37 top-level directories with 5,121 `.s2p` files (11 GB). Many
directories are fragmented pieces of a single logical experiment — interrupted runs that were
later resumed. Finding and assembling data for analysis (plotting, cross-comparison, debugging)
requires manual browsing across multiple timestamped folders.

Current pain points:

- **Fragmentation** — one continuous temperature sweep spread across 3–8 timestamped
  directories with no explicit linkage between them.
- **No at-a-glance identification** — directory names are raw timestamps; there is no way to
  tell what parameters a run used without opening the log or status file.
- **Junk accumulation** — 13 directories contain ≤5 `.s2p` files (failed starts, empty shells).
- **Duplicate measurements** — the same (T, Pv, Pl) point may have been measured multiple times
  across fragments, with no clear canonical copy.
- **Far-target data** — some `.s2p` files were recorded when the actual temperature had drifted
  >1 K from the target setpoint, making them unreliable for analysis.
- **Mixed naming conventions** — some directories use descriptive names (`40~60K&-15~-45dBm`),
  some use raw timestamps, and some are grouping folders (`accomplish`, `merged`).

## 2. Solution Overview

Two pieces:

| Component | Type | Purpose |
|-----------|------|---------|
| `manifest.json` | New file per experiment run | Forward-looking: each run self-describes its parameters at creation time. No inference needed for future data. |
| `consolidate.py` | New CLI tool | Backward + forward: scan `experiment_data/`, group fragments, deduplicate, clean far-target data, merge into named folders under `~merged/`. |

## 3. `manifest.json` — Forward-looking Metadata

Written by the experiment runner (`power_sweep_auto.py` or GUI worker) immediately after the
output directory is created, before any measurement begins.

### Schema

```json
{
  "experiment_id": "20260623_155113",
  "start_time": "2026-06-23T15:51:13",
  "temperature_plan": [50.0, 60.0, 70.0, 80.0],
  "vna_power_plan": [-55, -53, -51, -49, -47, -45, -43, -41, -39, -37, -35, -33, -31, -29, -27, -25],
  "laser_power_plan": [0, 1, 3, 5, 7, 9, 11, 13, 15, 17]
}
```

### Code Change

~15 lines added to the experiment start path (exact insertion site TBD during implementation —
candidate: `ui/workers/experiment_worker.py` or `power_sweep_auto.py`).

## 4. `consolidate.py` — Core Logic

### 4.1 Scan Phase

For each top-level directory in `experiment_data/`:

1. **Skip** if it is a special directory: `~merged`, `_junk`, `_fragments`, `_archive`.
2. **Skip** if it already contains a marker `.txt` (already consolidated).
3. **Extract parameters** (priority order):
   a. Read `manifest.json` → exact plans.
   b. Read `status.json` or `checkpoint.json` → extract `temperature_plan`, `vna_power_plan`,
      `laser_power_plan`.
   c. Parse the experiment log header lines (e.g. `温度列表: [...]`).
   d. Infer from directory structure (walk subdirectory names).
4. **Record** for each run:
   ```
   {
     id: str,           # directory name
     params_hash: str,  # hash of (sorted Pv_list, sorted Pl_list)
     temps: set[float],  # measured target temperatures
     s2p_count: int,
     timestamp: datetime,
     has_metadata: bool,
     min_actual_k: float,
     max_actual_k: float
   }
   ```

### 4.2 Junk Classification

A run is **junk** if:
- `s2p_count <= 5` AND `has_metadata == False`

Junk candidates are displayed and, on user confirmation, moved to `_junk/`.

### 4.3 Grouping Phase

```
Group = runs where:
  params_hash is identical (Pv_list and Pl_list are exactly equal)
  AND temperature ranges intersect or are adjacent (gap ≤ 1 step is fine)
```

- **Complementary** (no overlapping T) → straightforward merge.
- **Overlapping** (same T measured in multiple fragments) → dedup (see 4.4).

Each group gets a proposed consolidated name:
```
{first_date}-{last_date}__{minT}-{maxT}K__{total_pts}pts
```

Where:
- `first_date` = earliest start date among fragments (MMDD)
- `last_date` = latest start date among fragments (MMDD)  
- `minT` / `maxT` = min/max target temperature across the merged set (integer)
- `total_pts` = deduplicated s2p count

### 4.4 Dedup Phase (Same-T Conflict)

When the same target temperature `T` appears in multiple fragments within a group:

```
For each T:
  Look at the actual temperature recorded in each copy's s2p filename:
    YBCO_{Pv}dBm_{Pl}mW_target_{Ttarget}K_actual_{Tactual}K.s2p

  Deviation = |Tactual - Ttarget|

  If all copies have deviation ≤ 1.0 K:
    → All stable. Keep the one with the latest timestamp.
  If exactly one has deviation ≤ 1.0 K:
    → Keep the stable one, discard the rest. No prompt needed.
  If none have deviation ≤ 1.0 K:
    → Keep the one with smallest deviation. Report ⚠️ to user.

No interactive prompts during dedup — rules are deterministic.
```

### 4.5 Far-target Cleanup

Within each `{T}/{Pv}/{Pl}/` subdirectory:

```
If multiple .s2p files exist:
  Keep only the one with smallest |actual - target|.
  Delete the rest.

If deviation > 1.0 K for ALL copies:
  Keep the closest one, report as ⚠️ far_target warning.

Goal: exactly 1 .s2p per (T, Pv, Pl) leaf directory.
```

### 4.6 Execution Phase

For each confirmed group:

1. Create `~merged/{name}/` directory.
2. Merge `logs/` from all fragments (copy, don't overwrite — timestamp avoids collision).
3. For each target temperature subdirectory, merge `{Pv}/{Pl}/` trees with dedup applied.
4. Apply far-target cleanup (keep 1 s2p per leaf).
5. Write marker file: `{name}.txt` (empty).
6. Copy `manifest.json`, `status.json`, `checkpoint.json`, `readme.txt` (if present) to
   merged root for provenance.
7. Move original fragment directories into `~merged/{name}/_fragments/` for traceability.
8. Move junk runs to `_junk/`.

## 5. Directory Structure (After Consolidation)

```
experiment_data/
├── ~merged/                                    # ← output, tilde sorts to top
│   ├── 20260611-0614__10-80K__376pts/
│   │   ├── 20260611-0614__10-80K__376pts.txt   # ← marker (empty)
│   │   ├── manifest.json                       # ← merged manifest
│   │   ├── 10K/
│   │   │   └── -55dBm/
│   │   │       ├── 00mW/
│   │   │       │   └── YBCO_-55dBm_00mW_target_10K_actual_9.941K.s2p
│   │   │       └── 01mW/
│   │   ├── 12K/
│   │   ├── ...
│   │   ├── 80K/
│   │   ├── logs/
│   │   └── _fragments/                         # ← original timestamped dirs
│   │       ├── 20260611_115038/
│   │       ├── 20260612_014432/
│   │       └── ...
│   └── 20260615-0615__50K__160pts/
│       └── ...
├── 20260623_155113/          # ← still running / not yet consolidated
├── _junk/                    # ← failed starts, empty shells
└── _archive/                 # ← optional, for zip files
```

### Naming convention for `~merged/`

The `~` prefix ensures the merged folder sorts to the top in most file managers on Windows
(ASCII `~` = 126, before digits `0`–`9` = 48–57). This is a convention, not a hard requirement
— the user can rename the folder if desired.

## 6. User Interaction Flow

```
$ python consolidate.py

=== Scanning experiment_data/ ===
Found 37 directories, 5 untracked, 2 special (skipped)

--- Junk Candidates (13) ---
  [1] 20260611_062703 (0 s2p, no metadata)
  [2] 20260611_101227 (1 s2p, no metadata)
  ...
  Move all to _junk/? [Y/n]:

--- Consolidation Groups ---

Group A: Pv=[-55..-25], Pl=[0..17], T=[10..80] → 376pts
  Fragments:
    20260611_115038  T=10-28K  190 s2p
    20260612_014432  T=36-54K   95 s2p  ← gap: 28→36K
    20260612_095452  T=58-66K   25 s2p  ← gap: 54→58K
    20260612_145605  T=68K       5 s2p  ← overlap: 68K
    20260612_155002  T=68-72K   15 s2p  ← overlap: 68K
    ...
  → 20260611-0614__10-80K__376pts

  Conflicts:
    T=68K: 2 copies, both stable → keep later (20260612_155002)
    T=72K: 2 copies, 1 stable → keep stable (20260612_155002)
  Far-target to delete: 23 files (Δ > 1K)
  Merge? [Y/n/skip]:

Group B: Pv=[-45..-15], Pl=[0..17], T=[40..60] → 120pts
  20260618_150520  T=40-60K  120 s2p  (single fragment, rename only)
  → 20260618-0618__40-60K__120pts
  Merge? [Y/n/skip]:

=== Done ===
Merged: 2 groups → ~merged/
Junk moved: 13 → _junk/
Far-target deleted: 23 files
```

## 7. Files Changed

| File | Change | Lines (est.) |
|------|--------|--------------|
| `power_sweep_auto.py` or GUI worker | Write `manifest.json` at experiment start | ~15 |
| `consolidate.py` | **New file** — scan, group, dedup, merge, clean | ~350 |
| — | No other existing files touched | 0 |

## 8. Edge Cases & Safety

- **Empty fragment** (0 s2p): classified as junk.
- **No metadata at all**: infer from directory structure. If inference fails, skip and report.
- **Overlapping merge where both copies are unstable**: keep the closer one, flag with ⚠️.
- **Already-consolidated directory**: detected by presence of marker `.txt` — skipped.
- **Manually curated directories** (`T6-77K_...`, `40~60K...`, `accomplish`, `merged`):
  treated as regular runs — scanned, grouped if possible, otherwise renamed in place.
- **No destructive deletion**: original fragments are moved to `_fragments/`, not deleted.
  Only far-target `.s2p` files are deleted.
- **Dry-run mode**: `consolidate.py --dry-run` shows what would happen without making changes.

## 9. Out of Scope

- Monorepo `data/` directory (`D:\YBCO\VNAMeas\data\`) — too old, too varied, not worth
  automating.
- GUI integration — CLI only.
- Automatic periodic consolidation — user runs `consolidate.py` manually when needed.
