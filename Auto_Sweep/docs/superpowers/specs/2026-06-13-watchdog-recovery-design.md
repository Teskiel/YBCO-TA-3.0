# 实验进程看门狗 & 自动恢复 设计规范

**日期**: 2026-06-13  
**状态**: 设计中 → 待实现  
**关联**: [[2026-06-13-checkpoint-resume-design]]（已有 checkpoint 基础设施）  

## 1. 问题

实验进程（`pythonw.exe` / `ExperimentWorker`）在 VISA 调用中**静默挂死**（RS-232/USB 适配器层阻塞），不抛异常，已有 `_enter_recovery` 无法触发。本次实验 `20260613_031259` 即在 78K 稳定阶段卡死——进程仍存活（PID 17580，4.1 GB），但无任何日志输出。

**根因**: `pyvisa` 的 `timeout` 设置在 VISA 层，无法覆盖驱动层的阻塞。而 Python 在 Windows 下没有 `SIGALRM`，传统 signal-based timeout 不可行。

## 2. 目标

在实验线程 VISA 调用无限阻塞时，**自动检测 → 终止 → 重启并从 checkpoint 继续**，无需人工介入。

## 3. 架构

```
app.py (GUI mode)
├── QApplication + MainWindow
├── ExperimentWorker (在独立 QThread 中)
│   ├── HeartbeatThread —— 每 60s 写 heartbeat.json
│   └── 实验循环: 稳定 → 测量 → checkpoint
│
└── subprocess.Popen ──> app.py --watchdog --child-pid=<PID>
                             (纯后台，无GUI，仅 stdlib)
                             每 60s 读 heartbeat.json
                             超 300s → taskkill + respawn
```

## 4. 组件

### 4.1 HeartbeatThread (`heartbeat.py`)

- **职责**: 每 60s 将当前实验状态写入 `<output_dir>/heartbeat.json`
- **零依赖**: 仅 `json`, `time`, `os`, `threading`（不 import pyvisa/PyQt5）
- **线程安全**: 实验线程通过原子属性 `Heartbeat._current_step` 更新步骤（`str` 赋值在 CPython 中 GIL 保证原子），心跳线程读取后写入文件
- **文件格式**:

```json
{
  "pid": 17580,
  "step_ts": 1718245765.123,
  "step": "stabilising 78.0K",
  "temp_idx": 3,
  "vna_idx": 0,
  "power_idx": 0,
  "seq": 47
}
```

- `seq` 单调递增，看门狗据此检测心跳文件是否仍在更新（防止磁盘满导致 mtime 不更新）
- 覆盖写入（~200 bytes），不追加，无内存增长
- 写入失败（磁盘满等）静默忽略，不抛异常
- `stop()` 方法最后一次写入 heartbeat.json（含 `"stop": true`），设置停止事件，线程退出。**不删除文件**（看门狗需读取 stop 信号），文件 ~200 bytes 将在下次实验启动时覆盖

### 4.2 看门狗进程 (`watchdog.py`)

- **职责**: 监控子进程心跳，超时则 kill + respawn
- **零依赖**: 仅 `json`, `time`, `os`, `subprocess`, `sys`
- **启动方式**: `app.py --watchdog --child-pid=<PID> --resume-path=<dir>`  
  `app.py` 的看门狗模式直接调用 `watchdog.py` 的 `run()` 函数，退出后不进入 Qt 事件循环
- **监控循环**:
  1. 每 60s 读取 `<resume-path>/heartbeat.json`
  2. 检查 `seq` 是否递增 + `now - step_ts < 300`
  3. 如果 `step_ts` 过期或 `seq` 不递增 → 超时
  4. 如果文件不存在 → 子进程崩溃
  5. 超时/崩溃 → `taskkill /PID <child> /F` → 等 3s → `subprocess.Popen(["python", "app.py", "--resume", resume_path])`
  6. 循环继续（监控新子进程）
- **内存**: < 20 MB（无 GUI 库，仅 stdlib）
- **退出**: 信号文件 `heartbeat.json` 中包含 `"stop": true` → 看门狗退出
- **锁机制**: `<resume-path>/watchdog.pid` — 启动时检查，如已有合法 PID 运行中则拒绝启动

### 4.3 app.py CLI 参数

```
python app.py                          # 正常 GUI 启动
python app.py --resume <output_dir>    # 从 checkpoint 恢复实验
python app.py --watchdog --child-pid=<PID> --resume-path=<dir>  # 看门狗模式
```

`--resume` 模式:
- 仍需 GUI（用户可能要在 Dashboard 监控）
- 加载 `<output_dir>/checkpoint.json`
- `ExperimentWorker` 从 `temp_idx` 开始，跳已完成温度点
- 日志追加到已有 `experiment_log_*.txt`（追加模式，写入 `=== 恢复 @ <timestamp> ===` 分隔线）

### 4.4 ExperimentWorker 改动

- `_run_impl()` 开始时创建 `HeartbeatThread` 实例
- 在以下位置各加一行 `self._heartbeat.step("...", temp_idx, vna_idx, power_idx)`:
  - `→ Stabilising to X K`（稳定开始）
  - `稳定: X K`（稳定完成）
  - `Measuring ... @ X K`（测量开始）
  - `温度点完成`（温度点结束）
- `_run_impl()` 结束 → `self._heartbeat.stop()`（写入 `stop: true` 通知看门狗退出）
- `_abort_flag` 被用户设置 → `self._heartbeat.stop()`
- 看门狗进程在 `_run_impl()` 开始前启动（`subprocess.Popen`），实验正常结束时心跳文件写入 `stop: true` 通知看门狗退出

### 4.5 断点续传粒度

**温度点级别**，不做测量点级别断点：
- 如果 78K 测了 6/18 个点后被 kill，重启后 78K 从头开始测（18 点全量重测）
- 已完成温度点（72K, 74K, 76K）的 S2P 文件保留不动
- 理由: 避免部分测量状态不一致、S2P 文件名冲突

### 4.6 config.py 新增常量

```python
heartbeat_interval_s = 60          # 心跳写入间隔
heartbeat_timeout_s = 300          # 挂死判定阈值
```

## 5. 恢复逻辑矩阵

| 场景 | 检测 | 行为 |
|------|------|------|
| 实验线程挂死（heartbeat 过期） | `now - step_ts > 300` 或 `seq` 不递增 | taskkill + 重启 `--resume` |
| 子进程崩溃退出 | heartbeat.json 不存在 | 立即重启 `--resume` |
| 看门狗自身崩溃 | 子进程无保护 | 实验继续运行（不中断） |
| 用户正常点击 Stop | `heartbeat.stop()` → `stop: true` | 看门狗检测后优雅退出 |
| 磁盘满 (heartbeat 写失败) | `seq` 不递增（看门狗侧） | 误判为挂死 → 重启（可接受: 磁盘满时实验本身已不可靠） |
| 重复启动看门狗 | `watchdog.pid` 已存在且合法 | 后启动者检测冲突 → 报错退出 |
| resume 时 checkpoint 不存在 | `CheckpointManager.load()` 返回 None | 报错，提示手动指定起始温度 |
| resume 时实验已完成 | `temp_idx >= len(temp_list)` | 提示"实验已完成"，退出 |

## 6. 文件清单

```
新增:
  Auto_Sweep/heartbeat.py              # HeartbeatThread（~50 行）
  Auto_Sweep/watchdog.py               # 看门狗进程（~80 行）

修改:
  Auto_Sweep/app.py                    # CLI 参数 + 看门狗启动（~40 行改动）
  Auto_Sweep/ui/workers.py             # 集成心跳步骤更新（~20 行改动）
  Auto_Sweep/config.py                 # 2 个常量

不动:
  Auto_Sweep/lakeshore_control.py
  Auto_Sweep/laser_driver.py
  Auto_Sweep/vna_control.py
  Auto_Sweep/ui/experiment_stability_controller.py
  Auto_Sweep/ui/main_window.py
  Auto_Sweep/ui/dashboard_page.py
```

## 7. 测试策略

| 层 | 文件 | 测试内容 |
|----|------|---------|
| 单元 | `tests/test_heartbeat.py` | HeartbeatThread 启动/step/stop、文件内容正确性、seq 递增、写失败不崩溃 |
| 单元 | `tests/test_watchdog.py` | 看门狗超时判定（OK/超时/文件缺失/seq 不递增）、watchdog.pid 互斥 |
| 单元 | `tests/test_app_cli.py` | argparse: `--watchdog`, `--resume`, `--child-pid` 正确解析 |
| 集成 | `tests/test_workers.py` | mock LakeShore + HeartbeatThread 集成，验证关键步骤更新 |
| 集成 | `tests/test_watchdog_recovery.py` | `time.sleep(999)` 模拟卡死 → 看门狗检测 → 验证 temp_idx 恢复正确 |

测试遵循项目已有 BDD/TDD 约定（`tests/CLAUDE.md`）。

## 8. 非目标（本次不做）

- Linux/macOS 支持（Windows-only，同 `memory_monitor.py`）
- 远程通知（邮件/短信）——未来可加
- 多实验并行看门狗——当前仅单实验
- VISA 调用线程化（`concurrent.futures`）——C 方案中的第一层防线 B 即可覆盖
