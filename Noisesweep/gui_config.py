# -*- coding: utf-8 -*-
"""GUI 配置加载 / 合并 / 持久化（Qt-free）。

gui_config.json 是基准配置（seed 自 Noisesweep 的 noisesweep_config.json，
并修正为绝对路径）。collect_config() 在运行时把 V3 面板控件值 + 自动化 Tab
控件值合并进来，得到可直接喂给 orchestrator_noisesweep.run_one_point 的
config dict。

关键路径必须写绝对路径（相对默认会因 __file__ 位置漂移而失效）：
  - data_directory  : 原 KID v3 包的 data 目录（新 GUI 不复制数据，只引用）
  - save_root       : 自动化测量的保存根目录（默认 = data_directory/S21）
  - data_process_dir: YBCO-TA-3.0/Data_process（芯片标定库 + 追踪表）
  - autosweep_dir   : YBCO-TA-3.0/Auto_Sweep（lakeshore_control/laser_driver）
"""

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "gui_config.json"

# 上次使用参数叠加层（与 gui_config.json 默认基线分离）。测试可用环境变量
# KID_GUI_USER_SETTINGS 指到临时文件，避免污染真实用户设置。
USER_SETTINGS_PATH = Path(os.environ.get(
    "KID_GUI_USER_SETTINGS", SCRIPT_DIR / "gui_user_settings.json"))

# 原 KID v3 包数据目录（本程序只引用、不复制 23GB 数据）
KID_V3_DATA_DIR = (
    r"C:/Users/smlab/Desktop/TeSIA_Project/Python control/data_aquisition/"
    r"KID_measurement_v3_package 23/KID_measurement_v3_package/data"
)
DATA_PROCESS_DIR = r"C:/Windows/System32/YBCO-TA-3.0/Data_process"
AUTOSWEEP_DIR = r"C:/Windows/System32/YBCO-TA-3.0/Auto_Sweep"

SEED = {
    # ---- 后端 / 路径 ----
    "backend": "autosweep",
    "autosweep_dir": AUTOSWEEP_DIR,
    "tracking_file": (Path(DATA_PROCESS_DIR) / "resonance_table.txt").as_posix(),
    "data_process_dir": DATA_PROCESS_DIR,
    "data_directory": KID_V3_DATA_DIR,
    "save_root": (Path(KID_V3_DATA_DIR) / "S21").as_posix(),
    "iq_calibration_file": (
        Path(KID_V3_DATA_DIR) / "IQ_calibration" / "IQ_scan_summary-20260817-153944.txt"
    ).as_posix(),
    "experiment_name": "",
    "checkpoint_path": None,
    "log_file": None,

    # ---- 芯片标定库 ----
    "use_chip_library": True,
    "chip_id": "YBCO#1145",
    "chip_run_id": "20260609-0624__6-80K__full",

    # ---- 温度 ----
    "temperature_list_k": [77.0],
    "fixed_temperature_k": 77.0,
    "stability_tolerance_k": 0.05,
    "stability_hold_s": 60.0,
    "stability_poll_s": 5.0,
    "stability_max_wait_s": 1800.0,
    "abort_on_unstable": False,
    "lakeshore_channel": "A",
    "lakeshore_heater_range": None,
    "lakeshore_pid": None,
    "drive_temperature": False,       # 勾选后自动 set_temperature + wait_for_stability
    "read_actual_temperature": False,  # 勾选后优先读 LakeShore 实际温度
    "temperature_mismatch_limit_k": 1.0,  # 已连接 LakeShore 时 |目标-实测| 超限 → 弹窗中止

    # ---- 激光 ----
    "laser_wavelength_nm": 1550.0,
    "laser_power_mw": [0.0],
    "laser_settle_s": 2.0,
    "predict_laser_shift": True,

    # ---- 谐振列表 ----
    "resonators": [
        {"name": "res1", "reference_frequency_hz": None},
        {"name": "res2", "reference_frequency_hz": None},
        {"name": "res3", "reference_frequency_hz": None},
        {"name": "res4", "reference_frequency_hz": None},
        {"name": "res5", "reference_frequency_hz": None},
    ],

    # ---- 扫描策略（宽/精扫带宽，V3 没有的参数放自动化页）----
    "wide_bandwidth_mode": "fixed",          # fixed / temperature / formula
    "wide_bandwidth_mhz": 150.0,
    "wide_bandwidth_mhz_by_temperature": [[4.0, 50.0], [77.0, 200.0]],
    "wide_bandwidth_interpolation": "linear",
    "wide_bandwidth_formula": None,
    "fine_bandwidth_mhz_by_temperature": [[4.0, 15.0], [20.0, 20.0],
                                          [40.0, 20.0], [77.0, 25.0]],
    "fine_bandwidth_interpolation": "step",

    # ---- S21 / 噪声 / DAQ 默认（运行时由 V3 面板控件值覆盖）----
    "s21_points": 101,
    "samples_per_point": 1000,
    "settle_s": 0.010,
    "readout_power_dbm": -30.0,
    "scraps_readout_power_dbm": -60.0,
    "noise_frequency_mode": "F0_PLUS_DF",
    "noise_duration_s": 10.0,
    "noise_block_samples": 10000,
    "noise_power_dbm": -30.0,
    "noise_settle_s": 0.1,
    "noise_continuous": False,
    "noise_manual_frequency_hz": 0.0,
    "welch_window": "hamming",
    "welch_segment_seconds": 1.0,
    "pxie_device_name": "PXI2Slot2",
    "channels": [0, 1],
    "i_channel": 0,
    "q_channel": 1,
    "sample_rate": 1_000_000.0,
    "voltage_range": 1.0,
    "coupling": "DC",
    "trigger_mode": "IMMEDIATE",
    "trigger_source": None,
    "trigger_edge": "RISING",

    # ---- 仪器地址 ----
    "e8257d_visa_address": "GPIB1::19::INSTR",
    "lakeshore_visa_address": None,
    "laser_visa_address": None,

    # ---- 其它 ----
    "save_figures": True,
    "skip_noise": False,
}


def _load_json_optional(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_gui_config(path=None):
    """读 gui_config.json；文件缺失时用 SEED 写回并返回 SEED。"""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    data = _load_json_optional(p)
    if data is None:
        data = dict(SEED)
        save_gui_config(data, p)
    return data


def save_gui_config(config, path=None):
    """把需持久化的 GUI 设置写回 gui_config.json（UTF-8）。"""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    p.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def load_user_settings(path=None):
    """读上次使用参数叠加层；文件缺失/损坏 → {}（只用 gui_config.json 默认）。

    返回 dict 或 {}。不写回文件（与 load_gui_config 不同）：首次运行无用户文件时
    静默用默认，避免启动即生成文件。
    """
    p = Path(path) if path else USER_SETTINGS_PATH
    data = _load_json_optional(p)
    return data if isinstance(data, dict) else {}


def save_user_settings(settings, path=None):
    """把上次使用参数写回 gui_user_settings.json（UTF-8）。"""
    p = Path(path) if path else USER_SETTINGS_PATH
    p.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_number_list(text):
    """解析逗号/空格分隔的阿拉伯数字串 → [float, ...]；空/非法返回 []。

    例如 "0,1,3" → [0.0, 1.0, 3.0]。仅允许数字、小数点、逗号、空格、负号。
    """
    if not text:
        return []
    parts = [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]
    out = []
    for part in parts:
        try:
            out.append(float(part))
        except ValueError:
            return []
    return out


def parse_res_index_list(text):
    """解析谐振器数字选择串 "1,3,5" → [0, 2, 4]（0-based 索引）。"""
    nums = parse_number_list(text)
    if not nums or any(float(x) != int(x) for x in nums):
        return []
    idx = [int(x) - 1 for x in nums]
    if any(i < 0 for i in idx):
        return []
    return idx


def collect_config(window, gui_cfg, auto_tab):
    """运行时合并：gui_cfg 为基 → V3 面板控件值覆盖 → 自动化 Tab 控件值覆盖。

    返回可直接交给 run_one_point 的 config dict。
    """
    cfg = dict(gui_cfg)
    s = window.s21
    n = window.noise
    m = window.manager

    # ---- S21 参数 ← V3 S21 Tab 控件 ----
    cfg["s21_points"] = int(s.points.value())
    cfg["samples_per_point"] = int(s.samples.value())
    cfg["settle_s"] = float(s.settle.value())
    cfg["readout_power_dbm"] = float(s.power.value())
    cfg["scraps_readout_power_dbm"] = float(s.fit_power.value())

    # ---- 噪声参数 ← V3 噪声 Tab 控件 ----
    # 校准文件与 I/Q 通道必须与面板3（噪声页）同源：面板3 噪声测量用噪声页的
    # calibration_file/i/q（NoiseTab.begin），自动化若读 S21 页控件，两页一旦
    # 分叉（用户单页改过或设置各自恢复）就会产生"自动化 ≠ 面板3"的测量。
    cfg["iq_calibration_file"] = n.calibration_file.text().strip()
    cfg["i_channel"] = int(n.i.currentData())
    cfg["q_channel"] = int(n.q.currentData())
    cfg["noise_duration_s"] = float(n.duration.value())
    cfg["noise_block_samples"] = int(n.block.value())
    cfg["welch_window"] = n.psd_window.currentText()
    cfg["welch_segment_seconds"] = float(n.segment_seconds.value())
    cfg["noise_frequency_mode"] = str(n.frequency_mode.currentData())
    cfg["noise_power_dbm"] = float(n.power.value())
    cfg["noise_settle_s"] = float(n.settle.value())
    cfg["noise_continuous"] = n.mode.currentIndex() == 1
    cfg["noise_manual_frequency_hz"] = float(n.frequency.value()) * 1e9

    # ---- DAQ 配置 ← manager.daq_config ----
    d = m.daq_config
    cfg["pxie_device_name"] = d["device_name"]
    cfg["channels"] = list(d["channels"])
    cfg["sample_rate"] = float(d["sample_rate"])
    cfg["voltage_range"] = float(d["voltage_range"])
    cfg["coupling"] = d["coupling"]
    cfg["trigger_mode"] = d["trigger_mode"]
    cfg["trigger_source"] = d["trigger_source"]
    cfg["trigger_edge"] = d["trigger_edge"]

    # ---- 自动化 Tab 控件值覆盖 ----
    cfg.update(auto_tab.collect())

    # 后端回退：未显式选 mock 时，地址为空说明无硬件，由 backends 工厂自动
    # 返回 FixedTemperature / NullLaser，无需在此特殊处理。
    return cfg
