# Recovery Fast Stability + Overshoot Learning Fix

**Date**: 2026-06-24
**Status**: 已批准
**Target**: 恢复实验时跳过不必要的 over shoot，并修复 overshoot 学习数据持久化链路

---

## Problem

实验中断后恢复时，程序机械地先应用默认 overshoot setpoint（target + 2K），即使样品已经在目标温度附近稳定很久。同时，overshoot 学习模块（`ExperimentStabilityController.record_result()`）从未实际生效——数据只在实验完全结束时落盘，且 checkpoint 内的 overshoot 数据恒为空。

### Root Cause Breakdown

| # | 问题 | 根因 |
|---|------|------|
| P1 | 恢复时盲目走 overshoot | 没有恢复场景的快速预检逻辑 |
| P2 | `_checkpoint_overshoot` 永远为 `{}` | L2590 只更新 `_overshoot_learning`（局部变量），未同步 `self._checkpoint_overshoot` |
| P3 | checkpoint 的 overshoot 数据从未被应用 | `_resume_from_checkpoint()` 加载了 `self._checkpoint_overshoot`，但 `_overshoot_learning` 仅从 `app_settings.json` 加载，两者隔离 |
| P4 | 学习数据中断即丢失 | `app_settings.json` 仅在实验完全结束（L2640）时保存 |

### Impact

P1–P4 形成死锁：每次实验中断 → 学习数据丢失 → 恢复时无学习数据可用 → 机械 overshoot → 浪费时间 → 下次还可能中断 → 循环。

---

## Design

### 1. `stability_monitor.py` — 新增 `fast_stability_check()`

在 `AdvancedStabilityMonitor` 中新增一个无状态的快速预检方法，供上层在写入 setpoint 前调用：

```python
@staticmethod
def fast_stability_check(read_temperature_fn, target_k: float) -> dict:
    """快速预检：判断是否可跳过 overshoot。

    两阶段：
      Phase A（预检）: 2 次读数，间隔 10s
        → 两次都在 target ±1.0K 内 → 进入 Phase B
        → 否则 → {"skip_overshoot": False, "reason": "far_from_target"}

      Phase B（快速稳判）: 60s 内每 10s 读一次（共 ~6 个读数）
        → max − min ≤ 0.2K AND avg 在 target ±0.5K 内
          → {"skip_overshoot": True, "avg_temp": avg, ...}
        → 否则
          → {"skip_overshoot": False, "reason": "unstable"}

    Args:
        read_temperature_fn: 无参 callable，返回 float (K) 或 None
        target_k: 目标温度 (K)

    Returns:
        {"skip_overshoot": bool, "reason": str, "avg_temp": float, ...}
    """
```

**参数选择依据**：
- 预检 10s 间隔：LakeShore 读数延迟 ~1s，10s 足够看到真实温度变化
- ±1.0K 预检阈值：宽于 `SPARSE_BAND_K`(1.0K)，匹配稀疏判据
- 60s 快速稳判：在 `SPARSE_MIN_TIME_S`(60s) 范围内，与正常 2-phase 不冲突
- 0.2K max-min：略宽于 `steady_state_max_min_k`(0.1K)，因采样时间短
- ±0.5K avg 阈值：与 `final_stable_band_k`(0.5K) 一致

### 2. `ui/workers.py` — 恢复时触发快速预检

在 `_run_impl()` 中，从 checkpoint 恢复后的**第一个温度点**，在创建 `stability_ctrl` 之前插入：

```
checkpoint 恢复 → 确定 resume temp_idx
  │
  ├─ 合并 self._checkpoint_overshoot → _overshoot_learning  (Fix P3)
  │
  └─ 第一个温度点 (temp_idx == resume_idx):
       │
       ├─ fast_stability_check(read_temp_fn, target_k)
       │    ├─ skip_overshoot=True
       │    │    → stability_ctrl.setup() 后，
       │    │      手动设置 current_overshoot = 0
       │    │      跳入 Phase 2 (FINE) 快速稳判
       │    │      60s 内稳定 → 直接测量 ✓
       │    │      不稳定 → 回退正常 overshoot 流程
       │    │
       │    └─ skip_overshoot=False
       │         → 正常流程（overshoot + 2-phase）
       │           但使用已学习的 overshoot 值（如果有）
       │
       └─ 后续温度点 → 正常流程
```

**为什么只对恢复后第一个温度点触发**：正常升温切换时（如 50K→52K），样品温度几乎不可能恰好在下一个目标附近，预检只会浪费时间。仅在恢复场景下（样品可能已在目标附近停留很久）预检有意义。

### 3. `ui/workers.py` — Overshoot 学习增量持久化

修改 `record_result()` 调用点（L2586–2593），增加：

```python
# 原代码
_overshoot_learning.update(stability_ctrl.get_overshoot_learning())

# 新增：同步 checkpoint (Fix P1)
self._checkpoint_overshoot = dict(_overshoot_learning)

# 新增：立即增量保存到 app_settings.json (Fix P4)
_save_overshoot_learning_incremental(_overshoot_learning)
```

新增辅助函数 `_save_overshoot_learning_incremental()`，逻辑与 L2640–2657 相同但提取为独立函数。

### 4. `ui/workers.py` — Checkpoint 恢复时合并学习数据

在 `_resume_from_checkpoint()` 返回后，将 `self._checkpoint_overshoot` 合并到 `_overshoot_learning`：

```python
if hasattr(self, "_checkpoint_overshoot") and self._checkpoint_overshoot:
    for k, v in self._checkpoint_overshoot.items():
        if k not in _overshoot_learning:
            _overshoot_learning[k] = v
    _log(f"从 checkpoint 合并 overshoot 学习数据: "
         f"{len(self._checkpoint_overshoot)} 个温度点")
```

---

## Files Changed

| File | Change |
|------|--------|
| `stability_monitor.py` | 新增 `fast_stability_check()` 静态方法 |
| `ui/workers.py` | Bugfix P1/P2/P3/P4 + recovery fast-track 逻辑 |
| `ui/experiment_stability_controller.py` | 新增 `force_skip_overshoot()` 方法（设置 overshoot=0 + 跳入 FINE phase） |

---

## Non-functional

- 快速预检最长耗时 ~80s（2×10s + 6×10s），远短于正常 overshoot 流程（10–60 min）
- `fast_stability_check()` 为静态方法，纯算法，无副作用，可独立单测
- 增量保存使用原子写入（`.tmp` + `os.replace`），防止写一半崩溃
- 向后兼容：不改变现有 checkpoint 结构，仅修正数据同步逻辑

---

## Spec Self-Review

- [x] 无 TBD/TODO
- [x] 内部一致：4 个改动相互配合，修复完整链路
- [x] 范围可控：3 个文件，无新依赖
- [x] 无歧义：每个阶段输入/输出明确定义
