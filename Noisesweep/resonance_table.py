# -*- coding: utf-8 -*-
"""
Resonance tracking table reader
===============================
统一读取 YBCO_TA/Data_process 产出的谐振追踪文件，提供：

  - .txt   → `resonance_table.txt`：`np.loadtxt(comments="#")`，第 0 列为
            温度 K，1..N 列为各谐振频率 GHz。
  - .json  → `fit_results.json` / `selected_resonances.json`：按
            `by_resonator["R{i}"]` 结构读取 f0_hz；fit_results 另含
            `responsivity_vs_T_ppm_per_mw`（**当前数据集为空列表**，
            此时 responsivity 返回 None，激光频移预测自动退化）。

提供函数：
  - `reference_frequency_hz(temps, freqs, target_k, idx, manual_hz=None)`
    追踪表温度插值（两端线性外推），manual_hz 优先。
  - `responsivity_ppm_per_mw(temps, resp, target_k, idx)` 同法插值，
    无数据返回 None。
  - `predict_laser_shift(f_ref_hz, resp_ppm_per_mw, power_mw)` 激光频移预测：
    f_pred = f_ref × (1 + resp × P × 1e-6)。resp 为 None 时原样返回。
  - `resolve_reference_frequency(per_res_hz, global_hz, table_hz)` 参考频率
    回退优先级：每谐振手动值 > 全局手动值 > 追踪表插值。

数据结构：`(temps_k, freq_hz[n_res, n_temp])`，温度严格递增。
"""

import json
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

__all__ = [
    "load_resonance_table",
    "ResonanceTable",
    "reference_frequency_hz",
    "responsivity_ppm_per_mw",
    "predict_laser_shift",
    "resolve_reference_frequency",
]


# =========================================================================
# 解析器
# =========================================================================

def _validate_temps(temps_k: np.ndarray) -> np.ndarray:
    """校验温度严格递增且为正数，否则抛错。"""
    temps_k = np.asarray(temps_k, dtype=float).reshape(-1)
    if temps_k.size == 0:
        raise ValueError("共振追踪表没有温度数据")
    if np.any(temps_k <= 0):
        raise ValueError(f"共振追踪表出现非正温度: {temps_k.tolist()}")
    if np.any(np.diff(temps_k) <= 0):
        raise ValueError(f"共振追踪表温度必须严格递增: {temps_k.tolist()}")
    return temps_k


def _load_txt(path: Path) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """resonance_table.txt → (temps_k, freq_hz[n_res,n_temp], None)。"""
    data = np.loadtxt(path, comments="#", ndmin=2)
    if data.ndim != 2 or data.shape[0] < 1 or data.shape[1] < 2:
        raise ValueError(
            f"共振追踪表 {path} 格式无效：需要至少 2 列（温度 + ≥1 谐振频率），"
            f"实际 shape={data.shape}"
        )
    temps_k = _validate_temps(data[:, 0])
    freq_ghz = data[:, 1:]
    if np.any(freq_ghz <= 0):
        raise ValueError(f"共振追踪表 {path} 出现非正频率")
    freq_hz = freq_ghz.T * 1e9  # (n_res, n_temp)
    return temps_k, freq_hz, None


def _norm_key(s) -> str:
    """把 '6' / '6.0' / ' 6 ' 统一为 '6'，用于 json 温度键匹配。"""
    return str(float(s)) if isinstance(s, (int, float)) else str(s).strip()


def _sorted_temp_keys(by_temperature: dict) -> List[str]:
    """按数值排序 json 的温度键（str），返回与 temps_k 对齐的键列表。"""
    keys = list(by_temperature.keys())
    try:
        keys.sort(key=lambda k: float(k))
    except (TypeError, ValueError):
        raise ValueError(f"无法解析 json 温度键: {keys!r}")
    return keys


def _load_json(path: Path) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """fit_results.json / selected_resonances.json → (temps, freq_hz, resp)。"""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)

    by_res = doc.get("by_resonator")
    if not isinstance(by_res, dict) or not by_res:
        raise ValueError(f"{path} 缺少 by_resonator（非空字典）")

    # 各谐振器是否都有 by_temperature 子结构（fit_results 样式）？
    nested = all(
        isinstance(v, dict) and isinstance(v.get("by_temperature"), dict)
        for v in by_res.values()
    )

    temps_keys: Optional[List[str]] = None
    freq_by_res: List[np.ndarray] = []
    resp_by_res: Optional[List[Optional[np.ndarray]]] = []

    for name, val in by_res.items():
        if nested:
            bt = val["by_temperature"]
            keys = _sorted_temp_keys(bt)
            freqs = np.array(
                [float(bt[k]["f0_hz"]) for k in keys], dtype=float
            )
            resp = val.get("responsivity_vs_T_ppm_per_mw")
            if isinstance(resp, list) and len(resp) == len(keys) \
                    and all(r is not None for r in resp):
                resp_arr = np.array([float(r) for r in resp], dtype=float)
            else:
                resp_arr = None
        else:
            # selected_resonances 样式：by_resonator["R1"][str(T)] = f0_hz
            keys = _sorted_temp_keys(val)
            freqs = np.array([float(val[k]) for k in keys], dtype=float)
            resp_arr = None

        if temps_keys is None:
            temps_keys = keys
        elif keys != temps_keys:
            raise ValueError(f"{path} 各谐振器温度键不一致（{name}）")

        freq_by_res.append(freqs)
        resp_by_res.append(resp_arr)

    temps_k = _validate_temps(np.array([float(k) for k in temps_keys]))
    freq_hz = np.stack(freq_by_res, axis=0)  # (n_res, n_temp)

    # 只有全部谐振器都有 responsivity 才作为数组返回，否则视为无数据
    resp_out: Optional[np.ndarray] = None
    if all(r is not None for r in resp_by_res):
        resp_out = np.stack(resp_by_res, axis=0)

    return temps_k, freq_hz, resp_out


# =========================================================================
# 公开 API
# =========================================================================

class ResonanceTable:
    """一组谐振器的温度追踪数据。

    Attributes:
        temps_k: 温度轴 (n_temp,)，严格递增。
        freq_hz: (n_res, n_temp) 谐振频率，Hz。
        resp_ppm_per_mw: (n_res, n_temp) 光响应率（ppm/mW）或 None。
        resonator_names: 谐振器名称列表，如 ['res1', ...]。
        n_res, n_temp: 尺寸。
    """

    def __init__(self, temps_k, freq_hz, resp_ppm_per_mw=None,
                 resonator_names: Optional[List[str]] = None):
        self.temps_k = _validate_temps(temps_k)
        self.freq_hz = np.asarray(freq_hz, dtype=float)
        if self.freq_hz.ndim != 2 or self.freq_hz.shape[1] != self.temps_k.size:
            raise ValueError(
                f"freq_hz shape={self.freq_hz.shape} 与 temps size={self.temps_k.size} 不匹配"
            )
        if np.any(self.freq_hz <= 0):
            raise ValueError("freq_hz 出现非正频率")
        self.resp_ppm_per_mw = (
            None if resp_ppm_per_mw is None
            else np.asarray(resp_ppm_per_mw, dtype=float)
        )
        if self.resp_ppm_per_mw is not None \
                and self.resp_ppm_per_mw.shape != self.freq_hz.shape:
            raise ValueError("resp_ppm_per_mw 形状必须与 freq_hz 一致")

        n_res = self.freq_hz.shape[0]
        if resonator_names is None:
            self.resonator_names = [f"res{i + 1}" for i in range(n_res)]
        else:
            names = list(resonator_names)
            if len(names) != n_res:
                raise ValueError(
                    f"resonator_names 长度 {len(names)} != 谐振器数 {n_res}"
                )
            self.resonator_names = names

    @property
    def n_res(self) -> int:
        return self.freq_hz.shape[0]

    @property
    def n_temp(self) -> int:
        return self.freq_hz.shape[1]

    def resonator_index(self, name) -> int:
        """把 'res1' / 'r1' / 'R1' / 'resonator1' 归一化到 0-based 索引。"""
        s = str(name).strip().lower()
        s = s.replace("resonator", "").replace("res", "").replace("r", "")
        if not s.isdigit():
            raise ValueError(f"无法识别谐振器名称: {name!r}")
        idx = int(s) - 1
        if not 0 <= idx < self.n_res:
            raise ValueError(f"谐振器索引 {idx} 越界（共 {self.n_res} 个）")
        return idx

    def reference_frequency_hz(self, target_k: float, res_idx: int,
                               manual_hz: Optional[float] = None) -> float:
        """追踪表插值的参考频率，manual_hz 优先。"""
        return reference_frequency_hz(
            self.temps_k, self.freq_hz[res_idx], target_k, manual_hz=manual_hz
        )

    def responsivity_ppm_per_mw(self, target_k: float,
                                res_idx: int) -> Optional[float]:
        """该谐振器在 target_k 处的光响应率插值；无数据返回 None。"""
        if self.resp_ppm_per_mw is None:
            return None
        return responsivity_ppm_per_mw(
            self.temps_k, self.resp_ppm_per_mw[res_idx], target_k
        )

    def __repr__(self) -> str:
        return (f"ResonanceTable(n_res={self.n_res}, n_temp={self.n_temp}, "
                f"temps_k=[{self.temps_k[0]:.1f}..{self.temps_k[-1]:.1f}], "
                f"has_responsivity={self.resp_ppm_per_mw is not None})")


def load_resonance_table(path) -> ResonanceTable:
    """按扩展名读取共振追踪文件，返回 ResonanceTable。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"共振追踪文件不存在: {path}")

    if path.suffix.lower() == ".json":
        temps_k, freq_hz, resp = _load_json(path)
        return ResonanceTable(temps_k, freq_hz, resp)
    elif path.suffix.lower() == ".txt":
        temps_k, freq_hz, resp = _load_txt(path)
        return ResonanceTable(temps_k, freq_hz, resp)
    else:
        raise ValueError(
            f"不支持的共振追踪文件扩展名: {path.suffix!r}（仅支持 .txt / .json）"
        )


def reference_frequency_hz(temps_k, freq_hz_res, target_k: float,
                           manual_hz: Optional[float] = None) -> float:
    """单谐振器在 target_k 处的参考频率。

    manual_hz 非 None 时直接返回（手动值优先）；否则对追踪表做线性插值，
    超出温度范围的两端用最外侧两点的斜率线性外推。
    """
    if manual_hz is not None:
        return float(manual_hz)

    temps = np.asarray(temps_k, dtype=float)
    freqs = np.asarray(freq_hz_res, dtype=float)
    if temps.ndim != 1 or freqs.ndim != 1 or temps.size != freqs.size:
        raise ValueError("temps 与 freqs 必须是一维且等长")
    if temps.size < 2:
        raise ValueError("插值至少需要 2 个温度点")

    t = float(target_k)
    if t < temps[0]:
        slope = (freqs[1] - freqs[0]) / (temps[1] - temps[0])
        return float(freqs[0] + slope * (t - temps[0]))
    if t > temps[-1]:
        slope = (freqs[-1] - freqs[-2]) / (temps[-1] - temps[-2])
        return float(freqs[-1] + slope * (t - temps[-1]))
    return float(np.interp(t, temps, freqs))


def responsivity_ppm_per_mw(temps_k, resp_res, target_k: float) -> Optional[float]:
    """单谐振器在 target_k 处的光响应率插值；resp 数据缺失返回 None。"""
    if resp_res is None:
        return None
    resp = np.asarray(resp_res, dtype=float)
    if resp.size == 0:
        return None
    # 插值逻辑与 reference_frequency_hz 相同
    result = reference_frequency_hz(temps_k, resp, target_k, manual_hz=None)
    return result


def predict_laser_shift(f_ref_hz: float, resp_ppm_per_mw: Optional[float],
                        power_mw: float) -> float:
    """激光功率导致的参考频率移动预测。

    f_pred = f_ref × (1 + resp_ppm_per_mW × P_mW × 1e-6)
    resp 为 None（无 responsivity 数据）时原样返回 f_ref。
    """
    if resp_ppm_per_mw is None:
        return float(f_ref_hz)
    shift_ppm = float(resp_ppm_per_mw) * float(power_mw)
    return float(f_ref_hz) * (1.0 + shift_ppm * 1e-6)


def resolve_reference_frequency(per_res_hz: Optional[float],
                                global_hz: Optional[float],
                                table_hz: float) -> float:
    """参考频率回退优先级：每谐振手动值 > 全局手动值 > 追踪表插值。"""
    if per_res_hz is not None:
        return float(per_res_hz)
    if global_hz is not None:
        return float(global_hz)
    return float(table_hz)
