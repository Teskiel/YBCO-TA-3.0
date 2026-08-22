# Claude Code 主动监控与自动补测 — 设计文档

**日期:** 2026-06-17
**版本:** 1.0
**状态:** 待实现

---

## 1. 动机

### 1.1 问题

当前实验流程中，Claude Code 仅在用户主动提问时参与。实验运行数小时，期间可能出现温度超时、熔断耗尽后跳过温度点等问题，导致数据缺失。以 2026-06-16 的 50K 测量为例：

- 50K 处于被动冷却方向（从 ~53K 降至 50K），温度控制不稳定
- 连续 4 次熔断耗尽 3 次重启上限，系统放弃 50K
- -25 dBm 和 -15 dBm 数据完全缺失（20 个测量点）

用户希望在实验过程中有持续监控，并及时干预参数；实验完成后能自动分析缺失数据并执行补测。

### 1.2 目标

1. **实时监控**：Claude Code 每 5 分钟检查实验状态，在终端展示进度和告警
2. **主动干预**：Claude Code 在检测到异常时自动调整运行参数（max_wait、熔断阈值等），无需用户授权
3. **补测自动化**：实验完成后自动分析日志，生成补测计划，启动补测执行（含冷却策略确保从升温方向稳定）
4. **人类可读报告**：所有监控结果和补测报告在 Claude Code 终端中以格式化表格展示

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    ExperimentWorker (app.py)              │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ 温度稳定循环  │→│ 功率扫描循环  │→│ 状态写入器     │  │
│  │ + 熔断处理   │  │ + VNA 测量    │  │ _write_status()│  │
│  └──────────────┘  └──────────────┘  └───────┬───────┘  │
│                                               │          │
│  ┌──────────────┐  ┌──────────────┐           │          │
│  │ 命令轮询器    │  │ 补测模式      │           │          │
│  │ _poll_cmd()  │  │ --fill-mode  │           │          │
│  └──────┬───────┘  └──────────────┘           │          │
│         │                                      │          │
└─────────┼──────────────────────────────────────┼──────────┘
          │ 读取 commands.json                   │ 写入
          ▼                                      ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐
│  commands.json   │  │  status.json     │  │  experiment_ │
│  (Claude → Worker)│  │  (Worker → Claude)│  │  log.txt     │
└────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘
         │ 写入                │ 读取               │ 解析
         │                     │                    │
┌────────┴─────────────────────┴────────────────────┴───────┐
│                    Claude Code 监控进程                     │
│                                                            │
│  ScheduleWakeup(每5分钟) → parse_status() → 分析 → 决策     │
│  ├─ 正常: 打印摘要到终端                                    │
│  ├─ 异常: 写入干预命令 + 终端告警                             │
│  ├─ 完成: 生成补测报告 → 写入补测计划 → 启动 fill-mode       │
│  └─ 进度报告: 每30分钟输出完整实验状态表格                    │
└────────────────────────────────────────────────────────────┘
```

### 2.1 关键数据文件

均放在 `experiment_data/{timestamp}/` 目录下：

| 文件 | 方向 | 内容 |
|------|------|------|
| `status.json` | Worker → Claude | 实时实验状态（结构化 JSON） |
| `commands.json` | Claude → Worker | 干预命令队列 |
| `fill_plan.json` | Claude 生成 | 补测计划（温度/VNA 功率/激光功率列表） |
| `experiment_log.txt` | Worker | 保持现有文本日志不变 |
| `fill_complete.json` | Worker 写入 | 补测完成摘要 |

### 2.2 通信协议

- **Worker → Claude**：`status.json` 在每个关键事件时原子写入（先 `.tmp` 后 `os.replace()`）
- **Claude → Worker**：`commands.json` 由 Claude 写入，Worker 在每个决策点轮询
- **命令生命周期**：写入时 `status: "pending"` → Worker 执行后标记 `status: "applied"` → Claude 下次检查时确认
- **向后兼容**：JSON 文件读写失败不影响实验运行；原有 `experiment_log.txt` 不变

---

## 3. 数据结构

### 3.1 status.json

```json
{
  "experiment_id": "20260616_100438",
  "status": "running",
  "start_time": "2026-06-16T10:04:38",
  "last_update": "2026-06-16T12:36:46",
  "temperature_plan": [40.0, 50.0, 60.0],
  "vna_power_plan": [-45, -35, -25, -15],
  "laser_power_plan": [0, 1, 3, 5, 7, 9, 11, 13, 15, 17],
  "current": {
    "temp_idx": 1,
    "target_k": 50.0,
    "actual_k": 50.329,
    "vna_dbm": -45,
    "laser_mw": 3,
    "phase": "measuring"
  },
  "completed": [
    {
      "target_k": 40.0,
      "vna_dbm": -45,
      "powers_mw": [0, 1, 3, 5, 7, 9, 11, 13, 15, 17],
      "status": "done"
    }
  ],
  "issues": [
    {
      "time": "2026-06-16T12:36:46",
      "target_k": 50.0,
      "type": "meltdown",
      "detail": "max-min=0.346K > 0.25K",
      "restart_count": 1
    }
  ],
  "skipped": [
    {
      "target_k": 50.0,
      "reason": "meltdown_limit",
      "vna_power_remaining": [-25, -15]
    }
  ],
  "runtime_params": {
    "max_wait_seconds": 1800,
    "meltdown_threshold_k": 0.25,
    "max_meltdown_restarts": 3,
    "current_overshoot_k": 2.0
  }
}
```

**`status` 枚举：**
- `"running"` — 实验正常进行中
- `"completed"` — 正常完成
- `"error"` — 不可恢复错误
- `"aborted"` — 用户手动中止
- `"fill_running"` — 补测进行中
- `"fill_completed"` — 补测完成

**`phase` 枚举：**
- `"stabilizing_sparse"` — 温度稳定 Phase 1
- `"stabilizing_fine"` — 温度稳定 Phase 2
- `"pre_measuring"` — 预测量等待
- `"measuring"` — 正在测量
- `"meltdown_recovery"` — 熔断后恢复中

### 3.2 commands.json

```json
{
  "last_command_id": "cmd_003",
  "commands": [
    {
      "id": "cmd_001",
      "time": "2026-06-16T11:50:00",
      "action": "extend_max_wait",
      "params": {"add_minutes": 30},
      "reason": "连续2次超时，增加等待时间",
      "status": "applied"
    }
  ]
}
```

**命令类型与参数：**

| action | params | Worker 处理方式 |
|--------|--------|----------------|
| `extend_max_wait` | `{"add_minutes": 30}` | `self._max_wait_seconds += add_minutes * 60` |
| `relax_meltdown` | `{"new_threshold_k": 0.35}` | `self._meltdown_threshold_k = new_threshold_k`（仅当前温度点有效） |
| `increase_overshoot` | `{"add_k": 1.0}` | `self._stability_ctrl._current_overshoot += add_k` |
| `skip_temperature` | `{}` | 跳过当前温度点 |
| `stop_experiment` | `{}` | 优雅停止：完成当前测量 → 保存 checkpoint → 退出 |

### 3.3 fill_plan.json

```json
{
  "experiment_id": "20260616_100438",
  "generated_at": "2026-06-16T14:46:22",
  "strategy": "cooldown_then_heat",
  "cooldown_offset_k": 5.0,
  "temperature_plan": [50.0],
  "measurements": [
    {
      "target_k": 50.0,
      "vna_dbm": -25,
      "laser_powers_mw": [0, 1, 3, 5, 7, 9, 11, 13, 15, 17]
    },
    {
      "target_k": 50.0,
      "vna_dbm": -15,
      "laser_powers_mw": [0, 1, 3, 5, 7, 9, 11, 13, 15, 17]
    }
  ]
}
```

---

## 4. Worker 端修改

### 4.1 状态写入 (`_write_status()`)

- 新增方法，在任何关键阶段变更时调用
- 原子写入：`status.json.tmp` → `os.replace()`
- 写入失败时记录到 text log 但不影响实验
- 调用时机：稳定开始、phase 切换、测量开始、测量完成、熔断触发、超时判定、温度点跳过、实验结束

### 4.2 命令轮询 (`_poll_commands()`)

在以下决策点调用：

```
├── 温度稳定后、开始测量前
├── 每个温度点切换前
├── 熔断触发后、重启前
├── 超时判定后
└── 测量完成后
```

实现逻辑：
1. 读取 `commands.json`
2. 遍历 `status != "applied"` 的命令
3. 按 `id` 排序执行
4. 标记 `status: "applied"`
5. 原子写回

### 4.3 冷却阶段

补测模式专用。流程：

```
阶段 1: 冷却
  setpoint → T_target - cooldown_offset_k (默认 5K)
  轮询间隔: cooldown_poll_seconds (默认 10s)
  条件: actual < T_target → 进入阶段 2
  超时: cooldown_max_wait_minutes (默认 60min) → 超时后仍继续

阶段 2: 升温稳定
  setpoint → T_target + overshoot
  标准稳定性协议 (sparse → fine → stable)

阶段 3: 补测扫描
  仅测量 fill_plan.json 中列出的 (temp, vna) 组合
  跳过已存在的 .s2p 文件（去重）
  结果写入原实验目录的对应子文件夹

阶段 4: 完成
  写入 fill_complete.json
```

注意：冷却时 setpoint 设为 `target - offset`，不设为 0——考虑到 ramp rate 的存在，温和冷却即可；当 `actual < target` 时即可转入升温稳定（确保从升温方向进入测量）。

### 4.4 补测模式入口

```python
# app.py 新增参数
parser.add_argument("--fill", type=str, default=None, metavar="DIR",
    help="补测模式：指定实验输出目录，读取 fill_plan.json 执行补测")
parser.add_argument("--no-gui", action="store_true",
    help="无 GUI 模式（配合 --fill 使用）")
```

`ExperimentWorker` 新增 `_run_fill()` 方法，包含冷却 → 升温稳定 → 补测扫描 → 完成的完整流程。补测结果也写入 `checkpoint.json`，支持断点续传。

### 4.5 安全保护

- 补测模式执行内存监控
- 冷却阶段不低于 `MIN_SAFE_TEMP_K = 4.0K`
- 补测有熔断保护（次数可配置）
- `fill_complete.json` 记录补测开始/结束时间、完成测量点数、最终完整性百分比

### 4.6 向后兼容

- 不传 `--fill` 参数时，Worker 行为完全不变
- status.json 写入失败不影响实验
- 原有 text log 格式不变

---

## 5. 新增文件

### 5.1 `experiment_status.py` — 状态管理

- `ExperimentStatusWriter(output_dir)` — 写入 `status.json`，提供 `update_current()`、`add_completed()`、`add_issue()`、`add_skipped()` 等方法
- `ExperimentStatusReader(output_dir)` — 读取和解析 `status.json`
- 原子写入 + JSON schema 校验

### 5.2 `fill_planner.py` — 补测分析器（零硬件依赖）

- `parse_experiment_log(log_path)` — 解析文本日志，提取温度点、VNA 功率、激光功率、跳过/熔断事件
- `analyze_directory_structure(output_dir)` — 扫描文件系统，列出实际存在的 .s2p 文件
- `compute_missing(expected, actual)` — 计算缺失的 (temp, vna, laser_power) 三元组
- `generate_fill_plan(missing, config)` — 生成 `fill_plan.json`
- 可被 Claude Code 直接通过 `python fill_planner.py --dir ... --report` 调用

### 5.3 `claude_monitor.py` — Claude Code 侧监控脚本

- `python claude_monitor.py --dir DIR --check` — 读取 status.json，输出当前状态摘要
- `python claude_monitor.py --dir DIR --report` — 输出 markdown 格式完整报告
- `python claude_monitor.py --dir DIR --intervene` — 分析状态，如有异常写入 commands.json
- `python claude_monitor.py --dir DIR --fill-plan` — 实验完成后生成补测计划
- Claude Code 通过 `Bash` 工具调用此脚本，输出直接在终端显示

---

## 6. Claude Code 端行为

### 6.1 监控循环

```
用户: "开始监控实验 experiment_data/20260616_100438"

Claude Code:
  1. 读取 status.json 获取初始状态
  2. ScheduleWakeup(delay=300s, reason="检查实验状态")
  3. 每次醒来:
     a. 读取 status.json
     b. 如果 phase 未变 → 跳过详细分析（避免噪音）
     c. 如果 phase 变化 → 分析决策 → 终端输出简短报告
     d. 如果 status = "completed" → 进入补测分析
     e. 如果 status = "fill_completed" → 输出最终完整性报告 → 停止监控
     f. 每 30 分钟输出一次完整进度表
  4. 循环直到实验完成
```

### 6.2 决策规则表

| 触发条件 | 动作 | 命令 |
|----------|------|------|
| 连续 2 个温度点 `timeout` + `hard_fail` | 延长等待时间 | `extend_max_wait({add_minutes: 30})` |
| 同一温度点熔断 ≥ 2 次 | 放宽熔断阈值（仅当前温度点） | `relax_meltdown({new_threshold_k: 0.35})` |
| SPARSE 阶段超过 60 分钟未进入 FINE | 增加 overshoot | `increase_overshoot({add_k: 1.0})` |
| 温度异常（actual 远离 target > 20K 且持续上升） | 紧急告警，建议手动停止 | `stop_experiment` |
| 可用内存 < critical 阈值 | 紧急告警 | `stop_experiment` |

每条命令写入前先检查 `commands.json` 是否有未执行的同类命令，避免重复。

### 6.3 终端报告格式

**phase 变化时的简短报告：**
```
📡 [14:32] 实验 20260616_100438
   50.0K | -35dBm | 5mW → 完成 (S21 已保存)
   📊 进度: 温度 2/3 | 总测量点 54/120 (45%)
   ⚠️ 50K 已熔断 2 次 (阈值 0.25K)
```

**每 30 分钟的完整进度表：**
```
╔══════════════════════════════════════════════════════════╗
║  📊 实验 20260616_100438 — 运行 2h 15m                    ║
╠══════════════════════════════════════════════════════════╣
║  温度点    VNA功率      状态        测量点    备注         ║
║  ────────  ───────────  ────────    ────────  ──────────  ║
║  40.0 K    -45 dBm      ✅ 完成     10/10                 ║
║            -35 dBm      ✅ 完成     10/10                 ║
║            -25 dBm      ✅ 完成     10/10                 ║
║            -15 dBm      ✅ 完成     10/10                 ║
║  ────────  ───────────  ────────    ────────  ──────────  ║
║  50.0 K    -45 dBm      ⚠️ 部分     10/10    熔断×4, 放弃  ║
║            -35 dBm      ⚠️ 部分      1/10    熔断终止      ║
║            -25 dBm      ❌ 缺失      0/10    未开始        ║
║            -15 dBm      ❌ 缺失      0/10    未开始        ║
║  ────────  ───────────  ────────    ────────  ──────────  ║
║  60.0 K    -45 dBm      🔄 进行中    3/10                 ║
╚══════════════════════════════════════════════════════════╝
```

**实验完成后的补测报告：**
```
╔══════════════════════════════════════════════════════════╗
║  🔍 实验完成 — 数据完整性分析                              ║
╠══════════════════════════════════════════════════════════╣
║  总测量点: 98/120  (81.7%)                               ║
║  缺失数据:                                               ║
║    50.0K  -25 dBm  0~17 mW  (10点)  熔断耗尽后跳过       ║
║    50.0K  -15 dBm  0~17 mW  (10点)  熔断耗尽后跳过       ║
║                                                          ║
║  📋 补测计划:                                            ║
║    温度: [50.0K]                                        ║
║    VNA功率: [-25, -15] dBm                              ║
║    激光: [0,1,3,5,7,9,11,13,15,17] mW                   ║
║    预计耗时: ~40 分钟 (10功率×2Vna×2分钟/点)              ║
║    策略: 冷却至 target-5K → 升温至 target 稳定 → 测量     ║
║                                                          ║
║  ▶ 补测计划已写入 fill_plan.json                          ║
║  ▶ 自动启动补测? [Y/n]                                    ║
╚══════════════════════════════════════════════════════════╝
```

---

## 7. 新增配置常量

在 `config.py` 中新增：

```python
# --- Claude 主动监控相关 ---
# 冷却策略（补测模式）
fill_cooldown_offset_k = 5.0          # 冷却时 setpoint = target - offset
fill_cooldown_poll_seconds = 10       # 冷却阶段轮询间隔
fill_cooldown_max_wait_minutes = 60   # 冷却超时
fill_min_safe_temp_k = 4.0            # 最低安全温度

# 状态文件
status_write_enabled = True           # 是否写入 status.json
```

---

## 8. 测试计划

### 8.1 `tests/test_fill_planner.py`

| 测试 | 描述 |
|------|------|
| `test_parse_empty_log` | 空日志 → 返回空列表 |
| `test_parse_completed_temps` | 解析完成的温度点 |
| `test_parse_skipped_temps` | 解析因超时/熔断跳过的温度点 |
| `test_parse_meltdown_events` | 解析熔断事件和重启次数 |
| `test_scan_directory_complete` | 文件系统扫描：完整目录 |
| `test_scan_directory_partial` | 文件系统扫描：部分缺失 |
| `test_compute_missing_all_done` | 无缺失 → 空列表 |
| `test_compute_missing_partial` | 计算缺失的 (temp, vna, laser) 三元组 |
| `test_generate_fill_plan` | 生成 fill_plan.json 格式正确 |
| `test_generate_fill_plan_empty` | 无缺失时 fill_plan 返回 None |
| `test_parse_real_log_20260616` | 用真实日志文件验证解析 |

### 8.2 `tests/test_experiment_status.py`

| 测试 | 描述 |
|------|------|
| `test_write_and_read_status` | 写入 → 读取往返一致性 |
| `test_atomic_write` | .tmp 文件正确重命名为 .json |
| `test_update_current_no_data_loss` | update_current 不覆盖其他字段 |
| `test_add_completed_appends` | add_completed 正确追加 |
| `test_add_issue_appends` | add_issue 正确追加 |
| `test_add_skipped_appends` | add_skipped 正确追加 |
| `test_write_failure_safe` | 写入失败不抛异常 |

### 8.3 现有测试

- 所有现有测试必须继续通过
- Worker 在不传 `--fill` 时行为不变

---

## 9. 实现范围总结

### 需要修改的文件

| 文件 | 操作 | 内容 |
|------|------|------|
| `ui/workers.py` | 修改 | 新增 `_write_status()`、`_poll_commands()`、冷却阶段、`_run_fill()` 补测方法 |
| `ui/main_window.py` | 修改 | 支持 `--fill` 和 `--no-gui` 参数传递 |
| `app.py` | 修改 | 新增 `--fill`、`--no-gui` 命令行参数 |
| `config.py` | 修改 | 新增补测相关常量 |

### 需要新建的文件

| 文件 | 内容 |
|------|------|
| `experiment_status.py` | `ExperimentStatusWriter` / `ExperimentStatusReader` 类 |
| `fill_planner.py` | 日志解析 + 缺失分析 + 补测计划生成（零硬件依赖） |
| `claude_monitor.py` | Claude Code 侧监控脚本 |
| `tests/test_fill_planner.py` | fill_planner 的单元测试 |
| `tests/test_experiment_status.py` | 状态文件读写的单元测试 |

### 不做的

- 不修改现有稳定性算法
- 不修改现有熔断逻辑（仅通过命令文件调整阈值参数）
- 不修改 PID 策略
- 不影响 `power_sweep_auto.py`（CLI runner）的行为

---

## 10. 设计约束

1. **向后兼容**：原有 text log 不变；不传 `--fill` 参数时 Worker 行为完全不变；status.json 写入失败不影响实验运行
2. **原子写入**：所有 JSON 文件先写 `.tmp` 再 `os.replace()`
3. **命令去重**：每条命令有唯一 ID，Worker 只执行 `status != "applied"` 的命令
4. **fill_planner.py 零依赖**：不 import 任何硬件模块，可被 Claude Code 直接调用
5. **claude_monitor.py 设计为 Claude 通过 Bash 调用的脚本**：输出 markdown 格式到 stdout，Claude 直接展示在终端
