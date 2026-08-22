# plugins/plot/plot_power_dependence.py — VNA 功率依赖
"""VNA 功率扫描 S21 叠加图: 全频谱 + 5 谐振器 zoom 子图。

参考 OLD plot_power_dependence_v2.py: jet 渐变, figsize=(12,8), lw=2.0, alpha=0.8.
自适应频率窗口: T≤20K→±10MHz, T≥77K→±30MHz, 中间线性渐变 (移植自 _zoom_mhz).

每次运行同时产出两套图:
  uniform:   全范围均匀间隔 6 条 (-55 to -25, step=6 dB)
  low_power: 低功率均匀间隔 6 条 (-55 to -35, step=4 dB)
分别存入 T{temp}K/uniform/ 和 T{temp}K/low_power/ 子目录.
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
from _lib.io_s2p import load_s_param
from _lib.scanning import find_s2p
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


def _adaptive_span_mhz(temp_k: float, t_low=20.0, span_low=10.0, t_high=77.0, span_high=30.0) -> float:
    """温度自适应频率窗口 (单侧半宽, MHz). 移植自 otherwise/plot_power_dependence_v2.py:_zoom_mhz().

    T ≤ t_low  → span_low MHz  (低温谐振极窄)
    T ≥ t_high → span_high MHz (高温谐振展宽)
    中间线性渐变。
    """
    if temp_k <= t_low:
        return span_low
    if temp_k >= t_high:
        return span_high
    return span_low + (temp_k - t_low) / (t_high - t_low) * (span_high - span_low)


def _select_vna_powers(powers, method="all", n_lines=6):
    """从 VNA 功率列表中选择子集, 减少叠加图线条数.

    Args:
        powers: VNA 功率列表 (dBm, 负值)
        method: "all" | "uniform" | "low_power"
        n_lines: 目标线条数 (自动 clamp 到 [4, 8])

    Returns:
        排序后的选中功率列表

    Default selections (16 powers, -55 to -25 dBm, 2 dB steps):
        uniform:   [-55, -49, -43, -37, -31, -25]  (6 lines, step=6 dB)
        low_power: [-55, -51, -47, -43, -39, -35]  (6 lines, step=4 dB, max ≤ -35)
        两次选点间隔不同 (6 dB vs 4 dB).
    """
    if method == "all":
        return sorted(powers, reverse=False)

    # clamp n_lines
    n_lines = max(4, min(8, n_lines))

    sorted_powers = sorted(powers, reverse=False)

    if method == "uniform":
        # 全范围均匀间隔, 含端点
        n = len(sorted_powers)
        if n_lines >= n:
            return sorted_powers
        indices = np.linspace(0, n - 1, n_lines, dtype=int)
        return [sorted_powers[i] for i in indices]

    if method == "low_power":
        # 低功率区: 先滤出 ≤ -35 dBm, 再均匀间隔选点
        low_subset = [p for p in sorted_powers if p <= -35]
        n = len(low_subset)
        if n_lines >= n:
            return low_subset
        indices = np.linspace(0, n - 1, n_lines, dtype=int)
        return [low_subset[i] for i in indices]

    # unknown method: return all
    return sorted_powers


@plugin(
    phase="plot",
    order=7,
    inputs=["selected_resonances.json", "fit_results.json", "traces.npz", "scan_result.json"],
    outputs=[],
    description="VNA 功率依赖 — 全频谱 S21 叠加 + 5 谐振器 zoom",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    manifest = load_file_manifest(data_med_dir)
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    if source_data_dir is None:
        print("[plot_power_dependence] source_data_dir required, skipping")
        return

    pic_std = config.get("pic_std") if config else None
    formats = _cfg(pic_std, "plot", "S21_waterfall", "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "S21_waterfall", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "S21_waterfall", "figsize", default=[12, 8])
    cmap_name = _cfg(pic_std, "plot", "S21_waterfall", "colormap", default="jet")
    cmap = plt.get_cmap(cmap_name)

    # 自适应频率窗口参数 (与 plot_S21_waterfall 共用 S21_waterfall.adaptive_zoom 配置)
    az_cfg = _cfg(pic_std, "plot", "S21_waterfall", "adaptive_zoom", default={})
    az_enabled = az_cfg.get("enabled", True)
    az_t_low = az_cfg.get("t_low_k", 20)
    az_span_low = az_cfg.get("span_low_mhz", 10)
    az_t_high = az_cfg.get("t_high_k", 77)
    az_span_high = az_cfg.get("span_high_mhz", 30)

    lw_data = _cfg(pic_std, "defaults", "lines", "data", default=2.0)
    alpha_line = _cfg(pic_std, "defaults", "alpha", "data_line", default=0.8)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)
    legend_fs = _cfg(pic_std, "defaults", "font", "legend", default=12)
    layout_rect = _cfg(pic_std, "defaults", "layout", "rect", default=[0, 0.06, 1, 1])

    colors = _cfg(pic_std, "verification", "resonator_colors",
                  default={"r1": "#1F77B4", "r2": "#D62728", "r3": "#2CA02C",
                           "r4": "#FF7F0E", "r5": "#9467BD"})

    source = Path(source_data_dir)
    structure = scan["structure_type"]
    vna_powers_all = sorted(scan.get("vna_powers_dbm", [-25, -35, -45, -55]))
    n_vna_total = len(vna_powers_all)

    # ---- 定义两种 VNA 功率选择: uniform + low_power, 间隔不同 ----
    selections = [
        ("uniform",   _select_vna_powers(vna_powers_all, "uniform",   n_lines=6)),
        ("low_power", _select_vna_powers(vna_powers_all, "low_power", n_lines=6)),
    ]

    temps = scan["temperatures"]
    n_resonators = len(fit["by_resonator"])
    out_dir = Path(output_dir)

    # 清理旧版扁平 power_dependence_* 文件（已迁移到 uniform/low_power 子目录）
    for stale in out_dir.rglob("power_dependence_*"):
        # 只删除直接在 T{temp}K/ 下的旧扁平文件, 保留 uniform/ 和 low_power/ 子目录内的
        if stale.parent.name in ("uniform", "low_power"):
            continue
        try:
            stale.unlink()
        except OSError:
            pass

    # ---- 对每种选择分别生成全套图 ----
    for sel_name, vna_powers in selections:
        for temp in sorted(temps):
            temp_out_dir = out_dir / f"T{temp}K" / sel_name
            temp_out_dir.mkdir(parents=True, exist_ok=True)

            # ---- 全频谱叠加 ----
            fig, ax = plt.subplots(figsize=figsize)
            n_powers = len(vna_powers)
            for pi, pwr in enumerate(vna_powers):
                s2p_path = find_s2p(source, temp, -pwr, 0, structure=structure)
                if s2p_path is None:
                    for lmw in [0, 1, 3]:
                        s2p_path = find_s2p(source, temp, -pwr, lmw, structure=structure)
                        if s2p_path:
                            break
                if s2p_path is None:
                    continue
                freq, s21 = load_s_param(str(s2p_path))
                transmission = 20 * np.log10(np.abs(s21))
                color = cmap(pi / max(n_powers - 1, 1))
                actual_t_legend = lookup_actual_temp(manifest, temp, -pwr, 0)
                legend_label = f"{-pwr} dBm"
                if actual_t_legend is not None:
                    legend_label += f" ({actual_t_legend:.2f}K)"
                ax.plot(freq / 1e9, transmission, color=color, lw=lw_data,
                        alpha=alpha_line, label=legend_label)

            if len(ax.lines) == 0:
                plt.close(fig)
                continue

            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel("|S21| (dB)")
            actual_t_title = lookup_actual_temp(manifest, temp)
            title = f"VNA Power Dependence ({sel_name}) — Full Spectrum @ T={temp}K"
            if actual_t_title is not None:
                title += f" (actual {actual_t_title:.2f}K)"
            ax.set_title(title)
            if n_powers > 4:
                ax.legend(loc="upper left", fontsize=8, bbox_to_anchor=(1.02, 1))
            else:
                ax.legend(fontsize=legend_fs)
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)
            fig.tight_layout(rect=layout_rect)
            for fmt in formats:
                fig.savefig(str(temp_out_dir / f"power_dependence_full_T{temp}K.{fmt}"),
                            dpi=dpi, bbox_inches="tight", facecolor="white")
            plt.close(fig)

            # ---- 每谐振器 zoom ----
            for ri in range(n_resonators):
                rname = f"R{ri+1}"
                rdata = fit["by_resonator"].get(rname)
                if rdata is None:
                    continue
                tdata = rdata["by_temperature"].get(str(temp))
                if tdata is None or not tdata.get("f0_hz"):
                    continue
                f0_ghz = tdata["f0_hz"] / 1e9

                fig, ax = plt.subplots(figsize=figsize)
                for pi, pwr in enumerate(vna_powers):
                    s2p_path = find_s2p(source, temp, -pwr, 0, structure=structure)
                    if s2p_path is None:
                        for lmw in [0, 1, 3]:
                            s2p_path = find_s2p(source, temp, -pwr, lmw, structure=structure)
                            if s2p_path:
                                break
                    if s2p_path is None:
                        continue
                    freq, s21 = load_s_param(str(s2p_path))
                    transmission = 20 * np.log10(np.abs(s21))
                    color = cmap(pi / max(n_powers - 1, 1))

                    # 自适应频率窗口: 根据温度缩放, 移植自 _zoom_mhz
                    f_ghz = freq / 1e9
                    span_mhz = _adaptive_span_mhz(temp, az_t_low, az_span_low, az_t_high, az_span_high)
                    half_span_ghz = span_mhz / 1000.0
                    mask = (f_ghz > f0_ghz - half_span_ghz) & (f_ghz < f0_ghz + half_span_ghz)
                    if mask.sum() > 10:
                        actual_t_legend_r = lookup_actual_temp(manifest, temp, -pwr, 0)
                        legend_label_r = f"{-pwr} dBm"
                        if actual_t_legend_r is not None:
                            legend_label_r += f" ({actual_t_legend_r:.2f}K)"
                        ax.plot(f_ghz[mask], transmission[mask], color=color, lw=lw_data,
                                alpha=alpha_line, label=legend_label_r)

                color_r = colors.get(rname.lower(), "#1F77B4")
                ax.axvline(f0_ghz, color=color_r, ls="--", lw=0.8, label=f"{rname} f0")
                ax.set_xlabel("Frequency (GHz)")
                ax.set_ylabel("|S21| (dB)")
                actual_t_title_r = lookup_actual_temp(manifest, temp)
                title_r = f"{rname} — VNA Power Dependence ({sel_name}) @ T={temp}K"
                if actual_t_title_r is not None:
                    title_r += f" (actual {actual_t_title_r:.2f}K)"
                ax.set_title(title_r)
                if n_powers + 1 > 4:
                    ax.legend(loc="upper left", fontsize=7, bbox_to_anchor=(1.02, 1))
                else:
                    ax.legend(fontsize=legend_fs)
                ax.grid(True, alpha=alpha_grid)
                apply_axis_ticks(ax, pic_std)
                fig.tight_layout(rect=layout_rect)
                for fmt in formats:
                    fig.savefig(str(temp_out_dir / f"power_dependence_{rname}_T{temp}K.{fmt}"),
                                dpi=dpi, bbox_inches="tight", facecolor="white")
                plt.close(fig)

    n_temps = len(temps)
    print(f"[plot_power_dependence] {n_temps} temp x {n_resonators} resonators x 2 selections, "
          f"{len(selections[0][1])}+{len(selections[1][1])}/{n_vna_total} VNA powers "
          f"(uniform + low_power)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "power_dependence")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir), str(source))
