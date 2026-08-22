# -*- coding: utf-8 -*-
"""R4 谐振子 S21 局部放大图 — 多面板网格 (温度 x VNA 功率)。

基于 plot_laser_powersweep_0-9mW.py 的叠加模式，将每个 (温度, VNA功率) 组合
的 R4 局部 S21 画为子图，按行=温度、列=VNA 功率排列成多面板合并图。

生成两幅图：
  图1 (粗览): 4x4 网格 — 10/20/40/77K x -25/-35/-45/-55 dBm
  图2 (精细): 5x6 网格 — 40/50/60/70/77K x -55/-51/-47/-43/-39/-35 dBm

用法:
    python draw/plot_r4_s21_multipanel.py

输出:
    draw/output/r4_s21_multipanel/
        r4_s21_grid_10-77K_coarse.png
        r4_s21_grid_40-77K_fine.png
"""

import os
import sys
import glob

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import savgol_filter
import skrf as rf

# 项目模块导入
sys.path.insert(0, os.path.dirname(__file__))
from _data_cache import load_cache

# =========================================================================
# 配置常量
# =========================================================================

# 合并后的全温区实验数据
DATA_DIR = (
    r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data"
    r"\~merged\20260609-0624__6-80K__full"
)

# 已有缓存文件（包含各温度 R4 精确 f0）
CACHE_PATH = (
    r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data"
    r"\~merged\output\_cache\_cache_20260609-0624__6-80K__full.pkl"
)

# 激光功率列表 (mW)，仅 0-9 mW
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]

# Savitzky-Golay 平滑参数
SG_WINDOW_LENGTH = 101
SG_POLYORDER = 3

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "r4_s21_multipanel")

# 两幅图的网格规格
# 格式: (rows_K, cols_dbm, filename, title_suffix)
GRID_SPECS = [
    {
        "rows_K": [10, 20, 40, 77],
        "cols_dbm": [-25, -35, -45, -55],
        "filename": "r4_s21_grid_10-77K_coarse.png",
        "title_suffix": "coarse — 10-77K / selected VNA powers",
    },
    {
        "rows_K": [40, 50, 60, 70, 77],
        "cols_dbm": [-55, -51, -47, -43, -39, -35],
        "filename": "r4_s21_grid_40-77K_fine.png",
        "title_suffix": "fine — 40-77K / dense VNA powers",
    },
]

# =========================================================================
# R4 逐温度 zoom 半窗口 (MHz)
# 基于实测 -3dB 宽度，取值约 3x 宽度，使谐振特征占画面 25-35%
# =========================================================================
R4_ZOOM_MHZ = {
    6: 6.0,
    10: 6.0,
    20: 8.0,
    40: 14.0,
    50: 17.0,
    60: 22.0,
    70: 30.0,
    77: 40.0,
}


def _get_r4_zoom_mhz(T_K: float) -> float:
    """返回 R4 在温度 T_K 下的 zoom 半窗口 (MHz)。

    精确匹配 → 字典查表；超出范围 → 边界钳位；中间值 → 线性插值。
    """
    if T_K in R4_ZOOM_MHZ:
        return R4_ZOOM_MHZ[T_K]
    temps = sorted(R4_ZOOM_MHZ.keys())
    if T_K <= temps[0]:
        return R4_ZOOM_MHZ[temps[0]]
    if T_K >= temps[-1]:
        return R4_ZOOM_MHZ[temps[-1]]
    for i in range(len(temps) - 1):
        t_lo, t_hi = temps[i], temps[i + 1]
        if t_lo <= T_K <= t_hi:
            z_lo = R4_ZOOM_MHZ[t_lo]
            z_hi = R4_ZOOM_MHZ[t_hi]
            frac = (T_K - t_lo) / (t_hi - t_lo)
            return z_lo + frac * (z_hi - z_lo)
    return 20.0  # 理论上不可达，满足静态分析


# =========================================================================
# 工具函数
# =========================================================================


def load_r4_f0_map(cache_path: str) -> dict:
    """从缓存中提取 R4 在各温度下的 f0 (GHz)。

    Args:
        cache_path: pickle 缓存文件路径。

    Returns:
        Dict[int, float]: T_K -> f0_ghz 映射。

    Raises:
        FileNotFoundError: 缓存文件不存在。
        KeyError: 缓存中缺少 R4 数据。
    """
    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"缓存文件不存在: {cache_path}")

    cache = load_cache(cache_path)
    f0_map = {}

    for T_K in cache["metadata"]["temperatures_k"]:
        ident = cache["identification"][T_K]
        for r in ident["resonators"]:
            if r["name"] == "R4" and r["f0_ghz"] is not None:
                f0_map[T_K] = r["f0_ghz"]
                break
        else:
            print(f"警告: T={T_K}K 下未找到 R4 识别结果")

    return f0_map


def smooth_s21(s21_db: np.ndarray) -> np.ndarray:
    """Savitzky-Golay 平滑 S21 dB 数据，自适应调整窗口长度。

    Args:
        s21_db: 原始 S21 dB 数组。

    Returns:
        平滑后的 S21 dB 数组；数据点不足时返回原始数组。
    """
    wl = min(SG_WINDOW_LENGTH, len(s21_db))
    if wl % 2 == 0:
        wl -= 1
    if wl < SG_POLYORDER + 2:
        return s21_db
    return savgol_filter(s21_db, wl, SG_POLYORDER)


def _find_s2p_for_pv_pl(data_dir: str, T_K: int, Pv_dbm: int, Pl_mw: int) -> str:
    """查找指定 (T, Pv, Pl) 下的 S2P 文件。

    兼容两种激光功率目录名格式: {Pl:02d}mW 和 {Pl}mW。

    Args:
        data_dir: 数据集根目录。
        T_K: 目标温度 (K)。
        Pv_dbm: VNA 功率 (dBm)。
        Pl_mw: 激光功率 (mW)。

    Returns:
        S2P 文件路径，未找到则返回空字符串。
    """
    pv_dir = os.path.join(data_dir, f"{T_K}K", f"{Pv_dbm}dBm")

    # 先尝试补齐两位的格式 (00mW, 01mW, ...)
    pl_dir = os.path.join(pv_dir, f"{Pl_mw:02d}mW")
    pattern = os.path.join(pl_dir, "*.s2p")
    files = glob.glob(pattern)
    if files:
        return files[0]  # 通常只有一个 S2P 文件

    # 回退：尝试不补齐的格式 (0mW, 1mW, ...)
    pl_dir = os.path.join(pv_dir, f"{Pl_mw}mW")
    pattern = os.path.join(pl_dir, "*.s2p")
    files = glob.glob(pattern)
    if files:
        return files[0]

    return ""


# =========================================================================
# 绘图函数
# =========================================================================


def draw_single_cell(
    ax: plt.Axes,
    data_dir: str,
    T_K: int,
    Pv_dbm: int,
    f0_ghz: float,
    laser_powers_mw: list,
):
    """在指定 Axes 上绘制单个 (T, Pv) 格的 R4 局部 S21 叠加图。

    每格叠加 6 条曲线 (0-9 mW 激光功率)，jet 色谱着色，f0 虚线标注。

    Args:
        ax: matplotlib Axes 对象。
        data_dir: 数据集根目录。
        T_K: 温度 (K)。
        Pv_dbm: VNA 功率 (dBm)。
        f0_ghz: R4 在该温度下的谐振频率 (GHz)。
        laser_powers_mw: 激光功率列表 (mW)。
    """
    half_window_ghz = _get_r4_zoom_mhz(T_K) / 1000.0
    n_pl = len(laser_powers_mw)
    any_plotted = False

    # 用于自适应 Y 轴范围
    y_min_accum = float("inf")
    y_max_accum = float("-inf")

    for i, Pl_mw in enumerate(laser_powers_mw):
        fp = _find_s2p_for_pv_pl(data_dir, T_K, Pv_dbm, Pl_mw)
        if not fp:
            continue

        try:
            ntwk = rf.Network(fp)
            freq = ntwk.f / 1e9
            s21 = ntwk.s[:, 1, 0]
            s21_db = 20 * np.log10(np.abs(s21))

            # 过滤无效值
            valid = np.isfinite(s21_db)
            if not valid.any():
                continue
            freq = freq[valid]
            s21_db = s21_db[valid]

            # Zoom 窗口
            mask = np.abs(freq - f0_ghz) <= half_window_ghz
            if mask.sum() < 10:
                continue

            f_zoom = freq[mask]
            s_zoom = s21_db[mask]
            s_smooth = smooth_s21(s_zoom)

            color = plt.cm.jet(i / max(n_pl - 1, 1))
            ax.plot(
                f_zoom, s_smooth,
                color=color, linewidth=2.0, alpha=0.85,
                antialiased=True,
            )
            any_plotted = True
            y_min_accum = min(y_min_accum, float(np.min(s_smooth)))
            y_max_accum = max(y_max_accum, float(np.max(s_smooth)))

        except Exception as e:
            print(f"读取失败 T={T_K}K Pv={Pv_dbm}dBm Pl={Pl_mw}mW: {e}")

    # f0 参考线
    ax.axvline(x=f0_ghz, color="black", linestyle="--", alpha=0.35, linewidth=0.8)

    # 子图标题
    ax.set_title(f"T={T_K}K, {Pv_dbm}dBm", fontsize=8, pad=2)

    # 逐格自适应坐标轴范围
    if any_plotted and np.isfinite(y_min_accum) and np.isfinite(y_max_accum):
        # X 轴：精确控制 zoom 窗口
        ax.set_xlim(f0_ghz - half_window_ghz, f0_ghz + half_window_ghz)
        # Y 轴：数据范围 + 12% margin（最低 0.5 dB）
        y_range = y_max_accum - y_min_accum
        margin = max(y_range * 0.12, 0.5)
        ax.set_ylim(y_min_accum - margin, y_max_accum + margin)
    elif not any_plotted:
        # 如果没有绘制任何曲线，显示提示文字
        ax.text(
            0.5, 0.5, "No data",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=10, color="gray",
        )

    ax.grid(True, alpha=0.25)


def build_grid_figure(grid_spec: dict, f0_map: dict, data_dir: str, output_dir: str):
    """根据网格规格构建一幅完整的多面板图。

    Args:
        grid_spec: 包含 rows_K, cols_dbm, filename, title_suffix 的字典。
        f0_map: T_K -> f0_ghz 映射。
        data_dir: 数据集根目录。
        output_dir: 输出目录路径。
    """
    rows_K = grid_spec["rows_K"]
    cols_dbm = grid_spec["cols_dbm"]
    filename = grid_spec["filename"]
    title_suffix = grid_spec["title_suffix"]

    n_rows = len(rows_K)
    n_cols = len(cols_dbm)

    print(f"\n--- Building: {filename} ({n_rows}x{n_cols}) ---")

    # 根据网格大小自适应画布尺寸
    figsize_map = {
        (4, 4): (22, 16),
        (5, 6): (28, 18),
    }
    figsize = figsize_map.get((n_rows, n_cols), (n_cols * 5, n_rows * 4))

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=figsize,
        dpi=150,
        sharex="row",
        sharey=False,
        squeeze=False,
        constrained_layout=True,
    )

    laser_powers = LASER_POWERS_MW

    # 逐格绘制
    for i_row, T_K in enumerate(rows_K):
        f0_ghz = f0_map.get(T_K)
        if f0_ghz is None:
            print(f"  Skipping T={T_K}K: no R4 f0 data")
            for j_col in range(n_cols):
                ax = axes[i_row][j_col]
                ax.text(0.5, 0.5, f"No f0\nT={T_K}K", transform=ax.transAxes,
                        ha="center", va="center", fontsize=9, color="gray")
                ax.grid(True, alpha=0.15)
            continue

        for j_col, Pv_dbm in enumerate(cols_dbm):
            ax = axes[i_row][j_col]
            draw_single_cell(ax, data_dir, T_K, Pv_dbm, f0_ghz, laser_powers)

    # ---- 行标签 (温度 + Y 轴) ----
    for i_row, T_K in enumerate(rows_K):
        axes[i_row][0].set_ylabel(
            f"T={T_K} K\n|S21| (dB)",
            fontsize=9,
        )

    # ---- 列标签 (VNA 功率) — 仅顶部行标题 ----
    for j_col, Pv_dbm in enumerate(cols_dbm):
        axes[0][j_col].set_title(
            f"Pv={Pv_dbm} dBm",
            fontsize=9, pad=3, fontweight="bold", color="#333333",
        )

    # ---- 底部行 X 轴标签 ----
    for j_col in range(n_cols):
        axes[-1][j_col].set_xlabel("Freq (GHz)", fontsize=8)

    # ---- 总标题 ----
    fig.suptitle(
        f"R4 S21 Zoom — Laser Power 0-9 mW\n"
        f"T: {rows_K} K  |  Pv: {cols_dbm} dBm  |  {title_suffix}",
        fontsize=12, fontweight="bold", y=1.01,
    )

    # ---- 共享颜色条 (激光功率) ----
    # 在每个子图叠加激光功率曲线，颜色条标注激光功率
    sm = plt.cm.ScalarMappable(
        norm=plt.Normalize(vmin=laser_powers[0], vmax=laser_powers[-1]),
        cmap=plt.cm.jet,
    )
    cbar = fig.colorbar(
        sm, ax=axes,
        orientation="vertical",
        pad=0.008,
        aspect=40,
        shrink=0.92,
    )
    cbar.set_label("Laser Power (mW)", fontsize=11)
    cbar.set_ticks(laser_powers)

    # constrained_layout 自动处理布局和 suptitle 间距

    # ---- 保存 ----
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    print(f"  Saved: {output_path}")


# =========================================================================
# 主入口
# =========================================================================


def main():
    """主入口: 加载缓存、遍历网格规格、输出两幅多面板图。"""
    # 1. Load R4 f0 map
    print("=" * 60)
    print("R4 S21 Multi-Panel Zoom Plot")
    print("=" * 60)

    print(f"\nLoading cache: {CACHE_PATH}")
    f0_map = load_r4_f0_map(CACHE_PATH)
    print(f"Loaded R4 f0 for: {sorted(f0_map.keys())} K")
    for T_K in sorted(f0_map.keys()):
        print(f"  T={T_K:3d}K  f0={f0_map[T_K]:.4f} GHz")

    # 2. Verify data directory
    if not os.path.isdir(DATA_DIR):
        print(f"\nERROR: Data directory not found: {DATA_DIR}")
        sys.exit(1)
    print(f"\nData directory: {DATA_DIR}")

    # 3. Generate two grid figures
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for spec in GRID_SPECS:
        # Check required temperatures exist in f0_map
        missing = [t for t in spec["rows_K"] if t not in f0_map]
        if missing:
            print(f"\nERROR: Missing R4 f0 for temperatures: {missing}")
            continue

        build_grid_figure(spec, f0_map, DATA_DIR, OUTPUT_DIR)

    # 4. Output path report
    print("\n" + "=" * 60)
    print("Output files:")
    for spec in GRID_SPECS:
        output_path = os.path.join(OUTPUT_DIR, spec["filename"])
        if os.path.exists(output_path):
            size_kb = os.path.getsize(output_path) / 1024
            print(f"  [OK] {output_path}  ({size_kb:.0f} KB)")
        else:
            print(f"  [FAIL] {output_path}  (未生成)")
    print("=" * 60)


if __name__ == "__main__":
    main()
