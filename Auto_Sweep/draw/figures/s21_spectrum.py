# -*- coding: utf-8 -*-
"""
Figure: S21 Transmission Spectrum at Three Temperatures

三面板出版图，展示 YBCO CPW 谐振器在三个特征温度下的全频段 S21 透射谱:
  - 6 K  (远低于 Tc，深锐谐振谷)
  - 40 K (中间温区，谷变浅)
  - 77 K (接近 Tc，谷几乎消失)

每面板叠加两条 VNA 读出功率 (-55 dBm 低功率, -25 dBm 高功率)，
展示读出功率对非线性动能电感的影响（频率偏移 + 谷深变化）。

物理意义:
  随温度升高，超导能隙 Δ(T) 减小，准粒子密度指数增长 →
  动能电感 L_k ∝ λ_L(T) 增大 → f₀ 红移 + Qi 退化。

数据来源: merged 数据集 S2P 文件 (Pl=0 mW)
"""

import sys
from pathlib import Path
import pickle
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import skrf as rf

# 导入样式模块
_draw_dir = Path(__file__).resolve().parent.parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))
from _style_config import (apply_style, get_figsize, get_resonator_color,
                            save_figure, OUTPUT_FORMATS)

# ═══════════════════════════════════════════════════════
# 默认路径
# ═══════════════════════════════════════════════════════
DEFAULT_CACHE = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "_cache_20260609-0624__6-80K__full.pkl"))

DEFAULT_DATA_DIR = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/"
         "20260609-0624__6-80K__full"))

DEFAULT_OUTPUT_DIR = str(
    Path("D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
         "plot_output"))

# ═══════════════════════════════════════════════════════
# 画图参数
# ═══════════════════════════════════════════════════════
PANEL_TEMPERATURES = [6, 40, 77]         # 三个特征温度 (K)
VNA_POWERS_DBM = [-55, -25]             # 低 / 高读出功率
LASER_POWER_MW = 0                      # 暗态 (无激光加热)
FREQ_XLIM = (3.15, 5.55)                # 频率范围 (GHz), 覆盖全部 5 个谐振子
S21_YLIM = (-22, 14)                    # S21 幅度范围 (dB)

# 读出功率配色 — 深蓝 (低功率, linear regime) vs 深红 (高功率, nonlinear)
VNA_STYLES = {
    -55: {"color": "#2166AC", "linestyle": "-",  "linewidth": 1.8,
          "label": r"$P_{read}$ = −55 dBm", "zorder": 3},
    -25: {"color": "#B2182B", "linestyle": "-",  "linewidth": 1.3,
          "label": r"$P_{read}$ = −25 dBm", "zorder": 2},
}

# 温度面板标注位置 (右上角, 数据坐标)
T_LABEL_KWARGS = dict(fontsize=9, fontweight="bold",
                       ha="right", va="top",
                       bbox=dict(boxstyle="round,pad=0.3",
                                 facecolor="white", edgecolor="#CCCCCC",
                                 alpha=0.85))


# ═══════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════

def find_s2p_file(data_dir: str, T: int, pv_dbm: int, pl_mw: int) -> str | None:
    """查找给定 (温度, VNA功率, 激光功率) 下的第一个 S2P 文件。"""
    pattern = f"{T}K/{pv_dbm}dBm/{pl_mw:02d}mW/*.s2p"
    matches = sorted(Path(data_dir).glob(pattern))
    return str(matches[0]) if matches else None


def load_s21_trace(filepath: str):
    """加载 S2P 文件，返回 (freq_GHz, s21_mag_dB)。"""
    ntwk = rf.Network(filepath)
    freq = ntwk.f / 1e9
    s21_db = 20 * np.log10(np.abs(ntwk.s[:, 1, 0]))
    return freq, s21_db


def load_resonator_positions(cache_path: str):
    """从 cache 加载谐振子在每个温度下的 f0 位置（用于标注）。"""
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    positions = {}
    for T in PANEL_TEMPERATURES:
        ident = cache["identification"].get(T)
        if ident is None:
            continue
        positions[T] = {r["name"]: r["f0_ghz"] for r in ident["resonators"]}
    return positions, cache["metadata"]["dataset_name"]


# ═══════════════════════════════════════════════════════
# 主画图函数
# ═══════════════════════════════════════════════════════

def plot_s21_spectrum(data_dir: str = DEFAULT_DATA_DIR,
                       cache_path: str = DEFAULT_CACHE,
                       output_dir: str = DEFAULT_OUTPUT_DIR,
                       preset: str = "prb_double"):
    """生成 S21 全谱三温面板出版图。"""
    # ── 加载谐振子位置 ──
    try:
        rpos, dataset_name = load_resonator_positions(cache_path)
    except FileNotFoundError:
        print(f"Warning: cache not found at {cache_path}, resonator annotations disabled")
        rpos, dataset_name = {}, "unknown"

    # ── 样式 ──
    cfg = apply_style(preset)
    fig_width = cfg["width_inches"]
    # 三面板纵向堆叠, 总高按比例放大
    fig_height = fig_width * 1.05
    fig, axes = plt.subplots(3, 1, figsize=(fig_width, fig_height),
                              sharex=True, gridspec_kw={"hspace": 0.08})

    # ── 逐面板绘图 ──
    for i, (T, ax) in enumerate(zip(PANEL_TEMPERATURES, axes)):
        ax.set_xlim(*FREQ_XLIM)
        ax.set_ylim(*S21_YLIM)

        # 背景: 正常态渐近区域 (仅 77K 面板)
        if T == 77:
            ax.axvspan(FREQ_XLIM[0], FREQ_XLIM[1], alpha=0.04,
                       color="red", zorder=0)

        # 画各 VNA 功率的 S21 迹线
        for pv_dbm in VNA_POWERS_DBM:
            s2p_path = find_s2p_file(data_dir, T, pv_dbm, LASER_POWER_MW)
            if s2p_path is None:
                print(f"  [skip] T={T}K, Pv={pv_dbm}dBm: no S2P file found")
                continue

            freq, s21_db = load_s21_trace(s2p_path)
            style = VNA_STYLES[pv_dbm]
            ax.plot(freq, s21_db,
                    color=style["color"], linestyle=style["linestyle"],
                    linewidth=style["linewidth"],
                    label=style["label"], zorder=style["zorder"],
                    rasterized=True)   # 位图化迹线, 控制矢量文件大小

        # ── 谐振子标注（在 -55 dBm 迹线的 f0 位置标记） ──
        if T in rpos:
            for rname, f0 in rpos[T].items():
                if not (FREQ_XLIM[0] < f0 < FREQ_XLIM[1]):
                    continue
                color = get_resonator_color(rname)
                # 垂直虚线标记
                ax.axvline(x=f0, color=color, linestyle=":",
                           linewidth=0.7, alpha=0.5, zorder=1)
                # 谐振子标签（上方）
                y_text = S21_YLIM[1] - 0.8 - (int(rname[1]) % 3) * 0.9
                ax.annotate(rname, xy=(f0, S21_YLIM[1] - 3.0),
                            xytext=(f0, y_text),
                            fontsize=6, color=color, fontweight="bold",
                            ha="center", va="top",
                            arrowprops=dict(arrowstyle="->", color=color,
                                            lw=0.6, alpha=0.6),
                            zorder=5)

        # ── 温度标签 ──
        ax.text(FREQ_XLIM[1] - 0.08, S21_YLIM[1] - 1.2,
                f"T = {T} K", **T_LABEL_KWARGS)

        # grid + 右侧 y 轴标签
        ax.grid(True, alpha=0.22)
        ax.set_ylabel("S$_{21}$ (dB)")

    # ── 共享 x 轴标签 ──
    axes[-1].set_xlabel("Frequency (GHz)")

    # ── 图例 (仅顶部面板, 水平排列) ──
    axes[0].legend(loc="upper left", ncol=2, framealpha=0.75,
                   fontsize=7, handlelength=1.8)

    # ── 总标题 ──
    fig.suptitle(f"S$_{{21}}$ Transmission Spectrum — YBCO CPW Resonators\n"
                 f"{dataset_name}, $P_{{laser}}$ = 0 mW",
                 fontsize=9, y=0.985)

    # ── 保存 ──
    basepath = Path(output_dir) / "s21_spectrum"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    # ── 输出 report ──
    print(f"\n===== S21 Spectrum figure saved =====")
    for fmt in OUTPUT_FORMATS:
        print(f"  {basepath}.{fmt}")
    _print_data_summary(data_dir, rpos)


def _print_data_summary(data_dir: str, rpos: dict):
    """打印数据摘要，确认每条迹线加载成功。"""
    print("\n--- Data summary ---")
    for T in PANEL_TEMPERATURES:
        for pv_dbm in VNA_POWERS_DBM:
            s2p = find_s2p_file(data_dir, T, pv_dbm, LASER_POWER_MW)
            status = "OK" if s2p else "MISSING"
            if s2p:
                fname = Path(s2p).name[:55]
            else:
                fname = "—"
            print(f"  T={T:3d}K  Pv={pv_dbm:+3d}dBm  Pl=0mW  [{status}] {fname}")
    if rpos:
        print("\n--- Resonator f0 annotation positions (GHz) ---")
        header = f"{'':>6s}" + "".join(f"{'R'+str(j+1):>10s}" for j in range(5))
        print(header)
        for T in PANEL_TEMPERATURES:
            if T in rpos:
                vals = "".join(f"{rpos[T].get(f'R{j+1}', np.nan):10.4f}"
                               for j in range(5))
                print(f"  {T:3d}K  {vals}")


# ═══════════════════════════════════════════════════════
# CLI / self-check
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate S21 spectrum three-temperature panel figure")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="Merged data directory with S2P files")
    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help="Cache pickle (for resonator positions)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                        help="Output directory")
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"])
    args = parser.parse_args()

    if not Path(args.data_dir).is_dir():
        print(f"Data directory not found: {args.data_dir}")
        sys.exit(1)

    plot_s21_spectrum(args.data_dir, args.cache, args.output, args.preset)
