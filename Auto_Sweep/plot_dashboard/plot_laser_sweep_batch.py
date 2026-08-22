# -*- coding: utf-8 -*-
"""批量 S21 激光功率扫描图 — 基于 plot_laser_powersweep.py 的批量化扩展。

功能：给定一个 actual_X.XXXK 目录路径，自动查找其下所有 -xxdBm 子目录，
      每个子目录生成一张图，包含该 (Tr, Pv) 条件下全部激光功率的 S21 曲线。

用法：
    python plot_laser_sweep_batch.py

输出：
    在脚本所在目录下的 pic/ 文件夹中生成 Figure_1.png, Figure_2.png, ...

设计原则：
    尽可能少地修改原 plot_laser_powersweep.py 的核心逻辑，
    在其基础上增加路径查找、批量循环和结构化保存。
"""

import os
import re
import glob
from typing import List, Optional, Tuple, Dict

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")  # 兼容 Spyder / 独立运行
import matplotlib.pyplot as plt
import skrf as rf

# =========================================================================
# 配置 — 修改此路径即可批量出图
# =========================================================================

TARGET_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\20260606_092046\58K"

def find_actual_temp_dirs(base_dir: str) -> List[str]:
    """在 base_dir 下查找所有 actual_X.XXXK 子目录。

    如果 base_dir 本身路径包含 'actual_'，直接返回自己。
    否则扫描一级子目录，返回匹配 actual_*K 的目录路径。
    """
    if "actual_" in os.path.basename(base_dir):
        return [base_dir]

    results = []
    if not os.path.isdir(base_dir):
        return results

    for name in os.listdir(base_dir):
        if re.match(r"actual_[\d.]+K", name):
            full = os.path.join(base_dir, name)
            if os.path.isdir(full):
                results.append(full)

    results.sort()
    return results


# =========================================================================
# 路径解析函数
# =========================================================================


def extract_tr_from_path(path: str) -> Optional[float]:
    """从路径中提取实际温度 Tr。

    Example:
        ".../actual_6.611K" → 6.611
    """
    m = re.search(r"actual_([\d.]+)K", path)
    return float(m.group(1)) if m else None


def find_dbm_folders(actual_temp_dir: str) -> List[Tuple[str, int]]:
    """查找 actual_X.XXXK 目录下所有 -xxdBm 子目录。

    Args:
        actual_temp_dir: 形如 ".../actual_6.611K" 的目录路径。

    Returns:
        List of (full_path, dbm_value) 元组，按 dBm 升序排列。
    """
    results = []
    if not os.path.isdir(actual_temp_dir):
        return results

    for name in os.listdir(actual_temp_dir):
        m = re.match(r"^(-?\d+)dBm$", name)
        if not m:
            continue
        full = os.path.join(actual_temp_dir, name)
        if os.path.isdir(full):
            results.append((full, int(m.group(1))))
    results.sort(key=lambda x: x[1])
    return results


def extract_pl_from_path(file_path: str) -> Optional[int]:
    """从 .s2p 路径提取激光功率 Pl。

    Example:
        ".../-25dBm/05mW/YBCO.s2p" → 5
    """
    m = re.search(r"[\\/](\d+)mW[\\/]", file_path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None


# =========================================================================
# S2P 加载
# =========================================================================


def load_s2p(file_path: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """加载单个 .s2p 文件，返回 (freq_ghz, s21_db)。

    Returns:
        (freq_ghz, s21_db) 或 None（加载失败时）。
    """
    try:
        ntwk = rf.Network(file_path)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        return freq, s21_db
    except Exception:
        return None


def collect_s2p_files(dbm_dir: str) -> List[Dict]:
    """收集某个 -xxdBm 目录下全部 .s2p 文件并加载数据。

    Args:
        dbm_dir: 形如 ".../-25dBm" 的目录路径。

    Returns:
        按 Pl 升序排列的 trace 列表，每个元素为
        {"file_path": str, "pl": int, "freq": np.ndarray, "s21": np.ndarray}
    """
    traces = []
    s2p_files = sorted(glob.glob(os.path.join(dbm_dir, "**", "*.s2p"), recursive=True))

    for file_path in s2p_files:
        pl = extract_pl_from_path(file_path)
        if pl is None:
            continue
        loaded = load_s2p(file_path)
        if loaded is None:
            continue
        freq, s21 = loaded
        traces.append({
            "file_path": file_path,
            "pl": pl,
            "freq": freq,
            "s21": s21,
        })

    traces.sort(key=lambda t: t["pl"])
    return traces


# =========================================================================
# 绘图
# =========================================================================


def plot_single_figure(
    traces: List[Dict],
    tr: float,
    pv: int,
    output_dir: Optional[str] = None,
) -> plt.Figure:
    """为单个 (Tr, Pv) 组合生成一张 S21 叠加图。

    Args:
        traces: collect_s2p_files() 返回的 trace 列表。
        tr: 实际温度 (K)。
        pv: VNA 功率 (dBm)。
        output_dir: 若不为 None，保存 PNG 到此目录。

    Returns:
        matplotlib Figure 对象。
    """
    fig, ax = plt.subplots(figsize=(12, 8), dpi=200)

    n_traces = len(traces)
    for i, trace in enumerate(traces):
        color = plt.cm.jet(i / max(n_traces, 1))
        ax.plot(
            trace["freq"],
            trace["s21"],
            color=color,
            linewidth=1.5,
            alpha=0.85,
            antialiased=True,
            label=f"{trace['pl']} mW" if n_traces <= 12 else None,
        )

    ax.set_xlabel("Frequency (GHz)", fontsize=13)
    ax.set_ylabel("|S21| (dB)", fontsize=13)
    ax.set_title(f"Tr = {tr:.3f} K    |    Pv = {pv:+d} dBm", fontsize=15, fontweight="bold")
    ax.grid(True, alpha=0.4)

    if n_traces <= 12:
        ax.legend(loc="best", fontsize=9, ncol=2)

    # 色标
    if n_traces > 0:
        sm = plt.cm.ScalarMappable(
            norm=plt.Normalize(vmin=traces[0]["pl"], vmax=traces[-1]["pl"]),
            cmap=plt.cm.jet,
        )
        cbar = fig.colorbar(sm, ax=ax)
        cbar.set_label("Laser Power (mW)", fontsize=11)

    fig.tight_layout()

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        idx = len(os.listdir(output_dir)) + 1
        save_path = os.path.join(output_dir, f"Figure_{idx}.png")
        fig.savefig(save_path, dpi=200, bbox_inches="tight")
        print(f"  Saved: {save_path}")

    return fig


# =========================================================================
# 主流程
# =========================================================================


def main():
    """批量画图主入口。

    TARGET_DIR 可以是以下任意层级：
      - experiment_data/{timestamp}/                         → 扫描所有 {T}K/actual_*K
      - experiment_data/{timestamp}/{T}K/                    → 扫描 actual_*K
      - experiment_data/{timestamp}/{T}K/actual_X.XXXK/      → 直接使用
    """
    target_dir = TARGET_DIR

    if not os.path.isdir(target_dir):
        print(f"[Error] Directory not found: {target_dir}")
        return

    # ---- 查找所有 actual_X.XXXK 目录 ----
    actual_dirs = find_actual_temp_dirs(target_dir)
    if not actual_dirs:
        print(f"[Error] No 'actual_X.XXXK' directory found under: {target_dir}")
        print("        TARGET_DIR should point to an 'actual_X.XXXK' folder,")
        print("        or to a parent directory that contains one.")
        return

    print(f"Found {len(actual_dirs)} actual temperature folder(s):")
    for d in actual_dirs:
        print(f"  {os.path.basename(d)}")
    print("=" * 60)

    # 输出根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_root = os.path.join(script_dir, "pic")
    print(f"Output root: {output_root}")
    print("=" * 60)

    # ---- 对每个 actual 目录批量出图 ----
    for actual_dir in actual_dirs:
        tr = extract_tr_from_path(actual_dir)
        if tr is None:
            print(f"\n[Skip] Cannot extract Tr from: {actual_dir}")
            continue

        dbm_folders = find_dbm_folders(actual_dir)
        if not dbm_folders:
            print(f"\n[Skip] No -xxdBm folders under: {actual_dir}")
            continue

        print(f"\nTr = {tr:.3f} K  |  {len(dbm_folders)} Pv folder(s): "
              f"{[f'{v:+d}' for _, v in dbm_folders]}")

        output_dir = os.path.join(output_root, f"Tr_{tr:.3f}K")

        for dbm_path, pv in dbm_folders:
            print(f"  Pv = {pv:+d} dBm ...", end=" ")
            traces = collect_s2p_files(dbm_path)
            print(f"{len(traces)} traces: {[t['pl'] for t in traces]} mW")

            if not traces:
                print("    [Skip] No valid .s2p files.")
                continue

            plot_single_figure(traces, tr=tr, pv=pv, output_dir=output_dir)

    print(f"\nDone. Figures saved to {output_root}")


if __name__ == "__main__":
    main()
