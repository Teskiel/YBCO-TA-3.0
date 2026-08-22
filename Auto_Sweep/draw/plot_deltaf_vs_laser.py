# -*- coding: utf-8 -*-
"""Delta-f/f vs 激光功率 — 使用 f0 骨架 + 双验证追踪。

每张图 = 一个谐振子 (R1-R5) + 一个温度。
横轴 = 激光功率 (mW)，纵轴 = Delta-f/f (相对 Pl=0)。
不同 VNA 读出功率曲线叠放，含 polyfit 趋势线 + 2sigma 离群剔除。

f0 定位: _tracking_utils.py 的 scraps 骨架外推 + P90 基线矫正 + 双验证。
输出: D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser\

用法:
    python draw/plot_deltaf_vs_laser.py
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
    identify_resonators, track_across_pl, RESONATOR_NAMES,
)

# =========================================================================
# 配置常量
# =========================================================================

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"

TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]      # 横轴
N_RESONATORS = 5

# Savitzky-Golay
SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3

# 离群剔除
OUTLIER_SIGMA = 2.0

# 输出
_OUTPUT_BASE = "D:\\YBCO\\VNAMeas\\Data_process\\output\\_vna_2dBm_step_analysis"
OUTPUT_DIR = os.path.join(_OUTPUT_BASE, "deltaf_vs_laser")
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
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None


def load_s2p_complex(file_path: str) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
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


def find_s2p_for_pv_pl(temp_dir: str, target_pv: int, target_pl: int) -> Optional[str]:
    candidates = find_s2p_files_for_laser_power(temp_dir, target_pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == target_pv:
            return fp
    return None


def smooth_curve(y: np.ndarray) -> np.ndarray:
    valid = ~np.isnan(y)
    if valid.sum() < SG_POLYORDER + 2:
        return y.copy()
    wl = min(SG_WINDOW_LENGTH, valid.sum())
    if wl % 2 == 0:
        wl -= 1
    if wl < SG_POLYORDER + 2:
        return y.copy()
    y_smooth = y.copy()
    y_smooth[valid] = savgol_filter(y[valid], wl, SG_POLYORDER)
    return y_smooth

# =========================================================================
# Robust polyfit
# =========================================================================

def robust_polyfit(
    x: np.ndarray, y: np.ndarray,
    deg: int = 1, sigma: float = OUTLIER_SIGMA, max_iter: int = 3,
) -> Tuple[Optional[np.ndarray], np.ndarray]:
    n = len(x)
    outlier_mask = np.zeros(n, dtype=bool)
    valid = ~(np.isnan(x) | np.isnan(y))
    if valid.sum() < deg + 2:
        return None, outlier_mask

    orig_indices = np.where(valid)[0]
    x_w = x[valid].astype(float)
    y_w = y[valid].astype(float)
    idx_w = orig_indices.copy()

    for _ in range(max_iter):
        if len(x_w) < deg + 2:
            break
        coeffs = np.polyfit(x_w, y_w, deg)
        y_pred = np.polyval(coeffs, x_w)
        residuals = np.abs(y_w - y_pred)
        std = np.std(residuals)
        if std < 1e-15:
            break
        keep = residuals <= sigma * std
        if keep.all():
            break
        removed_idx = idx_w[~keep]
        outlier_mask[removed_idx] = True
        x_w = x_w[keep]
        y_w = y_w[keep]
        idx_w = idx_w[keep]

    if len(x_w) < deg + 2:
        return None, outlier_mask
    return np.polyfit(x_w, y_w, deg), outlier_mask

# =========================================================================
# 数据收集
# =========================================================================

def collect_data_for_deltaf(
    temp_dir: str, temp_k: float, resonator_name: str, f0_ref_ghz: float,
) -> Dict:
    """收集一个谐振子跨所有 (VNA功率, 激光功率) 的 delta-f/f 数据。

    Returns:
        {"pv_list": [...], "pl_list": [...],
         "delta_f_over_f": 2D [n_pv x n_pl],
         "flags": 2D [n_pv x n_pl]}
    """
    # 发现所有 VNA 功率级别
    all_pv = set()
    for Pl_mW in LASER_POWERS_MW:
        files = find_s2p_files_for_laser_power(temp_dir, Pl_mW)
        for fp in files:
            pv = extract_vna_power_from_path(fp)
            if pv is not None:
                all_pv.add(pv)
    vna_powers = sorted(all_pv)

    n_pv = len(vna_powers)
    n_pl = len(LASER_POWERS_MW)
    delta_f_over_f = np.full((n_pv, n_pl), np.nan)
    flags = np.full((n_pv, n_pl), "lost", dtype=object)

    # Pl=0 索引
    pl0_idx = LASER_POWERS_MW.index(0)

    # Step 1: 对每个 Pv, 在 Pl=0 时定位谐振子
    f0_at_pl0 = {}
    for i_pv, pv in enumerate(vna_powers):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None:
            continue
        loaded = load_s2p_complex(fp)
        if loaded is None:
            continue
        freq, s21_cplx, s21_db = loaded

        # 追踪: 使用跨 Pv 的历史来辅助定位
        # 构建 f0_history: 已找到的低 Pv 的 (T, f0)
        f0_history_local = []
        for prev_pv in vna_powers[:i_pv]:
            if prev_pv in f0_at_pl0 and f0_at_pl0[prev_pv] is not None:
                f0_history_local.append((temp_k, f0_at_pl0[prev_pv]))

        from _tracking_utils import track_one_resonator, SCRAPS_F0, SCRAPS_TEMPS
        skeleton_f0 = SCRAPS_F0[resonator_name]
        base_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0)
                        if t <= temp_k + 15]
        full_history = base_history + f0_history_local[-3:]  # 最近 3 个

        f0, dip = track_one_resonator(
            freq * 1e9, s21_db, s21_cplx, resonator_name, temp_k, full_history)

        if f0 is not None:
            f0_at_pl0[pv] = f0
            delta_f_over_f[i_pv, pl0_idx] = 0.0  # Pl=0 是参考, deltaf/f = 0
            flags[i_pv, pl0_idx] = "tracked"

    # Step 2: 对每个 Pv, 追踪跨 Pl 的 f0
    for i_pv, pv in enumerate(vna_powers):
        if pv not in f0_at_pl0 or f0_at_pl0[pv] is None:
            continue
        f0_ref = f0_at_pl0[pv]

        # 收集该 Pv 下所有 Pl 的数据
        s2p_by_pl = {}
        for Pl_mW in LASER_POWERS_MW:
            if Pl_mW == 0:
                continue
            fp = find_s2p_for_pv_pl(temp_dir, pv, Pl_mW)
            if fp is None:
                continue
            loaded = load_s2p_complex(fp)
            if loaded is None:
                continue
            s2p_by_pl[Pl_mW] = (loaded[0] * 1e9, loaded[2], loaded[1])

        if not s2p_by_pl:
            continue

        result = track_across_pl(s2p_by_pl, resonator_name, temp_k, f0_ref)

        for i_pl, pl in enumerate(LASER_POWERS_MW):
            if pl == 0:
                continue
            if pl in result["pl_list"]:
                idx = result["pl_list"].index(pl)
                f0_val = result["f0_ghz"][idx]
                flag = result["flags"][idx]
                flags[i_pv, i_pl] = flag
                if not np.isnan(f0_val) and flag != "lost":
                    delta_f_over_f[i_pv, i_pl] = (f0_val - f0_ref) / f0_ref

    return {
        "pv_list": vna_powers,
        "pl_list": LASER_POWERS_MW,
        "delta_f_over_f": delta_f_over_f,
        "flags": flags,
    }

# =========================================================================
# 绘图
# =========================================================================

def plot_one_deltaf_figure(
    data: Dict, resonator_name: str, temp_k: float,
    output_path: Optional[str] = None,
) -> plt.Figure:
    """单谐振子+单温度 Delta-f/f vs 激光功率图。"""
    pv_list = data["pv_list"]
    pl_list = data["pl_list"]
    dff = data["delta_f_over_f"]
    flags = data["flags"]

    fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
    cmap = build_red_purple_cmap()
    n_curves = len(pv_list)

    for i_pv, pv in enumerate(pv_list):
        y_raw = dff[i_pv, :]
        fl = flags[i_pv, :]

        valid = ~np.isnan(y_raw) & (fl != "lost")
        if valid.sum() < 2:
            continue

        x_data = np.array(pl_list)[valid]
        y_data = y_raw[valid]

        y_smooth = smooth_curve(y_data)
        coeffs, outlier_mask = robust_polyfit(x_data, y_smooth, deg=1)

        color = cmap(i_pv / max(n_curves - 1, 1))

        for j in range(len(x_data)):
            marker = 'x' if outlier_mask[j] else 'o'
            size = 30 if outlier_mask[j] else 20
            ax.scatter(x_data[j], y_smooth[j], color=color,
                       marker=marker, s=size, alpha=0.6, zorder=3)

        non_outlier = ~outlier_mask
        if non_outlier.sum() >= 2:
            ax.plot(x_data[non_outlier], y_smooth[non_outlier],
                    color=color, linewidth=3, alpha=0.8)

        if coeffs is not None and non_outlier.sum() >= 2:
            x_fit = np.linspace(x_data[non_outlier].min(),
                                x_data[non_outlier].max(), 50)
            y_fit = np.polyval(coeffs, x_fit)
            ax.plot(x_fit, y_fit, color=color, linewidth=1.5,
                    alpha=0.4, linestyle="--")

    ax.set_xlabel("Laser Power (mW)", fontsize=13)
    ax.set_ylabel("Delta-f / f", fontsize=13)
    ax.set_title(
        f"T = {temp_k:.0f} K  |  {resonator_name}",
        fontsize=14, fontweight="bold",
    )
    ax.axhline(y=0, color="gray", linewidth=0.8, linestyle=":")
    ax.grid(True, alpha=0.4)

    pv_min = min(pv_list)
    pv_max = max(pv_list)
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
    print("plot_deltaf_vs_laser.py (v2: 骨架+双验证追踪)")
    print(f"Data: {EXPERIMENT_DATA_DIR}")
    print(f"Temps: {TEMPERATURES_K}")
    print(f"Laser powers (x-axis): {LASER_POWERS_MW} mW")
    print("=" * 60)

    total_figs = 0

    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir):
            continue

        # 参考检测: 高温 (>70K) 用 Pl=9 + 高 VNA 功率 (dip 更深)
        if T_K >= 70:
            ref_pl = 9
            # 尝试找高 VNA 功率
            ref_fp = None
            for pv_try in [-25, -35, -45, -55]:
                ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, ref_pl)
                if ref_fp is not None:
                    break
        else:
            ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)
        if ref_fp is None:
            # 尝试最低可用 Pv
            all_pv = set()
            for pl in LASER_POWERS_MW:
                for f in find_s2p_files_for_laser_power(temp_dir, pl):
                    pv = extract_vna_power_from_path(f)
                    if pv is not None:
                        all_pv.add(pv)
            if not all_pv:
                continue
            ref_pv = min(all_pv)
            ref_fp = find_s2p_for_pv_pl(temp_dir, ref_pv, 0)
            if ref_fp is None:
                continue

        ref_loaded = load_s2p_complex(ref_fp)
        if ref_loaded is None:
            continue
        ref_freq, ref_s21_cplx, ref_s21_db = ref_loaded

        resonators = identify_resonators(
            ref_freq * 1e9, ref_s21_db, ref_s21_cplx, T_K)
        n_found = sum(1 for r in resonators if r["f0_ghz"] is not None)
        print(f"\nT={T_K}K  ->  {n_found}/5 resonators found")

        for r in resonators:
            if r["f0_ghz"] is None:
                print(f"  {r['name']}: MISSED")
                continue

            print(f"  Processing {r['name']} (f0={r['f0_ghz']:.4f} GHz)...", end=" ")
            data = collect_data_for_deltaf(temp_dir, T_K, r["name"], r["f0_ghz"])
            n_tracked = np.sum(data["flags"] == "tracked")
            print(f"tracked={n_tracked}")

            if n_tracked < 3:
                print(f"    [Skip] Too few tracked points")
                continue

            fname = f"T{T_K}K_{r['name']}.png"
            out_path = os.path.join(OUTPUT_DIR, fname)
            fig = plot_one_deltaf_figure(
                data, r["name"], T_K,
                output_path=out_path if SAVE_FIGURES else None,
            )
            if SHOW_PLOTS:
                plt.show()
            plt.close(fig)
            total_figs += 1

    print(f"\nDone: {total_figs} figures saved.")
    print(f"Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
