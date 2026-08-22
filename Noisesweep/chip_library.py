# -*- coding: utf-8 -*-
"""芯片级谐振标定库薄壳 — 封装 YBCO_TA/Data_process 的 thermal_model + io_thermal。

只做纯计算封装，不 import matplotlib / PyQt。物理模型见
`thermal_model.f_model` 的 docstring：

    f_n(T) = f_n(0) · [ (1 − α_n) + α_n / (1 − (T/Tc)^p) ] ^ (−1/2)

关键设计：**import 失败 / 标定缺失 / 预测异常一律返回可用回退信号（None），
绝不抛异常** —— 由调用方（noisesweep）据此回退到追踪表 / 手动频率并告警，
不中断测量。这是「标定库作为增强/校验，而非硬开关」的落地。

用法：
    import chip_library as cl
    if cl.available():
        f_pred, half = cl.predict_resonance_frequencies(
            chip_id="YBCO_unknown-batch", run_id="20260609-0624__6-80K__full",
            T_k=77.0)
"""

import sys
from pathlib import Path

# 3.0 整合：Data_process 是 Noisesweep 的兄弟目录（../Data_process）。
# 保留 data_process_dir 形参覆盖，便于用户在其它布局下显式指定。
DEFAULT_DATA_PROCESS_DIR = Path(__file__).resolve().parent.parent / "Data_process"

_thermal = None
_io_thermal = None
_import_error = None
_loaded_dir = None


def _load(data_process_dir=None) -> bool:
    """惰性导入 _lib（仅一次）。成功返回 True。"""
    global _thermal, _io_thermal, _import_error, _loaded_dir
    dp = Path(data_process_dir or DEFAULT_DATA_PROCESS_DIR)
    if _loaded_dir == str(dp):
        return _thermal is not None
    dp_str = str(dp)
    if dp_str not in sys.path:
        sys.path.insert(0, dp_str)
    try:
        from _lib import io_thermal, thermal_model  # noqa: E402
        _thermal = thermal_model
        _io_thermal = io_thermal
        _loaded_dir = dp_str
        _import_error = None
        return True
    except Exception as exc:  # 缺 scipy / _lib 不在 / 其它 —— 降级不崩
        _thermal = None
        _io_thermal = None
        _import_error = exc
        return False


def available(data_process_dir=None) -> bool:
    return _load(data_process_dir)


def import_error() -> str:
    return str(_import_error) if _import_error else None


def list_calibrations(data_process_dir=None):
    """列出可用的标定存档：[{chip_id, run_id, path}]。import 失败返回 []。"""
    if not _load(data_process_dir):
        return []
    out = []
    try:
        for p in _io_thermal.list_calibrations():
            name = p.name[:-5] if p.name.endswith(".json") else p.name
            if "__" in name:
                chip_id, run_id = name.split("__", 1)
            else:
                chip_id, run_id = name, ""
            out.append({"chip_id": chip_id, "run_id": run_id, "path": str(p)})
    except Exception:
        return []
    return out


def load_fit(chip_id=None, run_id=None, path=None, data_process_dir=None):
    """加载标定并还原 fit dict（可直接 predict）。失败返回 None。"""
    if not _load(data_process_dir):
        return None
    try:
        cal = _io_thermal.load_calibration(chip_id=chip_id, run_id=run_id,
                                           path=path)
        if cal is None or "fit" not in cal:
            return None
        return _thermal.from_dict(cal["fit"])
    except Exception:
        return None


def n_modes(fit=None, chip_id=None, run_id=None, path=None,
            data_process_dir=None):
    """标定里的谐振器数；失败返回 0。"""
    if fit is None:
        fit = load_fit(chip_id=chip_id, run_id=run_id, path=path,
                       data_process_dir=data_process_dir)
    if fit is None:
        return 0
    try:
        return int(fit["_layout"]["n_mode"])
    except Exception:
        return len(fit["f0_hz"])


def predict_resonance_frequencies(chip_id=None, run_id=None, T_k=0.0,
                                  laser_mw=0.0, path=None,
                                  data_process_dir=None):
    """预测各谐振器在 T_k 处的谐振频率。

    Returns:
        (f_pred_hz, half_width_hz) 均为 (n_mode,) ndarray；失败返回 (None, None)。
        half_width_hz 是 prediction_window 的经验半宽（含历史单步误差），
        可作宽扫带宽下界参考，不是硬约束。
    """
    fit = load_fit(chip_id=chip_id, run_id=run_id, path=path,
                   data_process_dir=data_process_dir)
    if fit is None:
        return None, None
    try:
        f_pred, half = _thermal.prediction_window(fit, float(T_k),
                                                  laser_mw=float(laser_mw))
        return f_pred, half
    except Exception:
        return None, None
