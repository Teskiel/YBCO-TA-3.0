# -*- coding: utf-8 -*-
"""Regenerate verification images from fixed cache, then re-run plots."""
import skrf as rf
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os, pickle, subprocess, sys

base = 'experiment_data/~merged/20260609-0624__6-80K__full'
cache_path = 'experiment_data/~merged/output/_cache/_cache_20260609-0624__6-80K__full.pkl'
verify_dir = 'experiment_data/~merged/output/_cache/verification_20260609-0624__6-80K__full'

with open(cache_path, 'rb') as f:
    cache = pickle.load(f)

# ============================================================
# Regenerate ALL verification images from cache's identified f0
# ============================================================
# Clear old verification images
for f in os.listdir(verify_dir):
    if f.endswith('_verify.png'):
        os.remove(os.path.join(verify_dir, f))
print(f'Cleared old verification images')

for t_k in cache['collected']:
    c = cache['collected'][t_k]
    t_str = f'{t_k:.0f}K'
    ref_pv = c['reference'].get('vna_power_dbm', -55)
    ref_pl = c['reference'].get('laser_power_mw', 0)

    # Use the reference VNA power for verification images
    pv_str = f'{ref_pv:g}dBm'
    pl_str = f'{ref_pl:02.0f}mW'

    t_dir = os.path.join(base, t_str, pv_str, pl_str)
    if not os.path.isdir(t_dir):
        print(f'  SKIP {t_str}: no data dir {pv_str}/{pl_str}')
        continue

    s2p_files = [f for f in os.listdir(t_dir) if f.endswith('.s2p')]
    if not s2p_files:
        continue
    ntwk = rf.Network(os.path.join(t_dir, s2p_files[0]))
    freq_full = ntwk.f / 1e9
    s21_full = 20 * np.log10(np.abs(ntwk.s[:,1,0]))

    for rname, info in c['identified'].items():
        f0_ref = info['f0_ghz']
        window = 0.060  # +/- 60 MHz window

        mask = (freq_full >= f0_ref - window) & (freq_full <= f0_ref + window)
        f_win = freq_full[mask]
        s_win = s21_full[mask]

        if len(f_win) < 10:
            continue

        dip_idx = np.argmin(s_win)
        f0_actual = f_win[dip_idx]
        dip_abs = s_win[dip_idx]
        baseline = np.percentile(s_win, 90)
        dip_depth = baseline - dip_abs

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(f_win, s_win, linewidth=0.8, color='#1a5276')
        ax.axvline(f0_actual, color='#e74c3c', linestyle='--', linewidth=1.2)
        ax.axhline(baseline, color='#7f8c8d', linestyle=':', linewidth=0.8, alpha=0.7)

        status = 'OK' if dip_depth >= 1.0 else ('SHALLOW' if dip_depth >= 0.5 else 'WEAK')
        ax.annotate(
            f'f0 = {f0_actual:.4f} GHz\n|S21| = {dip_abs:.1f} dB\nP90 baseline = {baseline:.1f} dB\ndepth = {dip_depth:.2f} dB  [{status}]',
            xy=(f0_actual, dip_abs), fontsize=10, color='#c0392b',
            xytext=(0.02, 0.98), textcoords='axes fraction',
            va='top', ha='left',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9))

        ax.set_title(f'{t_str}  {rname}  f0={f0_actual:.4f} GHz  dip_depth={dip_depth:.2f} dB  [{status}]  ({pv_str}, {pl_str})', fontsize=12)
        ax.set_xlabel('Frequency (GHz)', fontsize=11)
        ax.set_ylabel('|S21| (dB)', fontsize=11)
        ax.grid(True, alpha=0.25)

        out_path = os.path.join(verify_dir, f'T{t_str}_{rname}_verify.png')
        fig.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f'  [OK] T{t_str}_{rname}_verify.png  f0={f0_actual:.4f}  depth={dip_depth:.2f} dB')

print(f'\n[OK] All verification images regenerated')
