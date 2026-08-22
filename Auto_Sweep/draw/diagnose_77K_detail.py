# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 调试用，不再需要。数据验证已整合进 _data_cache.py。
# 此文件保留仅供代码参考，不再维护。
"""逐 Pl 追踪详细输出"""
import os, sys, re, numpy as np
import skrf as rf

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import track_across_pl, detect_dip_p90

base = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB\77K"
F0_ID = {"R1": 3.3982, "R5": 4.6565}
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]

def extract_vna_power_from_path(path):
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\", "/") + "/")
    return int(m.group(1)) if m else None

def find_s2p_for_pv_pl(td, pv, pl):
    candidates = find_s2p_files_for_laser_power(td, pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == pv: return fp
    return None

def load_s2p(fp):
    try:
        ntwk = rf.Network(fp)
        freq = ntwk.f / 1e9
        s21 = ntwk.s[:, 1, 0]
        s21_db = 20 * np.log10(np.abs(s21))
        mask = np.isfinite(s21_db)
        if not mask.any(): return None
        return freq[mask], s21[mask], s21_db[mask]
    except: return None

for rn in ["R1", "R5"]:
    for target_pv in [-55, -43, -25]:
        f0_id = F0_ID[rn]
        fp0 = find_s2p_for_pv_pl(base, target_pv, 0)
        l0 = load_s2p(fp0)
        f_dip, dd, bl = detect_dip_p90(l0[0], l0[2], f0_id, search_mhz=80)
        f0_ref = f_dip if f_dip else f0_id
        print(f"\n=== {rn} Pv={target_pv} dBm  f0_ref={f0_ref:.4f} GHz ===")

        s2p_by_pl = {}
        for pl in LASER_POWERS_MW:
            if pl == 0: continue
            fp = find_s2p_for_pv_pl(base, target_pv, pl)
            if fp is None: continue
            l = load_s2p(fp)
            if l is None: continue
            s2p_by_pl[pl] = (l[0] * 1e9, l[2], l[1])

        result = track_across_pl(s2p_by_pl, rn, 77, f0_ref)

        for pl in LASER_POWERS_MW:
            if pl == 0:
                print(f"  Pl={pl:>2d}: f0={f0_ref:.4f} GHz  (reference)")
            elif pl in result["pl_list"]:
                idx = result["pl_list"].index(pl)
                f0_val = result["f0_ghz"][idx]
                dd_val = result["dip_depth"][idx]
                flag = result["flags"][idx]
                df_mhz = (f0_val - f0_ref) * 1000
                df_ppm = (f0_val - f0_ref) / f0_ref * 1e6
                print(f"  Pl={pl:>2d}: f0={f0_val:.4f} GHz  df={df_mhz:.1f} MHz  df/f={df_ppm:.0f} ppm  dip={dd_val:.1f} dB  {flag}")
            else:
                print(f"  Pl={pl:>2d}: NOT IN RESULT")
