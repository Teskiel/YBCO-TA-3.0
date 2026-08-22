# plugins/plot/plot_verification.py — 全频谱验证
"""从 detected_peaks + fit_results 读取谐振频率, 加载原始 S2P 全频谱, 叠加 f0 竖线标注。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import matplotlib
import _backend  # noqa: F401
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, resolve_data_med_dir, resolve_output_dir
from _lib.io_s2p import load_s_param
from _lib.scanning import find_s2p
from _pic_std import load_pic_std, _cfg


@plugin(
    phase="plot",
    order=6,
    inputs=["fit_results.json", "detected_peaks.json", "scan_result.json"],
    outputs=[],
    description="全频谱验证 + 5 共振器 f0 标注",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    detected = read_json(data_med_dir, "detected_peaks.json")
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    if source_data_dir is None:
        print("[plot_verification] source_data_dir required for S2P loading, skipping")
        return

    pic_std = config.get("pic_std") if config else None
    formats = _cfg(pic_std, "plot", "verification", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "verification", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "verification", "figsize", default=[12, 9])
    colors = _cfg(pic_std, "verification", "resonator_colors",
                  default={"R1": "#1F77B4", "R2": "#D62728", "R3": "#2CA02C", "R4": "#FF7F0E", "R5": "#9467BD"})
    phase_color = _cfg(pic_std, "verification", "phase_diff", "color", default="#8E44AD")
    phase_lw = _cfg(pic_std, "verification", "phase_diff", "linewidth", default=0.8)
    phase_alpha = _cfg(pic_std, "verification", "phase_diff", "alpha", default=0.8)
    phase_ylabel = _cfg(pic_std, "verification", "phase_diff", "ylabel", default="diff(unwrapped phase) (rad/sample)")
    label_fontsize = _cfg(pic_std, "verification", "confirmed_f0_label", "fontsize", default=9)
    bbox_cfg = _cfg(pic_std, "verification", "confirmed_f0_label", "bbox",
                    default={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "lightgray", "alpha": 0.85})
    vline_cfg = _cfg(pic_std, "verification", "confirmed_f0_line", default={})

    source = Path(source_data_dir)
    structure = scan["structure_type"]
    temps = scan["temperatures"]
    n_resonators = len(fit["by_resonator"])
    out_dir = Path(output_dir)

    for temp in temps:
        ref_path = find_s2p(source, temp, -25, 0, structure=structure)
        if ref_path is None:
            for pwr in [30, 35, 45, 55]:
                ref_path = find_s2p(source, temp, -pwr, 0, structure=structure)
                if ref_path:
                    break
        if ref_path is None:
            print(f"  [SKIP] T={temp}K: 无 S2P 文件")
            continue

        freq, s21 = load_s_param(str(ref_path))
        transmission = 20 * np.log10(np.abs(s21))
        phase_diff = np.diff(np.unwrap(np.angle(s21)))
        freq_ghz = freq / 1e9
        freq_phase_ghz = freq[1:] / 1e9

        fig, (ax_mag, ax_phase) = plt.subplots(2, 1, sharex=True, figsize=figsize)

        # --- upper panel: |S21| magnitude ---
        ax_mag.plot(freq_ghz, transmission, "b-", lw=0.5, label="|S21|")

        # --- lower panel: diff(unwrapped phase) ---
        ax_phase.plot(freq_phase_ghz, phase_diff, color=phase_color, lw=phase_lw, alpha=phase_alpha)

        # Mark fitted f0 for each resonator
        # Collect f0 info first for proximity-aware stagger
        f0_info = []
        for ri in range(n_resonators):
            rname = f"R{ri+1}"
            rdata = fit["by_resonator"].get(rname)
            if rdata is None:
                continue
            tdata = rdata["by_temperature"].get(str(temp))
            if tdata and tdata.get("f0_hz"):
                f0_ghz = tdata["f0_hz"] / 1e9
                color = colors.get(rname, "red")
                f0_info.append((ri, rname, f0_ghz, color))

        # Stagger rows: odd-index → upper row (y=0.22), even-index → lower row (y=0.10)
        # Proximity check: if two adjacent f0 within 0.05 GHz share the same row, force-split
        rows = {}
        for i in range(len(f0_info)):
            ri = f0_info[i][0]
            row = 1 if ri % 2 == 1 else 0  # 1=upper(0.22), 0=lower(0.10)
            # Check proximity to previous resonator
            if i > 0:
                prev_ri, prev_rname, prev_f0, _ = f0_info[i - 1]
                if abs(f0_info[i][2] - prev_f0) < 0.05:
                    prev_row = rows.get(prev_ri, 0)
                    row = 1 - prev_row  # force opposite row
            rows[ri] = row

        # Micro-jitter per index to avoid exact overlap
        jitter = {r: (i % 3 - 1) * 0.02 for i, (r, _, _, _) in enumerate(f0_info)}

        for ri, rname, f0_ghz, color in f0_info:
            # axvline on both panels (style from config, color per resonator)
            vline_ls = vline_cfg.get("linestyle", "--")
            vline_lw = vline_cfg.get("linewidth", 0.6)
            vline_alpha = vline_cfg.get("alpha", 0.5)
            ax_mag.axvline(f0_ghz, color=color, ls=vline_ls, lw=vline_lw,
                           alpha=vline_alpha, label=f"{rname} f0")
            ax_phase.axvline(f0_ghz, color=color, ls=vline_ls, lw=vline_lw,
                             alpha=vline_alpha)

            # annotation box — offset left/right of the dashed line, below waveform
            base_y = 0.24 if rows[ri] == 1 else 0.05
            y_frac = base_y + jitter.get(ri, 0)
            # per-resonator horizontal offset: R1/R3/R4 → left, R2/R5 → right
            _left = {"R1", "R3", "R4"}
            h_offset = -35 if rname in _left else 35
            ann_bbox = dict(bbox_cfg)
            ann_bbox["edgecolor"] = color
            ax_mag.annotate(
                f"{rname}\n{f0_ghz:.3f} GHz",
                xy=(f0_ghz, y_frac),
                xycoords=("data", "axes fraction"),
                xytext=(h_offset, 0),
                textcoords="offset points",
                fontsize=label_fontsize,
                color=color,
                ha="center",
                va="bottom",
                bbox=ann_bbox,
            )

        ax_mag.set_ylabel("|S21| (dB)")
        ax_phase.set_xlabel("Frequency (GHz)")
        ax_phase.set_ylabel(phase_ylabel)
        ax_mag.set_title(f"Full Spectrum Verification @ T={temp}K")
        ax_mag.legend(loc="upper right", fontsize=8)
        ax_mag.grid(True, alpha=0.3)
        ax_phase.grid(True, alpha=0.3)
        plt.tight_layout()
        for fmt in formats:
            fig.savefig(str(out_dir / f"verification_T{temp}K.{fmt}"), dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    print(f"[plot_verification] {len(temps)} 温度点验证图已生成")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "verification")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir), str(source))
