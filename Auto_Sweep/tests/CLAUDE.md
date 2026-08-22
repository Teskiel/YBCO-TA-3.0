# tests/CLAUDE.md — 测试约定与基础设施

BDD/TDD 测试套件（pytest）。主文档见 [../CLAUDE.md](../CLAUDE.md)。

## BDD/TDD 约定

所有新代码遵循 BDD（行为驱动开发）和 TDD（测试驱动开发）。测试**先写**，然后实现遵循 Red → Green → Refactor 循环。

### 测试命名

测试函数名遵循：
```
test_given_<precondition>_when_<action>_then_<expected_result>
```

示例：
```python
def test_given_all_readings_in_band_when_checking_then_returns_stable(self):
def test_given_one_reading_outside_tolerance_when_checking_then_returns_not_stable(self):
def test_given_insufficient_data_when_checking_custom_then_returns_not_stable_with_reason(self):
```

## 测试分层

| Layer | 测试内容 | 方法 |
|---|---|---|
| **Config** | 常量完整性、值范围、区域边界 | 直接 import + 断言 |
| **Algorithm** (stability, PID, diagnostics) | 输入 → 输出逻辑 | 合成数据 + 预期结果 |
| **Instrument** (lakeshore, laser, vna) | SCPI 命令序列 | Mock pyvisa ResourceManager + Resource |
| **GUI pages** (VNAPage, LakeShore safety) | Widget 行为、signal/slot 逻辑 | QApplication + 真实 widget 实例 |
| **Experiment orchestration** | 完整 sweep 循环 + mocked controllers | MagicMock 仪器, patched `time.sleep` |
| **Hardware integration** | 此阶段不测试 | 需要真实硬件或集成环境 |

## 运行测试

```bash
pytest tests/ -v                                     # 全部测试
pytest tests/test_stability_monitor.py -v             # 单个模块
pytest tests/test_pid_parameters.py -v                # PID zone manager 测试
pytest tests/test_temperature_state_diagnostics.py -v # 状态分类器测试
pytest tests/test_lakeshore335_ramp.py -v             # ramp controller 测试
pytest tests/test_stability_fallback.py -v            # 稳定性控制器测试
pytest tests/test_experiment_data_completeness.py -v   # 完整性检查与迁移测试
pytest tests/test_stability_timeout_rollback.py -v     # 超时软化 + 回退 + 4K 豁免测试
pytest tests/test_auto_reconnect.py -v                # 自动重连测试
pytest tests/test_button_styling.py -v                # 按钮样式测试
pytest tests/ -k "test_given_actual_77k" -v           # 按关键字查找单个测试
```

## Mock VISA 模式

`tests/conftest.py` 提供 `MockResource` 和 `MockResourceManager` 类：
- 记录所有通过 `.write()` / `.query()` 发送的 SCPI 命令
- 为 `*IDN?`, `KRDG?`, `SETP?` 等返回可配置的预设响应
- 可在 `mock_resource.last_command` 和 `mock_resource.all_commands` 上断言

使用 `mock_pyvisa` fixture 全局 patch `pyvisa.ResourceManager`。

## GUI 测试模式

`test_vna_page.py`、`test_lakeshore_safety.py`、`test_experiment_worker.py`、`test_button_styling.py`、`test_auto_reconnect.py` 和 `test_stability_fallback.py` 中的测试使用：
- 模块级别的 `qapp` fixture，为整个模块创建一个 `QApplication`
- 直接实例化 widget（不需要 MainWindow，但 `test_auto_reconnect.py` 和 `test_button_styling.py` 直接测试 `MainWindow` 和详情页）
- `MagicMock` controllers 用于 worker 测试 — 无真实 VISA 调用
- `@patch("time.sleep", return_value=None)` 使实验循环测试即时完成
- `@patch("os.makedirs")` 跳过真实文件系统操作
- `@patch.object(MainWindow, "_start_reconnect")` 拦截自动重连

## 合成温度数据

`conftest.py` 为所有诊断场景提供预构建 fixtures：
- `stable_temperatures` — 30K ±0.02K 噪声
- `oscillating_temperatures` — 30K ±0.5K 正弦波
- `drifting_temperatures` — 30K → 32K 线性漂移
- `noisy_temperatures` — 30K ±0.08K 随机噪声
- `perfect_stable_temperatures` — 恰好 30K，零方差

## 测试文件索引

| 文件 | 测试数 | 测试内容 |
|------|--------|----------|
| `test_pid_parameters.py` | 47 | PIDZoneManager, SetpointCalculator, zone 边界, 验证 |
| `test_temperature_state_diagnostics.py` | 44 | 7 状态分类器, PID 自动调整, lockout, pure-P 模式 |
| `test_stability_fallback.py` | 36 | ExperimentStabilityController 状态机, zone 选择, 超时 |
| `test_config.py` | 35 | Config 完整性, 地址格式, 稳定性设置, PID zones, VNA 范围默认值 |
| `test_plot_vna_powersweep.py` | 29 | 路径解析, 文件发现, S2P 加载, trace 收集, 绘图 |
| `test_lakeshore335_ramp.py` | 21 | Ramp controller SCPI 序列, zone 应用, 紧急停止, heater 升级 |
| `test_experiment_data_completeness.py` | 29 | 实验完整性判定 (readme/日志/结构性), 扫描, dry-run 迁移 |
| `test_experiment_worker.py` | 21 | ExperimentWorker sweep 循环, 中止, 重试, 温度监控, readme 生成 |
| `test_stability_timeout_rollback.py` | 21 | 超时软化 (soft_pass/hard_fail), 连续回退状态机, 4K豁免, P-only过冲 |
| `test_auto_reconnect.py` | 16 | 错误分类, 重连触发, 重试限制, 用户断开标记, 独立性 |
| `test_vna_power_sweep.py` | 15 | VnaPowerRangeWidget, 功率范围生成, VNAPage 功率合并 |
| `test_readme_generator.py` | 14 | Readme 生成内容, 编码, 文件放置, 边界情况 |
| `test_pid_controller.py` | 13 | SmartPIDController zone 选择, setpoint 计算, 边界 |
| `test_button_styling.py` | 11 | DashboardPage + 详情页按钮样式, connect/disconnect/error/connecting 状态 |
| `test_vna_page.py` | 10 | VNAPage 频率 spinbox 范围, 默认值, 同步逻辑, 钳位 |
| `test_timing_settings.py` | 10 | TemperatureSweepWidget 时序设置 (pre-wait, max-wait), get/set/emit |
| `test_stability_monitor.py` | 8 | AdvancedStabilityMonitor 稳定性检查, 读数管理, 超时 |
| `test_lakeshore_safety.py` | 8 | LakeShoreWorker 冷却-安全联锁, heater range 覆盖 |
| `test_claude_md_structure.py` | 30 | CLAUDE.md 分层文档结构验证 |
