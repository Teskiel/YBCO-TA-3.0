#!/usr/bin/env python
# rescalibration.py — 人工校准与反馈闭环（独立脚本，不注册 plugin）
"""人工检查/矫正谐振选点，并把矫正结果反哺模型。

不参与自动管线（step1→step4 照常跑，本脚本在之后人工运行）。流程：

  1. 启动一个 Tk 主窗：选温度 / 激光功率 / 四组图层勾选 / 备注。
  2. 点 "Start Calibration" 弹出嵌入式三面板（幅度 / 去趋势 / diff 相位），
     用算法选点预填 N 个槽位（下标 = 模式号），人工只改错的那一两个。
  3. ENTER 确认后逐模式比对人工值 vs 算法值：
       - 任一模式 |delta| 超过该模式的预测窗口半宽 → 写新版本 resposition 记录
         （__v{M}），用全部最新记录重拟合 f(T,P)，覆盖更新 fit__{chip_id}.txt
         与 thermal_models/ 存档。
       - 全部在阈值内 → 不产生新版本，只打印确认（避免每次核对刷一个版本）。
  4. 人工改动始终回写 temporary_resonance.json（默认开启，--no-writeback 可关），
     回写前把原文件另存 .bak，保证下游 step4 / plot 读的是矫正后的值。

差异判定的阈值取 tm.prediction_window 返回的窗口半宽（模型自身不确定度），
不用线宽 f/Ql（后者在高温段 Qi 崩塌时暴涨，且依赖 step4 产物、校准现场可能
还没跑或已过期）。
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

# 关键顺序：matplotlib.use("TkAgg") 必须在 import _lib.resonance 之前
# （该模块顶部 import pyplot）。否则 pyplot 可能被设为 Agg / Qt，与 Tk 主循环冲突。
import matplotlib
matplotlib.use("TkAgg")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox

from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)

from _lib.resonance import ResonancePickerSession
from _lib.io_med import (resolve_data_med_dir, read_json, read_temporary_resonance,
                         write_temporary_resonance, get_temporary_resonance_path)
from _lib.io_s2p import load_s_param, parse_s2p_filename
from _lib.scanning import find_s2p
from _lib import thermal_model as tm
from _lib import io_thermal as it


# =========================================================================
# 数据上下文
# =========================================================================

def load_context(data_dir):
    """加载校准所需的全部上下文（chip / scan / initial_guess / 拟合函数）。"""
    source = Path(data_dir).resolve()
    med_dir = resolve_data_med_dir(source)
    scan = read_json(med_dir, "scan_result.json")
    if scan is None:
        raise SystemExit(f"未找到 scan_result.json（请先跑 pipeline --phase scan）: {med_dir}")

    chip = it.load_chip_json(str(source)) or {}
    chip_dir = it.resolve_chip_dir(source) or source
    chip_id = chip.get("chip_id") or chip_dir.name
    run_id = source.name
    structure = scan["structure_type"]
    temps = sorted(int(t) for t in scan["temperatures"])
    laser_powers = sorted({int(p) for p in scan.get("laser_powers_mw", [0])})
    vna_power = -abs(int(scan.get("vna_powers_dbm", [25])[0]))
    n_res = int(scan.get("n_resonators_expected", 5))
    rnames = [f"R{i+1}" for i in range(n_res)]
    initial_guess = read_json(med_dir, "initial_guess.json") or {}

    fit = None
    fit_txt = it.load_fit_txt(chip_dir, chip_id)
    if fit_txt is not None:
        try:
            fit = tm.from_dict(fit_txt)
        except Exception as e:
            print(f"[rescalibration] 加载拟合函数失败（忽略）: {e}")

    return {
        "source": source, "med_dir": med_dir, "scan": scan, "chip": chip,
        "chip_dir": chip_dir, "chip_id": chip_id, "run_id": run_id,
        "structure": structure, "temps": temps, "laser_powers": laser_powers,
        "vna_power": vna_power, "n_res": n_res, "rnames": rnames,
        "initial_guess": initial_guess, "fit": fit,
    }


# =========================================================================
# 取算法当前值 / 实际温度 / 上一温度选点
# =========================================================================

def _record_index(rec, temp):
    """目标温度在记录中的下标（记录按 scan 温度顺序排列）。"""
    try:
        return rec["target_temperatures_k"].index(int(temp))
    except (ValueError, KeyError):
        T = np.asarray(rec["temperatures_k"], dtype=float)
        return int(np.argmin(np.abs(T - float(temp))))


def _col(rec, rname, ti):
    arr = rec.get("by_resonator", {}).get(rname)
    if arr is None or ti >= len(arr):
        return None
    v = arr[ti]
    return float(v) if v is not None else None


def current_pick(ctx, temp, laser):
    """算法当前存的值（resposition 记录优先，回退 initial_guess）。"""
    rec = it.load_res_record(ctx["chip_dir"], ctx["run_id"], laser, latest=True)
    if rec is not None:
        ti = _record_index(rec, temp)
        return [_col(rec, r, ti) for r in ctx["rnames"]]
    ig = ctx["initial_guess"]
    if laser == 0:
        return ig.get("by_temperature", {}).get(str(temp))
    return ig.get("by_laser", {}).get(str(laser), {}).get("by_temperature", {}).get(str(temp))


def actual_temp_for(ctx, temp, laser, path):
    """实际温度：S2P 文件名 actual_ 段优先（权威），回退 resposition 记录。"""
    info = parse_s2p_filename(str(path))
    if info.get("actual_temp_k") is not None:
        return float(info["actual_temp_k"])
    rec = it.load_res_record(ctx["chip_dir"], ctx["run_id"], laser, latest=True)
    if rec is not None:
        ti = _record_index(rec, temp)
        return float(rec["temperatures_k"][ti])
    return float(temp)


def prev_picks(ctx, temp, laser):
    """上一温度（次低）的算法选点，用于"上一温度选点"虚线图层。"""
    temps = ctx["temps"]
    idx = temps.index(int(temp))
    if idx == 0:
        return None
    return current_pick(ctx, temps[idx - 1], laser)


# =========================================================================
# 交互校准窗口
# =========================================================================

def build_picker(parent, ctx, temp, laser, opts):
    """打开一个嵌入式校准窗口，返回人工结果 dict 或 None（找不到 S2P）。"""
    source = ctx["source"]
    structure = ctx["structure"]
    n = ctx["n_res"]

    path = find_s2p(source, temp, ctx["vna_power"], laser, structure=structure)
    if path is None:
        messagebox.showerror("错误", f"找不到 {temp}K / {laser}mW 的 S2P 文件")
        return None
    freq, s21 = load_s_param(str(path))

    _algo = current_pick(ctx, temp, laser)
    algo = (list(_algo) + [None] * n)[:n] if _algo else [None] * n
    actual = actual_temp_for(ctx, temp, laser, path)

    # 模型预测 + 窗口半宽（差异判定阈值也用它）
    prediction = [None] * n
    half = [None] * n
    fit = ctx["fit"]
    if fit is not None:
        try:
            pred, h = tm.prediction_window(fit, actual, laser_mw=float(laser))
            prediction = [float(pred[i]) for i in range(n)]
            half = [float(h[i]) for i in range(n)]
        except Exception as e:
            print(f"[rescalibration] 预测窗口计算失败（忽略）: {e}")

    prev = prev_picks(ctx, temp, laser)
    title = f"Calibrate {ctx['chip_id']} @ {temp}K / {laser}mW (actual {actual:.3f}K)"

    session = ResonancePickerSession(
        freq, s21, n, title=title, premarked=algo,
        prediction=prediction, windows=half, prev_picks=prev,
        show_prediction=opts.get("prediction", True),
        show_mode_labels=opts.get("labels", True),
        show_prev_picks=opts.get("prev", True),
        show_residuals=opts.get("residual", True),
    )

    top = tk.Toplevel(parent)
    top.title(title)
    canvas = FigureCanvasTkAgg(session.figure, master=top)
    session.bind_to(canvas)
    toolbar = NavigationToolbar2Tk(canvas, top)
    toolbar.update()
    canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    session.on_finish = top.destroy          # ENTER/ESC -> 关闭窗口
    canvas.get_tk_widget().focus_set()        # 让键盘事件生效
    parent.wait_window(top)                   # 阻塞直到该温度校准完成

    slot_freqs, confirmed = session.get_result()
    return {"slot_freqs": slot_freqs, "confirmed": confirmed,
            "algo": algo, "half": half, "actual": actual, "path": str(path)}


# =========================================================================
# 反馈闭环：写新版本 -> 重拟合 -> 回写
# =========================================================================

def refit_from_records(ctx):
    """用全部最新 resposition 记录联合拟合 f(T,P)，更新 fit 函数与标定存档。"""
    chip_dir, chip_id, run_id, chip = (ctx["chip_dir"], ctx["chip_id"],
                                       ctx["run_id"], ctx["chip"])
    rnames = ctx["rnames"]
    all_T, all_P, all_F = [], [], []
    for laser in ctx["laser_powers"]:
        rec = it.load_res_record(chip_dir, run_id, laser, latest=True)
        if rec is None:
            continue
        T = [float(t) for t in rec["temperatures_k"]]
        for ti in range(len(T)):
            row = [_col(rec, r, ti) for r in rnames]
            all_T.append(T[ti])
            all_P.append(float(laser))
            all_F.append([float(v) if v is not None else np.nan for v in row])
    if not all_T:
        print("[rescalibration] 没有可用的 resposition 记录，无法重拟合")
        return None
    all_T = np.asarray(all_T, float)
    all_P = np.asarray(all_P, float)
    all_F = np.asarray(all_F, float)
    has_laser = bool(np.any(all_P > 0))
    try:
        fit = tm.fit_global(all_T, all_F, tc_k=(chip.get("tc_k") or 88.6),
                            laser_mw=all_P if has_laser else None,
                            fit_kappa=has_laser)
    except ValueError as e:
        print(f"[rescalibration] 重拟合失败: {e}")
        return None
    it.save_fit_txt(chip_dir, chip_id, tm.to_dict(fit))
    try:
        it.save_calibration(tm.to_dict(fit), chip_id, run_id, str(ctx["source"]))
    except Exception as e:
        print(f"[rescalibration] 标定存档写入失败（忽略）: {e}")
    ctx["fit"] = fit
    return fit


def writeback_temporary(ctx, temp, laser, slot_freqs):
    """把人工矫正值回写 temporary_resonance.json（先备份 .bak）。"""
    data = read_temporary_resonance(ctx["source"])
    if data is None:
        data = read_json(ctx["med_dir"], "initial_guess.json")
    if data is None:
        print("[rescalibration] 未找到 temporary_resonance / initial_guess，跳过回写")
        return
    path = get_temporary_resonance_path(ctx["source"])
    if path.exists():
        shutil.copy(str(path), str(path) + ".bak")
        print(f"[rescalibration] 原 temporary_resonance 已备份为 {path.name}.bak")

    rnames = ctx["rnames"]
    temp_key = str(temp)
    vals = [float(f) if f is not None else None for f in slot_freqs]
    if laser == 0:
        data.setdefault("by_temperature", {})[temp_key] = vals
        by_res = data.setdefault("by_resonator", {})
        status = data.setdefault("status", {})
        for i, r in enumerate(rnames):
            by_res.setdefault(r, {})[temp_key] = vals[i]
            status.setdefault(r, {})[temp_key] = "manual"
    else:
        bl = data.setdefault("by_laser", {}).setdefault(str(laser), {})
        bl["by_temperature"] = bl.get("by_temperature") or {}
        bl["by_temperature"][temp_key] = vals
        bl["status"] = bl.get("status") or {}
        bl["status"][temp_key] = ["manual" if v is not None else "lost" for v in vals]
    write_temporary_resonance(ctx["source"], data)
    print("[rescalibration] 已回写 temporary_resonance.json")


def apply_correction(ctx, temp, laser, slot_freqs, algo, half, note):
    """逐模式比对人工值 vs 算法值，超阈值则写新版本并重拟合。"""
    n = ctx["n_res"]
    rnames = ctx["rnames"]
    delta_hz = {}
    delta_ppm = {}
    corrected = []
    changed = False
    for i in range(n):
        f = slot_freqs[i]
        a = algo[i]
        if f is None or a is None:
            continue
        d = f - a
        if abs(d) > 1e3:                       # > 1 kHz 才算人工动了标记
            changed = True
        delta_hz[rnames[i]] = float(d)
        delta_ppm[rnames[i]] = float(d / a * 1e6)
        th = half[i] if (half[i] is not None and np.isfinite(half[i])) else 0.0
        if abs(d) > th:
            corrected.append(rnames[i])

    if not changed:
        print(f"[rescalibration] {temp}K/{laser}mW: 人工选点与算法值一致，无需更新")
        return

    if not corrected:
        print(f"[rescalibration] {temp}K/{laser}mW: 改动未超过各模式窗口半宽阈值 "
              f"(delta_hz={ {k: round(v/1e3, 2) for k, v in delta_hz.items()} } kHz)，"
              f"不产生新版本")
        if ctx.get("writeback", True):
            writeback_temporary(ctx, temp, laser, slot_freqs)
        return

    # ---- 写新版本 resposition 记录 ----
    rec = it.load_res_record(ctx["chip_dir"], ctx["run_id"], laser, latest=True)
    if rec is None:
        print("[rescalibration] 未找到该 (run, laser) 的 resposition 记录，"
              "仅回写 temporary_resonance，跳过归档/重拟合")
        if ctx.get("writeback", True):
            writeback_temporary(ctx, temp, laser, slot_freqs)
        return

    ti = _record_index(rec, temp)
    for i in range(n):
        if slot_freqs[i] is not None:
            rec["by_resonator"][rnames[i]][ti] = float(slot_freqs[i])
            if "status" in rec:
                rec["status"][rnames[i]][ti] = "manual"
    rec["corrected_modes"] = corrected
    rec["delta_hz"] = delta_hz
    rec["delta_ppm"] = delta_ppm
    if note:
        rec["operator_note"] = note

    path, ver = it.save_res_record(ctx["chip_dir"], ctx["run_id"], laser, rec)
    print(f"[rescalibration] 已写新版本: {path.name} (v{ver}), 校正模式: {corrected}")

    fit = refit_from_records(ctx)
    if fit is not None:
        print(f"[rescalibration] 重拟合完成: rms={fit['rms_ppm']:.0f} ppm"
              + (f", kappa={np.asarray(fit['kappa']).round(6).tolist()}"
                 if fit.get("kappa") is not None else ""))

    if ctx.get("writeback", True):
        writeback_temporary(ctx, temp, laser, slot_freqs)


# =========================================================================
# 主窗口
# =========================================================================

def main():
    args = parse_args()
    ctx = load_context(args.data_dir)
    ctx["writeback"] = not args.no_writeback

    print(f"[rescalibration] 芯片: {ctx['chip_id']} | run: {ctx['run_id']} | "
          f"温度: {ctx['temps']}K | 激光: {ctx['laser_powers']}mW")
    if ctx["fit"] is None:
        print("[rescalibration] 提示: 未找到 fit__*.txt，预测窗口/残差图层将不显示")

    root = tk.Tk()
    root.title("Resonance Calibration")

    temp_var = tk.StringVar(value=str(ctx["temps"][0]))
    laser_var = tk.StringVar(value=str(ctx["laser_powers"][0]))
    var_pred = tk.BooleanVar(value=True)
    var_labels = tk.BooleanVar(value=True)
    var_prev = tk.BooleanVar(value=True)
    var_resid = tk.BooleanVar(value=True)
    note_var = tk.StringVar(value="")

    frm = ttk.Frame(root, padding=12)
    frm.grid(sticky="nsew")

    ttk.Label(frm, text=f"芯片: {ctx['chip_id']}     run: {ctx['run_id']}"
              ).grid(row=0, column=0, columnspan=4, sticky="w")

    ttk.Label(frm, text="温度 (K):").grid(row=1, column=0, sticky="w")
    ttk.Combobox(frm, textvariable=temp_var, values=[str(t) for t in ctx["temps"]],
                 width=6, state="readonly").grid(row=1, column=1, sticky="w")
    ttk.Label(frm, text="激光 (mW):").grid(row=1, column=2, sticky="w", padx=(12, 0))
    ttk.Combobox(frm, textvariable=laser_var,
                 values=[str(p) for p in ctx["laser_powers"]],
                 width=6, state="readonly").grid(row=1, column=3, sticky="w")

    ttk.Checkbutton(frm, text="算法选点 + 预测位置 + 窗口", variable=var_pred
                    ).grid(row=2, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(frm, text="模式编号 R1~R5", variable=var_labels
                    ).grid(row=2, column=2, columnspan=2, sticky="w")
    ttk.Checkbutton(frm, text="上一温度选点", variable=var_prev
                    ).grid(row=3, column=0, columnspan=2, sticky="w")
    ttk.Checkbutton(frm, text="拟合残差 (f_manual − f_model)", variable=var_resid
                    ).grid(row=3, column=2, columnspan=2, sticky="w")

    ttk.Label(frm, text="备注:").grid(row=4, column=0, sticky="w")
    ttk.Entry(frm, textvariable=note_var, width=40).grid(
        row=4, column=1, columnspan=3, sticky="we")

    def on_start():
        temp = int(temp_var.get())
        laser = int(laser_var.get())
        opts = {"prediction": var_pred.get(), "labels": var_labels.get(),
                "prev": var_prev.get(), "residual": var_resid.get()}
        result = build_picker(root, ctx, temp, laser, opts)
        if result is None:
            return
        if not result["confirmed"]:
            print(f"[rescalibration] {temp}K/{laser}mW: 已取消，未做修改")
            return
        apply_correction(ctx, temp, laser, result["slot_freqs"], result["algo"],
                         result["half"], note_var.get().strip())

    ttk.Button(frm, text="Start Calibration", command=on_start).grid(
        row=5, column=0, columnspan=4, sticky="we", pady=(8, 0))

    root.mainloop()


def parse_args():
    p = argparse.ArgumentParser(description="人工校准谐振选点并反哺模型")
    p.add_argument("--data-dir", required=True,
                   help="run 层或扁平数据目录")
    p.add_argument("--no-writeback", action="store_true",
                   help="不回写 temporary_resonance.json")
    return p.parse_args()


if __name__ == "__main__":
    main()
