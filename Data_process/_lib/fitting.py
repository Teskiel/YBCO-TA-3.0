# _lib/fitting.py — 双后端 IQ 拟合
"""谐振器 IQ 拟合: scraps (cmplxIQ) 或纯 dataprocess (-3dB BW 估计)。

所有函数都不直接依赖 scraps — import 在函数内部延迟加载,
scraps 不可用时 backend="scraps" 抛出明确的 ImportError。
"""
import numpy as np


def finite_diff_extrapolate(f0_history):
    """有限差分频率外推。

    根据历史点数量自动选择外推阶数:
      0 个 → 0.0
      1 个 → 返回该值
      2 个 → 1 阶差分
      3 个 → 2 阶差分
      4 个 → 3 阶差分
      5+ 个 → 4 阶差分 (使用最近 5 个点)

    Args:
        f0_history: 历史拟合 f0 值列表 (Hz), 按温度升序

    Returns:
        外推的下一个 f0 值 (Hz)
    """
    if not f0_history:
        return 0.0

    history = list(f0_history)
    n = len(history)

    if n == 1:
        return history[0]

    # 使用最多 5 个最近点
    pts = history[-5:] if n >= 5 else history
    m = len(pts)

    if m == 2:
        df = pts[1] - pts[0]
        return pts[1] + df
    elif m == 3:
        d1 = pts[1] - pts[0]
        d2 = pts[2] - pts[1]
        dd = d2 - d1
        return pts[2] + d2 + dd
    elif m == 4:
        d1 = pts[1] - pts[0]
        d2 = pts[2] - pts[1]
        d3 = pts[3] - pts[2]
        dd1 = d2 - d1
        dd2 = d3 - d2
        ddd = dd2 - dd1
        return pts[3] + d3 + dd2 + ddd
    else:  # m == 5
        d1 = pts[1] - pts[0]
        d2 = pts[2] - pts[1]
        d3 = pts[3] - pts[2]
        d4 = pts[4] - pts[3]
        dd1 = d2 - d1
        dd2 = d3 - d2
        dd3 = d4 - d3
        ddd1 = dd2 - dd1
        ddd2 = dd3 - dd2
        dddd = ddd2 - ddd1
        return pts[4] + d4 + dd3 + ddd2 + dddd


def fit_resonance(freq, s21, resfreq, span=20e6, temp=0, pwr=0, backend="scraps"):
    """统一拟合接口 — 双后端。

    backend="scraps":
        使用 scraps.resonator.Resonator + cmplxIQ_fit 做完整复平面拟合。
        返回 dict: f0_hz, qi, qc, ql, phi, a, alpha, tau, r_squared, resonator_obj

    backend="dataprocess":
        使用 find_true_resonances + -3dB 带宽估计。
        返回 dict: f0_hz, dip_db, bw_hz, ql

    Args:
        freq: 频率数组 (Hz)
        s21: 复 S21
        resfreq: 初始谐振频率猜测值 (Hz)
        span: 拟合窗口宽度 (Hz), 默认 20 MHz
        temp: 温度标签
        pwr: 功率标签
        backend: "scraps" | "dataprocess"

    Returns:
        dict — 拟合参数 (键取决于 backend)
    """
    df = freq[1] - freq[0]
    index = np.argmin(np.abs(freq - resfreq))
    count = round(span / df)

    idx_start = max(0, index - count)
    idx_stop = min(len(freq), index + count)

    freq_cut = freq[idx_start:idx_stop]
    s21_cut = s21[idx_start:idx_stop]

    # 重新定位到实际谷底
    idx_min = np.argmin(np.abs(s21_cut))
    idx_min_all = idx_start + idx_min
    idx_fit_start = max(0, idx_min_all - count)
    idx_fit_stop = min(len(freq), idx_min_all + count)

    freq_fit = freq[idx_fit_start:idx_fit_stop]
    s21_fit = s21[idx_fit_start:idx_fit_stop]

    if backend == "scraps":
        try:
            import scraps.resonator as scr
            from scraps.fitsS21 import cmplxIQ_fit, cmplxIQ_params
        except ImportError:
            raise ImportError(
                "scraps package not available. Use backend='dataprocess' instead."
            )

        res = scr.Resonator(
            f"R_temp{temp}_pwr{pwr}", temp, pwr,
            freq_fit, np.real(s21_fit), np.imag(s21_fit)
        )
        res.load_params(cmplxIQ_params)
        res.do_lmfit(cmplxIQ_fit)

        # scrapps stores fitted values in res.lmfit_labels + res.lmfit_vals,
        # NOT as direct attributes (f0, Qi, etc. do not exist on the object).
        try:
            label_to_val = dict(zip(res.lmfit_labels, res.lmfit_vals))

            f0 = label_to_val.get("f0")
            qi = label_to_val.get("qi")
            qc = label_to_val.get("qc")
            ql = (qi * qc / (qi + qc)) if (qi and qc and (qi + qc) > 0) else None

            # Compute r_squared from lmfit residuals if available
            r_squared = None
            if hasattr(res, "lmfit_result") and res.lmfit_result:
                fit_data = res.lmfit_result.get("default", {})
                lmfit_res = fit_data.get("result")
                if lmfit_res is not None:
                    try:
                        ss_res = np.sum(lmfit_res.residual ** 2)
                        ss_tot = np.sum(
                            (np.real(s21_fit) - np.mean(np.real(s21_fit))) ** 2
                            + (np.imag(s21_fit) - np.mean(np.imag(s21_fit))) ** 2
                        )
                        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else None
                    except Exception:
                        r_squared = None

            return {
                "f0_hz": float(f0) if f0 is not None else None,
                "qi": float(qi) if qi is not None else None,
                "qc": float(qc) if qc is not None else None,
                "ql": float(ql) if ql is not None else None,
                "phi": None,
                "a": None,
                "alpha": None,
                "tau": None,
                "r_squared": float(r_squared) if r_squared is not None else None,
                "resonator_obj": res,
                "freq_fit": freq_fit,
                "s21_fit": s21_fit,
            }
        except Exception:
            return {
                "f0_hz": None, "qi": None, "qc": None, "ql": None,
                "phi": None, "a": None, "alpha": None, "tau": None,
                "r_squared": None, "error": "scraps fit failed to extract parameters",
                "freq_fit": freq_fit, "s21_fit": s21_fit,
            }

    elif backend == "dataprocess":
        from _lib.resonance import find_true_resonances

        # Recompute idx_min relative to freq_fit window (not freq_cut)
        idx_min_fit = np.argmin(np.abs(s21_fit))

        peaks, _, _ = find_true_resonances(
            freq_fit, s21_fit, min_prominence=0.5, distance=5,
            phase_window=5, phase_diff_snr_threshold=0.1,
            plot=False, n_resonances=1,
            hint_freqs=[freq_fit[idx_min_fit]],
        )

        if not peaks:
            return {
                "f0_hz": None, "dip_db": None, "bw_hz": None, "ql": None,
                "error": "no peak found in fit window"
            }

        peak = peaks[0]
        f0 = peak["frequency"]

        # -3dB 带宽估计
        transmission = 20 * np.log10(np.abs(s21_fit))
        dip_val = peak["transmission"]
        half_dip = dip_val + 3.0  # -3dB from minimum

        above_half = np.where(transmission >= half_dip)[0]
        peak_idx = peak["index"]
        left = above_half[above_half < peak_idx]
        right = above_half[above_half > peak_idx]

        if len(left) > 0 and len(right) > 0:
            bw_hz = freq_fit[right[0]] - freq_fit[left[-1]]
        else:
            bw_hz = None

        ql = f0 / bw_hz if bw_hz and bw_hz > 0 else None

        return {
            "f0_hz": float(f0),
            "dip_db": float(dip_val),
            "bw_hz": float(bw_hz) if bw_hz else None,
            "ql": float(ql) if ql else None,
        }

    else:
        raise ValueError(f"Unknown backend: {backend}. Use 'scraps' or 'dataprocess'.")


def compute_normalized_iq(res):
    """从拟合后的 Resonator 对象计算归一化 IQ 数据。

    归一化 = 去除基线 (gain * pgain) 和 DC 偏移，
    只保留谐振器本征 hanger 响应，在 IQ 平面上呈现为标准谐振圆。

    Args:
        res: scraps Resonator 对象 (已完成 do_lmfit)

    Returns:
        dict: {"iq_data": (N,2) array, "iq_fit": (N,2) array}
              iq_data  — 归一化后的测量数据 (基线已除)
              iq_fit   — 归一化后的拟合模型 (纯 hanger 圆)
        如果计算失败返回 None
    """
    try:
        from scraps.fitsS21.hanger_resonator import cmplx_hanger

        # 获取拟合参数值
        label_to_val = dict(zip(res.lmfit_labels, res.lmfit_vals))

        f0 = label_to_val.get("f0")
        df = label_to_val.get("df", 0.0)
        qc = label_to_val.get("qc")
        qi = label_to_val.get("qi")
        gain0 = label_to_val.get("gain0", 1.0)
        gain1 = label_to_val.get("gain1", 0.0)
        gain2 = label_to_val.get("gain2", 0.0)
        pgain0 = label_to_val.get("pgain0", 0.0)
        pgain1 = label_to_val.get("pgain1", 0.0)
        pgain2 = label_to_val.get("pgain2", 0.0)
        Ioffset = label_to_val.get("Ioffset", 0.0)
        Qoffset = label_to_val.get("Qoffset", 0.0)

        # 计算基线 total_gain(f) = gain(f) * exp(j * phase_gain(f))
        fm = res.freq[int(np.round((len(res.freq) - 1) / 2.0))]
        ffm = (res.freq - fm) / fm
        gain = gain0 + gain1 * ffm + 0.5 * gain2 * ffm ** 2
        pgain = np.exp(1j * (pgain0 + pgain1 * ffm + 0.5 * pgain2 * ffm ** 2))
        total_gain = gain * pgain
        offset = complex(Ioffset, Qoffset)

        # 归一化测量数据: (raw_S21 - offset) / total_gain
        raw_cplx = res.I + 1j * res.Q
        norm_data = (raw_cplx - offset) / total_gain
        iq_data = np.column_stack([np.real(norm_data), np.imag(norm_data)])

        # 归一化拟合模型 = 纯 cmplx_hanger (不含基线, 不含 offset)
        if f0 and qc and qi:
            q0 = 1.0 / (1.0 / qi + 1.0 / qc)
            hanger_cplx = cmplx_hanger(res.freq, f0, df, qc, q0)
            iq_fit = np.column_stack([np.real(hanger_cplx), np.imag(hanger_cplx)])
        else:
            # 参数缺失时用 resultI/resultQ 除以基线作为近似
            result_cplx = res.resultI + 1j * res.resultQ
            norm_fit = (result_cplx - offset) / total_gain
            iq_fit = np.column_stack([np.real(norm_fit), np.imag(norm_fit)])

        return {"iq_data": iq_data, "iq_fit": iq_fit}

    except Exception as e:
        print(f"    [compute_normalized_iq] WARNING: 归一化计算失败: {e}")
        return None


def extract_params_from_fit(fit_result, backend="scraps"):
    """从拟合结果中提取标准化参数 dict。

    确保不同后端输出统一格式的参数列表。
    """
    params = {
        "f0_hz": fit_result.get("f0_hz"),
        "qi": fit_result.get("qi"),
        "qc": fit_result.get("qc"),
        "ql": fit_result.get("ql"),
        "phi": fit_result.get("phi"),
        "a": fit_result.get("a"),
        "alpha": fit_result.get("alpha"),
        "tau": fit_result.get("tau"),
        "r_squared": fit_result.get("r_squared"),
        "dip_db": fit_result.get("dip_db"),
        "bw_hz": fit_result.get("bw_hz"),
        "error": fit_result.get("error"),
    }
    return params
