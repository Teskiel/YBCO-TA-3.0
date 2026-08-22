# plugins/plot/plot_S21_laser_power.py — 激光功率依赖
"""激光功率扫描 S21 叠加图: 全频谱 + 每谐振器 zoom 子图。

参考 OLD plot_power_dependence_v2.py: plasma 渐变, figsize=(12,8), lw=2.0, alpha=0.8.
自适应频率窗口: T≤20K→±10MHz, T≥77K→±30MHz, 中间线性渐变 (移植自 _zoom_mhz).

每次运行产出全套图:
  全频谱: 固定 (目标温度, VNA 功率) 下所有激光功率的 |S21| 叠加
  zoom:  每谐振器在自适应频率窗口内的激光功率叠加 + f0 标注
图例标注格式: "0 mW (T=6.02K)" — 激光功率 + 即时实际温度.
存入 -{VNA}dBm/ 子目录.
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


@plugin(
    phase="plot",
    order=8,
    inputs=["selected_resonances.json", "fit_results.json", "scan_result.json"],
    outputs=[],
    description="激光功率依赖 — |S21| vs Frequency, 激光功率作 legend, 实际温度标注",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    fit = read_json(data_med_dir, "fit_results.json")
    scan = read_json(data_med_dir, "scan_result.json")
    manifest = load_file_manifest(data_med_dir)
    if fit is None or scan is None:
        raise FileNotFoundError("fit_results.json or scan_result.json not found")

    if source_data_dir is None:
        print("[plot_S21_laser_power] source_data_dir required, skipping")
        return

    pic_std = config.get("pic_std") if config else None

    # 从 pic_std 读取本插件专属配置 (回退到 S21_waterfall 的通用默认值)
    section = "S21_laser_power"
    formats = _cfg(pic_std, "plot", section, "save_formats", default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", section, "dpi", default=300)
    figsize = _cfg(pic_std, "plot", section, "figsize", default=[12, 8])
    cmap_name = _cfg(pic_std, "plot", section, "colormap", default="plasma")
    cmap = plt.get_cmap(cmap_name)
    vna_powers_cfg = _cfg(pic_std, "plot", section, "vna_powers_dbm", default=[-25])
    # 取绝对值用于显示 (VNA 功率以负值存储, -25 → 25)
    vna_powers_use = [abs(p) if p < 0 else p for p in vna_powers_cfg]

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
    laser_powers_all = sorted(scan.get("laser_powers_mw", []))
    if not laser_powers_all:
        print("[plot_S21_laser_power] no laser_powers_mw in scan_result.json, skipping")
        return

    temps = scan["temperatures"]
    n_resonators = len(fit["by_resonator"])
    out_dir = Path(output_dir)
    n_temps = len(temps)

    # ---- 对每个选中的 VNA 功率分别生成全套图 ----
    for vna_pwr_dbm, vna_abs in zip(vna_powers_cfg, vna_powers_use):
        vna_label = f"-{vna_abs}dBm"
        vna_out_dir = out_dir / vna_label
        vna_out_dir.mkdir(parents=True, exist_ok=True)

        for temp in sorted(temps):
            # ---- 收集该 (T, Pv) 下所有激光功率的 S2P 数据 ----
            s2p_data = []  # list of (laser_mw, actual_temp_k, freq_hz, s21_complex)
            for lmw in laser_powers_all:
                s2p_path = find_s2p(source, temp, vna_pwr_dbm, lmw, structure=structure)
                if s2p_path is None:
                    # 尝试宽松匹配: 向后回退几个激光功率
                    for fallback_lmw in [lmw - 1, lmw - 2, 0]:
                        if fallback_lmw < 0:
                            continue
                        s2p_path = find_s2p(source, temp, vna_pwr_dbm, fallback_lmw, structure=structure)
                        if s2p_path:
                            break
                if s2p_path is None:
                    continue
                freq, s21 = load_s_param(str(s2p_path))
                actual_t = lookup_actual_temp(manifest, temp, vna_pwr_dbm, lmw)
                s2p_data.append((lmw, actual_t, freq, s21))

            if len(s2p_data) < 2:
                # 叠加图需要至少 2 条曲线才有意义
                continue

            n_laser = len(s2p_data)

            # ---- Figure A: 全频谱叠加 ----
            fig, ax = plt.subplots(figsize=figsize)
            for i, (lmw, actual_t, freq, s21) in enumerate(s2p_data):
                transmission = 20 * np.log10(np.abs(s21))
                color = cmap(i / max(n_laser - 1, 1))
                label = f"{lmw} mW"
                if actual_t is not None:
                    label += f" (T={actual_t:.2f}K)"
                ax.plot(freq / 1e9, transmission, color=color, lw=lw_data,
                        alpha=alpha_line, label=label)

            if len(ax.lines) == 0:
                plt.close(fig)
                continue

            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel("|S21| (dB)")
            # 标题中的参考实际温度: 取 Pl=0 的实际温度, 无则取第一条
            ref_actual = lookup_actual_temp(manifest, temp, vna_pwr_dbm, 0)
            title = f"Laser Power Dependence — Full Spectrum @ T={temp}K, VNA={vna_label}"
            if ref_actual is not None:
                title += f" (actual ~{ref_actual:.2f}K)"
            ax.set_title(title)
            if n_laser > 4:
                ax.legend(loc="upper left", fontsize=8, bbox_to_anchor=(1.02, 1))
            else:
                ax.legend(fontsize=legend_fs)
            ax.grid(True, alpha=alpha_grid)
            apply_axis_ticks(ax, pic_std)
            fig.tight_layout(rect=layout_rect)
            for fmt in formats:
                fig.savefig(str(vna_out_dir / f"laser_power_full_T{temp}K.{fmt}"),
                            dpi=dpi, bbox_inches="tight", facecolor="white")
            plt.close(fig)

            # ---- Figure B: 每谐振器 zoom ----
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
                for i, (lmw, actual_t, freq, s21) in enumerate(s2p_data):
                    f_ghz = freq / 1e9
                    span_mhz = _adaptive_span_mhz(temp, az_t_low, az_span_low, az_t_high, az_span_high)
                    half_span_ghz = span_mhz / 1000.0
                    mask = (f_ghz > f0_ghz - half_span_ghz) & (f_ghz < f0_ghz + half_span_ghz)
                    if mask.sum() > 10:
                        transmission = 20 * np.log10(np.abs(s21))
                        color = cmap(i / max(n_laser - 1, 1))
                        label = f"{lmw} mW"
                        if actual_t is not None:
                            label += f" (T={actual_t:.2f}K)"
                        ax.plot(f_ghz[mask], transmission[mask], color=color, lw=lw_data,
                                alpha=alpha_line, label=label)

                color_r = colors.get(rname.lower(), "#1F77B4")
                ax.axvline(f0_ghz, color=color_r, ls="--", lw=0.8, label=f"{rname} f0")
                ax.set_xlabel("Frequency (GHz)")
                ax.set_ylabel("|S21| (dB)")
                ref_actual_r = lookup_actual_temp(manifest, temp, vna_pwr_dbm, 0)
                title_r = f"{rname} — Laser Power Dependence @ T={temp}K, VNA={vna_label}"
                if ref_actual_r is not None:
                    title_r += f" (actual ~{ref_actual_r:.2f}K)"
                ax.set_title(title_r)
                if n_laser + 1 > 4:
                    ax.legend(loc="upper left", fontsize=7, bbox_to_anchor=(1.02, 1))
                else:
                    ax.legend(fontsize=legend_fs)
                ax.grid(True, alpha=alpha_grid)
                apply_axis_ticks(ax, pic_std)
                fig.tight_layout(rect=layout_rect)
                for fmt in formats:
                    fig.savefig(str(vna_out_dir / f"laser_power_{rname}_T{temp}K.{fmt}"),
                                dpi=dpi, bbox_inches="tight", facecolor="white")
                plt.close(fig)

    print(f"[plot_S21_laser_power] {n_temps} temps x {n_resonators} resonators x {len(vna_powers_cfg)} VNA powers, "
          f"laser powers: {laser_powers_all}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "S21_laser_power")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir), str(source))
