# plugins/step4_fit.py — IQ 拟合 + 参数提取
"""对每个选定谐振器进行 IQ 拟合, 提取物理参数。

输入: initial_guess.json (粗略初值, 来自 step3)
输出: selected_resonances.json (IQ 拟合精确 f0), fit_results.json (完整参数), traces.npz
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, write_json, write_npz, resolve_data_med_dir
from _lib.io_s2p import load_s_param, parse_s2p_filename
from _lib.scanning import find_s2p
from _lib.fitting import fit_resonance, finite_diff_extrapolate, compute_normalized_iq
from _lib import thermal_model as tm
from _lib import io_thermal as it


@plugin(
    phase="process",
    order=4,
    inputs=["initial_guess.json", "scan_result.json"],
    outputs=["selected_resonances.json", "fit_results.json", "traces.npz"],
    description="IQ 拟合 + 物理参数提取 (f0, Qi, Qc, Ql, 响应率)",
    backend="scraps",
)
def main(data_med_dir, source_data_dir, config=None):
    scan = read_json(data_med_dir, "scan_result.json")
    initial_guess = read_json(data_med_dir, "initial_guess.json")
    if scan is None or initial_guess is None:
        raise FileNotFoundError("prerequisite files not found")

    source = Path(source_data_dir)
    structure = scan["structure_type"]
    temps = scan["temperatures"]
    vna_powers = [abs(p) for p in scan["vna_powers_dbm"]]
    laser_powers = scan["laser_powers_mw"]
    n_resonators = initial_guess["n_resonators"]
    fit_span = config.get("fit_span_hz", 50e6) if config else 50e6

    backend = "scraps"
    try:
        import scraps  # noqa: F401
    except ImportError:
        print("[step4_fit] WARNING: scraps 不可用, 回退到 dataprocess 后端")
        backend = "dataprocess"

    print(f"[step4_fit] 后端: {backend}, 拟合窗口: {fit_span/1e6:.0f} MHz")
    print(f"[step4_fit] 处理 {n_resonators} 个共振器 x {len(temps)} 个温度")

    by_resonator = {}
    npz_arrays = {}

    for ri in range(n_resonators):
        rname = f"R{ri+1}"
        print(f"\n  --- {rname} ---")

        r_data = {"by_temperature": {}, "f0_vs_T_hz": [], "responsivity_vs_T_ppm_per_mw": []}
        f0_history = []

        for ti, temp in enumerate(temps):
            temp_key = str(temp)
            if temp_key not in initial_guess["by_temperature"]:
                continue

            # 获取初值频率: 从 initial_guess 读取粗略值
            init_freqs = initial_guess["by_temperature"].get(temp_key, [])
            if ri < len(init_freqs):
                init_freq = init_freqs[ri]
            else:
                init_freq = None

            if f0_history and init_freq is None:
                init_freq = finite_diff_extrapolate(f0_history)

            if init_freq is None:
                print(f"    T={temp}K: 无初值频率, 跳过")
                r_data["f0_vs_T_hz"].append(None)
                continue

            # 用第一个 VNA 功率 + 0mW 做基准拟合
            ref_path = find_s2p(source, temp, -vna_powers[0], 0, structure=structure)
            if ref_path is None:
                print(f"    T={temp}K: 无 S2P, 跳过")
                r_data["f0_vs_T_hz"].append(None)
                continue

            freq, s21 = load_s_param(str(ref_path))
            fit = fit_resonance(freq, s21, init_freq, span=fit_span, temp=temp, pwr=vna_powers[0], backend=backend)

            if fit.get("f0_hz") is None:
                print(f"    T={temp}K: 拟合失败")
                r_data["f0_vs_T_hz"].append(None)
                continue

            f0_hz = fit["f0_hz"]
            f0_history.append(f0_hz)

            t_data = {
                "f0_hz": f0_hz,
                "qi": fit.get("qi"),
                "qc": fit.get("qc"),
                "ql": fit.get("ql"),
                "phi": fit.get("phi"),
                "a": fit.get("a"),
                "alpha": fit.get("alpha"),
                "tau": fit.get("tau"),
                "fit_r_squared": fit.get("r_squared"),
                "responsivity_ppm": None,
            }
            r_data["by_temperature"][temp_key] = t_data
            r_data["f0_vs_T_hz"].append(f0_hz)

            # 保存 traces 到 npz
            if "freq_fit" in fit and "s21_fit" in fit:
                trace_key = f"{rname}/T{temp}/freq_hz"
                npz_arrays[trace_key] = fit["freq_fit"]
                npz_arrays[f"{rname}/T{temp}/s21_db"] = 20 * np.log10(np.abs(fit["s21_fit"]))
                npz_arrays[f"{rname}/T{temp}/s21_complex"] = np.column_stack([
                    np.real(fit["s21_fit"]), np.imag(fit["s21_fit"])
                ])

            # 保存归一化 IQ 数据 + 拟合曲线 (去除基线, 保留纯谐振圆)
            if "resonator_obj" in fit:
                norm = compute_normalized_iq(fit["resonator_obj"])
                if norm is not None:
                    npz_arrays[f"{rname}/T{temp}/iq_norm"] = norm["iq_data"]
                    npz_arrays[f"{rname}/T{temp}/iq_fit_norm"] = norm["iq_fit"]

            print(f"    T={temp}K: f0={f0_hz/1e9:.6f} GHz"
                  + (f", Qi={t_data['qi']:.0f}" if t_data['qi'] else ""))

        by_resonator[rname] = r_data

    # 写入 fit_results.json (完整拟合参数)
    fit_results = {
        "backend": backend,
        "fit_span_hz": fit_span,
        "parameters_available": ["f0_hz", "qi", "qc", "ql", "phi", "a", "alpha", "tau"],
        "by_resonator": by_resonator,
        "traces": "traces.npz",
    }
    write_json(data_med_dir, "fit_results.json", fit_results)

    # 写入 selected_resonances.json (IQ 拟合后的精确 f0)
    selected_resonances = {
        "n_resonators": n_resonators,
        "by_temperature": {},
        "by_resonator": {},
    }
    for rname, rdata in by_resonator.items():
        selected_resonances["by_resonator"][rname] = {}
        for temp_key, tdata in rdata["by_temperature"].items():
            f0 = tdata.get("f0_hz")
            if f0 is not None:
                selected_resonances["by_resonator"][rname][temp_key] = f0
            if temp_key not in selected_resonances["by_temperature"]:
                selected_resonances["by_temperature"][temp_key] = []
            selected_resonances["by_temperature"][temp_key].append(f0 if f0 is not None else None)
    write_json(data_med_dir, "selected_resonances.json", selected_resonances)

    # 写入 traces.npz
    npz_path = Path(data_med_dir) / "traces.npz"
    write_npz(npz_path, **npz_arrays)

    # ================= 激光维度拟合 + resposition 归档 =================
    # fit_results.json / selected_resonances.json 保持 0mW 口径不变（下游 plot
    # 插件依赖该结构）。激光维度拟合的成果只落到 resposition 工作副本 + 拟合函数。
    _archive_resposition(source, scan, initial_guess, by_resonator, config)

    n_fit = sum(1 for r in by_resonator.values() for v in r["by_temperature"].values() if v.get("f0_hz"))
    print(f"\n[step4_fit] 完成 {n_fit} 次拟合 -> selected_resonances.json + fit_results.json + traces.npz")


def _archive_resposition(source, scan, initial_guess, by_resonator, config):
    """激光维度拟合 + resposition 归档 + f(T,P) 拟合函数写入。

    0mW 的 f0 直接复用主循环拟合结果（by_resonator）；激光功率 >0 的 f0 用
    initial_guess 里的 by_laser 初值做 IQ 拟合。每个激光功率写一份 resposition
    记录，最后用全部 (T, P, f0) 点联合拟合 f(T,P) 模型并写入 fit__{chip_id}.txt。
    """
    source = Path(source)
    chip = it.load_chip_json(str(source)) or {}
    chip_dir = it.resolve_chip_dir(source)
    if chip_dir is None:
        # 无 chip.json（扁平数据集未补 sidecar）：退化为在数据集目录下建 resposition
        chip_dir = source
    chip_id = chip.get("chip_id") or chip_dir.name
    run_id = source.name

    n_res = len(by_resonator)
    rnames = [f"R{i+1}" for i in range(n_res)]
    structure = scan["structure_type"]
    fit_span = config.get("fit_span_hz", 50e6) if config else 50e6

    backend = "scraps"
    try:
        import scraps  # noqa: F401
    except ImportError:
        backend = "dataprocess"

    def _actual_temp(temp_key, laser):
        if laser == 0:
            return initial_guess.get("actual_temps_k", {}).get(str(temp_key))
        return (initial_guess.get("laser_temps_k", {})
                .get(str(laser), {}).get(str(temp_key)))

    # ---- 收集每个激光功率的 (T_actual, f0_per_mode, f_initial_per_mode) ----
    records = {}          # laser -> record dict
    all_T = []            # 展开成 (n_point, n_mode) 用于联合 f(T,P) 拟合
    all_P = []
    all_F = []

    by_laser = initial_guess.get("by_laser", {})
    laser_powers = [0] + [int(p) for p in scan.get("laser_powers_mw", []) if int(p) != 0]

    for laser in laser_powers:
        f0_cols = {r: [] for r in rnames}
        f_ini_cols = {r: [] for r in rnames}
        T_actual_list = []
        status_list = []

        for temp in scan["temperatures"]:
            temp_key = str(temp)
            init_freqs = None
            if laser == 0:
                init_freqs = initial_guess.get("by_temperature", {}).get(temp_key)
            else:
                init_freqs = by_laser.get(str(laser), {}).get("by_temperature", {}).get(temp_key)

            # 0mW 直接用主循环拟合结果；>0 用 by_laser 初值做 IQ 拟合
            if laser == 0:
                fitted = [by_resonator[r].get("by_temperature", {}).get(temp_key, {}).get("f0_hz")
                          for r in rnames]
            else:
                fitted = [None] * n_res
                if init_freqs:
                    for ri, r in enumerate(rnames):
                        ini = init_freqs[ri] if ri < len(init_freqs) else None
                        if ini is None:
                            continue
                        path = find_s2p(source, temp, -25, laser, structure=structure)
                        if path is None:
                            continue
                        freq, s21 = load_s_param(str(path))
                        try:
                            fit = fit_resonance(freq, s21, ini, span=fit_span,
                                                temp=temp, pwr=25, backend=backend)
                            fitted[ri] = fit.get("f0_hz")
                        except Exception:
                            fitted[ri] = None

            actual = _actual_temp(temp_key, laser)
            if actual is None:
                actual = float(temp)
            T_actual_list.append(float(actual))
            row_f0 = []
            for ri, r in enumerate(rnames):
                f0 = fitted[ri]
                f0_cols[r].append(float(f0) if f0 else None)
                row_f0.append(float(f0) if f0 else np.nan)
                f_ini_cols[r].append((init_freqs[ri] if init_freqs and ri < len(init_freqs)
                                      else None))
            status_list.append([("ok" if f0 is not None else "lost") for f0 in fitted])
            all_T.append(float(actual))
            all_P.append(float(laser))
            all_F.append(row_f0)

        record = {
            "chip_id": chip_id,
            "run_id": str(run_id),
            "laser_mw": int(laser),
            "schema_version": 1,
            "temperatures_k": T_actual_list,
            "target_temperatures_k": [int(t) for t in scan["temperatures"]],
            "by_resonator": {r: f0_cols[r] for r in rnames},
            "f_initial_hz": {r: f_ini_cols[r] for r in rnames},
            "status": {r: [status_list[ti][ri] for ti in range(len(T_actual_list))]
                       for ri, r in enumerate(rnames)},
        }
        records[laser] = record
        it.save_res_record(chip_dir, run_id, laser, record)

    # ---- 联合拟合 f(T,P) + 写入拟合函数 ----
    all_T = np.asarray(all_T, float)
    all_P = np.asarray(all_P, float)
    all_F = np.asarray(all_F, float)
    # 至少要有激光维度数据（含非零 P）才拟合 kappa，否则退化为纯温度
    has_laser = bool(np.any(all_P > 0))
    try:
        fit = tm.fit_global(all_T, all_F, tc_k=(chip.get("tc_k") or 88.6),
                            laser_mw=all_P if has_laser else None,
                            fit_kappa=has_laser)
        it.save_fit_txt(chip_dir, chip_id, tm.to_dict(fit))
        print(f"[step4_fit] resposition 归档完成: {len(records)} 个激光功率, "
              f"f(T,P) rms={fit['rms_ppm']:.0f} ppm" +
              (f", kappa={np.asarray(fit['kappa']).round(6).tolist()}" if has_laser else ""))
    except ValueError as e:
        print(f"[step4_fit] 警告: f(T,P) 拟合失败，未写拟合函数: {e}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    main(str(med_dir), str(source))
