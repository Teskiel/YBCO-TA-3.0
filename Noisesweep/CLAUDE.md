# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

KID（Kinetic Inductance Detector，动态电感探测器）低温测量系统的 PyQt5 桌面程序，控制三台仪器完成 IQ 校准、S21 谐振器扫描和噪声采集。项目为**单文件扁平结构，没有包、没有构建系统、没有测试**——所有 `.py` 文件必须放在同一目录下，靠直接 `import` 模块名相互引用。

运行入口是 `kid_measurement_gui_v3.py`，从项目目录启动：

```bash
python kid_measurement_gui_v3.py
```

启动时会自动创建 `data/IQ_calibration/`、`data/S21/`、`data/noise/` 三个默认保存目录（相对脚本所在目录）。Python 版本为 3.14（见 `__pycache__/cpython-314`）。

## 依赖

```
PyQt5  matplotlib  numpy  h5py  pyvisa  nidaqmx  scipy
```

仅 S21 的 SCRAPS 拟合需要额外安装 `scraps` 和 `lmfit`（拟合失败不阻断测量，数据仍正常保存）。`scipy` 用于 `scipy.signal.welch` 噪声功率谱。

## 三台仪器与模块分工

| 仪器 | 控制器文件 | 接口 | 用途 |
|------|-----------|------|------|
| Keysight E8257D 信号源 | `E8257D_controller.py` | PyVISA / SCPI | S21 与噪声扫描的频率/功率激励源 |
| Keysight P5002A VNA | `P5002A_controller.py` + `P5002A_gui.py` | PyVISA / HiSLIP | 仅作 IQ 校准的第二射频源（CW 模式） |
| NI PXIe-4480 DAQ | `PXIE4480_controller.py` | nidaqmx | 采集 I/Q 时序电压 |

**关键架构点：S21 扫描不是用 VNA 扫频，而是逐频点设置 E8257D、用 PXIe-4480 读取 I/Q 电压。** P5002A 只在 IQ 校准阶段参与（与 E8257D 同频同功率产生参考椭圆）。因此：

- IQ 校准需要三台仪器全部连接（E8257D + P5002A + PXIe）。
- S21 和噪声测量只需要 E8257D + PXIe。

算法/数据处理模块（无 GUI、无仪器依赖）：

- `IQ_calibration.py` — `IQEllipseCalibrator`（椭圆拟合，MATLAB 移植）+ `IQCalibrationTable`（频率相关校准参数插值/外推）。
- `S21_fitting.py` — `ScrapsS21Fitter`（scraps+lmfit 拟合）、`S21NoiseCalibration`（噪声 IQ→幅度/相位换算）、`welch_psd`。
- `plot_S21_hdf5.py` — 独立脚本，读 S21 HDF5 并绘图（顶部 `FILE_PATH` 需手动改）。

## 架构：共享会话与线程模型

这是理解本代码库的核心，需要跨文件才能看清：

1. **`InstrumentManager`（`kid_measurement_gui_v3.py`）是唯一的仪器会话持有者。** 全局只存在一个 E8257D 实例（`manager.source`）、一个 P5002A 实例（`manager.p5002_window.vna`）和一份 PXIe 配置字典 `manager.daq_config`（device_name/channels/sample_rate/voltage_range/coupling/trigger_*）。所有测量 Tab 通过 `self.manager` 共享它们。

2. **线程安全用 `RLock` 实现**：`manager.source_lock`、`manager.p5002_lock`、`manager.daq_lock`。任何在 worker 线程里读写对应仪器的代码块必须先持有锁。

3. **所有阻塞的 VISA/nidaqmx 调用都跑在 `QThread` worker 里，通过 `pyqtSignal` 回传结果**，避免冻结 GUI。worker 有：
   - `MeasurementWorker`（S21 扫描 + 噪声采集）
   - `IQCalibrationWorker`（IQ 校准）
   - `PXIePreviewWorker`（PXIe 电压预览）
   - `P5002A_gui.py` 里的 `TaskThread`（P5002A 单次阻塞操作）

4. **GUI 结构**：`MainWindow` 含四个 Tab——`CombinedMeasurementTab`（S21+噪声一键）、`S21Tab`、`NoiseTab`、`IQCalibrationTab`。一键 Tab 不重复实现测量逻辑，而是**把参数写回 `S21Tab`/`NoiseTab` 的控件再调用其 `begin()`**，并用 `active`/`stage` 状态机把两个 worker 串起来。

5. **`PersistentP5002AWindow` 重写了 `closeEvent` 为 hide**，保证关闭仪器控制窗口时共享 VNA 会话不断开。

## 数据流与文件格式

完整链路：**IQ 校准 → `IQCalibrationTable` 频率插值 → S21 扫描 → SCRAPS 拟合 → 噪声采集**。

- **IQ 校准**输出：每个频点一个 `频率Hz-时间戳.txt`（列 `time_s I_raw_V Q_raw_V I_cal_V Q_cal_V`，头部存仪器参数）+ 同名 `-calibration.json`；扫描模式额外生成固定列宽的 `IQ_scan_summary-*.txt`。
- **IQ 汇总 TXT 是校准参数在模块间交接的格式**：`IQCalibrationTable.load()` 用 `np.loadtxt(comments="#")` 读取，前 6 列固定为 `frequency, I0, Q0, A_I, A_Q, rotation_q`。
- **S21 输出**为单个 HDF5：根级存 `frequency_hz`、`channels`、`raw_voltage_V`、`calibrated_iq_voltage_V`、`calibrated_mean_iq_V`、`iq_calibration_parameters`、`s21_magnitude_db`、`s21_phase_deg`；拟合成功时写入 `scraps_fit` 组，其属性 `x0`/`y0`/`Ioffset`/`Qoffset` 是后续噪声换算的必需输入（`S21NoiseCalibration.load()` 依赖它们）。
- **噪声采集不新建文件**，而是追加到所选 S21 HDF5 的 `/noise_measurements/<时间戳>/` 组（`raw_voltage_V`、`calibrated_iq_voltage_V`、`normalized_noise_iq`、`noise_amplitude`、`noise_phase_rad`、`time_s`、Welch PSD 数据集）。

## 关键约定与坑

- **I/Q 通道必须先在 PXIe-4480 公共配置里勾选**，测量 Tab 里选的 I/Q 通道（`ai0..ai5`）必须已包含在 `manager.daq_config["channels"]` 中且两者不同，否则 `begin()` 报错。
- **开始 IQ 校准前必须先停止 PXIe 电压预览**（各 Tab 的 `begin()` 都会检查 `preview_worker.isRunning()`）。
- 测量期间三个仪器控制窗口会被临时禁用，结束/失败后通过 `sync_instrument_controls()` 恢复并回读实时状态。
- 停止是**协作式**的：`worker.stop()` 置位 `stop_requested` 并调用 `daq.stop_continuous()`，当前正在执行的仪器通信/DAQ 读取完成后才退出，已保存频点不丢失。
- `output_path()` 负责生成防碰撞的 HDF5 文件名；S21 拟合成功后按 `谐振器名-频率GHz-温度mK-功率dBm` 用 `s21_resonance_path()` 重命名。
- 改任何文件的导入时注意：本项目**没有包结构**，必须保持平铺、同名模块直 import，且运行时 cwd 须为项目目录。
