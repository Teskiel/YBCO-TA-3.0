# plugins/plot/plot_f0_vs_T.py — f0(T) 温度响应
"""从 fit_results.json 生成 f0 温度响应图: 每共振器一条曲线。

支持三次样条平滑 + MAD 离群值剔除 (通过 pic_std 配置控制)。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import matplotlib
import _backend  # noqa: F401 — Qt5Agg -> Agg fallback
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, resolve_data_med_dir, resolve_output_dir
from _lib.smoothing import smooth_spline
from _lib.caption import add_caption
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


def _draw_curve(ax, temps, f0_ghz, color, label, lw_data, ms_data, alpha_scatter,
                alpha_line, smoothing_cfg):
    """根据 smoothing 配置画曲线: 平滑样条 + 保留散点, 或简单折线。

    Returns
    -------
    initial_f0 : float or None
        最低温度下的 f0 值 (用于图例标签)
    """
    initial_f0 = None
    if not temps or not f0_ghz:
        return initial_f0

    t_arr = np.array(temps)
    f_arr = np.array(f0_ghz)
    mask_valid = ~np.isnan(f_arr)
    t_valid = t_arr[mask_valid]
    f_valid = f_arr[mask_valid]

    if len(t_valid) == 0:
        return initial_f0

    initial_f0 = float(f_valid[np.argmin(t_valid)])

    drew = False
    if smoothing_cfg and smoothing_cfg.get("enabled"):
        k = smoothing_cfg.get("k", 3)
        mad = smoothing_cfg.get("outlier_mad", 5)
        n_pts = smoothing_cfg.get("n_points", 200)
        xs, ys, kept = smooth_spline(t_valid, f_valid, k=k, outlier_mad=mad,
                                     n_points=n_pts)
        if xs is not None and kept is not None:
            ax.plot(xs, ys, "--", linewidth=lw_data, color=color,
                    alpha=alpha_line, label=label)
            ax.plot(t_valid[kept], f_valid[kept], "o", markersize=ms_data,
                    color=color, alpha=alpha_scatter)
            drew = True

    if not drew:
        # 回退: 虚线连接 + 散点标记 (各自独立 alpha)
        ax.plot(t_valid, f_valid, "--", color=color, lw=lw_data,
                alpha=alpha_line)
        ax.plot(t_valid, f_valid, "o", color=color, markersize=ms_data,
                label=label, alpha=alpha_scatter)
    return initial_f0


@plugin(
    phase="plot",
    order=1,
    inputs=["selected_resonances.json", "fit_results.json", "scan_result.json"],
    outputs=[],
    description="f0(T) 温度响应 — 每共振器一条曲线",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    pic_std = config.get("pic_std") if config else None

    # 图片格式
    formats = _cfg(pic_std, "plot", "f0_vs_T", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "f0_vs_T", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "f0_vs_T", "figsize", default=[10, 7])

    # 平滑配置
    smoothing_cfg = _cfg(pic_std, "plot", "f0_vs_T", "smoothing", default={"enabled": False})

    # 视觉样式 — 从 defaults 读取
    lw_data = _cfg(pic_std, "defaults", "lines", "data", default=2.0)
    ms_data = _cfg(pic_std, "defaults", "markers", "scatter_big", default=9)
    alpha_scatter = _cfg(pic_std, "defaults", "alpha", "data_scatter", default=0.7)
    alpha_line = _cfg(pic_std, "defaults", "alpha", "data_line", default=0.5)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)
    legend_fs = _cfg(pic_std, "defaults", "font", "legend", default=12)
    legend_framealpha = _cfg(pic_std, "defaults", "legend", "framealpha", default=0.8)
    legend_facecolor = _cfg(pic_std, "defaults", "legend", "facecolor", default="white")
    legend_edgecolor = _cfg(pic_std, "defaults", "legend", "edgecolor", default="#999999")
    caption_fs = _cfg(pic_std, "defaults", "layout", "caption_fontsize", default=9)
    layout_rect = _cfg(pic_std, "defaults", "layout", "rect", default=[0, 0.06, 1, 1])

    n_resonators = len(fit["by_resonator"])
    colors = _cfg(pic_std, "verification", "resonator_colors",
                  default={"r1": "#1F77B4", "r2": "#D62728", "r3": "#2CA02C",
                           "r4": "#FF7F0E", "r5": "#9467BD"})

    # 双模式输出: 平滑样条 → 主文件夹, 原始连线 → raw/ 子文件夹
    out_dir = Path(output_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    modes = [
        (out_dir, smoothing_cfg, "smooth"),
        (raw_dir, None, "raw"),
    ]

    for save_dir, sm_cfg, mode_label in modes:
        # 每共振器单独图
        for ri in range(n_resonators):
            rname = f"R{ri+1}"
            rdata = fit["by_resonator"].get(rname)
            if rdata is None:
                continue

            fig, ax = plt.subplots(figsize=figsize)
            f0_ghz = []
            temps_used = []
            for temp_k_str, tdata in sorted(rdata["by_temperature"].items(),
                                             key=lambda kv: int(kv[0])):
                if tdata.get("f0_hz"):
                    f0_ghz.append(tdata["f0_hz"] / 1e9)
                    temps_used.append(float(temp_k_str))

            color = colors.get(rname.lower(), "#1F77B4")
            _draw_curve(ax, temps_used, f0_ghz, color, rname, lw_data, ms_data,
                        alpha_scatter, alpha_line, sm_cfg)
            ax.set_xlabel("Temperature (K)")
            ax.set_ylabel("f$_0$ (GHz)")
            ax.set_title(f"{rname} — f$_0$(T)")
            ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
                      facecolor=legend_facecolor, edgecolor=legend_edgecolor)
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)
            fig.tight_layout(rect=layout_rect)

            for fmt in formats:
                fpath = save_dir / f"f0_vs_T_{rname}.{fmt}"
                fig.savefig(str(fpath), dpi=dpi, bbox_inches="tight",
                            facecolor="white")
            plt.close(fig)

        # 汇总图: 所有共振器叠加
        fig, ax = plt.subplots(figsize=figsize)
        n_legend = 0
        for ri in range(n_resonators):
            rname = f"R{ri+1}"
            rdata = fit["by_resonator"].get(rname)
            if rdata is None:
                continue
            f0_ghz = []
            temps_used = []
            for temp_k_str, tdata in sorted(rdata["by_temperature"].items(),
                                             key=lambda kv: int(kv[0])):
                if tdata.get("f0_hz"):
                    f0_ghz.append(tdata["f0_hz"] / 1e9)
                    temps_used.append(float(temp_k_str))
            color = colors.get(rname.lower(), "#1F77B4")
            _draw_curve(ax, temps_used, f0_ghz, color,
                        f"{rname}: {f0_ghz[0]:.3f} GHz" if f0_ghz else rname,
                        lw_data, ms_data, alpha_scatter, alpha_line, sm_cfg)
            n_legend += 1

        ax.set_xlabel("Temperature (K)")
        ax.set_ylabel("f$_0$ (GHz)")
        ax.set_title("All Resonators — f$_0$(T) (−25 dBm, 0 mW)")

        # 图例: >4 条 → 图外
        if n_legend > 4:
            ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
                      facecolor=legend_facecolor, edgecolor=legend_edgecolor,
                      loc="upper left", bbox_to_anchor=(1.02, 1))
        else:
            ax.legend(fontsize=legend_fs, framealpha=legend_framealpha,
                      facecolor=legend_facecolor, edgecolor=legend_edgecolor)

        ax.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax, pic_std)
        fig.tight_layout(rect=layout_rect)
        add_caption(fig, "Fig. Resonant frequency vs temperature for all five "
                    "resonators (−25 dBm, 0 mW). Dashed: cubic spline with "
                    "outlier rejection; markers: retained data points.",
                    fontsize=caption_fs)
        for fmt in formats:
            fig.savefig(str(save_dir / f"f0_vs_T_all.{fmt}"), dpi=dpi,
                        bbox_inches="tight", facecolor="white")
        plt.close(fig)

    print(f"[plot_f0_vs_T] {n_resonators} 共振器 f0(T) 图已生成 "
          f"(smooth -> {out_dir}, raw -> {raw_dir})")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "f0_vs_T")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir))
