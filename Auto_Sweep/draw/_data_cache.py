# -*- coding: utf-8 -*-
"""统一数据收集 + 缓存 + 验证模块。

将 S2P 原始数据预处理为结构化缓存，后续所有画图脚本直接从缓存读取，
避免重复扫描文件、识别谐振子、追踪频率。

流程:
  1. 扫描数据目录 → 发现所有 (T, Pv, Pl) 组合
  2. 每个温度下识别 5 个谐振子 (identify_resonators)
  3. 生成验证图 → 用户确认谐振位置正确
  4. 跨 (Pv, Pl) 追踪每个谐振子的 f0 → δf/f₀
  5. 保存为 .pkl 缓存

用法:
    # 收集 + 缓存 + 验证
    python draw/_data_cache.py --data-dir "D:/.../T6-77K_VNA-55~-25dBm_step2dB"

    # 强制刷新 (忽略已有缓存)
    python draw/_data_cache.py --data-dir "..." --force

    # 仅验证 (使用已有缓存)
    python draw/_data_cache.py --data-dir "..." --verify-only

编程用法:
    from draw._data_cache import collect_and_cache, load_cache

    cache = collect_and_cache(data_dir, output_base)
    # 或
    cache = load_cache("path/to/_cache_xxx.pkl")
    # cache["temperatures_k"], cache["resonators"][T]["data"][R] ...
"""

import os
import sys
import re
import json
import pickle
import argparse
import time
from datetime import datetime
from typing import Optional, Dict, List, Tuple

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
import skrf as rf

sys.path.insert(0, os.path.dirname(__file__))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    identify_resonators,
    track_across_pl,
    track_one_resonator,
    RESONATOR_NAMES,
    SCRAPS_F0,
    SCRAPS_TEMPS,
    zoom_mhz,
    detect_dip_p90,
)
from _f0_overrides import load_overrides, patch_identification

# =========================================================================
# 工具函数
# =========================================================================

def extract_vna_power_from_path(path: str) -> Optional[int]:
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None


def load_s2p_complex(file_path: str):
    """加载 S2P 文件，返回 (freq_ghz, s21_complex, s21_db)。"""
    try:
        ntwk = rf.Network(file_path)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        mask = np.isfinite(s21_db)
        if not mask.any():
            return None
        return freq[mask], s21[mask], s21_db[mask]
    except Exception:
        return None


def find_s2p_for_pv_pl(temp_dir: str, target_pv: int, target_pl: int):
    candidates = find_s2p_files_for_laser_power(temp_dir, target_pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == target_pv:
            return fp
    return None


def discover_parameters(temp_dir: str, laser_powers_mw: List[int]):
    """发现给定温度目录下全部可用的 VNA 功率。

    Returns:
        sorted list of VNA power (dBm)
    """
    all_pv = set()
    for pl in laser_powers_mw:
        for fp in find_s2p_files_for_laser_power(temp_dir, pl):
            pv = extract_vna_power_from_path(fp)
            if pv is not None:
                all_pv.add(pv)
    return sorted(all_pv)


def discover_temperatures(data_dir: str) -> List[int]:
    """发现数据目录下全部温度文件夹。

    Scans for subdirectories matching {N}K pattern, returns sorted list.
    """
    temps = []
    if not os.path.isdir(data_dir):
        return temps
    for name in os.listdir(data_dir):
        m = re.match(r"^(\d+)K$", name)
        if m and os.path.isdir(os.path.join(data_dir, name)):
            temps.append(int(m.group(1)))
    return sorted(temps)


# =========================================================================
# 谐振子识别 (单温度)
# =========================================================================

def identify_at_temperature(temp_dir: str, temp_k: int):
    """在一个温度下识别全部 5 个谐振子。

    参照 plot_verification.py 的参考文件选择策略:
      - 低温 (T < 70): 用 Pl=0, 最低 VNA 功率 (-55 dBm) 作为参考
      - 高温 (T >= 70): 用 Pl=0, 最高 VNA 功率 (-25 dBm) 作为参考

    Returns:
        {
            "vna_powers_dbm": [...],   # 该温度下全部可用 VNA 功率
            "reference_file": str,     # 参考 S2P 文件路径
            "reference_pv_dbm": int,   # 参考 VNA 功率
            "reference_pl_mw": 0,      # 参考激光功率
            "resonators": [            # identify_resonators 结果
                {"name": "R1", "f0_ghz": 3.8451, "dip_depth_db": -15.5},
                ...
            ],
        }
    """
    laser_powers_mw = [0, 1, 3, 5, 7, 9]

    # 选择参考文件
    if temp_k >= 70:
        ref_fp = None
        for pv_try in [-25, -27, -29, -31, -35, -45, -55]:
            ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, 0)
            if ref_fp is not None:
                break
    else:
        ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)

    if ref_fp is None:
        # 兜底: 任意 Pl=0 + 最低可用 VNA 功率
        all_pv = discover_parameters(temp_dir, laser_powers_mw)
        if all_pv:
            ref_fp = find_s2p_for_pv_pl(temp_dir, min(all_pv), 0)
        if ref_fp is None:
            return None

    ref_pv = extract_vna_power_from_path(ref_fp)
    loaded = load_s2p_complex(ref_fp)
    if loaded is None:
        return None

    freq_ghz, s21_cplx, s21_db = loaded
    resonators = identify_resonators(freq_ghz * 1e9, s21_db, s21_cplx, temp_k)

    vna_powers = discover_parameters(temp_dir, laser_powers_mw)

    return {
        "vna_powers_dbm": vna_powers,
        "reference_file": ref_fp,
        "reference_pv_dbm": ref_pv,
        "reference_pl_mw": 0,
        "resonators": [
            {
                "name": r["name"],
                "f0_ghz": r["f0_ghz"],
                "dip_depth_db": r["dip_depth"],
            }
            for r in resonators
        ],
    }


# =========================================================================
# δf/f₀ 数据收集 (跨 Pv × Pl)
# =========================================================================

def collect_dff_for_resonator(
    temp_dir: str,
    temp_k: int,
    resonator_name: str,
    f0_identified_ghz: float,
    vna_powers_dbm: List[int],
    laser_powers_mw: List[int],
):
    """为一个谐振子收集全部 (Pv, Pl) 组合的 δf/f₀。

    算法:
      1. Pl=0 定位参考 f0 — 用权威 f0 做强先验锚点
      2. 跨 Pl 追踪 (track_across_pl)

    Returns:
        {
            "delta_f_over_f": np.array (n_pv, n_pl),  # NaN = 未追踪到
            "flags": np.array (n_pv, n_pl, dtype=object),  # "tracked"|"shallow"|"lost"
            "f0_refs": {pv: f0_ghz, ...},  # 每个 Pv 的 Pl=0 参考频率
        }
    """
    n_pv = len(vna_powers_dbm)
    n_pl = len(laser_powers_mw)
    delta_f_over_f = np.full((n_pv, n_pl), np.nan)
    flags = np.full((n_pv, n_pl), "lost", dtype=object)
    f0_refs = {}
    pl0_idx = laser_powers_mw.index(0)

    # Step 1: Pl=0 定位参考 f0
    f0_at_pl0 = {}
    for i_pv, pv in enumerate(vna_powers_dbm):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None:
            continue
        loaded = load_s2p_complex(fp)
        if loaded is None:
            continue
        freq, s21_cplx, s21_db = loaded

        # 优先: 在权威 f0 附近搜索 dip
        if f0_identified_ghz is not None:
            f_dip, dip_depth, baseline = detect_dip_p90(
                freq, s21_db, f0_identified_ghz, search_mhz=30.0
            )
            if f_dip is not None and dip_depth < -0.5:
                f0_at_pl0[pv] = f_dip
                f0_refs[pv] = f_dip
                delta_f_over_f[i_pv, pl0_idx] = 0.0
                flags[i_pv, pl0_idx] = "tracked"
                continue

        # 备选: FD 预测追踪
        f0_history_local = []
        for prev_pv in vna_powers_dbm[:i_pv]:
            if prev_pv in f0_at_pl0 and f0_at_pl0[prev_pv] is not None:
                f0_history_local.append((temp_k, f0_at_pl0[prev_pv]))
        skeleton_f0 = SCRAPS_F0[resonator_name]
        base_history = [
            (t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0) if t <= temp_k + 15
        ]
        full_history = base_history + f0_history_local[-3:]

        f0, dip = track_one_resonator(
            freq * 1e9, s21_db, s21_cplx, resonator_name, temp_k, full_history
        )
        if f0 is not None:
            if f0_identified_ghz is not None and abs(f0 - f0_identified_ghz) > 0.050:
                continue
            f0_at_pl0[pv] = f0
            f0_refs[pv] = f0
            delta_f_over_f[i_pv, pl0_idx] = 0.0
            flags[i_pv, pl0_idx] = "tracked"

    # Step 2: 跨 Pl 追踪
    for i_pv, pv in enumerate(vna_powers_dbm):
        if pv not in f0_at_pl0 or f0_at_pl0[pv] is None:
            continue
        f0_ref = f0_at_pl0[pv]

        s2p_by_pl = {}
        for pl in laser_powers_mw:
            if pl == 0:
                continue
            fp = find_s2p_for_pv_pl(temp_dir, pv, pl)
            if fp is None:
                continue
            loaded = load_s2p_complex(fp)
            if loaded is None:
                continue
            s2p_by_pl[pl] = (loaded[0] * 1e9, loaded[2], loaded[1])

        if not s2p_by_pl:
            continue
        result = track_across_pl(s2p_by_pl, resonator_name, temp_k, f0_ref)

        for i_pl, pl in enumerate(laser_powers_mw):
            if pl == 0:
                continue
            if pl in result["pl_list"]:
                idx = result["pl_list"].index(pl)
                f0_val = result["f0_ghz"][idx]
                flag = result["flags"][idx]
                flags[i_pv, i_pl] = flag
                if not np.isnan(f0_val) and flag != "lost":
                    delta_f_over_f[i_pv, i_pl] = (f0_val - f0_ref) / f0_ref

    return {
        "delta_f_over_f": delta_f_over_f,
        "flags": flags,
        "f0_refs": f0_refs,
    }


# =========================================================================
# 验证图生成
# =========================================================================

TEMP_COLORS = {6: "#1565C0", 10: "#0097A7", 20: "#4CAF50", 40: "#FF9800", 77: "#D32F2F"}


def generate_verification_plots(identification_results, output_dir):
    """为每个 (T, resonator) 生成 S21 zoom 验证图。

    每张图显示:
      - 参考 S21 曲线 (zoom 窗口)
      - 识别到的 f0 位置 (红色虚线)
      - 实际谷底最小值 (红色散点)
      - P90 基线 (绿色虚线)
    """
    os.makedirs(output_dir, exist_ok=True)
    total = 0

    for temp_k, info in identification_results.items():
        if info is None:
            continue

        ref_fp = info["reference_file"]
        loaded = load_s2p_complex(ref_fp)
        if loaded is None:
            continue
        freq_ghz, s21_cplx, s21_db = loaded

        for r in info["resonators"]:
            rname = r["name"]
            f0 = r["f0_ghz"]
            dip = r.get("dip_depth_db")

            if f0 is None:
                print(f"  [VERIFY] T={temp_k}K {rname}: MISSED — 无验证图")
                continue

            status = "OK" if dip is not None and dip < -1.0 else "SHALLOW"
            print(f"  [VERIFY] T={temp_k}K {rname}: f0={f0:.4f} GHz  dip={dip:.1f} dB  [{status}]")

            # 画 S21 zoom 图
            half_ghz = zoom_mhz(temp_k) / 1000.0
            mask = np.abs(freq_ghz - f0) <= half_ghz * 1.5
            if mask.sum() < 10:
                continue

            f_zoom = freq_ghz[mask]
            s_zoom = s21_db[mask]

            fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
            ax.plot(f_zoom, s_zoom, color="#1565C0", linewidth=2, alpha=0.9)

            # 标记 f0
            ax.axvline(
                x=f0, color="#D32F2F", linestyle="--", linewidth=1.5, alpha=0.7,
                label=f"f0 = {f0:.4f} GHz"
            )

            # 标记实际最小值
            idx_min = int(np.argmin(s_zoom))
            f_min = f_zoom[idx_min]
            s_min = s_zoom[idx_min]
            ax.scatter([f_min], [s_min], color="#D32F2F", s=50, zorder=5)
            ax.annotate(
                f"min: {f_min:.4f} GHz\n{s_min:.1f} dB",
                xy=(f_min, s_min),
                xytext=(f_min + half_ghz * 0.3, s_min + 2),
                fontsize=8, color="#D32F2F",
                arrowprops=dict(arrowstyle="->", color="#D32F2F", alpha=0.5),
            )

            # P90 基线
            baseline = np.percentile(s_zoom, 90)
            ax.axhline(
                y=baseline, color="#4CAF50", linestyle=":", linewidth=1, alpha=0.5,
                label=f"P90 baseline = {baseline:.1f} dB"
            )

            ax.set_xlabel("Frequency (GHz)", fontsize=12)
            ax.set_ylabel("|S21| (dB)", fontsize=12)
            pv_ref = info["reference_pv_dbm"]
            ax.set_title(
                f"{rname}  (f$_0$ = {f0:.4f} GHz)  @  T = {temp_k} K  "
                f"|  Pv = {pv_ref} dBm, Pl = 0 mW",
                fontsize=13, fontweight="bold",
            )
            ax.legend(fontsize=9, loc="lower left")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            fname = f"T{temp_k}K_{rname}_verify.png"
            fig.savefig(os.path.join(output_dir, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            total += 1

    print(f"  [VERIFY] 共 {total} 张验证图 → {output_dir}")
    return total


# =========================================================================
# 主入口: 收集 + 缓存
# =========================================================================

def collect_and_cache(
    data_dir: str,
    output_base: str = None,
    force_refresh: bool = False,
    skip_verification: bool = False,
    laser_powers_mw: List[int] = None,
    f0_overrides_path: str = None,
):
    """收集全部数据并缓存。

    Args:
        data_dir: 数据根目录，含 {T}K/ 子文件夹
        output_base: 缓存输出根目录 (默认: data_dir 同级的 output/_cache/)
        force_refresh: True = 忽略已有缓存，重新收集
        skip_verification: True = 跳过验证图
        laser_powers_mw: 激光功率列表 (默认: [0,1,3,5,7,9])

    Returns:
        cache dict (也保存到 .pkl 文件)
    """
    if laser_powers_mw is None:
        laser_powers_mw = [0, 1, 3, 5, 7, 9]

    # 缓存文件命名
    dataset_name = os.path.basename(data_dir.rstrip("/\\"))
    if output_base is None:
        output_base = os.path.join(
            os.path.dirname(data_dir), "output", "_cache"
        )
    os.makedirs(output_base, exist_ok=True)
    cache_path = os.path.join(output_base, f"_cache_{dataset_name}.pkl")

    # 检查已有缓存
    if not force_refresh and os.path.exists(cache_path):
        print(f"[CACHE] 加载已有缓存: {cache_path}")
        print(f"[CACHE] 如需刷新请加 --force")
        return load_cache(cache_path)

    # ---- 扫描温度 ----
    temperatures_k = discover_temperatures(data_dir)
    if not temperatures_k:
        print(f"[ERROR] 未找到温度文件夹: {data_dir}")
        return None
    print(f"[SCAN] 发现 {len(temperatures_k)} 个温度: {temperatures_k} K")

    # ---- 识别谐振子 ----
    print(f"[IDENTIFY] 识别谐振子...")
    identification = {}
    for T_K in temperatures_k:
        temp_dir = os.path.join(data_dir, f"{T_K}K")
        if not os.path.isdir(temp_dir):
            identification[T_K] = None
            continue

        info = identify_at_temperature(temp_dir, T_K)
        if info is None:
            print(f"  T={T_K}K: 无可用参考数据")
            identification[T_K] = None
            continue

        n_found = sum(1 for r in info["resonators"] if r["f0_ghz"] is not None)
        print(f"  T={T_K}K: {n_found}/5 谐振子识别, "
              f"{len(info['vna_powers_dbm'])} 个 VNA 功率, "
              f"ref Pv={info['reference_pv_dbm']}dBm")
        identification[T_K] = info

    # ---- 验证图 ----
    if not skip_verification:
        verify_dir = os.path.join(output_base, f"verification_{dataset_name}")
        print(f"\n[VERIFY] 生成验证图...")
        n_verify = generate_verification_plots(identification, verify_dir)
        print(f"[VERIFY] 请检查上述谐振位置是否正确，然后继续。")

    # ---- f0 手动覆盖 (在收集前注入) ----
    if f0_overrides_path and os.path.exists(f0_overrides_path):
        print(f"\n[OVERRIDE] 加载 f0 覆盖文件: {f0_overrides_path}")
        overrides = load_overrides(f0_overrides_path)
        n = patch_identification(identification, overrides)
        print(f"[OVERRIDE] 共覆盖 {n} 个 f0 值")
    elif f0_overrides_path:
        print(f"\n[WARN] f0 覆盖文件不存在: {f0_overrides_path}")

    # ---- 收集 δf/f₀ ----
    print(f"\n[COLLECT] collecting delta_f/f0 data...")
    collected = {}
    total_tracked = 0
    total_points = 0

    for T_K in temperatures_k:
        info = identification[T_K]
        if info is None:
            collected[T_K] = None
            continue

        temp_dir = os.path.join(data_dir, f"{T_K}K")
        vna_powers = info["vna_powers_dbm"]
        collected[T_K] = {
            "vna_powers_dbm": vna_powers,
            "reference": {
                "file": info["reference_file"],
                "vna_power_dbm": info["reference_pv_dbm"],
                "laser_power_mw": info["reference_pl_mw"],
            },
            "identified": {},
            "data": {},
        }

        for r in info["resonators"]:
            if r["f0_ghz"] is None:
                continue
            rname = r["name"]
            collected[T_K]["identified"][rname] = {
                "f0_ghz": r["f0_ghz"],
                "dip_depth_db": r["dip_depth_db"],
            }

            dff_data = collect_dff_for_resonator(
                temp_dir, T_K, rname, r["f0_ghz"],
                vna_powers, laser_powers_mw,
            )
            collected[T_K]["data"][rname] = dff_data

            n_tracked = int(np.sum(dff_data["flags"] == "tracked"))
            total_tracked += n_tracked
            total_points += len(vna_powers) * len(laser_powers_mw)

            if n_tracked > 0:
                print(f"  T={T_K}K {rname}: {n_tracked} tracked "
                      f"({len(vna_powers)} Pv × {len(laser_powers_mw)} Pl)")

    # ---- 组装缓存 ----
    cache = {
        "metadata": {
            "data_dir": os.path.abspath(data_dir),
            "dataset_name": dataset_name,
            "collected_at": datetime.now().isoformat(),
            "temperatures_k": temperatures_k,
            "laser_powers_mw": laser_powers_mw,
            "resonator_names": RESONATOR_NAMES,
            "total_tracked_points": total_tracked,
            "total_grid_points": total_points,
        },
        "identification": identification,
        "collected": collected,
    }

    # ---- 保存 ----
    with open(cache_path, "wb") as f:
        pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)

    file_size_mb = os.path.getsize(cache_path) / (1024 * 1024)
    print(f"\n[CACHE] 已保存: {cache_path} ({file_size_mb:.1f} MB)")
    print(f"[CACHE] {total_tracked}/{total_points} 数据点追踪成功")

    return cache


def load_cache(cache_path: str):
    """加载缓存的 .pkl 文件。"""
    if not os.path.exists(cache_path):
        raise FileNotFoundError(f"缓存文件不存在: {cache_path}")
    with open(cache_path, "rb") as f:
        return pickle.load(f)


def find_cache(data_dir: str, output_base: str = None):
    """根据数据目录查找对应的缓存文件。"""
    dataset_name = os.path.basename(data_dir.rstrip("/\\"))
    if output_base is None:
        output_base = os.path.join(
            os.path.dirname(data_dir), "output", "_cache"
        )
    cache_path = os.path.join(output_base, f"_cache_{dataset_name}.pkl")
    if os.path.exists(cache_path):
        return cache_path
    return None


# =========================================================================
# 缓存摘要
# =========================================================================

def print_cache_summary(cache: dict):
    """打印缓存内容摘要。"""
    meta = cache["metadata"]
    print("=" * 60)
    print(f"数据集: {meta['dataset_name']}")
    print(f"采集时间: {meta['collected_at']}")
    print(f"温度: {meta['temperatures_k']} K")
    print(f"激光功率: {meta['laser_powers_mw']} mW")
    print(f"谐振子: {meta['resonator_names']}")
    print(f"追踪率: {meta['total_tracked_points']}/{meta['total_grid_points']}")
    print("-" * 60)

    for T_K in meta["temperatures_k"]:
        c = cache["collected"].get(T_K)
        if c is None:
            print(f"  T={T_K}K: 无数据")
            continue
        n_pv = len(c["vna_powers_dbm"])
        id_names = list(c["identified"].keys())
        print(f"  T={T_K}K: {n_pv} Pv, {len(id_names)}/5 谐振子: {id_names}")
        for rname in id_names:
            ident = c["identified"][rname]
            d = c["data"][rname]
            n_trk = int(np.sum(d["flags"] == "tracked"))
            print(f"    {rname}: f0={ident['f0_ghz']:.4f} GHz, "
                  f"dip={ident['dip_depth_db']:.1f} dB, "
                  f"tracked={n_trk}/{n_pv * len(meta['laser_powers_mw'])}")


# =========================================================================
# CLI
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="统一数据收集 + 缓存 + 验证",
    )
    parser.add_argument(
        "--data-dir", required=True,
        help="数据根目录 (含 {T}K/ 子文件夹)",
    )
    parser.add_argument(
        "--output-base",
        help="缓存输出根目录 (默认: data_dir 同级的 output/_cache/)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="强制刷新缓存",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="仅生成验证图 (不重新收集)",
    )
    parser.add_argument(
        "--laser-powers", type=int, nargs="+",
        default=[0, 1, 3, 5, 7, 9],
        help="激光功率列表 (mW), 默认: 0 1 3 5 7 9",
    )
    parser.add_argument(
        "--f0-overrides",
        help="人工 f0 修正 JSON 文件路径 (格式: {\"T\": {\"R1\": f0, ...}})",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="仅打印缓存摘要",
    )

    args = parser.parse_args()

    if args.summary:
        cache_path = find_cache(args.data_dir, args.output_base)
        if cache_path:
            cache = load_cache(cache_path)
            print_cache_summary(cache)
        else:
            print(f"未找到缓存: {args.data_dir}")
        return

    if args.verify_only:
        cache_path = find_cache(args.data_dir, args.output_base)
        if cache_path is None:
            print(f"[ERROR] 未找到缓存，请先运行收集: --data-dir {args.data_dir}")
            return
        cache = load_cache(cache_path)
        dataset_name = cache["metadata"]["dataset_name"]
        output_base = args.output_base or os.path.join(
            os.path.dirname(args.data_dir), "output", "_cache"
        )
        verify_dir = os.path.join(output_base, f"verification_{dataset_name}")
        generate_verification_plots(cache["identification"], verify_dir)
        return

    cache = collect_and_cache(
        data_dir=args.data_dir,
        output_base=args.output_base,
        force_refresh=args.force,
        laser_powers_mw=args.laser_powers,
        f0_overrides_path=args.f0_overrides,
    )

    if cache:
        print_cache_summary(cache)


if __name__ == "__main__":
    main()
