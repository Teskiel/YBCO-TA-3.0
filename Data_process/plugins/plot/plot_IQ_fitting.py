# plugins/plot/plot_IQ_fitting.py — IQ 平面拟合散点
"""从 traces.npz 读取复 S21, 画 IQ 散点, 每温度 x 每共振器子图。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import matplotlib
import _backend  # noqa: F401
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, read_npz, load_file_manifest, lookup_actual_temp, resolve_data_med_dir, resolve_output_dir
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


def _build_fit_line_from_params(fit_results, rname, temp, n_pts=200):
    """用拟合参数重建理想 hanger 模型 IQ 曲线 (回退用)。"""
    try:
        params = fit_results["by_resonator"][rname]["by_temperature"][str(temp)]
    except (KeyError, TypeError):
        return None
    f0 = params.get("f0_hz")
    qi = params.get("qi")
    qc = params.get("qc")
    if not all([f0, qi, qc]):
        return None
    ql = qi * qc / (qi + qc)
    span = fit_results.get("fit_span_hz", 50e6)
    f = np.linspace(f0 - span / 2, f0 + span / 2, n_pts)
    x = (f - f0) / f0
    s21 = 1.0 - (ql / qc) / (1.0 + 2j * ql * x)
    return np.column_stack([np.real(s21), np.imag(s21)])


@plugin(
    phase="plot",
    order=5,
    inputs=["selected_resonances.json", "fit_results.json", "traces.npz", "scan_result.json"],
    outputs=[],
    description="IQ 平面拟合散点 — 每温度 x 每共振器",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    manifest = load_file_manifest(data_med_dir)
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    pic_std = config.get("pic_std") if config else None
    formats = _cfg(pic_std, "plot", "IQ_fitting", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "IQ_fitting", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "IQ_fitting", "figsize", default=[8, 8])
    scatter_size = _cfg(pic_std, "plot", "IQ_fitting", "scatter_size", default=10)
    scatter_color = _cfg(pic_std, "special_colors", "f0_marker", default="#D32F2F")

    # 拟合曲线样式
    fit_lw = _cfg(pic_std, "plot", "IQ_fitting", "fit_linewidth", default=1.5)
    fit_color = _cfg(pic_std, "special_colors", "zero_line", default="gray")

    # 视觉样式 — 从 defaults 读取
    alpha_scatter = _cfg(pic_std, "defaults", "alpha", "data_scatter", default=0.7)
    alpha_fit = _cfg(pic_std, "defaults", "alpha", "fit_line", default=0.5)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)
    font_legend = _cfg(pic_std, "defaults", "font", "legend", default=12)

    n_resonators = len(fit["by_resonator"])
    npz_path = Path(data_med_dir) / "traces.npz"
    if not npz_path.exists():
        print("[plot_IQ_fitting] traces.npz not found, skipping")
        return

    temps = scan["temperatures"]
    out_dir = Path(output_dir)

    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        for temp in temps:
            # 优先尝试归一化数据 (散点 + 拟合线), 回退到 raw S21 散点
            try:
                iq_data = read_npz(npz_path, f"{rname}/T{temp}/iq_norm")
                iq_fit = read_npz(npz_path, f"{rname}/T{temp}/iq_fit_norm")
                use_norm = True
            except (KeyError, FileNotFoundError):
                try:
                    s21_cplx = read_npz(npz_path, f"{rname}/T{temp}/s21_complex")
                    i_data = s21_cplx[:, 0]
                    q_data = s21_cplx[:, 1]
                    use_norm = False
                except (KeyError, FileNotFoundError):
                    continue

            fig, ax = plt.subplots(figsize=figsize)

            if use_norm:
                ax.scatter(iq_data[:, 0], iq_data[:, 1], s=scatter_size,
                           c=scatter_color, alpha=alpha_scatter,
                           edgecolors='none', zorder=3, label="Data")
                ax.plot(iq_fit[:, 0], iq_fit[:, 1], '-', color=fit_color,
                        linewidth=fit_lw, alpha=alpha_fit, zorder=4,
                        label="Fit")
                ax.set_xlabel("I (normalized)")
                ax.set_ylabel("Q (normalized)")
                ax.legend(fontsize=font_legend)
            else:
                ax.scatter(i_data, q_data, s=scatter_size, c=scatter_color,
                           alpha=alpha_scatter, edgecolors='none', zorder=3)
                # 尝试从拟合参数重建理想 hanger 模型曲线
                try:
                    fit_line = _build_fit_line_from_params(
                        fit, rname, temp, n_pts=len(i_data))
                    if fit_line is not None:
                        ax.plot(fit_line[:, 0], fit_line[:, 1], '-',
                                color=fit_color, linewidth=fit_lw,
                                alpha=alpha_fit, zorder=4, label="Fit (model)")
                        ax.legend(fontsize=font_legend)
                except Exception:
                    pass
                ax.set_xlabel("I (Real)")
                ax.set_ylabel("Q (Imag)")

            actual_t = lookup_actual_temp(manifest, temp)
            title = f"{rname} — IQ Plane @ T={temp}K"
            if actual_t is not None:
                title += f" (actual {actual_t:.2f}K)"
            ax.set_title(title)
            ax.set_aspect("equal")
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)
            plt.tight_layout()
            for fmt in formats:
                fig.savefig(str(out_dir / f"IQ_{rname}_T{temp}K.{fmt}"), dpi=dpi, bbox_inches="tight")
            plt.close(fig)

    print(f"[plot_IQ_fitting] IQ 散点图已生成")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "IQ_fitting")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir))
