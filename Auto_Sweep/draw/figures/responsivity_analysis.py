# -*- coding: utf-8 -*-
"""
Figure: Optical Responsivity Analysis — YBCO CPW Resonators

四面板出版图 (2×2), 展示 YBCO 谐振器的光学响应特性:

  (a) 左上: δf/f₀ vs Laser Power — R4 (最深谷), 全温度, 误差条+线性拟合
  (b) 右上: δf/f₀ vs Laser Power — R1 (参考谐振子), 全温度
  (c) 左下: δf/f₀ vs Laser Power — 全部谐振子 @ 6K, 器件间一致性
  (d) 右下: 响应率 vs 温度 — 5 谐振子, Bootstrap 95% CI

物理意义:
  激光加热破坏库珀对 → n_qp 增加 → L_k 增大 → f₀ 红移 (δf < 0)
  低温时超流密度高, 响应弱; 近 Tc 时超流密度骤降, 响应爆发 (×10)

数据来源: cache pickle (Pl=0 参考, Pl>0 追踪), Pv = -55 dBm
"""

import sys, pickle
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

# 导入样式和误差模块
_draw_dir = Path(__file__).resolve().parent.parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))
from _style_config import (apply_style, get_figsize, get_resonator_color,
                            save_figure, OUTPUT_FORMATS)
from _error_estimation import (estimate_dff_uncertainty, bootstrap_responsivity,
                                get_dff_value_and_error, get_responsivity_with_error)

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
# 画图参数
# ═══════════════════════════════════════════════════════
DETAIL_TEMPERATURES = [6, 20, 40, 77]   # 4 个代表性温度
ALL_TEMPERATURES = [6, 10, 20, 40, 50, 60, 70, 77]  # 全部 8 个温度 (R vs T 用)
PV_IDX = 0  # -55 dBm (最低读出功率, 最线性)
PANEL_LABELS = ["(a)", "(b)", "(c)", "(d)"]
MAX_ANOMALOUS_PL1_PPM = 5000  # |dff| at Pl=1 mW 超过此值视为追踪异常
CROSSCHECK_T = 6  # 面板 (c) 单温度 — 器件间一致性

# 固定温度配色 — 来自 _style_config，白底高对比，无 coolwarm 白区问题
T_FIXED_COLORS = {
    6:  "#2166AC",  # 深蓝
    20: "#66BD63",  # 绿
    40: "#FDAE61",  # 橙
    77: "#D73027",  # 深红 (近 Tc)
}


def get_t_color_fixed(T_k: float) -> tuple:
    """返回固定高对比度颜色 (无 white-out 问题)。"""
    color = T_FIXED_COLORS.get(T_k, "#888888")
    return color, 0.9


# ═══════════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════════

def _is_anomalous(dff_ppm: np.ndarray, T_k: float) -> bool:
    """检查数据是否追踪异常 (Pl=1mW 处 δf/f₀ 跃变过大)。"""
    if len(dff_ppm) < 2:
        return False
    return abs(dff_ppm[1]) > MAX_ANOMALOUS_PL1_PPM


def extract_panel_data(cache: dict, T_k: float, rname: str):
    """提取单个 (T, R) 的 δf/f₀ 数据 + 误差。异常数据返回 NaN。"""
    laser_pwrs, dff_ppm, dff_err = get_dff_value_and_error(
        cache, T_k, rname, PV_IDX)
    if _is_anomalous(dff_ppm, T_k):
        bs = {"slope": np.nan, "slope_std": np.nan,
              "ci_95_lower": np.nan, "ci_95_upper": np.nan,
              "r_squared": np.nan, "n_points": 0}
    else:
        bs = bootstrap_responsivity(laser_pwrs, dff_ppm)
    return laser_pwrs, dff_ppm, dff_err, bs


def extract_responsivity_curve(cache: dict, rname: str):
    """提取单个谐振子跨全部温度的响应率曲线。自动过滤异常点。"""
    temps = []
    resp = []
    resp_lower = []
    resp_upper = []
    for T_k in ALL_TEMPERATURES:
        if T_k not in cache["collected"]:
            continue
        # 先检查原始数据是否异常
        _, dff_ppm, _, = get_dff_value_and_error(cache, T_k, rname, PV_IDX)
        if _is_anomalous(dff_ppm, T_k):
            continue
        info = get_responsivity_with_error(cache, T_k, rname, PV_IDX)
        if np.isnan(info["responsivity_ppm_per_mw"]):
            continue
        temps.append(T_k)
        resp.append(abs(info["responsivity_ppm_per_mw"]))
        resp_lower.append(abs(info["responsivity_ci95"][0]))
        resp_upper.append(abs(info["responsivity_ci95"][1]))
    return (np.array(temps), np.array(resp),
            np.array(resp_lower), np.array(resp_upper))


# ═══════════════════════════════════════════════════════
# 子图: δf/f₀ vs Laser Power
# ═══════════════════════════════════════════════════════

def plot_dff_panel(ax, cache: dict, rname: str, title: str,
                    t_list: list = None,
                    show_fit: bool = True,
                    show_legend: bool = True):
    """在给定 Axes 上绘制 δf/f₀ vs Pl (多温度曲线 + 误差条 + 线性拟合)。"""
    if t_list is None:
        t_list = DETAIL_TEMPERATURES

    for T_k in t_list:
        if T_k not in cache["collected"]:
            continue
        laser_pwrs, dff_ppm, dff_err, bs = extract_panel_data(cache, T_k, rname)
        if _is_anomalous(dff_ppm, T_k):
            continue   # 跳过追踪异常数据
        color, alpha = get_t_color_fixed(T_k)

        # 误差条 + 散点
        ax.errorbar(laser_pwrs, dff_ppm, yerr=dff_err,
                     color=color, marker="o", markersize=5.0,
                     linewidth=0, capsize=3, capthick=0.9,
                     alpha=alpha, zorder=2)

        # 线性拟合 (虚线)
        if show_fit and not np.isnan(bs["slope"]):
            x_fit = np.linspace(0, 9, 50)
            y_fit = bs["slope"] * x_fit
            ax.plot(x_fit, y_fit, color=color, linestyle="--",
                     linewidth=1.2, alpha=0.55, zorder=1)

        # 响应率标注 (全部 4 组温度都标)
        if show_legend:
            label = (f"T={T_k}K  "
                     f"R={bs['slope']:.0f} ppm/mW")
            ax.plot([], [], color=color, marker="o", markersize=5.0,
                     label=label)

    # 参考线 (δf=0)
    ax.axhline(y=0, color="#999999", linewidth=0.6, linestyle=":",
                zorder=0)
    ax.set_xlabel("Laser Power (mW)")
    ax.set_ylabel(r"$\delta f / f_0$ (ppm)")
    ax.set_title(title, fontsize=8, fontweight="bold")
    ax.grid(True, alpha=0.22)

    if show_legend:
        ax.legend(loc="lower left", fontsize=6, framealpha=0.7,
                   ncol=1, handlelength=1.5)


# ═══════════════════════════════════════════════════════
# 子图: 响应率 vs 温度 (summary)
# ═══════════════════════════════════════════════════════

def plot_responsivity_summary(ax, cache: dict, rnames: list):
    """响应率 |R| vs 温度, 5 谐振子, Bootstrap 95% CI 阴影。"""
    for rname in rnames:
        temps, resp, ci_low, ci_high = extract_responsivity_curve(cache, rname)
        if len(temps) < 2:
            continue
        color = get_resonator_color(rname)

        # 95% CI 阴影
        ax.fill_between(temps, ci_low, ci_high,
                         color=color, alpha=0.12, zorder=1)
        # 主线
        ax.plot(temps, resp, color=color, marker="s", markersize=4.5,
                 linewidth=1.8, label=rname, zorder=2)

    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel(r"$|R|$  —  Responsivity (ppm/mW)")
    ax.set_title("Responsivity vs Temperature", fontsize=8, fontweight="bold")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.22)
    ax.legend(loc="upper left", fontsize=7, framealpha=0.7, ncol=1)

    # Tc 参考线 (在数据范围右侧)
    ax.axvline(x=82, color="#D62728", linestyle="--", linewidth=1.0,
                alpha=0.5, zorder=0)
    # 使用数据计算标注位置
    y_ann = ax.get_ylim()[1] * 0.7 if ax.get_ylim()[1] > 1 else resp.max() * 1.5
    ax.annotate("$T_c$ ~ 82 K", xy=(82, y_ann),
                xytext=(68, y_ann * 1.3),
                fontsize=6, color="#D62728", ha="center",
                arrowprops=dict(arrowstyle="->", color="#D62728",
                               lw=0.6, alpha=0.5))


# ═══════════════════════════════════════════════════════
# 主画图函数
# ═══════════════════════════════════════════════════════

def plot_responsivity_analysis(cache_path: str = DEFAULT_CACHE,
                                output_dir: str = DEFAULT_OUTPUT_DIR,
                                preset: str = "prb_double"):
    """生成光学响应率分析四面板出版图。"""
    # ── 加载 ──
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    rnames = cache["metadata"]["resonator_names"]
    dataset = cache["metadata"]["dataset_name"]

    # ── 样式 ──
    cfg = apply_style(preset)
    fig_width = cfg["width_inches"]          # 7.0"
    fig_height = fig_width * 0.95            # ~6.65"
    fig, axes = plt.subplots(2, 2, figsize=(fig_width, fig_height))

    # ── (a) R4 — 最佳谐振子, 全温度 ──
    plot_dff_panel(axes[0, 0], cache, "R4",
                    f"{PANEL_LABELS[0]}  R4  —  4 representative T (8 measured, 6–77 K)",
                    DETAIL_TEMPERATURES, show_fit=True, show_legend=True)

    # ── (b) R1 — 参考谐振子, 全温度 ──
    plot_dff_panel(axes[0, 1], cache, "R1",
                    f"{PANEL_LABELS[1]}  R1  —  4 representative T (8 measured, 6–77 K)",
                    DETAIL_TEMPERATURES, show_fit=True, show_legend=True)

    # ── (c) 全部谐振子 @ 6K — 器件间一致性 ──
    ax_c = axes[1, 0]
    for rname in rnames:
        laser_pwrs, dff_ppm, dff_err, bs = extract_panel_data(cache, CROSSCHECK_T, rname)
        color = get_resonator_color(rname)

        ax_c.errorbar(laser_pwrs, dff_ppm, yerr=dff_err,
                       color=color, marker="o", markersize=5,
                       linewidth=0, capsize=2.5, capthick=0.8,
                       alpha=0.9, zorder=2)

        # 拟合虚线
        if not np.isnan(bs["slope"]):
            x_fit = np.linspace(0, 9, 50)
            ax_c.plot(x_fit, bs["slope"] * x_fit,
                       color=color, linestyle="--",
                       linewidth=1.2, alpha=0.5, zorder=1)

        # 图例: 彩色标记 + 响应率
        ax_c.plot([], [], color=color, marker="o", markersize=5,
                   label=f"{rname}  R={abs(bs['slope']):.0f} ppm/mW")

    ax_c.axhline(y=0, color="#999999", linewidth=0.6, linestyle=":", zorder=0)
    ax_c.set_xlabel("Laser Power (mW)")
    ax_c.set_ylabel(r"$\delta f / f_0$ (ppm)")
    ax_c.set_title(f"{PANEL_LABELS[2]}  All resonators @ {CROSSCHECK_T} K — device consistency",
                    fontsize=8, fontweight="bold")
    ax_c.grid(True, alpha=0.22)
    ax_c.legend(loc="lower left", fontsize=5.5, framealpha=0.7,
                 handlelength=1.3)

    # ── (d) 响应率 vs 温度 ──
    plot_responsivity_summary(axes[1, 1], cache, rnames)
    axes[1, 1].set_title(f"{PANEL_LABELS[3]}  Responsivity vs Temperature",
                          fontsize=8, fontweight="bold")

    # ── 总标题 ──
    fig.suptitle(f"Optical Responsivity — YBCO CPW Resonators\n"
                 f"{dataset},  $P_{{\\rm read}}$ = −55 dBm",
                 fontsize=9, y=0.995)

    plt.tight_layout()

    # ── 保存 ──
    basepath = Path(output_dir) / "responsivity_analysis"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    # ── 报告 ──
    print(f"\n===== Responsivity Analysis figure saved =====")
    for fmt in OUTPUT_FORMATS:
        print(f"  {basepath}.{fmt}")
    _print_responsivity_table(cache, rnames)


def _print_responsivity_table(cache: dict, rnames: list):
    """打印全部 (T, R) 的响应率表格。"""
    print(f"\n--- Responsivity Table (|R| ppm/mW, Pv=-55 dBm) ---")
    header = f"{'T(K)':>5s}" + "".join(f"{r:>10s}" for r in rnames)
    print(header)
    print("-" * len(header))
    for T_k in ALL_TEMPERATURES:
        if T_k not in cache["collected"]:
            continue
        row = f"{T_k:5d}"
        for rname in rnames:
            info = get_responsivity_with_error(cache, T_k, rname, PV_IDX)
            if np.isnan(info["responsivity_ppm_per_mw"]):
                row += f"{'N/A':>10s}"
            elif _is_anomalous(info["dff_ppm"], T_k):
                row += f"{'bad':>10s}"
            else:
                r_val = info["responsivity_ppm_per_mw"]
                row += f"{abs(r_val):9.1f} "
        print(row)
    print(f"\n  R^2 values for R4:")
    for T_k in ALL_TEMPERATURES:
        if T_k not in cache["collected"]:
            continue
        info = get_responsivity_with_error(cache, T_k, "R4", PV_IDX)
        if _is_anomalous(info["dff_ppm"], T_k):
            print(f"    T={T_k:3d}K: [ANOMALOUS, skipped]")
            continue
        print(f"    T={T_k:3d}K: R={info['responsivity_ppm_per_mw']:.1f} ppm/mW, "
              f"R^2={info['r_squared']:.4f}, "
              f"CI=[{info['responsivity_ci95'][0]:.1f}, {info['responsivity_ci95'][1]:.1f}]")


# ═══════════════════════════════════════════════════════
# CLI / self-check
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate optical responsivity analysis figure")
    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help="Cache pickle path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                        help="Output directory")
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"])
    args = parser.parse_args()

    if not Path(args.cache).exists():
        print(f"Cache not found: {args.cache}")
        sys.exit(1)

    plot_responsivity_analysis(args.cache, args.output, args.preset)
