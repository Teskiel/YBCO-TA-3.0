# -*- coding: utf-8 -*-
"""R4 谐振子 0 mW vs 9 mW 激光功率 S21 叠加对比图。

每个温度一张图，叠加两组 VNA 功率扫描曲线：
  0 mW (冷色系: 蓝→青) vs 9 mW (暖色系: 橙→红)
直观展示激光功率引起的 f0 红移和 dip 深度变化。

用法:
    python draw/plot_r4_laser_compare.py

输出:
    draw/output/r4_laser_compare/T{temp}K_R4_0mw_vs_9mw.png
"""

import os
import sys
import re
from typing import List, Optional, Tuple, Dict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy.signal import savgol_filter
import skrf as rf

sys.path.insert(0, os.path.dirname(__file__))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    identify_resonators, zoom_mhz, RESONATOR_NAMES,
)

# =========================================================================
# 配置常量
# =========================================================================

# 合并后的实验数据目录
EXPERIMENT_DATA_DIR = (
    r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data"
    r"\~merged\20260609-0609__6-77K__480pts"
)

TEMPERATURES_K = [6, 10, 20, 40, 77]
TARGET_RESONATOR = "R4"

# Savitzky-Golay 平滑参数
SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3

# 输出
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "r4_laser_compare")
SAVE_FIGURES = True
SHOW_PLOTS = False

# =========================================================================
# 配色方案
# =========================================================================

def build_blue_teal_cmap(n_colors: int = 256) -> LinearSegmentedColormap:
    """0 mW 冷色系: 深蓝 → 青 → 浅青。"""
    return LinearSegmentedColormap.from_list(
        "BlueTeal",
        [
            (0.059, 0.278, 0.631),   # #0F47A1 深蓝
            (0.000, 0.592, 0.655),   # #0097A7 青
            (0.000, 0.757, 0.820),   # #00C1D1 浅青
        ],
        N=n_colors,
    )


def build_orange_red_cmap(n_colors: int = 256) -> LinearSegmentedColormap:
    """9 mW 暖色系: 橙 → 深红。"""
    return LinearSegmentedColormap.from_list(
        "OrangeRed",
        [
            (1.000, 0.596, 0.000),   # #FF9800 橙
            (0.937, 0.427, 0.000),   # #EF6D00 深橙
            (0.827, 0.184, 0.184),   # #D32F2F 红
        ],
        N=n_colors,
    )


# =========================================================================
# 工具函数
# =========================================================================

def extract_vna_power_from_path(path: str) -> Optional[int]:
    """从路径中提取 VNA 功率 (dBm)。"""
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


def find_resonator_f0(
    traces: List[Dict], temp_k: float, resonator_name: str
) -> Optional[float]:
    """从 traces 中识别指定谐振子的 f0。

    选择参考 trace: 低温用最低 VNA 功率, 高温用最高 VNA 功率。
    """
    if not traces:
        return None

    if temp_k >= 70:
        ref_trace = traces[-1]   # 最高 VNA 功率, 最佳 SNR
    else:
        ref_trace = traces[0]    # 最低 VNA 功率, 最接近小信号

    freq_hz = ref_trace["freq"] * 1e9
    s21_db = ref_trace["s21_db"]
    s21_cplx = ref_trace["s21_complex"]

    resonators = identify_resonators(freq_hz, s21_db, s21_cplx, temp_k)

    for r in resonators:
        if r["name"] == resonator_name and r["f0_ghz"] is not None:
            return r["f0_ghz"]
    return None


# =========================================================================
# 主绘图函数
# =========================================================================

def plot_r4_combined_overlay(
    traces_0mw: List[Dict],
    traces_9mw: List[Dict],
    f0_ghz: float,
    temp_k: float,
    output_path: Optional[str] = None,
) -> plt.Figure:
    """单温度 R4 S21 叠加对比图: 0 mW vs 9 mW 双色系叠加。"""
    half_window_ghz = zoom_mhz(temp_k) / 1000.0

    fig, ax = plt.subplots(figsize=(14, 9), dpi=150)
    cmap_0mw = build_blue_teal_cmap()
    cmap_9mw = build_orange_red_cmap()

    # ---- 画 0 mW 组 (冷色系) ----
    n_0mw = len(traces_0mw)
    for i, trace in enumerate(traces_0mw):
        freq = trace["freq"]
        s21_db = trace["s21_db"]
        mask = np.abs(freq - f0_ghz) <= half_window_ghz
        if mask.sum() < 10:
            continue

        f_zoom = freq[mask]
        s_zoom = s21_db[mask]
        s_smooth = smooth_s21(s_zoom)

        color = cmap_0mw(i / max(n_0mw - 1, 1))
        ax.plot(f_zoom, s_smooth, color=color, linewidth=2.5, alpha=0.85,
                antialiased=True)

    # ---- 画 9 mW 组 (暖色系) ----
    n_9mw = len(traces_9mw)
    for i, trace in enumerate(traces_9mw):
        freq = trace["freq"]
        s21_db = trace["s21_db"]
        mask = np.abs(freq - f0_ghz) <= half_window_ghz
        if mask.sum() < 10:
            continue

        f_zoom = freq[mask]
        s_zoom = s21_db[mask]
        s_smooth = smooth_s21(s_zoom)

        color = cmap_9mw(i / max(n_9mw - 1, 1))
        ax.plot(f_zoom, s_smooth, color=color, linewidth=2.5, alpha=0.85,
                antialiased=True)

    # ---- 标注 f0 ----
    ax.axvline(x=f0_ghz, color="black", linestyle="--", alpha=0.4, linewidth=1.2)

    # ---- 标签和标题 ----
    ax.set_xlabel("Frequency (GHz)", fontsize=14)
    ax.set_ylabel("|S21| (dB)", fontsize=14)
    ax.set_title(
        f"R4  |  T = {temp_k:.0f} K  |  0 mW vs 9 mW Laser  "
        f"(f0 = {f0_ghz:.4f} GHz)",
        fontsize=15, fontweight="bold",
    )
    ax.grid(True, alpha=0.35)

    # ---- 双色标: 左侧 0 mW (冷色), 右侧 9 mW (暖色) ----
    if n_0mw > 0:
        pv_min_0 = min(t["pv"] for t in traces_0mw)
        pv_max_0 = max(t["pv"] for t in traces_0mw)
        sm_0 = plt.cm.ScalarMappable(
            norm=plt.Normalize(vmin=pv_min_0, vmax=pv_max_0), cmap=cmap_0mw
        )
        cbar_0 = fig.colorbar(sm_0, ax=ax, pad=0.02)
        cbar_0.set_label("VNA Power (dBm)  —  0 mW Laser", fontsize=11)

    if n_9mw > 0:
        pv_min_9 = min(t["pv"] for t in traces_9mw)
        pv_max_9 = max(t["pv"] for t in traces_9mw)
        sm_9 = plt.cm.ScalarMappable(
            norm=plt.Normalize(vmin=pv_min_9, vmax=pv_max_9), cmap=cmap_9mw
        )
        cbar_9 = fig.colorbar(sm_9, ax=ax, pad=0.02)
        cbar_9.set_label("VNA Power (dBm)  —  9 mW Laser", fontsize=11)

    fig.tight_layout(pad=2.0)

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

    print("=" * 64)
    print("plot_r4_laser_compare.py — R4 谐振子 0 mW vs 9 mW 叠加对比")
    print(f"Data: {EXPERIMENT_DATA_DIR}")
    print(f"Temps: {TEMPERATURES_K}")
    print(f"Target: {TARGET_RESONATOR}")
    print("=" * 64)

    total_figs = 0
    total_missed = 0

    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir):
            print(f"\n  [Skip] T={T_K}K: directory not found")
            continue

        # 收集两组 trace
        traces_0mw = collect_traces_by_pv(temp_dir, 0)
        traces_9mw = collect_traces_by_pv(temp_dir, 9)

        print(f"\nT={T_K}K: 0mW -> {len(traces_0mw)} traces, "
              f"9mW -> {len(traces_9mw)} traces")

        if len(traces_0mw) < 2 and len(traces_9mw) < 2:
            print(f"  [Skip] Not enough traces for either laser power")
            continue

        # 用 0 mW 参考 trace 识别 R4 f0 (fallback to 9mW)
        f0 = find_resonator_f0(traces_0mw or traces_9mw, T_K, TARGET_RESONATOR)
        if f0 is None and traces_9mw:
            f0 = find_resonator_f0(traces_9mw, T_K, TARGET_RESONATOR)

        if f0 is None:
            print(f"  [MISSED] R4 f0 not found at T={T_K}K")
            total_missed += 1
            continue

        print(f"  R4 f0 = {f0:.4f} GHz  (zoom = +/-{zoom_mhz(T_K):.0f} MHz)")

        fname = f"T{T_K}K_R4_0mw_vs_9mw.png"
        out_path = os.path.join(OUTPUT_DIR, fname)
        fig = plot_r4_combined_overlay(
            traces_0mw, traces_9mw, f0, T_K,
            output_path=out_path if SAVE_FIGURES else None,
        )
        if SHOW_PLOTS:
            plt.show()
        plt.close(fig)
        total_figs += 1

    print(f"\n{'=' * 64}")
    print(f"Done: {total_figs} figures saved, {total_missed} missed.")
    print(f"Output: {OUTPUT_DIR}")
    print(f"{'=' * 64}")


if __name__ == "__main__":
    main()
