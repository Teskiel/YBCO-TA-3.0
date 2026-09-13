# -*- coding: utf-8 -*-
"""
Figure: Multi-Panel Paper Summary — YBCO CPW Kinetic Inductance Detectors

四面板论文总图 (2×2), 展示谐振器完整物理特性:

  (a) 左上: f₀ vs Temperature — 5 谐振子频率红移, 误差条, Tc 标注
  (b) 右上: S21 透射谱 @ 6 K — 原始数据展示谐振谷, 谐振子标注
  (c) 左下: df/f₀ vs Laser Power — R4 光学响应 @ 4 温度, 误差条+线性拟合
  (d) 右下: 响应率 vs Temperature — |R|, 5 谐振子, Bootstrap 95% CI

物理叙事:
  低温下 YBCO 谐振器展现高 Q 深谷; 随温度升高, f₀ 红移 (动能电感增大);
  近 Tc 时光学响应率爆发 (超流密度骤降); 四图互补构成完整物理图像。

数据来源: cache pickle + merged S2P 文件
"""

import sys, pickle
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import skrf as rf

_draw_dir = Path(__file__).resolve().parent.parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))
from _style_config import (apply_style, get_figsize, get_resonator_color,
                            save_figure, OUTPUT_FORMATS, TEMPERATURE_COLORS)
from _error_estimation import (estimate_f0_uncertainty_mhz, get_dip_depth_map,
                                get_dff_value_and_error, get_responsivity_with_error,
                                bootstrap_responsivity, estimate_dff_uncertainty)

# ═══════════════════════════════════════════════════════
# 路径
# ═══════════════════════════════════════════════════════
# 缓存根目录：环境变量 YBCO_DRAW_CACHE_ROOT 优先，否则取仓库内相对路径。
# 历史上下面的路径写死成旧机器的绝对路径（旧机器目录），换机器即
# 失效，且仓库是 PUBLIC，等于公开那台机器的目录结构。
# 详见 docs/multi-machine.md §5。机器专属根目录请设 YBCO_DRAW_CACHE_ROOT。
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
from _paths import cache_root as _cache_root
_CACHE_ROOT = str(_cache_root()).replace("\\", "/").rstrip("/")

DEFAULT_CACHE = str(
    Path("" + _CACHE_ROOT + "/output/_cache/"
         "_cache_20260609-0624__6-80K__full.pkl"))
DEFAULT_DATA_DIR = str(
    Path("" + _CACHE_ROOT + "/"
         "20260609-0624__6-80K__full"))
DEFAULT_OUTPUT_DIR = str(
    Path("" + _CACHE_ROOT + "/output/_cache/"
         "plot_output"))

# ═══════════════════════════════════════════════════════
# 参数
# ═══════════════════════════════════════════════════════
PANEL_LABELS = ["(a)", "(b)", "(c)", "(d)"]
RESP_TEMPERATURES = [6, 20, 40, 77]       # 面板 (c) 4 组温度
ALL_T_RESPS = [6, 10, 20, 40, 50, 60, 70, 77]
MAX_ANOMALOUS_PL1_PPM = 5000
PV_IDX = 0                                # -55 dBm

# 面板 (a) 固定颜色 (来自 _style_config)
T_COLORS = TEMPERATURE_COLORS
# 面板 (c) 温度固定色
TC_FIXED = {6: "#2166AC", 20: "#66BD63", 40: "#FDAE61", 77: "#D73027"}

# ═══════════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════════

def load_cache(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def _is_anomalous(dff_ppm: np.ndarray) -> bool:
    if len(dff_ppm) < 2:
        return False
    return abs(dff_ppm[1]) > MAX_ANOMALOUS_PL1_PPM


def find_s2p(data_dir: str, T: int, pv_dbm: int, pl_mw: int) -> str | None:
    pattern = f"{T}K/{pv_dbm}dBm/{pl_mw:02d}mW/*.s2p"
    matches = sorted(Path(data_dir).glob(pattern))
    return str(matches[0]) if matches else None


# ═══════════════════════════════════════════════════════
# 面板 (a): f₀ vs Temperature
# ═══════════════════════════════════════════════════════

def plot_f0_vs_T(ax, cache: dict):
    """f₀(T) — 5 谐振子频率温度演化。"""
    temps = cache["metadata"]["temperatures_k"]
    rnames = cache["metadata"]["resonator_names"]
    depths = get_dip_depth_map(cache)

    # 提取 f₀(T) 数据
    data = {}
    for rname in rnames:
        T_list, f0_list, err_list = [], [], []
        for T_k in temps:
            ident = cache["identification"].get(T_k)
            if ident is None:
                continue
            for r in ident["resonators"]:
                if r["name"] == rname:
                    f0 = r["f0_ghz"]
                    dip = depths.get(T_k, {}).get(rname, -5.0)
                    sigma_mhz = estimate_f0_uncertainty_mhz(dip, f0)
                    T_list.append(T_k)
                    f0_list.append(f0)
                    err_list.append(sigma_mhz / 1e3)  # MHz → GHz
                    break
        data[rname] = (np.array(T_list), np.array(f0_list), np.array(err_list))

    # 坐标范围
    f0_all = np.concatenate([d[1] for d in data.values()])
    ax.set_xlim(min(temps) - 2, max(temps) + 8)
    ax.set_ylim(f0_all.min() - 0.15, f0_all.max() + 0.15)

    # 正常态区域
    ax.axvspan(77, max(temps) + 3, alpha=0.06, color="red", zorder=0)

    # 谐振子曲线
    for rname in rnames:
        T_arr, f0_arr, err_arr = data[rname]
        color = get_resonator_color(rname)
        ax.errorbar(T_arr, f0_arr, yerr=err_arr,
                     color=color, marker="o", markersize=3.5,
                     linewidth=1.8, capsize=2, capthick=0.8,
                     label=rname, zorder=3)

    # Tc 标注
    tc = 82
    ax.axvline(x=tc, color="#D62728", linestyle="--", linewidth=1.0, alpha=0.5, zorder=1)
    ax.annotate("$T_c$ ~ 82 K", xy=(tc, ax.get_ylim()[0] + 0.25),
                xytext=(tc + 2.5, ax.get_ylim()[1] - 0.15),
                fontsize=6.5, color="#D62728",
                arrowprops=dict(arrowstyle="->", color="#D62728", lw=0.7))

    ax.set_ylabel("$f_0$ (GHz)")
    ax.set_title(f"{PANEL_LABELS[0]}  Resonant frequency vs Temperature",
                  fontsize=8, fontweight="bold")
    ax.legend(loc="upper right", ncol=1, fontsize=5.5, framealpha=0.7,
               handlelength=1.2)
    ax.grid(True, alpha=0.22)


# ═══════════════════════════════════════════════════════
# 面板 (b): S21 @ 6K
# ═══════════════════════════════════════════════════════

def plot_s21_6K(ax, cache: dict, data_dir: str):
    """S21 透射谱 @ 6K, -55 dBm, Pl=0 mW。"""
    T_sel = 6
    pv_sel = -55
    s2p_path = find_s2p(data_dir, T_sel, pv_sel, 0)
    if s2p_path is None:
        ax.text(0.5, 0.5, "S2P file not found", transform=ax.transAxes,
                ha="center", va="center", color="red")
        return

    ntwk = rf.Network(s2p_path)
    freq = ntwk.f / 1e9
    s21_db = 20 * np.log10(np.abs(ntwk.s[:, 1, 0]))

    ax.plot(freq, s21_db, color="#2166AC", linewidth=1.2, rasterized=True, zorder=2)

    # 谐振子标注
    ident = cache["identification"].get(T_sel)
    if ident:
        y_top = s21_db.max() + 1.5
        for r in ident["resonators"]:
            f0 = r["f0_ghz"]
            color = get_resonator_color(r["name"])
            ax.axvline(x=f0, color=color, linestyle=":", linewidth=0.6,
                        alpha=0.45, zorder=1)
            ax.annotate(r["name"], xy=(f0, y_top - 1.5),
                        fontsize=5.5, color=color, fontweight="bold",
                        ha="center", va="top", zorder=5)

    ax.set_xlim(3.15, 5.55)
    ax.set_ylim(s21_db.min() - 2, s21_db.max() + 3)
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("$S_{21}$ (dB)")
    ax.set_title(f"{PANEL_LABELS[1]}  Transmission @ 6 K, $P_{{\\rm read}}$ = −55 dBm",
                  fontsize=8, fontweight="bold")
    ax.grid(True, alpha=0.22)


# ═══════════════════════════════════════════════════════
# 面板 (c): df/f₀ vs Laser Power
# ═══════════════════════════════════════════════════════

def plot_dff_vs_Pl(ax, cache: dict):
    """δf/f₀ vs Pl — R4 @ 4 温度, 误差条 + 线性拟合。"""
    rname = "R4"
    for T_k in RESP_TEMPERATURES:
        if T_k not in cache["collected"]:
            continue
        laser_pwrs, dff_ppm, dff_err = get_dff_value_and_error(
            cache, T_k, rname, PV_IDX)
        if _is_anomalous(dff_ppm):
            continue

        color = TC_FIXED.get(T_k, "#888888")
        bs = bootstrap_responsivity(laser_pwrs, dff_ppm)

        ax.errorbar(laser_pwrs, dff_ppm, yerr=dff_err,
                     color=color, marker="o", markersize=5,
                     linewidth=0, capsize=2.5, capthick=0.8,
                     alpha=0.9, zorder=2)

        if not np.isnan(bs["slope"]):
            x_fit = np.linspace(0, 9, 50)
            ax.plot(x_fit, bs["slope"] * x_fit, color=color,
                     linestyle="--", linewidth=1.2, alpha=0.5, zorder=1)

        ax.plot([], [], color=color, marker="o", markersize=5,
                 label=f"T={T_k}K  R={bs['slope']:.0f} ppm/mW")

    ax.axhline(y=0, color="#999999", linewidth=0.6, linestyle=":", zorder=0)
    ax.set_xlabel("Laser Power (mW)")
    ax.set_ylabel(r"$\delta f / f_0$ (ppm)")
    ax.set_title(f"{PANEL_LABELS[2]}  Optical response — R4",
                  fontsize=8, fontweight="bold")
    ax.legend(loc="lower left", fontsize=5.5, framealpha=0.7, handlelength=1.3)
    ax.grid(True, alpha=0.22)


# ═══════════════════════════════════════════════════════
# 面板 (d): 响应率 vs Temperature
# ═══════════════════════════════════════════════════════

def plot_R_vs_T(ax, cache: dict):
    """|R| vs T — 全部谐振子, Bootstrap 95% CI。"""
    rnames = cache["metadata"]["resonator_names"]

    for rname in rnames:
        temps, resp_vals = [], []
        ci_lows, ci_highs = [], []
        for T_k in ALL_T_RESPS:
            if T_k not in cache["collected"]:
                continue
            _, dff_ppm, _ = get_dff_value_and_error(cache, T_k, rname, PV_IDX)
            if _is_anomalous(dff_ppm):
                continue
            info = get_responsivity_with_error(cache, T_k, rname, PV_IDX)
            if np.isnan(info["responsivity_ppm_per_mw"]):
                continue
            temps.append(T_k)
            resp_vals.append(abs(info["responsivity_ppm_per_mw"]))
            ci_lows.append(abs(info["responsivity_ci95"][0]))
            ci_highs.append(abs(info["responsivity_ci95"][1]))

        if len(temps) < 2:
            continue
        color = get_resonator_color(rname)
        ax.fill_between(temps, ci_lows, ci_highs,
                         color=color, alpha=0.12, zorder=1)
        ax.plot(temps, resp_vals, color=color, marker="s", markersize=4,
                 linewidth=1.8, label=rname, zorder=2)

    # Tc 参考线
    ax.axvline(x=82, color="#D62728", linestyle="--", linewidth=1.0,
                alpha=0.5, zorder=0)
    ax.annotate("$T_c$ ~ 82 K", xy=(82, ax.get_ylim()[1]),
                xytext=(68, ax.get_ylim()[1] * 0.7),
                fontsize=6, color="#D62728", ha="center",
                arrowprops=dict(arrowstyle="->", color="#D62728", lw=0.6))

    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("$|R|$  (ppm/mW)")
    ax.set_title(f"{PANEL_LABELS[3]}  Responsivity vs Temperature",
                  fontsize=8, fontweight="bold")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.22)
    ax.legend(loc="upper left", fontsize=6, framealpha=0.7, ncol=1,
               handlelength=1.2)


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def plot_multi_panel(cache_path: str = DEFAULT_CACHE,
                      data_dir: str = DEFAULT_DATA_DIR,
                      output_dir: str = DEFAULT_OUTPUT_DIR,
                      preset: str = "prb_double"):
    """生成四面板论文总图。"""
    cache = load_cache(cache_path)
    dataset = cache["metadata"]["dataset_name"]

    cfg = apply_style(preset)
    fig_width = cfg["width_inches"]
    fig_height = fig_width * 0.80     # 7.0 × 5.6"
    fig, axes = plt.subplots(2, 2, figsize=(fig_width, fig_height))

    # (a) f₀ vs T
    plot_f0_vs_T(axes[0, 0], cache)

    # (b) S21 @ 6K
    plot_s21_6K(axes[0, 1], cache, data_dir)

    # (c) df/f₀ vs Pl
    plot_dff_vs_Pl(axes[1, 0], cache)

    # (d) |R| vs T
    plot_R_vs_T(axes[1, 1], cache)

    # 总标题
    fig.suptitle(f"YBCO CPW Kinetic Inductance Detectors — {dataset}",
                 fontsize=9, y=0.997)

    plt.tight_layout()

    # 保存
    basepath = Path(output_dir) / "multi_panel_paper"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    print(f"\n===== Multi-panel paper figure saved =====")
    for fmt in OUTPUT_FORMATS:
        print(f"  {basepath}.{fmt}")


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate four-panel paper summary figure")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"])
    args = parser.parse_args()

    if not Path(args.cache).exists():
        print(f"Cache not found: {args.cache}")
        sys.exit(1)

    plot_multi_panel(args.cache, args.data_dir, args.output, args.preset)
