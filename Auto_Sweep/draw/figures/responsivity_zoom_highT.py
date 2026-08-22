# -*- coding: utf-8 -*-
"""
Figure: High-Temperature Responsivity Zoom — YBCO CPW Resonators

2×3 子面板出版图, 聚焦 50–77 K 近 Tc 响应率爆发区:

  (0,0) R1  |  (0,1) R2  |  (0,2) R3
  (1,0) R4  |  (1,1) R5  |  (1,2) Info

每个谐振子面板: δf/f₀ vs Laser Power @ 50/60/70/77 K
第 6 格: 共用图例 + 温度标注 + 异常说明

物理意义:
  近 Tc 时超流密度骤降 → 动能电感爆发 → 响应率 ×10+ 增长
  50–70 K 是转变区, 单独 zoom 展示完整趋势

数据来源: cache pickle, Pv = -55 dBm
"""

import sys, pickle
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_draw_dir = Path(__file__).resolve().parent.parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))
from _style_config import (apply_style, get_figsize, get_resonator_color,
                            save_figure, OUTPUT_FORMATS, TEMPERATURE_COLORS)
from _error_estimation import (get_dff_value_and_error, bootstrap_responsivity)

# ═══════════════════════════════════════════════════════
# 默认路径
# ═══════════════════════════════════════════════════════
DEFAULT_CACHE = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "_cache_20260609-0624__6-80K__full.pkl"))
DEFAULT_OUTPUT_DIR = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "plot_output"))

# ═══════════════════════════════════════════════════════
# 参数
# ═══════════════════════════════════════════════════════
HIGH_TEMPERATURES = [50, 60, 70, 77]  # 近 Tc 转变区
REF_TEMPERATURE = 6                    # 低温参考
PV_IDX = 0                             # -55 dBm
RESONATOR_NAMES = ["R1", "R2", "R3", "R4", "R5"]
MAX_ANOMALOUS_PL1_PPM = 5000

# 高温配色 (从 TEMPERATURE_COLORS 取, 强调暖色调)
T_COLORS = {
    50: TEMPERATURE_COLORS.get(50, "#F46D43"),   # 橙红
    60: TEMPERATURE_COLORS.get(60, "#D73027"),   # 红
    70: TEMPERATURE_COLORS.get(70, "#A50026"),   # 深红
    77: TEMPERATURE_COLORS.get(77, "#67001F"),   # 暗红 (近 Tc)
}
REF_COLOR = TEMPERATURE_COLORS.get(6, "#2166AC")  # 深蓝 (6K 参考)


def _is_anomalous(dff_ppm: np.ndarray) -> bool:
    """检查追踪异常 (Pl=1mW 处 δf/f₀ 跃变过大)。"""
    if len(dff_ppm) < 2:
        return False
    return abs(dff_ppm[1]) > MAX_ANOMALOUS_PL1_PPM


def plot_one_resonator(ax, cache: dict, rname: str, T_k: int):
    """在单个子面板上绘制 (T_k, rname) 的 δf/f₀ vs Pl。

    Returns:
        bs dict (slope, r_squared, etc.) or None if data unavailable/anomalous.
    """
    if T_k not in cache["collected"]:
        return None

    laser_pwrs, dff_ppm, dff_err = get_dff_value_and_error(
        cache, T_k, rname, PV_IDX)

    if _is_anomalous(dff_ppm):
        return None  # 异常数据不绘制

    color = T_COLORS.get(T_k, "#888888")
    alpha = 0.85 if T_k == 77 else 0.75  # 77K 最突出

    # 误差条 + 散点
    ax.errorbar(laser_pwrs, dff_ppm, yerr=dff_err,
                 color=color, marker="o", markersize=4.0,
                 linewidth=0, capsize=2, capthick=0.7,
                 alpha=alpha, zorder=2)

    # 线性拟合
    bs = bootstrap_responsivity(laser_pwrs, dff_ppm)
    if not np.isnan(bs["slope"]):
        x_fit = np.linspace(0, 9, 50)
        ax.plot(x_fit, bs["slope"] * x_fit,
                 color=color, linestyle="--", linewidth=1.0,
                 alpha=0.45, zorder=1)

    return bs


def plot_zoom_figure(cache_path: str = DEFAULT_CACHE,
                      output_dir: str = DEFAULT_OUTPUT_DIR,
                      preset: str = "prb_double"):
    """生成 2×3 高温度响应率 zoom 图。"""
    # ── 加载 ──
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    rnames = RESONATOR_NAMES
    dataset = cache["metadata"]["dataset_name"]

    # ── 样式 ──
    cfg = apply_style(preset)
    fig_width = cfg["width_inches"]
    fig_height = fig_width * 0.78   # 7.0 × 5.46"
    fig, axes = plt.subplots(2, 3, figsize=(fig_width, fig_height))

    # ── 遍历 5 个谐振子 ──
    all_bs = {}  # {rname: {T_k: bs}}
    for i, rname in enumerate(rnames):
        row, col = divmod(i, 3)
        ax = axes[row, col]
        all_bs[rname] = {}

        for T_k in HIGH_TEMPERATURES:
            bs = plot_one_resonator(ax, cache, rname, T_k)
            if bs is not None:
                all_bs[rname][T_k] = bs

        # 6K 参考 (虚线, 仅最低温)
        if 6 in cache["collected"]:
            laser_pwrs, dff_ref, dff_err = get_dff_value_and_error(
                cache, 6, rname, PV_IDX)
            if not _is_anomalous(dff_ref):
                bs_6k = bootstrap_responsivity(laser_pwrs, dff_ref)
                if not np.isnan(bs_6k["slope"]):
                    x_ref = np.linspace(0, 9, 30)
                    ax.plot(x_ref, bs_6k["slope"] * x_ref,
                             color=REF_COLOR, linestyle=":", linewidth=0.7,
                             alpha=0.35, zorder=0)

        # 装饰
        ax.axhline(y=0, color="#999999", linewidth=0.5, linestyle=":", zorder=0)
        ax.set_xlabel("Laser (mW)" if row == 1 else "")
        ax.set_ylabel(r"$\delta f/f_0$ (ppm)" if col == 0 else "")
        ax.set_title(rname, fontsize=8, fontweight="bold", color=get_resonator_color(rname))
        ax.grid(True, alpha=0.22)

        # y 轴自适应范围 (含一点余量)
        y_data = []
        for T_k in HIGH_TEMPERATURES:
            if T_k in cache["collected"]:
                _, dff, _ = get_dff_value_and_error(cache, T_k, rname, PV_IDX)
                if not _is_anomalous(dff):
                    y_data.append(dff)
        if y_data:
            y_all = np.concatenate([y for y in y_data if len(y) > 0])
            if len(y_all) > 0 and not np.all(np.isnan(y_all)):
                y_valid = y_all[~np.isnan(y_all)]
                if len(y_valid) > 0:
                    y_margin = max(abs(y_valid.min()), abs(y_valid.max())) * 0.15
                    ax.set_ylim(y_valid.min() - y_margin, y_valid.max() + y_margin)

    # ── 第 6 格: Info ──
    ax_info = axes[1, 2]
    ax_info.axis("off")

    # 图例
    legend_handles = []
    for T_k in HIGH_TEMPERATURES:
        color = T_COLORS.get(T_k, "#888888")
        legend_handles.append(
            plt.Line2D([0], [0], color=color, marker="o", markersize=5,
                        linestyle="-", linewidth=1.5,
                        label=f"T = {T_k} K"))
    legend_handles.append(
        plt.Line2D([0], [0], color=REF_COLOR, linestyle=":", linewidth=1.0,
                    label=f"T = {REF_TEMPERATURE} K (ref)"))

    ax_info.legend(handles=legend_handles, loc="center",
                    fontsize=7, framealpha=0.8, handlelength=1.5,
                    title="Temperature")

    # 响应率摘要 (R4 作为代表)
    y_pos = 0.58
    ax_info.text(0.05, 0.88,
                  "Response rate |R| (ppm/mW)\n"
                  "R4 @ Pv = -55 dBm",
                  transform=ax_info.transAxes,
                  fontsize=6.5, fontweight="bold", va="top")

    if "R4" in all_bs:
        for T_k in HIGH_TEMPERATURES:
            if T_k in all_bs["R4"]:
                bs = all_bs["R4"][T_k]
                color = T_COLORS.get(T_k, "#888888")
                ax_info.text(0.08, y_pos,
                              f"T={T_k}K: |R|={abs(bs['slope']):.0f}  "
                              f"R²={bs['r_squared']:.3f}",
                              transform=ax_info.transAxes,
                              fontsize=5.8, color=color, va="top")
                y_pos -= 0.065

    # Tc 参考 + 物理标注
    ax_info.text(0.05, 0.28, "$T_c$ ~ 82 K",
                  transform=ax_info.transAxes,
                  fontsize=6.5, color="#D62728", fontweight="bold")
    ax_info.text(0.05, 0.18,
                  "Near $T_c$: superfluid density\n"
                  "collapse → kinetic inductance\n"
                  "surge → responsivity ×10+",
                  transform=ax_info.transAxes,
                  fontsize=5.5, color="#555555", va="top")

    # 异常说明
    ax_info.text(0.05, 0.04,
                  "R1/R2/R5 @ 70 K excluded\n"
                  "— tracking anomaly\n"
                  "(resonance broadening near $T_c$)",
                  transform=ax_info.transAxes,
                  fontsize=5, color="#AA4444", va="bottom",
                  style="italic")

    # ── 总标题 ──
    fig.suptitle(f"Optical Responsivity near $T_c$ — Zoom 50–77 K\n"
                 f"{dataset},  $P_{{\\rm read}}$ = −55 dBm",
                 fontsize=9, y=1.005)

    plt.tight_layout()

    # ── 保存 ──
    basepath = Path(output_dir) / "responsivity_zoom_highT"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    # ── 报告 ──
    print(f"\n===== High-T Responsivity Zoom saved =====")
    for fmt in OUTPUT_FORMATS:
        print(f"  {basepath}.{fmt}")
    _print_zoom_table(cache, rnames)


def _print_zoom_table(cache: dict, rnames: list):
    """打印 50-77K 响应率表格。"""
    print(f"\n--- High-T Responsivity (|R| ppm/mW, Pv=-55 dBm) ---")
    header = f"{'T(K)':>5s}" + "".join(f"{r:>10s}" for r in rnames)
    print(header)
    print("-" * len(header))
    for T_k in HIGH_TEMPERATURES:
        if T_k not in cache["collected"]:
            continue
        row = f"{T_k:5d}"
        for rname in rnames:
            laser_pwrs, dff_ppm, _ = get_dff_value_and_error(
                cache, T_k, rname, PV_IDX)
            if _is_anomalous(dff_ppm):
                row += f"{'excl':>10s}"
            else:
                bs = bootstrap_responsivity(laser_pwrs, dff_ppm)
                if np.isnan(bs["slope"]):
                    row += f"{'N/A':>10s}"
                else:
                    row += f"{abs(bs['slope']):9.1f} "
        print(row)

    # 6K 参考
    print(f"\n  6K reference (|R| ppm/mW):")
    row = "       "
    for rname in rnames:
        laser_pwrs, dff_ref, _ = get_dff_value_and_error(cache, 6, rname, PV_IDX)
        bs = bootstrap_responsivity(laser_pwrs, dff_ref)
        row += f"{abs(bs['slope']):9.1f} " if not np.isnan(bs["slope"]) else f"{'N/A':>10s}"
    print(row)


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate high-temperature responsivity zoom figure")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"])
    args = parser.parse_args()

    if not Path(args.cache).exists():
        print(f"Cache not found: {args.cache}")
        sys.exit(1)

    plot_zoom_figure(args.cache, args.output, args.preset)
