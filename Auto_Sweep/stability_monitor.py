# -*- coding: utf-8 -*-
"""
Temperature stability monitoring algorithms.

Pure algorithm module — no hardware or VISA dependencies.

Three independent concepts:
  - trending_stable: |window₀−window₂| ≤ 0.1K (over 2 min) → 趋于平稳
  - in_target_zone:  |1min_avg−target| ≤ 0.5K → 进入目标区间
  - steady_state:    3-min window max−min ≤ 0.1K → 进入稳态

stable = in_target_zone AND steady_state (两者并行，无先后关系)

Usage:
    from stability_monitor import AdvancedStabilityMonitor

    monitor = AdvancedStabilityMonitor()
    monitor.add_reading(30.05, target=30.0)
    result = monitor.check_stability(30.0)
    if result["stable"]:
        print("Ready to measure!")
"""

from dataclasses import dataclass
from time import time
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class TemperatureReading:
    timestamp: float
    temperature: float
    target: float


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class AdvancedStabilityMonitor:
    """Ring-buffer-based temperature stability analyser.

    Uses rolling 1-minute averages for two coarse checks, plus a 3-minute
    max-min window for steady-state detection:

      Phase A — trending_stable: |window₀−window₂| ≤ 0.1K (over 2 min) → 趋于平稳
      Phase B — in_target_zone:  |1min_avg−target| ≤ 0.5K → 进入目标区间
      Phase C — steady_state:    3-min max−min ≤ 0.1K → 进入稳态

    stable = in_target_zone AND steady_state (parallel, no ordering).
    """

    def __init__(self):
        self.readings: List[TemperatureReading] = []
        self.max_readings = 1000

    # ---- data management ----

    def add_reading(self, temperature: float, target: float):
        """Append a reading with the current timestamp."""
        reading = TemperatureReading(
            timestamp=time(),
            temperature=temperature,
            target=target,
        )
        self.readings.append(reading)
        if len(self.readings) > self.max_readings:
            self.readings.pop(0)

    def clear(self):
        """Discard all stored readings."""
        self.readings = []

    def _get_recent_readings(self, window_seconds: float = 60) -> List[TemperatureReading]:
        if not self.readings:
            return []
        current_time = time()
        return [r for r in self.readings if current_time - r.timestamp < window_seconds]

    # ---- stability method ----

    def check_stability(self, target_k: float, method: str = "custom",
                         settings: Optional[dict] = None) -> dict:
        """滚动 1 分钟均值稳定性检测，含三概念分离。

        三个独立概念：
          - trending_stable (条件2): |window₀−window₂| ≤ 0.1K (over 2 min) → 趋于平稳
          - in_target_zone  (条件3): |1min_avg−target| ≤ 0.5K → 进入目标区间
          - steady_state    (新):   3 分钟窗口 max−min ≤ 0.1K → 进入稳态

        ``stable`` = in_target_zone AND steady_state（两者并行，无先后关系）。
        ``ready_for_adjust`` = trending_stable（用于触发设定点过冲调整）。
        """
        if settings is None:
            from config import custom_stability_settings as settings

        avg_window = settings.get("avg_window_seconds", 60)
        avg_tolerance = settings.get("avg_tolerance_k", 0.5)
        delta_tolerance = settings.get("delta_tolerance_k", 0.1)
        final_band = settings.get("final_stable_band_k", 0.3)
        min_readings = settings.get("min_readings_required", 6)

        # 稳态判定参数
        from config import steady_state_max_min_k, steady_state_window_s
        ss_max_min = steady_state_max_min_k
        ss_window = steady_state_window_s

        current_time = time()

        # 回溯窗口内的读数
        all_recent = [r for r in self.readings if current_time - r.timestamp < 180]

        if len(all_recent) < min_readings:
            return {
                "stable": False,
                "method": "custom",
                "reason": f"Need at least {min_readings} readings, have {len(all_recent)}",
                "avg_temp": None,
                "avg_delta": None,
                "trending_stable": False,
                "in_target_zone": False,
                "steady_state": False,
                "max_min_3min": None,
                "in_final_band": False,
                "ready_for_adjust": False,
            }

        # ---- 滚动 1 分钟均值窗口 ----
        minute_windows = []
        for i in range(3):
            window_start = current_time - (i + 1) * avg_window
            window_end = current_time - i * avg_window
            window_readings = [
                r for r in all_recent
                if window_start <= r.timestamp < window_end
            ]
            if len(window_readings) >= 3:
                window_avg = sum(r.temperature for r in window_readings) / len(window_readings)
                minute_windows.append({
                    "start": window_start,
                    "end": window_end,
                    "avg": window_avg,
                    "count": len(window_readings),
                })

        minute_windows.sort(key=lambda x: x["end"], reverse=True)

        if not minute_windows:
            return {
                "stable": False,
                "method": "custom",
                "reason": "No valid 1-minute windows",
                "avg_temp": None,
                "avg_delta": None,
                "trending_stable": False,
                "in_target_zone": False,
                "steady_state": False,
                "max_min_3min": None,
                "in_final_band": False,
                "ready_for_adjust": False,
            }

        latest_avg = minute_windows[0]["avg"]

        # ---- 条件 1: 粗判（1-min 均值在 ±avg_tolerance 内） ----
        condition1 = abs(latest_avg - target_k) <= avg_tolerance

        # ---- 条件 2: 趋于平稳（跨 2 分钟窗口均值变化 < delta_tolerance） ----
        condition2 = False
        avg_delta = None
        if len(minute_windows) >= 3:
            # 窗口 [0]=最新, [2]=2分钟前 → 跨 2 分钟的比较
            avg_delta = abs(minute_windows[0]["avg"] - minute_windows[2]["avg"])
            condition2 = avg_delta <= delta_tolerance

        # ---- 条件 3: 进入目标区间（1-min 均值在 ±final_band 内） ----
        condition3 = abs(latest_avg - target_k) <= final_band

        # ---- 稳态判定: 3 分钟窗口 max-min ≤ ss_max_min ----
        steady_readings = [
            r for r in self.readings
            if current_time - r.timestamp <= ss_window
        ]
        max_min_3min = None
        steady_state = False
        if len(steady_readings) >= 6:
            temps = [r.temperature for r in steady_readings]
            max_min_3min = max(temps) - min(temps)
            steady_state = max_min_3min <= ss_max_min

        # ---- 汇总 ----
        trending_stable = condition2
        in_target_zone = condition3
        is_stable = in_target_zone and steady_state

        reasons = []
        if not condition1:
            reasons.append(f"1-min avg {latest_avg:.3f}K not within ±{avg_tolerance}K of target")
        if not condition2 and avg_delta is not None:
            reasons.append(f"2 分钟窗口漂移 {avg_delta:.3f}K > {delta_tolerance}K（未趋于平稳）")
        if not condition3:
            reasons.append(f"未进入目标区间 ±{final_band}K（Δ={abs(latest_avg - target_k):.3f}K）")
        if not steady_state:
            if max_min_3min is not None:
                reasons.append(f"未进入稳态: 3min max-min={max_min_3min:.3f}K > {ss_max_min}K")
            else:
                reasons.append(f"稳态数据不足（需 ≥6 个读数在 {ss_window}s 内）")

        return {
            "stable": is_stable,
            "method": "custom",
            "avg_temp": latest_avg,
            "avg_delta": avg_delta,
            "trending_stable": trending_stable,
            "in_target_zone": in_target_zone,
            "steady_state": steady_state,
            "max_min_3min": max_min_3min,
            "in_final_band": in_target_zone,
            "ready_for_adjust": trending_stable,
            "reason": "Stable" if is_stable else f'Failed: {", ".join(reasons)}',
            "minute_windows": minute_windows,
        }


    # ------------------------------------------------------------------
    # 快速预检 — 恢复时判断是否可跳过 overshoot
    # ------------------------------------------------------------------

    @staticmethod
    def fast_stability_check(read_temperature_fn, target_k: float) -> dict:
        """快速预检：判断是否可跳过 overshoot（恢复场景专用）。

        两阶段：
          Phase A（预检）: 2 次读数，间隔 10s
            → 两次都在 target ±1.0K 内 → 进入 Phase B
            → 否则 → {"skip_overshoot": False, "reason": "far_from_target"}

          Phase B（快速稳判）: 60s 内每 10s 读一次（共 ~6 个读数）
            → max − min ≤ 0.2K AND avg 在 target ±0.5K 内
              → {"skip_overshoot": True, "avg_temp": avg, ...}
            → 否则
              → {"skip_overshoot": False, "reason": "unstable"}

        Args:
            read_temperature_fn: 无参 callable，返回 float (K) 或 None
            target_k: 目标温度 (K)

        Returns:
            dict with keys:
                skip_overshoot: bool
                reason: str
                avg_temp: float or None
                readings_count: int
                elapsed_s: float
        """
        import time as _time

        PRE_CHECK_INTERVAL_S = 10.0
        PRE_CHECK_BAND_K = 1.0
        FAST_WINDOW_S = 60.0
        FAST_MAX_MIN_K = 0.2
        FAST_AVG_BAND_K = 0.5

        # ---- Phase A: 2 次预检读数 ----
        pre_readings = []
        for _ in range(2):
            t = read_temperature_fn()
            if t is not None:
                pre_readings.append(t)
            _time.sleep(PRE_CHECK_INTERVAL_S)

        if len(pre_readings) < 2:
            return {
                "skip_overshoot": False,
                "reason": "pre_check_read_failed",
                "avg_temp": None,
                "readings_count": len(pre_readings),
                "elapsed_s": PRE_CHECK_INTERVAL_S * 2,
            }

        # 两次都在 target ±1K 内才进入 Phase B
        if not all(abs(t - target_k) <= PRE_CHECK_BAND_K for t in pre_readings):
            return {
                "skip_overshoot": False,
                "reason": "far_from_target",
                "avg_temp": sum(pre_readings) / len(pre_readings),
                "readings_count": 2,
                "elapsed_s": PRE_CHECK_INTERVAL_S * 2,
            }

        # ---- Phase B: 60s 快速稳判 ----
        fast_readings = list(pre_readings)
        fast_start = _time.monotonic()
        fast_iterations = int(FAST_WINDOW_S / PRE_CHECK_INTERVAL_S)

        for _ in range(fast_iterations):
            _time.sleep(PRE_CHECK_INTERVAL_S)
            t = read_temperature_fn()
            if t is not None:
                fast_readings.append(t)

        if len(fast_readings) < 4:  # 至少需要 4 个读数才能判稳
            return {
                "skip_overshoot": False,
                "reason": "insufficient_data",
                "avg_temp": (sum(fast_readings) / len(fast_readings)
                             if fast_readings else None),
                "readings_count": len(fast_readings),
                "elapsed_s": _time.monotonic() - fast_start + PRE_CHECK_INTERVAL_S * 2,
            }

        avg_temp = sum(fast_readings) / len(fast_readings)
        max_min = max(fast_readings) - min(fast_readings)
        avg_delta = abs(avg_temp - target_k)

        if max_min <= FAST_MAX_MIN_K and avg_delta <= FAST_AVG_BAND_K:
            return {
                "skip_overshoot": True,
                "reason": "already_stable",
                "avg_temp": avg_temp,
                "max_min_k": max_min,
                "avg_delta_k": avg_delta,
                "readings_count": len(fast_readings),
                "elapsed_s": _time.monotonic() - fast_start + PRE_CHECK_INTERVAL_S * 2,
            }

        return {
            "skip_overshoot": False,
            "reason": "unstable",
            "avg_temp": avg_temp,
            "max_min_k": max_min,
            "avg_delta_k": avg_delta,
            "readings_count": len(fast_readings),
            "elapsed_s": _time.monotonic() - fast_start + PRE_CHECK_INTERVAL_S * 2,
        }


# =============================================================================
# 去偏温度稳定性等级 — 后处理批量评估
# =============================================================================
#
# 与 AdvancedStabilityMonitor（实时在线判定）不同，此函数用于：
#   1. 实验完成后评估一批已测温度点的稳定性
#   2. 跨实验对比不同温度点的控温质量
#   3. 为 plot / 报告提供标准化的稳定性标注
#
# 核心思想：
#   - 偏移 (offset) 和 精度 (precision) 解耦
#   - 偏移由 overshoot 算法控制，不影响稳定性等级
#   - 等级基于 |Δ| = |actual − mean|，即"离自己中心多远"
#   - 双指标 P50 / P95 防止单一 outlier 扭曲整体评价
#
# 等级阈值（可调）：
#     S    P50 ≤ 0.010K    P95 ≤ 0.030K   超稳定 — 噪声接近 Lakeshore 极限
#     A    P50 ≤ 0.020K    P95 ≤ 0.050K   优秀 — 适合发表
#     B    P50 ≤ 0.040K    P95 ≤ 0.100K   良好 — 常规实验标准
#     C    P50 ≤ 0.080K    P95 ≤ 0.200K   可用 — 适合趋势分析
#     D    P50 ≤ 0.150K    P95 ≤ 0.350K   边缘 — 可用但建议标注
#     F    超过 D          超过 D         不稳定 — 建议重测
#
# 显示规则：
#     P50 和 P95 等级差 ≥ 2 → "S/B" (核心 S，尾部拖至 B)
#     等级差 = 1           → "A→B" (边界模糊)
#     等级差 = 0           → "B"   (均匀分布)

def grade_stability(actuals, target_k, bands=None):
    """Evaluate de-biased temperature stability for a batch of measurements.

    Parameters
    ----------
    actuals : list[float]
        Actual temperature readings (K) from a completed temperature point.
        Must have ≥ 2 values.
    target_k : float
        Target temperature (K). Used only for reporting offset; does
        **not** affect the grade (which is de-biased to the sample mean).
    bands : list[tuple] or None
        Optional custom grade bands. Each tuple is
        ``(p50_max, p95_max, grade_char, label)``.
        If None, the default bands above are used.

    Returns
    -------
    dict with keys:
        n           : int     — number of data points
        mean        : float   — sample mean (K)
        offset      : float   — mean − target (K)
        p50         : float   — median |actual − mean| (K)
        p95         : float   — 95th percentile |actual − mean| (K)
        p99         : float   — 99th percentile (K, n≥100 only)
        worst       : float   — max |actual − mean| (K)
        p50_grade   : str     — grade letter for P50
        p95_grade   : str     — grade letter for P95
        display     : str     — composite grade string ("S/B", "B", etc.)
        label       : str     — human-readable grade description
        tail_gap    : int     — P95 grade − P50 grade in grade steps
        detail      : str     — one-line assessment in English
    """
    n = len(actuals)
    if n < 2:
        raise ValueError("Need at least 2 temperature readings")

    # ---- de-biased statistics ----
    mean = sum(actuals) / n
    offset = mean - target_k
    deltas = sorted(abs(a - mean) for a in actuals)

    p50 = deltas[n // 2]
    p95 = deltas[int(n * 0.95)] if n >= 20 else deltas[-1]
    p99 = deltas[int(n * 0.99)] if n >= 100 else deltas[-1]
    worst = deltas[-1]

    # ---- default grade bands (relaxed for cryogenic reality) ----
    if bands is None:
        bands = [
            (0.010, 0.030, 'S', 'ultra-stable — noise near Lakeshore limit'),
            (0.020, 0.050, 'A', 'excellent — suitable for publication'),
            (0.040, 0.100, 'B', 'good — routine experiment standard'),
            (0.080, 0.200, 'C', 'acceptable — usable for trend analysis'),
            (0.150, 0.350, 'D', 'marginal — usable but annotate'),
        ]

    # ---- first-match grading (strictest band wins) ----
    p50_grade = 'F'
    for p50_max, _p95_max, g, _gl in bands:
        if p50 <= p50_max:
            p50_grade = g
            break

    p95_grade = 'F'
    grade_label = 'unstable — recommend retest'
    for _p50_max, p95_max, g, gl in bands:
        if p95 <= p95_max:
            p95_grade = g
            grade_label = gl
            break

    # ---- composite display ----
    _go = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'F': 5}
    tail_gap = _go.get(p95_grade, 5) - _go.get(p50_grade, 5)

    if tail_gap >= 2:
        core_pct = 100 * sum(1 for d in deltas if d <= p50) / n
        display = f'{p50_grade}/{p95_grade}'
        detail = (f'core {p50_grade}-grade ({core_pct:.0f}% pts), '
                  f'tail drags to {p95_grade}')
    elif tail_gap == 1:
        display = f'{p50_grade}>{p95_grade}'
        detail = f'slight tail, {p50_grade}/{p95_grade} boundary'
    else:
        display = p95_grade
        detail = f'uniform {p95_grade}-grade distribution'

    return {
        'n': n,
        'mean': mean,
        'offset': offset,
        'p50': p50,
        'p95': p95,
        'p99': p99,
        'worst': worst,
        'p50_grade': p50_grade,
        'p95_grade': p95_grade,
        'display': display,
        'label': grade_label,
        'tail_gap': tail_gap,
        'detail': detail,
    }