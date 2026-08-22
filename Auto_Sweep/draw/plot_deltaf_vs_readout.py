# -*- coding: utf-8 -*-
"""
R4 光学响应率 vs 温度 — 不同 VNA 读出功率对比

纵轴: 响应率 = (delta_f/f  at 9mW) / 9mW  * 1e6  [ppm/mW]
横轴: 温度 (K), 6–77 K
每个温度处 N 列散点 (对应不同 VNA 读出功率), 仅散点不连线不拟合.

数据来源: _data_cache.py 生成的 pickle 缓存
用法:
  python draw/plot_deltaf_vs_readout.py \
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

# ── 路径 & 可选 _style_config 导入 ──────────────────────────
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

# 固定: R4, Pl=9mW, 全部温度
RESONATOR = "R4"
LASER_POWER_MW = 9                          # 目标激光功率
TEMPERATURES_K = [6, 10, 20, 40, 50, 60, 70, 77]

# x 轴 dodge 宽度: 每个温度处 N 列点的总水平散布 (K)
DODGE_WIDTH = 1.2

# VNA 功率配色 (参照 plot_all.py _VNA_CMAP_BASE)
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

def extract_r4_response(cache: dict, T_K: float, vna_filter: list = None):
    """提取 R4 在温度 T 下, Pl=9mW 的响应率数据.

    Parameters
    ----------
    cache : dict
        pickle 加载的缓存字典.
    T_K : float
        温度 (K).
    vna_filter : list of int or None
        要保留的 VNA 功率列表. None 表示全部.

    Returns
    -------
    dict or None
        {"pv_list": [...], "response_ppm_per_mw": [...], "flags": [...]}
        如果该温度缺失 R4 数据则返回 None.
    """
    if T_K not in cache.get("collected", {}):
        return None

    c = cache["collected"][T_K]
    if RESONATOR not in c.get("data", {}):
        return None

    laser_powers = cache["metadata"]["laser_powers_mw"]
    try:
        pl_idx = laser_powers.index(LASER_POWER_MW)
    except ValueError:
        print(f"  [WARN] Laser power {LASER_POWER_MW} mW not in cache metadata")
        return None

    d = c["data"][RESONATOR]
    dff = d["delta_f_over_f"]          # shape: (n_pv, n_pl)
    flags = d["flags"]                  # shape: (n_pv, n_pl)
    pv_available = c["vna_powers_dbm"]

    # 过滤 VNA 功率
    if vna_filter is not None:
        vna_set = set(vna_filter)
        indices = [i for i, pv in enumerate(pv_available) if pv in vna_set]
    else:
        indices = list(range(len(pv_available)))

    pv_list = []
    response_list = []
    flag_list = []

    for i_pv in indices:
        val = dff[i_pv, pl_idx]
        fl = flags[i_pv, pl_idx]

        if fl == "lost":
            continue              # 丢失的点不画
        if np.isnan(val):
            continue

        # 响应率 (ppm/mW) = (delta_f/f) / 9mW * 1e6
        response = val * 1e6 / LASER_POWER_MW
        pv_list.append(pv_available[i_pv])
        response_list.append(response)
        flag_list.append(fl)

    if not pv_list:
        return None

    return {
        "pv_list": pv_list,
        "response_ppm_per_mw": np.array(response_list),
        "flags": flag_list,
    }


# ═════════════════════════════════════════════════════════════
# 画图
# ═════════════════════════════════════════════════════════════

def plot_deltaf_vs_readout(cache: dict, output_path: str,
                           vna_powers_filter: list = None,
                           preset: str = "quick_check"):
    """主画图函数.

    Parameters
    ----------
    cache : dict
        缓存数据.
    output_path : str
        输出 PNG 路径.
    vna_powers_filter : list of int or None
        VNA 功率过滤列表.
    preset : str
        样式预设名 (传给 _style_config.apply_style).
    """
    # ── 收集全部温度下的数据 ──
    all_temps = []                         # x 坐标
    all_responses = []                     # y 坐标
    all_pv = []                            # VNA 功率 (着色)
    all_flags = []                         # tracking 质量 (marker)
    skipped_temps = []

    for T_K in TEMPERATURES_K:
        result = extract_r4_response(cache, T_K, vna_powers_filter)
        if result is None:
            skipped_temps.append(T_K)
            continue
        n_pts = len(result["pv_list"])
        all_temps.extend([T_K] * n_pts)
        all_responses.extend(result["response_ppm_per_mw"])
        all_pv.extend(result["pv_list"])
        all_flags.extend(result["flags"])

    if skipped_temps:
        print(f"跳过的温度 (无 R4 数据): {skipped_temps}")

    if not all_temps:
        print("[ERROR] 没有任何有效数据点, 退出.")
        return

    all_temps = np.array(all_temps)
    all_responses = np.array(all_responses)
    all_pv = np.array(all_pv)

    # ── 唯一 VNA 功率列表 & 配色 ──
    unique_pv = sorted(set(all_pv))
    n_pv = len(unique_pv)
    pv_colors = get_vna_colors(n_pv)
    pv_to_color = {pv: pv_colors[i] for i, pv in enumerate(unique_pv)}
    pv_to_rank = {pv: i for i, pv in enumerate(unique_pv)}

    print(f"VNA 功率列数: {n_pv}  →  {unique_pv}")
    print(f"数据点数: {len(all_temps)}")

    # ── 创建图 ──
    if _HAS_STYLE:
        cfg = apply_style(preset)
        fig, ax = plt.subplots(figsize=get_figsize(preset, aspect=0.75))
    else:
        plt.rcParams.update({
            "font.family": "sans-serif",
            "font.size": 12,
            "axes.labelsize": 14,
            "axes.titlesize": 15,
            "legend.fontsize": 10,
        })
        fig, ax = plt.subplots(figsize=(10, 6), dpi=150)

    # ── 画散点 (每个温度处 dodge 排列成列) ──
    for T_K in TEMPERATURES_K:
        mask = all_temps == T_K
        if not mask.any():
            continue

        pv_at_t = all_pv[mask]
        resp_at_t = all_responses[mask]
        flags_at_t = [all_flags[i] for i, m in enumerate(mask) if m]

        # 按 VNA 功率排序, 计算 dodge 偏移
        n_at_t = len(pv_at_t)
        for j in range(n_at_t):
            pv = pv_at_t[j]
            rank = pv_to_rank[pv]
            # dodge: 均匀分布在 T ± DODGE_WIDTH/2
            if n_pv > 1:
                x_offset = DODGE_WIDTH * (rank / (n_pv - 1) - 0.5)
            else:
                x_offset = 0.0
            x = T_K + x_offset

            color = pv_to_color[pv]
            fl = flags_at_t[j]

            if fl == "tracked":
                ax.scatter(x, resp_at_t[j], color=color, marker="o",
                           s=50, alpha=0.85, edgecolors="none", zorder=3)
            elif fl == "shallow":
                ax.scatter(x, resp_at_t[j], facecolors="none",
                           edgecolors=color, marker="D",
                           s=60, alpha=0.5, linewidths=1.2, zorder=2)

    # ── Y=0 参考线 ──
    ax.axhline(y=0, color="gray", linestyle=":", linewidth=1, alpha=0.6, zorder=1)

    # ── 坐标轴标签 ──
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel(r"Response Rate  $\partial(\Delta f/f_0)/\partial P_{\rm laser}$  (ppm/mW)")
    ax.set_title(f"{RESONATOR}  Optical Response Rate @ {LASER_POWER_MW} mW  |  "
                 r"$\partial(\Delta f/f_0)/\partial P_{\rm laser}$ vs T")

    # ── X 轴刻度 ──
    ax.set_xticks(TEMPERATURES_K)
    ax.set_xlim(TEMPERATURES_K[0] - 2, TEMPERATURES_K[-1] + 2)

    # ── 网格 ──
    ax.grid(True, alpha=0.25, linestyle="--")

    # ── Colorbar: VNA 功率 → 颜色 ──
    sm = plt.cm.ScalarMappable(
        norm=plt.Normalize(vmin=min(unique_pv), vmax=max(unique_pv)),
        cmap=matplotlib.colors.ListedColormap(pv_colors))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, pad=0.02)
    cbar.set_label("VNA Readout Power (dBm)", fontsize=11)

    # ── 图例 (marker 类型) ──
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="gray",
               markersize=8, label="tracked"),
        Line2D([0], [0], marker="D", color="w", markerfacecolor="none",
               markeredgecolor="gray", markersize=8, label="shallow"),
    ]
    ax.legend(handles=legend_elements, loc="lower left", framealpha=0.8)

    fig.tight_layout()

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
        description="R4 响应率 vs 温度 — VNA 读出功率对比散点图")

    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help="缓存 pickle 文件路径")
    parser.add_argument("--output", default=None,
                        help="输出目录 (默认: 缓存旁的 plot_output/)")
    parser.add_argument("--vna-powers", type=str,
                        default="-55,-49,-43,-37,-31,-25",
                        help="VNA 功率列表 (dBm), 逗号分隔 "
                             "(默认: -55,-49,-43,-37,-31,-25)")
    parser.add_argument("--preset", default="quick_check",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"],
                        help="样式预设 (默认: quick_check)")
    args = parser.parse_args()

    # 解析 VNA 功率过滤
    vna_filter = [int(x.strip()) for x in args.vna_powers.split(",")]

    # 输出路径
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = Path(args.cache).resolve().parent.parent / "plot_output"

    n_vna = len(vna_filter)
    output_file = output_dir / f"deltaf_response_vs_T_R4_{n_vna}vna.png"

    print(f"缓存: {args.cache}")
    print(f"VNA 功率: {vna_filter}")
    print(f"输出: {output_file}")

    # 加载缓存
    with open(args.cache, "rb") as f:
        cache = pickle.load(f)

    meta = cache["metadata"]
    print(f"数据集: {meta['dataset_name']}")
    print(f"温度: {meta['temperatures_k']}")
    print(f"谐振子: {meta['resonator_names']}")

    # 画图
    plot_deltaf_vs_readout(
        cache, str(output_file),
        vna_powers_filter=vna_filter,
        preset=args.preset)


if __name__ == "__main__":
    main()
