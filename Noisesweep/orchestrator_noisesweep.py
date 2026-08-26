#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""noisesweep — 变温 × 谐振器 × 激光功率 KID 自动化噪声测量编排器。

把 YBCO_TA 的 LakeShore 335 温控 + N7779C 激光 + 谐振追踪表，与本工程的
E8257D + PXIe-4480 S21/噪声核心串成三层自动扫描：
    温度（外层）→ 谐振器 → 激光功率（最内层）
每个 (T, res, power)：粗扫 250 MHz + SCRAPS 拟合取 f0 → 精扫（温度相关
15–25 MHz）+ 拟合 → 追加噪声测量。

用法：
    python noisesweep.py --config noisesweep_config.json            # 正式跑
    python noisesweep.py --config noisesweep_config.json --dry-run  # mock 自测

本模块禁止 import kid_measurement_gui_v3.py（会连带 PyQt5）。
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

import kid_measurement_core as core
import resonance_table as rt
import chip_library as cl
from backends import (
    make_daq_factory,
    make_laser_backend,
    make_source,
    make_temperature_backend,
    wait_for_stability,
)

SCRIPT_DIR = Path(__file__).resolve().parent

LOG = logging.getLogger("noisesweep")


# =========================================================================
# 参数双轨：config 权威 + 可选继承 UI
# =========================================================================

# 内置默认（config 为 null 且不继承 UI 时使用）
DEFAULTS = {
    "temperature_list_k": [4.0, 20.0, 40.0, 77.0],
    "fixed_temperature_k": 77.0,          # 无温控（lakeshore_visa_address 为空）时的假设温度
    "stability_tolerance_k": 0.05,
    "stability_hold_s": 60.0,
    "stability_poll_s": 5.0,
    "stability_max_wait_s": 1800.0,
    "abort_on_unstable": False,
    "temperature_mismatch_limit_k": 1.0,  # 已连接 LakeShore 时 |目标-实测| 超过该值 → 弹窗中止
    "lakeshore_channel": "A",
    "laser_wavelength_nm": 1550.0,
    "laser_power_mw": [0.0, 1.0, 3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0, 17.0],
    "laser_settle_s": 2.0,
    # coarse_bandwidth_hz 已废弃 —— 宽扫带宽改由 wide_bandwidth_* 三态控制
    "wide_bandwidth_mode": "fixed",           # fixed / temperature / formula
    "wide_bandwidth_mhz": 150.0,              # fixed 模式的固定带宽
    "wide_bandwidth_mhz_by_temperature": [[4.0, 50.0], [77.0, 200.0]],
    "wide_bandwidth_interpolation": "linear", # temperature 模式的插值方式
    "wide_bandwidth_formula": None,           # formula 模式的表达式（T 单位 K）
    "fine_bandwidth_mhz_by_temperature": [[4.0, 15.0], [20.0, 20.0],
                                          [40.0, 20.0], [77.0, 25.0]],
    "fine_bandwidth_interpolation": "step",
    "predict_laser_shift": True,
    "use_chip_library": False,                # 勾选后屏蔽手动/追踪表，失败自动回退
    "chip_id": None,
    "chip_run_id": None,
    "data_process_dir": str(cl.DEFAULT_DATA_PROCESS_DIR),
    # 3.0 整合：Auto_Sweep 与 Data_process 均为 Noisesweep 的兄弟目录。
    # config 里这些键为 null 时回落到此相对默认（按 __file__ 解析，与 cwd 无关）。
    "autosweep_dir": str(SCRIPT_DIR.parent / "Auto_Sweep"),
    "app_settings_file": str(SCRIPT_DIR.parent / "Auto_Sweep" / "app_settings.json"),
    "tracking_file": str(SCRIPT_DIR.parent / "Data_process" / "resonance_table.txt"),
    "save_figures": True,                     # 精扫完成后存 GUI 拟合图到 pic/
    "save_root": str(SCRIPT_DIR / "data" / "S21"),
    "experiment_name": "",
    "s21_points": 101,
    "samples_per_point": 1000,
    "settle_s": 0.010,
    "readout_power_dbm": -30.0,
    "scraps_readout_power_dbm": -60.0,
    "noise_frequency_mode": "F0_PLUS_DF",
    "noise_duration_s": 10.0,
    "noise_block_samples": 10000,
    "welch_window": "hamming",
    "welch_segment_seconds": 1.0,
    "sample_rate": 1_000_000.0,
    "voltage_range": 10.0,
    "coupling": "DC",
    "trigger_mode": "IMMEDIATE",
    "trigger_source": None,
    "trigger_edge": "RISING",
    "i_channel": 0,
    "q_channel": 1,
    "channels": [0, 1],
    "pxie_device_name": "PXI2Slot2",
    "e8257d_visa_address": "",
    # 温控/激光默认无地址：config 不填时由 backends 工厂返回 FixedTemperature/NullLaser（可选硬件）
    "lakeshore_visa_address": "",
    "laser_visa_address": "",
    "lakeshore_heater_range": None,
    "lakeshore_pid": None,
}

# (config_key, app_settings 路径, kid_gui 键, 可选变换函数)
# app_settings 路径为嵌套键元组；kid_gui 键为点式字符串（与 save_gui_settings 一致）。
UI_SOURCES = [
    ("laser_power_mw", ("laser", "power_sequence_mw"), None, None),
    ("laser_wavelength_nm", ("laser", "wavelength_nm"), None, None),
    ("lakeshore_visa_address", ("addresses", "lakeshore"), None, None),
    ("laser_visa_address", ("addresses", "laser"), None, None),
    ("stability_max_wait_s", ("temperature_sweep", "max_wait_min"), None,
     lambda v: float(v) * 60.0),   # app_settings 用分钟
    ("pxie_device_name", None, "daq.device_name", None),
    ("sample_rate", None, "daq.sample_rate", None),
    ("voltage_range", None, "daq.voltage_range", None),
    ("coupling", None, "daq.coupling", None),
    ("trigger_mode", None, "daq.trigger_mode", None),
    ("trigger_source", None, "daq.trigger_source", None),
    ("trigger_edge", None, "daq.trigger_edge", None),
    ("channels", None, "daq.channels", None),
    ("i_channel", None, "s21.i_channel", None),
    ("q_channel", None, "s21.q_channel", None),
    ("s21_points", None, "s21.points", None),
    ("samples_per_point", None, "s21.samples", None),
    ("settle_s", None, "s21.settle_s", None),
    ("readout_power_dbm", None, "s21.power_dbm", None),
    ("iq_calibration_file", None, "s21.calibration_file", None),
    ("noise_duration_s", None, "noise.duration_s", None),
    ("noise_block_samples", None, "noise.block", None),
    ("welch_window", None, "noise.psd_window", None),
    ("welch_segment_seconds", None, "noise.segment_seconds", None),
]


def _load_json_optional(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _lookup_app(app, path):
    node = app
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _lookup_gui(gui, dotted_key):
    if not isinstance(gui, dict):
        return None
    return gui.get(dotted_key)


def _ui_values(inherit: bool, app: dict, gui: dict) -> dict:
    """从 UI 参数文件收集可继承的值（inherit=false 返回空 dict）。"""
    ui_map = {}
    if not inherit:
        return ui_map
    for key, app_path, gui_key, transform in UI_SOURCES:
        found = None
        if app_path is not None:
            found = _lookup_app(app, app_path)
        elif gui_key is not None:
            found = _lookup_gui(gui, gui_key)
        if found is not None:
            if transform is not None:
                try:
                    found = transform(found)
                except (TypeError, ValueError):
                    continue
            ui_map[key] = found
    return ui_map


def resolve_config(raw, app_settings_file=None, kid_gui_file=None,
                   inherit: bool = False) -> dict:
    """双轨解析：config 非 null 永远赢；null 且 inherit → 从 UI 文件继承；
    否则用内置默认。返回合并后的完整 config。"""
    app = _load_json_optional(app_settings_file)
    gui = _load_json_optional(kid_gui_file)
    cfg = dict(raw)
    ui_map = _ui_values(inherit, app, gui)

    for key, default in DEFAULTS.items():
        if key in cfg and cfg[key] is not None and cfg[key] != "":
            continue          # config 非 null → 保留
        cfg[key] = ui_map.get(key, default)   # null → UI 值或内置默认
    return cfg


def log_param_sources(cfg, raw, app_settings_file, kid_gui_file, inherit, log):
    """INFO 级逐条打印每个参数的最终值与来源。"""
    app = _load_json_optional(app_settings_file)
    gui = _load_json_optional(kid_gui_file)
    ui_map = _ui_values(inherit, app, gui)
    for key, value in sorted(cfg.items()):
        if key in ("fine_bandwidth_mhz_by_temperature", "resonators"):
            continue
        if key in raw and raw[key] is not None and raw[key] != "":
            source = "config"
        elif key in ui_map:
            source = "ui"
        elif key in DEFAULTS:
            source = "default"
        else:
            continue
        log.info("  %-28s = %-40s [%s]", key, _fmt(value), source)


def _fmt(value):
    if isinstance(value, list):
        return "[{}]".format(", ".join(_fmt(v) for v in value))
    if isinstance(value, float):
        return "{:g}".format(value)
    return str(value)


# =========================================================================
# 辅助：精扫带宽映射
# =========================================================================

def fine_bandwidth_hz(T_k: float, rows, mode: str = "step") -> float:
    """温度 → 精扫带宽（Hz）。rows = [[T_MHz, bw_MHz], ...]。

    step 语义：分段常数保持（取最后一个 T<=target 的行；低于首行取首行）。
    linear 语义：np.interp 两端截断。
    """
    rows = sorted((float(t), float(bw)) for t, bw in rows)
    if not rows:
        raise ValueError("fine_bandwidth_mhz_by_temperature 为空")
    if mode == "linear":
        temps = [r[0] for r in rows]
        bws = [r[1] for r in rows]
        return float(np.interp(float(T_k), temps, bws)) * 1e6
    # step
    value = rows[0][1]
    for t, bw in rows:
        if float(T_k) >= t:
            value = bw
        else:
            break
    return value * 1e6


# =========================================================================
# 辅助：宽扫带宽（固定 / 变温 / 自定义公式）
# =========================================================================

def _eval_bandwidth_formula(formula, T_k):
    """受限命名空间求值带宽公式（单位 MHz）。T 为温度 K。"""
    ns = {"T": float(T_k), "np": np}
    for name in ("sin", "cos", "tan", "exp", "log", "sqrt", "floor", "ceil",
                 "clip", "abs", "min", "max"):
        ns[name] = getattr(np, name)
    return float(eval(str(formula), {"__builtins__": {}}, ns)) * 1e6


def wide_bandwidth_hz(T_k, config) -> float:
    """温度 → 宽扫带宽（Hz）。三种模式：
      * "fixed"       → 固定 wide_bandwidth_mhz（默认 150 MHz）
      * "temperature" → 按 wide_bandwidth_mhz_by_temperature 插值（默认线性）
      * "formula"     → 自定义表达式 wide_bandwidth_formula（T 单位 K，结果 MHz）
    """
    mode = str(config.get("wide_bandwidth_mode", "fixed")).lower()
    if mode == "fixed":
        return float(config.get("wide_bandwidth_mhz", 150.0)) * 1e6
    if mode == "temperature":
        rows = config.get("wide_bandwidth_mhz_by_temperature") or \
            [[4.0, 50.0], [77.0, 200.0]]
        return fine_bandwidth_hz(
            T_k, rows, str(config.get("wide_bandwidth_interpolation", "linear")))
    if mode == "formula":
        formula = config.get("wide_bandwidth_formula")
        if not formula:
            raise ValueError("wide_bandwidth_mode=formula 但未提供 wide_bandwidth_formula")
        return _eval_bandwidth_formula(formula, T_k)
    raise ValueError("wide_bandwidth_mode 必须是 fixed/temperature/formula，得到 {!r}"
                     .format(mode))


# =========================================================================
# Checkpoint（断点续跑，原子写）
# =========================================================================

class Checkpoint:
    """单 JSON 记录每个 (T, res, power) 的完成状态。"""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"temperatures": []}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                LOG.warning("checkpoint 损坏，按空处理: %s", self.path)
                self.data = {"temperatures": []}

    # ---- 读取（无副作用） ----

    def is_complete(self, target_k, res_name, power_mw) -> bool:
        for t in self.data.get("temperatures", []):
            if abs(float(t.get("target_k", -1)) - float(target_k)) < 1e-9:
                for r in t.get("resonators", []):
                    if r.get("name") == res_name:
                        for p in r.get("powers", []):
                            if abs(float(p.get("mw", -1)) - float(power_mw)) < 1e-9:
                                return bool(p.get("completed"))
        return False

    # ---- 写入（创建缺失节点） ----

    def _temp(self, target_k):
        for t in self.data["temperatures"]:
            if abs(float(t["target_k"]) - float(target_k)) < 1e-9:
                return t
        t = {"target_k": float(target_k), "actual_k": None,
             "stable": None, "resonators": []}
        self.data["temperatures"].append(t)
        return t

    def _res(self, temp, name):
        for r in temp["resonators"]:
            if r["name"] == name:
                return r
        r = {"name": name, "powers": []}
        temp["resonators"].append(r)
        return r

    def _power(self, res, mw):
        for p in res["powers"]:
            if abs(float(p["mw"]) - float(mw)) < 1e-9:
                return p
        p = {"mw": float(mw), "completed": False}
        res["powers"].append(p)
        return p

    def mark_temperature(self, target_k, actual_k, stable):
        self._temp(target_k).update({"actual_k": actual_k, "stable": bool(stable)})
        self.save()

    def mark_complete(self, target_k, res_name, power_mw,
                      fine_center_hz=None, fine_file=None):
        p = self._power(self._res(self._temp(target_k), res_name), power_mw)
        p.update({
            "completed": True,
            "fine_center_hz": fine_center_hz,
            "fine_file": str(fine_file) if fine_file else None,
        })
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)


# =========================================================================
# 数据产物辅助
# =========================================================================

def fmt_K(T: float) -> str:
    return "{:g}K".format(T)


def fmt_mW(p: float) -> str:
    return "{:g}mW".format(p)


def _fine_has_noise(path) -> bool:
    import h5py
    try:
        with h5py.File(path, "r") as h:
            return bool(h.get("noise_measurements"))
    except Exception:
        return False


def _point_done_offline(point_dir, skip_noise) -> bool:
    """文件级完成判定：point_dir=<实测T>K/<res>/<power>mW 下直接有 coarse+fine
    且（跳过噪声或 fine 已有噪声组）。实际温度子文件夹已取消，文件直接落
    point_dir，故按扁平 glob 查找。"""
    if not point_dir.exists():
        return False
    coarse = list(point_dir.glob("coarse_s21.h5"))
    fine = list(point_dir.glob("fine_s21.h5"))
    if not (coarse and fine):
        return False
    if skip_noise:
        return True
    return any(_fine_has_noise(f) for f in fine)


def _save_figures(fine_path, pic_dir, log):
    """精扫完成后把 GUI 显示的拟合图像保存到 pic/（复用 plot_S21_hdf5）。

    必须在 import plot_S21_hdf5 之前设置 Agg backend（该模块顶部 import
    pyplot，晚设置则 backend 已初始化、Agg 失效）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import plot_S21_hdf5
    pic_dir = Path(pic_dir)
    pic_dir.mkdir(parents=True, exist_ok=True)
    try:
        plot_S21_hdf5.plot_s21_hdf5(
            str(fine_path), show=False, save_path=str(pic_dir / "s21.png"))
        log.info("    图像已保存: %s", pic_dir)
    except Exception as exc:
        log.warning("    保存图像失败（不影响测量）: %s", exc)


def _pic_name(meta):
    """从上下文生成信息文件名：res5_P0mW_T9.6K_Ta9.58K_YBCO1145_noise.png"""
    res = str(meta.get("res_name", "res?"))
    p = float(meta.get("power_mw", 0.0))
    tk = float(meta.get("target_k", 0.0))
    ak = float(meta.get("actual_k", tk))
    chip = str(meta.get("chip_id", "")).replace("#", "-") or "n-a"
    typ = str(meta.get("type", "fig"))
    return "{}_P{:g}mW_T{:g}K_Ta{:g}K_{}_{}.png".format(res, p, tk, ak, chip, typ)


def _pic_annotation(meta):
    """图上正上方标注：T_target / T_actual / chip / 功率 / 谐振 / 类型 / 频点模式。"""
    tk = float(meta.get("target_k", 0.0))
    ak = float(meta.get("actual_k", tk))
    chip = str(meta.get("chip_id", "")) or "n/a"
    res = str(meta.get("res_name", "?"))
    p = float(meta.get("power_mw", 0.0))
    typ = str(meta.get("type", "fig"))
    s = "T_target={:.2f} K  T_actual={:.2f} K  chip={}  P={:g} mW  res={}  type={}" \
        .format(tk, ak, chip, p, res, typ)
    mode = str(meta.get("mode", ""))
    if mode:
        s += "  mode={}".format(mode)
    return s


def _save_noise_figures(fine_path, meta, log):
    """headless 复写噪声四块图（CLI/验证用，自动化 GUI 走面板画布）。

    读 fine_s21.h5 的每个 /noise_measurements/* 组，画与 GUI 噪声面板一致的四块布局
    （幅度时域 / Welch PSD / 相位时域 / IQ 圆+测试点），写入
    `<save_root>/<目标T>K/PIC/noise/{_pic_name}` 并加 suptitle 标注。
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import h5py
    if not meta.get("save_root"):
        return
    with h5py.File(fine_path, "r") as h:
        if "noise_measurements" not in h:
            return
        sf = h["scraps_fit"]
        s21_i = np.asarray(sf["INorm"][:], dtype=float)
        s21_q = np.asarray(sf["QNorm"][:], dtype=float)
        for gname, g in h["noise_measurements"].items():
            mode = str(g.attrs.get("frequency_selection_mode", "UNKNOWN"))
            m = dict(meta)
            m["group"] = "noise"
            m["mode"] = mode
            m["type"] = ("noise" if mode.upper() == "F0_PLUS_DF"
                         else "noise_{}".format(mode.upper()))
            pic_dir = Path(m["save_root"]) / fmt_K(float(m["target_k"])) / "PIC" / "noise"
            pic_dir.mkdir(parents=True, exist_ok=True)
            name = _pic_name(m)
            freq = np.asarray(g["noise_spectrum_frequency_hz"][:], dtype=float)
            amp_psd = np.asarray(g["amplitude_psd_per_hz"][:], dtype=float)
            ph_psd = np.asarray(g["phase_psd_rad2_per_hz"][:], dtype=float)
            seg = np.asarray(g["noise_amplitude"][:], dtype=float)
            phs = np.asarray(g["noise_phase_rad"][:], dtype=float)
            tt = np.asarray(g["time_s"][:], dtype=float)
            step = max(1, seg.size // 20000)
            n_iq = np.asarray(g["normalized_noise_iq"][:], dtype=float)
            ref_i = int(g.attrs.get("s21_reference_index", 0))
            test_point = (s21_i[ref_i], s21_q[ref_i])
            count = min(10000, n_iq.shape[1])
            pstep = max(1, count // 20000)
            win = str(g.attrs.get("welch_window", "??"))
            nperseg = int(round(float(g.attrs.get("welch_segment_seconds", 1.0))
                                * float(g.attrs.get("actual_sample_rate_hz", 1.0))))
            fig = Figure(figsize=(12, 7))
            FigureCanvasAgg(fig)
            axes = fig.subplots(2, 2)
            axes[0, 0].plot(tt[::step], seg[::step], linewidth=.8)
            axes[0, 0].set_xlabel("Time (s)"); axes[0, 0].set_ylabel("Amplitude")
            axes[0, 0].grid(True, alpha=.3)
            if freq.size > 1:
                axes[0, 1].loglog(freq[1:], amp_psd[1:], linewidth=.8, label="Amplitude PSD")
                axes[0, 1].loglog(freq[1:], ph_psd[1:], linewidth=.8, label="Phase PSD")
            axes[0, 1].set_xlabel("Frequency (Hz)")
            axes[0, 1].set_ylabel("PSD (1/Hz or rad²/Hz)")
            axes[0, 1].set_title("Welch PSD: window={}, nperseg={}".format(win, nperseg))
            axes[0, 1].grid(True, which="both", alpha=.3); axes[0, 1].legend()
            axes[1, 0].plot(tt[::step], phs[::step], linewidth=.8)
            axes[1, 0].set_xlabel("Time (s)"); axes[1, 0].set_ylabel("Unwrapped phase (rad)")
            axes[1, 0].grid(True, alpha=.3)
            axes[1, 1].plot(s21_i, s21_q, ".-", markersize=3, linewidth=.8,
                            label="S21 INorm/QNorm")
            axes[1, 1].plot([test_point[0]], [test_point[1]], "o", markersize=8,
                            label="noise test point")
            axes[1, 1].plot(n_iq[0, -count::pstep], n_iq[1, -count::pstep], ".",
                            markersize=2, label="noise IQ Norm")
            axes[1, 1].set_xlabel("I Norm"); axes[1, 1].set_ylabel("Q Norm")
            axes[1, 1].set_title("S21 curve and normalized noise IQ (last {} points)".format(count))
            axes[1, 1].axis("equal"); axes[1, 1].grid(True, alpha=.3); axes[1, 1].legend()
            fig.suptitle(_pic_annotation(m), fontsize=10)
            fig.tight_layout(rect=[0, 0, 1, 0.94])
            fig.savefig(str(pic_dir / name), dpi=150)
            log.info("    噪声图已保存: %s", pic_dir / name)


# =========================================================================
# 三层编排
# =========================================================================

class AbortRun(Exception):
    pass


class TemperatureMismatch(AbortRun):
    """目标温度与 LakeShore 实测温度相差超过 temperature_mismatch_limit_k。

    由 resolve_temperature 抛出：GUI 经 worker.failed → QMessageBox 弹窗中止；
    CLI 进入 run() 的 except TemperatureMismatch 分支并走异常清理。
    """

    def __init__(self, target_k, actual_k, limit_k):
        super().__init__(
            "目标 {:.2f} K 与实测 {:.4f} K 相差超过 {:.1f} K，中止测量"
            .format(target_k, actual_k, limit_k)
        )


def _temperature_mismatch_limit(config) -> float:
    return float(config.get("temperature_mismatch_limit_k", 1.0))


def _check_temperature_mismatch(T, actual_T, config):
    limit = _temperature_mismatch_limit(config)
    if abs(float(T) - float(actual_T)) > limit:
        raise TemperatureMismatch(float(T), float(actual_T), limit)


def resolve_temperature(T, config, temp, log):
    """解析温控目标/实测（automation_worker 与 CLI run 共用）。

    规则（用户约定）：
      * 未连接 LakeShore（temp.is_fixed，含 mock）→ 手动输入温度同时作为
        目标与实测，返回 (T, T, stable=True)。
      * 已连接：
        - drive_temperature=True → set_temperature + wait_for_stability，
          返回 (T, actual_T, stable)。
        - 否则直接读实测 actual_T。
        已连接分支统一校验 |T - actual_T| > temperature_mismatch_limit_k
        即抛 TemperatureMismatch（GUI 弹窗 + 中止；CLI 进入 AbortRun 清理）。

    Returns:
        (T_target, actual_T, stable)
    """
    T = float(T)
    if getattr(temp, "is_fixed", False):
        log.info("无温控（LakeShore 未连接，含 mock），手动温度 %.2f K 同时作为目标/实测", T)
        return T, T, True

    if config.get("drive_temperature"):
        temp.set_temperature(T)
        actual_T, stable = wait_for_stability(
            temp, T, config["stability_tolerance_k"],
            config["stability_hold_s"], config["stability_poll_s"],
            config["stability_max_wait_s"], config["lakeshore_channel"], log)
        if not stable:
            log.warning("T=%.2f K 未稳定（实际 %.4f K）", T, actual_T)
            if config.get("abort_on_unstable"):
                raise RuntimeError("温度不稳定，按配置中止")
        _check_temperature_mismatch(T, actual_T, config)
        return T, actual_T, stable

    # 已连接、不驱动温控 → 读实测，过 1K 守卫
    actual_T = float(temp.get_temperature(config["lakeshore_channel"]))
    log.info("LakeShore 已连接：目标 %.2f K，实测 %.4f K", T, actual_T)
    _check_temperature_mismatch(T, actual_T, config)
    return T, actual_T, True


def _fallback_reference(config, table, res, res_idx, actual_T, power_mw, log):
    """非芯片标定库路径的参考频率：每谐振手动 > 全局手动 > 追踪表插值，
    再叠激光频移预测。"""
    f_ref = rt.resolve_reference_frequency(
        res.get("reference_frequency_hz"),
        config.get("manual_reference_frequency_hz"),
        table.reference_frequency_hz(actual_T, res_idx),
    )
    if config["predict_laser_shift"]:
        resp = table.responsivity_ppm_per_mw(actual_T, res_idx)
        f_pred = rt.predict_laser_shift(f_ref, resp, power_mw)
        if resp is not None and f_pred != f_ref:
            log.info("    激光频移预测: %.6f → %.6f GHz (%.0f ppm/mW × %s mW)",
                     f_ref / 1e9, f_pred / 1e9, resp, power_mw)
        f_ref = f_pred
    return f_ref


def _resolve_reference(config, table, res, res_idx, actual_T, power_mw, log):
    """频率来源三选一 + 兜底：芯片标定库（勾选时屏蔽手动/追踪表）> 手动/追踪表。

    Returns:
        (f_ref_hz, pred_half_hz)。pred_half 仅当芯片标定库预测成功时非 None
        （预测窗口半宽，作宽扫带宽下界）。标定缺失/失败时自动回退并告警。
    """
    pred_half = None
    if config.get("use_chip_library"):
        f_pred, half = cl.predict_resonance_frequencies(
            chip_id=config.get("chip_id"),
            run_id=config.get("chip_run_id"),
            T_k=actual_T, laser_mw=power_mw,
            data_process_dir=config.get("data_process_dir"),
        )
        if f_pred is not None and res_idx < len(f_pred):
            f_ref = float(f_pred[res_idx])
            pred_half = float(half[res_idx])
            log.info("    芯片标定库预测 f0 = %.6f GHz (窗口半宽 ±%.1f MHz)",
                     f_ref / 1e9, pred_half / 1e6)
            return f_ref, pred_half
        log.warning("    芯片标定库不可用/谐振器数不符（%s），回退追踪表/手动",
                    cl.import_error() or "标定缺失")
    f_ref = _fallback_reference(config, table, res, res_idx, actual_T,
                                power_mw, log)
    return f_ref, pred_half


def run_one_point(config, measure, source, laser, table, checkpoint, stop_event,
                  T, actual_T, res, res_idx, power_mw, save_root,
                  force, skip_noise, mock_instruments, log,
                  f_ref_override=None):
    """给定已稳定温度 + 频率来源，完成一个 (T, res, power) 点的测量。

    边界写死：本函数**不含设温**（设温在 temp sweep 层，设一次、内层共享）。
    本函数做：设激光 → 频率预测 → 宽扫 → 精扫 → 存图 → 噪声 → mark_complete。
    返回 True 表示实际测量；False 表示因幂等跳过。

    measure 是测量引擎（s21/noise 方法）：自动化 GUI 传 PanelMeasure（面板
    MeasurementWorker 代码，core 禁用），CLI 传 CoreMeasure（core.run_*）。
    f_ref_override 非 None 时跳过频率来源三选一，直接以给定频率作宽扫中心
    （scan 命令用手动中心频率）；None 走 _resolve_reference。
    """
    res_name = res["name"]
    log.info("  [res=%s, power=%s mW]", res_name, power_mw)

    point_dir = save_root / fmt_K(actual_T) / res_name / fmt_mW(power_mw)
    if not force and checkpoint.is_complete(T, res_name, power_mw):
        log.info("    checkpoint 已完成，跳过")
        return False
    if not force and _point_done_offline(point_dir, skip_noise):
        log.info("    文件已存在（coarse+fine），标记完成并跳过")
        checkpoint.mark_complete(T, res_name, power_mw)
        return False

    laser.set_power(power_mw)
    time.sleep(config["laser_settle_s"])

    if f_ref_override is not None:
        f_ref, pred_half = float(f_ref_override), None
    else:
        f_ref, pred_half = _resolve_reference(config, table, res, res_idx,
                                              actual_T, power_mw, log)
    wide_bw = wide_bandwidth_hz(actual_T, config)
    if pred_half is not None:
        wide_bw = max(wide_bw, 2.0 * pred_half)   # 预测窗口作带宽下界
    fine_bw = fine_bandwidth_hz(
        actual_T, config["fine_bandwidth_mhz_by_temperature"],
        config["fine_bandwidth_interpolation"],
    )

    res_dir = point_dir
    res_dir.mkdir(parents=True, exist_ok=True)

    extra_attrs = {
        "temperature_k": float(actual_T),
        "temperature_target_k": float(T),
        "laser_power_mw": float(power_mw),
        "laser_wavelength_nm": float(config["laser_wavelength_nm"]),
    }

    # 阶段 1：宽扫 + 拟合 → f0
    coarse_path = res_dir / "coarse_s21.h5"
    if mock_instruments is not None:
        mock_instruments.set_resonance_position(f_ref)
    log.info("    宽扫: center %.6f GHz, bw %.0f MHz → %s",
             f_ref / 1e9, wide_bw / 1e6, coarse_path)
    r_coarse = measure.s21(
        config, f_ref, wide_bw, res_name, actual_T, extra_attrs, coarse_path)
    if r_coarse.fit_ok:
        log.info("    宽扫 f0 = %.6f GHz (df %.3f kHz)",
                 r_coarse.resonance_frequency_hz / 1e9,
                 (r_coarse.resonance_frequency_hz - f_ref) / 1e3)
    else:
        log.warning("    宽扫拟合失败: %s；用参考频率对中", r_coarse.fit_error)
    center_fine = r_coarse.resonance_frequency_hz or f_ref

    # 阶段 2：精扫 + 拟合
    fine_path = res_dir / "fine_s21.h5"
    if mock_instruments is not None:
        mock_instruments.set_resonance_position(center_fine)
    log.info("    精扫: center %.6f GHz, bw %.1f MHz → %s",
             center_fine / 1e9, fine_bw / 1e6, fine_path)
    r_fine = measure.s21(
        config, center_fine, fine_bw, res_name, actual_T, extra_attrs, fine_path)
    if r_fine.fit_ok:
        log.info("    精扫 f0 = %.6f GHz", r_fine.resonance_frequency_hz / 1e9)
    else:
        log.warning("    精扫拟合失败: %s", r_fine.fit_error)

    # 存图（精扫完成后；噪声追加不改 S21/拟合数据，故前后皆可）
    if config.get("save_figures", True):
        _save_figures(fine_path, res_dir / "pic", log)

    # 图片上下文（文件名与 suptitle 标注用）：目标温度/实际温度/芯片/功率/谐振
    meta_base = {
        "save_root": str(save_root),
        "target_k": T,
        "actual_k": actual_T,
        "chip_id": str(config.get("chip_id", "")),
        "res_name": res_name,
        "res_index": res_idx,
        "power_mw": power_mw,
    }

    # 阶段 3：噪声（面板所选频点，与面板3 一致；每点只测一次）
    if not skip_noise:
        log.info("    噪声: mode=%s, %.1f s → %s",
                 config["noise_frequency_mode"], config["noise_duration_s"],
                 fine_path)
        measure.noise(config, config["noise_frequency_mode"], fine_path, meta_base)
    # headless 复写（CLI 用）：自动化 GUI 走面板画布（save_figures 被强制 False）。
    if not skip_noise and config.get("save_figures", True):
        _save_noise_figures(fine_path, meta_base, log)

    checkpoint.mark_complete(T, res_name, power_mw, center_fine, fine_path)
    log.info("    [完成] (T=%.1f K, %s, %s mW)", T, res_name, power_mw)
    return True


def _build_env(config, log, need_thermal_laser=True):
    """构建测量环境（信号源/温控/激光/DAQ 上下文），供 run 与各硬件子命令复用。

    need_thermal_laser=False 时不连温控/激光（temp/laser 返回 None），供
    仅需 S21/噪声的命令（如 noise）使用。

    Returns:
        (source, temp, laser, ctx, mock_instruments, stop_event)
    """
    kind = config["backend"]
    cfg = dict(config)
    # 核心 MeasurementContext.daq_config 用 device_name；config 模板里叫 pxie_device_name
    cfg["device_name"] = cfg.get("pxie_device_name")
    stop_event = threading.Event()
    mock_instruments = __import__("mock_instruments") if kind == "mock" else None

    source = make_source(kind, cfg, log)
    temp = make_temperature_backend(kind, cfg, log) if need_thermal_laser else None
    laser = make_laser_backend(kind, cfg, log) if need_thermal_laser else None
    make_daq = make_daq_factory(kind, cfg, source, log)

    ctx = core.MeasurementContext(
        source=source, source_lock=threading.RLock(),
        daq_config=cfg, daq_lock=threading.RLock(),
        make_daq=make_daq,
        on_file_created=lambda p: log.info("  文件已创建: %s", p),
        should_stop=lambda: stop_event.is_set(),
    )
    return source, temp, laser, ctx, mock_instruments, stop_event


def run(config, args):
    log = LOG
    source, temp, laser, ctx, mock_instruments, stop_event = _build_env(config, log)

    def _cleanup(abnormal):
        for name, fn in (("激光", laser.output_off), ("信号源", source.rf_off)):
            try:
                fn()
            except Exception as exc:
                log.error("清理 %s 失败: %s", name, exc)
        if abnormal:
            try:
                temp.all_heaters_off()
                log.warning("异常中止 → 加热器已全部关闭")
            except Exception as exc:
                log.error("关闭加热器失败: %s", exc)
        for name, fn in (("激光", laser.close), ("信号源", source.disconnect),
                         ("温控", temp.close)):
            try:
                fn()
            except Exception as exc:
                log.error("关闭 %s 失败: %s", name, exc)

    def _sig(signum, _frame):
        log.warning("收到信号 %s，请求停止…", signum)
        stop_event.set()
        if signum == signal.SIGINT:
            raise KeyboardInterrupt   # 中断长阻塞（如等温），尽快进入清理

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    abnormal = False
    try:
        source.rf_off()
        laser.set_wavelength(config["laser_wavelength_nm"])

        # ---- 谐振追踪表 ----
        table = rt.load_resonance_table(config["tracking_file"])
        log.info("谐振追踪表: %s (%s)", config["tracking_file"], table)

        # ---- 数据根目录 / checkpoint ----
        save_root = Path(config["save_root"]) / config["experiment_name"]
        save_root.mkdir(parents=True, exist_ok=True)
        checkpoint = Checkpoint(config["checkpoint_path"]
                                or save_root / "checkpoint.json")
        log.info("数据根目录: %s", save_root)
        log.info("checkpoint: %s", checkpoint.path)

        # ---- 循环 ----
        temps = [float(t) for t in config["temperature_list_k"]]
        powers = [float(p) for p in config["laser_power_mw"]]
        if args.only_temps:
            temps = [t for t in temps if t in args.only_temps]
        if args.only_powers:
            powers = [p for p in powers if p in args.only_powers]
        resonators = list(config["resonators"])
        if args.only_res:
            names = args.only_res
            resonators = [r for r in resonators if r["name"] in names]

        fixed_k = float(config.get("fixed_temperature_k", 77.0))
        if getattr(temp, "is_fixed", False):
            if len(temps) > 1:
                log.warning("无温控（FixedTemperature）：temperature_list_k 多于 1 个温度，"
                            "强制单点 %.2f K", fixed_k)
            temps = [fixed_k]

        log.info("计划: %d 温度 × %d 谐振器 × %d 功率 = %d 点",
                 len(temps), len(resonators), len(powers),
                 len(temps) * len(resonators) * len(powers))

        for T in temps:
            if stop_event.is_set():
                break
            log.info("=== 温度 %.2f K ===", T)
            _T, actual_T, stable = resolve_temperature(T, config, temp, log)
            checkpoint.mark_temperature(T, actual_T, stable)
            if not stable:
                log.error("T=%.2f K 未在 %s s 内稳定（实际 %.4f K）",
                          T, config["stability_max_wait_s"], actual_T)
                if config["abort_on_unstable"]:
                    raise AbortRun("温度不稳定，按配置中止")
                log.warning("按配置继续（abort_on_unstable=false）")
            else:
                log.info("T=%.2f K 稳定，实际 %.4f K", T, actual_T)

            for res in resonators:
                res_name = res["name"]
                res_idx = table.resonator_index(res_name)
                for power_mw in powers:
                    if stop_event.is_set():
                        break
                    run_one_point(config, CoreMeasure(ctx), source, laser,
                                  table, checkpoint, stop_event, T, actual_T,
                                  res, res_idx, power_mw, save_root, args.force,
                                  args.skip_noise, mock_instruments, log)

            laser.set_power(0)   # 该温度点结束 → 关激光
            log.info("=== 温度 %.2f K 完成，激光关闭 ===", T)

    except KeyboardInterrupt:
        abnormal = True
        log.error("用户中断（Ctrl+C）")
    except TemperatureMismatch as exc:
        abnormal = True
        log.error("运行中止: %s", exc)
    except AbortRun:
        abnormal = True
        log.error("运行中止: 温度不稳定")
    except Exception:
        abnormal = True
        log.exception("运行异常中止")
    finally:
        abnormal = abnormal or stop_event.is_set()   # 收到停止信号也算异常中止
        _cleanup(abnormal)
        if abnormal:
            log.warning("本次运行异常中止（%s），加热器已关。可加 --resume 续跑未完成项。",
                        "signal" if stop_event.is_set() else "error")


def _s21_params(config, center_hz, bandwidth_hz, res_name, T_k, extra_attrs):
    return core.S21Params(
        start_hz=center_hz - bandwidth_hz / 2,
        stop_hz=center_hz + bandwidth_hz / 2,
        center_hz=center_hz,
        bandwidth_hz=bandwidth_hz,
        points=int(config["s21_points"]),
        samples=int(config["samples_per_point"]),
        power_dbm=float(config["readout_power_dbm"]),
        settle_s=float(config["settle_s"]),
        i_channel=int(config["i_channel"]),
        q_channel=int(config["q_channel"]),
        calibration_file=config["iq_calibration_file"],
        fit_enabled=True,
        resonator_name=res_name,
        temperature_k=float(T_k),
        readout_power_dbm=float(config["scraps_readout_power_dbm"]),
        extra_attrs=extra_attrs,
    )


def _noise_params(config, frequency_mode=None):
    """core.NoiseParams（CLI/mock 用）；值与面板噪声参数对齐（collect_config 已读面板控件）。"""
    return core.NoiseParams(
        s21_file="",   # run_noise 用第二个参数 s21_file
        frequency_mode=frequency_mode or config["noise_frequency_mode"],
        manual_frequency_hz=float(config.get("noise_manual_frequency_hz", 0.0)),
        power_dbm=float(config.get("noise_power_dbm",
                                   config.get("readout_power_dbm", -30.0))),
        settle_s=float(config.get("noise_settle_s", config.get("settle_s", 0.1))),
        continuous=bool(config.get("noise_continuous", False)),
        duration_s=float(config["noise_duration_s"]),
        block=max(int(config["noise_block_samples"]),
                  int(round(float(config["sample_rate"])))),
        i_channel=int(config["i_channel"]),
        q_channel=int(config["q_channel"]),
        calibration_file=config["iq_calibration_file"],
        window=config["welch_window"],
        segment_seconds=float(config["welch_segment_seconds"]),
    )


def _s21_panel_p(config, center_hz, bandwidth_hz, res_name, T_k, extra_attrs):
    """面板 MeasurementWorker 期望的 S21 参数字典（值来自 config=面板控件）。"""
    return {
        "start": center_hz - bandwidth_hz / 2,
        "stop": center_hz + bandwidth_hz / 2,
        "center": center_hz,
        "bandwidth": bandwidth_hz,
        "points": int(config["s21_points"]),
        "samples": int(config["samples_per_point"]),
        "power": float(config["readout_power_dbm"]),
        "settle": float(config["settle_s"]),
        "i": int(config["i_channel"]),
        "q": int(config["q_channel"]),
        "calibration_file": config["iq_calibration_file"],
        "fit_enabled": True,
        "resonator_name": res_name,
        "temperature_k": float(T_k),
        "readout_power_dbm": float(config["scraps_readout_power_dbm"]),
        "extra_attrs": dict(extra_attrs or {}),
    }


def _noise_panel_p(config, frequency_mode, s21_file):
    """面板 MeasurementWorker 期望的噪声参数字典（值来自 config=面板控件）。"""
    return {
        "frequency_mode": str(frequency_mode),
        "manual_frequency": float(config.get("noise_manual_frequency_hz", 0.0)),
        "power": float(config.get("noise_power_dbm",
                                  config.get("readout_power_dbm", -30.0))),
        "settle": float(config.get("noise_settle_s", config.get("settle_s", 0.1))),
        "continuous": bool(config.get("noise_continuous", False)),
        "duration": float(config["noise_duration_s"]),
        "block": max(int(config["noise_block_samples"]),
                     int(round(float(config["sample_rate"])))),
        "i": int(config["i_channel"]),
        "q": int(config["q_channel"]),
        "calibration_file": config["iq_calibration_file"],
        "s21_file": str(s21_file),
        "window": config["welch_window"],
        "segment_seconds": float(config["welch_segment_seconds"]),
    }


class CoreMeasure:
    """core.run_s21/run_noise 引擎（CLI 用）。自动化 GUI 走 PanelMeasure（面板代码）。"""

    def __init__(self, ctx):
        self._ctx = ctx

    def s21(self, config, center_hz, bandwidth_hz, res_name, T_k, extra_attrs, path):
        return core.run_s21(self._ctx, _s21_params(
            config, center_hz, bandwidth_hz, res_name, T_k, extra_attrs), path)

    def noise(self, config, frequency_mode, path, meta_base=None):
        return core.run_noise(self._ctx, _noise_params(config, frequency_mode), path)


# =========================================================================
# CLI
# =========================================================================

def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="noisesweep — 变温×谐振器×激光功率 KID 自动化测量（子命令式）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # ---- 全局参数（子命令前）----
    p.add_argument("--config", required=True, help="config JSON 路径")
    p.add_argument("--dry-run", action="store_true",
                   help="mock 后端空跑（不碰硬件）")
    p.add_argument("--log-file", type=str, default=None, help="日志文件路径")
    p.add_argument("--inherit-ui", dest="inherit_ui", action="store_true",
                   help="覆盖 config: null 字段从 UI 参数文件继承")
    p.add_argument("--no-inherit-ui", dest="inherit_ui", action="store_false",
                   help="覆盖 config: 不继承 UI 参数")
    p.set_defaults(inherit_ui=None)

    sub = p.add_subparsers(dest="command", required=True)

    # ---- run：全自动变温×谐振×功率 ----
    run_p = sub.add_parser("run", help="全自动变温×谐振器×功率扫描（原默认行为）")
    run_p.add_argument("--force", action="store_true",
                       help="忽略 checkpoint 与已有文件，全部重跑")
    run_p.add_argument("--resume", action="store_true",
                       help="从 checkpoint 续跑（默认即跳过已完成点）")
    run_p.add_argument("--only-temps", type=str, default=None,
                       help="只跑这些温度，逗号分隔，如 '4,20'")
    run_p.add_argument("--only-res", type=str, default=None,
                       help="只跑这些谐振器，逗号分隔，如 'res1,res3'")
    run_p.add_argument("--only-powers", type=str, default=None,
                       help="只跑这些功率，逗号分隔，如 '0,5'")
    run_p.add_argument("--skip-noise", action="store_true",
                       help="只做 S21 扫描不做噪声")

    # ---- scan：当前温度下 n 个中心频率 ----
    scan_p = sub.add_parser("scan", help="当前温度下对 n 个中心频率宽扫→精扫→噪声")
    scan_p.add_argument("freqs_ghz", nargs="+", type=float,
                        help="中心频率列表 (GHz)")
    scan_p.add_argument("--res", default=None,
                        help="谐振器名（目录层级，默认 config 第一个）")
    scan_p.add_argument("--power-mw", type=float, default=None,
                        help="激光功率 (mW)，默认 config 第一个")
    scan_p.add_argument("--target-k", type=float, default=None,
                        help="目录顶层目标温度 (K)，默认读当前温度")
    scan_p.add_argument("--skip-noise", action="store_true")
    scan_p.add_argument("--force", action="store_true")

    # ---- noise：对已有精扫文件补噪声 ----
    noise_p = sub.add_parser("noise", help="对已有 fine_s21.h5 追加噪声")
    noise_p.add_argument("s21_file", help="精扫 HDF5 路径")

    # ---- temp：读温 / 设点+扫描 ----
    temp_p = sub.add_parser("temp", help="温控")
    temp_sub = temp_p.add_subparsers(dest="temp_cmd", required=True)
    temp_sub.add_parser("read", help="读当前温度")
    temp_sweep = temp_sub.add_parser("sweep", help="设点+等稳+读真实温度+扫描")
    temp_sweep.add_argument("target_k", type=float)
    temp_sweep.add_argument("--skip-noise", action="store_true")
    temp_sweep.add_argument("--force", action="store_true")

    # ---- laser：设功率 / 关 / 读 ----
    laser_p = sub.add_parser("laser", help="激光控制")
    laser_sub = laser_p.add_subparsers(dest="laser_cmd", required=True)
    laser_set = laser_sub.add_parser("set", help="设功率 (mW)")
    laser_set.add_argument("power_mw", type=float)
    laser_sub.add_parser("off", help="关激光输出")
    laser_sub.add_parser("read", help="读激光状态")

    # ---- source：信号源控制 ----
    source_p = sub.add_parser("source", help="信号源控制")
    source_sub = source_p.add_subparsers(dest="source_cmd", required=True)
    source_sub.add_parser("on", help="RF 开")
    source_sub.add_parser("off", help="RF 关")
    source_freq = source_sub.add_parser("freq", help="设频率 (GHz)")
    source_freq.add_argument("freq_ghz", type=float)
    source_power = source_sub.add_parser("power", help="设功率 (dBm)")
    source_power.add_argument("power_dbm", type=float)

    # ---- track：追踪表查询（无硬件）----
    track_p = sub.add_parser("track", help="追踪表查询（无硬件）")
    track_p.add_argument("target_k", type=float)
    track_p.add_argument("res", nargs="?", default=None,
                         help="谐振器名（省略则列出全部）")

    # ---- chip：芯片标定库预测（无硬件）----
    chip_p = sub.add_parser("chip", help="芯片标定库预测谐振频率（无硬件）")
    chip_p.add_argument("target_k", type=float)

    # ---- iq：IQ 校准文件校验（无硬件）----
    iq_p = sub.add_parser("iq", help="IQ 校准文件校验（无硬件）")
    iq_sub = iq_p.add_subparsers(dest="iq_cmd", required=True)
    iq_check = iq_sub.add_parser("check", help="校验校准文件可加载")
    iq_check.add_argument("file")

    # ---- checkpoint：断点查看/重置（无硬件）----
    cp_p = sub.add_parser("checkpoint", help="断点查看/重置（无硬件）")
    cp_sub = cp_p.add_subparsers(dest="cp_cmd", required=True)
    cp_sub.add_parser("show", help="打印断点 JSON")
    cp_sub.add_parser("clear", help="删除断点文件")

    # ---- verify：硬件连通性验证 ----
    verify_p = sub.add_parser("verify", help="硬件连通性验证")
    verify_sub = verify_p.add_subparsers(dest="verify_cmd", required=True)
    verify_sub.add_parser("lakeshore", help="温控基本操作")
    verify_sub.add_parser("laser", help="激光基本操作")

    return p.parse_args(argv)


def _split_csv(text):
    if not text:
        return None
    parts = [x.strip() for x in text.split(",") if x.strip()]
    return [float(x) if _is_num(x) else x for x in parts]


def _is_num(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


def main(argv=None):
    args = _parse_args(argv)
    config_path = Path(args.config)
    if not config_path.exists():
        LOG.error("config 不存在: %s", config_path)
        return 2

    with open(config_path, encoding="utf-8") as fh:
        raw = json.load(fh)

    inherit = args.inherit_ui if args.inherit_ui is not None \
        else bool(raw.get("inherit_ui_params", False))

    config = resolve_config(
        raw,
        raw.get("app_settings_file"),
        raw.get("kid_gui_settings_file") or str(SCRIPT_DIR / "kid_gui_settings.json"),
        inherit,
    )

    if args.dry_run:
        config["backend"] = "mock"
        # mock 需要 identity 校准；config 没给就在项目 data 下生成一份
        if not config.get("iq_calibration_file"):
            import mock_instruments
            cal = SCRIPT_DIR / "data" / "IQ_calibration" / "_dryrun_identity_cal.txt"
            cal.parent.mkdir(parents=True, exist_ok=True)
            mock_instruments.write_identity_iq_calibration(cal)
            config["iq_calibration_file"] = str(cal)

    # 日志
    # Windows 控制台默认 GBK：把不可编码字符替换为 "?" 而非抛异常中断日志流
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = args.log_file or config.get("log_file")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )

    LOG.info("config: %s（inherit_ui_params=%s, backend=%s）",
             config_path, inherit, config["backend"])
    if args.dry_run:
        LOG.info("== --dry-run：backend=mock，不接触硬件 ==")
    log_param_sources(config, raw, raw.get("app_settings_file"),
                      raw.get("kid_gui_settings_file"), inherit, LOG)

    cmd = args.command

    # ---- 无硬件子命令：不校验/不需要任何仪器 ----
    if cmd == "track":
        return cmd_track(config, args)
    if cmd == "chip":
        return cmd_chip(config, args)
    if cmd == "iq":
        return cmd_iq(config, args)
    if cmd == "checkpoint":
        return cmd_checkpoint(config, args)
    if cmd == "verify":
        return cmd_verify(config, args)

    # ---- 以下命令需要测量硬件，统一校验 ----
    if config["backend"] != "mock":
        if not config.get("e8257d_visa_address"):
            LOG.error("backend 非 mock 时必须有 e8257d_visa_address")
            return 2
        if not config.get("pxie_device_name"):
            LOG.error("backend 非 mock 时必须有 pxie_device_name")
            return 2
    cal = config.get("iq_calibration_file")
    if not cal:
        LOG.error("config 缺少 iq_calibration_file（或未继承到）")
        return 2
    if config["backend"] != "mock" and not Path(cal).exists():
        LOG.error("IQ 校准文件不存在: %s", cal)
        return 2
    if config["i_channel"] not in config["channels"] or \
            config["q_channel"] not in config["channels"]:
        LOG.error("i_channel/q_channel 必须都在 channels 里: %s/%s in %s",
                  config["i_channel"], config["q_channel"], config["channels"])
        return 2
    if config["i_channel"] == config["q_channel"]:
        LOG.error("i_channel 与 q_channel 不能相同")
        return 2
    if not config.get("resonators"):
        LOG.error("config 缺少 resonators")
        return 2

    if cmd in ("scan", "temp") and not config.get("experiment_name"):
        config["experiment_name"] = cmd
        LOG.info("实验名自动生成: %s", config["experiment_name"])

    if cmd == "run":
        # 过滤参数 + 自动生成实验名
        args.only_temps = _split_csv(args.only_temps)
        args.only_powers = _split_csv(args.only_powers)
        args.only_res = _split_csv(args.only_res)
        if not config.get("experiment_name"):
            temps = sorted(float(t) for t in config["temperature_list_k"])
            powers = sorted(float(p) for p in config["laser_power_mw"])
            config["experiment_name"] = "{}-{}K&{}-{}mW".format(
                _fmt(temps[0]), _fmt(temps[-1]), _fmt(powers[0]), _fmt(powers[-1]))
            LOG.info("实验名自动生成: %s", config["experiment_name"])
        run(config, args)
        return 0
    if cmd == "scan":
        return cmd_scan(config, args)
    if cmd == "noise":
        return cmd_noise(config, args)
    if cmd == "temp":
        return cmd_temp(config, args)
    if cmd == "laser":
        return cmd_laser(config, args)
    if cmd == "source":
        return cmd_source(config, args)

    LOG.error("未知命令: %s", cmd)
    return 2


def _checkpoint_path(config):
    """断点文件路径：config.checkpoint_path 优先，否则 save_root/<experiment>/checkpoint.json。"""
    if config.get("checkpoint_path"):
        return Path(config["checkpoint_path"])
    return Path(config["save_root"]) / config["experiment_name"] / "checkpoint.json"


# =========================================================================
# 子命令实现
# =========================================================================

def cmd_track(config, args):
    """追踪表查询（无硬件）。"""
    if not config.get("tracking_file"):
        LOG.error("config 缺少 tracking_file")
        return 2
    table = rt.load_resonance_table(config["tracking_file"])
    LOG.info("%s", table)
    T = float(args.target_k)
    indices = [table.resonator_index(args.res)] if args.res is not None \
        else list(range(table.n_res))
    for idx in indices:
        f_ref = table.reference_frequency_hz(T, idx)
        resp = table.responsivity_ppm_per_mw(T, idx)
        resp_s = "{:.1f}".format(resp) if resp is not None else "None"
        LOG.info("  %-6s @ %.2f K: f_ref=%.6f GHz  resp=%s ppm/mW",
                 table.resonator_names[idx], T, f_ref / 1e9, resp_s)
    return 0


def cmd_chip(config, args):
    """芯片标定库预测（无硬件）。"""
    dp = config.get("data_process_dir")
    if not cl.available(dp):
        LOG.error("芯片标定库不可用: %s", cl.import_error())
        return 2
    cals = cl.list_calibrations(dp)
    if not cals:
        LOG.error("无可用标定存档（Data_process/thermal_models/ 为空）")
        return 2
    LOG.info("可用标定: %s", [c["chip_id"] + "__" + c["run_id"] for c in cals])
    T = float(args.target_k)
    f_pred, half = cl.predict_resonance_frequencies(
        chip_id=config.get("chip_id"), run_id=config.get("chip_run_id"),
        T_k=T, data_process_dir=dp)
    if f_pred is None:
        LOG.error("预测失败（标定缺失/谐振器数不符，chip_id=%s run_id=%s）",
                  config.get("chip_id"), config.get("chip_run_id"))
        return 2
    for i, f in enumerate(f_pred):
        LOG.info("  res%d @ %.2f K: f0=%.6f GHz  窗口半宽 ±%.1f MHz",
                 i + 1, T, f / 1e9, half[i] / 1e6)
    return 0


def cmd_iq(config, args):
    """IQ 校准文件校验（无硬件）。"""
    from IQ_calibration import IQCalibrationTable
    path = Path(args.file)
    try:
        table = IQCalibrationTable.load(str(path))
    except Exception as exc:
        LOG.error("IQ 校准文件校验失败: %s", exc)
        return 2
    LOG.info("IQ 校准文件 OK: %s", table.source_path)
    LOG.info("  %d 个频率点: %s",
             table.frequencies_hz.size,
             ["{:.3f} GHz".format(f / 1e9) for f in table.frequencies_hz])
    return 0


def cmd_checkpoint(config, args):
    """断点查看/重置（无硬件）。"""
    path = _checkpoint_path(config)
    if args.cp_cmd == "clear":
        if path.exists():
            path.unlink()
            LOG.info("断点已删除: %s", path)
        else:
            LOG.info("断点不存在（无需删除）: %s", path)
        return 0
    cp = Checkpoint(path)
    LOG.info("断点文件: %s", path)
    LOG.info("%s", json.dumps(cp.data, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(config, args):
    if args.verify_cmd == "lakeshore":
        return _verify_lakeshore(config)
    if args.verify_cmd == "laser":
        return _verify_laser(config)
    LOG.error("未知 verify 子命令: %s", args.verify_cmd)
    return 2


def cmd_laser(config, args):
    laser = make_laser_backend(config["backend"], config, LOG)
    if getattr(laser, "is_null", False):
        LOG.error("laser_visa_address 为空，激光未连接，'laser' 命令不可用")
        return 2
    try:
        if args.laser_cmd == "set":
            laser.set_wavelength(config["laser_wavelength_nm"])
            laser.set_power(args.power_mw)
            time.sleep(config["laser_settle_s"])
            LOG.info("激光功率已设 %.3f mW", args.power_mw)
        elif args.laser_cmd == "off":
            laser.output_off()
            LOG.info("激光输出已关")
        else:  # read
            LOG.info("激光状态:\n%s",
                     json.dumps(laser.get_status(), ensure_ascii=False, indent=2))
        return 0
    finally:
        laser.close()


def cmd_source(config, args):
    source = make_source(config["backend"], config, LOG)
    try:
        if args.source_cmd == "on":
            source.rf_on()
            LOG.info("RF 已开")
        elif args.source_cmd == "off":
            source.rf_off()
            LOG.info("RF 已关")
        elif args.source_cmd == "freq":
            source.set_frequency_ghz(args.freq_ghz)
            LOG.info("频率已设 %.6f GHz", args.freq_ghz)
        elif args.source_cmd == "power":
            source.set_power_dbm(args.power_dbm)
            LOG.info("功率已设 %.2f dBm", args.power_dbm)
        LOG.info("信号源状态: %s",
                 json.dumps(source.read_status(), ensure_ascii=False))
        return 0
    finally:
        source.disconnect()


def cmd_noise(config, args):
    """对已有精扫文件追加噪声（只需信号源+DAQ，不连温控/激光）。"""
    path = Path(args.s21_file)
    if not path.exists():
        LOG.error("S21 文件不存在: %s", path)
        return 2
    source, temp, laser, ctx, mock_instruments, stop_event = \
        _build_env(config, LOG, need_thermal_laser=False)
    try:
        source.rf_off()
        LOG.info("噪声: mode=%s, %.1f s → %s",
                 config["noise_frequency_mode"], config["noise_duration_s"], path)
        core.run_noise(ctx, _noise_params(config), str(path))
        return 0
    finally:
        try:
            source.rf_off()
        except Exception:
            pass
        try:
            source.disconnect()
        except Exception:
            pass


def cmd_scan(config, args):
    """当前温度下对 n 个中心频率宽扫→精扫→噪声（频率来源=手动给定）。"""
    source, temp, laser, ctx, mock_instruments, stop_event = _build_env(config, LOG)
    try:
        source.rf_off()
        laser.set_wavelength(config["laser_wavelength_nm"])

        actual_T = float(temp.get_temperature(config["lakeshore_channel"]))
        if getattr(temp, "is_fixed", False):
            LOG.info("无温控（FixedTemperature），按固定温度 %.2f K 扫描", actual_T)
        target_k = float(args.target_k) if args.target_k is not None else actual_T
        power_mw = float(args.power_mw) if args.power_mw is not None \
            else float(config["laser_power_mw"][0])
        resonators = list(config["resonators"])

        # 频率 ↔ 谐振器一一对应（每个 KID 谐振器有自己的 f0；幂等键含 res 名，不冲突）
        if args.res is not None:
            if len(args.freqs_ghz) != 1:
                LOG.error("指定 --res 时只能给 1 个中心频率（n 频率与 n 谐振器一一对应）")
                return 2
            pairs = [(args.res, args.freqs_ghz[0])]
        else:
            if len(args.freqs_ghz) > len(resonators):
                LOG.error("中心频率数 %d 超过谐振器数 %d",
                          len(args.freqs_ghz), len(resonators))
                return 2
            pairs = [(resonators[i]["name"], args.freqs_ghz[i])
                     for i in range(len(args.freqs_ghz))]

        save_root = Path(config["save_root"]) / config["experiment_name"]
        save_root.mkdir(parents=True, exist_ok=True)
        checkpoint = Checkpoint(_checkpoint_path(config))

        LOG.info("scan: 当前 T=%.4f K, 目录目标 %.2f K, power=%s mW, %d 中心",
                 actual_T, target_k, power_mw, len(pairs))
        for res_name, f in pairs:
            if stop_event.is_set():
                break
            res = {"name": res_name}
            run_one_point(config, CoreMeasure(ctx), source, laser, None, checkpoint,
                          stop_event, target_k, actual_T, res, 0, power_mw, save_root,
                          args.force, args.skip_noise, mock_instruments, LOG,
                          f_ref_override=f * 1e9)
        return 0
    finally:
        try:
            source.rf_off()
            laser.output_off()
        except Exception:
            pass
        try:
            source.disconnect()
            temp.close()
            laser.close()
        except Exception:
            pass


def cmd_temp(config, args):
    if args.temp_cmd == "read":
        temp = make_temperature_backend(config["backend"], config, LOG)
        if getattr(temp, "is_fixed", False):
            LOG.error("lakeshore_visa_address 为空，温控未连接，'temp read' 不可用")
            return 2
        try:
            T = temp.get_temperature(config["lakeshore_channel"])
            LOG.info("当前温度 %s = %.4f K (setpoint %.4f K)",
                     config["lakeshore_channel"], T, temp.get_setpoint())
            return 0
        finally:
            temp.close()

    # sweep：设点+等稳+读真实温度+扫描（一体，不拆）
    if not config.get("tracking_file"):
        LOG.error("config 缺少 tracking_file")
        return 2
    source, temp, laser, ctx, mock_instruments, stop_event = _build_env(config, LOG)
    try:
        source.rf_off()
        laser.set_wavelength(config["laser_wavelength_nm"])
        table = rt.load_resonance_table(config["tracking_file"])
        save_root = Path(config["save_root"]) / config["experiment_name"]
        save_root.mkdir(parents=True, exist_ok=True)
        checkpoint = Checkpoint(_checkpoint_path(config))

        T = float(args.target_k)
        LOG.info("=== 温度 %.2f K ===", T)
        if getattr(temp, "is_fixed", False):
            actual_T = float(config.get("fixed_temperature_k", T))
            stable = True
            LOG.info("无温控（FixedTemperature），跳过 set_temperature/wait_for_stability，"
                     "按固定温度 %.2f K", actual_T)
        else:
            temp.set_temperature(T)
            actual_T, stable = wait_for_stability(
                temp, T, config["stability_tolerance_k"],
                config["stability_hold_s"], config["stability_poll_s"],
                config["stability_max_wait_s"], config["lakeshore_channel"], LOG)
        checkpoint.mark_temperature(T, actual_T, stable)
        if not stable:
            LOG.error("T=%.2f K 未稳定（实际 %.4f K）", T, actual_T)
            if config["abort_on_unstable"]:
                return 2
            LOG.warning("按配置继续（abort_on_unstable=false）")
        else:
            LOG.info("T=%.2f K 稳定，实际 %.4f K", T, actual_T)

        powers = [float(p) for p in config["laser_power_mw"]]
        resonators = list(config["resonators"])
        for res in resonators:
            res_name = res["name"]
            res_idx = table.resonator_index(res_name)
            for power_mw in powers:
                if stop_event.is_set():
                    break
                run_one_point(config, CoreMeasure(ctx), source, laser, table,
                              checkpoint, stop_event, T, actual_T, res, res_idx,
                              power_mw, save_root, args.force, args.skip_noise,
                              mock_instruments, LOG)
        laser.set_power(0)
        LOG.info("=== 温度 %.2f K 完成，激光关闭 ===", T)
        return 0
    finally:
        try:
            source.rf_off()
            laser.output_off()
        except Exception:
            pass
        try:
            source.disconnect()
            temp.close()
            laser.close()
        except Exception:
            pass


def _verify_lakeshore(config):
    temp = make_temperature_backend(config["backend"], config, LOG)
    if getattr(temp, "is_fixed", False):
        LOG.error("温控未连接（lakeshore_visa_address 为空），请先接 LakeShore 335 再验证")
        return 2
    try:
        T = temp.get_temperature(config["lakeshore_channel"])
        LOG.info("== 温控验证: identity=%s", getattr(temp, "identity", "n/a"))
        LOG.info("当前温度 %s = %.4f K", config["lakeshore_channel"], T)
        sp = T + 1.0   # 设一个略高于当前温度的 setpoint
        temp.set_temperature(sp)
        time.sleep(0.5)
        LOG.info("回读 setpoint = %.4f K，温度 %.4f K（应开始趋向 %.2f K）",
                 temp.get_setpoint(), temp.get_temperature(config["lakeshore_channel"]),
                 sp)
        LOG.info("温控验证通过")
        return 0
    finally:
        temp.close()


def _verify_laser(config):
    laser = make_laser_backend(config["backend"], config, LOG)
    if getattr(laser, "is_null", False):
        LOG.error("激光未连接（laser_visa_address 为空），请先接 N7779C 再验证")
        return 2
    try:
        laser.set_wavelength(config["laser_wavelength_nm"])
        laser.set_power(0.5)
        time.sleep(0.5)
        status = laser.get_status()
        LOG.info("== 激光验证 ==\n%s", json.dumps(status, ensure_ascii=False, indent=2))
        laser.output_off()
        LOG.info("激光验证通过（已 output_off）")
        return 0
    finally:
        laser.close()


if __name__ == "__main__":
    sys.exit(main())
