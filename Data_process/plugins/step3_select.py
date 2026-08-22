# plugins/step3_select.py — 谐振选定（温度模型驱动）
"""选定谐振器 -> initial_guess.json + temporary_resonance。

策略（按温度升序的 walk-forward）：
  低温 (target < auto_temp_max_k)  沿用现有自动策略：取 dip 最深的 n 个峰按频率编号；
                                  已有足够锚点后用温度模型反查自动分配（>5σ 警告配错）。
  高温 (target >= auto_temp_max_k) 模型驱动：用全部"已确认"点拟合 f_n(T)，
                                  预测下一温度每模式中心频率 ± 窗口，在窗口内
                                  验证峰，匈牙利算法全局分配，窗口逐级展宽重扫，
                                  未命中标 merged/lost（保留编号占位、不参与拟合、
                                  后续温度继续尝试找回）。人工点选仅作为兜底。

温度一律用 S2P 文件名里的 actual 实测值（与参考 trace 同源）。
模型参数（Tc 等）取自 chip.json；无 chip.json 或锚点不足时退回原逻辑。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import (read_json, write_json, read_temporary_resonance,
                         write_temporary_resonance, resolve_data_med_dir)
from _lib.io_s2p import load_s_param, parse_s2p_filename
from _lib.scanning import find_s2p
from _lib.resonance import (interactive_pick_resonances, find_true_resonances,
                            find_dip_peaks_in_windows)
from _lib import thermal_model as tm
from _lib.io_thermal import load_chip_json, resolve_tc_k, save_calibration

# 高温交互检测使用更宽容的寻峰阈值（峰变浅变宽后 prominence 不再够用）
HIGH_T_DETECT_KW = dict(min_prominence=1.0, distance=5, phase_window=15,
                        phase_diff_snr_threshold=0.2, plot=False)


@plugin(
    phase="process",
    order=3,
    inputs=["detected_peaks.json", "scan_result.json"],
    outputs=["initial_guess.json"],
    description="谐振选定: 低温自动 + 高温模型驱动 walk-forward",
)
def main(data_med_dir, source_data_dir, config=None):
    scan = read_json(data_med_dir, "scan_result.json")
    detected = read_json(data_med_dir, "detected_peaks.json")
    if scan is None or detected is None:
        raise FileNotFoundError("prerequisite files not found")

    source = Path(source_data_dir)
    structure = scan["structure_type"]
    temps = scan["temperatures"]           # 目标温度 (int)
    n_resonators = scan.get("n_resonators_expected", 5)
    force_interactive = config.get("force_interactive", False) if config else False
    th_cfg = (config.get("thermal_model") or {}) if config else {}
    enabled = bool(th_cfg.get("enabled", True))
    auto_temp_max_k = int(th_cfg.get("auto_temp_max_k", 50))
    min_anchor_temps = int(th_cfg.get("min_anchor_temps", 3))
    n_sigma = float(th_cfg.get("n_sigma", tm.DEFAULT_N_SIGMA))
    window_min_hz = float(th_cfg.get("window_min_hz", tm.DEFAULT_WINDOW_MIN_HZ))
    widen_factors = list(th_cfg.get("widen_factors", [2, 4]))
    laser_window_slope = float(th_cfg.get("laser_window_slope", 3e6))

    chip = load_chip_json(source_data_dir, path=th_cfg.get("chip_json"))
    tc_k, tc_source = resolve_tc_k(chip, tc_k=th_cfg.get("tc_k"),
                                   tc_free=bool(th_cfg.get("tc_free", False)))
    if tc_k is None:
        print(f"[step3_select] 警告: 未找到 chip.json 的 tc_k，温度模型停用，退回原逻辑")
        enabled = False

    # ---- 每个目标温度的实际温度（与参考 trace 同源）----
    def _actual_temp(t, laser=0):
        path = find_s2p(source, t, -25, laser, structure=structure)
        info = parse_s2p_filename(str(path)) if path else {}
        act = info.get("actual_temp_k")
        return (float(act) if act is not None else float(t)), path

    temps_actual = {t: _actual_temp(t)[0] for t in temps}
    temps = sorted(temps)

    auto_temps = [t for t in temps if t < auto_temp_max_k]
    high_temps = [t for t in temps if t >= auto_temp_max_k]

    by_temperature = {}
    by_resonator = {f"R{i+1}": {} for i in range(n_resonators)}
    actual_temps_k = {str(t): temps_actual[t] for t in temps}
    status = {f"R{i+1}": {} for i in range(n_resonators)}
    prediction = {f"R{i+1}": {} for i in range(n_resonators)}
    interactive_log = {}

    # 拟合数据集：每个已确认温度一行的 (t_actual, freqs[n], mask[n])
    fit_temps = []
    past_error_hz = 0.0
    last_fit = None

    def _append_fit_point(t_actual, freqs, mask):
        fit_temps.append({"t_actual": t_actual, "freqs": np.asarray(freqs, float),
                          "mask": np.asarray(mask, bool)})

    def _model_fit():
        """用已确认点做全局拟合，返回 fit dict 或 None。"""
        if len(fit_temps) < min_anchor_temps:
            return None
        try:
            T = np.array([p["t_actual"] for p in fit_temps])
            F = np.array([p["freqs"] for p in fit_temps])
            M = np.array([p["mask"] for p in fit_temps])
            return tm.fit_global(T, F, mask=M, tc_k=tc_k)
        except ValueError as e:
            print(f"[step3_select] 警告: 拟合失败，退回原逻辑: {e}")
            return None

    # 加载持久选点文件做预标记（重跑时恢复）
    persistent = read_temporary_resonance(source_data_dir)
    if persistent:
        print(f"[step3_select] 已加载 temporary_resonance 预标记")

    # ============ 低温锚定：现有自动策略 + 模型反查守卫 ============
    for temp in auto_temps:
        temp_key = str(temp)
        if temp_key not in detected["by_temperature"]:
            continue
        peaks = detected["by_temperature"][temp_key]["peaks"]
        sorted_peaks = sorted(peaks, key=lambda p: p["dip_db"])[:n_resonators]
        selected = sorted(p["freq_hz"] for p in sorted_peaks)
        # 低温也找不到足够峰 → 用已有模型预测补齐占位（仅记录，不参与拟合）
        for j in range(n_resonators - len(selected)):
            fit = _model_fit()
            if fit is not None:
                f_pred, half = tm.prediction_window(fit, temps_actual[temp], 0.0,
                                                    n_sigma=n_sigma,
                                                    window_min_hz=window_min_hz)
                selected.append(float(f_pred[len(selected)]))

        by_temperature[temp_key] = selected
        # 模型补齐的占位值不参与拟合（配错会毒化外推链）
        ok_mask = [len(peaks) > j for j in range(n_resonators)]
        for i, freq in enumerate(selected):
            by_resonator[f"R{i+1}"][temp_key] = float(freq)
            status[f"R{i+1}"][temp_key] = "auto"
            prediction[f"R{i+1}"][temp_key] = {"source": "auto"}
        _append_fit_point(temps_actual[temp], selected, ok_mask)
        print(f"  T={temp}K (auto, actual={temps_actual[temp]:.3f}K): {len(selected)} resonators")

        # 模型反查：自动分配是否与模型冲突（>5σ 警告，不改分配）
        if len(fit_temps) >= min_anchor_temps:
            fit = _model_fit()
            if fit is not None:
                f_pred, sigma = tm.predict(fit, float(temps_actual[temp]))
                for i in range(n_resonators):
                    if sigma[i] > 0 and abs(selected[i] - f_pred[i]) > 5 * sigma[i]:
                        print(f"    [警告] R{i+1} @ T={temp}K: 自动分配值偏离模型 "
                              f"{abs(selected[i]-f_pred[i])/1e6:.2f} MHz (>5σ={5*sigma[i]/1e6:.2f})，"
                              f"可能配错，请人工核对")

    # ============ 高温：模型驱动 walk-forward ============
    for temp in high_temps:
        temp_key = str(temp)
        ref_path = find_s2p(source, temp, -25, 0, structure=structure)
        if ref_path is None:
            print(f"  [SKIP] T={temp}K: 无 S2P 文件")
            continue

        freq, s21 = load_s_param(str(ref_path))
        fit = _model_fit()

        if not enabled or fit is None:
            # 退回原逻辑：hint 引导的自动选点
            premarked = None
            if persistent and temp_key in persistent.get("by_temperature", {}):
                premarked = persistent["by_temperature"][temp_key]
            elif by_temperature:
                last_key = sorted(by_temperature)[-1]
                premarked = by_temperature[last_key]
            if force_interactive:
                selected = interactive_pick_resonances(
                    freq, s21, f"Select {n_resonators} Resonances @ {temp}K", premarked)
            else:
                peaks, _, _ = find_true_resonances(
                    freq, s21, n_resonances=n_resonators, hint_freqs=premarked,
                    **HIGH_T_DETECT_KW)
                selected = [p["frequency"] for p in peaks]
            if len(selected) < n_resonators:
                selected = selected + [None] * (n_resonators - len(selected))
            by_temperature[temp_key] = [float(s) if s is not None else None
                                        for s in selected]
            for i, fr in enumerate(by_temperature[temp_key]):
                by_resonator[f"R{i+1}"][temp_key] = fr
                status[f"R{i+1}"][temp_key] = "auto_hinted"
                prediction[f"R{i+1}"][temp_key] = {"source": "auto_hinted"}
                if fr is not None:
                    _append_fit_point(temps_actual[temp],
                                      [fr if j == i else np.nan for j in range(n_resonators)],
                                      [j == i for j in range(n_resonators)])
            interactive_log[temp_key] = {"action": "auto_hinted", "n_selected": len(selected)}
            print(f"  T={temp}K (fallback, actual={temps_actual[temp]:.3f}K): "
                  f"{len(selected)} resonators")
            continue

        # ---- 模型驱动路径 ----
        f_pred, half = tm.prediction_window(fit, float(temps_actual[temp]),
                                            past_error_hz=past_error_hz,
                                            n_sigma=n_sigma,
                                            window_min_hz=window_min_hz)
        _, sigma = tm.predict(fit, float(temps_actual[temp]))
        # 窗口内下凹验证（高温段全局 prominence 判据失效，见
        # resonance.find_dip_peaks_in_windows 的 docstring）
        transmission = 20.0 * np.log10(np.abs(s21))
        candidates = list(find_dip_peaks_in_windows(freq, transmission,
                                                    f_pred, half))

        # 逐级展宽窗口重扫
        assign = None
        widen_level = 0
        for level, factor in enumerate([1.0] + widen_factors):
            assign = tm.assign_peaks(f_pred, half * factor, candidates)
            widen_level = level
            if assign["status"].count("ok") == n_resonators:
                break

        # 兜底：出现 lost 且允许交互时弹窗（默认路径降级为异常路径）。
        # interactive_pick_resonances 返回升序频率列表（Hz），不能按模式索引
        # 直接对应 → 把人工点并入候选集后重新全局分配。
        if force_interactive and "lost" in assign["status"]:
            premarked = [float(f_pred[j]) for j in range(n_resonators)
                         if assign["status"][j] != "ok"]
            picked = interactive_pick_resonances(
                freq, s21, f"Select {n_resonators} Resonances @ {temp}K", premarked)
            if picked:
                candidates = sorted(set(candidates) | set(float(p) for p in picked))
                assign = tm.assign_peaks(f_pred, half * (2.0 ** widen_factors[-1]),
                                         candidates)

        f_assigned = assign["f_assigned_hz"]
        by_temperature[temp_key] = [float(f_assigned[j]) if np.isfinite(f_assigned[j])
                                    else None for j in range(n_resonators)]
        n_ok = 0
        for j in range(n_resonators):
            rname = f"R{j+1}"
            st = assign["status"][j]
            status[rname][temp_key] = st
            by_resonator[rname][temp_key] = (float(f_assigned[j])
                                             if np.isfinite(f_assigned[j]) else None)
            prediction[rname][temp_key] = {
                "f_pred_hz": float(f_pred[j]),
                "sigma_hz": (float(sigma[j]) if np.isfinite(sigma[j]) else None),
                "window_hz": float(half[j] * ([1.0] + widen_factors)[widen_level]),
                "widen_level": widen_level,
                "source": "model",
            }
            if st == "ok":
                n_ok += 1
        ok_mask = [assign["status"][j] == "ok" for j in range(n_resonators)]
        if any(ok_mask):
            _append_fit_point(temps_actual[temp], f_assigned, ok_mask)
            # 经验自标定：更新历史最大单步误差
            for j in range(n_resonators):
                if ok_mask[j] and np.isfinite(f_pred[j]):
                    past_error_hz = max(past_error_hz, abs(f_assigned[j] - f_pred[j]))
        interactive_log[temp_key] = {
            "action": "model" if not force_interactive else "model+manual",
            "n_selected": n_ok,
            "status": dict((f"R{j+1}", assign["status"][j]) for j in range(n_resonators)),
        }
        print(f"  T={temp}K (model, actual={temps_actual[temp]:.3f}K): "
              f"{n_ok}/{n_resonators} ok, status="
              f"{[assign['status'][j] for j in range(n_resonators)]}, "
              f"widen={widen_level}, worst_err_so_far={past_error_hz/1e6:.2f} MHz")

    # ============ 汇总 + 保存 ============
    last_fit = _model_fit()

    # ============ 激光维度选点（0mW 模型预测为窗口中心，窗口随功率放宽）============
    # 每个激光功率 P > 0 在每个温度下的 5 模式选点，存入 by_laser。0mW 已由
    # 上面的温度 walk-forward 完成。物理依据：激光只是把谐振中心单调拉低（
    # f(T,P) 模型里的 κ·P 项），以 0mW 预测为中心、窗口按 |P| 线性放宽即可
    # 覆盖该偏移；响应率随 T 增大，故窗口下限仍由 prediction_window 兜底。
    by_laser = {}
    laser_temps = {}
    laser_powers_mw = [int(p) for p in scan.get("laser_powers_mw", [0])]
    if enabled and last_fit is not None:
        for laser in laser_powers_mw:
            if laser == 0:
                continue
            lkey = str(laser)
            by_laser[lkey] = {"by_temperature": {}, "status": {}}
            laser_temps[lkey] = {}
            for temp in temps:
                temp_key = str(temp)
                actual, path = _actual_temp(temp, laser)
                laser_temps[lkey][temp_key] = actual
                if path is None:
                    by_laser[lkey]["by_temperature"][temp_key] = [None] * n_resonators
                    continue
                freq, s21 = load_s_param(str(path))
                # 以 0mW 模型在 same actual 温度下的预测为中心，窗口额外放宽
                f_pred, half = tm.prediction_window(last_fit, actual,
                                                    past_error_hz=past_error_hz,
                                                    n_sigma=n_sigma,
                                                    window_min_hz=window_min_hz)
                half_laser = half + laser_window_slope * float(laser)
                transmission = 20.0 * np.log10(np.abs(s21))
                candidates = list(find_dip_peaks_in_windows(freq, transmission,
                                                            f_pred, half_laser))
                assign = tm.assign_peaks(f_pred, half_laser, candidates)
                f_assigned = assign["f_assigned_hz"]
                by_laser[lkey]["by_temperature"][temp_key] = [
                    float(f_assigned[j]) if np.isfinite(f_assigned[j]) else None
                    for j in range(n_resonators)]
                by_laser[lkey]["status"][temp_key] = assign["status"]
        print(f"[step3_select] 激光维度选点完成: {len(by_laser)} 个非零功率 × "
              f"{len(temps)} 温度")

    result = {
        "schema_version": 2,
        "method": "model_walk_forward" if enabled else "auto",
        "n_resonators": n_resonators,
        "by_temperature": {k: by_temperature[k] for k in sorted(by_temperature,
                                                                key=lambda x: int(x))},
        "by_resonator": by_resonator,
        "actual_temps_k": {k: actual_temps_k[k] for k in sorted(actual_temps_k,
                                                                key=lambda x: int(x))},
        "status": status,
        "prediction": prediction,
        "interactive_log": interactive_log,
    }
    if by_laser:
        result["by_laser"] = by_laser
        result["laser_temps_k"] = laser_temps
    if last_fit is not None and enabled:
        result["thermal_fit"] = tm.to_dict(last_fit)

    write_json(data_med_dir, "initial_guess.json", result)
    write_temporary_resonance(source_data_dir, result)

    # 标定存档
    if enabled and last_fit is not None and th_cfg.get("save_calibration", True):
        chip_id = (chip or {}).get("chip_id", "unknown-chip")
        run_id = source.name
        save_calibration(tm.to_dict(last_fit), chip_id, run_id, str(source),
                         temps_k=np.array([p["t_actual"] for p in fit_temps]),
                         freqs_hz=np.array([p["freqs"] for p in fit_temps]),
                         backtest={"max_abs_delta_hz": past_error_hz})

    n_ok_total = sum(1 for r in status.values() for s in r.values() if s in ("ok", "auto"))
    print(f"[step3_select] 选点完成: {len(by_temperature)} 温度, "
          f"{n_ok_total} ok/auto 点, {sum(1 for r in status.values() for s in r.values() if s in ('merged','lost'))} "
          f"merged/lost -> initial_guess.json + temporary_resonance")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    p.add_argument("--interactive", action="store_true")
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    main(str(med_dir), str(source),
         config={"interactive": args.interactive, "force_interactive": args.interactive})
