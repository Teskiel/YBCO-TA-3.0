# -*- coding: utf-8 -*-
"""
全局绘图样式配置 — 唯一真源 (single source of truth).

所有新绘图模块 import 此文件获取样式，禁止在各自模块中硬编码尺寸/配色/字体。
修改一处，全局生效。

参考: Data_process/compare_resonators.py 的成熟出版级样式
"""

import matplotlib.pyplot as plt
import numpy as np

# ═══════════════════════════════════════════════════════
# 出版物预设
# ═══════════════════════════════════════════════════════
PRESETS = {
    "prb_single": {
        "width_inches": 3.375,
        "height_inches": 2.5,
        "font_size": 8,
        "dpi": 600,
    },
    "prb_double": {
        "width_inches": 7.0,
        "height_inches": 5.25,
        "font_size": 8,
        "dpi": 600,
    },
    "presentation": {
        "width_inches": 10,
        "height_inches": 7.5,
        "font_size": 14,
        "dpi": 300,
    },
    "quick_check": {
        "width_inches": 8,
        "height_inches": 6,
        "font_size": 10,
        "dpi": 150,
    },
}

DEFAULT_PRESET = "quick_check"

# ═══════════════════════════════════════════════════════
# 输出格式
# ═══════════════════════════════════════════════════════
OUTPUT_FORMATS = ["svg", "pdf", "png"]       # 出版默认 (矢量 + 位图)
QUICK_FORMATS = ["png"]                      # 快速预览

# ═══════════════════════════════════════════════════════
# 配色方案
# ═══════════════════════════════════════════════════════

# 谐振子固定色 (Tableau 10 adapted, 白底高饱和) — 来自 compare_resonators.py
RESONATOR_COLORS = {
    "R1": "#1F77B4",  # 蓝
    "R2": "#D62728",  # 红
    "R3": "#2CA02C",  # 绿
    "R4": "#FF7F0E",  # 橙
    "R5": "#9467BD",  # 紫
}
RESONATOR_COLOR_LIST = list(RESONATOR_COLORS.values())

# Tol Light 色盲友好调色板 (备选)
TOL_LIGHT = ["#0077BB", "#EE7733", "#009988", "#EE3377", "#33BBEE", "#CC3311"]

# 连续变量 colormap (colorblind-friendly, perceptually uniform)
CMAP_TEMPERATURE = "viridis"
CMAP_VNA_POWER = "plasma"
CMAP_LASER_POWER = "inferno"

# 温度固定配色 (回退用)
TEMPERATURE_COLORS = {
    6:  "#2166AC",  # 深蓝
    10: "#4393C3",
    20: "#66BD63",  # 绿
    40: "#FDAE61",  # 橙
    50: "#F46D43",
    60: "#D73027",
    70: "#A50026",
    77: "#67001F",  # 深红 (近 Tc)
    80: "#400010",
}

# ═══════════════════════════════════════════════════════
# 线条与标记
# ═══════════════════════════════════════════════════════
LINES = {
    "linewidth": 1.5,
    "markersize": 6,
    "grid_alpha": 0.25,
    "grid_color": "#CCCCCC",
    "spine_width": 1.0,
}

# ═══════════════════════════════════════════════════════
# 函数接口
# ═══════════════════════════════════════════════════════

def apply_style(preset: str = DEFAULT_PRESET):
    """应用全局 matplotlib rcParams 样式。"""
    cfg = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    plt.rcParams.update({
        "figure.dpi": cfg["dpi"] // 2,     # 屏幕预览
        "savefig.dpi": cfg["dpi"],          # 保存
        "savefig.bbox": "tight",
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
        "font.size": cfg["font_size"],
        "axes.titlesize": cfg["font_size"] + 1,
        "axes.labelsize": cfg["font_size"],
        "legend.fontsize": cfg["font_size"] - 1,
        "xtick.labelsize": cfg["font_size"] - 1,
        "ytick.labelsize": cfg["font_size"] - 1,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#222222",
        "axes.labelcolor": "#111111",
        "text.color": "#111111",
        "xtick.color": "#222222",
        "ytick.color": "#222222",
        "grid.color": LINES["grid_color"],
        "grid.alpha": LINES["grid_alpha"],
        "axes.linewidth": LINES["spine_width"],
        "xtick.major.width": 1.0,
        "ytick.major.width": 1.0,
        "lines.linewidth": LINES["linewidth"],
        "lines.markersize": LINES["markersize"],
    })
    return cfg


def get_figsize(preset: str = DEFAULT_PRESET, aspect: float = None):
    """返回 (width, height) 英寸。aspect 覆盖默认高宽比。"""
    cfg = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    w = cfg["width_inches"]
    if aspect is not None:
        return (w, w * aspect)
    return (w, cfg["height_inches"])


def get_resonator_color(name: str) -> str:
    """获取谐振子颜色。'R1'~'R5' 返回固定色，其他返回灰。"""
    return RESONATOR_COLORS.get(name, "#888888")


def get_temperature_colors(temperatures_k: list) -> list:
    """为给定温度列表返回 colormap 颜色。"""
    from matplotlib.cm import get_cmap
    cmap = get_cmap(CMAP_TEMPERATURE)
    t_min, t_max = min(temperatures_k), max(temperatures_k)
    norm = (np.array(temperatures_k) - t_min) / max(t_max - t_min, 1)
    return [cmap(v) for v in norm]


def get_vna_colors(n: int) -> list:
    """为 n 条 VNA 功率曲线返回颜色列表。"""
    from matplotlib.cm import get_cmap
    cmap = get_cmap(CMAP_VNA_POWER)
    return [cmap(i / max(n - 1, 1)) for i in range(n)]


def save_figure(fig, basepath: str, formats: list = None):
    """保存图形为多格式。basepath 不含扩展名。"""
    if formats is None:
        formats = QUICK_FORMATS
    for fmt in formats:
        path = f"{basepath}.{fmt}"
        fig.savefig(path, format=fmt, bbox_inches="tight")
        print(f"  saved: {path}")


# ═══════════════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=== _style_config self-check ===\n")
    for name, cfg in PRESETS.items():
        print(f"Preset '{name}': {cfg['width_inches']}x{cfg['height_inches']}\" @ {cfg['dpi']}dpi, font {cfg['font_size']}pt")
    print(f"\nResonator colors: {RESONATOR_COLORS}")
    print(f"Temperature cmap: {CMAP_TEMPERATURE}")
    print(f"VNA power cmap: {CMAP_VNA_POWER}")
    print(f"Default output formats: {OUTPUT_FORMATS}")
    print(f"Quick preview formats: {QUICK_FORMATS}")
    print("\napply_style('prb_single')...")
    cfg = apply_style("prb_single")
    print(f"  screen DPI: {plt.rcParams['figure.dpi']}, save DPI: {plt.rcParams['savefig.dpi']}")
    print(f"  font: {plt.rcParams['font.size']}pt, family: {plt.rcParams['font.family']}")
    print("\n[OK] All checks passed")
