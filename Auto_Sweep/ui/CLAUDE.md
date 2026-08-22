# ui/CLAUDE.md — GUI 架构与组件文档

PyQt5 控制面板，通过 `app.py` 启动。主文档见 [../CLAUDE.md](../CLAUDE.md)。

## GUI 架构

`MainWindow` (`main_window.py`) 使用：
- **QStackedWidget**: 4 个页面 — Dashboard (index 0), Laser (1), LakeShore (2), VNA (3)
- **Worker threads**: 每个仪器有独立 `QThread` + `QObject` worker。所有 VISA I/O 在 UI 线程外执行，结果通过 `pyqtSignal` 传递。第 4 个 worker (`ExperimentWorker`) 在实验启动时按需创建线程
- **DeviceCard**: Dashboard 上的可点击卡片。点击导航到详情页。StatusLight 显示连接状态（red/yellow/green）
- **LakeShore polling**: 1 秒 QTimer 驱动 `LakeShoreWorker.poll()` → 发射 `reading` 信号 → UI 更新大字体温度标签
- **Settings auto-persist**: 窗口关闭时所有当前设置（地址、参数、sweep 配置）自动保存到 `app_settings.json`。启动时自动恢复。无需手动保存/加载。这替代了 JSON 预设文件的会话状态功能
- **Dashboard temperature sweep**: `TempSweepWidget` 支持两种模式 — "Fixed Points"（逗号分隔列表）和 "Range Sweep"（start/stop/step）

### 设计原则
1. **信息密度分层** — dashboard 仅显示状态；详情在子页面
2. **颜色编码优于文字** — green/yellow/red 状态灯；彩色操作按钮
3. **危险操作确认** — 激光物理关闭和所有加热器关闭需要 QMessageBox 确认
4. **Fusion 风格** — "Deep Space Cyan" 暗色主题，全局 stylesheet 在 `app.py`

## 页面速查

| 页面 | 文件 | 功能 |
|------|------|------|
| Dashboard | `dashboard_page.py` | 3 个 DeviceCard、地址选择、Connect All、温度扫描配置、实验启动/中止 |
| Laser | `laser_page.py` | 3×6 功率网格（0-17 mW）、波长输入、Output ON/OFF、Physical OFF |
| LakeShore | `lakeshore_page.py` | 大字体温度读数、Heater Range 单选、Loop 1/2 并排、All Heaters OFF |
| VNA | `vna_page.py` | 频率设置（Start/Stop/Center/Span 双向同步）、S 参数选择、功率网格 + 范围扫描 |

## Workers 线程模型

| Worker | 位置 | 职责 |
|--------|------|------|
| `LaserWorker` | `workers.py` | connect/disconnect、set_power、set_wavelength、output_on/off、physical_off |
| `LakeShoreWorker` | `workers.py` | connect/disconnect、poll（1s 定时器）、set_setpoint（带安全联锁）、set_heater_range、all_heaters_off |
| `VNAWorker` | `workers.py` | connect/disconnect（HiSLIP）、apply_settings、single_sweep + 保存 S2P |
| `ExperimentWorker` | `workers.py` | 完整实验循环：温度 + 激光功率 + VNA 功率扫描 → S2P 保存 |

所有 worker 使用 `@pyqtSlot()` 装饰器确保跨线程信号-槽安全。每个仪器线程通过 `moveToThread()` 转移 worker。

## Temperature Safety Interlock (LakeShoreWorker)

`LakeShoreWorker.set_setpoint()` 实现**单向冷却安全规则**：

如果 `actual_temp > target_setpoint + 20 K`:
1. 设置 heater range 为 **0 (OFF)**
2. 每 2 秒轮询 `get_temperature()`
3. 等待直到 `actual_temp - target_setpoint < 20 K`
4. 设置 heater range 为 **2 (Medium)**
5. 然后写入 setpoint

10 分钟安全超时防止无限轮询。如果 `actual_temp <= target_setpoint + 20 K`，直接写入 setpoint。测试见 `tests/test_lakeshore_safety.py`。

## VNA Page Frequency Controls

`VNAPage` (`vna_page.py`) 提供：
- **频率 spinbox**: Start, Stop, Center, Span — 均为 GHz，范围 1–14 GHz
- **双向同步**: 更改 Start/Stop 会更新 Center/Span，反之亦然。`_freq_syncing` 守卫标志防止无限递归
- **单位转换**: 内部 API 使用 Hz（`get_all_settings()` 返回 Hz），UI 显示 GHz
- **S 参数选择器**: S21, S11, S12, S22
- **VNA 源功率**, sweep points, IF bandwidth 控制
- **Single sweep**: 触发一次扫描并保存 .s2p 到选定路径
- **Full settings dict**: `apply_settings()` 一次性发送所有参数到 VNA
- **功率合并**: `get_all_settings()` 将按钮选择和范围扫描合并为排序去重的 `power_dbm` 并集

## Experiment Stability Controller

`ExperimentStabilityController` (`experiment_stability_controller.py`) 是 GUI 的 `ExperimentWorker` 使用的**简化**稳定性控制器。它替代了旧的 `SmartPIDController` + 动态 PID 调整方式。

**核心哲学**: PID 参数**按温度区域固定，永不调整**。仅调整 setpoint overshoot。

**Fixed PID zones**（来自 `config.FIXED_PID_ZONES`）:

| Zone | Range | P | I | D | Base Overshoot |
|---|---|---|---|---|---|
| Low | ≤ 20 K | 100 | 5 | 0 | 0 K |
| Medium | 20–40 K | 100 | 0 | 0 | 2.5 K |
| High | > 40 K | 150 | 0 | 0 | 2.0 K |

### 设计哲学：x/0/0 (P-only) vs x/y/0 (P+I)

`ExperimentStabilityController` 在 Medium/High 温区**故意使用 I=0**（纯 P 控制），而 `pid_parameters.py`（Ramp controller）在 Medium 温区使用 I=3（PI 控制）。这是两种不同的温度驱动策略：

| | x/0/0 + overshoot（本控制器） | x/y/0 PI（pid_parameters.py） |
|---|---|---|
| **消除稳态误差** | 靠提高 setpoint（外部补偿） | 靠积分项累积（内部消除） |
| **振荡风险** | **低** — 无积分 windup，不会过冲后反向 | **高** — 积分累积 → 超调 → 反向修正 → 振荡 |
| **调参复杂度** | 低 — 只调 `base_overshoot_k` | 高 — P、I 都要调，且不同热负载下 I 的最优值不同 |
| **兜底策略** | `good_enough` 回退 + 最多 2 次 overshoot 调整 | pure-P 模式：振荡失败 ≥5 次后强制 I=0 |
| **适用场景** | 长时间测量，温度必须平稳不振荡 | 快速 ramp，允许少量过冲 |

**为什么 P-only 更可靠**：

1. **无积分 windup** — 积分项会在温度远离目标时持续累积，导致大幅超调，随后又反向修正，形成振荡。P-only 的输出仅与当前偏差成正比，不会"记住"历史误差。
2. **稳定性优先** — 超导测量对温度稳定性要求极高（±0.1K 以内）。振荡即使最终收敛，也会在收敛过程中破坏测量条件。P-only 的单调趋近特性更适合此场景。
3. **setpoint overshoot 是可预测的外部补偿** — 计算方式：`overshoot = base + (target − avg_actual)`，钳位在 `[base, MAX_OVERSHOOT_K]`。这个值完全可控，不会像积分项那样"失控"。
4. **pid_parameters.py 的 pure-P 兜底印证了这一判断** — 即使传统 PI 方案也设计了"振荡失败 ≥5 次后强制 I=0"的回退机制，这说明在实际热系统中，积分项是振荡的主要来源。

**为什么 Low 温区保留 I=5**：
- ≤20K 的极低温区热容极小，P-only 可能导致温度在目标附近"卡住"（稳态误差过大）
- 小积分项提供微弱的"推力"帮助跨越最后 0.xK 的差距
- 低温区热响应快，积分 windup 风险相对可控

**2 阶段稳定性状态机**:

| Phase | 描述 |
|---|---|
| `SPARSE` (0) | 20 s 轮询。对最近 4 个原始读数做 ±1K 带检查。60 s min + 4 读数在带内 → `FINE` |
| `FINE` (1) | 5 s 轮询。双轨检查: `in_target_zone` AND `steady_state`（3 min max−min ≤ 0.1K）。不稳定 60 s → setpoint 调整 |
| 过冲调整 | `overshoot = base + (target − actual_avg)`。终止条件: \|avg − target\| ≤ 0.7K，**无计数上限** |

**关键属性**:
- `SPARSE_BAND_K = 1.0` — ±1K 原始读数带（sparse→fine 转换）
- `OVERSHOOT_TARGET_BAND_K = 0.7` — overshoot 调整终止: \|avg−target\| ≤ 0.7K
- `GOOD_ENOUGH_BAND_K = 0.5` — "足够接近"容差
- `MAX_OVERSHOOT_K = 10.0` — overshoot 上限
- `STABLE_HOLD_SECONDS = 60` — 双轨通过后必须稳定保持 60 s
- `MAX_WAIT_SECONDS = 30 min`（FINE）/ 90 min（SPARSE）

**使用模式**:
```python
stability_ctrl = ExperimentStabilityController()
stability_ctrl.setup(target_k=30.0, current_temperature=actual_k)
stability_ctrl.add_reading(actual_k)
result = stability_ctrl.check(elapsed_s=elapsed)
if result.stable or result.reason == "good_enough":
    # 继续测量
sp_adj = stability_ctrl.needs_setpoint_adjustment()
if sp_adj:
    lakeshore.set_temperature(sp_adj, loop=1)
```

`get_fixed_pid()` 返回区域的 PID dict（永不改变）。测试见 `tests/test_stability_fallback.py`。

## Auto-Reconnect Mechanism

`MainWindow` (`main_window.py`) 检测 VISA 连接错误并自动尝试重连。

**错误检测** (`_is_connection_error()`):
- `VI_ERROR` 前缀 → 连接错误
- 关键字: `timeout`, `disconnect`, `closed`, `lost`, `not responding`
- 非连接错误（数据解析失败、无效参数）**不**触发重连

**重连流程**:
1. `_on_error("laser", message)` → 如果连接错误且非用户主动断开 → `_start_reconnect("laser")`
2. `_start_reconnect()`: 增加尝试计数，设置设备为 "connecting"（黄色），启动 QTimer(2s 延迟)
3. `_attempt_reconnect()`: 使用最后已知地址重新创建 VISA 连接
4. 成功 → `_on_device_connected()` 重置尝试计数为 0
5. 失败 → 如果 attempts < `max_reconnect_attempts` (3)，重试；否则保持断开（红色）

**用户断开保护**: `_user_disconnect[device]` 标记在用户点击 "Disconnect" 时设为 True。此标记为 True 时**永不**触发自动重连。用户点击 "Connect" 时清除标记。

**设备独立性**: 每个设备（laser, lakeshore, vna）有独立的重连状态、尝试计数器和定时器。

**配置**: `max_reconnect_attempts = 3`, `reconnect_delay_seconds = 2` in `config.py`。

测试见 `tests/test_auto_reconnect.py`。

## Dashboard Button Styling API

`DashboardPage` (`dashboard_page.py`) 提供按设备的连接状态方法，更新按钮颜色和启用状态：

| Method | Connect 按钮 | Disconnect 按钮 |
|---|---|---|
| `set_device_disconnected(key)` | 🟢 绿色, 启用 | ⚫ 灰色, 禁用 |
| `set_device_connected(key, model)` | ⚫ 灰色, 禁用 | 🔴 红色, 启用 |
| `set_device_error(key)` | 🟢 绿色, 启用 | ⚫ 灰色, 禁用 |
| `set_device_connecting(key)` | ⚫ 灰色, 禁用 | ⚫ 灰色, 禁用 |

三个设备使用相同模式。详情页有对应的 `set_connected()`/`set_disconnected()`/`set_connecting()` 方法。测试见 `tests/test_button_styling.py`。

## Preset System

`presets/` 目录中的 JSON 文件，每个设备一个文件。由 `PresetManager` 类（`widgets.py`）管理。

```json
// presets/laser_default.json
{"name": "default", "device": "laser",
 "wavelength_nm": 1550.0, "power_sequence_mw": [0,1,3,5,7,9],
 "resource_address": "TCPIP0::169.254.77.29::INSTR"}

// presets/lakeshore_default.json
{"name": "default", "device": "lakeshore",
 "resource_address": "ASRL4::INSTR",
 "loop1": {"setpoint_k": 50.0, "pid": {"p":100,"i":5,"d":0}, "heater_range": 2},
 "loop2": {"setpoint_k": 295.0, "pid": {"p":10,"i":20,"d":0}, "heater_range": 0}}
```

预设可从 Dashboard 和单个设备页面加载/保存。
