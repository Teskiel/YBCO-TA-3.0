# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 调试用，不再需要。数据验证已整合进 _data_cache.py。
# 此文件保留仅供代码参考，不再维护。
"""诊断 A/C 方案的 no-fit 问题"""
import os, sys, re
import numpy as np
from scipy.signal import savgol_filter
import skrf as rf

from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import identify_resonators, track_across_pl, SCRAPS_F0, SCRAPS_TEMPS, track_one_resonator

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]

def extract_vna_power_from_path(path):
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None

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

def find_s2p_for_pv_pl(temp_dir, target_pv, target_pl):
    candidates = find_s2p_files_for_laser_power(temp_dir, target_pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == target_pv: return fp
    return None

def smooth_curve(y):
    valid = ~np.isnan(y)
    if valid.sum() < 5: return y.copy()
    wl = min(101, valid.sum())
    if wl % 2 == 0: wl -= 1
    if wl < 5: return y.copy()
    y_smooth = y.copy()
    y_smooth[valid] = savgol_filter(y[valid], wl, 3)
    return y_smooth

for T_K in [6, 40, 77]:
    temp_dir = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
    print(f"\n{'='*70}")
    print(f"T={T_K}K DIAGNOSTIC")
    print(f"{'='*70}")

    if T_K >= 70:
        ref_fp = None
        for pv_try in [-25, -35, -45, -55]:
            ref_fp = find_s2p_for_pv_pl(temp_dir, pv_try, 9)
            if ref_fp is not None: break
    else:
        ref_fp = find_s2p_for_pv_pl(temp_dir, -55, 0)

    ref_loaded = load_s2p_complex(ref_fp)
    if ref_loaded is None: continue
    ref_freq, ref_s21_cplx, ref_s21_db = ref_loaded

    resonators = identify_resonators(ref_freq * 1e9, ref_s21_db, ref_s21_cplx, T_K)

    for r in resonators[:2]:  # R1, R2 only
        rname = r["name"]
        if r["f0_ghz"] is None:
            print(f"  {rname}: MISSED")
            continue

        # Collect data
        all_pv = set()
        for Pl_mW in LASER_POWERS_MW:
            for fp in find_s2p_files_for_laser_power(temp_dir, Pl_mW):
                pv = extract_vna_power_from_path(fp)
                if pv is not None: all_pv.add(pv)
        vna_powers = sorted(all_pv)

        # Step 1: Pl=0 ref
        f0_at_pl0 = {}
        for i_pv, pv in enumerate(vna_powers):
            fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
            if fp is None: continue
            loaded = load_s2p_complex(fp)
            if loaded is None: continue
            freq, s21_cplx, s21_db = loaded
            f0_history_local = []
            for prev_pv in vna_powers[:i_pv]:
                if prev_pv in f0_at_pl0 and f0_at_pl0[prev_pv] is not None:
                    f0_history_local.append((T_K, f0_at_pl0[prev_pv]))
            skeleton_f0 = SCRAPS_F0[rname]
            base_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0) if t <= T_K + 15]
            full_history = base_history + f0_history_local[-3:]
            f0, dip = track_one_resonator(freq * 1e9, s21_db, s21_cplx, rname, T_K, full_history)
            if f0 is not None:
                f0_at_pl0[pv] = f0

        # Step 2: Responsivity per VNA power
        n_good, n_bad = 0, 0
        for i_pv, pv in enumerate(vna_powers):
            if pv not in f0_at_pl0 or f0_at_pl0[pv] is None: continue
            f0_ref = f0_at_pl0[pv]

            s2p_by_pl = {}
            for Pl_mW in LASER_POWERS_MW:
                if Pl_mW == 0: continue
                fp = find_s2p_for_pv_pl(temp_dir, pv, Pl_mW)
                if fp is None: continue
                loaded = load_s2p_complex(fp)
                if loaded is None: continue
                s2p_by_pl[Pl_mW] = (loaded[0] * 1e9, loaded[2], loaded[1])

            if not s2p_by_pl: continue

            result = track_across_pl(s2p_by_pl, rname, T_K, f0_ref)

            x_vals, y_vals = [], []
            for pl in LASER_POWERS_MW:
                if pl == 0: continue
                if pl in result["pl_list"]:
                    idx = result["pl_list"].index(pl)
                    f0_val = result["f0_ghz"][idx]
                    flag = result["flags"][idx]
                    if not np.isnan(f0_val) and flag != "lost":
                        x_vals.append(pl)
                        y_vals.append((f0_val - f0_ref) / f0_ref * 1e6)

            if len(x_vals) < 3:
                n_bad += 1
                continue

            x_arr = np.array(x_vals)
            y_arr = np.array(y_vals)
            y_smooth = smooth_curve(y_arr)
            mask_ok = np.isfinite(y_smooth)
            if mask_ok.sum() < 3:
                n_bad += 1
                continue

            x_fit, y_fit = x_arr[mask_ok], y_smooth[mask_ok]
            coeffs = np.polyfit(x_fit, y_fit, 1)
            y_pred = np.polyval(coeffs, x_fit)
            ss_res = np.sum((y_fit - y_pred)**2)
            ss_tot = np.sum((y_fit - np.mean(y_fit))**2)
            r2 = 1 - ss_res/ss_tot if ss_tot > 1e-15 else 0

            if r2 > 0.5:
                n_good += 1
            else:
                n_bad += 1
                if n_bad <= 3:  # print first few
                    print(f"  {rname} Pv={pv:>4d}: slope={coeffs[0]:.1f} ppm/mW  R2={r2:.3f}  n={len(x_fit)}  vals={[f'{v:.1f}' for v in y_fit]}")

        print(f"  {rname}: {n_good} good (R2>0.5), {n_bad} bad — out of {len(vna_powers)} VNA powers")

print("\nDone.")
