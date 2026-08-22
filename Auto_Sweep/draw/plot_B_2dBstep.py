# -*- coding: utf-8 -*-
# DEPRECATED (2026-06-23): 使用 plot_all.py --approaches B --vna-powers -55,-53,...,-39 --style fit_only 替代。
# 此文件保留仅供代码参考，不再维护。
"""方案 B: 9 条 VNA 曲线，2dB 间隔，-55 ~ -39 dBm。仅拟合实线版本。"""
import os, sys, re, numpy as np
import matplotlib; matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter
import skrf as rf

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from plot_VNA_powersweep import find_s2p_files_for_laser_power
from _tracking_utils import identify_resonators, track_across_pl, RESONATOR_NAMES, SCRAPS_F0

EXPERIMENT_DATA_DIR = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB"
TEMPERATURES_K = [6, 10, 20, 40, 77]
LASER_POWERS_MW = [0, 1, 3, 5, 7, 9]
SELECTED_VNA_DBM = list(range(-55, -38, 2))  # -55, -53, ..., -39 (9 levels)
SG_WINDOW_LENGTH = 101; SG_POLYORDER = 3

_OUTPUT_BASE = r"D:\YBCO\VNAMeas\Data_process\output\_vna_2dBm_step_analysis\deltaf_vs_laser"
OUT_DIR = os.path.join(_OUTPUT_BASE, "approach_B_grid", "individual_2dBstep")

# 9 条 VNA 配色: 深蓝→青→绿→黄→橙→红
VNA_9_COLORS = ["#0D47A1","#1565C0","#1976D2","#00838F","#2E7D32","#558B2F","#F57F17","#E65100","#BF360C"]
TEMP_COLORS = {6:"#1565C0",10:"#0097A7",20:"#4CAF50",40:"#FF9800",77:"#D32F2F"}

def extract_vna_power_from_path(path):
    m = re.search(r"[\\/](-\d+)dBm[\\/]", path.replace("\\","/")+"/")
    return int(m.group(1)) if m else None
def find_s2p_for_pv_pl(td, pv, pl):
    candidates = find_s2p_files_for_laser_power(td, pl)
    for fp in candidates:
        if extract_vna_power_from_path(fp) == pv: return fp
    return None
def load_s2p_complex(fp):
    try:
        ntwk = rf.Network(fp); freq = ntwk.f/1e9; s21 = ntwk.s[:,1,0]
        s21_db = 20*np.log10(np.abs(s21)); mask = np.isfinite(s21_db)
        if not mask.any(): return None
        return freq[mask], s21[mask], s21_db[mask]
    except: return None
def smooth_curve(y):
    valid = ~np.isnan(y)
    if valid.sum() < SG_POLYORDER+2: return y.copy()
    wl = min(SG_WINDOW_LENGTH, valid.sum())
    if wl%2==0: wl-=1
    if wl < SG_POLYORDER+2: return y.copy()
    y_s = y.copy(); y_s[valid] = savgol_filter(y[valid], wl, SG_POLYORDER)
    return y_s

def collect_data(temp_dir, temp_k, resonator_name, f0_identified):
    from _tracking_utils import track_one_resonator, SCRAPS_TEMPS, detect_dip_p90
    vna_powers = SELECTED_VNA_DBM
    n_pv, n_pl = len(vna_powers), len(LASER_POWERS_MW)
    dff = np.full((n_pv, n_pl), np.nan)
    flags = np.full((n_pv, n_pl), "lost", dtype=object)
    pl0_idx = LASER_POWERS_MW.index(0)
    f0_at_pl0 = {}

    for i_pv, pv in enumerate(vna_powers):
        fp = find_s2p_for_pv_pl(temp_dir, pv, 0)
        if fp is None: continue
        loaded = load_s2p_complex(fp)
        if loaded is None: continue
        freq, s21_c, s21_db = loaded
        if f0_identified is not None:
            f_dip, dd, bl = detect_dip_p90(freq, s21_db, f0_identified, search_mhz=30)
            if f_dip is not None and dd < -0.5:
                f0_at_pl0[pv] = f_dip; dff[i_pv, pl0_idx] = 0.0; flags[i_pv, pl0_idx] = "tracked"
                continue
        f0_hist = []
        for pp in vna_powers[:i_pv]:
            if pp in f0_at_pl0 and f0_at_pl0[pp] is not None:
                f0_hist.append((temp_k, f0_at_pl0[pp]))
        skeleton = SCRAPS_F0[resonator_name]
        base_hist = [(t,f) for t,f in zip(SCRAPS_TEMPS, skeleton) if t <= temp_k+15]
        full_hist = base_hist + f0_hist[-3:]
        f0, dip = track_one_resonator(freq*1e9, s21_db, s21_c, resonator_name, temp_k, full_hist)
        if f0 is not None:
            if f0_identified is not None and abs(f0 - f0_identified) > 0.050: continue
            f0_at_pl0[pv] = f0; dff[i_pv, pl0_idx] = 0.0; flags[i_pv, pl0_idx] = "tracked"

    for i_pv, pv in enumerate(vna_powers):
        if pv not in f0_at_pl0 or f0_at_pl0[pv] is None: continue
        f0_ref = f0_at_pl0[pv]
        s2p_by_pl = {}
        for pl in LASER_POWERS_MW:
            if pl == 0: continue
            fp = find_s2p_for_pv_pl(temp_dir, pv, pl)
            if fp is None: continue
            l = load_s2p_complex(fp)
            if l is None: continue
            s2p_by_pl[pl] = (l[0]*1e9, l[2], l[1])
        if not s2p_by_pl: continue
        result = track_across_pl(s2p_by_pl, resonator_name, temp_k, f0_ref)
        for i_pl, pl in enumerate(LASER_POWERS_MW):
            if pl == 0: continue
            if pl in result["pl_list"]:
                idx = result["pl_list"].index(pl)
                f0_val = result["f0_ghz"][idx]; flag = result["flags"][idx]
                flags[i_pv, i_pl] = flag
                if not np.isnan(f0_val) and flag != "lost":
                    dff[i_pv, i_pl] = (f0_val - f0_ref)/f0_ref
    return {"pv_list":vna_powers, "pl_list":LASER_POWERS_MW,
            "delta_f_over_f":dff, "flags":flags, "f0_ghz":f0_identified}

def draw_fit_only(ax, data, show_legend=True):
    pv_list = data["pv_list"]; pl_list = data["pl_list"]
    dff = data["delta_f_over_f"]; flags = data["flags"]
    for i_pv, pv in enumerate(pv_list):
        y_raw = dff[i_pv,:]*1e6; fl = flags[i_pv,:]
        valid = ~np.isnan(y_raw) & (fl!="lost")
        if valid.sum() < 2: continue
        x_data = np.array(pl_list)[valid]; y_data = y_raw[valid]
        y_smooth = smooth_curve(y_data)
        mask_ok = np.isfinite(y_smooth)
        if mask_ok.sum() < 2: continue
        x_plot = x_data[mask_ok]; y_plot = y_smooth[mask_ok]
        try:
            coeffs = np.polyfit(x_plot, y_plot, 1)
        except: continue
        color = VNA_9_COLORS[i_pv % len(VNA_9_COLORS)]
        label = f"{pv} dBm" if show_legend else None
        x_fit = np.linspace(x_plot.min(), x_plot.max(), 50)
        y_fit = np.polyval(coeffs, x_fit)
        ax.plot(x_fit, y_fit, '-', color=color, linewidth=2.5, alpha=0.9, label=label)
    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle=":")
    ax.grid(True, alpha=0.25)
    ax.set_xlabel("Laser Power (mW)", fontsize=9)
    ax.set_ylabel("df/f (ppm)", fontsize=9)

def main():
    print("="*60)
    print(f"B: 2dB step, {len(SELECTED_VNA_DBM)} VNA lines: {SELECTED_VNA_DBM}")
    print("="*60)
    all_data = {}
    for T_K in TEMPERATURES_K:
        td = os.path.join(EXPERIMENT_DATA_DIR, f"{T_K}K")
        if not os.path.isdir(td): continue
        all_data[T_K] = {}
        if T_K >= 70:
            ref_fp = None
            for pv_try in [-25,-27,-29,-31,-35,-45,-55]:
                ref_fp = find_s2p_for_pv_pl(td, pv_try, 0)
                if ref_fp is not None: break
        else:
            ref_fp = find_s2p_for_pv_pl(td, -55, 0)
        if ref_fp is None: continue
        loaded = load_s2p_complex(ref_fp)
        if loaded is None: continue
        resonators = identify_resonators(loaded[0]*1e9, loaded[2], loaded[1], T_K)
        print(f"  T={T_K}K: {sum(1 for r in resonators if r['f0_ghz'])}/5")
        for r in resonators:
            if r["f0_ghz"] is None: continue
            data = collect_data(td, T_K, r["name"], r["f0_ghz"])
            if np.sum(data["flags"]=="tracked") >= 3:
                all_data[T_K][r["name"]] = data

    os.makedirs(OUT_DIR, exist_ok=True)
    count = 0
    for T_K in TEMPERATURES_K:
        for rn in RESONATOR_NAMES:
            if T_K not in all_data or rn not in all_data[T_K]: continue
            data = all_data[T_K][rn]
            f0 = data.get("f0_ghz")
            freq_str = f"f$_0$ = {f0:.3f} GHz" if f0 else ""
            fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
            draw_fit_only(ax, data, show_legend=True)
            ax.set_title(f"{rn}  ({freq_str})  @  {T_K} K  |  df/f vs Laser Power  (2dB step)",
                         fontsize=13, fontweight="bold")
            ax.legend(title="VNA Power (dBm)", loc="lower left", fontsize=7, ncol=2)
            fname = f"T{T_K}K_{rn}.png"
            fig.savefig(os.path.join(OUT_DIR, fname), dpi=150, bbox_inches="tight")
            plt.close(fig); count += 1
    print(f"Done: {count} figures -> {OUT_DIR}")

if __name__ == "__main__":
    main()
