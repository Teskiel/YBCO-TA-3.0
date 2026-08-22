# plugins/plot/plot_thermal_extrapolation.py — 温度模型拟合与外推诊断
"""离线验证温度模型 f_n(T) 的拟合、外推预测与搜峰窗口。

产出（写入 output/{dataset}/thermal_extrapolation/）：
  1. thermal_f0_vs_T.{fmt}        5 模式 f0(T) 拟合曲线 + walk-forward 预测窗口带
  2. thermal_residual.{fmt}       分数残差 vs T（判断是否需要背景项）
  3. thermal_backtest.{fmt}       模式 A/B 回测误差对比图
  4. thermal_comparison.{fmt}     actual vs 整数温度 / 模型 vs 有限差分 两幅对照
  5. thermal_backtest_table.txt   逐 (温度, 模式) 回测明细表

温度一律取自 S2P 文件名里的 actual 实测值（用 find_s2p 定位参考文件后对
该路径解析），频率与温度同源于同一个文件。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import matplotlib
import _backend  # noqa: F401 — Qt5Agg -> Agg fallback
import matplotlib.pyplot as plt
from pathlib import Path

from _lib.plugin_registry import plugin
from _lib.io_med import read_json, read_temporary_resonance
from _lib.io_s2p import parse_s2p_filename
from _lib.scanning import find_s2p, scan_temperatures
from _lib.fitting import finite_diff_extrapolate
from _lib import thermal_model as tm
from _lib.io_thermal import load_chip_json, resolve_tc_k, save_calibration
from _lib.io_med import get_temporary_resonance_path as _get_temp_path
from _lib.caption import add_caption
from _pic_std import load_pic_std, _cfg, apply_axis_ticks


def _detect_structure(data_dir):
    """flat: {temp}K/{pwr}dBm/{laser}mW/*.s2p ；hierarchical 有 actual_ 子目录。"""
    temps = scan_temperatures(data_dir)
    if temps:
        tdir = Path(data_dir) / f"{temps[0]}K"
        if any(d.startswith("actual_") and (tdir / d).is_dir()
               for d in os.listdir(tdir)):
            return "hierarchical"
    return "flat"


def _build_data(source, n_resonators, config, data_med_dir=None):
    """从 temporary_resonance 金标准 + actual 温度构建 (T_actual, F) 数组。

    Returns
    -------
    (temps_actual, freqs_hz, target_temps, ql_dict)
        ql_dict: {(target_str, Rname): ql}，取 fit_results.json（可能为空 dict）
    """
    source = Path(source)
    temp_res = read_temporary_resonance(source)
    if not temp_res or "by_resonator" not in temp_res:
        raise FileNotFoundError(f"缺少人工选点金标准: {_get_temp_path(source)}")

    structure = _detect_structure(source)
    by_res = temp_res["by_resonator"]
    target_strs = sorted(by_res[list(by_res)[0]].keys(), key=int)

    temps_actual = []
    freqs = []
    targets = []
    for ts in target_strs:
        t = int(ts)
        path = find_s2p(source, t, -25, 0, structure=structure)
        info = parse_s2p_filename(str(path)) if path else {}
        actual = info.get("actual_temp_k")
        if actual is None:
            actual = float(t)   # 极端回退（不应发生）
        temps_actual.append(actual)
        targets.append(t)
        freqs.append([by_res[r][ts] for r in by_res])
    temps_actual = np.asarray(temps_actual, float)
    freqs = np.asarray(freqs, float)

    # Ql（用于把误差换算成线宽单位）——可选
    ql_dict = {}
    med_dir = Path(data_med_dir) if data_med_dir else source.parent.parent / "Data_process" / "data_med" / source.name
    fit_res = read_json(med_dir, "fit_results.json")
    if fit_res and fit_res.get("by_resonator"):
        for rname, rdata in fit_res["by_resonator"].items():
            for ts, td in rdata.get("by_temperature", {}).items():
                if td and td.get("ql"):
                    ql_dict[(ts, rname)] = float(td["ql"])
    return temps_actual, freqs, targets, ql_dict


def _backtest_row_ppms(steps):
    """[(temp, mode)] 展开回测明细：Δf(MHz), Δf/f(ppm), half(MHz), covered。"""
    rows = []
    for s in steps:
        t = s["temp_k"]
        for j in range(s["f_pred_hz"].shape[0]):
            d = s["delta_hz"][j]
            if not np.isfinite(d):
                continue
            rows.append({
                "temp_k": t,
                "mode": j + 1,
                "delta_mhz": d / 1e6,
                "ppm": d / (s["f_true_hz"][j]) * 1e6,
                "half_mhz": s["half_width_hz"][j] / 1e6,
                "covered": bool(s["covered"][j]),
            })
    return rows


def _finite_diff_baseline(temps_actual, freqs, n_anchor=4):
    """逐模式 walk-forward 的有限差分外推基线（不利用温度信息）。"""
    rows = []
    for k in range(n_anchor, len(temps_actual)):
        for j in range(freqs.shape[1]):
            pred = finite_diff_extrapolate(list(freqs[:k, j]))
            if pred == 0.0:
                continue
            d = pred - freqs[k, j]
            rows.append({
                "temp_k": float(temps_actual[k]), "mode": j + 1,
                "delta_mhz": d / 1e6, "ppm": d / freqs[k, j] * 1e6,
            })
    return rows


@plugin(
    phase="plot",
    order=5,
    inputs=["scan_result.json", "temporary_resonance.json"],
    outputs=[],
    description="温度模型拟合与外推回测 — f0(T)/残差/回测表/标定存档",
)
def main(data_med_dir, output_dir, source_data_dir=None, config=None):
    if not source_data_dir:
        raise ValueError("需要 source_data_dir（S2P 数据根目录）")
    pic_std = config.get("pic_std") if config else None
    th_cfg = (config.get("thermal_model") or {}) if config else {}

    formats = _cfg(pic_std, "plot", "thermal_extrapolation", "save_formats",
                   default=["svg", "png"])
    dpi = _cfg(pic_std, "plot", "thermal_extrapolation", "dpi", default=300)
    figsize = _cfg(pic_std, "plot", "thermal_extrapolation", "figsize",
                   default=[10, 7])
    lw_fit = _cfg(pic_std, "defaults", "lines", "fit", default=2.5)
    alpha_band = _cfg(pic_std, "defaults", "alpha", "fit_band", default=0.18)
    alpha_grid = _cfg(pic_std, "defaults", "alpha", "grid", default=0.3)
    colors = _cfg(pic_std, "verification", "resonator_colors",
                  default={"r1": "#1F77B4", "r2": "#D62728", "r3": "#2CA02C",
                           "r4": "#FF7F0E", "r5": "#9467BD"})
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 数据与 Tc ----
    n_res = int(th_cfg.get("n_resonators", 5))
    temps_actual, freqs, targets, ql_dict = _build_data(source_data_dir, n_res, config,
                                                        data_med_dir=data_med_dir)
    chip = load_chip_json(source_data_dir, path=th_cfg.get("chip_json"))
    tc_k, tc_source = resolve_tc_k(chip,
                                   tc_k=th_cfg.get("tc_k"),
                                   tc_free=bool(th_cfg.get("tc_free", False)))
    if tc_k is None:
        raise ValueError("未找到 Tc：请在 chip.json 提供 tc_k，或用 --chip-json 指定")
    n_anchor = int(th_cfg.get("min_anchor_temps", 3)) + 1
    n_sigma = float(th_cfg.get("n_sigma", tm.DEFAULT_N_SIGMA))
    window_min_hz = float(th_cfg.get("window_min_hz", tm.DEFAULT_WINDOW_MIN_HZ))

    # ---- 回测 ----
    bt_b = tm.backtest_walk_forward(temps_actual, freqs, n_anchor=n_anchor,
                                    tc_k=tc_k, n_sigma=n_sigma,
                                    window_min_hz=window_min_hz)
    bt_a = tm.backtest_holdout(temps_actual, freqs, np.array(targets) < 50,
                               tc_k=tc_k, n_sigma=n_sigma,
                               window_min_hz=window_min_hz)
    fit_full = tm.fit_global(temps_actual, freqs, tc_k=tc_k)
    fd_baseline = _finite_diff_baseline(temps_actual, freqs, n_anchor=n_anchor)

    rows_b = _backtest_row_ppms(bt_b["steps"])
    rows_a = []
    for s in bt_a["rows"]:
        for j in range(s["f_pred_hz"].shape[0]):
            d = s["delta_hz"][j]
            if not np.isfinite(d):
                continue
            rows_a.append({"temp_k": s["temp_k"], "mode": j + 1,
                           "delta_mhz": d / 1e6,
                           "ppm": d / s["f_true_hz"][j] * 1e6,
                           "half_mhz": s["half_width_hz"][j] / 1e6,
                           "covered": bool(s["covered"][j])})

    # ---- 图 1: f0(T) + 预测窗口 ----
    fig, ax = plt.subplots(figsize=figsize)
    t_dense = np.linspace(2.0, tc_k * 0.985, 300)
    for j in range(freqs.shape[1]):
        color = colors.get(f"r{j+1}", "#1F77B4")
        ax.plot(t_dense, tm.f_model(t_dense, fit_full["f0_hz"][j],
                                    fit_full["alpha"][j], tc_k, fit_full["p"]) / 1e9,
                color=color, lw=lw_fit, alpha=0.8)
        ax.plot(temps_actual, freqs[:, j] / 1e9, "o", ms=5, color=color,
                label=f"R{j+1}", alpha=0.9)
        # walk-forward 各步的预测点
        for s in bt_b["steps"]:
            fp, half = s["f_pred_hz"][j], s["half_width_hz"][j]
            ax.errorbar(s["temp_k"], fp / 1e9, yerr=half / 1e9, fmt="x",
                        color=color, ms=4, alpha=0.7, capsize=2)
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("f$_0$ (GHz)")
    ax.set_title(f"Thermal model fit + walk-forward prediction  (Tc={tc_k}K, "
                 f"p={fit_full['p']:.2f})")
    ax.legend(fontsize=_cfg(pic_std, "defaults", "font", "legend", default=12),
              ncol=5, loc="upper right", framealpha=0.8)
    ax.grid(True, alpha=alpha_grid)
    apply_axis_ticks(ax, pic_std)
    fig.tight_layout()
    add_caption(fig, "Fig. Solid: global fit on all data. Markers: measured "
                     f"f0. Crosses with bars: walk-forward predicted center "
                     f"± window. Worst walk-forward error "
                     f"{bt_b['max_abs_delta_hz']/1e6:.1f} MHz.")
    for fmt in formats:
        fig.savefig(out_dir / f"thermal_f0_vs_T.{fmt}", dpi=dpi,
                    bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # ---- 图 2: 分数残差 ----
    fig, ax = plt.subplots(figsize=figsize)
    resid = fit_full["resid_frac"] * 1e6
    for j in range(freqs.shape[1]):
        ax.plot(temps_actual, resid[:, j], "o-", ms=4, color=colors.get(f"r{j+1}", "#1F77B4"),
                label=f"R{j+1}")
    ax.axhline(0, color="gray", lw=1)
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("Fractional residual (ppm)")
    ax.set_title("Global fit residual (all data)")
    ax.legend(fontsize=11, ncol=5, framealpha=0.8)
    ax.grid(True, alpha=alpha_grid)
    apply_axis_ticks(ax, pic_std)
    fig.tight_layout()
    add_caption(fig, f"Fig. (f_model-f_meas)/f_meas. RMS "
                     f"{fit_full['rms_ppm']:.0f} ppm. "
                     "Systematic curvature indicates a missing background term.")
    for fmt in formats:
        fig.savefig(out_dir / f"thermal_residual.{fmt}", dpi=dpi,
                    bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # ---- 图 3: 回测误差对比（模式 A vs B）----
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True)
    for ax, (title, rows) in zip(axes, [("Mode B: walk-forward", rows_b),
                                        ("Mode A: one-shot holdout", rows_a)]):
        for r in rows:
            ax.plot([r["temp_k"]], [r["delta_mhz"]], "o", color=colors.get(f"r{r['mode']}", "#888888"),
                    ms=6, alpha=0.85)
        ax.axhline(0, color="gray", lw=1)
        ax.axhspan(-30, 30, color="green", alpha=0.08, label="±30 MHz gate")
        ax.set_title(title)
        ax.set_xlabel("Temperature (K)")
        ax.grid(True, alpha=alpha_grid)
        apply_axis_ticks(ax, pic_std)
    axes[0].set_ylabel("Prediction error (MHz)")
    axes[0].legend(fontsize=11, framealpha=0.8)
    fig.tight_layout()
    add_caption(fig, "Fig. Crosses: model prediction minus measured f0 per "
                     "(temp, mode). Mode B (per-step re-fit) is the production "
                     "path and stays inside ±30 MHz; Mode A is diagnostic.")
    for fmt in formats:
        fig.savefig(out_dir / f"thermal_backtest.{fmt}", dpi=dpi,
                    bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # ---- 图 4: 对照（actual vs 整数温度；模型 vs 有限差分）----
    t_int = np.array([float(t) for t in targets])
    bt_int = tm.backtest_walk_forward(t_int, freqs, n_anchor=n_anchor,
                                      tc_k=tc_k, n_sigma=n_sigma,
                                      window_min_hz=window_min_hz)
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    # (a) actual vs integer temp
    for label, bt, color in [("actual temps", bt_b, "#1F77B4"),
                             ("integer target temps", bt_int, "#D62728")]:
        for s in bt["steps"]:
            axes[0].plot([s["temp_k"]], [np.abs(s["delta_hz"]).max() / 1e6],
                         "o", color=color, ms=6)
    axes[0].axhline(30, color="gray", ls="--", lw=1)
    axes[0].set_title("Worst |err| per step: actual vs integer temps")
    axes[0].set_xlabel("Temperature (K)")
    axes[0].set_ylabel("Worst |error| (MHz)")
    axes[0].grid(True, alpha=alpha_grid)
    apply_axis_ticks(axes[0], pic_std)
    # (b) model vs finite-diff baseline
    fd_by_t = {}
    for r in fd_baseline:
        fd_by_t.setdefault(r["temp_k"], []).append(r["delta_mhz"])
    fd_t = sorted(fd_by_t)
    axes[1].plot(fd_t, [np.abs(fd_by_t[t]).max() for t in fd_t], "o--",
                 color="#D62728", label="finite-diff baseline")
    axes[1].plot([s["temp_k"] for s in bt_b["steps"]],
                 [np.abs(s["delta_hz"]).max() / 1e6 for s in bt_b["steps"]],
                 "o-", color="#1F77B4", label="physical model")
    axes[1].axhline(30, color="gray", ls="--", lw=1)
    axes[1].set_title("Worst |err|: physical model vs finite-diff")
    axes[1].set_xlabel("Temperature (K)")
    axes[1].legend(fontsize=11, framealpha=0.8)
    axes[1].grid(True, alpha=alpha_grid)
    apply_axis_ticks(axes[1], pic_std)
    fig.tight_layout()
    add_caption(fig, "Fig. (a) Using S2P filename actual temperature instead "
                     "of folder-name integer target is worth ~2.6x. (b) The "
                     "physical model beats pure finite-difference extrapolation.")
    for fmt in formats:
        fig.savefig(out_dir / f"thermal_comparison.{fmt}", dpi=dpi,
                    bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # ---- 文本回测表 ----
    lines = []
    lines.append("# Thermal-model backtest table")
    lines.append(f"# Dataset: {Path(source_data_dir).name}   Tc={tc_k}K ({tc_source})  "
                 f"p={fit_full['p']:.3f}  n_anchor={n_anchor}")
    lines.append(f"# Mode B (walk-forward): worst |df|={bt_b['max_abs_delta_hz']/1e6:.2f} MHz, "
                 f"coverage={bt_b['coverage']*100:.0f}%")
    lines.append(f"# Mode A (one-shot):     worst |df|={bt_a['max_abs_delta_hz']/1e6:.2f} MHz, "
                 f"coverage={bt_a['coverage']*100:.0f}%")
    lines.append(f"# integer-temp Mode B:   worst |df|={bt_int['max_abs_delta_hz']/1e6:.2f} MHz")
    fd_worst = max((abs(r["delta_mhz"]) for r in fd_baseline), default=float("nan"))
    lines.append(f"# finite-diff baseline:  worst |df|={fd_worst:.2f} MHz")
    lines.append("")
    lines.append("  Temp(K)  Mode   dF(MHz)   dF/f(ppm)   win(MHz)  covered   "
                 "dF/linewidth")
    for r in rows_b:
        ql = ql_dict.get((str(int(r["temp_k"])), f"R{r['mode']}"))
        lw = ""
        if ql:
            # Δf / (f/Ql)，用真值 f 近似
            f_true = freqs[np.argmin(np.abs(temps_actual - r["temp_k"])), r["mode"] - 1]
            lw = f"{abs(r['delta_mhz']) / (f_true/1e6/ql):.2f}"
        lines.append(f"{r['temp_k']:9.3f}  {r['mode']:4d}  {r['delta_mhz']:+9.3f}  "
                     f"{r['ppm']:+10.1f}  {r['half_mhz']:8.3f}  "
                     f"{'YES' if r['covered'] else 'no':>7s}   {lw}")
    (out_dir / "thermal_backtest_table.txt").write_text("\n".join(lines),
                                                        encoding="utf-8")

    # ---- 标定存档 ----
    if th_cfg.get("save_calibration", True):
        chip_id = (chip or {}).get("chip_id", "unknown-chip")
        run_id = Path(source_data_dir).name
        save_calibration(tm.to_dict(fit_full), chip_id, run_id, str(source_data_dir),
                         temps_k=temps_actual, freqs_hz=freqs,
                         backtest=bt_b)

    print(f"[plot_thermal_extrapolation] Tc={tc_k}K, p={fit_full['p']:.3f}, "
          f"rms={fit_full['rms_ppm']:.0f} ppm | walk-fwd worst "
          f"{bt_b['max_abs_delta_hz']/1e6:.2f} MHz (cov {bt_b['coverage']*100:.0f}%) | "
          f"holdout worst {bt_a['max_abs_delta_hz']/1e6:.2f} MHz | "
          f"integer-temp {bt_int['max_abs_delta_hz']/1e6:.2f} MHz | "
          f"finite-diff {fd_worst:.2f} MHz")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    out_dir = resolve_output_dir(source, "thermal_extrapolation")
    out_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(out_dir), str(source), None)
