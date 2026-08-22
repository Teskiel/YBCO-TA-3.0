# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 调试用，不再需要。数据验证已整合进 _data_cache.py。
# 此文件保留仅供代码参考，不再维护。
"""诊断 77K R1/R5 追踪失败根因 — 逐激光功率对比 S21 频谱和追踪结果。"""
import os, sys, re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import (
    detect_dip_p90, find_all_dips_p90, SCRAPS_F0, SCRAPS_TEMPS,
    predict_f0_fd, track_one_resonator, track_across_pl,
)

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
OUT_DIR = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser\diagnostics"
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]

def extract_vna_power_from_path(path):
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None

def find_s2p_for_pv_pl(temp_dir, target_pv, target_pl):
    candidates = find_s2p_files_for_laser_power(temp_dir, target_pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == target_pv: return fp
    return None

import skrf as rf
def load_s2p_complex(file_path):
    try:
        ntwk = rf.Network(file_path)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        mask = np.isfinite(s21_db)
        if not mask.any(): return None
        return freq[mask], s21[mask], s21_db[mask]
    except: return None

os.makedirs(OUT_DIR, exist_ok=True)

# ===================================================================
# 对 77K 的 R1 和 R5，每个 VNA 功率画 Pl=0..9 的局部 S21 + 追踪结果
# ===================================================================
F0_ID = {"R1": 3.3982, "R5": 4.6565}
TARGET_PVS = [-55, -43, -25]  # 最低、中间、最高 VNA 功率
SEARCH_WINDOW_MHZ = 300  # 较宽窗口看全景

for rn in ["R1", "R5"]:
    f0_id = F0_ID[rn]

    # 画汇总图: 3 行 (VNA 功率) × 1 列
    fig, axes = plt.subplots(len(TARGET_PVS), 1, figsize=(16, 5 * len(TARGET_PVS)), dpi=150)
    if len(TARGET_PVS) == 1:
        axes = [axes]

    for i_pv, target_pv in enumerate(TARGET_PVS):
        ax = axes[i_pv]

        # 收集该 VNA 功率下所有 Pl 的 S21
        traces = {}
        for pl in LASER_POWERS_MW:
            fp = find_s2p_for_pv_pl(os.path.join(EXPERIMENT_DATA_DIR, "77K"), target_pv, pl)
            if fp is None:
                print(f"  {rn} Pv={target_pv} Pl={pl}: FILE NOT FOUND")
                continue
            loaded = load_s2p_complex(fp)
            if loaded is None:
                print(f"  {rn} Pv={target_pv} Pl={pl}: LOAD FAILED")
                continue
            traces[pl] = loaded

        if 0 not in traces:
            continue

        # ---- 先跟踪 Pl=0 的 f0 ----
        freq_ghz_0 = traces[0][0]
        s21_db_0 = traces[0][2]
        s21_cplx_0 = traces[0][1]

        # 检查权威 f0 附近是否有真正的 dip
        f0_ref = None
        f_dip, dd, bl = detect_dip_p90(freq_ghz_0, s21_db_0, f0_id, search_mhz=50)
        print(f"\n=== {rn} Pv={target_pv} dBm Pl=0 ===")
        print(f"  identify_resonators f0 = {f0_id:.4f} GHz")
        print(f"  detect_dip_p90 at {f0_id:.4f}: f={f_dip:.4f} GHz, dip_depth={dd:.1f} dB, baseline={bl:.1f} dB"
              if f_dip else f"  detect_dip_p90: NOT FOUND within ±50 MHz")

        if f_dip is not None:
            f0_ref = f_dip
        else:
            f0_ref = f0_id  # fallback to identified

        # ---- 对每个 Pl 追踪 f0 ----
        # 建立跨 Pl 追踪
        s2p_by_pl = {}
        for pl in LASER_POWERS_MW:
            if pl == 0: continue
            if pl in traces:
                s2p_by_pl[pl] = (traces[pl][0] * 1e9, traces[pl][2], traces[pl][1])

        tracked_data = {"f0_ghz": [], "pl": [], "dip_depth": [], "flag": []}

        # Pl=0
        tracked_data["f0_ghz"].append(f0_ref)
        tracked_data["pl"].append(0)
        tracked_data["dip_depth"].append(dd if f_dip else np.nan)
        tracked_data["flag"].append("ref")

        if s2p_by_pl:
            result = track_across_pl(s2p_by_pl, rn, 77, f0_ref)
        else:
            result = {"pl_list": [], "f0_ghz": [], "dip_depth": [], "flags": []}

        # 合并结果
        for j, pl in enumerate(LASER_POWERS_MW):
            if pl == 0: continue
            if pl in result["pl_list"]:
                idx = result["pl_list"].index(pl)
                tracked_data["f0_ghz"].append(result["f0_ghz"][idx])
                tracked_data["pl"].append(pl)
                tracked_data["dip_depth"].append(result["dip_depth"][idx])
                tracked_data["flag"].append(result["flags"][idx])

        # ---- 画图 ----
        colors = plt.cm.plasma(np.linspace(0.1, 0.9, len(LASER_POWERS_MW)))

        # 找出全局 x 范围: f0_id ± SEARCH_WINDOW_MHZ
        x_lo = f0_id - SEARCH_WINDOW_MHZ / 1000.0
        x_hi = f0_id + SEARCH_WINDOW_MHZ / 1000.0

        for i_pl, pl in enumerate(LASER_POWERS_MW):
            if pl not in traces: continue
            freq_g, s21_c, s21_d = traces[pl]
            mask = (freq_g >= x_lo) & (freq_g <= x_hi)
            if mask.sum() < 10: continue
            ax.plot(freq_g[mask], s21_d[mask], color=colors[i_pl], linewidth=1.5, alpha=0.8,
                    label=f"Pl={pl} mW")

        # 标记追踪到的 f0 位置
        for j in range(len(tracked_data["pl"])):
            pl_val = tracked_data["pl"][j]
            f0_val = tracked_data["f0_ghz"][j]
            flag = tracked_data["flag"][j]
            if np.isnan(f0_val): continue

            marker = 'v' if flag == 'ref' else 'o'
            color_marker = 'green' if flag == 'tracked' else 'red'
            size = 80 if flag == 'ref' else 40
            ax.scatter([f0_val], [-5], color=color_marker, marker=marker, s=size, zorder=10,
                       edgecolors='black', linewidths=0.5)
            ax.annotate(f"f0={f0_val:.4f}\nPl={pl_val}",
                        xy=(f0_val, -5), fontsize=7, ha='center', va='top',
                        color=color_marker,
                        xytext=(0, -15), textcoords='offset points')

        # 标注 identify_resonators 位置
        ax.axvline(x=f0_id, color='blue', linestyle=':', linewidth=1.5, alpha=0.7,
                   label=f"identify f0={f0_id:.4f} GHz")

        # All dips from P90 scan at Pl=0
        all_dips_0 = find_all_dips_p90(freq_ghz_0, s21_db_0, 77, prominence_db=0.5)
        for f_d, d_d, b_d in all_dips_0:
            if x_lo <= f_d <= x_hi and d_d < -1.0:
                ax.axvline(x=f_d, color='gray', linestyle='--', linewidth=0.5, alpha=0.3)

        ax.set_xlabel("Frequency (GHz)", fontsize=11)
        ax.set_ylabel("|S21| (dB)", fontsize=11)
        ax.set_title(f"{rn} @ 77K, Pv={target_pv} dBm  |  Green=tracked  Red=lost  Blue=identify f0",
                     fontsize=12, fontweight="bold")
        ax.legend(fontsize=7, ncol=3, loc="upper right")
        ax.grid(True, alpha=0.25)
        ax.set_ylim(-10, 10)

    fig.suptitle(f"77K {rn} — Cross-Pl Tracking Diagnostic  (window = ±{SEARCH_WINDOW_MHZ} MHz)",
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    out_path = os.path.join(OUT_DIR, f"T77K_{rn}_diagnostic.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [saved] T77K_{rn}_diagnostic.png")

print(f"\nDone. Output: {OUT_DIR}")
