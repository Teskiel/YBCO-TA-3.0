# -*- coding: utf-8 -*-
"""谐振子局部 S21 叠加图 — 使用 f0 骨架 + 双验证追踪。

每张图 = 一个谐振子 (R1-R5) + 一个温度 + 一个激光功率 (0/9 mW)。
16 条 VNA 读出功率 S21 曲线叠加于谐振子局部 zoom 窗口。
Savitzky-Golay 平滑，Red->Orange->Yellow->Green->Purple 五段色谱。

f0 定位: _tracking_utils.py 的 scraps 骨架外推 + P90 基线矫正 + 双验证。
输出: D:/YBCO/VNAMeas/Data_process/output/_vna_2dBm_step_analysis/s21_overlay/

用法:
    python draw/plot_s21_overlay_batch.py
"""

import os
import sys
import re
from typing import List, Optional, Tuple, Dict

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy.signal import savgol_filter
import skrf as rf

# ---- 复用 ----
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    identify_resonators, zoom_mhz, RESONATOR_NAMES, SCRAPS_F0,
)

# =========================================================================
# 配置常量
# =========================================================================

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"

TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 9]

# Savitzky-Golay 平滑
SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3

# 输出
_OUTPUT_BASE = "D:\\YBCO\\VNAMeas\\Data_process\\output\\_vna_2dBm_step_analysis"
OUTPUT_DIR = os.path.join(_OUTPUT_BASE, "s21_overlay")
SHOW_PLOTS = False
SAVE_FIGURES = True

# =========================================================================
# 工具函数
# =========================================================================

def build_red_purple_cmap(n_colors: int = 256) -> LinearSegmentedColormap:
    """Red(#D32F2F) -> Orange(#FF9800) -> Yellow(#FFEB3B) -> Green(#4CAF50) -> Purple(#7B1FA2) 五段渐变。"""
    return LinearSegmentedColormap.from_list(
        "RedOrangeYellowGreenPurple",
        [
            (0.827, 0.184, 0.184),   # Red
            (1.000, 0.596, 0.000),   # Orange
            (1.000, 0.922, 0.231),   # Yellow
            (0.298, 0.686, 0.314),   # Green
            (0.482, 0.122, 0.635),   # Purple
        ],
        N=n_colors,
    )


def extract_vna_power_from_path(path: str) -> Optional[int]:
    """从路径中提取 VNA 功率 (dBm) — 匹配目录段。"""
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None


def load_s2p_complex(
    file_path: str,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """加载 S2P，返回 (freq_ghz, s21_complex, s21_db)。"""
    try:
        ntwk = rf.Network(file_path)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        mask = np.isfinite(s21_db)
        if not mask.any():
            return None
        return freq[mask], s21[mask], s21_db[mask]
    except Exception:
        return None


def smooth_s21(s21_db: np.ndarray) -> np.ndarray:
    """Savitzky-Golay 平滑 S21 dB 数据。"""
    wl = min(SG_WINDOW_LENGTH, len(s21_db))
    if wl % 2 == 0:
        wl -= 1
    if wl < SG_POLYORDER + 2:
        return s21_db
    return savgol_filter(s21_db, wl, SG_POLYORDER)


def collect_traces_by_pv(temp_dir: str, laser_power_mw: int) -> List[Dict]:
    """收集指定温度+激光功率下所有 VNA 功率的 trace (去重)。"""
    files = find_s2p_files_for_laser_power(temp_dir, laser_power_mw)
    traces = []
    seen_pv = set()

    for fp in files:
        pv = extract_vna_power_from_path(fp)
        if pv is None or pv in seen_pv:
            continue
        seen_pv.add(pv)

        loaded = load_s2p_complex(fp)
        if loaded is None:
            continue
        freq, s21_cplx, s21_db = loaded
        traces.append({
            "file_path": fp, "pv": pv,
            "freq": freq, "s21_complex": s21_cplx, "s21_db": s21_db,
        })

    traces.sort(key=lambda t: t["pv"])
    return traces

# =========================================================================
# 绘图
# =========================================================================

def plot_one_resonator_overlay(
    traces: List[Dict],
    resonator_name: str,
    f0_ghz: float,
    temp_k: float,
    laser_mw: int,
    output_path: Optional[str] = None,
) -> plt.Figure:
    """单谐振子 S21 叠加图: zoom 窗口内 16 条 VNA 功率曲线。"""
    half_window_ghz = zoom_mhz(temp_k) / 1000.0

    fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
    cmap = build_red_purple_cmap()
    n_traces = len(traces)
    pv_min = min(t["pv"] for t in traces)
    pv_max = max(t["pv"] for t in traces)

    for i, trace in enumerate(traces):
        freq = trace["freq"]
        s21_db = trace["s21_db"]
        mask = np.abs(freq - f0_ghz) <= half_window_ghz
        if mask.sum() < 10:
            continue

        f_zoom = freq[mask]
        s_zoom = s21_db[mask]
        s_smooth = smooth_s21(s_zoom)

        color = cmap(i / max(n_traces - 1, 1))
        ax.plot(f_zoom, s_smooth, color=color, linewidth=3, alpha=0.8, antialiased=True)

    ax.axvline(x=f0_ghz, color="red", linestyle="--", alpha=0.4, linewidth=1)
    ax.set_xlabel("Frequency (GHz)", fontsize=13)
    ax.set_ylabel("|S21| (dB)", fontsize=13)
    ax.set_title(
        f"T = {temp_k:.0f} K  |  Laser = {laser_mw} mW  |  {resonator_name}  "
        f"(f0 = {f0_ghz:.4f} GHz)",
        fontsize=14, fontweight="bold",
    )
    ax.grid(True, alpha=0.4)

    sm = plt.cm.ScalarMappable(norm=plt.Normalize(vmin=pv_min, vmax=pv_max), cmap=cmap)
    cbar = fig.colorbar(sm, ax=ax)
    cbar.set_label("VNA Readout Power (dBm)", fontsize=11)

    fig.tight_layout()

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"  [saved] {os.path.basename(output_path)}")

    return fig

# =========================================================================
# 主入口
# =========================================================================

def main():
    if not os.path.isdir(EXPERIMENT_DATA_DIR):
        print(f"[Error] Data dir not found: {EXPERIMENT_DATA_DIR}")
        return

    print("=" * 60)
    print("plot_s21_overlay_batch.py (v2: 骨架+双验证追踪)")
    print(f"Data: {EXPERIMENT_DATA_DIR}")
    print(f"Temps: {TEMPERATURES_K}, Lasers: {LASER_POWERS_MW} mW")
    print("=" * 60)

    total_figs = 0
    total_missed = 0

    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir):
            continue

        for Pl_mW in LASER_POWERS_MW:
            traces = collect_traces_by_pv(temp_dir, Pl_mW)
            if len(traces) < 2:
                continue

            # 选择最佳参考 trace 做谐振子识别
            # 高温时 S21 基线抬升 → 用高 VNA 功率 (dip 更深, SNR 更好)
            # 低温时 → 用低 VNA 功率 (避免非线性)
            if T_K >= 70:
                ref_trace = traces[-1]  # 最高 VNA 功率 = 最佳 SNR
            else:
                ref_trace = traces[0]   # 最低 VNA 功率 = 最接近小信号
            freq_hz = ref_trace["freq"] * 1e9
            s21_db = ref_trace["s21_db"]
            s21_cplx = ref_trace["s21_complex"]

            resonators = identify_resonators(freq_hz, s21_db, s21_cplx, T_K)
            n_found = sum(1 for r in resonators if r["f0_ghz"] is not None)
            print(f"\nT={T_K}K  Pl={Pl_mW}mW  ->  {n_found}/5 resonators")

            for r in resonators:
                if r["f0_ghz"] is None:
                    print(f"  {r['name']}: MISSED")
                    total_missed += 1
                    continue

                fname = f"T{T_K}K_Pl{Pl_mW:02d}mW_{r['name']}.png"
                out_path = os.path.join(OUTPUT_DIR, fname)
                fig = plot_one_resonator_overlay(
                    traces, r["name"], r["f0_ghz"], T_K, Pl_mW,
                    output_path=out_path if SAVE_FIGURES else None,
                )
                if SHOW_PLOTS:
                    plt.show()
                plt.close(fig)
                total_figs += 1

    print(f"\nDone: {total_figs} figures saved, {total_missed} missed.")
    print(f"Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
