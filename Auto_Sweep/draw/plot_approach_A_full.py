# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 使用 plot_all.py --approaches A --style both 替代。
# 此文件保留仅供代码参考，不再维护。
"""方案 A 完整版: 响应率 vs VNA 功率 — 使用全部 16 级 VNA 功率 (2dB 间隔, -55~-25 dBm)。

输出两套图:
  1. approach_A_full/          → 散点 + 连线 + 拟合趋势线 (完整版)
  2. approach_A_full_fit_only/ → 仅拟合趋势线 (简洁版, 仿 individual_fit_only 风格)

用法:
    python draw/plot_approach_A_full.py
"""

import os
import sys
import re
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter
import skrf as rf

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    identify_resonators, track_across_pl, RESONATOR_NAMES, SCRAPS_F0,
)

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]

SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3
OUTLIER_SIGMA = 2.0

_OUTPUT_BASE = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser"

# 温度配色
TEMP_COLORS = {6: "#1565C0", 10: "#0097A7", 20: "#4CAF50", 40: "#FF9800", 77: "#D32F2F"}

# =====================================================================
# 工具函数
# =====================================================================

def extract_vna_power_from_path(path: str) -> Optional[int]:
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None

def load_s2p_complex(file_path):
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

# =====================================================================
# 数据收集 — 使用全部可用 VNA 功率
# =====================================================================

def collect_all_vna_data(temp_dir, temp_k, resonator_name, f0_identified):
    """收集 δf/f₀ 数据，遍历数据目录中全部可用的 VNA 功率。

    Args:
        f0_identified: identify_resonators 返回的权威 f0 (GHz)，用作 Pl=0 追踪锚点
    """
    from _tracking_utils import track_one_resonator, SCRAPS_TEMPS, detect_dip_p90

    # 发现该温度下全部可用的 VNA 功率
    all_pv = set()
    for Pl_mW in LASER_POWERS_MW:
        for fp in find_s2p_files_for_laser_power(temp_dir, Pl_mW):
            pv = extract_vna_power_from_path(fp)
            if pv is not None:
                all_pv.add(pv)
    vna_powers = sorted(all_pv)

    n_pv = len(vna_powers)
    n_pl = len(LASER_POWERS_MW)
    delta_f_over_f = np.full((n_pv, n_pl), np.nan)
    flags = np.full((n_pv, n_pl), "lost", dtype=object)
    f0_refs = {}
    pl0_idx = LASER_POWERS_MW.index(0)

    # Step 1: Pl=0 定位参考 f0
    f0_at_pl0 = {}
    for i_pv, pv in enumerate(vna_powers):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None: continue
        loaded = load_s2p_complex(fp)
        if loaded is None: continue
        freq, s21_cplx, s21_db = loaded

        # 优先: 在权威 f0 附近搜索 dip (窄窗口)
        if f0_identified is not None:
            f_dip, dip_depth, baseline = detect_dip_p90(
                freq, s21_db, f0_identified, search_mhz=30.0)
            if f_dip is not None and dip_depth < -0.5:
                f0_at_pl0[pv] = f_dip
                f0_refs[pv] = f_dip
                delta_f_over_f[i_pv, pl0_idx] = 0.0
                flags[i_pv, pl0_idx] = "tracked"
                continue

        # 备选: FD 预测追踪
        f0_history_local = []
        for prev_pv in vna_powers[:i_pv]:
            if prev_pv in f0_at_pl0 and f0_at_pl0[prev_pv] is not None:
                f0_history_local.append((temp_k, f0_at_pl0[prev_pv]))
        skeleton_f0 = SCRAPS_F0[resonator_name]
        base_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0) if t <= temp_k + 15]
        full_history = base_history + f0_history_local[-3:]

        f0, dip = track_one_resonator(freq * 1e9, s21_db, s21_cplx, resonator_name, temp_k, full_history)
        if f0 is not None:
            if f0_identified is not None and abs(f0 - f0_identified) > 0.050:
                continue
            f0_at_pl0[pv] = f0
            f0_refs[pv] = f0
            delta_f_over_f[i_pv, pl0_idx] = 0.0
            flags[i_pv, pl0_idx] = "tracked"

    # Step 2: 跨 Pl 追踪
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

    return {
        "pv_list": vna_powers,
        "pl_list": LASER_POWERS_MW,
        "delta_f_over_f": delta_f_over_f,
        "flags": flags,
        "f0_ghz": f0_identified,
    }

# =====================================================================
# 提取响应率
# =====================================================================

def extract_responsivity(data):
    """为每条 VNA 功率线提取斜率 = d(df/f)/dP_laser (ppm/mW)。

    返回每个 VNA 功率点的: 斜率, R², 有效数据点数。
    """
    pv_list = data["pv_list"]
    pl_list = data["pl_list"]
    dff = data["delta_f_over_f"]
    flags = data["flags"]

    responsivity, r2_scores, n_points_list = [], [], []
    for i_pv, pv in enumerate(pv_list):
        y_raw = dff[i_pv, :] * 1e6
        fl = flags[i_pv, :]
        valid_mask = ~np.isnan(y_raw) & (fl != "lost")
        if valid_mask.sum() < 3:
            responsivity.append(np.nan)
            r2_scores.append(np.nan)
            n_points_list.append(0)
            continue
        x_data = np.array(pl_list)[valid_mask].astype(float)
        y_data = y_raw[valid_mask].astype(float)
        coeffs = np.polyfit(x_data, y_data, 1)
        y_pred = np.polyval(coeffs, x_data)
        ss_res = np.sum((y_data - y_pred) ** 2)
        ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-15 else 0
        responsivity.append(coeffs[0])
        r2_scores.append(max(0, r2))
        n_points_list.append(len(x_data))
    return {
        "pv_list": pv_list,
        "responsivity_ppm_per_mw": responsivity,
        "r2_scores": r2_scores,
        "n_points": n_points_list,
    }

# =====================================================================
# 绘图: 完整版 (散点 + 连线 + 拟合趋势)
# =====================================================================

def plot_approach_A_full(ax, temp_resp_data, resonator_name, f0_6k, show_legend=True):
    """在 ax 上画响应率 vs VNA 功率 — 完整版: 散点 + 连线 + 趋势拟合。

    temp_resp_data: {temp_k: resp_dict, ...}
    """
    for temp_k in TEMPERATURES_K:
        if temp_k not in temp_resp_data: continue
        resp = temp_resp_data[temp_k]

        pv_arr = np.array(resp["pv_list"])
        resp_arr = np.array(resp["responsivity_ppm_per_mw"])
        r2_arr = np.array(resp["r2_scores"])

        # R² 阈值
        r2_threshold = 0.2 if temp_k >= 70 else 0.5
        valid = ~np.isnan(resp_arr) & (r2_arr > r2_threshold)
        if valid.sum() < 2: continue

        pv_v = pv_arr[valid]
        resp_v = resp_arr[valid]
        r2_v = r2_arr[valid]

        color = TEMP_COLORS[temp_k]

        # 高低置信度分标记
        high_conf = r2_v >= 0.5
        low_conf = ~high_conf

        # 高置信度: 实心圆
        if high_conf.sum() > 0:
            ax.scatter(pv_v[high_conf], resp_v[high_conf], color=color,
                       alpha=0.8, s=40, zorder=3, edgecolors="none")

        # 低置信度: 空心方块
        if low_conf.sum() > 0:
            ax.scatter(pv_v[low_conf], resp_v[low_conf],
                       facecolors='none', edgecolors=color,
                       alpha=0.5, s=60, marker='s', linewidths=1.5, zorder=3)

        # 连线 (按 VNA 功率排序)
        sort_idx = np.argsort(pv_v)
        linestyle = '--' if low_conf.sum() > 0 else '-'
        label = f"{temp_k} K" + (" *" if low_conf.sum() > 0 else "")
        ax.plot(pv_v[sort_idx], resp_v[sort_idx], linestyle, color=color,
                linewidth=2, alpha=0.7, marker='', label=label)

    ax.axhline(y=0, color="gray", linewidth=0.8, linestyle=":")
    ax.set_xlabel("VNA Readout Power (dBm)", fontsize=12)
    ax.set_ylabel("Responsivity d(df/f)/dP_laser (ppm/mW)", fontsize=12)
    ax.set_title(f"KID Responsivity — {resonator_name}  (f$_0$ = {f0_6k:.3f} GHz at 6 K)\n"
                 f"All 2dB-step VNA powers  |  * = low confidence (R\\u00b2 < 0.5)",
                 fontsize=12, fontweight="bold")
    if show_legend:
        ax.legend(title="Temperature", loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)

# =====================================================================
# 绘图: 仅拟合线版 (仿 individual_fit_only 风格)
# =====================================================================

def plot_approach_A_fit_only(ax, temp_resp_data, resonator_name, f0_6k, show_legend=True):
    """在 ax 上画响应率 vs VNA 功率 — 仅拟合趋势线，无散点无虚线。

    对每个温度的有效响应率点做 Savitzky-Golay 平滑 → polyfit 趋势线。
    """
    for temp_k in TEMPERATURES_K:
        if temp_k not in temp_resp_data: continue
        resp = temp_resp_data[temp_k]

        pv_arr = np.array(resp["pv_list"])
        resp_arr = np.array(resp["responsivity_ppm_per_mw"])
        r2_arr = np.array(resp["r2_scores"])

        r2_threshold = 0.2 if temp_k >= 70 else 0.5
        valid = ~np.isnan(resp_arr) & (r2_arr > r2_threshold)
        if valid.sum() < 2: continue

        pv_v = pv_arr[valid]
        resp_v = resp_arr[valid]

        # 按 x 排序后平滑 + 拟合
        sort_idx = np.argsort(pv_v)
        x_sorted = pv_v[sort_idx]
        y_sorted = resp_v[sort_idx]

        # SG 平滑
        y_smooth = smooth_curve(y_sorted)
        mask_ok = np.isfinite(y_smooth)
        if mask_ok.sum() < 2: continue
        x_plot = x_sorted[mask_ok]
        y_plot = y_smooth[mask_ok]

        # 线性拟合
        try:
            coeffs = np.polyfit(x_plot, y_plot, 1)
        except:
            continue

        color = TEMP_COLORS[temp_k]
        label = f"{temp_k} K" if show_legend else None

        x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
        y_fit = np.polyval(coeffs, x_fit)
        ax.plot(x_fit, y_fit, '-', color=color, linewidth=2.5, alpha=0.9, label=label)

    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
    ax.set_xlabel("VNA Readout Power (dBm)", fontsize=12)
    ax.set_ylabel("Responsivity d(df/f)/dP_laser (ppm/mW)", fontsize=12)
    ax.set_title(f"KID Responsivity — {resonator_name}  (f$_0$ = {f0_6k:.3f} GHz at 6 K)\n"
                 f"All 2dB-step VNA powers  |  trend lines only",
                 fontsize=12, fontweight="bold")
    if show_legend:
        ax.legend(title="Temperature", loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)

# =====================================================================
# Main
# =====================================================================

def main():
    print("=" * 60)
    print("Approach A — FULL: All 16 VNA power levels (2dB step)")
    print(f"Data: {EXPERIMENT_DATA_DIR}")
    print("=" * 60)

    # ---- 收集数据 ----
    print("\n[Collecting data — all VNA powers...]")
    all_data = {}
    f0_6k_map = {}
    total_pv_levels = {}

    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir): continue
        all_data[T_K] = {}

        # 参照 plot_AB_final.py 的选择策略
        if T_K >= 70:
            ref_pl = 0
            ref_fp = None
            for pv_try in [-25, -27, -29, -31, -35, -45, -55]:
                ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, ref_pl)
                if ref_fp is not None: break
        else:
            ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)
        if ref_fp is None:
            all_pv_set = set()
            for pl in LASER_POWERS_MW:
                for f in find_s2p_files_for_laser_power(temp_dir, pl):
                    pv = extract_vna_power_from_path(f)
                    if pv is not None: all_pv_set.add(pv)
            if not all_pv_set: continue
            ref_fp = find_s2p_for_pv_pl(temp_dir, min(all_pv_set), 0)
            if ref_fp is None: continue

        ref_loaded = load_s2p_complex(ref_fp)
        if ref_loaded is None: continue
        ref_freq, ref_s21_cplx, ref_s21_db = ref_loaded
        resonators = identify_resonators(ref_freq * 1e9, ref_s21_db, ref_s21_cplx, T_K)
        n_found = sum(1 for r in resonators if r["f0_ghz"] is not None)
        print(f"  T={T_K}K (ref Pv={extract_vna_power_from_path(ref_fp)}dBm Pl=0): {n_found}/5 found")

        for r in resonators:
            if r["f0_ghz"] is None: continue
            f0_id = r["f0_ghz"]
            data = collect_all_vna_data(temp_dir, T_K, r["name"], f0_id)
            n_tracked = np.sum(data["flags"] == "tracked")
            if n_tracked >= 3:
                all_data[T_K][r["name"]] = data
                total_pv_levels[T_K] = len(data["pv_list"])
                if T_K == 6:
                    f0_6k_map[r["name"]] = f0_id

    print(f"\n  VNA power levels per temperature: {total_pv_levels}")

    # ---- 提取响应率 ----
    print("\n[Extracting responsivity...]")
    resp_data = {}  # {resonator_name: {temp_k: resp_dict}}
    for rname in RESONATOR_NAMES:
        resp_data[rname] = {}
        for T_K in TEMPERATURES_K:
            if T_K in all_data and rname in all_data[T_K]:
                resp = extract_responsivity(all_data[T_K][rname])
                n_valid = sum(1 for r, r2 in zip(resp["responsivity_ppm_per_mw"], resp["r2_scores"])
                             if not np.isnan(r) and r2 > (0.2 if T_K >= 70 else 0.5))
                if n_valid >= 2:
                    resp_data[rname][T_K] = resp
                    print(f"  {rname} @ {T_K}K: {n_valid}/{len(resp['pv_list'])} valid points")

    # ---- 生成完整版 ----
    print("\n[Generating FULL version — scatter + lines + trend...]")
    out_full = os.path.join(_OUTPUT_BASE, "approach_A_full")
    os.makedirs(out_full, exist_ok=True)

    for rname in RESONATOR_NAMES:
        f0_6k = f0_6k_map.get(rname)
        fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
        plot_approach_A_full(ax, resp_data[rname], rname, f0_6k)
        fig.tight_layout()
        out_path = os.path.join(out_full, f"responsivity_vs_VNA_{rname}.png")
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  [full] {os.path.basename(out_path)}")

    # ---- 生成仅拟合线版 ----
    print("\n[Generating FIT-ONLY version — trend lines only...]")
    out_fit = os.path.join(_OUTPUT_BASE, "approach_A_full_fit_only")
    os.makedirs(out_fit, exist_ok=True)

    for rname in RESONATOR_NAMES:
        f0_6k = f0_6k_map.get(rname)
        fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
        plot_approach_A_fit_only(ax, resp_data[rname], rname, f0_6k)
        fig.tight_layout()
        out_path = os.path.join(out_fit, f"responsivity_vs_VNA_{rname}_fitonly.png")
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  [fit] {os.path.basename(out_path)}")

    print(f"\nDone!")
    print(f"  Full version: {out_full}/")
    print(f"  Fit-only version: {out_fit}/")

if __name__ == "__main__":
    main()
