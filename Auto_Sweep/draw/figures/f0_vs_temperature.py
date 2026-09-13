# -*- coding: utf-8 -*-
"""
Figure: f0 vs Temperature — 谐振频率温度演化

物理意义:
  随温度升高，YBCO 超导能隙减小，准粒子密度增加，
  动能电感增大 (L_k ~ λ_L)，导致谐振频率红移 (f0 下降)。
  在 Tc (~80-85K) 附近 f0 急剧下降，对应超导-正常态转变。

数据来源: cache pickle, Pl=0, lowest VNA power 的 f0 值
"""

import sys
from pathlib import Path
import pickle
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 导入样式和误差模块
_draw_dir = Path(__file__).resolve().parent.parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))
from _style_config import (apply_style, get_figsize, get_resonator_color,
                            save_figure, OUTPUT_FORMATS)
from _error_estimation import estimate_f0_uncertainty_mhz, get_dip_depth_map

# 缓存根目录：环境变量 YBCO_DRAW_CACHE_ROOT 优先，否则取仓库内相对路径。
# 历史上下面的路径写死成旧机器的绝对路径（旧机器目录），换机器即
# 失效，且仓库是 PUBLIC，等于公开那台机器的目录结构。
# 详见 docs/multi-machine.md §5。机器专属根目录请设 YBCO_DRAW_CACHE_ROOT。
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
from _paths import cache_root as _cache_root
_CACHE_ROOT = str(_cache_root()).replace("\\", "/").rstrip("/")


# 默认缓存路径
DEFAULT_CACHE = str(
    Path(_CACHE_ROOT + "/output/_cache/"
         "_cache_20260609-0624__6-80K__full.pkl"))

DEFAULT_OUTPUT_DIR = str(
    Path(_CACHE_ROOT + "/output/_cache/"
         "plot_output"))


def extract_f0_data(cache: dict):
    """从 cache 提取 f0(T) 数据: {resonator: (T_list, f0_list, err_list)}"""
    temps = cache["metadata"]["temperatures_k"]
    rnames = cache["metadata"]["resonator_names"]
    depths = get_dip_depth_map(cache)

    data = {}
    for rname in rnames:
        T_list, f0_list, err_list = [], [], []
        for T_k in temps:
            ident = cache["identification"].get(T_k)
            if ident is None:
                continue
            # 找到该谐振子的 f0
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
    return data


def plot_f0_vs_temperature(cache_path: str = DEFAULT_CACHE,
                            output_dir: str = DEFAULT_OUTPUT_DIR,
                            preset: str = "quick_check"):
    """生成 f0(T) 出版图。"""
    # 加载
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    dataset = cache["metadata"]["dataset_name"]

    # 提取数据
    f0_data = extract_f0_data(cache)
    temps_all = cache["metadata"]["temperatures_k"]

    # 样式
    apply_style(preset)
    fig, ax = plt.subplots(figsize=get_figsize(preset, aspect=0.75))

    # 计算坐标轴范围 (在绘图前预设, 避免 get_ylim 不可靠)
    f0_all = np.concatenate([d[1] for d in f0_data.values()])
    y_min = f0_all.min() - 0.2
    y_max = f0_all.max() + 0.2
    x_min = min(temps_all) - 2
    x_max = max(temps_all) + 8   # 右侧留空给标注
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)

    # 绘制每条谐振子曲线
    for rname in cache["metadata"]["resonator_names"]:
        T_arr, f0_arr, err_arr = f0_data[rname]
        color = get_resonator_color(rname)
        ax.errorbar(T_arr, f0_arr, yerr=err_arr,
                     color=color, marker="o", markersize=5,
                     linewidth=2.0, capsize=3, capthick=1.0,
                     label=rname, zorder=3)

    # 正常态渐近区域 (先画, 在数据下层)
    ax.axvspan(77, max(temps_all) + 3, alpha=0.06, color="red", zorder=0)
    ax.text(79.5, y_min + 0.12, "normal state",
            fontsize=7, color="#999999", ha="center", va="bottom")

    # Tc 标注 (数据坐标, 先设定好范围)
    tc_guess = 82
    ax.axvline(x=tc_guess, color="#D62728", linestyle="--", linewidth=1.2,
               alpha=0.7, zorder=1)
    ax.annotate("T$_c$ ~ 82 K", xy=(tc_guess, 3.55),
                xytext=(tc_guess + 2.5, y_max - 0.1),
                fontsize=8, color="#D62728",
                arrowprops=dict(arrowstyle="->", color="#D62728", lw=1.0))

    # 标签
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("Resonant Frequency f$_0$ (GHz)")
    ax.set_title(f"f$_0$(T) — YBCO CPW Resonators\n{dataset}", fontsize=9)
    ax.legend(loc="upper right", ncol=1, framealpha=0.8)
    ax.grid(True, alpha=0.25)

    plt.tight_layout()

    # 保存
    basepath = Path(output_dir) / "f0_vs_temperature"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    print(f"Figure saved to {basepath}.{{svg,pdf,png}}")
    return f0_data


# ═══════════════════════════════════════════════════════
# CLI / self-check
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate f0(T) figure")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="Cache pickle path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double", "presentation"])
    args = parser.parse_args()

    if not Path(args.cache).exists():
        print(f"Cache not found: {args.cache}")
        sys.exit(1)

    plot_f0_vs_temperature(args.cache, args.output, args.preset)
