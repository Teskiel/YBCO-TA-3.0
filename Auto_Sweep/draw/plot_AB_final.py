# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 使用 plot_all.py --approaches A,B 替代。
# 此文件保留仅供代码参考，不再维护。
"""方案 A + B 最终版 — 用户要求：
  - 标题标注谐振子名 + 实际谐振频率
  - 每条线清晰标注 VNA 功率 (dBm)
  - 6 条等间距 VNA 曲线: {-55, -49, -43, -37, -31, -25} dBm (6dB 间隔)
  - 使用 T6-77K_VNA-55~-25dBm_step2dB 数据

输出:
    approach_A_responsivity/   → 每谐振子一张: 响应率 vs VNA 功率
    approach_B_grid/           → 5×5 网格 + 25 单独大图
"""

import os
import sys
import re
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

# 从 16 级 VNA 功率中等间距选 6 条 (6dB 间隔)
SELECTED_VNA_DBM = [-55, -49, -43, -37, -31, -25]

SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3
OUTLIER_SIGMA = 2.0

_OUTPUT_BASE = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser"

# 6 条 VNA 线的配色: 深蓝→青→绿→橙→红 (6 档 jet)
VNA_6_COLORS = ["#0D47A1", "#00838F", "#2E7D32", "#E65100", "#BF360C", "#D32F2F"]

# 温度配色
TEMP_COLORS = {6: "#1565C0", 10: "#0097A7", 20: "#4CAF50", 40: "#FF9800", 77: "#D32F2F"}


# =====================================================================
# 工具函数
# =====================================================================

def extract_vna_power_from_path(path: str):
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
        if extract_vna_power_from_path(fp) == target_pv: return fp
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
# 数据收集 — 仅收集 SELECTED_VNA_DBM 的 6 条
# =====================================================================

def collect_selected_data(temp_dir, temp_k, resonator_name, f0_identified):
    """收集 δf/f₀ 数据，仅针对选定的 6 个 VNA 功率。

    Args:
        f0_identified: identify_resonators 返回的权威 f0 (GHz)，用作 Pl=0 追踪锚点
    """
    vna_powers = SELECTED_VNA_DBM
    n_pv = len(vna_powers)
    n_pl = len(LASER_POWERS_MW)
    delta_f_over_f = np.full((n_pv, n_pl), np.nan)
    flags = np.full((n_pv, n_pl), "lost", dtype=object)
    f0_refs = {}
    pl0_idx = LASER_POWERS_MW.index(0)

    # Step 1: Pl=0 定位参考 f0 — 用权威 f0 做强先验锚点
    from _tracking_utils import track_one_resonator, SCRAPS_TEMPS, detect_dip_p90
    f0_at_pl0 = {}
    for i_pv, pv in enumerate(vna_powers):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None: continue
        loaded = load_s2p_complex(fp)
        if loaded is None: continue
        freq, s21_cplx, s21_db = loaded
        freq_ghz = freq

        # 优先: 在权威 f0 附近搜索 dip (窄窗口)
        if f0_identified is not None:
            f_dip, dip_depth, baseline = detect_dip_p90(
                freq_ghz, s21_db, f0_identified, search_mhz=30.0)
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
            # 额外检查: 追踪结果不能离权威 f0 太远 (>50 MHz 则拒绝)
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

    # 返回: 使用 identify_resonators 的权威 f0 作为标题频率
    return {
        "pv_list": vna_powers,
        "pl_list": LASER_POWERS_MW,
        "delta_f_over_f": delta_f_over_f,
        "flags": flags,
        "f0_ghz": f0_identified,  # 标题用权威 f0
    }

# =====================================================================
# 提取响应率 (方案 A 用)
# =====================================================================

def extract_responsivity(data):
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
            responsivity.append(np.nan); r2_scores.append(np.nan); n_points_list.append(0)
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
    return {"pv_list": pv_list, "responsivity_ppm_per_mw": responsivity,
            "r2_scores": r2_scores, "n_points": n_points_list}

# =====================================================================
# 画单格 (方案 B 共用)
# =====================================================================

def draw_single_cell(ax, data, resonator_name, temp_k, show_legend=True, fit_only=False):
    """在 ax 上画 δf/f₀ vs Pl, 6 条 VNA 曲线。

    Args:
        fit_only: True=仅画拟合实线, 无散点和原始连线
    """
    pv_list = data["pv_list"]
    pl_list = data["pl_list"]
    dff = data["delta_f_over_f"]
    flags = data["flags"]

    for i_pv, pv in enumerate(pv_list):
        y_raw = dff[i_pv, :] * 1e6
        fl = flags[i_pv, :]
        valid = ~np.isnan(y_raw) & (fl != "lost")
        if valid.sum() < 2: continue

        x_data = np.array(pl_list)[valid]
        y_data = y_raw[valid]

        y_smooth = smooth_curve(y_data)
        mask_ok = np.isfinite(y_smooth)
        if mask_ok.sum() < 2: continue
        x_plot = x_data[mask_ok]
        y_plot = y_smooth[mask_ok]

        # 拟合
        try:
            coeffs = np.polyfit(x_plot, y_plot, 1)
            y_pred = np.polyval(coeffs, x_plot)
            ss_res = np.sum((y_plot - y_pred)**2)
            ss_tot = np.sum((y_plot - np.mean(y_plot))**2)
            r2 = 1 - ss_res/ss_tot if ss_tot > 1e-15 else 0
        except:
            coeffs, r2 = None, 0

        color = VNA_6_COLORS[i_pv % len(VNA_6_COLORS)]
        label = f"{pv} dBm" if show_legend else None

        if fit_only:
            # 仅拟合线: 实线
            if coeffs is not None:
                x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
                y_fit = np.polyval(coeffs, x_fit)
                ax.plot(x_fit, y_fit, '-', color=color, linewidth=2.5, alpha=0.9, label=label)
        else:
            # 散点 + 原始连线 + 拟合虚线
            ax.scatter(x_plot, y_plot, color=color, s=15, alpha=0.7, zorder=3, edgecolors="none")
            ax.plot(x_plot, y_plot, '-', color=color, linewidth=2, alpha=0.8, label=label)
            if coeffs is not None:
                x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
                y_fit = np.polyval(coeffs, x_fit)
                ax.plot(x_fit, y_fit, '--', color=color, linewidth=1, alpha=0.4)

    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
    ax.grid(True, alpha=0.25)
    ax.set_xlabel("Laser Power (mW)", fontsize=9)

    # 纵轴: ppm 或科学记数
    ax.set_ylabel("df/f (ppm)", fontsize=9)


# =====================================================================
# 方案 A: 响应率 vs VNA 功率
# =====================================================================

def plot_approach_A(all_data, resonator_name, f0_6k, output_path):
    """响应率 vs VNA 功率，曲线 = 温度。
    高温(>=70K)使用宽松 R² 阈值 (>0.2)，低置信度点用虚线标记。
    """
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)

    for temp_k in TEMPERATURES_K:
        if temp_k not in all_data or resonator_name not in all_data[temp_k]: continue
        data = all_data[temp_k][resonator_name]
        resp = extract_responsivity(data)

        pv_arr = np.array(resp["pv_list"])
        resp_arr = np.array(resp["responsivity_ppm_per_mw"])
        r2_arr = np.array(resp["r2_scores"])

        # 高温近 Tc 时放宽阈值以显示数据
        r2_threshold = 0.2 if temp_k >= 70 else 0.5
        valid = ~np.isnan(resp_arr) & (r2_arr > r2_threshold)
        if valid.sum() < 2: continue

        pv_v = pv_arr[valid]
        resp_v = resp_arr[valid]
        r2_v = r2_arr[valid]

        color = TEMP_COLORS[temp_k]
        # 低置信度点 (R²<0.5) 用不同标记
        high_conf = r2_v >= 0.5
        low_conf = ~high_conf

        # 高置信度散点
        if high_conf.sum() > 0:
            ax.scatter(pv_v[high_conf], resp_v[high_conf], color=color, alpha=0.8, s=40, zorder=3)
        # 低置信度散点: 空心 + 小
        if low_conf.sum() > 0:
            ax.scatter(pv_v[low_conf], resp_v[low_conf], facecolors='none', edgecolors=color,
                       alpha=0.5, s=60, marker='s', linewidths=1.5, zorder=3)

        sort_idx = np.argsort(pv_v)
        linestyle = '--' if low_conf.sum() > 0 else '-'
        label = f"{temp_k} K" + (" *" if low_conf.sum() > 0 else "")
        ax.plot(pv_v[sort_idx], resp_v[sort_idx], linestyle, color=color, linewidth=2, alpha=0.7,
                marker='', label=label)

    ax.axhline(y=0, color="gray", linewidth=0.8, linestyle=":")
    ax.set_xlabel("VNA Readout Power (dBm)", fontsize=12)
    ax.set_ylabel("Responsivity d(df/f)/dP_laser (ppm/mW)", fontsize=12)
    ax.set_title(f"KID Responsivity — {resonator_name}  (f$_0$ = {f0_6k:.3f} GHz at 6 K)\n"
                 "* = low confidence (R\\u00b2 < 0.5, near-Tc SNR limited)",
                 fontsize=13, fontweight="bold")
    ax.legend(title="Temperature", loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  [A] {os.path.basename(output_path)}")
    plt.close(fig)

# =====================================================================
# 方案 B: 5x5 网格
# =====================================================================

def plot_approach_B_grid(all_data, output_path):
    """5×5 网格，标题含频率，行首标注温度。"""
    n_rows, n_cols = len(TEMPERATURES_K), len(RESONATOR_NAMES)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(28, 22), dpi=150,
                             sharex=True, sharey="row")

    for i_row, temp_k in enumerate(TEMPERATURES_K):
        for j_col, rname in enumerate(RESONATOR_NAMES):
            ax = axes[i_row, j_col]
            if temp_k not in all_data or rname not in all_data[temp_k]:
                ax.text(0.5, 0.5, "N/A", transform=ax.transAxes, ha="center", va="center")
                continue

            data = all_data[temp_k][rname]
            f0 = data.get("f0_ghz")
            freq_str = f"f$_0$={f0:.3f} GHz" if f0 else ""

            draw_single_cell(ax, data, rname, temp_k, show_legend=False)

            # 标题: 谐振子名 + 频率
            ax.set_title(f"{rname}  ({freq_str})", fontsize=9, fontweight="bold")

            # 77K 行加低 SNR 标记
            if temp_k >= 70:
                ax.text(0.95, 0.05, "low SNR", transform=ax.transAxes,
                        fontsize=6, color="#D32F2F", ha="right", va="bottom",
                        style="italic")

            # 行首: 温度
            if j_col == 0:
                ax.set_ylabel(f"{temp_k} K\ndf/f (ppm)", fontsize=9,
                              color=TEMP_COLORS[temp_k], fontweight="bold")

    # 顶部图例
    legend_elements = [
        plt.Line2D([0], [0], color=VNA_6_COLORS[i], linewidth=2, label=f"{pv} dBm")
        for i, pv in enumerate(SELECTED_VNA_DBM)
    ]
    fig.legend(handles=legend_elements, loc="upper center", ncol=6,
               fontsize=10, title="VNA Readout Power", title_fontsize=11,
               bbox_to_anchor=(0.5, 1.01))

    fig.suptitle("df/f vs Laser Power — 6 VNA power levels (6 dB spacing)",
                 fontsize=16, fontweight="bold", y=1.03)
    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"  [B] grid: {os.path.basename(output_path)}")
    plt.close(fig)

# =====================================================================
# 方案 B: 单独导出每格
# =====================================================================

def export_individual_B(all_data, output_dir):
    """每格一张大图，包含完整图例。"""
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    for temp_k in TEMPERATURES_K:
        for rname in RESONATOR_NAMES:
            if temp_k not in all_data or rname not in all_data[temp_k]: continue
            data = all_data[temp_k][rname]
            f0 = data.get("f0_ghz")
            freq_str = f"f$_0$ = {f0:.3f} GHz" if f0 else ""

            fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
            draw_single_cell(ax, data, rname, temp_k, show_legend=True)

            ax.set_title(f"{rname}  ({freq_str})  @  {temp_k} K  |  df/f vs Laser Power",
                         fontsize=13, fontweight="bold")
            # 77K 低 SNR 警告
            if temp_k >= 70:
                ax.text(0.5, 0.97, "LOW SNR near Tc — tracking uncertain",
                        transform=ax.transAxes, fontsize=9, color="#D32F2F",
                        ha="center", va="top", style="italic",
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="#FFF3E0", alpha=0.8))
            ax.legend(title="VNA Power (dBm)", loc="lower left", fontsize=8, ncol=2)

            fname = f"T{temp_k}K_{rname}.png"
            fig.savefig(os.path.join(output_dir, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            count += 1
    print(f"  [B] individual: {count} cells")

# =====================================================================
# 方案 B: 仅拟合线版本
# =====================================================================

def export_individual_B_fit_only(all_data, output_dir):
    """每格一张大图，仅画拟合实线，无散点无虚线。"""
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    for temp_k in TEMPERATURES_K:
        for rname in RESONATOR_NAMES:
            if temp_k not in all_data or rname not in all_data[temp_k]: continue
            data = all_data[temp_k][rname]
            f0 = data.get("f0_ghz")
            freq_str = f"f$_0$ = {f0:.3f} GHz" if f0 else ""

            fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
            draw_single_cell(ax, data, rname, temp_k, show_legend=True, fit_only=True)

            ax.set_title(f"{rname}  ({freq_str})  @  {temp_k} K  |  df/f vs Laser Power  (fit only)",
                         fontsize=13, fontweight="bold")
            ax.legend(title="VNA Power (dBm)", loc="lower left", fontsize=8, ncol=2)

            fname = f"T{temp_k}K_{rname}.png"
            fig.savefig(os.path.join(output_dir, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            count += 1
    print(f"  [B] fit-only: {count} cells")

# =====================================================================
# Main
# =====================================================================

def main():
    print("=" * 60)
    print("Approach A+B Final — 6 VNA lines, frequency titles, dBm legend")
    print(f"Data: {EXPERIMENT_DATA_DIR}")
    print(f"Selected VNA (6 lines): {SELECTED_VNA_DBM} dBm")
    print("=" * 60)

    # ---- 收集数据 ----
    print("\n[Collecting data...]")
    all_data = {}
    f0_6k_map = {}
    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir): continue
        all_data[T_K] = {}

        # 参照 S21 overlay 的选择策略: 用 Pl=0, 高温用最高 VNA 功率 (更深 dip)
        if T_K >= 70:
            ref_pl = 0
            # 选最高 VNA 功率 (-25 dBm, dip 最深)
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
            f0_id = r["f0_ghz"]  # identify_resonators 权威 f0
            data = collect_selected_data(temp_dir, T_K, r["name"], f0_id)
            n_tracked = np.sum(data["flags"] == "tracked")
            if n_tracked >= 3:
                all_data[T_K][r["name"]] = data
                if T_K == 6:
                    f0_6k_map[r["name"]] = f0_id

    # ---- 方案 A ----
    print("\n[Approach A: Responsivity vs VNA Power]")
    out_a = os.path.join(_OUTPUT_BASE, "approach_A_responsivity")
    for rname in ["R1", "R2", "R3", "R4", "R5"]:
        f0_6k = f0_6k_map.get(rname)
        plot_approach_A(all_data, rname, f0_6k, os.path.join(out_a, f"responsivity_vs_VNA_{rname}.png"))

    # ---- 方案 B ----
    print("\n[Approach B: Grid + Individual]")
    out_b = os.path.join(_OUTPUT_BASE, "approach_B_grid")
    plot_approach_B_grid(all_data, os.path.join(out_b, "grid_5x5_overview.png"))
    export_individual_B(all_data, os.path.join(out_b, "individual"))
    export_individual_B_fit_only(all_data, os.path.join(out_b, "individual_fit_only"))

    print("\nDone!")
    print(f"  A: {out_a}/")
    print(f"  B: {out_b}/")

if __name__ == "__main__":
    main()
