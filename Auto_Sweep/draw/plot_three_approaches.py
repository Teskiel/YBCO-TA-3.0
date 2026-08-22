# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 概念已整合进 plot_all.py。
# 此文件保留仅供代码参考，不再维护。
"""三种 delta-f/f 可视化方案的参考图生成脚本。

方案 A: 响应率 vs VNA 功率（每谐振子一张，曲线=温度）
方案 B: 5×5 网格（行=温度，列=谐振子），精选 3 条 VNA 曲线
方案 C: 双图分离 — (1) 响应率 vs VNA + (2) 最优 VNA 下的 δf/f₀ vs Pl

输出目录:
    deltaf_vs_laser/
    ├── approach_A_responsivity/
    ├── approach_B_grid/
    └── approach_C_twopanel/
"""

import os
import sys
import re
from typing import Optional, Tuple, Dict, List

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from scipy.signal import savgol_filter
import skrf as rf

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    identify_resonators, track_across_pl, RESONATOR_NAMES,
)

# =========================================================================
# 配置常量
# =========================================================================

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]
SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3
OUTLIER_SIGMA = 2.0

_OUTPUT_BASE = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser"

# =========================================================================
# 配色方案
# =========================================================================

# 温度色: 深蓝(6K) → 青(20K) → 橙(40K) → 深红(77K)
TEMP_COLORS = {
    6:  "#1565C0",
    10: "#0097A7",
    20: "#4CAF50",
    40: "#FF9800",
    77: "#D32F2F",
}

# 谐振子色 (R1-R5, 紫→红渐变)
RES_COLORS = {
    "R1": "#7B1FA2",
    "R2": "#1976D2",
    "R3": "#388E3C",
    "R4": "#F57C00",
    "R5": "#D32F2F",
}

# VNA 功率色 (低→高: 蓝→红)
VNA_CMAP_NAME = "coolwarm"

def build_red_purple_cmap(n_colors: int = 256) -> LinearSegmentedColormap:
    return LinearSegmentedColormap.from_list(
        "RedOrangeYellowGreenPurple",
        [
            (0.827, 0.184, 0.184),
            (1.000, 0.596, 0.000),
            (1.000, 0.922, 0.231),
            (0.298, 0.686, 0.314),
            (0.482, 0.122, 0.635),
        ],
        N=n_colors,
    )

# =========================================================================
# 共用工具函数
# =========================================================================

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


def robust_polyfit(
    x: np.ndarray, y: np.ndarray,
    deg: int = 1, sigma: float = OUTLIER_SIGMA, max_iter: int = 3,
) -> Tuple[Optional[np.ndarray], np.ndarray]:
    n = len(x)
    outlier_mask = np.zeros(n, dtype=bool)
    valid = ~(np.isnan(x) | np.isnan(y) | np.isinf(x) | np.isinf(y))
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
# 数据收集 — δf/f₀ (与 otherwise 一致)
# =========================================================================

def collect_data_for_deltaf(
    temp_dir: str, temp_k: float, resonator_name: str,
) -> Dict:
    """收集一个谐振子跨所有 (VNA功率, 激光功率) 的 δf/f₀ 数据。

    纵轴: δf/f₀ = (f0 - f0_ref) / f0_ref  (分数频移，无量纲)
    Pl=0 定义为 δf/f₀ = 0。

    Returns:
        {"pv_list": [...], "pl_list": [...],
         "delta_f_over_f": 2D [n_pv x n_pl],
         "flags": 2D [n_pv x n_pl]}
    """
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

    pl0_idx = LASER_POWERS_MW.index(0)

    # Step 1: Pl=0 时定位参考 f0
    f0_at_pl0 = {}
    for i_pv, pv in enumerate(vna_powers):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None:
            continue
        loaded = load_s2p_complex(fp)
        if loaded is None:
            continue
        freq, s21_cplx, s21_db = loaded

        f0_history_local = []
        for prev_pv in vna_powers[:i_pv]:
            if prev_pv in f0_at_pl0 and f0_at_pl0[prev_pv] is not None:
                f0_history_local.append((temp_k, f0_at_pl0[prev_pv]))

        from _tracking_utils import track_one_resonator, SCRAPS_F0, SCRAPS_TEMPS
        skeleton_f0 = SCRAPS_F0[resonator_name]
        base_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0)
                        if t <= temp_k + 15]
        full_history = base_history + f0_history_local[-3:]

        f0, dip = track_one_resonator(
            freq * 1e9, s21_db, s21_cplx, resonator_name, temp_k, full_history)

        if f0 is not None:
            f0_at_pl0[pv] = f0
            delta_f_over_f[i_pv, pl0_idx] = 0.0
            flags[i_pv, pl0_idx] = "tracked"

    # Step 2: 跨 Pl 追踪
    for i_pv, pv in enumerate(vna_powers):
        if pv not in f0_at_pl0 or f0_at_pl0[pv] is None:
            continue
        f0_ref = f0_at_pl0[pv]

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
# 提取响应率: d(δf/f₀)/dP_laser  (ppm/mW)
# =========================================================================

def extract_responsivity(data: Dict) -> Dict:
    """从 δf/f₀ 数据中提取每条 VNA 功率曲线的响应率 (斜率)。

    注意: 不使用 SG 平滑 — 只有 6 个激光功率点，SG 窗口过大导致边缘伪影。
    直接用原始数据做普通 polyfit。

    Returns:
        {"pv_list": [...], "responsivity_ppm_per_mw": [...],
         "r2_scores": [...], "n_points": [...]}
    """
    pv_list = data["pv_list"]
    pl_list = data["pl_list"]
    dff = data["delta_f_over_f"]
    flags = data["flags"]

    responsivity = []   # ppm/mW
    r2_scores = []
    n_points_list = []

    for i_pv, pv in enumerate(pv_list):
        y_raw = dff[i_pv, :] * 1e6  # → ppm
        fl = flags[i_pv, :]
        valid_mask = ~np.isnan(y_raw) & (fl != "lost")
        if valid_mask.sum() < 3:
            responsivity.append(np.nan)
            r2_scores.append(np.nan)
            n_points_list.append(0)
            continue

        x_data = np.array(pl_list)[valid_mask].astype(float)
        y_data = y_raw[valid_mask].astype(float)

        # 直接 polyfit，不做 SG 平滑（点太少，平滑引入伪影）
        coeffs = np.polyfit(x_data, y_data, 1)
        y_pred = np.polyval(coeffs, x_data)
        ss_res = np.sum((y_data - y_pred) ** 2)
        ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-15 else 0

        responsivity.append(coeffs[0])  # slope in ppm/mW
        r2_scores.append(max(0, r2))
        n_points_list.append(len(x_data))

    return {
        "pv_list": pv_list,
        "responsivity_ppm_per_mw": responsivity,
        "r2_scores": r2_scores,
        "n_points": n_points_list,
    }

# =========================================================================
# 方案 A: 响应率 vs VNA 功率
# =========================================================================

def plot_approach_a(
    all_data: Dict,   # {temp_k: {rname: data}}
    resonator_name: str,
    output_path: str,
):
    """响应率 vs VNA 功率，一条曲线一个温度。"""
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)

    for temp_k in TEMPERATURES_K:
        if temp_k not in all_data:
            continue
        if resonator_name not in all_data[temp_k]:
            continue
        data = all_data[temp_k][resonator_name]
        resp = extract_responsivity(data)

        pv_arr = np.array(resp["pv_list"])
        resp_arr = np.array(resp["responsivity_ppm_per_mw"])
        r2_arr = np.array(resp["r2_scores"])

        valid = ~np.isnan(resp_arr) & (r2_arr > 0.5)
        if valid.sum() < 2:
            continue

        pv_valid = pv_arr[valid]
        resp_valid = resp_arr[valid]
        r2_valid = r2_arr[valid]

        color = TEMP_COLORS.get(temp_k, "#888888")
        alpha_map = np.clip(r2_valid, 0.5, 1.0)

        # 散点: 透明度随 R² 变化
        for i in range(len(pv_valid)):
            ax.scatter(pv_valid[i], resp_valid[i], color=color,
                       alpha=float(alpha_map[i]), s=40, zorder=3)

        # 连线
        sort_idx = np.argsort(pv_valid)
        ax.plot(pv_valid[sort_idx], resp_valid[sort_idx],
                color=color, linewidth=2, alpha=0.7,
                label=f"{temp_k} K")

    ax.axhline(y=0, color="gray", linewidth=0.8, linestyle=":")
    ax.set_xlabel("VNA Readout Power (dBm)", fontsize=12)
    ax.set_ylabel("Responsivity d(df/f)/dP_laser (ppm/mW)", fontsize=12)
    ax.set_title(f"KID Responsivity vs VNA Power — {resonator_name}",
                 fontsize=14, fontweight="bold")
    ax.legend(title="Temperature", loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  [A] {os.path.basename(output_path)}")
    plt.close(fig)

# =========================================================================
# 方案 B: 5×5 网格，精选 3 条 VNA 曲线
# =========================================================================

def plot_approach_b_grid(
    all_data: Dict,
    output_path: str,
):
    """5 行 (温度) × 5 列 (谐振子) 网格，每格只画 3 条精选 VNA 功率曲线。"""
    fig, axes = plt.subplots(
        len(TEMPERATURES_K), len(RESONATOR_NAMES),
        figsize=(22, 18), dpi=150,
        sharex=True, sharey="row",
    )

    for i_row, temp_k in enumerate(TEMPERATURES_K):
        if temp_k not in all_data:
            continue
        for j_col, rname in enumerate(RESONATOR_NAMES):
            ax = axes[i_row, j_col]
            if rname not in all_data[temp_k]:
                ax.text(0.5, 0.5, "N/A", transform=ax.transAxes, ha="center", va="center")
                continue

            data = all_data[temp_k][rname]
            pv_list = data["pv_list"]
            pl_list = data["pl_list"]
            dff = data["delta_f_over_f"]
            flags = data["flags"]

            if len(pv_list) < 3:
                ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center", va="center")
                continue

            # 选 3 条: 最低, 中位, 最高 VNA 功率
            n_pv = len(pv_list)
            selected_indices = [0, n_pv // 2, n_pv - 1]
            # 去重
            selected_indices = sorted(set(selected_indices))

            vna_cmap = plt.cm.get_cmap(VNA_CMAP_NAME)

            for sel_i, i_pv in enumerate(selected_indices):
                pv = pv_list[i_pv]
                y_raw = dff[i_pv, :] * 1e6  # → ppm
                fl = flags[i_pv, :]
                valid = ~np.isnan(y_raw) & (fl != "lost")
                if valid.sum() < 2:
                    continue

                x_data = np.array(pl_list)[valid]
                y_data = y_raw[valid]
                y_smooth = smooth_curve(y_data)
                mask_finite = np.isfinite(y_smooth)
                if mask_finite.sum() < 2:
                    continue
                x_plot = x_data[mask_finite]
                y_plot = y_smooth[mask_finite]

                # 颜色: coolwarm 低VNA=蓝, 高VNA=红
                frac = sel_i / max(len(selected_indices) - 1, 1)
                color = vna_cmap(0.2 + frac * 0.6)  # avoid pure blue/red extremes

                ax.plot(x_plot, y_plot, '-o', color=color, linewidth=2,
                        markersize=5, alpha=0.8, label=f"{pv} dBm")

            ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
            ax.grid(True, alpha=0.25)

            # 行/列标签
            if j_col == 0:
                ax.set_ylabel(f"{temp_k} K\ndf/f (ppm)", fontsize=9)
            if i_row == 0:
                ax.set_title(rname, fontsize=11, fontweight="bold")
            if i_row == len(TEMPERATURES_K) - 1:
                ax.set_xlabel("Pl (mW)", fontsize=8)

            # 小图例
            if i_row == 0 and j_col == len(RESONATOR_NAMES) - 1:
                ax.legend(fontsize=6, loc="upper right")

    fig.suptitle("df/f vs Laser Power — Grid Overview (3 VNA power levels per cell)",
                 fontsize=16, fontweight="bold", y=1.01)
    fig.tight_layout()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  [B] {os.path.basename(output_path)}")
    plt.close(fig)

# =========================================================================
# 方案 C: 双图分离
# =========================================================================

def plot_approach_c_twopanel(
    all_data: Dict,
    output_dir: str,
):
    """图 1: 响应率 vs VNA 功率 (所有谐振子+温度)
       图 2: 最优 VNA 下的 δf/f₀ vs 激光功率"""
    os.makedirs(output_dir, exist_ok=True)

    # ---- 图 1: 响应率 vs VNA 功率 ----
    fig1, axes1 = plt.subplots(1, 2, figsize=(20, 8), dpi=150)

    # Left: per-resonator view
    ax_left = axes1[0]
    markers = {"R1": "o", "R2": "s", "R3": "D", "R4": "^", "R5": "v"}
    for rname in RESONATOR_NAMES:
        for temp_k in TEMPERATURES_K:
            if temp_k not in all_data or rname not in all_data[temp_k]:
                continue
            data = all_data[temp_k][rname]
            resp = extract_responsivity(data)
            pv_arr = np.array(resp["pv_list"])
            resp_arr = np.array(resp["responsivity_ppm_per_mw"])
            r2_arr = np.array(resp["r2_scores"])
            valid = ~np.isnan(resp_arr) & (r2_arr > 0.5)
            if valid.sum() < 2:
                continue
            pv_v = pv_arr[valid]
            resp_v = resp_arr[valid]
            sort_idx = np.argsort(pv_v)
            ax_left.plot(pv_v[sort_idx], resp_v[sort_idx],
                         color=TEMP_COLORS[temp_k], linewidth=1.5, alpha=0.7,
                         marker=markers[rname], markersize=5, markevery=2)

    ax_left.axhline(y=0, color="gray", linewidth=0.8, linestyle=":")
    ax_left.set_xlabel("VNA Power (dBm)", fontsize=12)
    ax_left.set_ylabel("Responsivity (ppm/mW)", fontsize=12)
    ax_left.set_title("Responsivity by Resonator & Temperature", fontsize=13, fontweight="bold")
    ax_left.grid(True, alpha=0.3)

    # Right: per-temperature view (average across resonators)
    ax_right = axes1[1]
    for temp_k in TEMPERATURES_K:
        if temp_k not in all_data:
            continue
        all_resp = []
        for rname in RESONATOR_NAMES:
            if rname not in all_data[temp_k]:
                continue
            data = all_data[temp_k][rname]
            resp = extract_responsivity(data)
            pv_arr = np.array(resp["pv_list"])
            resp_arr = np.array(resp["responsivity_ppm_per_mw"])
            r2_arr = np.array(resp["r2_scores"])
            valid = ~np.isnan(resp_arr) & (r2_arr > 0.5)
            if valid.sum() >= 2:
                all_resp.append((pv_arr[valid], resp_arr[valid]))

        if not all_resp:
            continue

        # 合并所有谐振子, 按 VNA 功率分桶取中位数
        import collections
        buckets = collections.defaultdict(list)
        for pv_a, resp_a in all_resp:
            for p, r in zip(pv_a, resp_a):
                buckets[p].append(r)

        pv_sorted = sorted(buckets.keys())
        medians = [np.median(buckets[p]) for p in pv_sorted]
        p25 = [np.percentile(buckets[p], 25) for p in pv_sorted]
        p75 = [np.percentile(buckets[p], 75) for p in pv_sorted]

        color = TEMP_COLORS[temp_k]
        ax_right.plot(pv_sorted, medians, color=color, linewidth=2.5,
                      label=f"{temp_k} K")
        ax_right.fill_between(pv_sorted, p25, p75, color=color, alpha=0.15)

    ax_right.axhline(y=0, color="gray", linewidth=0.8, linestyle=":")
    ax_right.set_xlabel("VNA Power (dBm)", fontsize=12)
    ax_right.set_ylabel("Responsivity (ppm/mW)", fontsize=12)
    ax_right.set_title("Median Responsivity (R1-R5) ± IQR", fontsize=13, fontweight="bold")
    ax_right.legend(fontsize=9)
    ax_right.grid(True, alpha=0.3)

    fig1.suptitle("C1: KID Responsivity — VNA Power Dependence",
                  fontsize=15, fontweight="bold")
    fig1.tight_layout()
    out1 = os.path.join(output_dir, "C1_responsivity_vs_VNA.png")
    fig1.savefig(out1, dpi=150, bbox_inches="tight")
    print(f"  [C] {os.path.basename(out1)}")
    plt.close(fig1)

    # ---- 图 2: 最优 VNA 下的 δf/f₀ vs 激光功率 ----
    # 对每个 (温度, 谐振子) 选 R² 最高且响应最强的 VNA 功率
    fig2, axes2 = plt.subplots(
        len(TEMPERATURES_K), len(RESONATOR_NAMES),
        figsize=(22, 18), dpi=150,
        sharex=True,
    )

    for i_row, temp_k in enumerate(TEMPERATURES_K):
        if temp_k not in all_data:
            continue
        for j_col, rname in enumerate(RESONATOR_NAMES):
            ax = axes2[i_row, j_col]
            if rname not in all_data[temp_k]:
                ax.text(0.5, 0.5, "N/A", transform=ax.transAxes, ha="center", va="center")
                continue

            data = all_data[temp_k][rname]
            resp = extract_responsivity(data)
            pv_arr = np.array(resp["pv_list"])
            resp_arr = np.array(resp["responsivity_ppm_per_mw"])
            r2_arr = np.array(resp["r2_scores"])

            # 选最优: R² > 0.7 且 |响应率| 最大
            good = ~np.isnan(resp_arr) & (r2_arr > 0.7)
            if good.sum() == 0:
                # 放宽: 任何 R² > 0.5
                good = ~np.isnan(resp_arr) & (r2_arr > 0.5)
            if good.sum() == 0:
                ax.text(0.5, 0.5, "No fit", transform=ax.transAxes, ha="center", va="center")
                continue

            # 选响应率绝对值最大的
            best_idx = np.argmax(np.abs(resp_arr[good]))
            best_i = np.where(good)[0][best_idx]
            best_pv = pv_arr[best_i]

            # 画该 VNA 功率的 δf/f₀ vs Pl
            dff = data["delta_f_over_f"]
            flags = data["flags"]
            pl_list = data["pl_list"]

            y_raw = dff[best_i, :] * 1e6  # ppm
            fl = flags[best_i, :]
            valid = ~np.isnan(y_raw) & (fl != "lost")
            if valid.sum() < 2:
                continue

            x_data = np.array(pl_list)[valid]
            y_data = y_raw[valid]
            y_smooth = smooth_curve(y_data)
            mask_finite = np.isfinite(y_smooth)
            if mask_finite.sum() < 2:
                continue

            x_plot = x_data[mask_finite]
            y_plot = y_smooth[mask_finite]

            coeffs, _ = robust_polyfit(x_plot, y_plot, deg=1)
            color = TEMP_COLORS[temp_k]

            ax.scatter(x_plot, y_plot, color=color, s=25, alpha=0.7, zorder=3, edgecolors="none")
            ax.plot(x_plot, y_plot, color=color, linewidth=2, alpha=0.7)

            if coeffs is not None:
                x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
                y_fit = np.polyval(coeffs, x_fit)
                ax.plot(x_fit, y_fit, color="black", linewidth=1.2, alpha=0.5, linestyle="--")

            ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
            ax.grid(True, alpha=0.25)

            resp_val = resp_arr[best_i]
            r2_val = r2_arr[best_i]
            ax.text(0.95, 0.05,
                    f"Pv={best_pv}dBm\nR={resp_val:.1f} ppm/mW\nR²={r2_val:.2f}",
                    transform=ax.transAxes, fontsize=7, va="bottom", ha="right",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.7))

            if j_col == 0:
                ax.set_ylabel(f"{temp_k} K\ndf/f (ppm)", fontsize=9)
            if i_row == 0:
                ax.set_title(rname, fontsize=11, fontweight="bold")
            if i_row == len(TEMPERATURES_K) - 1:
                ax.set_xlabel("Pl (mW)", fontsize=8)

    fig2.suptitle("C2: df/f vs Laser Power — Best VNA Power per (T, Resonator)",
                  fontsize=16, fontweight="bold", y=1.01)
    fig2.tight_layout()
    out2 = os.path.join(output_dir, "C2_bestVNA_dff_vs_laser.png")
    fig2.savefig(out2, dpi=150, bbox_inches="tight")
    print(f"  [C] {os.path.basename(out2)}")
    plt.close(fig2)

# =========================================================================
# 主入口
# =========================================================================

def main():
    print("=" * 60)
    print("Three Approach Demo — df/f Visualization")
    print("=" * 60)

    # ---- 收集全部数据 ----
    print("\n[Collecting data...]")

    # all_data[temp_k][rname] = data_dict
    all_data: Dict[int, Dict[str, Dict]] = {}

    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir):
            continue

        all_data[T_K] = {}

        # Find reference
        if T_K >= 70:
            ref_fp = None
            for pv_try in [-25, -35, -45, -55]:
                ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, 9)
                if ref_fp is not None:
                    break
        else:
            ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)
        if ref_fp is None:
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

        resonators = identify_resonators(ref_freq * 1e9, ref_s21_db, ref_s21_cplx, T_K)
        n_found = sum(1 for r in resonators if r["f0_ghz"] is not None)
        print(f"  T={T_K}K: {n_found}/5 found")

        for r in resonators:
            if r["f0_ghz"] is None:
                continue
            data = collect_data_for_deltaf(temp_dir, T_K, r["name"])
            n_tracked = np.sum(data["flags"] == "tracked")
            if n_tracked >= 3:
                all_data[T_K][r["name"]] = data

    # ---- 方案 A: 响应率 vs VNA (R1, R3, R5) ----
    print("\n[Approach A: Responsivity vs VNA Power]")
    out_a = os.path.join(_OUTPUT_BASE, "approach_A_responsivity")
    for rname in ["R1", "R3", "R5"]:
        plot_approach_a(all_data, rname,
                        os.path.join(out_a, f"responsivity_vs_VNA_{rname}.png"))

    # ---- 方案 B: 5×5 网格 ----
    print("\n[Approach B: Grid Layout]")
    out_b = os.path.join(_OUTPUT_BASE, "approach_B_grid")
    plot_approach_b_grid(all_data, os.path.join(out_b, "grid_5x5_overview.png"))

    # ---- 方案 C: 双图分离 ----
    print("\n[Approach C: Two-Panel Design]")
    out_c = os.path.join(_OUTPUT_BASE, "approach_C_twopanel")
    plot_approach_c_twopanel(all_data, out_c)

    print("\n" + "=" * 60)
    print("Done! Output directories:")
    print(f"  A: {out_a}")
    print(f"  B: {out_b}")
    print(f"  C: {out_c}")
    print("=" * 60)


if __name__ == "__main__":
    main()
