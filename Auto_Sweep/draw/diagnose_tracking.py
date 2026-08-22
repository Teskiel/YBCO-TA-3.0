# -*- coding: utf-8 -*-
"""
谐振子追踪诊断图 — S21 全谱 + 谐振位置标注。

对指定温度绘制 S21 透射谱并标注已识别的谐振子位置，
供用户肉眼判定识别是否正确，作为手动修正 f0 的依据。

用法:
    # 基本诊断 (使用默认缓存和数据目录)
    python draw/diagnose_tracking.py --temp 70

    # 指定 S2P 文件
    python draw/diagnose_tracking.py --temp 70 --pv -55 --pl 0

    # 指定缓存和数据目录
    python draw/diagnose_tracking.py --temp 70 \
        --cache "path/to/_cache_xxx.pkl" \
        --data-dir "path/to/merged_data"

输出:
    plot_output/diagnose_T{xx}K.{svg,pdf,png}
"""

import sys, pickle, os
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import skrf as rf

_draw_dir = Path(__file__).resolve().parent
if str(_draw_dir) not in sys.path:
    sys.path.insert(0, str(_draw_dir))
from _style_config import (apply_style, get_figsize, get_resonator_color,
                            save_figure, OUTPUT_FORMATS, TEMPERATURE_COLORS)
from _tracking_utils import SCRAPS_F0, SCRAPS_TEMPS, RESONATOR_NAMES

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


def find_s2p(data_dir: str, T: int, pv_dbm: int, pl_mw: int) -> str | None:
    """在合并数据目录中查找 S2P 文件。"""
    pattern = f"{T}K/{pv_dbm}dBm/{pl_mw:02d}mW/*.s2p"
    matches = sorted(Path(data_dir).glob(pattern))
    if not matches:
        # fallback: 尝试旧格式 (pl 不带前导零)
        pattern2 = f"{T}K/{pv_dbm}dBm/{pl_mw}mW/*.s2p"
        matches = sorted(Path(data_dir).glob(pattern2))
    return str(matches[0]) if matches else None


def get_scraps_f0(T_k: float, rname: str) -> float | None:
    """从 scraps 骨架插值获取参考 f0 值。"""
    if rname not in SCRAPS_F0:
        return None
    f0_vals = SCRAPS_F0[rname]
    temps = SCRAPS_TEMPS

    if T_k <= temps[0]:
        return float(f0_vals[0])
    if T_k >= temps[-1]:
        return float(f0_vals[-1])

    return float(np.interp(T_k, temps, f0_vals))


def plot_diagnose(cache_path: str, data_dir: str, T_k: int,
                   pv_dbm: int = -55, pl_mw: int = 0,
                   output_dir: str = DEFAULT_OUTPUT_DIR,
                   preset: str = "prb_double"):
    """生成指定温度的 S21 谐振子诊断图。"""
    # ── 加载缓存 ──
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)

    # ── 查找 S2P 文件 ──
    s2p_path = find_s2p(data_dir, T_k, pv_dbm, pl_mw)
    if s2p_path is None:
        print(f"[ERROR] 未找到 S2P 文件: T={T_k}K, Pv={pv_dbm}dBm, Pl={pl_mw}mW")
        print(f"  搜索目录: {data_dir}")
        return None

    # ── 加载 S2P ──
    ntwk = rf.Network(s2p_path)
    freq = ntwk.f / 1e9
    s21_db = 20 * np.log10(np.abs(ntwk.s[:, 1, 0]))

    # ── 样式 ──
    cfg = apply_style(preset)
    fig_width = cfg["width_inches"]
    fig_height = fig_width * 0.45  # 宽幅横条
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    # ── 绘制 S21  ──
    ax.plot(freq, s21_db, color="#2166AC", linewidth=1.0,
             rasterized=True, zorder=2, label="|S21|")

    # P90 基线 (水平参考线)
    p90 = np.percentile(s21_db, 90)
    ax.axhline(y=p90, color="#BBBBBB", linewidth=0.6, linestyle="--",
                alpha=0.5, zorder=0, label=f"P90 baseline ({p90:.1f} dB)")

    # ── 标注谐振子位置 ──
    # 优先使用 collected 阶段的 identified (实际追踪使用的 f0)
    collected = cache["collected"].get(T_k)
    identification = cache["identification"].get(T_k)

    if collected is None or collected.get("identified") is None:
        print(f"[WARN] T={T_k}K: collected 数据为空, 无法标注谐振子")
    else:
        identified = collected["identified"]
        # 获取 identification 阶段的值用于对比
        id_phase = {}
        if identification is not None:
            for r in identification.get("resonators", []):
                if r["f0_ghz"] is not None:
                    id_phase[r["name"]] = r["f0_ghz"]

        # 确定 y 轴标注高度范围
        y_min, y_max = s21_db.min() - 2, s21_db.max() + 4
        ax.set_ylim(y_min, y_max)

        anno_y = y_max - 1.0  # 标签起始高度
        anno_step = (y_max - y_min) * 0.06  # 标签纵向间距

        for i, rname in enumerate(RESONATOR_NAMES):
            if rname not in identified:
                continue

            f0_collected = identified[rname]["f0_ghz"]
            dip_db = identified[rname].get("dip_depth_db", None)
            color = get_resonator_color(rname)

            # 主标记: collected 阶段 f0 (实色竖线)
            ax.axvline(x=f0_collected, color=color, linestyle="--",
                        linewidth=1.2, alpha=0.7, zorder=3)

            # 标签: 谐振子名 + f0 + dip
            label_lines = [rname]
            label_lines.append(f"f0={f0_collected:.4f} GHz")
            if dip_db is not None:
                label_lines.append(f"dip={dip_db:.1f} dB")

            # scraps 参考值
            scraps_f0 = get_scraps_f0(T_k, rname)
            if scraps_f0 is not None:
                dev_mhz = (f0_collected - scraps_f0) * 1e3
                label_lines.append(f"scraps={scraps_f0:.4f} (dev={dev_mhz:+.0f}MHz)")

            label_text = "\n".join(label_lines)

            # 偏移方向: 交替上下避免重叠
            y_pos = anno_y - (i % 3) * anno_step * 2.5
            ax.annotate(label_text,
                        xy=(f0_collected, s21_db[np.argmin(np.abs(freq - f0_collected))]),
                        xytext=(f0_collected + 0.12, y_pos),
                        fontsize=5.5, color=color, fontweight="bold",
                        arrowprops=dict(arrowstyle="->", color=color,
                                       lw=0.6, alpha=0.6),
                        zorder=5)

            # 如有 identification 阶段差异, 灰色标记
            if rname in id_phase:
                f0_id = id_phase[rname]
                if abs(f0_id - f0_collected) > 2e-3:  # 差异 > 2 MHz
                    ax.axvline(x=f0_id, color="#999999", linestyle=":",
                                linewidth=0.8, alpha=0.45, zorder=1)
                    ax.annotate(f"ID phase: {f0_id:.4f}",
                                xy=(f0_id, y_min + 0.5),
                                fontsize=4.5, color="#999999",
                                ha="center", zorder=1)

    # ── 频率轴范围 ──
    # 自动适配: 覆盖所有谐振子频率 ± buffer
    f0_vals = []
    if collected and collected.get("identified"):
        for rname, ident in collected["identified"].items():
            f0_vals.append(ident["f0_ghz"])
    if f0_vals:
        f_min = max(min(f0_vals) - 0.3, freq.min())
        f_max = min(max(f0_vals) + 0.3, freq.max())
        ax.set_xlim(f_min, f_max)

    # ── 标签和标题 ──
    dataset = cache["metadata"]["dataset_name"]
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|S21| (dB)")
    ax.set_title(f"Resonator Identification Diagnosis — T={T_k} K, "
                 f"$P_{{\\rm read}}$ = {pv_dbm} dBm, $P_{{\\rm laser}}$ = {pl_mw} mW\n"
                 f"{dataset}  |  source: {Path(s2p_path).name}",
                 fontsize=8, fontweight="bold")
    ax.grid(True, alpha=0.22)
    ax.legend(loc="lower left", fontsize=5.5, framealpha=0.7,
               handlelength=1.2, ncol=1)

    plt.tight_layout()

    # ── 保存 ──
    basepath = Path(output_dir) / f"diagnose_T{T_k}K"
    basepath.parent.mkdir(parents=True, exist_ok=True)
    save_figure(fig, str(basepath), OUTPUT_FORMATS)
    plt.close(fig)

    # ── 控制台报告 ──
    print(f"\n===== Diagnosis T={T_k}K =====")
    print(f"S2P: {s2p_path}")
    print(f"Pv={pv_dbm} dBm, Pl={pl_mw} mW")
    print(f"S21 range: {s21_db.min():.1f} to {s21_db.max():.1f} dB, P90={p90:.1f} dB")
    print()
    if collected and collected.get("identified"):
        print(f"{'Resonator':>10s}  {'f0_collected':>12s}  {'dip':>6s}  "
              f"{'scraps_f0':>10s}  {'dev(MHz)':>9s}  {'f0_IDphase':>12s}  {'note'}")
        print("-" * 85)
        for rname in RESONATOR_NAMES:
            if rname not in collected["identified"]:
                print(f"{rname:>10s}  {'---':>12s}")
                continue
            ident = collected["identified"][rname]
            f0 = ident["f0_ghz"]
            dip = ident.get("dip_depth_db", float("nan"))
            scraps_f0 = get_scraps_f0(T_k, rname)
            dev = (f0 - scraps_f0) * 1e3 if scraps_f0 else float("nan")
            f0_id = id_phase.get(rname, float("nan"))
            dev_id = (f0 - f0_id) * 1e3 if not np.isnan(f0_id) else float("nan")

            # 判断 note
            note = ""
            if not np.isnan(dev_id) and abs(dev_id) > 2:
                note = "ID phase mismatch!"
            if dip is not None and not np.isnan(dip) and dip > -1.5:
                note += " SHALLOW"

            scraps_str = f"{scraps_f0:.4f}" if scraps_f0 else "N/A"
            dev_str = f"{dev:+.0f}" if not np.isnan(dev) else "N/A"
            f0_id_str = f"{f0_id:.4f}" if not np.isnan(f0_id) else "N/A"

            print(f"{rname:>10s}  {f0:12.4f}  {dip:5.1f}dB  "
                  f"{scraps_str:>10s}  {dev_str:>9s}  {f0_id_str:>12s}  {note}")
    else:
        print("  (无 collected 数据)")

    print(f"\n输出:")
    for fmt in OUTPUT_FORMATS:
        print(f"  {basepath}.{fmt}")

    return str(basepath)


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="谐振子追踪诊断图 — S21 全谱 + 谐振位置标注")
    parser.add_argument("--temp", type=int, required=True,
                        help="目标温度 (K)")
    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help="Cache pickle 路径")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                        help="合并数据目录 (含 {T}K/ 子文件夹)")
    parser.add_argument("--pv", type=int, default=-55,
                        help="用于绘图的 VNA 功率 (dBm), 默认 -55")
    parser.add_argument("--pl", type=int, default=0,
                        help="激光功率 (mW), 默认 0 (暗态)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                        help="输出目录")
    parser.add_argument("--preset", default="prb_double",
                        choices=["quick_check", "prb_single", "prb_double",
                                 "presentation"])
    args = parser.parse_args()

    if not Path(args.cache).exists():
        print(f"缓存不存在: {args.cache}")
        sys.exit(1)
    if not Path(args.data_dir).is_dir():
        print(f"数据目录不存在: {args.data_dir}")
        sys.exit(1)

    plot_diagnose(args.cache, args.data_dir, args.temp,
                   args.pv, args.pl, args.output, args.preset)
