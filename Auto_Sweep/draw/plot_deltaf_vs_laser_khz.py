# -*- coding: utf-8 -*-
"""
R4 绝对频移 Δf vs 激光功率 — 不同 VNA 读出功率对比 (多温度面板)

纵轴: 绝对频移 Δf = (f0(Pl) - f0(0)) [kHz]
横轴: 激光功率 (mW), 0–9 mW
每个温度一个面板 (2×4), 每个激光功率处 N 列散点 (对应不同 VNA 读出功率).
仅散点, 不连线不拟合.

数据来源: _data_cache.py 生成的 pickle 缓存
用法:
  python draw/plot_deltaf_vs_laser_khz.py \
      --cache "path/to/_cache_xxx.pkl" \
      --vna-powers -55,-49,-43,-37,-31,-25
"""

import sys
import argparse
import pickle
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── 路径 ──────────────────────────────────────────────────
_draw_dir = Path(__file__).resolve().parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))

try:
    from _style_config import apply_style, get_figsize, save_figure, OUTPUT_FORMATS
    _HAS_STYLE = True
except ImportError:
    _HAS_STYLE = False


# ═════════════════════════════════════════════════════════════
# 默认参数
# ═════════════════════════════════════════════════════════════
DEFAULT_CACHE = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "_cache_20260609-0624__6-80K__full.pkl"))
DEFAULT_OUTPUT_DIR = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "plot_output"))

RESONATOR = "R4"
TEMPERATURES_K = [6, 10, 20, 40, 50, 60, 70, 77]

# x 轴 dodge 宽度: 每个激光功率处 N 列 VNA 点的总水平散布 (mW)
DODGE_WIDTH = 0.60

# VNA 功率配色
_VNA_CMAP_BASE = [
    "#0D47A1", "#1565C0", "#1976D2", "#1E88E5", "#00838F",
    "#0097A7", "#00ACC1", "#2E7D32", "#388E3C", "#43A047",
    "#558B2F", "#689F38", "#7CB342", "#F57F17", "#EF6C00",
    "#E65100", "#BF360C", "#D32F2F",
]


def get_vna_colors(n: int):
    """为 n 个 VNA 功率生成均匀分布的配色."""
    if n <= len(_VNA_CMAP_BASE):
        indices = np.linspace(0, len(_VNA_CMAP_BASE) - 1, n, dtype=int)
        return [_VNA_CMAP_BASE[i] for i in indices]
    cmap = plt.cm.plasma
    return [matplotlib.colors.to_hex(cmap(i / max(n - 1, 1))) for i in range(n)]


# ═════════════════════════════════════════════════════════════
# 数据提取
# ═════════════════════════════════════════════════════════════

def extract_r4_deltaf_all_pl(cache: dict, T_K: float, vna_filter: list = None):
    """提取 R4 在温度 T 下, 全部激光功率的绝对 Δf (kHz).

    Returns
    -------
    dict or None
        {"laser_powers_mw": [...],           # 全部激光功率
         "pv_list": [...],                    # 该温度可用的 VNA 功率
         "deltaf_khz": ndarray (n_pv, n_pl), # 绝对频移 (kHz), NaN = 无数据
         "flags": ndarray (n_pv, n_pl)}      # tracking 质量
    """
    if T_K not in cache.get("collected", {}):
        return None

    c = cache["collected"][T_K]
    if RESONATOR not in c.get("data", {}):
        return None

    laser_powers = cache["metadata"]["laser_powers_mw"]   # [0,1,3,5,7,9]
    d = c["data"][RESONATOR]
    dff = d["delta_f_over_f"]          # shape (n_pv, n_pl)
    flags = d["flags"]
    f0_refs = d["f0_refs"]
    pv_available = c["vna_powers_dbm"]

    # 过滤 VNA 功率
    if vna_filter is not None:
        vna_set = set(vna_filter)
        keep_idx = [i for i, pv in enumerate(pv_available) if pv in vna_set]
    else:
        keep_idx = list(range(len(pv_available)))

    n_pv_keep = len(keep_idx)
    n_pl = len(laser_powers)
    deltaf_khz = np.full((n_pv_keep, n_pl), np.nan)
    flags_out = np.full((n_pv_keep, n_pl), "lost", dtype=object)
    pv_keep = [pv_available[i] for i in keep_idx]

    for i_out, i_pv in enumerate(keep_idx):
        pv = pv_available[i_pv]
        f0_ref_ghz = f0_refs.get(pv, None)
        if f0_ref_ghz is None:
            continue
        for i_pl in range(n_pl):
            val = dff[i_pv, i_pl]
            fl = flags[i_pv, i_pl]
            if fl == "lost" or np.isnan(val):
                continue
            # 绝对 Δf (kHz) = (delta_f/f) * f0_ref(GHz) * 1e9 / 1e3
            deltaf_khz[i_out, i_pl] = val * f0_ref_ghz * 1e6
            flags_out[i_out, i_pl] = fl

    return {
        "laser_powers_mw": laser_powers,
        "pv_list": pv_keep,
        "deltaf_khz": deltaf_khz,
        "flags": flags_out,
    }


# ═════════════════════════════════════════════════════════════
# 画图
# ═════════════════════════════════════════════════════════════

def plot_deltaf_vs_laser(cache: dict, output_path: str,
                         vna_powers_filter: list = None):
    """主画图 — 2×4 多面板, 每个温度一张子图."""

    # ── 收集全部温度的数据 ──
    all_results = {}
    skipped = []
    for T_K in TEMPERATURES_K:
        r = extract_r4_deltaf_all_pl(cache, T_K, vna_powers_filter)
        if r is None:
            skipped.append(T_K)
        else:
            all_results[T_K] = r

    if skipped:
        print(f"跳过的温度 (无 R4 数据): {skipped}")
    if not all_results:
        print("[ERROR] 没有任何有效数据, 退出.")
        return

    # ── 全局 VNA 功率 & 配色 ──
    all_pv = set()
    for r in all_results.values():
        all_pv.update(r["pv_list"])
    unique_pv = sorted(all_pv)
    n_pv = len(unique_pv)
    pv_colors = get_vna_colors(n_pv)
    pv_to_color = {pv: pv_colors[i] for i, pv in enumerate(unique_pv)}
    pv_to_rank = {pv: i for i, pv in enumerate(unique_pv)}

    print(f"VNA 功率列数: {n_pv}  →  {unique_pv}")
    total_pts = sum(
        (~np.isnan(r["deltaf_khz"])).sum() for r in all_results.values())
    print(f"总数据点数: {total_pts}")

    # ── 创建 2×4 网格 ──
    n_rows, n_cols = 2, 4
    if _HAS_STYLE:
        cfg = apply_style("quick_check")
        fig, axes = plt.subplots(n_rows, n_cols,
                                 figsize=(22, 12), dpi=150,
                                 sharex=True, sharey=True)
    else:
        fig, axes = plt.subplots(n_rows, n_cols,
                                 figsize=(22, 12), dpi=150,
                                 sharex=True, sharey=True)

    active_temps = sorted(all_results.keys())
    laser_powers_mw = cache["metadata"]["laser_powers_mw"]

    for idx, T_K in enumerate(active_temps):
        row, col = divmod(idx, n_cols)
        ax = axes[row, col]
        r = all_results[T_K]
        deltaf = r["deltaf_khz"]      # (n_pv, n_pl)
        flags = r["flags"]
        pv_list = r["pv_list"]

        # 在每个激光功率处, 按 VNA 功率 dodge 排列
        for j_pl, pl in enumerate(laser_powers_mw):
            # 收集该激光功率下各 VNA 功率的 Δf
            for i_pv, pv in enumerate(pv_list):
                val = deltaf[i_pv, j_pl]
                fl = flags[i_pv, j_pl]
                if fl == "lost" or np.isnan(val):
                    continue

                rank = pv_to_rank.get(pv, 0)
                if n_pv > 1:
                    x_offset = DODGE_WIDTH * (rank / (n_pv - 1) - 0.5)
                else:
                    x_offset = 0.0
                x = pl + x_offset
                color = pv_to_color.get(pv, "gray")

                if fl == "tracked":
                    ax.scatter(x, val, color=color, marker="o",
                               s=35, alpha=0.85, edgecolors="none", zorder=3)
                elif fl == "shallow":
                    ax.scatter(x, val, facecolors="none",
                               edgecolors=color, marker="D",
                               s=45, alpha=0.5, linewidths=1.0, zorder=2)

        # Y=0 参考线
        ax.axhline(y=0, color="gray", linestyle=":", linewidth=0.8, alpha=0.5)

        ax.set_title(f"T = {T_K} K", fontsize=12, fontweight="bold")
        ax.grid(True, alpha=0.2, linestyle="--")
        ax.set_xticks(laser_powers_mw)

    # ── 隐藏多余子图 ──
    for idx in range(len(active_temps), n_rows * n_cols):
        row, col = divmod(idx, n_cols)
        axes[row, col].set_visible(False)

    # ── 全局坐标轴标签 ──
    fig.supxlabel("Laser Power (mW)", fontsize=14, y=0.02)
    fig.supylabel(r"$\Delta f$  (kHz)", fontsize=14, x=0.02)
    fig.suptitle(f"{RESONATOR}  Absolute Frequency Shift  "
                 r"$\Delta f$ vs Laser Power",
                 fontsize=16, fontweight="bold", y=0.995)

    # ── 统一 colorbar (VNA 功率) ──
    sm = plt.cm.ScalarMappable(
        norm=plt.Normalize(vmin=min(unique_pv), vmax=max(unique_pv)),
        cmap=matplotlib.colors.ListedColormap(pv_colors))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=axes.ravel().tolist(), pad=0.015,
                        aspect=50, shrink=0.92)
    cbar.set_label("VNA Readout Power (dBm)", fontsize=12)

    # ── 图例 (marker 类型) ──
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="gray",
               markersize=7, label="tracked"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="none",
               markeredgecolor="gray", markersize=7, label="shallow"),
    ]
    # 把图例放在最后一个可见子图里
    last_visible_ax = axes[(len(active_temps) - 1) // n_cols,
                           (len(active_temps) - 1) % n_cols]
    last_visible_ax.legend(handles=legend_elements,
                           loc="upper left", framealpha=0.7, fontsize=8)

    fig.subplots_adjust(left=0.06, right=0.92, bottom=0.07, top=0.94,
                        hspace=0.25, wspace=0.15)

    # ── 保存 ──
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if _HAS_STYLE:
        basepath = str(output_file.with_suffix(""))
        save_figure(fig, basepath, OUTPUT_FORMATS)
    else:
        fig.savefig(str(output_file), dpi=150, bbox_inches="tight")
        print(f"输出: {output_file}")

    plt.close(fig)
    print("完成.")


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="R4 绝对频移 vs 激光功率 — 多温度面板散点图")

    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help="缓存 pickle 文件路径")
    parser.add_argument("--output", default=None,
                        help="输出目录 (默认: 缓存旁的 plot_output/)")
    parser.add_argument("--vna-powers", type=str,
                        default="-55,-49,-43,-37,-31,-25",
                        help="VNA 功率列表 (dBm), 逗号分隔 "
                             "(默认: -55,-49,-43,-37,-31,-25)")
    args = parser.parse_args()

    vna_filter = [int(x.strip()) for x in args.vna_powers.split(",")]

    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = Path(args.cache).resolve().parent.parent / "plot_output"

    n_vna = len(vna_filter)
    output_file = output_dir / f"deltaf_vs_laser_R4_{n_vna}vna_2x4.png"

    print(f"缓存: {args.cache}")
    print(f"VNA 功率: {vna_filter}")
    print(f"输出: {output_file}")

    with open(args.cache, "rb") as f:
        cache = pickle.load(f)

    meta = cache["metadata"]
    print(f"数据集: {meta['dataset_name']}")

    plot_deltaf_vs_laser(cache, str(output_file),
                         vna_powers_filter=vna_filter)


if __name__ == "__main__":
    main()
