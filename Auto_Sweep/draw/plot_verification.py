# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 使用 _data_cache.py (自动生成验证图) 替代。
# 此文件保留仅供代码参考，不再维护。
"""验证图: 每 (T, resonator) 的 S21 参考曲线 + f0 标注。
使用与 plot_s21_overlay_batch.py 完全一致的算法和参考文件选择。

输出: approach_B_grid/verification/
  每格一张 S21 zoom 图，标注 identify_resonators 返回的 f0 位置。
"""

import os, sys, re
import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
import skrf as rf

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import identify_resonators, zoom_mhz, RESONATOR_NAMES

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]

_OUTPUT_BASE = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser"
OUT_DIR = os.path.join(_OUTPUT_BASE, "approach_B_grid", "verification")


def extract_vna_power_from_path(path):
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None

def find_s2p_for_pv_pl(temp_dir, target_pv, target_pl):
    candidates = find_s2p_files_for_laser_power(temp_dir, target_pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == target_pv: return fp
    return None

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


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("=" * 60)
    print("Verification: S21 curves with identified f0 markers")
    print(f"Output: {OUT_DIR}")
    print("=" * 60)

    total = 0
    for T_K in TEMPERATURES_K:
        temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(temp_dir): continue

        # ---- 参考文件选择 (与 S21 overlay 完全一致) ----
        # 取 Pl=0, 选择参考 VNA 功率: 低温用最低, 高温用最高
        if T_K >= 70:
            ref_pl = 0
            ref_fp = None
            for pv_try in [-25, -27, -29, -31, -35, -45, -55]:
                ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, ref_pl)
                if ref_fp is not None: break
        else:
            ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)
        if ref_fp is None: continue

        ref_pv = extract_vna_power_from_path(ref_fp)

        loaded = load_s2p_complex(ref_fp)
        if loaded is None: continue
        freq_ghz, s21_cplx, s21_db = loaded

        # ---- identify_resonators (与 S21 overlay 同源) ----
        resonators = identify_resonators(freq_ghz * 1e9, s21_db, s21_cplx, T_K)
        print(f"\nT={T_K}K (ref Pv={ref_pv}dBm Pl=0):")

        for r in resonators:
            rname = r["name"]
            f0 = r["f0_ghz"]
            dip = r.get("dip_depth", None)

            if f0 is None:
                print(f"  {rname}: MISSED")
                continue

            print(f"  {rname}: f0={f0:.4f} GHz  dip={dip:.1f} dB" if dip else f"  {rname}: f0={f0:.4f} GHz")

            # ---- 画 S21 zoom 图 ----
            half_ghz = zoom_mhz(T_K) / 1000.0
            mask = np.abs(freq_ghz - f0) <= half_ghz * 1.5  # 稍宽一点看到上下文
            if mask.sum() < 10: continue

            f_zoom = freq_ghz[mask]
            s_zoom = s21_db[mask]

            fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
            ax.plot(f_zoom, s_zoom, color="#1565C0", linewidth=2, alpha=0.9)

            # 标记 f0
            ax.axvline(x=f0, color="#D32F2F", linestyle="--", linewidth=1.5, alpha=0.7,
                       label=f"f0 = {f0:.4f} GHz")

            # 标记谷底 (数据中的实际最小值)
            idx_min = int(np.argmin(s_zoom))
            f_min = f_zoom[idx_min]
            s_min = s_zoom[idx_min]
            ax.scatter([f_min], [s_min], color="#D32F2F", s=50, zorder=5)
            ax.annotate(f"min: {f_min:.4f} GHz\n{s_min:.1f} dB",
                        xy=(f_min, s_min), xytext=(f_min + half_ghz*0.3, s_min + 2),
                        fontsize=8, color="#D32F2F",
                        arrowprops=dict(arrowstyle="->", color="#D32F2F", alpha=0.5))

            # P90 基线
            baseline = np.percentile(s_zoom, 90)
            ax.axhline(y=baseline, color="#4CAF50", linestyle=":", linewidth=1, alpha=0.5,
                       label=f"P90 baseline = {baseline:.1f} dB")

            ax.set_xlabel("Frequency (GHz)", fontsize=12)
            ax.set_ylabel("|S21| (dB)", fontsize=12)
            ax.set_title(f"{rname}  (f$_0$ = {f0:.4f} GHz)  @  T = {T_K} K  |  Pv = {ref_pv} dBm, Pl = 0 mW",
                         fontsize=13, fontweight="bold")
            ax.legend(fontsize=9, loc="lower left")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            fname = f"T{T_K}K_{rname}_verify.png"
            fig.savefig(os.path.join(OUT_DIR, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            total += 1

    print(f"\nDone: {total} verification figures saved to {OUT_DIR}")


if __name__ == "__main__":
    main()
