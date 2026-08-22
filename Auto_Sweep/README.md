# YBCO KID 微波自动测量系统

> 来源：YBCO_TA monorepo @3d28a76（v1.2）快照，2026-08-22。本仓库为 Auto_Sweep 独立版本（AutosweepSmlab201ver）。

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![tests](https://img.shields.io/badge/tests-pytest-red.svg)](https://docs.pytest.org/)

面向 **YBCO 高温超导 KID（Kinetic Inductance Detector）** 的自动化微波测量平台。协调 LakeShore 335 温控仪、Keysight PXI VNA 矢量网络分析仪和 Keysight N7779C 可调激光器，在 **(温度, VNA功率, 激光功率)** 三维参数空间中对 S 参数进行全自动扫描采集，并配套数据分析工具提取谐振频率偏移与 Q 值变化。

---

## 目录

- [1. 物理背景](#1-物理背景)
- [2. 测量变量空间](#2-测量变量空间)
- [3. 硬件拓扑](#3-硬件拓扑)
- [4. 项目架构](#4-项目架构)
- [5. 快速上手](#5-快速上手)
- [6. 数据与文件结构](#6-数据与文件结构)
- [7. 实验参数全映射](#7-实验参数全映射)
- [8. 数据分析工具](#8-数据分析工具)
- [9. 运行测试](#9-运行测试)
- [10. 依赖](#10-依赖)

---

## 1. 物理背景

### 被测器件

YBCO（YBa₂Cu₃O₇₋ₓ）薄膜在超导转变温度以下制成共面波导（CPW）谐振器，用作 KID。微波频段下，谐振器在 S₂₁ 传输谱上表现为一个极窄的吸收谷：

```
|S21| (dB)
  ↑
  0 ├────────────────────────────────────
    │            ╲          ╱
    │             ╲        ╱
    │              ╲      ╱  ← 谐振谷 (resonance dip)
    │               ╲    ╱
 -20 ├─ ─ ─ ─ ─ ─ ─  ╲╱  ─ ─ ─ ─ ─ ─ ─
    │
    └──────────────────────────────────→ f (GHz)
                      ↑
                     f₀ (谐振频率)
```

### 核心物理量

| 参数 | 符号 | 物理含义 | 在 S₂₁ 谱上的几何特征 |
|---|---|---|---|
| 谐振频率 | *f*₀ | 超导 LC 回路本征频率 | 谷底最低点对应的频率 |
| 品质因数 | *Q* | 能量存储 / 每周期能量损耗 | *Q* = *f*₀ / Δ*f*₃dB，谷越深/越窄，*Q* 越高 |
| 频率偏移 | Δ*f*₀ | 表面阻抗（*L*ₛ）随准粒子浓度的变化 | 谷底沿频率轴的位移 |
| 插入损耗 | \|S₂₁\|ₘᵢₙ | 准粒子耗散（*R*ₛ）的度量 | 谷底处的 dB 深度 |

### 物理图像

YBCO 超导态由 Cooper 对凝聚体构成。外部扰动——温升（热激发拆对）或光子注入（光致拆对）——均会增大准粒子密度，进而改变复电导率：

- 实部变化（σ₁ 增大）→ 表面电阻 *R*ₛ ↑ → **Q 值下降**（谷变浅、变宽）
- 虚部变化（σ₂ 减小）→ 表面电感 *L*ₛ ↑ → **f₀ 红移**（谷向左移动）

实验的核心目标即是通过控制 *Tr*、*Pl* 并测量 *Pv* 下的 S₂₁ 谱，建立 **f₀(Tr, Pl, Pv)** 和 **Q(Tr, Pl, Pv)** 的定量依赖关系。

---

## 2. 测量变量空间

实验扫描三个独立变量，遍历一个三维网格：

```
                      ┌────────────────────┐
                      │  激光功率 Pl         │
                      │  0, 1, 3, 5, 7, 9  │
                      │  单位: mW           │
                      │  作用: 光子拆对      │
                      └─────────┬──────────┘
                                │
  ┌──────────────────┐          │          ┌────────────────────┐
  │  温度 Tr          │          │          │  VNA 源功率 Pv      │
  │  6 → 100 K       │          │          │  -45, -35, -25 dBm │
  │  步进 2 K        │          │          │  作用: 微波读出功率  │
  │  作用: 热拆对     │          │          │                    │
  └──────────────────┘          │          └────────────────────┘
                                │
                                ▼
                     ┌─────────────────────┐
                     │  每个 (Tr, Pv, Pl)    │
                     │  → 一张 .s2p 文件     │
                     │  → S₂₁(f) 复数频谱    │
                     └─────────────────────┘
```

典型实验规模：~38 温度点 × 3 VNA 功率 × 6 激光功率 ≈ **684 个 .s2p 文件**。

---

## 3. 硬件拓扑

```
┌──────────────┐     RS-232 (57600,7,O,1)      ┌───────────────┐
│  LakeShore   │◄────────────────────────────►│   控制主机      │
│  335 温控仪   │    SETP / KRDG? / PID         │  (Windows)    │
│              │                               │               │
│  Cryostat    │                               │  pyvisa       │
│  Heater +    │                               │  + NI-VISA    │
│  Sensor      │                               │               │
└──────────────┘                               │               │
                                               │               │
┌──────────────┐     TCP/HiSLIP                 │               │
│  Keysight    │◄─────────────────────────────►│               │
│  PXI VNA     │    CALC:MEAS:DATA? SDATA?     │               │
│              │    (*.s2p over HiSLIP)         │               │
└──────────────┘                               │               │
                                               │               │
┌──────────────┐     TCP/IP (VXI-11)           │               │
│  Keysight    │◄─────────────────────────────►│               │
│  N7779C 激光 │     POW / OUTP:STAT            │               │
│  1550 nm     │                               └───────────────┘
└──────────────┘
         │
         ▼  光纤入低温恒温器 → 照射 YBCO KID 芯片
```

---

## 4. 项目架构

```
Auto_Sweep/
├── app.py                          # PyQt5 GUI 入口
├── config.py                       # 所有实验常量（单一配置源）
│
├── 算法层（纯逻辑，无硬件依赖）
│   ├── stability_monitor.py        # 温度稳定性判定（6 种方法，默认 custom）
│   ├── pid_controller.py           # 按温区查表选择 P/I/D（CLI 用）
│   ├── pid_parameters.py           # PID 区管理 + 加热器范围策略（Ramp 用）
│   ├── temperature_diagnostics.py  # 8 问题分类诊断器（CLI 用）
│   └── temperature_state_diagnostics.py  # 7 状态分类 + PID 自动调整（Ramp 用）
│
├── 仪器驱动层（pyvisa 封装）
│   ├── lakeshore_control.py        # LakeShore 335 — SETP, KRDG?, PID, HTRNG
│   ├── laser_driver.py             # Keysight N7779C — 功率/波长/开关
│   ├── laser_control.py            # 旧版激光驱动（独立脚本用）
│   └── vna_control.py              # VNA — HiSLIP 发现 + S2P 保存
│
├── 实验编排层
│   ├── power_sweep_auto.py         # CLI 全自动实验：温控→激光扫描→VNA采集
│   └── lakeshore335_ramp.py        # 独立温控爬坡（无 VNA/激光，10→80K）
│
├── GUI 层 (PyQt5)
│   └── ui/
│       ├── main_window.py          # QStackedWidget 路由 + 自动重连
│       ├── dashboard_page.py       # 总览面板（温度扫描配置 + 一键连接）
│       ├── laser_page.py           # 激光控制（3×6 功率网格）
│       ├── lakeshore_page.py       # 温控面板（实时读数 + 回路控制）
│       ├── vna_page.py             # VNA 面板（频率/功率/扫频控制）
│       ├── workers.py              # QThread 工作线程 + ExperimentWorker
│       ├── experiment_stability_controller.py  # 固定 PID 稳定性状态机
│       └── widgets.py              # StatusLight, DeviceCard, PresetManager
│
├── 数据分析
│   └── draw/
│       └── plot_laser_powersweep.py # 单 (Tr, Pv) 下多激光功率 S₂₁ 叠加图
│
├── tests/                          # BDD/TDD 测试套件 (pytest, 400+ 用例)
├── testcode/                       # 交互式调试工具（激光/VNA 探针）
├── presets/                        # 各设备 JSON 参数预设
└── experiment_data/                # 实验输出（按时间戳组织）
```

**分层依赖原则**：`config → 算法 → 仪器驱动 → 编排 → GUI`。每层只依赖下层，无循环引用。替换一个仪器只需修改一个驱动文件。

---

## 5. 快速上手

### 5.1 启动 GUI 控制面板（推荐）

```bash
cd Auto_Sweep
python app.py
```

操作流程：
1. 在 Dashboard 点击 **Connect All**（或依次连接三台设备）
2. 在 Dashboard 配置温度扫描（Fixed Points / Range Sweep）
3. 在 Laser 页面配置激光功率序列和波长
4. 在 VNA 页面设置频率范围（Start/Stop）、源功率列表、IF 带宽
5. 点击 **Start** 开始自动测量
6. 观察 Dashboard 上的实时温度和稳定性状态
7. 实验数据自动存入 `experiment_data/{timestamp}/`

### 5.2 CLI 命令行运行

```bash
python power_sweep_auto.py
```

所有参数在 `config.py` 中预设。适用于无 GUI 的远程/批量运行。

### 5.3 独立温度爬坡

```bash
# 默认 10K → 80K，步进 2K
python lakeshore335_ramp.py

# 自定义范围
python lakeshore335_ramp.py --start 20 --end 30 --step 2 --hold-seconds 60
```

输出 CSV 日志 + JSON 最优参数汇总。

---

## 6. 数据与文件结构

### 6.1 实验输出目录

```
experiment_data/
└── 20260605_215526/               ← 时间戳 (YYYYMMDD_HHMMSS)
    ├── 6K/                        ← 目标温度
    │   └── actual_6.452K/         ← 实际温度（LakeShore 读数）
    │       ├── -25dBm/            ← VNA 源功率
    │       │   ├── 00mW/          ← 激光功率
    │       │   │   └── YBCO_-25dBm_00mW_target_6K_actual_6.452K.s2p
    │       │   ├── 01mW/ → *.s2p
    │       │   ├── 03mW/ → *.s2p
    │       │   ├── 05mW/ → *.s2p
    │       │   ├── 07mW/ → *.s2p
    │       │   └── 09mW/ → *.s2p
    │       ├── -35dBm/ → {6 个激光功率}
    │       └── -45dBm/ → {6 个激光功率}
    ├── 8K/actual_7.718K/ → ...
    └── ...
```

### 6.2 S2P 文件格式

标准的 Touchstone 2-port 格式，包含频率 (Hz)、S₁₁、S₂₁、S₁₂、S₂₂ 的复数数据。可直接用 `scikit-rf` 读取：

```python
import skrf as rf
ntwk = rf.Network("YBCO_-25dBm_00mW_target_6K_actual_6.452K.s2p")
freq = ntwk.f           # 频率数组 (Hz)
s21  = ntwk.s[:, 1, 0]  # S₂₁ 复数数组（端口 1→端口 2）
```

---

## 7. 实验参数全映射

以下六张表覆盖实验中的全部物理量、控制变量和导出参数。每项均附缩写、中文术语、代码变量名、仪器 SCPI 指令（如适用）和典型范围。

> **约定**：缩写沿用超导 KID 文献惯例。PID 三系数记为 P / I / D。

---

### 7.1 温度子系统 — LakeShore 335

| 缩写 | 中文术语 | 代码变量 | 物理含义 | 仪器指令 | 单位 | 典型值 |
|---|---|---|---|---|---|---|
| **Tr** | 实测温度 | `actual_temp`, `temperature_a` | 传感器 A 瞬时读数，实验因变量 | `KRDG? A` | K | 4–300 |
| **Tt** | 目标温度 | `target_k`, `target_temp_k` | 实验期望稳态温度（纯软件变量） | — | K | 6–100 |
| **SP** | 当前设定点（瞬时） | `current_setpoint` | 温控仪此刻追踪的中间值，随 ramp rate 连续演化 | `SETP? 1` | K | 4–300 |
| **SP₀**| 稳态设定点（命令值） | `commanded_setpoint` | 写入温控仪后不再改变的终点值，等于 Tt + Δos | `SETP 1,{v}` | K | 4–300 |
| **RR** | 升温速率 | `ramp_rate` | SP 从旧值向 SP₀ 爬升的速率，决定 SP(t) 时变斜率 | `RAMP 1,{v}` | K/min | 0–100（默认 0.5） |
| **HR** | 加热器档位 | `heater_range`, `range_1` | 0=Off, 1=Low, 2=Medium, 3=High（3 禁止用于实验） | `RANGE 1,{v}` | — | 0–3 |
| **H%** | 加热器输出百分比 | `heater_1` | 当前加热器实际输出占最大之比，指示 PID 努力程度 | `HTR? 1` | % | 0–100 |
| **P**  | 比例增益 | `pid_p1`, `p` | PID 比例项系数，决定响应速度 | `PID 1,P,{v},I,…` | — | 100–150 |
| **I**  | 积分时间 | `pid_i1`, `i` | PID 积分项系数，消除稳态误差（≤20K 时启用） | 同上 | — | 0–5 |
| **D**  | 微分时间 | `pid_d1`, `d` | PID 微分项系数（所有温区恒为 0） | 同上 | — | 0 |
| **Δos**| 过冲量 | `current_overshoot`, `base_overshoot_k` | SP₀ − Tt，温区预设值 + 动态补偿 | — | K | 0–10 |
| **αos**| 过冲系数 | `overshoot_factor` | Δos 对温度误差 = Tt − Tr 的响应比例 | — | — | 0.5 |

> **SP 与 SP₀ 的区别**：`SETP` 命令写入的是稳态目标 SP₀。由于 ramp rate 的存在（默认 0.5 K/min），仪表的内部设定点会从旧值按 RR 逐步爬升到 SP₀——这个随时间变化的中间态即是 SP(t)。`SETP?` 读回的是瞬时 SP，而非 SP₀。

---

### 7.2 稳定性判据 — 纯算法（无仪器指令）

| 缩写 | 中文术语 | 代码变量 | 物理含义 | 单位 | 典型值 |
|---|---|---|---|---|---|
| **ε₁** | 粗稳容差 | `avg_tolerance_k` | 1 分钟窗口平均与 Tt 的允许偏差 | K | 1.0 |
| **ε₂** | 漂移容差 | `delta_tolerance_k` | 相邻两分钟窗口平均间的最大允许 Δ | K | 0.5 |
| **ε₃** | 精稳带宽 | `final_stable_band_k` | 最终判定稳定所需的温度带半宽 | K | 0.5 |
| **tₕ** | 稳定保持时间 | `stable_hold_seconds` | Tr 连续落在 ε₃ 内达到此秒数后才判定 stable | s | 60 |
| **tₚₒₗₗ** | 轮询间隔 | `temperature_poll_seconds` | 两次温度读数之间的等待秒数 | s | 10 |
| **tₘₐₓ** | 超时上限 | `max_wait_seconds` | 单温度点最长等待，超时强行继续 | s | 1800 |
| **t𝒹** | 诊断间隔 | `diagnostic_interval` | 两次周期诊断之间的间隔 | s | 30 / 60 |
| **Nₘᵢₙ** | 最小读数 | `min_readings_required` | custom 方法启动稳定判定所需的最少读数 | — | 10 |
| **τ𝓌** | 平均窗口 | `avg_window_seconds` | 滚动平均窗口长度 | s | 60 |

---

### 7.3 激光子系统 — Keysight N7779C

| 缩写 | 中文术语 | 代码变量 | 物理含义 | 仪器指令 | 单位 | 典型值 |
|---|---|---|---|---|---|---|
| **Pl** | 激光功率 | `power_mw`, `power_levels_mw` | 光纤输出端面光功率，光子拆对的外控变量 | `SOUR:POW {v} mW` | mW | 0–17 |
| **λ** | 波长 | `wavelength_nm`, `target_wavelength` | 载波波长（C 波段），决定单光子能量 | `SOUR:WAV {v} NM` | nm | 1520–1570 |
| — | 出光状态 | `output_state` | ON: 激光输出；OFF: 内部快门闭合 | `OUTP:STAT {ON/OFF}` | — | ON/OFF |
| **tₛ** | 光路稳定等待 | `time_elapse` | 切换功率后等待光路与热平衡的秒数 | — | s | 20 |

---

### 7.4 VNA 子系统 — Keysight PXI VNA

| 缩写 | 中文术语 | 代码变量 | 物理含义 | 仪器指令 | 单位 | 典型值 |
|---|---|---|---|---|---|---|
| **Pv** | VNA 源功率 | `vna_power_list`, `selected_dbm` | 微波读出信号强度，影响准粒子激发与信噪比 | `SOUR:POW {v} dBm` | dBm | −50 ~ −10 |
| **f_start** | 起频 | `start_freq_ghz`, `_start_spin` | 扫频起始频率 | `SENS:FREQ:START {v} GHz` | GHz | 1–14 |
| **f_stop** | 止频 | `stop_freq_ghz`, `_stop_spin` | 扫频终止频率 | `SENS:FREQ:STOP {v} GHz` | GHz | 1–14 |
| **f_c** | 中心频率 | `center_freq` | (f_start + f_stop) / 2，联动计算 | `SENS:FREQ:CENT {v}` | GHz | 1–14 |
| **Δf_span** | 频率跨度 | `span_freq` | f_stop − f_start | `SENS:FREQ:SPAN {v}` | GHz | 0–13 |
| **Sᵢⱼ** | S 参数选择 | `s_parameter` | 测量的散射参数类型，KID 常用 S₂₁ | `CALC:PAR:DEF 'MEAS',S21` | — | S21 |
| **Npts** | 扫频点数 | `sweep_points` | 单次扫描的频率采样点数，决定频率分辨率 | `SENS:SWE:POIN {n}` | — | 201–64001 |
| **BW_IF** | 中频带宽 | `if_bandwidth` | 中频滤波器带宽，窄→低噪/慢，宽→快/噪 | `SENS:BWID {v} Hz` | Hz | 10–100k |

---

### 7.5 导出参数 — 从 S2P 后处理提取

| 缩写 | 中文术语 | 提取方式 | 物理含义 | 单位 | 依赖关系 |
|---|---|---|---|---|---|
| **f₀** | 谐振频率 | \|S₂₁(f)\| 最小值对应频率 | 超导 LC 本征谐振频点 | GHz | f₀(Tr, Pl, Pv) |
| **Dₘᵢₙ** | 谷底深度 | min( \|S₂₁(f)\| ) | 谐振谷最低点的 dB 值，反映准粒子损耗 | dB | Dₘᵢₙ ∝ σ₁ |
| **Δf₃dB** | 3 dB 带宽 | \|S₂₁\| = Dₘᵢₙ + 3 dB 处两频点之差 | 谐振谷半高全宽 | MHz | 反映总损耗 |
| **Q** | 品质因数 | Q = f₀ / Δf₃dB | 谐振储能 / 每周期损耗，核心性能指标 | — | Q(Tr, Pl, Pv) |
| **Qi** | 内部 Q 值 | 圆拟合（待实现） | 仅本征损耗（超导体内部），排除耦合贡献 | — | Qi(Tr, Pl) |
| **Qc** | 耦合 Q 值 | 圆拟合（待实现） | 仅耦合损耗（谐振器到馈线的能量泄漏） | — | Qc(Pv) |
| **δf₀** | 谐振频移 | f₀(当前态) − f₀(参考态) | 准粒子密度变化引起的电感变化量 | MHz | δf₀ ∝ δn_qp |
| **δQ⁻¹**| 损耗增量 | Q⁻¹(当前态) − Q⁻¹(参考态) | 准粒子耗散的变化 | — | ∝ σ₁ 变化 |

---

### 7.6 稳定性状态机参数（GUI 专用）

| 缩写 | 中文术语 | 代码变量 | 含义 | 典型值 |
|---|---|---|---|---|
| **Nₐ𝒹ⱼ** | 最大调整次数 | `MAX_SETPOINT_ADJUSTMENTS` | Δos 调整的允许次数上限 | 2 |
| **εg** | good-enough 带 | `GOOD_ENOUGH_BAND_K` | Nₐ𝒹ⱼ 次调整后仍不稳定时的兜底容差 | ±0.5 K |
| **Δos_max** | 过冲安全上限 | `MAX_OVERSHOOT_K` | Δos 的绝对钳位，防止温控超出安全范围 | 10 K |

---

### 核心变量关系

```
实验自变量:  Tr  ×  Pl  ×  Pv
                │
                ▼
实验因变量:  f₀(Tr, Pl, Pv)    谐振频率
            Q(Tr, Pl, Pv)      品质因数
            Dₘᵢₙ(Tr, Pl, Pv)   谷底深度

设定点时变:  SP(t) ──RR──→ SP₀ = Tt + Δos
```

---

## 8. 数据分析工具

当前 `draw/` 目录下提供基础 S₂₁ 可视化：

```bash
# 修改脚本中的 target_dir 路径后运行
python draw/plot_laser_powersweep.py
```

**当前功能**：固定 (Tr, Pv) 下，将不同激光功率的 S₂₁(dB) vs f(GHz) 叠加在一张图上。

**待开发的分析流水线**：

```
.s2p 文件 ──→ [寻谷] ──→ f₀, |S21|_min, BW_3dB ──→ Q = f₀ / BW_3dB
                              │
                              ▼
            ┌─────────────────────────────────┐
            │  跨变量趋势：                     │
            │  f₀(Tr, Pl, Pv)                  │
            │  Q(Tr, Pl, Pv)                   │
            │  Δf₀ vs 准粒子密度                │
            └─────────────────────────────────┘
```

---

## 9. 运行测试

项目采用 **BDD/TDD** 方法论（约 400+ 测试用例覆盖算法与 GUI 逻辑）：

```bash
pytest tests/ -v                          # 全量测试
pytest tests/test_stability_monitor.py -v  # 稳定性算法
pytest tests/test_pid_controller.py -v     # PID 选参逻辑
pytest tests/test_experiment_worker.py -v  # GUI 实验线程
pytest tests/test_auto_reconnect.py -v     # 自动重连机制
pytest tests/ -k "test_given" -v           # 按命名模式筛选
```

测试命名遵循 Given-When-Then 规范：
```python
def test_given_all_readings_in_band_when_checking_then_returns_stable(self):
    ...
```

---

## 10. 依赖

| 包 | 用途 |
|---|---|
| `pyvisa` | VISA 仪器通信（需 NI-VISA 或 Keysight IO Libraries 后端） |
| `numpy` | 温度诊断中的数值计算 |
| `PyQt5` | GUI 面板（仅 `app.py` 需要） |
| `scikit-rf` | S2P 文件解析与微波网络分析（数据分析用） |
| `matplotlib` | 数据可视化 |
| `pytest` | 测试框架（开发/CI 用，生产环境可不装） |

---

## 版本历史

### v1.1 (2026-06-09) — 可视化与测量逻辑增强

**新增模块**

| 模块 | 说明 |
|---|---|
| `plot_dashboard/` | 交互式 PyQt5 数据可视化面板 — 支持按温度/功率/VNA 功率筛选、批量 S₂₁ 叠加图绘制、Q 值面板 |
| `readme_generator.py` | 实验数据头部 `readme.txt` 自动生成器 — 包含设备参数、实验时长、环境温度、操作人员 |
| `draw/plot_laser_powersweep.py` | 固定 (Tr, Pv) 下多激光功率 S₂₁ 叠加图绘制脚本 |

**测量逻辑改进**

| 改进 | 说明 |
|---|---|
| 双阶段轮询 | 稀疏轮询（30s，趋于平稳前）→ 密集轮询（5s，进入目标区间后），加速稳态判定 |
| 稳态检测 | 新增 3 分钟窗口 max−min ≤ 0.1K 独立稳态判据，与目标区间判定并行 |
| 测量逻辑版本号 | `config.MEASUREMENT_LOGIC_VERSION = "2026-06-08"`，写入数据头部便于回溯 |
| VNA 功率区间扫描 | GUI 新增功率区间模式（start/stop/step），与固定功率按钮网格并行 |
| 实验重试机制 | S₂₁ 偏差超过阈值（0.5 dB）时自动重测（最多 2 次） |
| 低温超时保护 | 30 分钟超时，防止低温区无限等待 |

**配置与架构**

| 改动 | 说明 |
|---|---|
| `config.py` 新增 8 组常量 | 稳态判定、双阶段轮询、功率扫描、重试参数、版本号等 |
| `temperature_diagnostics.py` → 删除 | 已被 `temperature_state_diagnostics.py`（7 状态分类器）完全替代 |
| `stability_monitor.py` 重构 | 简化接口，融入稳态检测逻辑 |
| `ui/vna_page.py` | 新增 VNA 功率区间扫描 UI |
| `ui/workers.py` | 集成重试逻辑、功率区间扫描、readme 自动生成 |
| `ui/experiment_stability_controller.py` | 重构稳定性状态机 |

**文档**

| 文件 | 说明 |
|---|---|
| `README.md` | 完整项目文档 — 物理背景、变量空间、硬件拓扑、6 张参数映射表 |
| `CLAUDE.md` | AI 辅助开发文档 — 架构图、文件地图、修改指南 |

**测试**

- 新增 3 个测试文件：`test_readme_generator.py`、`test_vna_power_sweep.py`、`test_measurement_retry.py`
- 扩展 4 个已有测试文件：`test_config.py`、`test_stability_monitor.py`、`test_stability_fallback.py`、`test_experiment_worker.py`
- 测试总数：263 passed, 30 skipped（skipped = 待实现的 TDD Red 阶段用例）

**数据清理**

- 从版本库移除所有 `.s2p`、`.txt`、`.csv` 数据文件及日志
- 完善 `.gitignore`：排除实验数据、压缩包、临时文件

---

### v1.0 (2026-06-05) — 初始版本

- 三层自动化测量：温控（LakeShore 335）→ 激光功率扫描（Keysight N7779C）→ VNA S 参数采集（Keysight PXI）
- PyQt5 GUI 控制面板（Dashboard / Laser / LakeShore / VNA 四页）
- CLI 命令行运行 (`power_sweep_auto.py`)
- 独立温度爬坡控制器 (`lakeshore335_ramp.py`)
- 温度稳定性判定（6 种方法，默认 custom 双阶段协议）
- PID 参数按温区查表（3 温区：Low/Medium/High）
- 自动重连机制（连接断开后 3 次重试）
- 温度安全互锁（降温前自动关加热器等待）
- JSON 预设管理系统
- BDD/TDD 测试套件（pytest，200+ 用例）
- 自动设置持久化（`app_settings.json`）

---

## 引用

如果本项目对你的研究有帮助，请引用相关论文（待补充）。

---

*维护者：Teskiel · 最后更新：2026-06-09*
