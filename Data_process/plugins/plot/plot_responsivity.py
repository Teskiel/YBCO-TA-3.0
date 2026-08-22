# plugins/plot/plot_responsivity.py — 响应率
"""从 S2P 原始数据计算 df/f vs 激光功率, 线性拟合斜率 = 响应率 vs T。

若 fit_results 中缺少 per-laser-power 数据，则从原始 S2P 按 f0 位置寻 dip 提取。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import matplotlib
import _backend  # noqa: F401
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, load_file_manifest, lookup_actual_temp, resolve_data_med_dir, resolve_output_dir
from _lib.io_s2p import load_s_param
from _lib.scanning import find_s2p
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


def _find_nearest_dip(freq_hz, s21_db, f0_target_hz, search_span_hz=50e6):
    """在 f0_target 附近 ±search_span 内找最深 dip 的频率。

    Returns
    -------
    f0_hz : float or None
    """
    f_ghz = freq_hz / 1e9
    f0_ghz = f0_target_hz / 1e9
    span_ghz = search_span_hz / 1e9
    mask = (f_ghz > f0_ghz - span_ghz) & (f_ghz < f0_ghz + span_ghz)
    if mask.sum() < 5:
        return None
    idx_local = np.argmin(s21_db[mask])
    return float(freq_hz[mask][idx_local])


@plugin(
    phase="plot",
    order=4,
    inputs=["selected_resonances.json", "fit_results.json", "scan_result.json"],
    outputs=[],
    description="df/f vs 激光功率 + 响应率 vs T",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    manifest = load_file_manifest(data_med_dir)
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    if source_data_dir is None:
        print("[plot_responsivity] source_data_dir required for S2P loading, "
              "falling back to f0(T) only")
        source_data_dir = None

    pic_std = config.get("pic_std") if config else None
    formats = _cfg(pic_std, "plot", "responsivity", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "responsivity", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "responsivity", "figsize", default=[10, 7])

    lw_data = _cfg(pic_std, "defaults", "lines", "data", default=2.0)
    ms_data = _cfg(pic_std, "defaults", "markers", "scatter_big", default=9)
    alpha_scatter = _cfg(pic_std, "defaults", "alpha", "data_scatter", default=0.7)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)
    legend_fs = _cfg(pic_std, "defaults", "font", "legend", default=12)
    legend_framealpha = _cfg(pic_std, "defaults", "legend", "framealpha", default=0.8)
    legend_facecolor = _cfg(pic_std, "defaults", "legend", "facecolor", default="white")
    legend_edgecolor = _cfg(pic_std, "defaults", "legend", "edgecolor", default="#999999")
    layout_rect = _cfg(pic_std, "defaults", "layout", "rect", default=[0, 0.06, 1, 1])

    # Intersection-only mode for laser powers
    intersection_enabled = _cfg(pic_std, "plot", "responsivity",
                                "intersection_only", "enabled", default=True)
    # Dashed-line overlay config
    dashed_enabled = _cfg(pic_std, "plot", "responsivity",
                          "dashed", "enabled", default=True)
    dashed_alpha = _cfg(pic_std, "plot", "responsivity",
                        "dashed", "alpha", default=0.4)
    dashed_ls = _cfg(pic_std, "plot", "responsivity",
                     "dashed", "linestyle", default="--")
    dashed_lw = _cfg(pic_std, "plot", "responsivity",
                     "dashed", "linewidth", default=1.0)

    n_resonators = len(fit["by_resonator"])
    colors = _cfg(pic_std, "verification", "resonator_colors",
                  default={"r1": "#1F77B4", "r2": "#D62728", "r3": "#2CA02C",
                           "r4": "#FF7F0E", "r5": "#9467BD"})

    temps = scan["temperatures"]
    laser_powers = scan.get("laser_powers_mw", [0, 1, 3, 5, 7, 9])
    vna_power_dbm = 25  # -25 dBm reference
    structure = scan["structure_type"]
    out_dir = Path(output_dir)

    # Collect responsivity data: {rname: {temp: (laser_powers, df_f_list, slope_ppm_per_mw)}}
    resp_data = {}

    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        rdata = fit["by_resonator"].get(rname)
        if rdata is None:
            continue
        resp_data[rname] = {}

        for temp in temps:
            tdata = rdata["by_temperature"].get(str(temp))
            if tdata is None or not tdata.get("f0_hz"):
                continue
            f0_ref = tdata["f0_hz"]

            # 尝试从 S2P 提取不同激光功率下的 f0
            if source_data_dir:
                source = Path(source_data_dir)
                df_f_list = []
                valid_powers = []
                for lmw in laser_powers:
                    s2p_path = find_s2p(source, temp, -vna_power_dbm, lmw,
                                        structure=structure)
                    if s2p_path is None:
                        continue
                    freq, s21 = load_s_param(str(s2p_path))
                    s21_db = 20 * np.log10(np.abs(s21))
                    f0_dip = _find_nearest_dip(freq, s21_db, f0_ref)
                    if f0_dip is not None:
                        df_f = (f0_dip - f0_ref) / f0_ref
                        df_f_list.append(df_f)
                        valid_powers.append(lmw)

                if len(valid_powers) >= 3:
                    # 线性拟合 df/f vs laser → 斜率 = 响应率
                    pw_arr = np.array(valid_powers)
                    df_arr = np.array(df_f_list)
                    slope, intercept = np.polyfit(pw_arr, df_arr, 1)
                    resp_data[rname][temp] = {
                        "laser_powers": valid_powers,
                        "df_f": df_f_list,
                        "slope_ppm_per_mw": float(slope * 1e6),
                    }
                elif len(valid_powers) >= 2:
                    slope = (df_f_list[-1] - df_f_list[0]) / (valid_powers[-1] - valid_powers[0])
                    resp_data[rname][temp] = {
                        "laser_powers": valid_powers,
                        "df_f": df_f_list,
                        "slope_ppm_per_mw": float(slope * 1e6),
                    }

    # ---- Post-process: intersect laser powers across temperatures ----
    if intersection_enabled:
        for rname, rdata_dict in resp_data.items():
            if len(rdata_dict) < 2:
                continue
            # Compute intersection of valid laser powers across all temperatures
            power_sets = [set(entry["laser_powers"]) for entry in rdata_dict.values()]
            common_powers = sorted(set.intersection(*power_sets))
            if len(common_powers) < 2:
                continue
            # Filter each temperature entry and recompute slope
            for temp, entry in list(rdata_dict.items()):
                pw = entry["laser_powers"]
                df = entry["df_f"]
                # Keep only pairs whose laser power is in the common set
                filtered = [(p, d) for p, d in zip(pw, df) if p in common_powers]
                if len(filtered) < 2:
                    del rdata_dict[temp]
                    continue
                filtered_pw, filtered_df = zip(*filtered)
                pw_arr = np.array(filtered_pw, dtype=float)
                df_arr = np.array(filtered_df, dtype=float)
                if len(pw_arr) >= 3:
                    slope, intercept = np.polyfit(pw_arr, df_arr, 1)
                else:
                    slope = (df_arr[-1] - df_arr[0]) / (pw_arr[-1] - pw_arr[0])
                entry["laser_powers"] = list(filtered_pw)
                entry["df_f"] = list(filtered_df)
                entry["slope_ppm_per_mw"] = float(slope * 1e6)

    # ---- Plot 1: df/f vs laser power (per resonator, all temperatures) ----
    with_line_dir = out_dir / "with_line" if dashed_enabled else None
    if with_line_dir:
        with_line_dir.mkdir(parents=True, exist_ok=True)

    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        if rname not in resp_data or not resp_data[rname]:
            continue

        # --- Points-only version ---
        fig, ax = plt.subplots(figsize=figsize)
        for temp, entry in sorted(resp_data[rname].items()):
            pw = entry["laser_powers"]
            df = np.array(entry["df_f"]) * 1e6  # -> ppm
            actual_t = lookup_actual_temp(manifest, temp)
            legend_label = f"T={temp}K"
            if actual_t is not None:
                legend_label += f" ({actual_t:.2f}K)"
            ax.plot(pw, df, "o", markersize=ms_data,
                    alpha=alpha_scatter, label=legend_label)

        ax.set_xlabel("Laser Power (mW)")
        ax.set_ylabel("df/f (ppm)")
        ax.set_title(f"{rname} - df/f vs Laser Power")
        ax.legend(fontsize=legend_fs - 2, framealpha=legend_framealpha,
                  facecolor=legend_facecolor, edgecolor=legend_edgecolor)
        ax.axhline(0, color="gray", lw=0.5, ls=":")
        ax.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax, pic_std)
        fig.tight_layout(rect=layout_rect)
        for fmt in formats:
            fig.savefig(str(out_dir / f"responsivity_df_f_{rname}.{fmt}"),
                        dpi=dpi, bbox_inches="tight", facecolor="white")
        plt.close(fig)

        # --- Dashed-line version (with_line/) ---
        if dashed_enabled:
            fig, ax = plt.subplots(figsize=figsize)
            for temp, entry in sorted(resp_data[rname].items()):
                pw = entry["laser_powers"]
                df = np.array(entry["df_f"]) * 1e6
                actual_t = lookup_actual_temp(manifest, temp)
                legend_label = f"T={temp}K"
                if actual_t is not None:
                    legend_label += f" ({actual_t:.2f}K)"
                pts = ax.plot(pw, df, "o", markersize=ms_data,
                              alpha=alpha_scatter, label=legend_label)
                ax.plot(pw, df, dashed_ls, lw=dashed_lw,
                        alpha=dashed_alpha, color=pts[0].get_color())

            ax.set_xlabel("Laser Power (mW)")
            ax.set_ylabel("df/f (ppm)")
            ax.set_title(f"{rname} - df/f vs Laser Power")
            ax.legend(fontsize=legend_fs - 2, framealpha=legend_framealpha,
                      facecolor=legend_facecolor, edgecolor=legend_edgecolor)
            ax.axhline(0, color="gray", lw=0.5, ls=":")
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)
            fig.tight_layout(rect=layout_rect)
            for fmt in formats:
                fig.savefig(str(with_line_dir / f"responsivity_df_f_{rname}.{fmt}"),
                            dpi=dpi, bbox_inches="tight", facecolor="white")
            plt.close(fig)

    # ---- Plot 2: Responsivity vs T (all resonators) ----
    fig, ax = plt.subplots(figsize=figsize)
    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        if rname not in resp_data or not resp_data[rname]:
            continue
        color = colors.get(rname.lower(), "#1F77B4")
        t_list = []
        s_list = []
        for temp, entry in sorted(resp_data[rname].items()):
            t_list.append(temp)
            s_list.append(entry["slope_ppm_per_mw"])
        if t_list:
            ax.plot(t_list, s_list, "o", color=color, markersize=ms_data,
                    label=rname)

    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("Responsivity (ppm/mW)")
    ax.set_title("Responsivity vs Temperature")
    ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
              facecolor=legend_facecolor, edgecolor=legend_edgecolor)
    ax.axhline(0, color="gray", lw=0.5, ls=":")
    ax.grid(True, alpha=alpha_grid)
    apply_axis_ticks(ax, pic_std)
    fig.tight_layout(rect=layout_rect)
    for fmt in formats:
        fig.savefig(str(out_dir / f"responsivity_vs_T.{fmt}"),
                    dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # --- Dashed-line version (with_line/) ---
    if dashed_enabled:
        fig, ax = plt.subplots(figsize=figsize)
        for ri in range(n_resonators):
            rname = f"R{ri+1}"
            if rname not in resp_data or not resp_data[rname]:
                continue
            color = colors.get(rname.lower(), "#1F77B4")
            t_list = []
            s_list = []
            for temp, entry in sorted(resp_data[rname].items()):
                t_list.append(temp)
                s_list.append(entry["slope_ppm_per_mw"])
            if t_list:
                ax.plot(t_list, s_list, "o", color=color, markersize=ms_data,
                        label=rname)
                ax.plot(t_list, s_list, dashed_ls, color=color,
                        lw=dashed_lw, alpha=dashed_alpha)

        ax.set_xlabel("Temperature (K)")
        ax.set_ylabel("Responsivity (ppm/mW)")
        ax.set_title("Responsivity vs Temperature")
        ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
                  facecolor=legend_facecolor, edgecolor=legend_edgecolor)
        ax.axhline(0, color="gray", lw=0.5, ls=":")
        ax.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax, pic_std)
        fig.tight_layout(rect=layout_rect)
        for fmt in formats:
            fig.savefig(str(with_line_dir / f"responsivity_vs_T.{fmt}"),
                        dpi=dpi, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    # ---- Plot 3: f0 vs T (保留原有输出) ----
    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        rdata = fit["by_resonator"].get(rname)
        if rdata is None:
            continue

        temps_list = []
        f0_list = []
        for temp_k_str, tdata in rdata["by_temperature"].items():
            if tdata.get("f0_hz"):
                temps_list.append(float(temp_k_str))
                f0_list.append(tdata["f0_hz"])

        if len(temps_list) < 2:
            continue

        fig, ax = plt.subplots(figsize=figsize)
        color = colors.get(rname.lower(), "#1F77B4")
        f0_ghz = [f / 1e9 for f in f0_list]
        ax.plot(temps_list, f0_ghz, "o", color=color, markersize=ms_data,
                label=rname)
        ax.set_xlabel("Temperature (K)")
        ax.set_ylabel("f$_0$ (GHz)")
        ax.set_title(f"{rname} - f$_0$(T)")
        ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
                  facecolor=legend_facecolor, edgecolor=legend_edgecolor)
        ax.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax, pic_std)
        fig.tight_layout(rect=layout_rect)
        for fmt in formats:
            fig.savefig(str(out_dir / f"responsivity_f0_vs_T_{rname}.{fmt}"),
                        dpi=dpi, bbox_inches="tight", facecolor="white")
        plt.close(fig)

        # --- Dashed-line version (with_line/) ---
        if dashed_enabled:
            fig, ax = plt.subplots(figsize=figsize)
            ax.plot(temps_list, f0_ghz, "o", color=color, markersize=ms_data,
                    label=rname)
            ax.plot(temps_list, f0_ghz, dashed_ls, color=color,
                    lw=dashed_lw, alpha=dashed_alpha)
            ax.set_xlabel("Temperature (K)")
            ax.set_ylabel("f$_0$ (GHz)")
            ax.set_title(f"{rname} - f$_0$(T)")
            ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
                      facecolor=legend_facecolor, edgecolor=legend_edgecolor)
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)
            fig.tight_layout(rect=layout_rect)
            for fmt in formats:
                fig.savefig(str(with_line_dir / f"responsivity_f0_vs_T_{rname}.{fmt}"),
                            dpi=dpi, bbox_inches="tight", facecolor="white")
            plt.close(fig)

    n_computed = len(resp_data)
    print(f"[plot_responsivity] {n_computed} 共振器响应率计算完成 "
          f"({n_resonators} total)")

    if source_data_dir is None:
        print("[plot_responsivity] 提示: 提供 --source-data-dir 以计算 df/f vs laser")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "responsivity")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir), str(source))
