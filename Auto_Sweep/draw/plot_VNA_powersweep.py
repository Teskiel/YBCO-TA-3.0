# -*- coding: utf-8 -*-
"""VNA 功率扫描 S21 可视化脚本。

固定目标温度 + 激光功率 (Pl)，画出所有 VNA 输出功率下的 S21 叠加曲线。

与 plot_laser_powersweep.py 对称：
    plot_laser_powersweep: 固定 (T, Pv)，变化 Pl
    plot_VNA_powersweep:  固定 (T, Pl)，变化 Pv

用法：
    python draw/plot_VNA_powersweep.py

输出：
    弹出 matplotlib 窗口显示 S21 叠加图。
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
# 配置 — 修改以下常量即可切换实验/温度/激光功率
# =========================================================================

# 实验数据根目录（时间戳级别或温度级别均可）
TARGET_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\20260609_185708-1\6K"

# 固定的激光功率 (mW)
TARGET_PL_MW = 9

# 输出 PNG 路径（None 表示仅显示不保存）
OUTPUT_PATH = None


# =========================================================================
# 路径解析函数
# =========================================================================

def extract_vna_power_from_path(path: str) -> Optional[int]:
    """从路径中提取 VNA 功率 (dBm)。

    Example:
        ".../20K/-25dBm/05mW/file.s2p" → -25
        ".../actual_6.452K/-35dBm/..." → -35

    Args:
        path: 文件或目录路径，含 -{N}dBm 段。

    Returns:
        VNA 功率整数值 (dBm)，未找到则返回 None。
    """
    m = re.search(r"(-\d+)dBm", path)
    return int(m.group(1)) if m else None


def extract_target_temp_from_path(path: str) -> Optional[float]:
    """从路径中提取目标温度 (K)。

    匹配模式：路径中以 /{整数}K/ 形式出现的目录名。
    跳过 actual_X.XXXK（那是实际温度，非目标温度）。

    Example:
        ".../20K/-25dBm/..." → 20.0
        ".../6K/actual_6.452K/..." → 6.0

    Args:
        path: 文件或目录路径。

    Returns:
        温度浮点值 (K)，未找到则返回 None。
    """
    # 匹配 /{整数}K/ 或 /{整数}K（路径末尾）（非 actual_ 开头）
    m = re.search(r"[\\/](\d+)K(?:[\\/]|$)", path)
    return float(m.group(1)) if m else None


def extract_laser_power_from_path(path: str) -> Optional[int]:
    """从路径中提取激光功率 (mW)。

    Example:
        ".../05mW/file.s2p" → 5
        ".../00mW/..." → 0

    Args:
        path: 文件或目录路径，含 {N}mW 目录名。

    Returns:
        激光功率整数值 (mW)，未找到则返回 None。
    """
    m = re.search(r"[\\/](\d+)mW[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None


# =========================================================================
# 文件发现
# =========================================================================

def find_s2p_files_for_laser_power(
    target_temp_dir: str,
    laser_power_mw: int,
) -> List[str]:
    """在目标温度目录下，查找所有匹配指定激光功率的 .s2p 文件。

    使用 recursive glob 遍历所有子目录，兼容新旧两种目录格式：
      - 新格式：{T}K / -{Pv}dBm / {Pl}mW / *.s2p
      - 旧格式：{T}K / actual_X.XXXK / -{Pv}dBm / {Pl}mW / *.s2p

    Args:
        target_temp_dir: 目标温度目录路径（如 ".../20K"）。
        laser_power_mw: 目标激光功率 (mW)，如 3 表示查找 03mW 目录。

    Returns:
        匹配的 .s2p 文件路径列表，按路径排序（等价于按 VNA 功率升序）。
    """
    if not os.path.isdir(target_temp_dir):
        return []

    # 递归查找所有 .s2p 文件
    pattern = os.path.join(target_temp_dir, "**", "*.s2p")
    all_s2p_files = sorted(glob.glob(pattern, recursive=True))

    # 筛选：路径中包含 /{Pl:02d}mW/ 或 /{Pl}mW/
    laser_dir_patterns = [
        f"{laser_power_mw:02d}mW",
        f"{laser_power_mw}mW",
    ]

    matching = []
    for file_path in all_s2p_files:
        normalized = file_path.replace("\\", "/")
        for pat in laser_dir_patterns:
            if f"/{pat}/" in normalized:
                matching.append(file_path)
                break

    # 按 VNA 功率升序排列（-55 dBm 最负在前，-25 dBm 在最后）
    return sorted(matching, key=lambda p: extract_vna_power_from_path(p) or -999)


# =========================================================================
# S2P 加载
# =========================================================================

def load_s2p(file_path: str) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """加载单个 .s2p 文件，提取 S21 数据。

    Args:
        file_path: .s2p 文件路径。

    Returns:
        (freq_ghz, s21_db) 元组，加载失败则返回 None。
    """
    try:
        ntwk = rf.Network(file_path)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        # 过滤 NaN/Inf
        mask = np.isfinite(s21_db)
        if not mask.any():
            return None
        return freq[mask], s21_db[mask]
    except Exception:
        return None


# =========================================================================
# 数据整理
# =========================================================================

def collect_traces(
    target_temp_dir: str,
    laser_power_mw: int,
) -> List[Dict]:
    """收集指定激光功率下所有 VNA 功率级别的 S21 trace。

    步骤：
    1. 发现所有匹配的 .s2p 文件
    2. 加载每个文件的 S21 数据
    3. 提取 VNA 功率
    4. 按 VNA 功率升序排列

    Args:
        target_temp_dir: 目标温度目录路径。
        laser_power_mw: 目标激光功率 (mW)。

    Returns:
        按 pv 升序排列的 trace 列表，每项：
        {"file_path": str, "pv": int, "freq": np.ndarray, "s21": np.ndarray}
    """
    files = find_s2p_files_for_laser_power(target_temp_dir, laser_power_mw)
    traces = []

    for file_path in files:
        pv = extract_vna_power_from_path(file_path)
        if pv is None:
            continue

        loaded = load_s2p(file_path)
        if loaded is None:
            continue

        freq, s21 = loaded
        traces.append({
            "file_path": file_path,
            "pv": pv,
            "freq": freq,
            "s21": s21,
        })

    traces.sort(key=lambda t: t["pv"])
    return traces


# =========================================================================
# 绘图
# =========================================================================

def plot_vna_power_sweep(
    traces: List[Dict],
    target_temp_k: float,
    laser_power_mw: int,
    output_path: Optional[str] = None,
) -> plt.Figure:
    """生成 VNA 功率扫描 S21 叠加图。

    每条曲线对应一个 VNA 输出功率，按 jet 色谱着色。
    图标题显示固定的目标温度和激光功率。

    Args:
        traces: collect_traces() 返回的 trace 列表。
        target_temp_k: 目标温度 (K)。
        laser_power_mw: 激光功率 (mW)。
        output_path: 可选 PNG 保存路径。

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
            label=f"{trace['pv']:+d} dBm" if n_traces <= 16 else None,
        )

    ax.set_xlabel("Frequency (GHz)", fontsize=13)
    ax.set_ylabel("|S21| (dB)", fontsize=13)
    ax.set_title(
        f"T_target = {target_temp_k:.0f} K    |    P_laser = {laser_power_mw} mW",
        fontsize=15,
        fontweight="bold",
    )
    ax.grid(True, alpha=0.4)

    # 图例：trace ≤ 16 时显示
    if 0 < n_traces <= 16:
        ax.legend(loc="best", fontsize=9, ncol=2)

    # 色标：按 VNA 功率着色
    if n_traces > 0:
        pv_min = min(t["pv"] for t in traces)
        pv_max = max(t["pv"] for t in traces)
        sm = plt.cm.ScalarMappable(
            norm=plt.Normalize(vmin=pv_min, vmax=pv_max),
            cmap=plt.cm.jet,
        )
        cbar = fig.colorbar(sm, ax=ax)
        cbar.set_label("VNA Power (dBm)", fontsize=11)

    fig.tight_layout()

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
        print(f"Saved: {output_path}")

    return fig


# =========================================================================
# 主入口
# =========================================================================

def main():
    """主入口：按配置常量发现文件、加载数据、绘图。"""
    target_dir = TARGET_DIR

    if not os.path.isdir(target_dir):
        print(f"[Error] 目录不存在: {target_dir}")
        return

    # 从路径中自动提取目标温度
    target_temp = extract_target_temp_from_path(target_dir)
    if target_temp is None:
        print(f"[Error] 无法从路径中提取目标温度: {target_dir}")
        print("        请确保路径中包含形如 /{N}K/ 的目标温度目录。")
        return

    print(f"目标温度: {target_temp:.0f} K")
    print(f"激光功率: {TARGET_PL_MW} mW")
    print(f"扫描目录: {target_dir}")
    print("=" * 60)

    traces = collect_traces(target_dir, TARGET_PL_MW)

    if not traces:
        print(f"[Warning] 未找到匹配激光功率 {TARGET_PL_MW} mW 的 .s2p 文件。")
        return

    pv_list = sorted(set(t["pv"] for t in traces))
    print(f"找到 {len(traces)} 条 trace，{len(pv_list)} 个 VNA 功率级别: {pv_list}")

    fig = plot_vna_power_sweep(
        traces,
        target_temp_k=target_temp,
        laser_power_mw=TARGET_PL_MW,
        output_path=OUTPUT_PATH,
    )

    plt.show()
    print("--- 绘图完成 ---")


if __name__ == "__main__":
    main()
