# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 使用 plot_all.py --approaches B 替代。
# 此文件保留仅供代码参考，不再维护。
"""方案 B: 5×5 网格 + 每格单独导出。

行 = 温度 (6K, 10K, 20K, 40K, 77K)
列 = 谐振子 (R1, R2, R3, R4, R5)
每格 = 3 条精选 VNA 功率曲线 (最低/中位/最高)

输出:
    approach_B_grid/
    ├── grid_5x5_overview.png          ← 完整网格
    └── individual/
        ├── T6K_R1.png ... T77K_R5.png ← 每格单独导出
"""

import os, sys, re, collections
from typing import Optional, Tuple, Dict

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter
import skrf as rf

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    identify_resonators, track_across_pl, RESONATOR_NAMES,
)

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]
SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3
OUTLIER_SIGMA = 2.0

_OUTPUT_BASE = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser"
OUT_DIR = os.path.join(_OUTPUT_BASE, "approach_B_grid")

# 配色
TEMP_COLORS = {6: "#1565C0", 10: "#0097A7", 20: "#4CAF50", 40: "#FF9800", 77: "#D32F2F"}
RES_COLORS = {"R1": "#7B1FA2", "R2": "#1976D2", "R3": "#388E3C", "R4": "#F57C00", "R5": "#D32F2F"}

# =====================================================================
# 共用工具
# =====================================================================

def extract_vna_power_from_path(path: str) -> Optional[int]:
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None

def load_s2p_complex(file_path: str):
    try:
        ntwk = rf.Network(file_path)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        mask = np.isfinite(s21_db)
        if not mask.any(): return None
        return freq[mask], s21[mask], s21_db[mask]
    except: return None

def find_s2p_for_pv_pl(temp_dir, target_pv, target_pl):
    candidates = find_s2p_files_for_laser_power(temp_dir, target_pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == target_pv:
            return fp
    return None

def smooth_curve(y):
    valid = ~np.isnan(y)
    if valid.sum() < SG_POLYORDER + 2: return y.copy()
    wl = min(SG_WINDOW_LENGTH, valid.sum())
    if wl % 2 == 0: wl -= 1
    if wl < SG_POLYORDER + 2: return y.copy()
    y_smooth = y.copy()
    y_smooth[valid] = savgol_filter(y[valid], wl, SG_POLYORDER)
    return y_smooth

def robust_polyfit(x, y, deg=1, sigma=OUTLIER_SIGMA, max_iter=3):
    n = len(x)
    outlier_mask = np.zeros(n, dtype=bool)
    valid = ~(np.isnan(x) | np.isnan(y) | np.isinf(x) | np.isinf(y))
    if valid.sum() < deg + 2: return None, outlier_mask, np.ones(n, dtype=bool)
    full_valid = valid.copy()
    orig_indices = np.where(valid)[0]
    x_w = x[valid].astype(float)
    y_w = y[valid].astype(float)
    idx_w = orig_indices.copy()
    for _ in range(max_iter):
        if len(x_w) < deg + 2: break
        coeffs = np.polyfit(x_w, y_w, deg)
        y_pred = np.polyval(coeffs, x_w)
        residuals = np.abs(y_w - y_pred)
        std = np.std(residuals)
        if std < 1e-15: break
        keep = residuals <= sigma * std
        if keep.all(): break
        removed_idx = idx_w[~keep]
        outlier_mask[removed_idx] = True
        x_w = x_w[keep]; y_w = y_w[keep]; idx_w = idx_w[keep]
    if len(x_w) < deg + 2: return None, outlier_mask, full_valid
    # R²
    y_pred_all = np.polyval(coeffs, x[full_valid])
    ss_res = np.sum((y[full_valid] - y_pred_all) ** 2)
    ss_tot = np.sum((y[full_valid] - np.mean(y[full_valid])) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-15 else 0
    return coeffs, outlier_mask, full_valid, max(0, r2)

# =====================================================================
# 数据收集
# =====================================================================

def collect_data_for_deltaf(temp_dir, temp_k, resonator_name):
    all_pv = set()
    for Pl_mW in LASER_POWERS_MW:
        for fp in find_s2p_files_for_laser_power(temp_dir, Pl_mW):
            pv = extract_vna_power_from_path(fp)
            if pv is not None: all_pv.add(pv)
    vna_powers = sorted(all_pv)
    n_pv, n_pl = len(vna_powers), len(LASER_POWERS_MW)
    delta_f_over_f = np.full((n_pv, n_pl), np.nan)
    flags = np.full((n_pv, n_pl), "lost", dtype=object)
    pl0_idx = LASER_POWERS_MW.index(0)

    f0_at_pl0 = {}
    for i_pv, pv in enumerate(vna_powers):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None: continue
        loaded = load_s2p_complex(fp)
        if loaded is None: continue
        freq, s21_cplx, s21_db = loaded
        f0_history_local = []
        for prev_pv in vna_powers[:i_pv]:
            if prev_pv in f0_at_pl0 and f0_at_pl0[prev_pv] is not None:
                f0_history_local.append((temp_k, f0_at_pl0[prev_pv]))
        from _tracking_utils import track_one_resonator, SCRAPS_F0, SCRAPS_TEMPS
        skeleton_f0 = SCRAPS_F0[resonator_name]
        base_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0) if t <= temp_k + 15]
        full_history = base_history + f0_history_local[-3:]
        f0, dip = track_one_resonator(freq * 1e9, s21_db, s21_cplx, resonator_name, temp_k, full_history)
        if f0 is not None:
            f0_at_pl0[pv] = f0
            delta_f_over_f[i_pv, pl0_idx] = 0.0
            flags[i_pv, pl0_idx] = "tracked"

    for i_pv, pv in enumerate(vna_powers):
        if pv not in f0_at_pl0 or f0_at_pl0[pv] is None: continue
        f0_ref = f0_at_pl0[pv]
        s2p_by_pl = {}
        for Pl_mW in LASER_POWERS_MW:
            if Pl_mW == 0: continue
            fp = find_s2p_for_pv_pl(temp_dir, pv, Pl_mW)
            if fp is None: continue
            loaded = load_s2p_complex(fp)
            if loaded is None: continue
            s2p_by_pl[Pl_mW] = (loaded[0] * 1e9, loaded[2], loaded[1])
        if not s2p_by_pl: continue
        result = track_across_pl(s2p_by_pl, resonator_name, temp_k, f0_ref)
        for i_pl, pl in enumerate(LASER_POWERS_MW):
            if pl == 0: continue
            if pl in result["pl_list"]:
                idx = result["pl_list"].index(pl)
                f0_val = result["f0_ghz"][idx]
                flag = result["flags"][idx]
                flags[i_pv, i_pl] = flag
                if not np.isnan(f0_val) and flag != "lost":
                    delta_f_over_f[i_pv, i_pl] = (f0_val - f0_ref) / f0_ref

    return {"pv_list": vna_powers, "pl_list": LASER_POWERS_MW,
            "delta_f_over_f": delta_f_over_f, "flags": flags}

# =====================================================================
# 单格绘图函数 (供网格和单独导出共用)
# =====================================================================

def draw_single_cell(ax, data, resonator_name, temp_k, show_ylabel=True, show_xlabel=True,
                     show_title=True, show_legend=False):
    """在给定的 ax 上画一个格子: δf/f₀ vs Pl, 3 条精选 VNA 曲线 + 拟合线。"""
    pv_list = data["pv_list"]
    pl_list = data["pl_list"]
    dff = data["delta_f_over_f"]
    flags = data["flags"]

    if len(pv_list) < 2:
        ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center")
        return

    n_pv = len(pv_list)
    # 选 3 条: 最低, 中位, 最高 VNA 功率
    selected = sorted(set([0, n_pv // 2, n_pv - 1]))
    if len(selected) == 1:
        selected = [0]
    if len(selected) == 2:
        selected = [0, n_pv - 1] if n_pv > 1 else [0]

    # VNA 功率配色: 低(蓝) → 中(灰) → 高(红)
    vna_colors = {0: "#2196F3", 1: "#757575", 2: "#E53935"}

    for sel_i, i_pv in enumerate(selected):
        pv = pv_list[i_pv]
        y_raw = dff[i_pv, :] * 1e6  # → ppm
        fl = flags[i_pv, :]
        valid = ~np.isnan(y_raw) & (fl != "lost")
        if valid.sum() < 2:
            continue

        x_data = np.array(pl_list)[valid]
        y_data = y_raw[valid]

        # 平滑
        y_smooth = smooth_curve(y_data)
        mask_ok = np.isfinite(y_smooth)
        if mask_ok.sum() < 2:
            continue
        x_plot = x_data[mask_ok]
        y_plot = y_smooth[mask_ok]

        # 拟合
        result = robust_polyfit(x_plot, y_plot, deg=1)
        if result[0] is not None:
            coeffs, outlier_mask, _, r2 = result
        else:
            coeffs = None
            outlier_mask = np.zeros(len(x_plot), dtype=bool)
            r2 = 0

        color = vna_colors.get(sel_i, "#888888")

        # 散点
        for j in range(len(x_plot)):
            if outlier_mask[j]:
                ax.scatter(x_plot[j], y_plot[j], color=color, marker='x', s=30, alpha=0.4, zorder=3)
            else:
                ax.scatter(x_plot[j], y_plot[j], color=color, marker='o', s=18, alpha=0.7, zorder=3)

        # 连线 (非 outlier)
        non_out = ~outlier_mask
        if non_out.sum() >= 2:
            ax.plot(x_plot[non_out], y_plot[non_out], color=color, linewidth=2, alpha=0.8)

        # 拟合虚线
        if coeffs is not None and non_out.sum() >= 2:
            x_fit = np.linspace(x_plot[non_out].min(), x_plot[non_out].max(), 50)
            y_fit = np.polyval(coeffs, x_fit)
            lbl = f"{pv} dBm (R={coeffs[0]:.1f})" if show_legend else None
            ax.plot(x_fit, y_fit, color=color, linewidth=1, alpha=0.5, linestyle="--", label=lbl)

    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
    ax.grid(True, alpha=0.25)

    if show_xlabel:
        ax.set_xlabel("Laser Power (mW)", fontsize=10)
    if show_ylabel:
        ax.set_ylabel("df/f (ppm)", fontsize=10)
    if show_title:
        ax.set_title(f"{resonator_name}  @  {temp_k} K", fontsize=11, fontweight="bold")
    if show_legend:
        ax.legend(fontsize=7, loc="upper left")

# =====================================================================
# 主网格
# =====================================================================

def plot_grid(all_data, output_path):
    n_rows, n_cols = len(TEMPERATURES_K), len(RESONATOR_NAMES)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(26, 20), dpi=150,
                             sharex=True, sharey="row")

    for i_row, temp_k in enumerate(TEMPERATURES_K):
        for j_col, rname in enumerate(RESONATOR_NAMES):
            ax = axes[i_row, j_col]
            if temp_k not in all_data or rname not in all_data[temp_k]:
                ax.text(0.5, 0.5, "N/A", transform=ax.transAxes, ha="center", va="center")
                continue

            data = all_data[temp_k][rname]
            is_last_row = (i_row == n_rows - 1)
            is_first_col = (j_col == 0)
            is_top_right = (i_row == 0 and j_col == n_cols - 1)

            draw_single_cell(ax, data, rname, temp_k,
                             show_ylabel=is_first_col,
                             show_xlabel=is_last_row,
                             show_title=(i_row == 0),
                             show_legend=is_top_right)

            # 行首标注温度
            if is_first_col:
                ax.annotate(f"{temp_k} K", xy=(-0.22, 0.5), xycoords="axes fraction",
                            fontsize=13, fontweight="bold", ha="center", va="center",
                            rotation=90, color=TEMP_COLORS[temp_k])

    fig.suptitle("df/f vs Laser Power — Grid Overview  |  Blue=low VNA  Grey=mid VNA  Red=high VNA",
                 fontsize=16, fontweight="bold", y=1.005)
    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  [grid] {os.path.basename(output_path)}")
    plt.close(fig)

# =====================================================================
# 单独导出每格
# =====================================================================

def export_individual_cells(all_data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    for temp_k in TEMPERATURES_K:
        for rname in RESONATOR_NAMES:
            if temp_k not in all_data or rname not in all_data[temp_k]:
                continue

            data = all_data[temp_k][rname]
            fig, ax = plt.subplots(figsize=(10, 6), dpi=150)

            draw_single_cell(ax, data, rname, temp_k,
                             show_ylabel=True, show_xlabel=True,
                             show_title=True, show_legend=True)

            fname = f"T{temp_k}K_{rname}.png"
            out_path = os.path.join(output_dir, fname)
            fig.savefig(out_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            count += 1
    print(f"  [individual] {count} cells exported to {output_dir}")

# =====================================================================
# Main
# =====================================================================

def main():
    print("=" * 60)
    print("Approach B: 5x5 Grid + Individual Cell Export")
    print("=" * 60)

    # ---- 收集数据 ----
    print("\n[Collecting data...]")
    all_data = {}
    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir): continue
        all_data[T_K] = {}

        if T_K >= 70:
            ref_fp = None
            for pv_try in [-25, -35, -45, -55]:
                ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, 9)
                if ref_fp is not None: break
        else:
            ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)
        if ref_fp is None:
            all_pv = set()
            for pl in LASER_POWERS_MW:
                for f in find_s2p_files_for_laser_power(temp_dir, pl):
                    pv = extract_vna_power_from_path(f)
                    if pv is not None: all_pv.add(pv)
            if not all_pv: continue
            ref_pv = min(all_pv)
            ref_fp = find_s2p_for_pv_pl(temp_dir, ref_pv, 0)
            if ref_fp is None: continue

        ref_loaded = load_s2p_complex(ref_fp)
        if ref_loaded is None: continue
        ref_freq, ref_s21_cplx, ref_s21_db = ref_loaded

        resonators = identify_resonators(ref_freq * 1e9, ref_s21_db, ref_s21_cplx, T_K)
        n_found = sum(1 for r in resonators if r["f0_ghz"] is not None)
        print(f"  T={T_K}K: {n_found}/5 found")

        for r in resonators:
            if r["f0_ghz"] is None: continue
            data = collect_data_for_deltaf(temp_dir, T_K, r["name"])
            n_tracked = np.sum(data["flags"] == "tracked")
            if n_tracked >= 3:
                all_data[T_K][r["name"]] = data

    # ---- 生成 ----
    print("\n[Generating grid...]")
    plot_grid(all_data, os.path.join(OUT_DIR, "grid_5x5_overview.png"))

    print("\n[Exporting individual cells...]")
    export_individual_cells(all_data, os.path.join(OUT_DIR, "individual"))

    print("\nDone!")
    print(f"Output: {OUT_DIR}")

if __name__ == "__main__":
    main()
