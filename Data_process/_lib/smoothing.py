# _lib/smoothing.py — 三次样条平滑 + MAD 离群值剔除
"""从 OLD compare_resonators.py 提取的 smooth_spline 算法。

纯算法函数，不依赖 pic_std。插件通过 _cfg 读取 smoothing 配置后传入参数。
"""
import numpy as np
from scipy.interpolate import UnivariateSpline
from typing import Optional, Tuple


def smooth_spline(
    x,
    y,
    k: int = 3,
    outlier_mad: float = 5.0,
    n_points: int = 200,
    min_points: int = 5,
    n_iterations: int = 3,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
    """三次样条平滑 + 迭代 MAD 离群点剔除。

    Parameters
    ----------
    x, y : array-like
        输入数据点
    k : int
        样条阶数 (默认 3 = cubic)
    outlier_mad : float
        离群值判定阈值 — 残差 > outlier_mad × MAD 的点被剔除
    n_points : int
        插值输出点数
    min_points : int
        最少需要的有效点数，不足则返回 (None, None, None)
    n_iterations : int
        迭代剔除轮数

    Returns
    -------
    (xs, ys, kept_mask) : (ndarray, ndarray, ndarray) or (None, None, None)
        xs : 平滑插值 x 坐标
        ys : 平滑插值 y 值
        kept_mask : bool 数组，标记原始输入中保留的点
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < min_points:
        return None, None, None

    # 去重排序
    order = np.argsort(x)
    xu, idx = np.unique(x[order], return_index=True)
    yu = y[order][idx]
    if len(xu) < min_points:
        return None, None, None

    # 迭代剔除离群点
    keep = np.ones(len(xu), dtype=bool)
    for _ in range(n_iterations):
        xk = xu[keep]
        yk = yu[keep]
        if len(xk) < max(min_points, 4):
            break
        y_span = np.ptp(yk)
        s = len(xk) * 1.5 * (y_span if y_span > 0 else 1.0)
        k_eff = min(k, len(xk) - 1)
        try:
            spl = UnivariateSpline(xk, yk, s=s, k=k_eff)
        except Exception:
            break
        residuals = np.abs(yk - spl(xk))
        mad = np.median(residuals)
        if mad < 1e-12:
            break
        outliers = residuals > outlier_mad * mad
        if not outliers.any():
            break
        keep[np.where(keep)[0][outliers]] = False

    xk = xu[keep]
    yk = yu[keep]
    if len(xk) < min_points:
        return None, None, None

    y_span = np.ptp(yk)
    s_final = len(xk) * 1.0 * (y_span if y_span > 0 else 1.0)
    k_final = min(k, len(xk) - 1)
    try:
        spl = UnivariateSpline(xk, yk, s=s_final, k=k_final)
    except Exception:
        return None, None, None

    xs = np.linspace(xk.min(), xk.max(), n_points)
    ys = spl(xs)

    # 映射回原始索引
    full_keep = np.zeros(len(x), dtype=bool)
    for i, ok in enumerate(keep):
        if ok:
            full_keep[order[idx[i]]] = True
    return xs, ys, full_keep


def linear_fit_robust(
    x,
    y,
    outlier_mad: float = 5.0,
    min_points: int = 3,
    n_iterations: int = 3,
) -> Tuple[Optional[float], Optional[float], Optional[np.ndarray], Optional[np.ndarray]]:
    """鲁棒线性拟合 + 迭代 MAD 离群点剔除。

    对 Qi(T) 等近似线性关系做直线拟合，自动剔除偏离线性区域的点。

    Parameters
    ----------
    x, y : array-like
        输入数据点
    outlier_mad : float
        离群值判定阈值 — 残差 > outlier_mad × MAD 的点被剔除
    min_points : int
        最少需要的有效点数，不足则返回 (None, None, None, None)
    n_iterations : int
        迭代剔除轮数

    Returns
    -------
    (slope, intercept, kept_mask, fit_x) : (float, float, ndarray, ndarray) or (None, None, None, None)
        slope, intercept : 拟合直线参数 y = slope * x + intercept
        kept_mask : bool 数组，标记原始输入中保留（线性区域）的点
        fit_x : 拟合直线 x 坐标 (min→max of kept points, 50 pts)
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < min_points:
        return None, None, None, None

    # 去重排序
    order = np.argsort(x)
    xu, idx = np.unique(x[order], return_index=True)
    yu = y[order][idx]
    if len(xu) < min_points:
        return None, None, None, None

    keep = np.ones(len(xu), dtype=bool)
    slope, intercept = None, None

    for _ in range(n_iterations):
        xk = xu[keep]
        yk = yu[keep]
        if len(xk) < min_points:
            break
        # 一次线性拟合
        coeffs = np.polyfit(xk, yk, 1)
        slope, intercept = coeffs[0], coeffs[1]
        residuals = np.abs(yk - (slope * xk + intercept))
        mad = np.median(residuals)
        if mad < 1e-12:
            break
        outliers = residuals > outlier_mad * mad
        if not outliers.any():
            break
        keep[np.where(keep)[0][outliers]] = False

    xk = xu[keep]
    yk = yu[keep]
    if len(xk) < min_points:
        return None, None, None, None

    # 最终拟合
    coeffs = np.polyfit(xk, yk, 1)
    slope, intercept = coeffs[0], coeffs[1]

    fit_x = np.linspace(xk.min(), xk.max(), 50)

    # 映射回原始索引
    full_keep = np.zeros(len(x), dtype=bool)
    for i, ok in enumerate(keep):
        if ok:
            full_keep[order[idx[i]]] = True
    return slope, intercept, full_keep, fit_x
