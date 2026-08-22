# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

本仓库为 Auto_Sweep 独立快照（源自 YBCO_TA monorepo @3d28a76 v1.2，2026-08-22）。
GUI 详情见 [ui/CLAUDE.md](ui/CLAUDE.md)。
测试约定见 [tests/CLAUDE.md](tests/CLAUDE.md)。
绘图脚本见 [draw/CLAUDE.md](draw/CLAUDE.md)。

## Project Overview

Automated YBCO superconductor measurement system. Orchestrates temperature sweeps (LakeShore 335 cryostat) and laser power sweeps (Keysight N7779C), capturing S-parameter data from a Keysight PXI VNA. Includes a PyQt5 GUI for manual instrument control.

Core workflow: **stabilise temperature → sweep laser power levels → capture S2P from VNA → repeat at next temperature**.

Refactored from a single 860-line monolithic script into a layered modular architecture following BDD/TDD methodology.

## File Map

```
├── app.py                       # GUI entry point (PyQt5) — also defines global QSS theme
├── app_settings.json            # Auto-persisted settings (addresses, params, sweep config)
├── config.py                    # ALL experiment constants — single source of truth
├── stability_monitor.py         # Temperature stability algorithms (pure, no hardware)
├── pid_controller.py            # OLDER PID — SmartPIDController (used by CLI runner)
├── pid_parameters.py            # NEWER PID — PIDZoneManager, SetpointCalculator, zone validation
├── temperature_state_diagnostics.py # State classifier — 7 states, auto PID adjust, lockout
├── memory_monitor.py            # Windows memory monitor (ctypes + GlobalMemoryStatusEx), zero deps
├── lakeshore_control.py         # LakeShore 335 driver + duck-typing helpers
├── laser_driver.py              # Keysight N7779C laser driver (production — used by GUI + CLI)
├── laser_control.py             # Older standalone laser driver (different API, default addr)
├── vna_control.py               # VNA S2P save + HiSLIP discovery + connection utilities
├── power_sweep_auto.py          # CLI experiment orchestration (temp + laser power + VNA sweep)
├── lakeshore335_ramp.py         # Standalone temperature ramp controller (10K→80K, no VNA/laser)
├── test_vna.py                  # Standalone VNA connection + S2P test
├── CLAUDE.md                    # ← this file (core architecture + config reference)
│
├── ui/                          # PyQt5 GUI — 详见 ui/CLAUDE.md
│   ├── main_window.py           # QStackedWidget router + 3 device threads + experiment thread
│   ├── dashboard_page.py        # Overview: 3 device cards, Connect All, temp sweep config
│   ├── laser_page.py            # Laser: 3×6 power grid, wavelength, quick actions
│   ├── lakeshore_page.py        # LakeShore: live readings, loop controls, emergency
│   ├── vna_page.py              # VNA: frequency, S-param, power, sweep
│   ├── workers.py               # LaserWorker, LakeShoreWorker, VNAWorker, ExperimentWorker
│   ├── experiment_stability_controller.py  # Simplified stability: fixed PID + setpoint overshoot
│   └── widgets.py               # StatusLight, DeviceCard, PresetBar, PresetManager, TempSweepWidget
│
├── presets/                     # Per-device JSON preset files
├── draw/                        # Standalone matplotlib plotting — 详见 draw/CLAUDE.md
├── tests/                       # BDD test suite (pytest) — 详见 tests/CLAUDE.md
├── testcode/                    # Standalone interactive debugging tools
│   ├── laser_test.py            # Laser interactive menu (imports laser_driver)
│   ├── vna_test.py              # VNA interactive menu (self-contained)
│   └── vna_probe.py             # Dumps ALL readable VNA parameters (SCPI probe)
│
├── experiment_data/             # Output from GUI experiment runs
│   └── {timestamp}/{target}K/actual_{temp}K/{dbm}dBm/{mw}mW/*.s2p
│
└── Lakeshore335_output.py       # Reference implementation (original GUI prototype)
```

## Architecture: Layered Dependency Graph

Strict one-way dependencies — no circular imports:

```
config.py                     ← zero dependencies, pure data
    │
    ├── stability_monitor.py        ← pure algorithm (time, dataclasses)
    ├── pid_controller.py           ← pure algorithm (imports config)
    ├── pid_parameters.py           ← pure algorithm (dataclasses, no config dependency)
    └── temperature_state_diagnostics.py ← pure algorithm (dataclasses, no hardware)
            │
            │  memory_monitor.py            ← OS instrumentation (ctypes, no pyvisa)
            │
            ├── lakeshore_control.py ← instrument driver (pyvisa + config)
            ├── laser_driver.py      ← instrument driver (pyvisa)
            ├── laser_control.py     ← older standalone driver (pyvisa)
            └── vna_control.py       ← instrument driver (pyvisa + socket)
                    │
                    ├── power_sweep_auto.py      ← CLI orchestration (temp + laser + VNA)
                    ├── lakeshore335_ramp.py     ← standalone ramp (temp only, no VNA/laser)
                    └── ui/workers.py            ← GUI orchestration (ExperimentWorker)
                            │  └── ui/experiment_stability_controller.py
                            │
                            └── ui/main_window.py → ui/*_page.py  ← GUI layer
```

**Key principle**: replacing an instrument means changing exactly one driver file. The orchestration layer only calls the driver's public API — it never sends raw SCPI commands.

## Two Laser Drivers

There are **two** laser driver modules — don't confuse them:

| File | Used by | API style | Default address |
|---|---|---|---|
| `laser_driver.py` | GUI, `power_sweep_auto.py` (production) | `set_power()`, `output_on()`, `output_off()`, `close()` | `TCPIP0::K-N7779C-00108::inst0::INSTR` |
| `laser_control.py` | Standalone (older) | `set_power()`, `physical_off()`, `physical_on()`, `disconnect()` | `TCPIP0::100.65.11.65::INSTR` |

`laser_driver.py` is the production module. When adding features, prefer `laser_driver.py`.

## Temperature Diagnostics

| File | Used by | Classification |
|---|---|---|
| `temperature_state_diagnostics.py` | `lakeshore335_ramp.py` | 7 states (stable, converging, oscillating, offset, drifting, overshooting, insufficient_data); 15-min lockout, rollback, pure-P mode after 5+ oscillation failures |

（旧模块 `temperature_diagnostics.py` 已移除；CLI runner 不再使用诊断模块。）

## Three Experiment Runners

| Runner | Location | Trigger | Output dir | What it does |
|---|---|---|---|---|
| CLI | `power_sweep_auto.py` | `python power_sweep_auto.py` | `base_folder` from config.py (见 Environment Setup) | Full experiment: temp → laser power sweep → VNA S2P |
| GUI | `ExperimentWorker` in `ui/workers.py` | "Start" button on Dashboard | `experiment_data/{timestamp}/` | Full experiment + VNA power sweep outer loop |
| Ramp | `lakeshore335_ramp.py` | `python lakeshore335_ramp.py` | `experiment_data/{timestamp}/ramp_log.csv` + `optimal_ramp_params.json` | Temperature-only ramp (10K→80K default); no VNA or laser |

The CLI runner uses `SmartPIDController` and `AdvancedStabilityMonitor`. The GUI runner uses `ExperimentStabilityController` (详见 [ui/CLAUDE.md](ui/CLAUDE.md)). The Ramp runner uses `PIDZoneManager` + `TemperatureStateDiagnostics`.

The GUI runner supports a **VNA power sweep** outer loop (`vna_power_list`) that the CLI runner does not.

## PID Strategy

There are **two** PID modules plus a third approach used by the GUI runner:

### SmartPIDController (`pid_controller.py`) — used by CLI runner

| Zone | Range | P | I | D | Notes |
|---|---|---|---|---|---|
| Low | ≤ 20 K | 100 | 5 | 0 | Integral term for cryogenic accuracy |
| Medium | 20–40 K | 100 | 0 | 0 | Proportional-only |
| High | > 40 K | 150 | 0 | 0 | Higher gain for faster warm-up |

Setpoint overshoot only applied above 20 K. Below 20 K, setpoint = target exactly (cryogenic safety).

### PIDZoneManager (`pid_parameters.py`) — used by ramp controller

| Zone | Range | P | I | D | Heater Range |
|---|---|---|---|---|---|
| Low | ≤ 20 K | 100 | 5 | 0 | Low (1) |
| Medium | 20–40 K | 100 | 3 | 0 | Medium (2) |
| High | > 40 K | 150 | 0 | 0 | Medium (2) |

Key differences from `pid_controller.py`:
- **Heater range policy**: High (3) forbidden. Auto-upgrade Low→Medium when heater > 85%.
- **Zone validation**: `validate_zones()` checks gaps, overlaps, valid ranges, D=0 enforcement.
- **Medium zone uses I=3** (not I=0) — small integral term for transitional zone accuracy.
- **SetpointCalculator**: configurable overshoot factors (0.3 medium, 0.5 high), clamped to [1.0, 5.0] K.
- **Pure-P mode**: after 5+ consecutive oscillation failures, forces I=0.

### ExperimentStabilityController (`ui/experiment_stability_controller.py`) — used by GUI runner

**x/0/0 (P-only) 策略**：Medium/High 温区故意使用 I=0，通过 setpoint overshoot 补偿 P-only 的稳态误差。相比传统 PI（x/y/0），P-only **无积分 windup 风险，不易振荡**——对长时间超导测量来说，稳定性优先于快速收敛。

Overshoot 调整无计数上限，终止条件为 `|avg − target| ≤ OVERSHOOT_TARGET_BAND_K` (0.7K)。SPARSE 阶段轮询间隔 20s（确保监视器分钟窗口有 ≥3 个读数）。

Fixed PID per zone, never adjusted — only setpoint overshoot is tuned. 详见 [ui/CLAUDE.md](ui/CLAUDE.md) 中的完整设计哲学对比。

**When adding PID features**: 优先扩展 `pid_parameters.py`（非 experiment_stability_controller）。新功能如需改变 PID 策略，保持 P-only（I=0, D=0）原则，通过 overshoot 而非积分项补偿稳态误差。

## Entry Points

| File | Run with | Purpose |
|---|---|---|
| `app.py` | `python app.py` | Launch PyQt5 GUI |
| `power_sweep_auto.py` | `python power_sweep_auto.py` | Automated experiment (CLI) |
| `lakeshore335_ramp.py` | `python lakeshore335_ramp.py --start 20 --end 30 --step 2` | Standalone temperature ramp |
| `testcode/laser_test.py` | `python testcode/laser_test.py` | Interactive laser debugging |
| `testcode/vna_test.py` | `python testcode/vna_test.py` | Interactive VNA discovery + S2P |
| `testcode/vna_probe.py` | `python testcode/vna_probe.py` | Dump all readable VNA SCPI params |
| `test_vna.py` | `python test_vna.py` | Standalone VNA connection + S2P save |
| `draw/plot_laser_powersweep.py` | `python draw/plot_laser_powersweep.py` | S21 overlay: fixed (T, Pv), varying Pl |
| `draw/plot_VNA_powersweep.py` | `python draw/plot_VNA_powersweep.py` | S21 overlay: fixed (T, Pl), varying Pv |

## Temperature Stability System

`AdvancedStabilityMonitor` in `stability_monitor.py` implements 6 methods via `config.stability_method`:

| Method | Behaviour |
|---|---|
| `simple` | All readings within tolerance for hold duration |
| `v1` | Mean error + variance below thresholds |
| `v2` | Absolute error, relative error, max-delta combined |
| `v3` | Standard deviation + error over time window |
| `custom` | Rolling 1-minute averages; delta between consecutive windows; final band check (production default) |

The `custom` method uses two-phase protocol: `ready_for_adjust` (rate-of-change stable) → `stable` (within final band).

For the GUI experiment runner's simplified controller, see [ui/CLAUDE.md](ui/CLAUDE.md).

## Memory Monitoring System

`memory_monitor.py` provides lightweight Windows memory monitoring via `ctypes` (`GlobalMemoryStatusEx`), zero PyPI deps.

- `MemoryMonitor().check()` → `MemoryInfo` with warning/critical thresholds
- `MemoryMonitor().track(label)` — context manager for trend analysis
- `get_top_processes()` — PowerShell `Get-Process` Top-5 dump at startup

**GUI runner**: auto-pause if available < `memory_auto_pause_threshold_mb` (3 GB); `gc.collect()` on finish; warns if runtime > `long_experiment_warning_hours`.
**CLI runner**: `MemoryMonitor` injected into `wait_for_temperature()` — checks every 60 s.

## LakeShore Duck-Typing Pattern

`lakeshore_control.py` provides driver-agnostic helpers that work with *any* LakeShore 335 object — try multiple method names, fall back to raw VISA:

- `set_lakeshore_temperature()` — `set_temperature()` → `set_setpoint()` → `setpoint()` → `SETP`
- `set_lakeshore_pid()` — `set_pid()` → raw `PID`
- `get_lakeshore_temperature()` — `get_temperature()` → raw `KRDG? A`
- `configure_lakeshore_serial()` — sets baud/parity on ASRL handle
- `_raw_lakeshore_handle()` — introspects for `.write()`-capable attribute

## Hardware & VISA Addresses

| Instrument | Interface | Addresses used |
|---|---|---|
| Keysight PXI VNA | HiSLIP/TCPIP | `TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR` |
| Keysight N7779C Laser | TCPIP | `TCPIP0::K-N7779C-00108::inst0::INSTR` (config.py), `TCPIP0::169.254.77.29::INSTR` (legacy) |
| LakeShore 335 | RS-232 Serial | `ASRL4::INSTR` (57600 baud, 7 data bits, odd parity) |

**Critical**: PXI VNA cannot use raw PXI resource addresses — must use HiSLIP format. `vna_control.build_hislip_addresses()` generates candidates; `vna_control.try_connect()` returns `(resource, is_pxi, conn_type)` — if `is_pxi` is True, the address is wrong.

## Experiment Data Directory Structure

GUI runs:
```
experiment_data/{YYYYMMDD_HHMMSS}/{target}K/actual_{actual}K/{vna_dbm}dBm/{laser_mw}mW/
    YBCO_{vna_dbm}dBm_{laser_mw}mW_target_{target}K_actual_{actual}K.s2p
```

CLI runs use `config.py`'s `base_folder` with slightly different naming (no VNA power level in filename).

Ramp runs:
```
experiment_data/{YYYYMMDD_HHMMSS}/
    ramp_log.csv               # per-step CSV with timestamps, PID, heater range, stability
    optimal_ramp_params.json   # full parameter summary per temperature step
```

## Common Modifications

All experiment parameters are in **`config.py`** — single source of truth:

| To change | Edit in `config.py` |
|---|---|
| Sweep temperature range | `temperature_levels_k` (default: 26–100 K, step 2) |
| Sweep power levels | `power_levels_mw` (default: [0,1,3,5,7,9]) |
| Stability tolerances | `custom_stability_settings` dict |
| PID values | `PID_PARAMS` dict |
| Setpoint overshoot | `setpoint_adjust_settings` dict |
| Fixed PID zones & overshoot | `FIXED_PID_ZONES` dict |
| Stability fallback | `stability_fallback_settings` |
| VISA addresses | `resource_vna`, `laser_resource`, `resource_lakeshore` |
| Output directory | `date` and `base_folder` |
| Poll/stable/max timing | `temperature_poll_seconds`, `stable_hold_seconds`, `max_wait_seconds` |
| Stability method | `stability_method` (simple/v1/v2/v3/custom) |
| Auto-reconnect | `max_reconnect_attempts` (3), `reconnect_delay_seconds` (2) |
| Memory monitoring | `memory_monitor_enabled`, `memory_warning_threshold_mb`, `memory_critical_threshold_mb`, `memory_check_interval_s`, `memory_auto_pause_threshold_mb`, `long_experiment_warning_hours` |
| VNA power range sweep | `vna_power_range_default_start_dbm`, `vna_power_range_default_stop_dbm`, `vna_power_range_default_step_db` |

To replace an instrument: implement the same public API in a new driver file, update the import in `power_sweep_auto.py`.

**Important**: If the change affects experiment protocol, check `ExperimentWorker.run()` in `ui/workers.py` and `lakeshore335_ramp.py` — they have independent orchestration logic.

## Dependencies

- **pyvisa** (with `visa32.dll` backend — NI-VISA or Keysight IO Libraries)
- **numpy** (temperature diagnostics)
- **scipy** (used by `draw/` scripts — savgol_filter, linregress)
- **PyQt5** (GUI only — not needed for CLI or ramp runner)
- **scikit-rf** (S2P parsing — used by `draw/` and `plot_dashboard/`)
- **matplotlib** (used by `draw/` and `plot_dashboard/`)
- **pytest** (testing only)
- **ctypes** (stdlib — `memory_monitor.py`; Windows only)

## Environment Setup

以下文件含硬编码绝对路径（`D:\YBCO\VNAMeas\...`），迁移到新机器或新目录时需修改：

| 文件 | 硬编码项 | 说明 |
|---|---|---|
| `config.py` | `base_folder`、`experiment_data_base_dir` | CLI/GUI 实验输出目录 |
| `draw/` 十余个绘图脚本 | 数据/输出路径 | 建议改为相对 `__file__` 派生或参数化 |
| `plot_dashboard/plot_laser_sweep_batch.py` | `TARGET_DIR` | 批量绘图输出目录 |
| `report/generate_docx.py` | 模板/输出路径 | Word 报告生成 |

仪器 VISA 地址见 `config.py` 与 `app_settings.json`（GUI 自动持久化设置）。
