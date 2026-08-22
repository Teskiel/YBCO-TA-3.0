# plugins/plot/plot_S21_waterfall.py — |S21| 瀑布图
"""从 traces.npz 读取 S21 数组, 每温度 x 每 VNA 功率生成瀑布子图。

自适应频率窗口: 低温谐振窄 -> 小窗口, 高温谐振宽 -> 大窗口。
参考 otherwise/plot_power_dependence_v2.py:_zoom_mhz()
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
from _lib.io_med import read_json, read_npz, load_file_manifest, lookup_actual_temp, resolve_data_med_dir, resolve_output_dir
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


def _adaptive_span_mhz(temp_k: float, t_low=20.0, span_low=10.0, t_high=77.0, span_high=30.0) -> float:
    """温度自适应频率窗口 (单侧半宽, MHz).

    T ≤ t_low  → span_low MHz  (低温谐振极窄)
    T ≥ t_high → span_high MHz (高温谐振展宽)
    中间线性渐变。
    """
    if temp_k <= t_low:
        return span_low
    if temp_k >= t_high:
        return span_high
    return span_low + (temp_k - t_low) / (t_high - t_low) * (span_high - span_low)


@plugin(
    phase="plot",
    order=3,
    inputs=["selected_resonances.json", "fit_results.json", "traces.npz", "scan_result.json"],
    outputs=[],
    description="|S21| 瀑布图 — 频率 x 温度 x 功率",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    manifest = load_file_manifest(data_med_dir)
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    pic_std = config.get("pic_std") if config else None
    formats = _cfg(pic_std, "plot", "S21_waterfall", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "S21_waterfall", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "S21_waterfall", "figsize", default=[12, 8])
    cmap_name = _cfg(pic_std, "plot", "S21_waterfall", "colormap", default="jet")
    cmap = plt.get_cmap(cmap_name)

    # 自适应频率窗口参数
    az_cfg = _cfg(pic_std, "plot", "S21_waterfall", "adaptive_zoom", default={})
    az_enabled = az_cfg.get("enabled", True)
    az_t_low = az_cfg.get("t_low_k", 20)
    az_span_low = az_cfg.get("span_low_mhz", 10)
    az_t_high = az_cfg.get("t_high_k", 77)
    az_span_high = az_cfg.get("span_high_mhz", 30)

    # 视觉样式 — 从 defaults 读取
    lw_waterfall = _cfg(pic_std, "defaults", "lines", "data", default=2.0)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)

    n_resonators = len(fit["by_resonator"])
    npz_path = Path(data_med_dir) / "traces.npz"
    if not npz_path.exists():
        print("[plot_S21_waterfall] traces.npz not found, skipping")
        return

    temps = scan["temperatures"]

    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        water_data = []
        for temp in temps:
            trace_key = f"{rname}/T{temp}/s21_db"
            try:
                s21_db = read_npz(npz_path, trace_key)
                freq_key = f"{rname}/T{temp}/freq_hz"
                freq_hz = read_npz(npz_path, freq_key)
                water_data.append((temp, freq_hz, s21_db))
            except (KeyError, FileNotFoundError):
                continue

        if not water_data:
            continue

        n_cols = min(3, len(water_data))
        n_rows = (len(water_data) + n_cols - 1) // n_cols
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(figsize[0] * n_cols / 3, figsize[1] * n_rows / 3))
        if n_rows * n_cols == 1:
            axes = np.array([[axes]])
        elif n_rows == 1:
            axes = axes.reshape(1, -1)
        elif n_cols == 1:
            axes = axes.reshape(-1, 1)

        for idx, (temp, freq_hz, s21_db) in enumerate(water_data):
            row, col = idx // n_cols, idx % n_cols
            ax = axes[row, col]
            # 按温度在 colormap 中取色
            color = cmap(idx / max(len(water_data) - 1, 1))

            # 自适应频率窗口: 根据温度缩放 x 轴范围
            freq_plot = freq_hz
            s21_plot = s21_db
            if az_enabled:
                try:
                    f0_hz = fit["by_resonator"][rname]["by_temperature"][str(temp)]["f0_hz"]
                    span_mhz = _adaptive_span_mhz(temp, az_t_low, az_span_low, az_t_high, az_span_high)
                    lo = f0_hz - span_mhz * 1e6
                    hi = f0_hz + span_mhz * 1e6
                    mask = (freq_hz >= lo) & (freq_hz <= hi)
                    if mask.any():
                        freq_plot = freq_hz[mask]
                        s21_plot = s21_db[mask]
                except (KeyError, TypeError):
                    pass  # f0 缺失时回退到全窗口

            ax.plot(freq_plot / 1e9, s21_plot, color=color, lw=lw_waterfall)
            actual_t = lookup_actual_temp(manifest, temp)
            title = f"T={temp}K"
            if actual_t is not None:
                title += f" (actual {actual_t:.2f}K)"
            ax.set_title(title)
            ax.set_xlabel("Freq (GHz)")
            ax.set_ylabel("|S21| (dB)")
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)

        # Hide unused subplots
        for idx in range(len(water_data), n_rows * n_cols):
            row, col = idx // n_cols, idx % n_cols
            axes[row, col].set_visible(False)

        suptitle_fs = _cfg(pic_std, "defaults", "font", "suptitle", default=20)
        fig.suptitle(f"{rname} ", fontsize=suptitle_fs)
        plt.tight_layout()
        out_dir = Path(output_dir)
        for fmt in formats:
            fig.savefig(str(out_dir / f"S21_waterfall_{rname}.{fmt}"), dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    print(f"[plot_S21_waterfall] {n_resonators} 共振器瀑布图已生成")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "S21_waterfall")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir))
