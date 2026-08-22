# -*- coding: utf-8 -*-
"""
Figure: Responsivity vs Temperature — 光学响应率温度演化

物理意义:
  响应率 R = df₀/dP_laser (Hz/mW)，即谐振频率对激光功率的线性斜率。
  随温度升高，超导能隙减小，准粒子弛豫时间缩短，
  响应率下降。在 Tc (~80K) 附近响应率急剧减小或变号。

数据来源: 缓存 pickle (delta_f_over_f)，线性拟合得到斜率后转 Hz/mW
条件: Pl = 0~9 mW (6 级扫描)，自动检测所有 VNA 功率

用法:
  python responsivity_vs_temperature.py
  python responsivity_vs_temperature.py --cache "path/to/cache.pkl"
  python responsivity_vs_temperature.py --preset prb_double
"""

import sys
import os
import pickle
import argparse
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

# ── draw 模块 ───────────────────────────────────────────
_DRAW_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _DRAW_DIR not in sys.path:
    sys.path.insert(0, _DRAW_DIR)
from _style_config import (apply_style, get_resonator_color,
                           save_figure, RESONATOR_COLORS)
from _data_cache import load_cache, find_cache


# ═══════════════════════════════════════════════════════════
# 响应率计算
# ═══════════════════════════════════════════════════════════

def compute_responsivity_Hz_per_mW(dff_frac, flags, pl_mw, f0_ghz):
    """对一组 (Pl, δf/f) 线性拟合，返回响应率 (Hz/mW)。

    Args:
        dff_frac: δf/f 分数数组 (n_pl,)
        flags: 追踪状态数组 (n_pl,)
        pl_mw: 激光功率数组 (n_pl,)
        f0_ghz: 参考谐振频率 (GHz)

    Returns:
        resp_Hz_per_mW: float or None
        n_valid: 有效数据点数
    """
    valid = (flags == "tracked") & ~np.isnan(dff_frac)
    n_valid = int(valid.sum())
    if n_valid < 3:
        return None, n_valid

    x = np.array(pl_mw)[valid]
    y = np.array(dff_frac)[valid]

    try:
        slope_frac_per_mW = np.polyfit(x, y, 1)[0]
    except Exception:
        return None, n_valid

    f0_Hz = f0_ghz * 1e9
    resp_Hz_per_mW = slope_frac_per_mW * f0_Hz
    return resp_Hz_per_mW, n_valid


# ═══════════════════════════════════════════════════════════
# 从缓存提取数据
# ═══════════════════════════════════════════════════════════

def extract_responsivity_data(cache):
    """从缓存提取全部响应率数据。

    Returns:
        results: {pv_dbm: {"temps": [T, ...],
                           "resonators": {"R1": [resp_or_None, ...], ...}}}
    """
    pl_mw = cache["metadata"]["laser_powers_mw"]
    temps = sorted(cache["metadata"]["temperatures_k"])
    r_names = cache["metadata"]["resonator_names"]

    # 收集全部 VNA 功率
    all_pv = set()
    for T in temps:
        if T not in cache["collected"]:
            continue
        all_pv.update(cache["collected"][T].get("vna_powers_dbm", []))
    all_pv = sorted(all_pv, key=lambda x: int(x), reverse=True)

    # 建立 VNA 功率到索引的映射 (每个温度处的 VNA 功率列表可能不同)
    # 使用 collected 中实际记录的 vna_powers_dbm

    results = {}
    for pv in all_pv:
        results[pv] = {"temps": [], "resonators": {r: [] for r in r_names}}

    print(f"\n温度: {len(temps)} 个  |  VNA 功率: {all_pv}")
    print(f"激光功率: {pl_mw} mW")
    print(f"谐振器: {r_names}\n")

    stats_ok = 0
    stats_fail = 0
    stats_skip = 0

    for T in temps:
        col = cache["collected"].get(T)
        if col is None:
            continue

        pv_list = col.get("vna_powers_dbm", [])
        if not pv_list:
            continue

        # 建立 pv → index 映射
        pv_to_idx = {pv: i for i, pv in enumerate(pv_list)}

        for pv in all_pv:
            if pv not in pv_to_idx:
                # 该温度没有此 VNA 功率
                for r in r_names:
                    results[pv]["resonators"][r].append(None)
                stats_skip += 1
                continue

            i_pv = pv_to_idx[pv]
            results[pv]["temps"].append(T)

            data = col.get("data", {})
            identified = col.get("identified", {})

            for r_name in r_names:
                r_data = data.get(r_name)
                r_ident = identified.get(r_name)

                if r_data is None or r_ident is None:
                    results[pv]["resonators"][r_name].append(None)
                    stats_skip += 1
                    continue

                dff = r_data["delta_f_over_f"]
                flags = r_data["flags"]

                # f0: 优先用 f0_refs (per-Pv)，回退到 identified
                f0_refs = r_data.get("f0_refs", {})
                f0_ghz = f0_refs.get(pv, r_ident.get("f0_ghz"))

                if f0_ghz is None:
                    results[pv]["resonators"][r_name].append(None)
                    stats_skip += 1
                    continue

                resp, n = compute_responsivity_Hz_per_mW(
                    dff[i_pv, :], flags[i_pv, :], pl_mw, f0_ghz
                )
                results[pv]["resonators"][r_name].append(resp)

                if resp is not None:
                    stats_ok += 1
                else:
                    stats_fail += 1

    print(f"统计: OK={stats_ok}, FAIL={stats_fail}, SKIP={stats_skip}")
    return results, all_pv, r_names


# ═══════════════════════════════════════════════════════════
# 绘图
# ═══════════════════════════════════════════════════════════

def plot_responsivity_vs_temperature(results, all_pv, r_names, output_dir,
                                      dataset_label="", preset="quick_check"):
    """生成 Responsivity(T) 出版图。

    N×1 垂直子图 (N = VNA 功率数)，纯散点无连线。
    """
    apply_style(preset)

    active_pv = [pv for pv in all_pv if pv in results and results[pv]["temps"]]
    n_pv = len(active_pv)
    if n_pv == 0:
        print("[ERROR] 无有效数据可绘图")
        return None

    fig, axes = plt.subplots(n_pv, 1, figsize=(10, 3.6 * n_pv),
                             sharex=True, squeeze=False)
    axes = axes.flatten()

    for ax_idx, pv in enumerate(active_pv):
        ax = axes[ax_idx]
        data = results[pv]
        temps = np.array(data["temps"])

        for r_name in r_names:
            resp_list = data["resonators"].get(r_name, [])

            if len(resp_list) != len(temps):
                continue

            valid = []
            for t, r in zip(temps, resp_list):
                if r is None or np.isnan(r):
                    continue
                valid.append((t, abs(r)))  # |R| 取绝对值

            if len(valid) == 0:
                continue

            tv = np.array([v[0] for v in valid])
            rv = np.array([v[1] for v in valid])

            color = get_resonator_color(r_name)

            # 纯散点 (无连线)
            ax.scatter(tv, rv, s=28, color=color, alpha=0.55,
                      edgecolors="none", zorder=3, label=r_name)

        ax.set_ylabel("|Responsivity| (Hz/mW)", fontsize=11)
        ax.set_yscale("log")
        ax.set_title(f"$P_v$ = {pv} dBm",
                    fontweight="bold", fontsize=13)
        ax.legend(loc="upper right", framealpha=0.85, fontsize=9,
                 ncol=1, edgecolor="#999999")
        ax.grid(True, alpha=0.3)

        # 正常态底纹
        ax.axvspan(77, 93, alpha=0.06, color="red", zorder=0)

    axes[-1].set_xlabel("Temperature (K)", fontsize=11)

    title = "|Responsivity| vs Temperature — $P_l$ = 0–9 mW"
    if dataset_label:
        title += f"\n{dataset_label}"
    fig.suptitle(title, fontweight="bold", fontsize=14)

    fig.tight_layout(rect=[0, 0, 1, 0.97])

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    basepath = out_dir / "responsivity_vs_temperature"
    save_figure(fig, str(basepath), ["png", "svg", "pdf"])
    plt.close(fig)

    print(f"\n图片已保存:")
    for ext in ["png", "svg", "pdf"]:
        print(f"  {basepath}.{ext}")
    return fig


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

DEFAULT_CACHE = (
    "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
    "_cache_20260605-0606__6-90K__774pts.pkl"
)
DEFAULT_OUTPUT = (
    "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/"
)


def main():
    parser = argparse.ArgumentParser(
        description="Responsivity vs Temperature — 光学响应率温度演化"
    )
    parser.add_argument("--cache", default=DEFAULT_CACHE,
                       help="缓存 pickle 路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                       help="输出目录")
    parser.add_argument("--preset", default="quick_check",
                       choices=["quick_check", "prb_single", "prb_double",
                                "presentation"],
                       help="绘图样式预设")
    args = parser.parse_args()

    # 查找缓存
    cache_path = args.cache
    if not Path(cache_path).exists():
        # 尝试自动查找
        print(f"缓存文件未找到: {cache_path}")
        print("尝试自动查找...")
        cache_path = find_cache(
            "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/"
            "20260605-0606__6-90K__774pts"
        )
        if cache_path and Path(cache_path).exists():
            print(f"找到缓存: {cache_path}")
        else:
            print("[ERROR] 未找到缓存文件。请先运行 _data_cache.py 生成缓存。")
            sys.exit(1)

    print(f"加载缓存: {cache_path}")
    cache = load_cache(cache_path)
    dataset_label = cache["metadata"].get("dataset_name", "")

    # ── 提取数据 ──
    results, all_pv, r_names = extract_responsivity_data(cache)

    if not results:
        print("[ERROR] 提取响应率数据失败")
        sys.exit(1)

    # ── 绘图 ──
    plot_responsivity_vs_temperature(
        results, all_pv, r_names, args.output,
        dataset_label=dataset_label, preset=args.preset
    )

    print("\nDone!")


if __name__ == "__main__":
    main()
