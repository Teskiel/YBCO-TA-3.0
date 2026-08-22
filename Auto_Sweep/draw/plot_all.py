# -*- coding: utf-8 -*-
"""统一画图入口 — 从缓存读取数据，生成所有方案图表。

所有方案共用同一缓存，仅参数不同:
  方案 A: 响应率 d(df/f)/dP_laser vs VNA 功率 (每谐振子一张，温度曲线)
  方案 B: df/f vs Laser Power 网格 (每 T×R 一格，VNA 功率曲线)
  S21 overlay: S21 全谱叠加 (固定 T 和 Pl，变化 Pv)

用法:
    # 全部方案，全部 VNA 功率，两种风格
    python draw/plot_all.py --cache "D:/.../output/_cache/_cache_XXX.pkl"

    # 仅方案 B，9 条 VNA 线 (2dB 间隔)，仅拟合线
    python draw/plot_all.py --cache "..." --approaches B \\
        --vna-powers -55,-53,-51,-49,-47,-45,-43,-41,-39 --style fit_only

    # 方案 A，6 条 VNA 线 (6dB 间隔)，两种风格
    python draw/plot_all.py --cache "..." --approaches A \\
        --vna-powers -55,-49,-43,-37,-31,-25 --style both

    # 方案 A，全部 VNA 功率 (默认)
    python draw/plot_all.py --cache "..." --approaches A
"""

import os
import sys
import argparse
from typing import List, Optional

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter

sys.path.insert(0, os.path.dirname(__file__))
from _data_cache import load_cache, find_cache
from _tracking_utils import RESONATOR_NAMES

# =========================================================================
# 常量
# =========================================================================

SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3

# 温度配色 (与历史惯例一致)
TEMP_COLORS = {6: "#1565C0", 10: "#0097A7", 20: "#4CAF50", 40: "#FF9800", 77: "#D32F2F"}

# VNA 功率配色 — 自动生成，支持任意条数
_VNA_CMAP_BASE = [
    "#0D47A1", "#1565C0", "#1976D2", "#1E88E5", "#00838F",
    "#0097A7", "#00ACC1", "#2E7D32", "#388E3C", "#43A047",
    "#558B2F", "#689F38", "#7CB342", "#F57F17", "#EF6C00",
    "#E65100", "#BF360C", "#D32F2F",
]


def get_vna_colors(n: int) -> List[str]:
    """为 n 条 VNA 曲线生成均匀分布的配色。"""
    if n <= len(_VNA_CMAP_BASE):
        indices = np.linspace(0, len(_VNA_CMAP_BASE) - 1, n, dtype=int)
        return [_VNA_CMAP_BASE[i] for i in indices]
    # 超出预设: 用 jet colormap
    cmap = plt.cm.jet
    return [matplotlib.colors.to_hex(cmap(i / max(n - 1, 1))) for i in range(n)]


# =========================================================================
# 工具函数
# =========================================================================

def smooth_curve(y: np.ndarray) -> np.ndarray:
    """Savitzky-Golay 平滑。"""
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


def filter_vna_powers(available: List[int], selection: List[int] = None) -> List[int]:
    """从可用 VNA 功率中选择子集。

    Args:
        available: 全部可用 VNA 功率 (sorted)
        selection: 要使用的 VNA 功率列表，None = 全部使用

    Returns:
        筛选后的 sorted VNA 功率列表 (只保留 available 中存在的)
    """
    if selection is None:
        return available
    avail_set = set(available)
    return sorted([p for p in selection if p in avail_set])


# =========================================================================
# 方案 A: 响应率 vs VNA 功率
# =========================================================================

def extract_responsivity(delta_f_over_f, flags, pl_list):
    """为每条 VNA 功率线提取响应率 (ppm/mW)。"""
    n_pv = delta_f_over_f.shape[0]
    responsivity, r2_scores, n_points_list = [], [], []
    for i_pv in range(n_pv):
        y_raw = delta_f_over_f[i_pv, :] * 1e6  # → ppm
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
        "responsivity_ppm_per_mw": responsivity,
        "r2_scores": r2_scores,
        "n_points": n_points_list,
    }


def plot_approach_A(ax, resp_data, resonator_name, f0_6k, show_legend=True,
                    fit_only=False):
    """在 ax 上画响应率 vs VNA 功率。

    Args:
        resp_data: {temp_k: resp_dict}
        fit_only: True = 仅拟合趋势线，无散点
    """
    for temp_k in sorted(resp_data.keys()):
        resp = resp_data[temp_k]

        pv_arr = np.array(resp["pv_list"])
        resp_arr = np.array(resp["responsivity_ppm_per_mw"])
        r2_arr = np.array(resp["r2_scores"])

        r2_threshold = 0.2 if temp_k >= 70 else 0.5
        valid = ~np.isnan(resp_arr) & (r2_arr > r2_threshold)
        if valid.sum() < 2:
            continue

        pv_v = pv_arr[valid]
        resp_v = resp_arr[valid]
        r2_v = r2_arr[valid]

        color = TEMP_COLORS.get(temp_k, "#333333")
        high_conf = r2_v >= 0.5
        low_conf = ~high_conf
        label = f"{temp_k} K" + (" *" if low_conf.sum() > 0 else "")

        if fit_only:
            sort_idx = np.argsort(pv_v)
            x_sorted = pv_v[sort_idx]
            y_sorted = resp_v[sort_idx]
            y_smooth = smooth_curve(y_sorted)
            mask_ok = np.isfinite(y_smooth)
            if mask_ok.sum() < 2:
                continue
            x_plot = x_sorted[mask_ok]
            y_plot = y_smooth[mask_ok]
            try:
                coeffs = np.polyfit(x_plot, y_plot, 1)
                x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
                y_fit = np.polyval(coeffs, x_fit)
                ax.plot(x_fit, y_fit, '-', color=color, linewidth=2.5, alpha=0.9,
                        label=label)
            except Exception:
                continue
        else:
            if high_conf.sum() > 0:
                ax.scatter(pv_v[high_conf], resp_v[high_conf], color=color,
                           alpha=0.8, s=40, zorder=3, edgecolors="none")
            if low_conf.sum() > 0:
                ax.scatter(pv_v[low_conf], resp_v[low_conf],
                           facecolors='none', edgecolors=color,
                           alpha=0.5, s=60, marker='s', linewidths=1.5, zorder=3)
            sort_idx = np.argsort(pv_v)
            linestyle = '--' if low_conf.sum() > 0 else '-'
            ax.plot(pv_v[sort_idx], resp_v[sort_idx], linestyle, color=color,
                    linewidth=2, alpha=0.7, marker='', label=label)

    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
    ax.set_xlabel("VNA Readout Power (dBm)", fontsize=12)
    ax.set_ylabel("Responsivity d(df/f)/dP_laser (ppm/mW)", fontsize=12)
    title = f"KID Responsivity — {resonator_name}"
    if f0_6k is not None:
        title += f"  (f$_0$ = {f0_6k:.3f} GHz at 6 K)"
    if fit_only:
        title += "\n(trend lines only)"
    else:
        title += "\n* = low confidence (R² < 0.5, near-Tc SNR limited)"
    ax.set_title(title, fontsize=12, fontweight="bold")
    if show_legend:
        ax.legend(title="Temperature", loc="lower left", fontsize=9)
    ax.grid(True, alpha=0.3)


def generate_approach_A(cache, output_dir, vna_powers=None, style="both"):
    """方案 A: 响应率 vs VNA 功率。

    Args:
        style: "full" | "fit_only" | "both"
    """
    meta = cache["metadata"]
    collected = cache["collected"]
    temperatures_k = meta["temperatures_k"]
    laser_powers_mw = meta["laser_powers_mw"]

    # 每个谐振子收集各温度的响应率
    resp_data = {}  # {rname: {temp_k: resp_dict}}
    f0_6k_map = {}

    for rname in meta["resonator_names"]:
        resp_data[rname] = {}
        for T_K in temperatures_k:
            c = collected.get(T_K)
            if c is None or rname not in c["data"]:
                continue
            d = c["data"][rname]
            pv_available = c["vna_powers_dbm"]
            pv_use = filter_vna_powers(pv_available, vna_powers)
            if not pv_use:
                continue

            # 索引映射
            pv_idx_map = {pv: i for i, pv in enumerate(pv_available)}
            indices = [pv_idx_map[pv] for pv in pv_use]

            dff_sub = d["delta_f_over_f"][indices, :]
            flags_sub = d["flags"][indices, :]
            resp = extract_responsivity(dff_sub, flags_sub, laser_powers_mw)
            resp["pv_list"] = pv_use  # 覆盖为筛选后的功率

            n_valid = sum(1 for r, r2 in zip(resp["responsivity_ppm_per_mw"],
                                            resp["r2_scores"])
                         if not np.isnan(r) and r2 > (0.2 if T_K >= 70 else 0.5))
            if n_valid >= 2:
                resp_data[rname][T_K] = resp

            # 记录 6K f0
            if T_K == 6 and rname in c["identified"]:
                f0_6k_map[rname] = c["identified"][rname]["f0_ghz"]

    # 生成图表
    styles = []
    if style in ("full", "both"):
        styles.append(("full", "approach_A"))
    if style in ("fit_only", "both"):
        styles.append(("fit_only", "approach_A_fit_only"))

    for st, dirname in styles:
        out_dir = os.path.join(output_dir, dirname)
        os.makedirs(out_dir, exist_ok=True)
        for rname in meta["resonator_names"]:
            if not resp_data[rname]:
                continue
            f0_6k = f0_6k_map.get(rname)
            fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
            plot_approach_A(ax, resp_data[rname], rname, f0_6k,
                            fit_only=(st == "fit_only"))
            fig.tight_layout()
            fname = f"responsivity_vs_VNA_{rname}.png"
            fig.savefig(os.path.join(out_dir, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"  [A/{st}] {fname}")
    print(f"  [A] 完成 → {output_dir}")


# =========================================================================
# 方案 B: df/f vs Laser Power
# =========================================================================

def draw_cell_B(ax, pv_list, pl_list, delta_f_over_f, flags,
                vna_colors, show_legend=True, fit_only=False):
    """在 ax 上画 df/f vs Pl，每条 VNA 功率一条曲线。

    Args:
        fit_only: True = 仅画拟合实线
    """
    for i_pv, pv in enumerate(pv_list):
        y_raw = delta_f_over_f[i_pv, :] * 1e6  # → ppm
        fl = flags[i_pv, :]
        valid = ~np.isnan(y_raw) & (fl != "lost")
        if valid.sum() < 2:
            continue

        x_data = np.array(pl_list)[valid]
        y_data = y_raw[valid]
        y_smooth = smooth_curve(y_data)
        mask_ok = np.isfinite(y_smooth)
        if mask_ok.sum() < 2:
            continue
        x_plot = x_data[mask_ok]
        y_plot = y_smooth[mask_ok]

        try:
            coeffs = np.polyfit(x_plot, y_plot, 1)
        except Exception:
            coeffs = None

        color = vna_colors[i_pv % len(vna_colors)]
        label = f"{pv} dBm" if show_legend else None

        if fit_only:
            if coeffs is not None:
                x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
                y_fit = np.polyval(coeffs, x_fit)
                ax.plot(x_fit, y_fit, '-', color=color, linewidth=2.5, alpha=0.9,
                        label=label)
        else:
            ax.scatter(x_plot, y_plot, color=color, s=15, alpha=0.7, zorder=3,
                       edgecolors="none")
            ax.plot(x_plot, y_plot, '-', color=color, linewidth=2, alpha=0.8,
                    label=label)
            if coeffs is not None:
                x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
                y_fit = np.polyval(coeffs, x_fit)
                ax.plot(x_fit, y_fit, '--', color=color, linewidth=1, alpha=0.4)

    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
    ax.grid(True, alpha=0.25)
    ax.set_xlabel("Laser Power (mW)", fontsize=9)
    ax.set_ylabel("df/f (ppm)", fontsize=9)


def generate_approach_B(cache, output_dir, vna_powers=None, style="both"):
    """方案 B: df/f vs Laser Power 网格 + 单独大图。

    Args:
        style: "full" | "fit_only" | "both"
    """
    meta = cache["metadata"]
    collected = cache["collected"]
    temperatures_k = meta["temperatures_k"]
    laser_powers_mw = meta["laser_powers_mw"]
    resonator_names = meta["resonator_names"]

    # 筛选 VNA 功率
    all_pv = set()
    for T_K in temperatures_k:
        c = collected.get(T_K)
        if c:
            all_pv.update(c["vna_powers_dbm"])
    pv_use = filter_vna_powers(sorted(all_pv), vna_powers)
    if not pv_use:
        print("  [B] 错误: 没有可用的 VNA 功率")
        return

    vna_colors = get_vna_colors(len(pv_use))
    print(f"  [B] VNA 功率 ({len(pv_use)} 条): {pv_use}")

    # 构建筛选后的数据视图
    # filtered_data[T_K][rname] = {"pv_list": [...], "delta_f_over_f": ..., "flags": ...}
    filtered = {}
    for T_K in temperatures_k:
        c = collected.get(T_K)
        if c is None:
            continue
        filtered[T_K] = {}
        for rname in resonator_names:
            if rname not in c["data"]:
                continue
            d = c["data"][rname]
            pv_available = c["vna_powers_dbm"]
            pv_idx_map = {pv: i for i, pv in enumerate(pv_available)}
            indices = [pv_idx_map[pv] for pv in pv_use if pv in pv_idx_map]
            if not indices:
                continue
            filtered[T_K][rname] = {
                "pv_list": [pv_available[i] for i in indices],
                "delta_f_over_f": d["delta_f_over_f"][indices, :],
                "flags": d["flags"][indices, :],
                "f0_ghz": c["identified"].get(rname, {}).get("f0_ghz"),
            }

    styles = []
    if style in ("full", "both"):
        styles.append(("full", "approach_B"))
    if style in ("fit_only", "both"):
        styles.append(("fit_only", "approach_B_fit_only"))

    for st, dirname in styles:
        out_dir = os.path.join(output_dir, dirname)
        os.makedirs(out_dir, exist_ok=True)
        fit_only = (st == "fit_only")

        # ---- 5×5 网格总览 ----
        n_rows, n_cols = len(temperatures_k), len(resonator_names)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(28, 22), dpi=150,
                                 sharex=True, sharey="row")
        if n_rows == 1:
            axes = axes.reshape(1, -1)
        if n_cols == 1:
            axes = axes.reshape(-1, 1)

        for i_row, T_K in enumerate(temperatures_k):
            for j_col, rname in enumerate(resonator_names):
                ax = axes[i_row, j_col]
                if T_K not in filtered or rname not in filtered[T_K]:
                    ax.text(0.5, 0.5, "N/A", transform=ax.transAxes,
                            ha="center", va="center")
                    continue

                fd = filtered[T_K][rname]
                f0 = fd.get("f0_ghz")
                freq_str = f"f$_0$={f0:.3f} GHz" if f0 else ""

                draw_cell_B(ax, fd["pv_list"], laser_powers_mw,
                            fd["delta_f_over_f"], fd["flags"],
                            vna_colors, show_legend=False, fit_only=fit_only)

                ax.set_title(f"{rname}  ({freq_str})", fontsize=9, fontweight="bold")

                if T_K >= 70:
                    ax.text(0.95, 0.05, "low SNR", transform=ax.transAxes,
                            fontsize=6, color="#D32F2F", ha="right", va="bottom",
                            style="italic")

                if j_col == 0:
                    ax.set_ylabel(f"{T_K} K\ndf/f (ppm)", fontsize=9,
                                  color=TEMP_COLORS.get(T_K, "#333"),
                                  fontweight="bold")

        # 顶部图例
        legend_elements = [
            plt.Line2D([0], [0], color=vna_colors[i], linewidth=2,
                       label=f"{pv} dBm")
            for i, pv in enumerate(pv_use)
        ]
        fig.legend(handles=legend_elements, loc="upper center", ncol=min(8, len(pv_use)),
                   fontsize=10, title="VNA Readout Power", title_fontsize=11,
                   bbox_to_anchor=(0.5, 1.01))
        title_extra = " (fit only)" if fit_only else ""
        fig.suptitle(f"df/f vs Laser Power — {len(pv_use)} VNA power levels{title_extra}",
                     fontsize=16, fontweight="bold", y=1.03)
        fig.tight_layout()
        grid_path = os.path.join(out_dir, "grid_overview.png")
        fig.savefig(grid_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  [B/{st}] grid_overview.png")

        # ---- 单独大图 ----
        indiv_dir = os.path.join(out_dir, "individual")
        os.makedirs(indiv_dir, exist_ok=True)
        count = 0
        for T_K in temperatures_k:
            if T_K not in filtered:
                continue
            for rname in resonator_names:
                if rname not in filtered[T_K]:
                    continue
                fd = filtered[T_K][rname]
                f0 = fd.get("f0_ghz")
                freq_str = f"f$_0$ = {f0:.3f} GHz" if f0 else ""

                fig_i, ax_i = plt.subplots(figsize=(10, 6), dpi=150)
                draw_cell_B(ax_i, fd["pv_list"], laser_powers_mw,
                            fd["delta_f_over_f"], fd["flags"],
                            vna_colors, show_legend=True, fit_only=fit_only)

                title = f"{rname}  ({freq_str})  @  {T_K} K  |  df/f vs Laser Power"
                if fit_only:
                    title += "  (fit only)"
                ax_i.set_title(title, fontsize=13, fontweight="bold")

                if T_K >= 70:
                    ax_i.text(0.5, 0.97, "LOW SNR near Tc — tracking uncertain",
                              transform=ax_i.transAxes, fontsize=9, color="#D32F2F",
                              ha="center", va="top", style="italic",
                              bbox=dict(boxstyle="round,pad=0.3",
                                       facecolor="#FFF3E0", alpha=0.8))

                ax_i.legend(title="VNA Power (dBm)", loc="lower left",
                            fontsize=8, ncol=2)
                fname = f"T{T_K}K_{rname}.png"
                fig_i.savefig(os.path.join(indiv_dir, fname), dpi=150,
                              bbox_inches="tight")
                plt.close(fig_i)
                count += 1

        print(f"  [B/{st}] {count} 张单独图")

    print(f"  [B] 完成 → {output_dir}")


# =========================================================================
# S21 Overlay
# =========================================================================

def generate_S21_overlay(cache, output_dir, laser_powers=None):
    """S21 全谱叠加图: 固定 (T, Pl)，变化 Pv。

    对每个 (T, Pl) 组合:
      - 全谱 S21 图，每条 VNA 功率一种颜色
      - 标注 5 个谐振子的 f0 位置
    """
    meta = cache["metadata"]
    identification = cache["identification"]
    temperatures_k = meta["temperatures_k"]
    laser_powers_mw = meta["laser_powers_mw"]

    if laser_powers is not None:
        pl_to_plot = [pl for pl in laser_powers if pl in laser_powers_mw]
    else:
        pl_to_plot = laser_powers_mw

    out_dir = os.path.join(output_dir, "S21_overlay")
    os.makedirs(out_dir, exist_ok=True)

    from _data_cache import load_s2p_complex, find_s2p_for_pv_pl

    data_dir = meta["data_dir"]
    total = 0

    for T_K in temperatures_k:
        info = identification.get(T_K)
        if info is None:
            continue
        temp_dir = os.path.join(data_dir, f"{T_K}K")
        vna_powers = info["vna_powers_dbm"]
        vna_colors = get_vna_colors(len(vna_powers))

        for pl in pl_to_plot:
            # 收集该 (T, Pl) 下所有 VNA 功率的 S21
            traces = []
            for pv in vna_powers:
                fp = find_s2p_for_pv_pl(temp_dir, pv, pl)
                if fp is None:
                    continue
                loaded = load_s2p_complex(fp)
                if loaded is None:
                    continue
                traces.append({
                    "pv": pv,
                    "freq_ghz": loaded[0],
                    "s21_db": loaded[2],
                })

            if len(traces) < 2:
                continue

            fig, ax = plt.subplots(figsize=(12, 8), dpi=150)
            for i, tr in enumerate(traces):
                color = vna_colors[i % len(vna_colors)]
                ax.plot(tr["freq_ghz"], tr["s21_db"], color=color,
                        linewidth=1.5, alpha=0.8,
                        label=f"{tr['pv']} dBm")

            # 标注谐振子位置
            resonators = info["resonators"]
            for r in resonators:
                if r["f0_ghz"] is not None:
                    ax.axvline(x=r["f0_ghz"], color="#D32F2F", linestyle="--",
                               linewidth=0.8, alpha=0.5)
                    ax.annotate(r["name"],
                                xy=(r["f0_ghz"], ax.get_ylim()[0]),
                                fontsize=7, color="#D32F2F",
                                ha="center", va="bottom",
                                rotation=90)

            ax.set_xlabel("Frequency (GHz)", fontsize=12)
            ax.set_ylabel("|S21| (dB)", fontsize=12)
            ax.set_title(f"S21 — T = {T_K} K, Laser = {pl} mW  "
                         f"({len(traces)} VNA powers)",
                         fontsize=13, fontweight="bold")
            ax.legend(title="VNA Power", loc="lower left", fontsize=7,
                      ncol=min(3, (len(traces) + 5) // 6))
            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            fname = f"T{T_K}K_Pl{pl:02d}mW.png"
            fig.savefig(os.path.join(out_dir, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            total += 1

    print(f"  [S21] {total} 张图 → {out_dir}")


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="统一画图 — 从缓存读取数据，生成方案 A/B/S21 图表",
    )
    parser.add_argument(
        "--cache", required=True,
        help="缓存文件路径 (.pkl) 或数据目录 (自动查找缓存)",
    )
    parser.add_argument(
        "--approaches", default="all",
        help="方案选择: A, B, S21, all (逗号分隔, 默认: all)",
    )
    parser.add_argument(
        "--vna-powers", type=str, default=None,
        help="VNA 功率列表 (dBm), 逗号分隔, 如: -55,-49,-43,-37,-31,-25。默认: 全部",
    )
    parser.add_argument(
        "--style", default="both",
        choices=["full", "fit_only", "both"],
        help="画图风格: full (散点+线+拟合), fit_only (仅趋势线), both (默认)",
    )
    parser.add_argument(
        "--output", default=None,
        help="输出根目录 (默认: 缓存文件同级的 plot_output/)",
    )

    args = parser.parse_args()

    # 加载缓存
    if os.path.isfile(args.cache):
        cache_path = args.cache
    else:
        cache_path = find_cache(args.cache)
        if cache_path is None:
            print(f"[ERROR] 未找到缓存: {args.cache}")
            print(f"  请先运行: python draw/_data_cache.py --data-dir <路径>")
            return

    print(f"[LOAD] {cache_path}")
    cache = load_cache(cache_path)
    meta = cache["metadata"]
    print(f"  数据集: {meta['dataset_name']}")
    print(f"  温度: {meta['temperatures_k']} K")
    print(f"  激光功率: {meta['laser_powers_mw']} mW")
    print(f"  追踪率: {meta['total_tracked_points']}/{meta['total_grid_points']}")

    # 解析 VNA 功率
    vna_powers = None
    if args.vna_powers:
        vna_powers = [int(x) for x in args.vna_powers.split(",")]
        print(f"  VNA 功率筛选: {vna_powers}")

    # 输出目录
    if args.output:
        output_base = args.output
    else:
        output_base = os.path.join(
            os.path.dirname(cache_path), "plot_output"
        )
    os.makedirs(output_base, exist_ok=True)

    # 解析方案
    approaches = [a.strip() for a in args.approaches.split(",")]
    if "all" in approaches:
        approaches = ["A", "B", "S21"]

    # 生成
    for app in approaches:
        print(f"\n{'=' * 60}")
        if app == "A":
            print("方案 A: 响应率 vs VNA 功率")
            print(f"{'=' * 60}")
            generate_approach_A(cache, output_base, vna_powers, args.style)
        elif app == "B":
            print("方案 B: df/f vs Laser Power")
            print(f"{'=' * 60}")
            generate_approach_B(cache, output_base, vna_powers, args.style)
        elif app == "S21":
            print("S21 全谱叠加")
            print(f"{'=' * 60}")
            generate_S21_overlay(cache, output_base)
        else:
            print(f"未知方案: {app} (可选: A, B, S21, all)")

    print(f"\n[DONE] 全部输出 → {output_base}")


if __name__ == "__main__":
    main()
