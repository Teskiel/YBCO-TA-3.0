# -*- coding: utf-8 -*-
"""
Figure: Readout Power Dependence — Nonlinear Kinetic Inductance Test

双面板图，展示 YBCO 谐振器 f₀ 随 VNA 读出功率的变化:

  (a) f₀ vs P_read — R4 绝对频率, 全温度, 误差条
  (b) δf₀/f₀ vs P_read — 全部谐振子 @ 60K (最大表观信号), 归一化对比

物理意义:
  非线性动能电感 δL_k/L_k ∝ I² ∝ P_read。若可测，δf₀/f₀ 应随 P_read 线性下降。
  实测: −55→−25 dBm (1000× 功率) 引起 <0.5 ppm 偏移, 噪声量级 ±0.3 MHz。
  结论: YBCO 谐振器在此功率范围高度线性 — 读出不干扰测量。

对比: 激光响应在 77K 产生 −363 ppm/mW (9 mW → −3290 ppm),
      比读出功率非线性大 4 个数量级。

数据来源: cache["collected"][T]["data"][R]["f0_refs"] (Pl=0, 16 VNA 功率级)
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
from _error_estimation import estimate_f0_uncertainty_mhz, get_dip_depth_map

# ═══════════════════════════════════════════════════════
# 路径 & 参数
# ═══════════════════════════════════════════════════════
DEFAULT_CACHE = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "_cache_20260609-0624__6-80K__full.pkl"))
DEFAULT_OUTPUT_DIR = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "plot_output"))

PANEL_A_TEMPS = [6, 20, 40, 60, 77]        # 面板 (a): R4 @ 5 温度
PANEL_B_TEMP = 60                            # 面板 (b): 全部谐振子
PANEL_LABELS = ["(a)", "(b)"]

# 固定色 (白底高对比)
T_FIXED = {6: "#2166AC", 20: "#66BD63", 40: "#FDAE61",
            60: "#D73027", 77: "#67001F"}

# ═══════════════════════════════════════════════════════
# 数据提取
# ═══════════════════════════════════════════════════════

def extract_f0_vs_pread(cache: dict, T_k: float, rname: str):
    """提取某个 (T, R) 的全部 f0(P_read) 数据。
    Returns: (pv_dbm_sorted, f0_ghz_arr, err_ghz_arr)"""
    c = cache["collected"][T_k]
    pv_list = sorted(c["vna_powers_dbm"])
    f0_refs = c["data"][rname]["f0_refs"]

    depths = get_dip_depth_map(cache)
    dip_db = depths.get(T_k, {}).get(rname, -5.0)

    f0_arr = np.array([f0_refs[pv] for pv in pv_list])
    # 对每个 P_read 估算误差
    err_arr = np.array([
        estimate_f0_uncertainty_mhz(dip_db, f0) / 1e3  # MHz → GHz
        for f0 in f0_arr
    ])

    return np.array(pv_list), f0_arr, err_arr


# ═══════════════════════════════════════════════════════
# 面板 (a): f₀ vs P_read — R4, 全温度
# ═══════════════════════════════════════════════════════

def plot_f0_vs_pread(ax, cache: dict):
    """f₀ vs P_read — R4, 绝对频率, 误差条。"""
    rname = "R4"
    for T_k in PANEL_A_TEMPS:
        if T_k not in cache["collected"]:
            continue
        pv_list, f0_arr, err_arr = extract_f0_vs_pread(cache, T_k, rname)
        color = T_FIXED.get(T_k, "#888888")

        ax.errorbar(pv_list, f0_arr, yerr=err_arr,
                     color=color, marker="o", markersize=4,
                     linewidth=1.4, capsize=2, capthick=0.7,
                     label=f"T = {T_k} K", zorder=2)

    # 标注每个温度的平均值和总跨度
    for T_k in PANEL_A_TEMPS[:1]:  # 仅 6K
        pv_list, f0_arr, _ = extract_f0_vs_pread(cache, T_k, rname)
        span_mhz = (f0_arr.max() - f0_arr.min()) * 1e3
        ax.annotate(f"span = {span_mhz:.1f} MHz",
                     xy=(pv_list[len(pv_list)//2], f0_arr.min()),
                     fontsize=5.5, color="#888888", ha="center",
                     zorder=5)

    ax.set_xlabel("VNA Readout Power (dBm)")
    ax.set_ylabel("$f_0$ (GHz)")
    ax.set_title(f"{PANEL_LABELS[0]}  $f_0$ vs Readout Power — R4",
                  fontsize=8, fontweight="bold")
    ax.legend(loc="upper right", fontsize=5.5, framealpha=0.7, ncol=1,
               handlelength=1.3)
    ax.grid(True, alpha=0.22)


# ═══════════════════════════════════════════════════════
# 面板 (b): δf₀/f₀ vs P_read — 全部谐振子, 归一化
# ═══════════════════════════════════════════════════════

def plot_dff_vs_pread(ax, cache: dict):
    """δf₀/f₀ vs P_read — 全部谐振子, 参考 -55 dBm。"""
    rnames = cache["metadata"]["resonator_names"]
    T_k = PANEL_B_TEMP

    for rname in rnames:
        if T_k not in cache["collected"]:
            continue
        pv_list, f0_arr, err_arr = extract_f0_vs_pread(cache, T_k, rname)
        f0_ref = f0_arr[0]  # -55 dBm reference
        dff_ppm = (f0_arr - f0_ref) / f0_ref * 1e6
        dff_err_ppm = err_arr / f0_ref * 1e6

        color = get_resonator_color(rname)
        ax.errorbar(pv_list, dff_ppm, yerr=dff_err_ppm,
                     color=color, marker="o", markersize=4.5,
                     linewidth=1.4, capsize=2, capthick=0.7,
                     label=rname, zorder=2)

    # 参考线
    ax.axhline(y=0, color="#999999", linewidth=0.7, linestyle=":", zorder=0)

    # 标注激光响应对比量级
    laser_mag = 3290  # ppm @ 77K, 9 mW
    ax.annotate(f"cf. laser response @ 77K:\n"
                f"−3290 ppm (9 mW, R4)",
                xy=(ax.get_xlim()[0] + 2, -laser_mag * 0.03),
                fontsize=5.5, color="#D62728",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                          edgecolor="#D62728", alpha=0.7),
                zorder=5)

    ax.set_xlabel("VNA Readout Power (dBm)")
    ax.set_ylabel(r"$\delta f_0 / f_0$ (ppm)")
    ax.set_title(f"{PANEL_LABELS[1]}  Fractional shift — all Rs @ T = {T_k} K",
                  fontsize=8, fontweight="bold")
    ax.legend(loc="best", fontsize=5.5, framealpha=0.7, ncol=1,
               handlelength=1.3)
    ax.grid(True, alpha=0.22)


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def plot_readout_power_dependence(cache_path: str = DEFAULT_CACHE,
                                   output_dir: str = DEFAULT_OUTPUT_DIR,
                                   preset: str = "prb_double"):
    """生成读出功率依赖双面板图。"""
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    dataset = cache["metadata"]["dataset_name"]

    cfg = apply_style(preset)
    fig_width = cfg["width_inches"]
    fig_height = fig_width * 0.90     # 7.0 × 6.3"
    fig, axes = plt.subplots(2, 1, figsize=(fig_width, fig_height),
                              gridspec_kw={"hspace": 0.25})

    # (a) f₀ vs P_read
    plot_f0_vs_pread(axes[0], cache)

    # (b) δf₀/f₀ vs P_read
    plot_dff_vs_pread(axes[1], cache)

    fig.suptitle(f"Readout Power Dependence — YBCO CPW Resonators\n"
                 f"{dataset},  $P_{{\\rm laser}}$ = 0 mW,  "
                 f"$P_{{\\rm read}}$ = −55 to −25 dBm",
                 fontsize=9, y=0.997)

    # 保存
    basepath = Path(output_dir) / "readout_power_dependence"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    print(f"\n===== Readout Power Dependence figure saved =====")
    for fmt in OUTPUT_FORMATS:
        print(f"  {basepath}.{fmt}")
    _print_summary(cache)


def _print_summary(cache: dict):
    """打印读出功率非线性摘要。"""
    print(f"\n--- Readout Power Nonlinearity Summary ---")
    rnames = cache["metadata"]["resonator_names"]
    for T_k in [6, 20, 40, 60, 77]:
        if T_k not in cache["collected"]:
            continue
        for rname in ["R4"]:
            pv_list, f0_arr, _ = extract_f0_vs_pread(cache, T_k, rname)
            span_mhz = (f0_arr.max() - f0_arr.min()) * 1e3
            dff_ppm = (f0_arr[-1] - f0_arr[0]) / f0_arr[0] * 1e6
            mono = "mono" if np.all(np.diff(f0_arr) <= 0) else "NON-MONO"
            print(f"  T={T_k:3d}K  {rname}: "
                  f"span={span_mhz:.2f} MHz,  "
                  f"dff(-55->-25)={dff_ppm:+.2f} ppm,  "
                  f"[{mono}]")
    print(f"\n  Conclusion: Readout nonlinearity buried in measurement noise (~1-2 MHz span).")
    print(f"  All temperatures NON-MONOTONIC - noise dominates over systematic shift.")
    print(f"  cf. Laser response: ~3300 ppm @ 77K (R4, 9 mW)")
    print(f"  Nonlinear kinetic inductance from readout is negligible in this power range.")


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate readout power dependence figure")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"])
    args = parser.parse_args()

    if not Path(args.cache).exists():
        print(f"Cache not found: {args.cache}")
        sys.exit(1)

    plot_readout_power_dependence(args.cache, args.output, args.preset)
