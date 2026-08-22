# 实验断点续传 & 自动重连 — 设计规范

日期: 2026-06-13 | 状态: 已批准 | 版本: 1.0

## 背景

`/checklog` 分析（2026-06-12 19:06 实验）暴露了关键问题：实验运行 2h 后在 76.0K 因 LakeShore `VI_ERROR_CONN_LOST` 异常中断，仅完成 25/90 测量点（28%）。当前 `ExperimentWorker.run()` 无连接错误恢复机制——任何 VISA 断开都导致实验报废。

本设计是**批次一**（断点续传 + 自动重连），后续批次二将改进熔断策略和预等待监控。

## 设计决策

| 决策 | 选择 |
|------|------|
| 续传粒度 | **测量点级** — (温度, VNA功率, 激光功率) |
| 错误检测 | **统一异常包装** — 顶层 try/except 兜底所有 VISA 错误 |
| 断开时加热器 | **不干预** — LakeShore 保持最后设定点自主运行 |
| 架构方案 | **方案 A：检查点文件 + 原地续传** |

## 架构

```
ExperimentWorker.run()
├── 初始化（日志、内存监控、回退状态机）
├── 检查点恢复询问（如有 checkpoint.json）
│   ├── "恢复" → 跳转到中断位置
│   ├── "重新开始" → 删除检查点
│   └── "取消" → 让用户手动处理
├── 主循环（现有逻辑 + CheckpointManager.append_point()）
│   └── 每完成 5 个测量点 → 增量保存检查点
├── 正常结束 → 删除 checkpoint.json
└── 异常捕获
    ├── _is_recoverable_error() → True → _enter_recovery()
    │   ├── 保存检查点
    │   ├── 重连循环（30s × 60 次 = 30min）
    │   ├── 成功 → 回到主循环顶部（检查点恢复）
    │   └── 超时 → 保存检查点，通知用户
    └── _is_recoverable_error() → False → experiment_error（不可恢复）
```

## 组件

### CheckpointManager

新建类，位于 `ui/workers.py`。

**职责**：
- 原子写入 `checkpoint.json`（先写 `.tmp` 再 `os.rename`）
- 读取并验证检查点完整性
- 判断恢复起点（跳过已完成的测量点）
- 增量追加 `completed_points`
- 实验正常结束或重新开始时清理检查点

**检查点文件格式** (`checkpoint.json`)：
```json
{
  "version": 1,
  "experiment_id": "20260612_190601",
  "timestamp": "2026-06-12T21:08:32",
  "original_temp_list": [72.0, 74.0, 76.0, 78.0, 80.0],
  "original_vna_power_list": [-45, -30, -25],
  "original_power_list": [0, 1, 3, 5, 7, 9],
  "state": {
    "temp_idx": 2,
    "vna_dbm_idx": 0,
    "power_mw_idx": 0,
    "current_temp_k": 73.6,
    "total_count": 25,
    "extended_max_wait_s": 1800,
    "extended_pre_wait_s": 300,
    "rollback_consecutive_issues": 0,
    "rollback_first_issue_index": null,
    "rollback_count": 0,
    "overshoot_learning": {}
  },
  "completed_points": [
    {"temp_k": 72.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 71.604},
    {"temp_k": 72.0, "vna_dbm": -45, "power_mw": 1, "actual_k": 71.693}
  ]
}
```

**API**：
- `save(output_dir, state_dict, completed_points)` → None
- `load(output_dir)` → `(state_dict, completed_points)` | `None`
- `append_point(output_dir, point_dict)` → None（增量追加）
- `delete(output_dir)` → None
- `resume_from(output_dir, completed_points, temp_list, vna_power_list, power_list)` → `(temp_idx, vna_idx, power_idx)` | `None`

**恢复优先级规则**: `completed_points` 是权威数据源——从 `state` 中记录的 `temp_idx` 开始扫描，跳过 `completed_points` 中已存在的 `(temp_k, vna_dbm, power_mw)` 组合。`state` 中的 `vna_dbm_idx` 和 `power_mw_idx` 仅用于加速扫描起点，不作为最终判断依据。

### 异常检测 `_is_recoverable_error()`

`ExperimentWorker` 新增静态方法，匹配关键字：
- `VI_ERROR_CONN_LOST`（VISA 标准错误码）
- `timeout`, `disconnected`, `closed`, `lost`, `not responding`, `connection`, `tcpip`, `hislip`

**非**连接错误（`VI_ERROR_TMO` 单独超时、数据解析失败、无效参数）不触发恢复——沿用现有逻辑。

### 重连循环 `_enter_recovery()`

```
1. 日志记录: "VISA 连接丢失: {error}"
2. checkpoint.save() — 保存当前完整状态
3. signal: experiment_recovering.emit(error_msg)
4. for attempt in 1..60:  # 30min / 30s
     a. if abort_flag → 保存检查点, experiment_aborted, return
     b. sleep(30s)
     c. 尝试 lakeshore.connect(), laser.connect(), vna.connect()
     d. 全部成功 → signal: experiment_recovered.emit(), return
     e. 任何失败 → 日志: "重连尝试 #{attempt}/60 失败"
5. 超时 → signal: experiment_recovery_timeout.emit()
   日志: "重连超时（30min），检查点已保存，可稍后手动恢复"
```

### S2P 文件去重 `_find_next_filename()`

避免恢复期间覆盖已有 S2P 文件：

```python
def _build_filename(temp_k, vna_dbm, power_mw, actual_k, attempt=0):
    base = f"YBCO_{vna_dbm:+d}dBm_{power_mw:02d}mW_target_{temp_k:.0f}K"
    if attempt > 0:
        return f"{base}_attempt{attempt}_actual_{actual_k:.3f}K.s2p"
    return f"{base}_actual_{actual_k:.3f}K.s2p"
```

自动扫描已有文件递增 `attempt` 编号。实验正常结束时统一清理旧 attempt（仅保留最新）。

### 新信号

```python
experiment_recovering = pyqtSignal(str)           # 连接丢失，进入重连
experiment_recovered = pyqtSignal()               # 重连成功
experiment_recovery_timeout = pyqtSignal()        # 重连超时
experiment_resume_prompt = pyqtSignal(str, int)   # 恢复询问 (exp_id, completed_n)
```

### 恢复询问 — 用户交互

`MainWindow` 连接 `experiment_resume_prompt` →

```
QMessageBox:
  "检测到未完成实验 20260612_190601
   （已完成 25 个测量点）
   上次中断: 76.0K / -45dBm / 0mW

   是否恢复？"
  [恢复] [重新开始] [取消]
```

## 新增 Config 常量

```python
# 断点续传 & 自动重连
reconnect_retry_interval_s = 30          # 重连尝试间隔（秒）
reconnect_max_wait_minutes = 30          # 最大等待重连时间（分钟）
checkpoint_save_interval_points = 5      # 每 N 个测量点增量保存
checkpoint_keep_latest_attempt_only = True  # 实验结束时仅保留最新 attempt
```

## 文件改动清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `config.py` | 新增 4 个常量 | 重连间隔、最大等待、保存间隔、清理策略 |
| `ui/workers.py` | 新增 `CheckpointManager` 类 | ~80 行，独立类 |
| `ui/workers.py` | 修改 `ExperimentWorker` | 新增方法 + 修改 `run()` 骨架（~60 行改动） |
| `ui/main_window.py` | 新增信号连接 | `experiment_resume_prompt` / `experiment_recovering` / `experiment_recovered` |
| `tests/test_experiment_worker.py` | 新增测试 | 检查点读写/恢复、重连循环、文件去重 |

## 边界情况

| 场景 | 处理 |
|------|------|
| 检查点文件损坏（写入中途崩溃） | `.tmp` 原子写入 + JSON 解析异常 → 视为无检查点 |
| 恢复时参数列表已变更 | 对比 `original_*_list` vs 当前；不匹配 → 警告用户建议重新开始 |
| 重连期间用户点 Abort | 退出重连循环，保存检查点，`experiment_aborted` |
| 实验完成后残留检查点 | 仅当 `run()` 正常走完所有温度点（`temp_idx >= len(temp_list)`）时才自动删除检查点。若因熔断跳过的温度点在 `completed_points` 中缺失但循环已正常退出，仍视为完成并清理。 |
| 部分设备恢复、部分未恢复 | 分别重连，全部成功才算成功 |
| 同一测量点已有多个 attempt S2P 文件 | `_find_next_filename()` 自动递增，完成后统一清理 |
| 重连后温度与目标偏差过大 | 走正常稳定循环，不跳过——现有逻辑已处理此情况 |
| 用户选择"重新开始" | 删除 `checkpoint.json` + 确认是否删除已有 S2P 文件 |

## 不在范围内

- **不**修改 `ExperimentStabilityController` 和 `stability_monitor.py`（属于批次二）
- **不**调整熔断阈值和激光沉降时间（属于批次二）
- **不**重构 `run()` 为状态机
- **不**支持设备热插拔（地址变化）
- **不**支持跨机器/跨进程恢复

## 测试计划

| 测试 | 覆盖 |
|------|------|
| `test_checkpoint_save_load` | 写入→读取→字段完整性 |
| `test_checkpoint_atomic_write` | `.tmp` 中间文件 → crash 仿真 → 恢复时忽略 |
| `test_checkpoint_delete_on_complete` | 实验完成 → 检查点不存在 |
| `test_resume_skips_completed_points` | 已有 5 个 completed_points → 恢复从第 6 个开始 |
| `test_resume_param_list_mismatch` | 温度列表变更 → 警告用户 |
| `test_is_recoverable_error` | 各种异常字符串 → 正确分类 |
| `test_recovery_loop_success` | Mock VISA 重连成功 → signal `experiment_recovered` |
| `test_recovery_loop_timeout` | Mock 持续失败 → signal `experiment_recovery_timeout` |
| `test_find_next_filename_no_conflict` | 文件名不存在 → attempt=0 |
| `test_find_next_filename_with_conflict` | 文件名已存在 → attempt=1, 2, ... |
| `test_completed_points_cleanup_old_attempts` | 实验完成 → 仅保留最新 attempt |
