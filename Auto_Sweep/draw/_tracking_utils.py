# -*- coding: utf-8 -*-
"""谐振子追踪共用模块 — f0 骨架预测 + 双验证 + P90 基线矫正。

复用 plot_power_dependence_v2.py 的成熟逻辑:
  1. scraps f0 骨架 → 有限差分外推预测 f0(T)
  2. 预测位置 ±50 MHz → P90 局部基线 dip 检测
  3. 若失败 → find_true_resonances() 幅度+相位交叉验证兜底

用法:
    from _tracking_utils import (
        identify_resonators, track_across_pv, track_across_pl,
        SCRAPS_F0, SCRAPS_TEMPS,
    )
"""

import sys
import os
import numpy as np

# ---- dataprocess（自包含副本，源自 YBCO_TA monorepo 的 otherwise/dataprocess.py）----
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "lib")
)
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)
from dataprocess import find_true_resonances

# =========================================================================
# f0 骨架 (scraps cmplxIQ 拟合结果)
# =========================================================================

SCRAPS_TEMPS = np.array([6, 22, 38, 54, 70])  # K

SCRAPS_F0 = {
    # 按频率升序排列的 5 个谐振子
    "R1": np.array([3.8451, 3.8334, 3.8020, 3.7405, 3.5841]),  # GHz
    "R2": np.array([4.0085, 3.9961, 3.9625, 3.8974, 3.7319]),
    "R3": np.array([4.4990, 4.4854, 4.4499, 4.3812, 4.2064]),
    "R4": np.array([4.9957, 4.9803, 4.9397, 4.8611, 4.6644]),
    "R5": np.array([5.2503, 5.2343, 5.1917, 5.0831, 4.6644]),
}

RESONATOR_NAMES = ["R1", "R2", "R3", "R4", "R5"]

# find_true_resonances 参数 (宽松 — 兜底用)
PEAK_KWARGS = dict(
    min_prominence=1.0,
    distance=50,
    phase_window=25,
    phase_diff_snr_threshold=1.5,
    noise_inner_window=5,
    noise_outer_window=40,
    min_phase_diff_support_points=2,
    min_phase_diff_width=2,
    plot=False,
)

# =========================================================================
# Zoom 窗口
# =========================================================================

def zoom_mhz(temp_k: float) -> float:
    """T <= 20K -> 10 MHz; T >= 77K -> 30 MHz; 中间线性渐变。"""
    if temp_k <= 20:
        return 10.0
    if temp_k >= 77:
        return 30.0
    return 10.0 + (temp_k - 20.0) / (77.0 - 20.0) * 20.0

# =========================================================================
# 有限差分外推
# =========================================================================

def predict_f0_fd(f0_history, temp_k: float):
    """有限差分外推预测温度 temp_k 的 f0 (GHz)。

    f0_history: [(T1, f0_1), (T2, f0_2), ...] 按温度升序。
    来源: plot_power_dependence_v2.py _predict_f0_fd()
    """
    if not f0_history:
        return None

    # 精确匹配 (0.5 K 容差)
    for tk, f0 in f0_history:
        if abs(tk - temp_k) < 0.5:
            return f0

    # 仅取温度低于目标的历史点
    below = [(tk, f0) for tk, f0 in f0_history if tk < temp_k]
    if not below:
        return f0_history[0][1]

    recent = below[-5:]
    f_vals = [f for _, f in recent]
    n = len(f_vals)

    if n == 1:
        return f_vals[0]
    elif n == 2:
        return f_vals[-1] + (f_vals[-1] - f_vals[-2])
    elif n == 3:
        f1, f2, f3 = f_vals
        return f3 + (f3 - f2) - (f3 - 2*f2 + f1)
    elif n == 4:
        f1, f2, f3, f4 = f_vals
        return f4 + (f4 - f3) - (f4 - 2*f3 + f2) + (f4 - 3*f3 + 3*f2 - f1)
    else:
        f1, f2, f3, f4, f5 = f_vals[-5:]
        return (f5 + (f5 - f4) - (f5 - 2*f4 + f3)
                + (f5 - 3*f4 + 3*f3 - f2)
                - (f5 - 4*f4 + 6*f3 - 4*f2 + f1))

# =========================================================================
# P90 基线 dip 检测
# =========================================================================

def detect_dip_p90(freq_ghz, s21_db, f0_pred_ghz, search_mhz=50.0):
    """在预测位置附近搜索 dip，P90 局部基线矫正。

    Returns:
        (f0_found_ghz, dip_depth_db, baseline_db) or (None, None, None).
        dip_depth = dip_abs - baseline (负 = 凹陷)
    """
    lo = f0_pred_ghz - search_mhz / 1000.0
    hi = f0_pred_ghz + search_mhz / 1000.0
    mask = (freq_ghz >= lo) & (freq_ghz <= hi)
    if mask.sum() < 20:
        return None, None, None

    f_win = freq_ghz[mask]
    s_win = s21_db[mask]

    baseline = float(np.percentile(s_win, 90))
    idx_min = int(np.argmin(s_win))
    dip_abs = float(s_win[idx_min])
    dip_depth = dip_abs - baseline
    f_dip = float(f_win[idx_min])

    return f_dip, dip_depth, baseline


def find_all_dips_p90(freq_ghz, s21_db, temp_k, prominence_db=1.0):
    """全谱 P90 滑动窗口 dip 检测 — 找到所有显著凹陷。

    使用滑动窗口（宽度 = 4 × zoom_mhz），在每个窗口内计算 P90 基线，
    找到低于基线的局部最小值。然后按 dip 深度排序。

    Args:
        freq_ghz: 频率 (GHz)
        s21_db: |S21| (dB)
        temp_k: 温度 (K)，用于自适应窗口
        prominence_db: 最小 prominence (dB)，低于此深度的 dip 被过滤

    Returns:
        List[Tuple[float, float, float]]: [(f0_ghz, dip_depth_db, baseline_db), ...]
        按频率升序排列。
    """
    half_win_mhz = zoom_mhz(temp_k)
    # 滑动窗口宽度 = 4 × zoom (确保覆盖单个谐振子但不过度重叠)
    window_mhz = half_win_mhz * 4
    window_ghz = window_mhz / 1000.0
    step_ghz = window_ghz / 3  # 2/3 重叠

    if window_ghz >= (freq_ghz[-1] - freq_ghz[0]):
        # 全谱只有一个窗口
        windows = [(freq_ghz[0], freq_ghz[-1])]
    else:
        windows = []
        start = freq_ghz[0]
        while start < freq_ghz[-1]:
            end = start + window_ghz
            windows.append((start, end))
            start += step_ghz

    all_dips = []  # [(f0, dip_depth, baseline), ...]
    seen_freqs = set()

    for w_lo, w_hi in windows:
        mask = (freq_ghz >= w_lo) & (freq_ghz <= w_hi)
        if mask.sum() < 20:
            continue
        f_win = freq_ghz[mask]
        s_win = s21_db[mask]

        baseline = float(np.percentile(s_win, 90))
        below = s_win < baseline  # 低于基线的点

        if below.sum() < 5:
            continue

        # 找连续低于基线的区域中的最小值
        in_dip = False
        dip_start = 0
        for i in range(len(f_win)):
            if below[i] and not in_dip:
                in_dip = True
                dip_start = i
            elif not below[i] and in_dip:
                in_dip = False
                # 找到该区域的最小值
                seg_s = s_win[dip_start:i]
                seg_f = f_win[dip_start:i]
                idx_min = int(np.argmin(seg_s))
                dip_f = float(seg_f[idx_min])
                dip_abs = float(seg_s[idx_min])
                dip_depth = dip_abs - baseline
                if dip_depth < -prominence_db:
                    # 去重: 附近 5 MHz 内去重
                    key = round(dip_f * 1000 / 5)  # 5 MHz 桶
                    if key not in seen_freqs:
                        seen_freqs.add(key)
                        all_dips.append((dip_f, dip_depth, baseline))
        # 处理最后一个未闭合的 dip
        if in_dip:
            seg_s = s_win[dip_start:]
            seg_f = f_win[dip_start:]
            idx_min = int(np.argmin(seg_s))
            dip_f = float(seg_f[idx_min])
            dip_abs = float(seg_s[idx_min])
            dip_depth = dip_abs - baseline
            if dip_depth < -prominence_db:
                key = round(dip_f * 1000 / 5)
                if key not in seen_freqs:
                    seen_freqs.add(key)
                    all_dips.append((dip_f, dip_depth, baseline))

    # 按频率升序
    all_dips.sort(key=lambda x: x[0])
    return all_dips

# =========================================================================
# 双验证追踪
# =========================================================================

# 跨温度缓存 find_true_resonances 结果
_resonance_cache = {}

def track_one_resonator(freq_hz, s21_db, s21_complex, rname, temp_k, f0_history):
    """双验证追踪单个谐振子 f0。

    验证 a) 有限差分预测 + P90 基线 dip 检测 (±50 MHz)
    验证 b) 若 a 失败 → find_true_resonances() 交叉验证寻峰

    Args:
        freq_hz: 频率 (Hz)
        s21_db: |S21| (dB)
        s21_complex: 复 S21
        rname: "R1".."R5"
        temp_k: 当前温度 (K)
        f0_history: [(T, f0), ...] 该谐振子的历史追踪记录

    Returns:
        (f0_ghz, dip_depth_db) or (None, None)
    """
    f0_pred = predict_f0_fd(f0_history, temp_k)
    if f0_pred is None:
        return None, None

    freq_ghz = freq_hz / 1e9

    # 高温时放宽 dip 深度阈值
    if temp_k >= 70:
        dip_threshold = -0.3
        deviation_limit = 200  # MHz
    elif temp_k >= 50:
        dip_threshold = -0.7
        deviation_limit = 150
    else:
        dip_threshold = -1.0
        deviation_limit = 100

    # ---- 验证 a: 预测 + P90 基线 (温度自适应搜索半径) ----
    # 检测是否为步进追踪 (同温度历史点存在 → 跨 Pl/Pv，频移有限)
    same_T_points = [f for tk, f in f0_history if abs(tk - temp_k) < 0.5]
    if same_T_points:
        # 步进追踪: 相邻功率/时间步之间频移≤几十 MHz，收紧窗口防跳谐振子
        # 高温近 Tc 时 df/dP 较大，按温度分级
        if temp_k >= 70:
            search_mhz = 50.0
        elif temp_k >= 50:
            search_mhz = 30.0
        else:
            search_mhz = 20.0
    else:
        search_mhz = _search_mhz_for_temp(temp_k)

    f_dip, dip_depth, baseline = detect_dip_p90(freq_ghz, s21_db, f0_pred, search_mhz=search_mhz)

    if f_dip is not None and dip_depth < dip_threshold:
        deviation = abs(f_dip - f0_pred) * 1000  # MHz
        if deviation < deviation_limit:
            return f_dip, dip_depth
        if dip_depth < -3.0:
            return f_dip, dip_depth

    # ---- 验证 b: find_true_resonances 兜底 ----
    cache_key = (temp_k, rname)
    if cache_key not in _resonance_cache:
        try:
            peaks, _, _ = find_true_resonances(
                freq=freq_hz, s21=s21_complex, **PEAK_KWARGS)
            _resonance_cache[cache_key] = peaks
        except Exception:
            _resonance_cache[cache_key] = []

    peaks = _resonance_cache[cache_key]

    best_f0, best_dist = None, 999.0
    for p in peaks:
        pf_ghz = p["frequency"] / 1e9
        dist = abs(pf_ghz - f0_pred) * 1000  # MHz
        if dist < deviation_limit and dist < best_dist:
            best_dist = dist
            best_f0 = pf_ghz

    if best_f0 is not None:
        _, dd, _ = detect_dip_p90(freq_ghz, s21_db, best_f0, search_mhz=10)
        return best_f0, dd if dd is not None else -99.0

    # ---- 验证 c: 高温兜底 — 仅信任靠近预测值的候选 ----
    if temp_k >= 65 and f_dip is not None:
        deviation = abs(f_dip - f0_pred) * 1000
        if deviation <= 30.0:   # 步间跳变 ≤30 MHz 才接受
            return f_dip, dip_depth

    return None, None

# =========================================================================
# 批量识别: 全局 dip 检测 + 预测就近匹配 + 去重
# =========================================================================

def _search_mhz_for_temp(temp_k: float) -> float:
    """温度自适应搜索半径。

    低温区 (T<50K): f0(T) 变化平缓，靠近 scraps 点时收紧窗口防止误选。
    高温区 (T>=50K): 近 Tc 时 df/dT 急剧增大，即使靠近 scraps 也需宽搜索。
    """
    nearest_dist = min(abs(temp_k - t) for t in SCRAPS_TEMPS)

    if temp_k < 50:
        # 低温区: f0 随 T 变化缓慢，靠近 scraps 时预测准确
        if nearest_dist <= 3:
            return 20.0
        elif nearest_dist <= 8:
            return 30.0
        else:
            return 50.0
    else:
        # 高温区/近 Tc: df/dT 可能很大，保持宽搜索
        if temp_k >= 70:
            return 250.0
        elif temp_k >= 50:
            return 100.0
        else:
            return 70.0


def identify_resonators(freq_hz, s21_db, s21_complex, temp_k):
    """紧阈值追踪 + 碰撞检测/解决 + 基线恢复 — 定位所有 5 个谐振子。

    参照 plot_power_dependence_v2.py 的成熟三段式策略:

    1. 首轮: 每个谐振子用紧阈值独立追踪 (dip<-1.0, dev<100MHz, 搜索±50MHz)
       深 dip (<-3.0) 可绕过 dev 限制; 失败则用 find_true_resonances (80MHz 半径)
    2. 碰撞检测: 两个谐振子追踪到 <10MHz 的同一 dip → 多尺度全频段 dip 搜索
       + 最优双向分配 (最小化总偏差, 保持频谱顺序 R1<R2<R3<R4<R5)
    3. 基线恢复: 仍未匹配的谐振子用 find_true_resonances 相位峰重搜 (100MHz)

    Returns:
        List[Dict]: 每个谐振子 {"name": "R1", "f0_ghz": float, "dip_depth": float}
    """
    freq_ghz = freq_hz / 1e9

    # ---- FD 预测 ----
    fd_predictions = {}
    for rname in RESONATOR_NAMES:
        skeleton_f0 = SCRAPS_F0[rname]
        history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0)
                   if t <= temp_k + 15]
        pred = predict_f0_fd(history, temp_k)
        fd_predictions[rname] = pred

    # ---- 高温预加载相位峰 (供频谱重排使用) ----
    all_phase_peaks = []
    if temp_k >= 65 and temp_k not in _resonance_cache:
        try:
            pk = dict(PEAK_KWARGS, min_prominence=0.3, phase_diff_snr_threshold=1.0,
                     distance=20, min_phase_diff_support_points=1, min_phase_diff_width=1)
            peaks, _, _ = find_true_resonances(freq=freq_hz, s21=s21_complex, **pk)
            _resonance_cache[temp_k] = peaks
        except Exception:
            _resonance_cache[temp_k] = []
    if temp_k in _resonance_cache:
        all_phase_peaks = _resonance_cache[temp_k]

    # ---- 首轮: 紧阈值独立追踪 (禁止合并) ----
    tracked = {}   # rname -> (f0_ghz, dip_depth)
    occupied_freqs = []  # [(f0_ghz, rname), ...] 已占用频率, 防止多谐振子选同一 dip

    for rname in RESONATOR_NAMES:
        pred = fd_predictions[rname]
        if pred is None:
            continue

        # 验证 a: P90 基线 dip (温度自适应搜索半径, dip<-1.0, dev<限制)
        search_mhz_primary = _search_mhz_for_temp(temp_k)
        f_dip, dip_depth, baseline = detect_dip_p90(
            freq_ghz, s21_db, pred, search_mhz=search_mhz_primary)

        # 温度自适应偏差限制: 高温时 FD 预测不可靠 (近 Tc 频移加速)
        if temp_k >= 70:
            _dev_limit_mhz = 250
        elif temp_k >= 50:
            _dev_limit_mhz = 150
        else:
            _dev_limit_mhz = 100

        accepted = False
        if f_dip is not None and dip_depth < -1.0:
            deviation = abs(f_dip - pred) * 1000  # MHz
            if deviation < _dev_limit_mhz:
                accepted = True
            elif dip_depth < -3.0:
                # 深 dip 例外: 绕过 deviation 限制
                accepted = True

        # 验证 a2: 主搜索失败 → 更宽窗口 P90 兜底 (高温时尤其重要)
        if not accepted and temp_k >= 50:
            fallback_mhz = max(200, search_mhz_primary * 1.5)
            f_dip2, dip_depth2, baseline2 = detect_dip_p90(
                freq_ghz, s21_db, pred, search_mhz=fallback_mhz)
            if f_dip2 is not None and dip_depth2 < -1.0:
                deviation2 = abs(f_dip2 - pred) * 1000
                if deviation2 <= fallback_mhz:
                    f_dip, dip_depth, baseline = f_dip2, dip_depth2, baseline2
                    accepted = True

        if accepted:
            tracked[rname] = (f_dip, dip_depth)
            occupied_freqs.append((f_dip, rname))
            continue

        # 验证 b: find_true_resonances 相位交叉验证
        # 此温度首次调用时缓存
        cache_key = temp_k
        if cache_key not in _resonance_cache:
            try:
                # 根据温度调整阈值
                if temp_k >= 70:
                    pk = dict(PEAK_KWARGS, min_prominence=0.3, phase_diff_snr_threshold=1.0,
                             distance=20, min_phase_diff_support_points=1, min_phase_diff_width=1)
                elif temp_k >= 50:
                    pk = dict(PEAK_KWARGS, min_prominence=0.5, phase_diff_snr_threshold=1.2, distance=30)
                else:
                    pk = dict(PEAK_KWARGS)
                peaks, _, _ = find_true_resonances(
                    freq=freq_hz, s21=s21_complex, **pk)
                _resonance_cache[cache_key] = peaks
            except Exception:
                _resonance_cache[cache_key] = []

        all_phase_peaks = _resonance_cache[cache_key]

        # 温度自适应搜索半径: 高温时 FD 预测偏差大
        _phase_search_mhz = 200 if temp_k >= 70 else 80

        best_f0, best_score = None, -999.0
        for p in all_phase_peaks:
            pf_ghz = p["frequency"] / 1e9
            dist = abs(pf_ghz - pred) * 1000  # MHz
            if dist >= _phase_search_mhz:
                continue
            # 排除已被其他谐振子占用的相位峰 (15 MHz 内)
            if any(abs(pf_ghz - of) < 0.015 for of, _ in occupied_freqs):
                continue
            # SNR 优先评分: 强相位峰即使稍远也比近处弱峰可信
            snr = p.get("phase_diff_snr", 1.0)
            score = snr * 80 - dist  # SNR 权重 80, 距离惩罚
            if score > best_score:
                best_score = score
                best_f0 = pf_ghz

        if best_f0 is not None:
            _, dd, _ = detect_dip_p90(freq_ghz, s21_db, best_f0, search_mhz=10)
            tracked[rname] = (best_f0, dd if dd is not None else -99.0)
            occupied_freqs.append((best_f0, rname))

    # ---- 碰撞检测与解决 (禁止合并) ----
    # 当两个谐振子追踪到 <10 MHz 的同一 dip 时, 弱方需重新寻找独立位置
    rnames_tracked = list(tracked.keys())
    for i in range(len(rnames_tracked)):
        for j in range(i + 1, len(rnames_tracked)):
            ra, rb = rnames_tracked[i], rnames_tracked[j]
            fa, da = tracked[ra]
            fb, db = tracked[rb]
            if abs(fa - fb) >= 0.010:
                continue

            # 确定 keeper (保留原位置) 和 mover (需重分配)
            pred_a = fd_predictions.get(ra, 0) or 0
            pred_b = fd_predictions.get(rb, 0) or 0
            dev_a = abs(fa - pred_a) * 1000
            dev_b = abs(fb - pred_b) * 1000
            # 质量评分: 0=P90深dip(dip<-3) > 1=紧dev(dev<50) > 2=其他
            q_a = 0 if da < -3.0 else 1 if dev_a < 50 else 2
            q_b = 0 if db < -3.0 else 1 if dev_b < 50 else 2

            if q_a <= q_b:
                keeper, mover = ra, rb
                keeper_f, mover_pred = fa, pred_b
            else:
                keeper, mover = rb, ra
                keeper_f, mover_pred = fb, pred_a

            # 为 mover 寻找独立候选 (排除 keeper 位置 15 MHz 范围)
            candidates = []

            # 相位峰候选
            if all_phase_peaks:
                for p in all_phase_peaks:
                    pf = p["frequency"] / 1e9
                    if abs(pf - keeper_f) < 0.015:
                        continue
                    dist = abs(pf - mover_pred) * 1000
                    if dist < 250:
                        snr = p.get("phase_diff_snr", 1.0)
                        candidates.append((pf, snr * 80 - dist, snr))

            # P90 滑动窗口 dip 候选 (比 argrelextrema 对浅 dip 更敏感)
            p90_candidates = find_all_dips_p90(freq_ghz, s21_db, temp_k, prominence_db=0.5)
            for f_d, dd, _ in p90_candidates:
                if abs(f_d - keeper_f) < 0.015:
                    continue
                dist = abs(f_d - mover_pred) * 1000
                if dist < 200 and dd < -0.8:
                    # 用实际 dip 深度评分: 深 dip 获得更高权重
                    candidates.append((f_d, -dd * 80 - dist, -dd))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                new_f = candidates[0][0]
                _, dd_new, _ = detect_dip_p90(freq_ghz, s21_db, new_f, search_mhz=10)
                tracked[mover] = (new_f, dd_new if dd_new is not None else -99.0)
            # 若无其他候选, mover 保持原位置

    # ---- 基线恢复: 未匹配的谐振子用相位峰重搜 (T≥70:200MHz, T<70:100MHz) ----
    for rname in RESONATOR_NAMES:
        if rname in tracked:
            continue
        pred = fd_predictions[rname]
        if pred is None:
            continue

        _recovery_search_mhz = 200 if temp_k >= 70 else 100

        # 确保相位峰已加载
        if temp_k not in _resonance_cache:
            try:
                pk = dict(PEAK_KWARGS, min_prominence=0.3, phase_diff_snr_threshold=1.0,
                         distance=20, min_phase_diff_support_points=1, min_phase_diff_width=1)
                peaks, _, _ = find_true_resonances(freq=freq_hz, s21=s21_complex, **pk)
                _resonance_cache[temp_k] = peaks
            except Exception:
                _resonance_cache[temp_k] = []

        best_f0, best_score = None, -999.0
        for p in _resonance_cache[temp_k]:
            pf_ghz = p["frequency"] / 1e9
            dist = abs(pf_ghz - pred) * 1000
            if dist >= _recovery_search_mhz:
                continue
            # 排除已被其他谐振子占用的相位峰
            if any(abs(pf_ghz - of) < 0.015 for of, _ in occupied_freqs):
                continue
            snr = p.get("phase_diff_snr", 1.0)
            score = snr * 80 - dist
            if score > best_score:
                best_score = score
                best_f0 = pf_ghz

        if best_f0 is not None:
            _, dd, _ = detect_dip_p90(freq_ghz, s21_db, best_f0, search_mhz=10)
            tracked[rname] = (best_f0, dd if dd is not None else -99.0)
            occupied_freqs.append((best_f0, rname))

    # ---- 频谱顺序验证: 高温时 FD 预测不可靠, P90 dip 扫描修正 ----
    if temp_k >= 65 and len(tracked) >= 4:
        # 统计追踪质量 — 浅 dip (<1.5 dB) 可能抓到了噪声而非真实谐振
        shallow_count = sum(1 for _r, (_f, d) in tracked.items() if d > -1.5)
        if shallow_count >= 1:
            # 使用 P90 全频段 dip 扫描 (比相位峰 SNR 更稳健, 不受 VNA 功率影响)
            p90_candidates = find_all_dips_p90(
                freq_ghz, s21_db, temp_k, prominence_db=0.8)

            all_preds = [fd_predictions[r] for r in RESONATOR_NAMES
                        if fd_predictions[r] is not None]
            if all_preds:
                min_exp = min(all_preds) - 0.3
                max_exp = max(all_preds) + 0.3

                # 为每个谐振子收集搜索窗口内的 P90 候选 dip
                resonator_candidates = {}
                for rname in RESONATOR_NAMES:
                    pred = fd_predictions.get(rname)
                    if pred is None:
                        continue
                    search_ghz = _search_mhz_for_temp(temp_k) / 1000.0
                    candidates = [
                        (f, d) for f, d, _ in p90_candidates
                        if min_exp <= f <= max_exp
                        and abs(f - pred) <= search_ghz
                        and d < -1.0
                    ]
                    # 按 dip 深度排序 (最深优先)
                    candidates.sort(key=lambda x: x[1])
                    resonator_candidates[rname] = candidates

                # 贪心分配: R1→R5 按频率升序, 每个取最深且 > 前一频率的候选
                new_tracked = {}
                prev_freq = min_exp - 0.01
                for rname in RESONATOR_NAMES:
                    candidates = resonator_candidates.get(rname, [])
                    best = None
                    for f, d in candidates:
                        if f > prev_freq + 0.005:  # 与前一谐振子至少间隔 5 MHz
                            best = (f, d)
                            break
                    if best is not None:
                        new_tracked[rname] = best
                        prev_freq = best[0]

                # 仅在修正覆盖 ≥4 个谐振子时采纳 (避免部分修正导致错位)
                if len(new_tracked) >= 4:
                    for rname, (pf, _d) in new_tracked.items():
                        _, dd, _ = detect_dip_p90(
                            freq_ghz, s21_db, pf, search_mhz=10)
                        new_tracked[rname] = (pf, dd if dd is not None else -99.0)
                    tracked = new_tracked

    # ---- 构建结果 ----
    results = []
    for rname in RESONATOR_NAMES:
        if rname in tracked:
            f0, dip = tracked[rname]
            results.append({"name": rname, "f0_ghz": f0, "dip_depth": dip})
        else:
            results.append({"name": rname, "f0_ghz": None, "dip_depth": None})

    return results

# =========================================================================
# 跨 VNA 功率追踪
# =========================================================================

def track_across_pv(traces, resonator_name, temp_k, f0_ref_ghz):
    """在固定激光功率下，追踪一个谐振子跨所有 VNA 功率的 f0。

    Args:
        traces: 按 VNA 功率升序排列的 trace 列表
                [{"pv": int, "freq": ndarray, "s21_complex": ndarray, "s21_db": ndarray}, ...]
        resonator_name: "R1".."R5"
        temp_k: 温度
        f0_ref_ghz: 最低 VNA 功率时的参考 f0 (GHz)

    Returns:
        {"pv_list": [...], "f0_ghz": [...], "dip_depth": [...], "flags": [...]}
    """
    # 初始化历史: 骨架 + 当前参考点
    skeleton_f0 = SCRAPS_F0[resonator_name]
    f0_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0)
                   if t <= temp_k + 15]

    pv_list = []
    f0_list = []
    dip_list = []
    flag_list = []

    f0_prev = f0_ref_ghz

    for trace in traces:
        pv = trace["pv"]
        freq_hz = trace["freq"] * 1e9
        s21_db = trace["s21_db"]
        s21_complex = trace["s21_complex"]

        # 用上一点作为历史外推的锚
        current_history = f0_history + [(temp_k, f0_prev)]

        f0, dip = track_one_resonator(
            freq_hz, s21_db, s21_complex, resonator_name, temp_k, current_history)

        # 步间跳变守卫 (同 track_across_pl, 跨 VNA 功率也用相同逻辑)
        MAX_STEP_MHZ = 30.0
        if f0 is not None and f0_prev is not None:
            delta_mhz = abs(f0 - f0_prev) * 1000
            if delta_mhz > MAX_STEP_MHZ:
                f0 = None
                dip = None

        # 温度自适应跟踪阈值
        if temp_k >= 70:
            track_threshold = -0.3
        elif temp_k >= 50:
            track_threshold = -0.7
        else:
            track_threshold = -1.0

        pv_list.append(pv)
        if f0 is not None and dip is not None and dip < track_threshold:
            f0_list.append(f0)
            dip_list.append(dip)
            flag_list.append("tracked")
            f0_prev = f0
        elif f0 is not None:
            f0_list.append(f0)
            dip_list.append(dip)
            flag_list.append("shallow")
        else:
            f0_list.append(np.nan)
            dip_list.append(np.nan)
            flag_list.append("lost")

    return {
        "pv_list": pv_list,
        "f0_ghz": f0_list,
        "dip_depth": dip_list,
        "flags": flag_list,
    }

# =========================================================================
# 跨激光功率追踪 (在同一 VNA 功率下)
# =========================================================================

def track_across_pl(s2p_files_by_pl, resonator_name, temp_k, f0_ref_ghz):
    """在固定 VNA 功率下，追踪一个谐振子跨所有激光功率的 f0。

    Args:
        s2p_files_by_pl: {pl_mw: (freq_hz, s21_db, s21_complex), ...}
        resonator_name: "R1".."R5"
        temp_k: 温度
        f0_ref_ghz: Pl=0 时的参考 f0

    Returns:
        {"pl_list": [...], "f0_ghz": [...], "dip_depth": [...], "flags": [...]}
    """
    skeleton_f0 = SCRAPS_F0[resonator_name]
    f0_history = [(t, f) for t, f in zip(SCRAPS_TEMPS, skeleton_f0)
                   if t <= temp_k + 15]

    pl_sorted = sorted(s2p_files_by_pl.keys())
    pl_list = []
    f0_list = []
    dip_list = []
    flag_list = []

    f0_prev = f0_ref_ghz

    for pl in pl_sorted:
        freq_hz, s21_db, s21_complex = s2p_files_by_pl[pl]

        current_history = f0_history + [(temp_k, f0_prev)]

        f0, dip = track_one_resonator(
            freq_hz, s21_db, s21_complex, resonator_name, temp_k, current_history)

        # 步间跳变守卫: 激光加热引起的 f0 偏移物理上限 ~2 MHz/mW,
        # 步长最大 2 mW → 预期 ≤4 MHz。阈值 30 MHz (>7× 余量)。
        MAX_STEP_MHZ = 30.0
        if f0 is not None and f0_prev is not None:
            delta_mhz = abs(f0 - f0_prev) * 1000
            if delta_mhz > MAX_STEP_MHZ:
                f0 = None
                dip = None

        # 温度自适应跟踪阈值
        if temp_k >= 70:
            track_threshold = -0.3
        elif temp_k >= 50:
            track_threshold = -0.7
        else:
            track_threshold = -1.0

        pl_list.append(pl)
        if f0 is not None and dip is not None and dip < track_threshold:
            f0_list.append(f0)
            dip_list.append(dip)
            flag_list.append("tracked")
            f0_prev = f0
        elif f0 is not None:
            f0_list.append(f0)
            dip_list.append(dip)
            flag_list.append("shallow")
        else:
            f0_list.append(np.nan)
            dip_list.append(np.nan)
            flag_list.append("lost")

    return {
        "pl_list": pl_list,
        "f0_ghz": f0_list,
        "dip_depth": dip_list,
        "flags": flag_list,
    }
