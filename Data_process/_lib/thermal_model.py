# _lib/thermal_model.py — 谐振频率的温度模型：拟合、外推、窗口、峰分配
"""薄膜 KID 谐振频率随温度偏移的物理模型。

物理来源
--------
薄膜极限 (膜厚 d << λ) 下动态电感 L_k ∝ λ²/d，故 L_k(T)/L_k(0) = (λ(T)/λ(0))²。
定义动态电感分数 α_n = L_k(0) / (L_g + L_k(0))，由 f = 1/(2π√((L_g+L_k)C)) 得

    f_n(T) = f_n(0) · [ (1 - α_n) + α_n · (λ(T)/λ(0))² ] ^ (-1/2)

双流体模型给出 (λ(T)/λ(0))² = 1 / (1 - (T/Tc)^p)。

参数结构
--------
    Tc      全局，默认取自 chip.json 的实测值（不自由拟合）
    p       全局共享，自由拟合
    α_n     每模式
    f_n(0)  每模式

全局共享 Tc/p 意味着所有模式的数据共同约束温度形状，这是单模式独立拟合做不到的。

重要经验结论（来自 20260609-0624__6-80K__full 数据集的回测）
--------------------------------------------------------
1. 温度必须用 S2P 文件名里的 actual 实测值。用文件夹名的整数 target 会让
   walk-forward 最坏误差从 12.7 MHz 恶化到 32.5 MHz（全量拟合 rms 447→974 ppm）。
2. 跨段一次性外推（只用 <50K 拟合去预测 50~77K）不可靠，最坏误差 53 MHz 且
   全部同号——那是模型形式误差。单步 walk-forward 才可用（最坏 12.7 MHz）。
3. 给 (λ/λ0)² 增加自由指数 q 反而把 walk-forward 误差翻倍（12.7→22.8 MHz），
   属于过拟合。保持双流体原形，不要加参数。
4. delta method 的 σ 严重低估（3σ 覆盖率仅 30%），因为它只含参数不确定度、
   不含模型形式误差。预测窗口必须用 prediction_window() 的经验自标定，
   不要直接用 3σ。

本模块为纯计算，不做文件 IO、不 import matplotlib/skrf。
文件存取见 _lib/io_thermal.py。
"""
import numpy as np
from scipy.optimize import least_squares, linear_sum_assignment

# 双流体指数的默认初值。经验上 YBCO 落在 1.4~2.0，既非 BCS 的 4 也非常引用的 2。
DEFAULT_P_INIT = 1.85
P_BOUNDS = (0.5, 6.0)
ALPHA_BOUNDS = (1e-4, 0.95)

# 预测窗口的经验自标定参数（依据见模块 docstring 第 4 条）
DEFAULT_N_SIGMA = 3.0
DEFAULT_BETA = 2.0            # 历史最大单步误差的放大倍数
DEFAULT_WINDOW_MIN_HZ = 15e6  # 窗口下限，覆盖首次预测时无历史误差可用的情况

_T_RATIO_MAX = 1.0 - 1e-7     # 防止 T→Tc 时 1/(1-t^p) 溢出

# κ 拟合边界：每 mW 使凝聚态分数下降的斜率。经验值 ~6e-4，5e-2 已是 80 倍裕度。
KAPPA_BOUNDS = (0.0, 0.05)
_RHO_MIN = 1e-6               # ρ_s 下限，防止高 T + 高 P 使凝聚态分数 ≤0 时除法溢出


def rho_s(temps_k, tc_k, p, laser_mw=0.0, kappa=None):
    """凝聚态分数 ρ_s = 1 − (T/Tc)^p − κ·P，含激光准粒子项。

    激光项把功率 P（mW）线性地从凝聚态分数里扣除：激光光致准粒子对超流密度的
    抑制，与温度的热抑制并列。κ=0 或 P=0 时退化为纯双流体形式 1−(T/Tc)^p。

    Parameters
    ----------
    temps_k : float or array (n_temp,)
    tc_k, p : float
    laser_mw : float or array (n_temp,)，每温度点对应的激光功率 (mW)
    kappa : float or array (n_mode,)，每模式的激光响应系数。None 时不含激光项

    Returns
    -------
    ndarray，形状 (n_temp, n_mode)，或在相应输入为标量时折叠维度。
    结果下限截断到 _RHO_MIN，保证 >0。
    """
    t = np.clip(np.asarray(temps_k, dtype=float) / float(tc_k), 0.0, _T_RATIO_MAX)
    scalar_t = np.ndim(temps_k) == 0
    t = np.atleast_1d(t)
    rho = 1.0 - t ** float(p)                       # 纯温度项，(n_temp,) 维
    if kappa is not None:
        P = np.asarray(laser_mw, dtype=float)
        if np.any(P != 0.0):
            k_arr = np.atleast_1d(np.asarray(kappa, dtype=float))   # (n_mode,)
            rho = rho[:, None] - k_arr[None, :] * P[..., None]      # (n_temp, n_mode)
    rho = np.clip(np.asarray(rho, dtype=float), _RHO_MIN, None)
    if scalar_t:
        rho = rho[0]
    return rho


def lambda_ratio_sq(temps_k, tc_k, p):
    """双流体模型的 (λ(T)/λ(0))²  =  1 / (1 - (T/Tc)^p)。

    T >= Tc 处按 _T_RATIO_MAX 截断，返回有限大值而非 inf，以免破坏最小二乘迭代。
    """
    t = np.clip(np.asarray(temps_k, dtype=float) / float(tc_k), 0.0, _T_RATIO_MAX)
    return 1.0 / (1.0 - t ** float(p))


def f_model(temps_k, f0_hz, alpha, tc_k, p, laser_mw=0.0, kappa=None):
    """模型频率 f_n(T, P)。

    Parameters
    ----------
    temps_k : float or array (n_temp,)
    f0_hz, alpha : float or array (n_mode,)
    tc_k, p : float
    laser_mw : float or array (n_temp,)，激光功率 (mW)。默认 0
    kappa : float or array (n_mode,)，激光响应系数。None 时无激光项

    Returns
    -------
    ndarray
        temps_k 标量 / 数组 与 f0 标量 / 数组 任意组合，返回维度相应折叠。
        （标量温度 → 去掉温度维；标量 f0 → 去掉模式维。）

    kappa=None 或 laser_mw=0 时与旧版（纯温度）逐位一致。
    """
    f0_arr = np.atleast_1d(np.asarray(f0_hz, dtype=float))
    alpha_arr = np.atleast_1d(np.asarray(alpha, dtype=float))
    scalar_t = np.ndim(temps_k) == 0
    scalar_m = np.ndim(f0_hz) == 0

    if kappa is None or not np.any(np.asarray(laser_mw, dtype=float) != 0.0):
        # 纯温度路径：保持旧版逐位一致的实现
        lam2 = lambda_ratio_sq(np.atleast_1d(temps_k), tc_k, p)[:, None]  # (n_temp, 1)
        denom = (1.0 - alpha_arr[None, :]) + alpha_arr[None, :] * lam2
    else:
        rho = rho_s(np.atleast_1d(temps_k), tc_k, p, laser_mw=laser_mw, kappa=kappa)
        rho = np.atleast_2d(rho) if rho.ndim < 2 else rho
        denom = (1.0 - alpha_arr[None, :]) + alpha_arr[None, :] / rho
    out = f0_arr[None, :] / np.sqrt(denom)
    if scalar_t:
        out = out[0]
    if scalar_m:
        out = out[..., 0]
    return out


# =========================================================================
# 拟合
# =========================================================================

def fit_global(temps_k, freqs_hz, mask=None, tc_k=None, tc_free=False,
               p_init=None, p_fixed=None, init_from=None,
               laser_mw=None, fit_kappa=False):
    """全局联合拟合：所有模式共享 Tc 与 p，各自拥有 f_n(0)、α_n（可选 κ_n）。

    Parameters
    ----------
    temps_k : array (n_temp,)
        **必须是 actual 实测温度**，与 freqs_hz 的行逐点对应。
    freqs_hz : array (n_temp, n_mode)
        NaN 表示该 (温度, 模式) 缺失。
    mask : array (n_temp, n_mode) of bool, optional
        True = 参与拟合。与 NaN 检查取交集。仅 status=="ok" 的点应置 True。
    tc_k : float
        临界温度。tc_free=False 时固定于此值（推荐：取自 chip.json 实测值）。
    tc_free : bool
        True 则把 Tc 也作为自由参数。默认 False——用户实测值优先。
    p_init : float, optional
        p 的初值，默认 DEFAULT_P_INIT。
    p_fixed : float, optional
        给定则固定 p 不拟合。
    init_from : dict, optional
        既有标定结果，仅用于取初值（p / alpha / kappa），不作为先验约束。
    laser_mw : float or array (n_temp,), optional
        每行对应的激光功率 (mW)。None 时视为纯温度拟合。
    fit_kappa : bool
        True 时额外为每模式拟合 κ_n（激光响应系数）。需配合 laser_mw 使用。

    Returns
    -------
    dict
        tc_k, tc_source, p, alpha, f0_hz, cov, n_points, n_params,
        resid_frac, rms_ppm；fit_kappa=True 时另有 kappa, kappa_source。

    Raises
    ------
    ValueError
        有效数据点不足以确定参数时。
    """
    temps_k = np.asarray(temps_k, dtype=float)
    freqs_hz = np.asarray(freqs_hz, dtype=float)
    if freqs_hz.ndim != 2:
        raise ValueError("freqs_hz 必须是 (n_temp, n_mode) 二维数组")
    n_temp, n_mode = freqs_hz.shape
    if temps_k.shape != (n_temp,):
        raise ValueError(f"temps_k 长度 {temps_k.shape} 与 freqs_hz 行数 {n_temp} 不符")
    if tc_k is None:
        raise ValueError("必须提供 tc_k（通常取自 chip.json 的实测值）")

    good = np.isfinite(freqs_hz) & (freqs_hz > 0)
    if mask is not None:
        good &= np.asarray(mask, dtype=bool)
    if not good.any():
        raise ValueError("没有任何有效数据点可供拟合")

    # 每个模式至少需要 2 个点才能定出 f0 与 alpha
    per_mode = good.sum(axis=0)
    if (per_mode[per_mode > 0] < 2).any():
        raise ValueError(f"存在有效点少于 2 的模式：每模式点数 {per_mode.tolist()}")

    # ---- 激光项归一化 ----
    use_laser = bool(fit_kappa and laser_mw is not None)
    laser_arr = None
    if use_laser:
        laser_arr = np.broadcast_to(np.asarray(laser_mw, dtype=float), (n_temp,))
        if not np.any(laser_arr != 0.0):
            use_laser = False   # 全 0 功率下 κ 不可辨识，退化为纯温度

    # ---- 初值 ----
    f0_init = np.empty(n_mode)
    for j in range(n_mode):
        col = np.where(good[:, j])[0]
        # 取最低温处的实测值作为 f(0) 初值（该处模型修正量最小）
        f0_init[j] = freqs_hz[col[np.argmin(temps_k[col])], j] if len(col) else 1e9

    alpha_init = np.full(n_mode, 0.09)
    kappa_init = np.full(n_mode, 0.0006)
    p0 = DEFAULT_P_INIT if p_init is None else float(p_init)
    if init_from:
        p0 = float(init_from.get("p", p0))
        a_prev = init_from.get("alpha")
        if a_prev is not None and len(a_prev) == n_mode:
            alpha_init = np.clip(np.asarray(a_prev, float), *ALPHA_BOUNDS)
        k_prev = init_from.get("kappa")
        if k_prev is not None and len(k_prev) == n_mode:
            kappa_init = np.clip(np.asarray(k_prev, float), *KAPPA_BOUNDS)

    p_is_free = p_fixed is None
    p_start = p0 if p_is_free else float(p_fixed)

    n_free = (n_mode * 2 + (n_mode if use_laser else 0)
              + (1 if p_is_free else 0) + (1 if tc_free else 0))
    n_points = int(good.sum())
    if n_points < n_free:
        raise ValueError(f"有效点数 {n_points} 少于自由参数数 {n_free}，无法拟合")

    def residual(x):
        f0, alpha, p, tc, kappa = _unpack_flex(x, n_mode, tc_k, tc_free,
                                               p_is_free, p_start, use_laser)
        pred = f_model(temps_k, f0, alpha, tc, p,
                       laser_mw=laser_arr if use_laser else 0.0,
                       kappa=kappa if use_laser else None)
        r = (pred - freqs_hz) / freqs_hz
        return r[good]

    x0 = _pack_flex(f0_init, alpha_init, p_start, tc_k, tc_free, p_is_free,
                    use_laser, kappa_init)
    lo = _pack_flex(f0_init * 0.85, np.full(n_mode, ALPHA_BOUNDS[0]),
                    P_BOUNDS[0], max(temps_k.max() + 0.5, 1.0), tc_free, p_is_free,
                    use_laser, np.full(n_mode, KAPPA_BOUNDS[0]))
    hi = _pack_flex(f0_init * 1.15, np.full(n_mode, ALPHA_BOUNDS[1]),
                    P_BOUNDS[1], 200.0, tc_free, p_is_free,
                    use_laser, np.full(n_mode, KAPPA_BOUNDS[1]))

    res = least_squares(residual, x0, bounds=(lo, hi), method="trf",
                        xtol=1e-15, ftol=1e-15, max_nfev=20000)

    f0, alpha, p, tc, kappa = _unpack_flex(res.x, n_mode, tc_k, tc_free,
                                           p_is_free, p_start, use_laser)

    # 协方差 C = (JᵀJ)⁻¹ · RSS/(N-M)
    dof = max(n_points - n_free, 1)
    s2 = 2.0 * res.cost / dof
    jtj = res.jac.T @ res.jac
    try:
        cov = np.linalg.inv(jtj) * s2
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(jtj) * s2

    resid_frac = np.full((n_temp, n_mode), np.nan)
    resid_frac[good] = res.fun

    out = {
        "tc_k": float(tc),
        "tc_source": "fitted" if tc_free else "fixed",
        "p": float(p),
        "p_source": "fitted" if p_is_free else "fixed",
        "alpha": alpha.copy(),
        "f0_hz": f0.copy(),
        "cov": cov,
        "_layout": {"n_mode": n_mode, "p_free": p_is_free, "tc_free": tc_free,
                    "fit_kappa": use_laser},
        "n_points": n_points,
        "n_params": n_free,
        "resid_frac": resid_frac,
        "rms_ppm": float(np.sqrt(np.mean(res.fun ** 2)) * 1e6),
    }
    if use_laser:
        out["kappa"] = kappa.copy()
        out["kappa_source"] = "fitted"
    return out


def _pack_flex(f0_hz, alpha, p, tc_k, tc_free, p_free, fit_kappa=False,
               kappa=None):
    parts = [np.asarray(f0_hz, float) / 1e9, np.asarray(alpha, float)]
    if fit_kappa:
        parts.append(np.asarray(kappa, float))
    if p_free:
        parts.append([p])
    if tc_free:
        parts.append([tc_k])
    return np.concatenate(parts)


def _unpack_flex(x, n_mode, tc_k, tc_free, p_free, p_fixed_value, fit_kappa=False):
    f0_hz = x[:n_mode] * 1e9
    alpha = x[n_mode:2 * n_mode]
    k = 2 * n_mode
    if fit_kappa:
        kappa = x[k:k + n_mode]
        k += n_mode
    else:
        kappa = None
    if p_free:
        p = x[k]
        k += 1
    else:
        p = p_fixed_value
    tc = x[k] if tc_free else tc_k
    return f0_hz, alpha, p, tc, kappa


# =========================================================================
# 预测
# =========================================================================

def predict(fit, temps_k, laser_mw=0.0):
    """预测频率及其 delta-method 不确定度。

    警告
    ----
    返回的 sigma **只含参数不确定度，不含模型形式误差**，在外推区严重低估
    （实测 3σ 覆盖率仅 30%）。请勿直接用 3σ 作为搜峰窗口——用
    prediction_window()，它把历史实测单步误差一并纳入。

    Parameters
    ----------
    fit : dict
    temps_k : float or array
    laser_mw : float or array (n_temp,)，激光功率。fit 不含 kappa 时忽略

    Returns
    -------
    (f_pred_hz, sigma_hz) : 形状同 f_model 的输出
    """
    temps_k_arr = np.atleast_1d(np.asarray(temps_k, dtype=float))
    scalar_in = np.ndim(temps_k) == 0
    lay = fit["_layout"]
    n_mode, p_free, tc_free = lay["n_mode"], lay["p_free"], lay["tc_free"]
    has_kappa = bool(lay.get("fit_kappa", False)) and fit.get("kappa") is not None
    laser_arr = (np.broadcast_to(np.asarray(laser_mw, dtype=float),
                                 temps_k_arr.shape) if has_kappa else None)

    x = _pack_flex(fit["f0_hz"], fit["alpha"], fit["p"], fit["tc_k"], tc_free,
                   p_free, has_kappa, fit.get("kappa"))

    def evaluate(xv):
        f0, alpha, p, tc, kappa = _unpack_flex(xv, n_mode, fit["tc_k"], tc_free,
                                               p_free, fit["p"], has_kappa)
        return f_model(temps_k_arr, f0, alpha, tc, p,
                       laser_mw=laser_arr if has_kappa else 0.0,
                       kappa=kappa if has_kappa else None)

    base = evaluate(x)                                   # (n_temp, n_mode)
    cov = fit["cov"]
    grad = np.zeros(base.shape + (len(x),))
    for i in range(len(x)):
        step = max(abs(x[i]) * 1e-6, 1e-9)
        xp = x.copy()
        xp[i] += step
        grad[..., i] = (evaluate(xp) - base) / step

    var = np.einsum("tmi,ij,tmj->tm", grad, cov, grad)
    sigma = np.sqrt(np.clip(var, 0.0, None))

    # Tc 以上模型无意义
    invalid = temps_k_arr >= fit["tc_k"]
    if invalid.any():
        base = base.copy()
        base[invalid, :] = np.nan
        sigma = sigma.copy()
        sigma[invalid, :] = np.nan

    if scalar_in:
        return base[0], sigma[0]
    return base, sigma


def prediction_window(fit, temp_k, past_error_hz=0.0, n_sigma=DEFAULT_N_SIGMA,
                      beta=DEFAULT_BETA, window_min_hz=DEFAULT_WINDOW_MIN_HZ,
                      laser_mw=0.0):
    """搜峰窗口半宽——经验自标定，不单纯依赖 delta-method σ。

        半宽 = max( n_sigma·σ_delta,  beta·past_error,  window_min_hz )

    past_error_hz 是该模型在**已确认的历史温度点**上实测到的最大单步预测误差，
    它把模型形式误差（delta method 捕捉不到的部分）纳入进来。这是本设计能达到
    100% 覆盖率的关键——纯 3σ 只有 30%。

    Parameters
    ----------
    fit : dict
    temp_k : float
        待预测温度（actual 实测值）。
    past_error_hz : float or array (n_mode,)
        历史最大单步误差。首次预测时为 0，此时窗口由 window_min_hz 兜底。
    laser_mw : float，待预测温度点的激光功率。fit 不含 kappa 时忽略。

    Returns
    -------
    (f_pred_hz, half_width_hz) : 均为 (n_mode,)
    """
    f_pred, sigma = predict(fit, float(temp_k), laser_mw=laser_mw)
    past = np.broadcast_to(np.asarray(past_error_hz, dtype=float),
                           f_pred.shape).astype(float)
    half = np.maximum.reduce([
        n_sigma * sigma,
        beta * past,
        np.full_like(f_pred, float(window_min_hz)),
    ])
    return f_pred, half


# =========================================================================
# 回测
# =========================================================================

def backtest_walk_forward(temps_k, freqs_hz, n_anchor=4, n_sigma=DEFAULT_N_SIGMA,
                          beta=DEFAULT_BETA, window_min_hz=DEFAULT_WINDOW_MIN_HZ,
                          **fit_kw):
    """模式 B——生产实际路径：每个温度点只用**严格更低**的温度拟合，单步外推。

    这是推荐的验收依据。跨段外推请用 backtest_holdout，但实测表明它不可靠。

    Parameters
    ----------
    n_sigma, beta, window_min_hz : 传给 prediction_window 的窗口参数（不与拟合参数混在一起）

    Returns
    -------
    dict
        steps : list of per-step dict(temp_k, f_pred, f_true, delta_hz,
                sigma_hz, half_width_hz, covered)
        max_abs_delta_hz, coverage
    """
    temps_k = np.asarray(temps_k, dtype=float)
    freqs_hz = np.asarray(freqs_hz, dtype=float)
    order = np.argsort(temps_k)
    temps_k, freqs_hz = temps_k[order], freqs_hz[order]

    steps = []
    past_error = 0.0
    for k in range(n_anchor, len(temps_k)):
        fit = fit_global(temps_k[:k], freqs_hz[:k], **fit_kw)
        f_pred, half = prediction_window(fit, temps_k[k], past_error_hz=past_error,
                                         n_sigma=n_sigma, beta=beta,
                                         window_min_hz=window_min_hz)
        _, sigma = predict(fit, float(temps_k[k]))
        delta = f_pred - freqs_hz[k]
        valid = np.isfinite(delta)
        covered = np.abs(delta) <= half
        steps.append({
            "temp_k": float(temps_k[k]),
            "f_pred_hz": f_pred,
            "f_true_hz": freqs_hz[k].copy(),
            "delta_hz": delta,
            "sigma_hz": sigma,
            "half_width_hz": half,
            "covered": covered,
            "fit": fit,
        })
        if valid.any():
            past_error = max(past_error, float(np.nanmax(np.abs(delta[valid]))))

    all_delta = np.concatenate([s["delta_hz"] for s in steps]) if steps else np.array([])
    all_cov = np.concatenate([s["covered"] for s in steps]) if steps else np.array([])
    finite = np.isfinite(all_delta)
    return {
        "steps": steps,
        "max_abs_delta_hz": float(np.max(np.abs(all_delta[finite]))) if finite.any() else np.nan,
        "coverage": float(np.mean(all_cov)) if all_cov.size else np.nan,
    }


def backtest_holdout(temps_k, freqs_hz, anchor_mask, n_sigma=DEFAULT_N_SIGMA,
                     beta=DEFAULT_BETA, window_min_hz=DEFAULT_WINDOW_MIN_HZ,
                     **fit_kw):
    """模式 A——只用 anchor_mask 标记的（低温）点拟合，一次性预测其余全部点。

    诊断用途。实测表明跨段外推系统性偏低（误差全部同号且随 T 单调增大），
    不应作为验收门。anchor_mask 应按 **target 温度**划分，避免 actual 温度
    在阈值附近抖动导致锚定集意外多进一个点。
    """
    temps_k = np.asarray(temps_k, dtype=float)
    freqs_hz = np.asarray(freqs_hz, dtype=float)
    anchor_mask = np.asarray(anchor_mask, dtype=bool)

    fit = fit_global(temps_k[anchor_mask], freqs_hz[anchor_mask], **fit_kw)
    held = np.where(~anchor_mask)[0]
    rows = []
    for i in held:
        f_pred, half = prediction_window(fit, temps_k[i], past_error_hz=0.0,
                                         n_sigma=n_sigma, beta=beta,
                                         window_min_hz=window_min_hz)
        _, sigma = predict(fit, float(temps_k[i]))
        delta = f_pred - freqs_hz[i]
        rows.append({
            "temp_k": float(temps_k[i]),
            "f_pred_hz": f_pred,
            "f_true_hz": freqs_hz[i].copy(),
            "delta_hz": delta,
            "sigma_hz": sigma,
            "half_width_hz": half,
            "covered": np.abs(delta) <= half,
        })
    all_delta = np.concatenate([r["delta_hz"] for r in rows]) if rows else np.array([])
    finite = np.isfinite(all_delta)
    return {
        "fit": fit,
        "rows": rows,
        "max_abs_delta_hz": float(np.max(np.abs(all_delta[finite]))) if finite.any() else np.nan,
        "coverage": float(np.mean(np.concatenate([r["covered"] for r in rows]))) if rows else np.nan,
    }


# =========================================================================
# 峰分配
# =========================================================================

def assign_peaks(f_pred_hz, half_width_hz, f_peaks_hz, merge_tol_hz=None):
    """把实测峰全局最优地分配给各模式编号。

    用匈牙利算法（linear_sum_assignment）做全局最优匹配，避免两个模式编号
    抢同一个峰——贪心最近邻在高温模式互相靠近时会出这种错。

    状态判定：
      ok      窗口内命中唯一峰
      merged  未命中，但预测位置落在某个**已被其它模式占用**的峰的 merge_tol 内
              （两个谐振靠得太近，实测上并成了一个）
      lost    未命中且不构成 merged

    merged 与 lost 都保留编号占位、都不应参与后续拟合。

    Parameters
    ----------
    f_pred_hz, half_width_hz : array (n_mode,)
    f_peaks_hz : array (n_peak,)
        实测峰频率，可为空。
    merge_tol_hz : float or array (n_mode,), optional
        判定 merged 的容差，默认取 half_width_hz。

    Returns
    -------
    dict
        status : list[str]  长度 n_mode
        peak_index : list[int|None]  命中的峰在 f_peaks_hz 中的下标
        f_assigned_hz : ndarray (n_mode,)  未命中处为 NaN
    """
    f_pred_hz = np.asarray(f_pred_hz, dtype=float)
    half = np.asarray(half_width_hz, dtype=float)
    peaks = np.asarray(f_peaks_hz, dtype=float).ravel()
    n_mode = len(f_pred_hz)
    if merge_tol_hz is None:
        merge_tol = half
    else:
        merge_tol = np.broadcast_to(np.asarray(merge_tol_hz, float), (n_mode,))

    status = ["lost"] * n_mode
    peak_index = [None] * n_mode
    f_assigned = np.full(n_mode, np.nan)

    if peaks.size:
        # 代价 = 归一化距离；超窗设为 inf（用大数代替以免 Hungarian 无解）
        dist = np.abs(f_pred_hz[:, None] - peaks[None, :])
        norm = dist / np.where(half[:, None] > 0, half[:, None], 1.0)
        big = 1e6
        cost = np.where(norm <= 1.0, norm, big)
        rows, cols = linear_sum_assignment(cost)
        for r, c in zip(rows, cols):
            if cost[r, c] < big:
                status[r] = "ok"
                peak_index[r] = int(c)
                f_assigned[r] = peaks[c]

    # 未命中的：判断是否与已占用的峰重叠 -> merged
    taken = {i for i in peak_index if i is not None}
    for m in range(n_mode):
        if status[m] != "lost" or not taken:
            continue
        for i in taken:
            if abs(f_pred_hz[m] - peaks[i]) <= merge_tol[m]:
                status[m] = "merged"
                break

    return {"status": status, "peak_index": peak_index, "f_assigned_hz": f_assigned}


# =========================================================================
# 序列化（纯函数，文件 IO 见 io_thermal.py）
# =========================================================================

def to_dict(fit):
    """把 fit 结果转成 JSON 可序列化的 dict。"""
    out = {
        "tc_k": float(fit["tc_k"]),
        "tc_source": fit["tc_source"],
        "p": float(fit["p"]),
        "p_source": fit.get("p_source", "fitted"),
        "alpha": np.asarray(fit["alpha"], float).tolist(),
        "f0_hz": np.asarray(fit["f0_hz"], float).tolist(),
        "cov": np.asarray(fit["cov"], float).tolist(),
        "_layout": dict(fit["_layout"]),
        "n_points": int(fit["n_points"]),
        "n_params": int(fit["n_params"]),
        "rms_ppm": float(fit["rms_ppm"]),
    }
    if fit.get("kappa") is not None:
        out["kappa"] = np.asarray(fit["kappa"], float).tolist()
        out["kappa_source"] = fit.get("kappa_source", "fitted")
    return out


def from_dict(d):
    """to_dict() 的逆操作，还原可直接用于 predict() 的 fit dict。"""
    out = {
        "tc_k": float(d["tc_k"]),
        "tc_source": d.get("tc_source", "fixed"),
        "p": float(d["p"]),
        "p_source": d.get("p_source", "fitted"),
        "alpha": np.asarray(d["alpha"], dtype=float),
        "f0_hz": np.asarray(d["f0_hz"], dtype=float),
        "cov": np.asarray(d["cov"], dtype=float),
        "_layout": dict(d["_layout"]),
        "n_points": int(d.get("n_points", 0)),
        "n_params": int(d.get("n_params", 0)),
        "rms_ppm": float(d.get("rms_ppm", np.nan)),
    }
    if d.get("kappa") is not None:
        out["kappa"] = np.asarray(d["kappa"], dtype=float)
        out["kappa_source"] = d.get("kappa_source", "fitted")
        out["_layout"]["fit_kappa"] = True
    return out
