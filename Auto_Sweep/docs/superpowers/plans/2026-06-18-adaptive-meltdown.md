# Adaptive Meltdown Retry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Worker 检测到测量中温度漂移熔断后自动调整沉降时间与阈值，熔断耗尽时进入复测模式而非跳过温度点。

**Architecture:** 修改集中在 `ui/workers.py` 的 `_run_impl()` 熔断处理段。新增实例变量追踪漂移熔断计数，沉降时间和阈值改为从实例变量读取（修复已有 bug）。复测模式复用现有 VNA×laser 测量循环。

**Tech Stack:** Python 3, no new dependencies

## Global Constraints

- 仅影响测量中漂移熔断（`max−min > threshold`），不改变测量前温度偏离熔断
- 温度点切换时参数重置
- 复测模式去重（跳过已存在的 .s2p）
- 一次只改 3 个文件

---

### Task 1: 新增 config.py 常量

**Files:**
- Modify: `config.py:149`（在 `max_meltdown_restarts` 下方插入）

- [ ] **Step 1: 插入配置常量**

在 `config.py` 第 149 行 `max_meltdown_restarts = 3` 后插入：

```python
# 测量中漂移熔断自适应
meltdown_settling_multipliers = [8, 15]   # 第1次×8, 第2次×15
meltdown_relaxed_threshold_k = 0.45       # 第2次放宽后的阈值
retry_mode_max_meltdowns = 2              # 复测模式额外熔断次数
```

- [ ] **Step 2: 验证导入**

```bash
python -c "import config; print(config.meltdown_relaxed_threshold_k); print(config.meltdown_settling_multipliers)"
```
Expected: `0.45` and `[8, 15]`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add adaptive meltdown config constants"
```

---

### Task 2: 修复熔断阈值 bug + 新增自适应实例变量

**Files:**
- Modify: `ui/workers.py:818`（`__init__` 内 `_meltdown_threshold_k` 初始化）
- Modify: `ui/workers.py:847-848`（`configure` 内）
- Modify: `ui/workers.py:1946-1947`（`_run_impl` 内 measurement 循环前）
- Modify: `ui/workers.py:2100-2104`（熔断检测行）

- [ ] **Step 1: 修改 `__init__` — 追加新实例变量**

在 `ui/workers.py` 第 818 行后添加：

```python
        self._drift_meltdown_count = 0        # 当前温度点漂移熔断计数
        self._settling_multiplier = 1.0       # 沉降倍率（1.0 = 默认）
        self._in_retry_mode = False           # 是否处于复测模式
```

- [ ] **Step 2: 修改 `configure` — 统一用实例变量初始化**

将 `ui/workers.py` 第 847-848 行：

```python
        self._meltdown_threshold_k = getattr(
            config, "inter_measurement_max_delta_k", 0.25)
```

改为：

```python
        self._meltdown_threshold_k = getattr(
            config, "inter_measurement_max_delta_k", 0.25)
        # 自适应熔断实例变量（每个温度点重置）
        self._drift_meltdown_count = 0
        self._settling_multiplier = 1.0
        self._in_retry_mode = False
```

- [ ] **Step 3: 修改熔断检测 — 使用实例变量而非 config 常量**

将 `ui/workers.py` 第 2104 行：

```python
                                if temp_range > config.inter_measurement_max_delta_k:
```

改为：

```python
                                if temp_range > self._meltdown_threshold_k:
```

- [ ] **Step 4: 在测量重启循环前初始化漂移计数**

在 `ui/workers.py` 第 1947 行 `measurement_restarts = 0` 后添加：

```python
                measurement_restarts = 0
                self._drift_meltdown_count = 0   # 每个温度点重置
                self._settling_multiplier = 1.0
                self._in_retry_mode = False
```

- [ ] **Step 5: 运行现有熔断相关测试确保不回归**

```bash
python -m pytest tests/ -x -q --tb=short -k "meltdown"
```

- [ ] **Step 6: Commit**

```bash
git add ui/workers.py
git commit -m "fix: use instance variable for meltdown threshold, add adaptive tracking vars"
```

---

### Task 3: 熔断后自适应调整沉降时间与阈值

**Files:**
- Modify: `ui/workers.py:2136-2139`（熔断 break 前插入自适应逻辑）
- Modify: `ui/workers.py:2152-2155`（沉降时间使用倍率）

- [ ] **Step 1: 在熔断 break 前插入自适应递进逻辑**

在 `ui/workers.py` 第 2137 行 `measurement_ok = False` 之后、`deleted_any = True` 之前插入：

```python
                                    # ---- 自适应参数调整 ----
                                    self._drift_meltdown_count += 1
                                    if self._drift_meltdown_count == 1:
                                        self._settling_multiplier = float(
                                            config.meltdown_settling_multipliers[0])
                                        _log(
                                            f"  🔧 熔断 #{measurement_restarts + 1}"
                                            f" → 沉降时间 ×"
                                            f"{self._settling_multiplier:.0f}"
                                            f"（激光 {config.laser_settle_time_s * self._settling_multiplier:.0f}s"
                                            f" / 首次上电 {config.laser_first_on_settle_time_s * self._settling_multiplier:.0f}s）")
                                    elif self._drift_meltdown_count == 2:
                                        self._settling_multiplier = float(
                                            config.meltdown_settling_multipliers[1])
                                        self._meltdown_threshold_k = \
                                            config.meltdown_relaxed_threshold_k
                                        _log(
                                            f"  🔧 熔断 #{measurement_restarts + 1}"
                                            f" → 沉降时间 ×"
                                            f"{self._settling_multiplier:.0f}"
                                            f" + 阈值放宽至 "
                                            f"{self._meltdown_threshold_k}K")
                                    elif self._drift_meltdown_count == 3:
                                        _log(
                                            f"  🔧 熔断 #{measurement_restarts + 1}"
                                            f" → 进入复测模式（重测所有 VNA×laser）")
                                        self._in_retry_mode = True
                                    elif self._drift_meltdown_count > 3:
                                        _log(
                                            f"  🔧 复测熔断 "
                                            f"(#{self._drift_meltdown_count - 3}"
                                            f"/{config.retry_mode_max_meltdowns})")
```

- [ ] **Step 2: 沉降时间应用倍率**

将 `ui/workers.py` 第 2152-2154 行：

```python
                                settle_s = (config.laser_first_on_settle_time_s
                                            if laser_was_off and power_mw > 0
                                            else config.laser_settle_time_s)
```

改为：

```python
                                settle_s = (config.laser_first_on_settle_time_s
                                            if laser_was_off and power_mw > 0
                                            else config.laser_settle_time_s)
                                settle_s = settle_s * self._settling_multiplier
```

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/ -x -q --tb=short -k "meltdown or stability"
```

- [ ] **Step 4: Commit**

```bash
git add ui/workers.py
git commit -m "feat: adaptive settling time and threshold escalation on drift meltdown"
```

---

### Task 4: 复测模式 — 熔断 #3 重测所有 VNA×laser

**Files:**
- Modify: `ui/workers.py:2239-2264`（熔断上限检查段 — 改写为条件跳过）

- [ ] **Step 1: 改写熔断上限检查段**

将 `ui/workers.py` 第 2239-2264 行：

```python
                    if measurement_restarts >= config.max_meltdown_restarts:
                        _log(f"  ⛔ 熔断重启已达上限 "
                             f"({measurement_restarts}/{config.max_meltdown_restarts})，"
                             f"跳过温度点 {target_k:.1f}K")
                        # Claude 监控: 记录跳过
                        try:
                            if self._status_writer:
                                self._status_writer.add_skipped(
                                    target_k=target_k,
                                    reason="meltdown_limit",
                                    vna_power_remaining=self._vna_power_list,
                                )
                        except Exception:
                            pass
                        # 确保激光已关闭再跳至下一温度点
                        if self._laser_ctrl:
                            try:
                                self._laser_ctrl.set_power(0)
                                self._laser_ctrl.output_off()
                            except Exception:
                                pass
                        self._laser_was_off = True
                        # 需求 B: 熔断跳过计入连续问题
                        _rollback_state.record_result(temp_idx, "meltdown_skip")
                        break  # 跳出 while True → 下一温度点
```

改为：

```python
                    # 复测模式: 额外熔断次数耗尽才跳过
                    _skip_now = False
                    if self._in_retry_mode:
                        retry_meltdowns = (self._drift_meltdown_count
                                           - config.max_meltdown_restarts)
                        if retry_meltdowns > config.retry_mode_max_meltdowns:
                            _log(f"  ⛔ 复测熔断已达上限，跳过温度点 {target_k:.1f}K")
                            _skip_now = True
                    elif measurement_restarts >= config.max_meltdown_restarts:
                        _log(f"  ⛔ 熔断重启已达上限 "
                             f"({measurement_restarts}/{config.max_meltdown_restarts})，"
                             f"跳过温度点 {target_k:.1f}K")
                        _skip_now = True

                    if _skip_now:
                        # Claude 监控: 记录跳过
                        try:
                            if self._status_writer:
                                self._status_writer.add_skipped(
                                    target_k=target_k,
                                    reason="meltdown_limit",
                                    vna_power_remaining=self._vna_power_list,
                                )
                        except Exception:
                            pass
                        if self._laser_ctrl:
                            try:
                                self._laser_ctrl.set_power(0)
                                self._laser_ctrl.output_off()
                            except Exception:
                                pass
                        self._laser_was_off = True
                        _rollback_state.record_result(temp_idx, "meltdown_skip")
                        break
```

- [ ] **Step 2: 复测模式进入时重置 VNA 功率索引**

在熔断 #3 进入复测模式的逻辑之后（第 3 步插入的代码块中 `self._in_retry_mode = True` 之后），添加日志但不需要重置 `vi`/`pi`——它们会由 `break` 跳出内循环后，外层 `while True` 重新进入时从 `vna_powers[0]` 和 `power_list[0]` 开始。

确认外层 `for vi, vna_dbm in enumerate(vna_powers)` 会在 `measurement_ok = False` break 后重新从 0 开始。

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/ -x -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add ui/workers.py
git commit -m "feat: retry mode — re-measure all VNA×laser after 3rd meltdown"
```

---

### Task 5: 对齐 claude_monitor.py 阈值 + 写测试

**Files:**
- Modify: `claude_monitor.py:77`（`relax_meltdown` 阈值 0.35→0.45）
- Create: `tests/test_adaptive_meltdown.py`

- [ ] **Step 1: 更新 claude_monitor.py 阈值**

将 `claude_monitor.py` 第 77 行：

```python
                "params": {"new_threshold_k": 0.35, "target_k": temp_k},
```

改为：

```python
                "params": {"new_threshold_k": 0.45, "target_k": temp_k},
```

- [ ] **Step 2: 写测试文件**

创建 `tests/test_adaptive_meltdown.py`：

```python
# -*- coding: utf-8 -*-
"""测量中漂移熔断自适应测试"""

import pytest
import config


class TestAdaptiveMeltdownConfig:
    """验证新增配置常量存在且类型正确。"""

    def test_meltdown_settling_multipliers(self):
        assert hasattr(config, "meltdown_settling_multipliers")
        assert config.meltdown_settling_multipliers == [8, 15]

    def test_meltdown_relaxed_threshold(self):
        assert hasattr(config, "meltdown_relaxed_threshold_k")
        assert config.meltdown_relaxed_threshold_k == 0.45

    def test_retry_mode_max_meltdowns(self):
        assert hasattr(config, "retry_mode_max_meltdowns")
        assert config.retry_mode_max_meltdowns == 2


class TestAdaptiveLogic:
    """纯逻辑测试 — 不依赖硬件，直接测试自适应状态机。"""

    @staticmethod
    def _simulate_meltdown_escalation():
        """模拟熔断递进逻辑，返回各阶段的 (settling_mult, threshold, in_retry)。"""
        multipliers = config.meltdown_settling_multipliers
        relaxed_k = config.meltdown_relaxed_threshold_k
        retry_max = config.retry_mode_max_meltdowns

        stages = []
        settling = 1.0
        threshold = config.inter_measurement_max_delta_k
        in_retry = False

        for count in range(1, 7):
            if count == 1:
                settling = float(multipliers[0])
            elif count == 2:
                settling = float(multipliers[1])
                threshold = relaxed_k
            elif count == 3:
                in_retry = True
            elif count > 3:
                retry_num = count - config.max_meltdown_restarts
                if retry_num > retry_max:
                    break  # skip temp point

            stages.append({
                "count": count,
                "settling": settling,
                "threshold": threshold,
                "in_retry": in_retry,
            })

        return stages

    def test_stage1_settling_x8(self):
        stages = self._simulate_meltdown_escalation()
        s1 = stages[0]
        assert s1["count"] == 1
        assert s1["settling"] == 8.0
        assert s1["threshold"] == 0.25
        assert not s1["in_retry"]

    def test_stage2_settling_x15_threshold_relaxed(self):
        stages = self._simulate_meltdown_escalation()
        s2 = stages[1]
        assert s2["count"] == 2
        assert s2["settling"] == 15.0
        assert s2["threshold"] == 0.45
        assert not s2["in_retry"]

    def test_stage3_retry_mode(self):
        stages = self._simulate_meltdown_escalation()
        s3 = stages[2]
        assert s3["count"] == 3
        assert s3["in_retry"]

    def test_stage4_5_retry_within_limit(self):
        stages = self._simulate_meltdown_escalation()
        assert len(stages) == 5  # count 1-5, 5th is retry #2
        assert stages[3]["count"] == 4
        assert stages[3]["in_retry"]
        assert stages[4]["count"] == 5
        assert stages[4]["in_retry"]

    def test_stage6_exceeds_retry_limit(self):
        stages = self._simulate_meltdown_escalation()
        # 熔断 #6 = retry #3 > retry_mode_max_meltdowns (2) → 跳过
        counts = [s["count"] for s in stages]
        assert 6 not in counts  # stage6 broke out early

    def test_settling_multiplier_applied(self):
        """验证沉降时间正确应用倍率。"""
        base_settle = config.laser_settle_time_s  # 20
        base_first = config.laser_first_on_settle_time_s  # 60

        assert base_settle * 8 == 160
        assert base_first * 8 == 480
        assert base_settle * 15 == 300
        assert base_first * 15 == 900
```

- [ ] **Step 3: 运行测试验证**

```bash
python -m pytest tests/test_adaptive_meltdown.py -v -q --tb=short
```

Expected: 8 passed

- [ ] **Step 4: 运行全量测试确保不回归**

```bash
python -m pytest tests/ -x -q --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add claude_monitor.py tests/test_adaptive_meltdown.py
git commit -m "feat: align relax_meltdown threshold 0.35→0.45, add adaptive meltdown tests"
```
