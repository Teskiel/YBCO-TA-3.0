# plugins/plot/plot_Qi_vs_T.py — Qi, Qc 品质因子 vs T
"""从 fit_results.json 生成 Qi(T), Qc(T) 品质因子图: semilogy, 每共振器单独图 + 汇总图。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import matplotlib
import _backend  # noqa: F401
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, NullFormatter, LogLocator
import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, resolve_data_med_dir, resolve_output_dir
from _lib.smoothing import smooth_spline, linear_fit_robust
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


@plugin(
    phase="plot",
    order=2,
    inputs=["selected_resonances.json", "fit_results.json", "scan_result.json"],
    outputs=[],
    description="Qi(T), Qc(T) 品质因子温度演化 — semilogy 图",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    pic_std = config.get("pic_std") if config else None
    formats = _cfg(pic_std, "plot", "Qi_vs_T", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "Qi_vs_T", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "Qi_vs_T", "figsize", default=[10, 7])

    # 视觉样式 — 从 defaults 读取
    lw_data = _cfg(pic_std, "defaults", "lines", "data", default=2.0)
    lw_fit = _cfg(pic_std, "defaults", "lines", "fit", default=1.0)
    ms_data = _cfg(pic_std, "defaults", "markers", "scatter_big", default=9)
    ms_fit = _cfg(pic_std, "defaults", "markers", "scatter_small", default=6)
    alpha_fit = _cfg(pic_std, "defaults", "alpha", "fit_line", default=0.5)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)
    legend_fs = _cfg(pic_std, "defaults", "font", "legend", default=12)
    legend_framealpha = _cfg(pic_std, "defaults", "legend", "framealpha", default=0.8)
    legend_facecolor = _cfg(pic_std, "defaults", "legend", "facecolor", default="white")
    legend_edgecolor = _cfg(pic_std, "defaults", "legend", "edgecolor", default="#999999")
    legend_title = _cfg(pic_std, "plot", "Qi_vs_T", "legend", "title", default=None)
    smoothing_cfg = _cfg(pic_std, "plot", "Qi_vs_T", "smoothing", default={"enabled": False})
    layout_rect = _cfg(pic_std, "defaults", "layout", "rect", default=[0, 0.06, 1, 1])

    # semilogy y 轴主刻度 subs — 控制标签密度
    log_subs = _cfg(pic_std, "plot", "Qi_vs_T", "log_ticks", "subs",
                    default=[1.0, 2.0, 5.0])

    n_resonators = len(fit["by_resonator"])
    colors = _cfg(pic_std, "verification", "resonator_colors",
                  default={"r1": "#1F77B4", "r2": "#D62728", "r3": "#2CA02C", "r4": "#FF7F0E", "r5": "#9467BD"})

    fit_type = smoothing_cfg.get("fit_type", "linear")

    out_dir = Path(output_dir)
    # 带 Qc 虚线的图放入 -with_line 子文件夹
    with_line_dir = out_dir.parent / (out_dir.name + "-with_line")
    with_line_dir.mkdir(parents=True, exist_ok=True)

    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        rdata = fit["by_resonator"].get(rname)
        if rdata is None:
            continue

        temps_list = []
        qi_list = []
        qc_list = []
        for temp_k_str, tdata in rdata["by_temperature"].items():
            if tdata.get("qi") and tdata.get("qc"):
                temps_list.append(float(temp_k_str))
                qi_list.append(tdata["qi"])
                qc_list.append(tdata["qc"])

        if not temps_list:
            continue

        t_arr = np.array(temps_list)
        qi_arr = np.array(qi_list)
        color = colors.get(rname.lower(), "#1F77B4")

        # ================================================================
        # 图 1: Qi + Qc (带虚线) → -with_line 子文件夹
        # ================================================================
        fig, ax = plt.subplots(figsize=figsize)

        # Qi: 根据 fit_type 选择拟合方式
        if smoothing_cfg.get("enabled"):
            if fit_type == "linear":
                slope, intercept, kept, fit_x = linear_fit_robust(
                    t_arr, qi_arr,
                    outlier_mad=smoothing_cfg.get("outlier_mad", 5),
                    n_iterations=smoothing_cfg.get("n_iterations", 3),
                )
                if slope is not None and kept is not None:
                    fit_y = slope * fit_x + intercept
                    ax.semilogy(fit_x, fit_y, "-", color=color, lw=lw_data,
                                label=f"{rname} Qi (linear fit)")
                    # 保留点（线性区域）
                    ax.semilogy(t_arr[kept], qi_arr[kept], "o", color=color,
                                markersize=ms_data, alpha=0.7)
                    # 剔除点（非线性区域）— 空心
                    rejected = ~kept
                    if rejected.any():
                        ax.semilogy(t_arr[rejected], qi_arr[rejected], "o",
                                    color=color, markersize=ms_data,
                                    alpha=0.35, markerfacecolor="none")
                else:
                    ax.semilogy(t_arr, qi_arr, "o-", color=color, lw=lw_data,
                                markersize=ms_data, label=f"{rname} Qi")
            else:
                # spline 模式（保留原行为）
                k = smoothing_cfg.get("k", 3)
                mad = smoothing_cfg.get("outlier_mad", 5)
                n_pts = smoothing_cfg.get("n_points", 200)
                xs, ys, kept = smooth_spline(t_arr, qi_arr, k=k, outlier_mad=mad,
                                             n_points=n_pts)
                if xs is not None and kept is not None:
                    ax.semilogy(xs, ys, "-", color=color, lw=lw_data,
                                label=f"{rname} Qi")
                    ax.semilogy(t_arr[kept], qi_arr[kept], "o", color=color,
                                markersize=ms_data, alpha=0.7)
                else:
                    ax.semilogy(t_arr, qi_arr, "o-", color=color, lw=lw_data,
                                markersize=ms_data, label=f"{rname} Qi")
        else:
            ax.semilogy(t_arr, qi_arr, "o-", color=color, lw=lw_data,
                        markersize=ms_data, label=f"{rname} Qi")

        # Qc: 虚线 (不平滑)
        ax.semilogy(temps_list, qc_list, "s--", color=color, lw=lw_fit,
                    markersize=ms_fit, alpha=alpha_fit, label=f"{rname} Qc")
        ax.set_xlabel("Temperature (K)")
        ax.set_ylabel("Quality Factor")
        ax.set_title(f"{rname} — Qi, Qc vs T")
        legend_kw = dict(fontsize=legend_fs, framealpha=legend_framealpha,
                         facecolor=legend_facecolor, edgecolor=legend_edgecolor)
        if legend_title:
            legend_kw["title"] = legend_title
        ax.legend(**legend_kw)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{x:.0f}'))
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=log_subs))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax, pic_std, axis='x')
        fig.tight_layout(rect=layout_rect)

        for fmt in formats:
            fig.savefig(str(with_line_dir / f"Qi_vs_T_{rname}.{fmt}"), dpi=dpi,
                        bbox_inches="tight", facecolor="white")
        plt.close(fig)

        # ================================================================
        # 图 2: Qi only (散点，无拟合线，无 Qc) → 主文件夹
        # ================================================================
        fig2, ax2 = plt.subplots(figsize=figsize)
        ax2.semilogy(t_arr, qi_arr, "o", color=color, markersize=ms_data,
                     alpha=0.7, label=f"{rname} Qi")
        ax2.set_xlabel("Temperature (K)")
        ax2.set_ylabel("Qi")
        ax2.set_title(f"{rname} — Qi vs T")
        legend_kw2 = dict(fontsize=legend_fs, framealpha=legend_framealpha,
                          facecolor=legend_facecolor, edgecolor=legend_edgecolor)
        if legend_title:
            legend_kw2["title"] = legend_title
        ax2.legend(**legend_kw2)
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, p: f'{x:.0f}'))
        ax2.yaxis.set_major_locator(LogLocator(base=10, subs=log_subs))
        ax2.yaxis.set_minor_formatter(NullFormatter())
        ax2.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax2, pic_std, axis='x')
        fig2.tight_layout(rect=layout_rect)

        for fmt in formats:
            fig2.savefig(str(out_dir / f"Qi_only_vs_T_{rname}.{fmt}"), dpi=dpi,
                         bbox_inches="tight", facecolor="white")
        plt.close(fig2)

    print(f"[plot_Qi_vs_T] {n_resonators} 共振器 Qi/Qc(T) 图已生成 "
          f"(Qi+Qc → {with_line_dir.name}/, Qi-only → {out_dir.name}/)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "Qi_vs_T")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir))
