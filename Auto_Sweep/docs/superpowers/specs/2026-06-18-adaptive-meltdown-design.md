# 测量中漂移熔断自适应 — 设计文档

**日期:** 2026-06-18
**版本:** 1.0
**状态:** 待实现

---

## 1. 动机

2026-06-17 50K 实验暴露出两个问题：

1. **熔断耗尽后直接跳过温度点**：同一温度点连续 4 次熔断（3 次重启上限），导致 -35/-25/-15 dBm 共 27 个测量点缺失。事后需要手动补测，流程割裂。
2. **熔断参数不可自适应**：0.25K 阈值和固定 20s/60s 沉降时间对所有温区一视同仁，50K 温区激光加热效应显著（Δ 高达 +0.176K），参数偏紧。

此外，代码审查发现一个已有 bug：`_poll_commands` 收到的 `relax_meltdown` 干预命令写入了 `self._meltdown_threshold_k`，但熔断检测实际读取 `config.inter_measurement_max_delta_k`，导致阈值干预从未生效。

## 2. 目标

1. **运行中自适应**：Worker 检测到测量中温度漂移熔断后，自动调整沉降时间和熔断阈值，不依赖 Claude 干预
2. **熔断耗尽不复测（非跳过）**：达到原重启上限时，用放宽参数对该温度点所有 VNA 功率 × 激光功率组合重新测量
3. **修复已有 bug**：统一熔断检测使用实例变量而非硬编码 config 常量

## 3. 设计

### 3.1 参数递进

```
熔断 #1（测量中漂移，max−min > threshold）:
  ├── 沉降时间 ×8（实验全局生效）
  │     激光功率切换沉降:     20s → 160s
  │     首次上电沉降:         60s → 480s
  ├── 阈值不变（仍为 0.25K）
  └── 重启稳定 → 从断点继续

熔断 #2:
  ├── 沉降时间 ×15
  │     激光功率切换沉降:     20s → 300s
  │     首次上电沉降:         60s → 900s
  ├── 阈值放宽至 0.45K
  └── 重启稳定 → 从断点继续

熔断 #3（原重启上限）:
  ├── 不跳过温度点
  ├── 进入「复测模式」：该温度点下所有 VNA 功率 × 激光功率从头重测
  ├── 跳过已存在的 .s2p 文件（去重保护）
  ├── 参数保持：阈值 0.45K + 沉降 ×15

熔断 #4~5（复测阶段）:
  ├── 参数保持
  └── 再失败 → 真正跳过该温度点，记录 skip 事件
```

### 3.2 新增配置常量（config.py）

```python
# 测量中漂移熔断自适应
meltdown_settling_multipliers = [8, 15]   # 第1次×8, 第2次×15
meltdown_relaxed_threshold_k = 0.45       # 第2次放宽后的阈值
retry_mode_max_meltdowns = 2              # 复测模式额外熔断次数
```

### 3.3 Worker 端改动（ui/workers.py）

改动集中在 `ExperimentWorker._run_impl()` 的熔断处理段（约第 2094-2247 行）。

**新增实例变量**（`__init__` 或首次使用前）：

```python
self._drift_meltdown_count = 0        # 当前温度点漂移熔断计数
self._settling_multiplier = 1.0       # 沉降倍率
self._effective_meltdown_threshold = config.inter_measurement_max_delta_k  # 0.25K
```

**熔断检测改用实例变量**：

```python
# 旧：if max_min_delta > config.inter_measurement_max_delta_k
# 新：
if max_min_delta > self._effective_meltdown_threshold:
```

**自适应逻辑**（在 `measurement_restarts` 递增后）：

```python
drift_meltdown_count += 1

if drift_meltdown_count == 1:
    self._settling_multiplier = config.meltdown_settling_multipliers[0]  # 8
elif drift_meltdown_count == 2:
    self._settling_multiplier = config.meltdown_settling_multipliers[1]  # 15
    self._effective_meltdown_threshold = config.meltdown_relaxed_threshold_k  # 0.45
elif drift_meltdown_count >= 3:
    if drift_meltdown_count == 3:
        self._enter_retry_mode()  # 重置功率索引，标记复测模式
    # 继续（熔断 #4~5 才真正跳过）
```

**沉降时间应用**：

```python
effective_settle = config.laser_settle_time_s * self._settling_multiplier
effective_first_on = config.laser_first_on_settle_time_s * self._settling_multiplier
```

**温度点切换时重置**：

```python
self._drift_meltdown_count = 0
self._settling_multiplier = 1.0
self._effective_meltdown_threshold = config.inter_measurement_max_delta_k
```

### 3.4 不复测的范围

- 不复测「测量前温度偏离」熔断（`Δ > 0.5K`）——该类型仍走原有重启流程
- 不复测 CLI runner（`power_sweep_auto.py`）——仅限 GUI Worker
- 不复测 `_run_fill()` 补测模式——补测模式保持简化逻辑

### 3.5 与现有干预系统的关系

- 保留 `relax_meltdown` 干预命令作为外部覆盖通道（Claude 可在 Worker 未检测到模式时手动放宽）
- 干预命令现在写入 `self._effective_meltdown_threshold`（之前写入无效的 `self._meltdown_threshold_k` 将被废弃）
- `analyze_for_intervention()` 中的 `relax_meltdown` 规则阈值从 0.35 更新为 0.45（与自适应一致）

## 4. 需要修改的文件

| 文件 | 操作 | 内容 |
|------|------|------|
| `config.py` | 修改 | 新增 3 个常量 |
| `ui/workers.py` | 修改 | `_run_impl()` 熔断段重写 + 新增实例变量 + 沉降应用 + 复测模式 |
| `claude_monitor.py` | 修改 | `relax_meltdown` 阈值 0.35→0.45，对齐自适应值 |

## 5. 测试计划

| 测试 | 描述 |
|------|------|
| `test_meltdown_settling_escalation` | 验证熔断次数递增时沉降倍率正确切换 |
| `test_meltdown_threshold_relaxed` | 验证第 2 次熔断后阈值变为 0.45K |
| `test_retry_mode_entered` | 验证第 3 次熔断进入复测模式而非跳过 |
| `test_retry_mode_exhausted` | 验证复测 2 次后再失败才跳过 |
| `test_settling_reset_on_temp_change` | 验证温度点切换时参数重置 |
| `test_settling_applied_in_measurement` | 验证沉降时间实际使用倍率后的值 |
| `test_retry_mode_all_combinations` | 验证复测模式覆盖所有 VNA×laser 组合 |
| `test_pre_measurement_meltdown_unchanged` | 验证测量前偏离熔断不受影响 |
| `test_effective_threshold_used` | 回归验证熔断检测使用实例变量 |

## 6. 设计约束

1. **仅影响测量中漂移熔断**（`max−min > threshold`），不改变测量前温度偏离熔断逻辑
2. **温度点切换时参数重置**：每个新温度点从默认参数开始
3. **复测模式去重**：已存在的 .s2p 文件不重复保存
4. **一次只改 2 个文件**（+1 个配置对齐），不碰 CLI runner、fill 模式、GUI 页面
