# -*- coding: utf-8 -*-
"""
误差估计与不确定度传播模块。

为缓存中的 delta_f/f0 数据点提供不确定度估计，支持:
  - f0 测量精度 (基于 dip 深度)
  - VNA 功率间漂移 (Pl=0 列标准差)
  - Bootstrap 响应率置信区间

所有函数均无 scraps 依赖，仅需 numpy/scipy。
"""

import numpy as np
from scipy.stats import linregress


# ═══════════════════════════════════════════════════════
# f0 不确定度 (基于 dip 深度)
# ═══════════════════════════════════════════════════════

def estimate_f0_uncertainty_mhz(dip_depth_db: float, f0_ghz: float = 4.5,
                                 ql_approx: float = None) -> float:
    """
    从 dip 深度估算 f0 测量不确定度 (MHz).

    模型: sigma_f0 ~ f0 / (2 * Ql * SNR_voltage)
    其中 SNR_voltage ~ 10^(-dip_depth/20)  (线性电压比)
    若无 Ql 值，使用 dip 深度的经验缩放:
      - >10 dB dip → ~0.1 MHz (高 SNR)
      - 3-10 dB    → ~0.3-1.0 MHz
      - <3 dB      → ~2-5 MHz (低 SNR, 近 Tc)
    """
    depth = abs(dip_depth_db)
    if ql_approx is not None and ql_approx > 0:
        snr_voltage = 10 ** (depth / 20.0)
        sigma = f0_ghz * 1e3 / (2.0 * ql_approx * snr_voltage)  # MHz
        return max(sigma, 0.05)  # 底限 0.05 MHz

    # 经验模型 (无 Ql 时)
    if depth >= 10:
        return 0.10  # 深 dip: ~100 kHz
    elif depth >= 5:
        return 0.30
    elif depth >= 3:
        return 0.80
    elif depth >= 1.5:
        return 2.0
    else:
        return 5.0  # 极浅 dip: ~5 MHz


def get_dip_depth_map(cache: dict) -> dict:
    """从 cache 提取每个 (T, R) 的 dip 深度 (dB)."""
    depths = {}
    for T_k, ident in cache.get("identification", {}).items():
        depths[T_k] = {}
        for r in ident.get("resonators", []):
            depths[T_k][r["name"]] = r["dip_depth_db"]
    return depths


# ═══════════════════════════════════════════════════════
# delta_f/f0 不确定度 (ppm)
# ═══════════════════════════════════════════════════════

def estimate_dff_uncertainty(cache: dict, temp_k: float, resonator: str) -> np.ndarray:
    """
    为单个 (T, R) 的 delta_f/f0 网格估算不确定度 (ppm).

    返回: (n_pv, n_pl) ndarray, 单位 ppm
    """
    collected = cache["collected"][temp_k]
    data = collected["data"][resonator]
    dff = data["delta_f_over_f"]  # (n_pv, n_pl), ppm
    flags = data["flags"]

    n_pv, n_pl = dff.shape

    # 1. 从 dip 深度估算基础精度
    depths = get_dip_depth_map(cache)
    dip_db = depths.get(temp_k, {}).get(resonator, -5.0)
    f0_ghz = collected["identified"][resonator]["f0_ghz"]
    sigma_f0_mhz = estimate_f0_uncertainty_mhz(dip_db, f0_ghz)
    base_sigma_ppm = (sigma_f0_mhz / (f0_ghz * 1e3)) * 1e6  # MHz → ppm

    # 2. 从 f0_refs 跨 VNA 功率抖动估算系统漂移
    f0_refs = collected["data"][resonator].get("f0_refs", {})
    if len(f0_refs) > 1:
        f0_vals = np.array(list(f0_refs.values()))
        pv_drift_mhz = np.nanstd(f0_vals) * 1e3  # GHz → MHz
        pv_drift_ppm = (pv_drift_mhz / (f0_ghz * 1e3)) * 1e6
    else:
        pv_drift_ppm = 0.0

    # 3. 组合不确定度: 基础精度 + 漂移 (RSS)
    sigma = np.full((n_pv, n_pl), np.sqrt(base_sigma_ppm ** 2 + pv_drift_ppm ** 2))

    # 4. 对高 T 数据点放大 (近 Tc SNR 恶化)
    if temp_k >= 70:
        sigma *= 2.5
    elif temp_k >= 60:
        sigma *= 1.5

    # 5. 标记浅 dip 为高不确定度
    sigma[flags != "tracked"] *= 3.0

    return sigma


def get_dff_value_and_error(cache: dict, temp_k: float, resonator: str,
                             pv_idx: int = 0) -> tuple:
    """
    获取 Pl=0 列(或指定 pv_idx)的 delta_f/f0 值和误差.

    注意: cache 中 delta_f_over_f 存储为**分数** (fraction),
    返回值已转换为 ppm (×1e6).

    Returns: (pl_mw_list, dff_values_ppm, dff_errors_ppm)
    """
    collected = cache["collected"][temp_k]
    data = collected["data"][resonator]
    dff_frac = data["delta_f_over_f"]  # (n_pv, n_pl), fraction
    sigma = estimate_dff_uncertainty(cache, temp_k, resonator)
    laser_powers = cache["metadata"]["laser_powers_mw"]

    return (
        np.array(laser_powers),
        dff_frac[pv_idx, :] * 1e6,  # fraction → ppm
        sigma[pv_idx, :],
    )


# ═══════════════════════════════════════════════════════
# Bootstrap 响应率置信区间
# ═══════════════════════════════════════════════════════

def bootstrap_responsivity(x: np.ndarray, y: np.ndarray,
                            n_bootstrap: int = 1000,
                            rng: np.random.Generator = None) -> dict:
    """
    Bootstrap 重采样估计线性响应率 (df/f vs P_laser) 的 95% CI.

    Args:
        x: 激光功率 (mW)
        y: delta_f/f0 (ppm)
        n_bootstrap: 重采样次数

    Returns:
        {"slope": 最佳估计 (ppm/mW),
         "slope_std": 标准误,
         "ci_95_lower": 下界,
         "ci_95_upper": 上界,
         "r_squared": R²,
         "n_points": 有效数据点数}
    """
    if rng is None:
        rng = np.random.default_rng(42)

    # 去除 NaN
    mask = ~(np.isnan(x) | np.isnan(y))
    x_clean, y_clean = x[mask], y[mask]
    n = len(x_clean)

    if n < 3:
        return {"slope": np.nan, "slope_std": np.nan,
                "ci_95_lower": np.nan, "ci_95_upper": np.nan,
                "r_squared": np.nan, "n_points": n}

    # 最佳估计
    result = linregress(x_clean, y_clean)
    slope_best = result.slope
    r2 = result.rvalue ** 2

    # Bootstrap
    slopes = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = rng.choice(n, size=n, replace=True)
        xb, yb = x_clean[idx], y_clean[idx]
        try:
            slopes[i] = linregress(xb, yb).slope
        except (ValueError, TypeError):
            slopes[i] = np.nan

    slopes_valid = slopes[~np.isnan(slopes)]
    ci_lower = np.percentile(slopes_valid, 2.5)
    ci_upper = np.percentile(slopes_valid, 97.5)

    return {
        "slope": slope_best,
        "slope_std": np.std(slopes_valid),
        "ci_95_lower": ci_lower,
        "ci_95_upper": ci_upper,
        "r_squared": r2,
        "n_points": n,
    }


# ═══════════════════════════════════════════════════════
# 便捷函数: 提取响应率 + 误差
# ═══════════════════════════════════════════════════════

def get_responsivity_with_error(cache: dict, temp_k: float, resonator: str,
                                 pv_idx: int = 0) -> dict:
    """
    一站式获取某个 (T, R, Pv) 的响应率及其误差.

    Returns 可直接用于标注和误差条的 dict.
    """
    laser_powers, dff_vals, dff_errs = get_dff_value_and_error(
        cache, temp_k, resonator, pv_idx)

    bs = bootstrap_responsivity(laser_powers, dff_vals)

    return {
        "temp_k": temp_k,
        "resonator": resonator,
        "laser_powers_mw": laser_powers,
        "dff_ppm": dff_vals,
        "dff_err_ppm": dff_errs,
        "responsivity_ppm_per_mw": bs["slope"],
        "responsivity_std": bs["slope_std"],
        "responsivity_ci95": (bs["ci_95_lower"], bs["ci_95_upper"]),
        "r_squared": bs["r_squared"],
        "n_points": bs["n_points"],
    }


# ═══════════════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import pickle
    from pathlib import Path

    cache_path = Path(
        "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
        "_cache_20260609-0624__6-80K__full.pkl")

    if not cache_path.exists():
        print(f"Cache not found: {cache_path}")
        print("Skipping integration test.")
        # 纯单元测试
        print("\n--- Unit tests ---")
        print(f"dip=-15dB, f0=4.5GHz: {estimate_f0_uncertainty_mhz(-15, 4.5):.3f} MHz (expect ~0.1)")
        print(f"dip=-5dB,  f0=4.5GHz: {estimate_f0_uncertainty_mhz(-5, 4.5):.3f} MHz (expect ~0.3)")
        print(f"dip=-2dB,  f0=4.5GHz: {estimate_f0_uncertainty_mhz(-2, 4.5):.3f} MHz (expect ~2.0)")
        print(f"dip=-1dB,  f0=4.5GHz: {estimate_f0_uncertainty_mhz(-1, 4.5):.3f} MHz (expect ~5.0)")

        x = np.array([0, 1, 3, 5, 7, 9])
        y = np.array([0, -12, -38, -65, -90, -118])
        bs = bootstrap_responsivity(x, y, n_bootstrap=500)
        print(f"\nBootstrap: slope={bs['slope']:.1f} ppm/mW, "
              f"CI=[{bs['ci_95_lower']:.1f}, {bs['ci_95_upper']:.1f}], "
              f"R2={bs['r_squared']:.4f}")
    else:
        with open(cache_path, "rb") as f:
            cache = pickle.load(f)

        meta = cache["metadata"]
        print(f"Dataset: {meta['dataset_name']}")
        print(f"Temperatures: {meta['temperatures_k']}")
        print(f"Resonators: {meta['resonator_names']}")

        # 对每个谐振器打印误差预算
        print("\n=== Error budget per resonator (6K, Pl=0, lowest VNA power) ===\n")
        depths = get_dip_depth_map(cache)
        for rname in meta["resonator_names"]:
            dip = depths[6][rname]
            f0 = cache["identification"][6]["resonators"][
                [r["name"] for r in cache["identification"][6]["resonators"]].index(rname)
            ]["f0_ghz"]
            sigma_f0 = estimate_f0_uncertainty_mhz(dip, f0)
            sigma_ppm = (sigma_f0 / (f0 * 1e3)) * 1e6

            # 获取 Pl=0 列标准差
            dff = cache["collected"][6]["data"][rname]["delta_f_over_f"]
            pv_drift = np.nanstd(dff[:, 0])

            # Bootstrap 响应率 (dff 是 fraction, 转换为 ppm)
            laser_pwrs = np.array(meta["laser_powers_mw"])
            dff_ppm = dff[0, :] * 1e6  # fraction → ppm
            bs = bootstrap_responsivity(laser_pwrs, dff_ppm)

            print(f"{rname}: f0={f0:.3f}GHz, dip={dip:.1f}dB, "
                  f"sigma_f0={sigma_f0:.3f}MHz ({sigma_ppm:.1f}ppm), "
                  f"Pv_drift={pv_drift:.1f}ppm, "
                  f"resp={bs['slope']:.1f}[{bs['ci_95_lower']:.1f},{bs['ci_95_upper']:.1f}] ppm/mW")

    print("\n[OK] _error_estimation self-check passed")
