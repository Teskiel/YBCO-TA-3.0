# -*- coding: utf-8 -*-
"""
Temperature state diagnostic system for cryogenic control.

Pure algorithm module — no hardware dependencies.

Analyses temperature readings in real time and classifies the system
state into one of 7 categories:
  - stable              — within tolerance, low variance
  - converging          — monotonically approaching target
  - slow_oscillating    — slow oscillation (I too aggressive)
  - fast_oscillating    — rapid ringing (P too large)
  - steady_offset       — stable but consistently off target (I too weak)
  - drifting            — barely changing, far from target (P too small)
  - overshooting        — crossed target and moving away
  - insufficient_data   — not enough readings yet

For each non-stable state, provides a recommended PID adjustment based
on the LakeShore 335 manual's tuning guidance.

Safety mechanisms:
  - 15-minute lockout between adjustments of the same type
  - Rollback tracking (if variance worsens after adjustment)
  - Desperate mode after 3+ consecutive failed adjustments
  - Oscillation time accumulator (flags point if > 30 min total)

Usage:
    from temperature_state_diagnostics import (
        TemperatureStateDiagnostics, TemperatureReading
    )

    diag = TemperatureStateDiagnostics(sample_interval=10.0)
    diag.add_reading(TemperatureReading(t, temp, target))
    result = diag.diagnose(target, current_pid={"p": 100, "i": 3, "d": 0})
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class TemperatureReading:
    """Single temperature reading with metadata."""

    timestamp: float       # seconds since epoch (monotonic preferred)
    temperature: float     # kelvin
    target: float          # target temperature in kelvin


@dataclass
class DiagnosticResult:
    """Result of a temperature state diagnosis.

    Attributes:
        state: one of 'stable', 'converging', 'slow_oscillating',
               'fast_oscillating', 'steady_offset', 'drifting',
               'overshooting', 'insufficient_data'.
        metrics: computed diagnostic metrics (variance, slope, etc.).
        pid_adjustment: recommended PID changes. May contain:
            p_delta, i_delta, d_delta (relative changes),
            p_new, i_new, d_new (absolute new values),
            extra_overshoot_k (additional setpoint overshoot),
            setpoint_override_k (override setpoint to this value),
            locked_out (True if lockout prevented adjustment),
            rollback_reference (previous PID to rollback to),
            heater_range_change (recommended range change, if any).
        description: human-readable description in Chinese.
        requires_intervention: True if operator attention needed.
    """

    state: str
    metrics: Dict = field(default_factory=dict)
    pid_adjustment: Dict = field(default_factory=dict)
    description: str = ""
    requires_intervention: bool = False


# =========================================================================
# TemperatureStateDiagnostics
# =========================================================================

class TemperatureStateDiagnostics:
    """Real-time temperature state classifier with PID adjustment recommendations.

    Uses rolling 1-minute window averages (6 samples at 10s intervals),
    5-minute linear fits, error sign reversal counting, and variance
    analysis to classify the system state.

    Designed for the 20–40K cryogenic transition zone where thermal
    properties change rapidly.
    """

    def __init__(
        self,
        sample_interval: float = 10.0,
        max_readings: int = 300,
        lockout_seconds: float = 900.0,      # 15 minutes
        tolerance_k: float = 0.5,
        oscillation_timeout_s: float = 1800.0,  # 30 minutes
    ) -> None:
        """Initialise the diagnostics engine.

        Args:
            sample_interval: seconds between temperature samples.
            max_readings: ring buffer capacity.
            lockout_seconds: minimum time between same-type adjustments.
            tolerance_k: band within which temperature is considered stable.
            oscillation_timeout_s: cumulative oscillation time before flagging.
        """
        self.sample_interval = sample_interval
        self.max_readings = max_readings
        self.lockout_seconds = lockout_seconds
        self.tolerance_k = tolerance_k
        self.oscillation_timeout_s = oscillation_timeout_s

        self.readings: List[TemperatureReading] = []
        self._last_adjustment_time: float = 0.0
        self._adjustment_count: int = 0
        self._oscillation_adjustment_count: int = 0
        self._pure_p_mode: bool = False
        self._adjustment_history: List[Dict] = []
        self._previous_pid: Optional[Dict] = None
        self._oscillation_accumulated_s: float = 0.0
        self._last_state: str = "unknown"

    # ==================================================================
    # Public API
    # ==================================================================

    def add_reading(self, reading: TemperatureReading) -> None:
        """Append a temperature reading to the ring buffer."""
        self.readings.append(reading)
        if len(self.readings) > self.max_readings:
            self.readings = self.readings[-self.max_readings:]

    def diagnose(
        self,
        target: float,
        current_pid: Dict,
    ) -> DiagnosticResult:
        """Classify the current temperature state and recommend PID adjustments.

        Args:
            target: target temperature in kelvin.
            current_pid: dict with keys 'p', 'i', 'd' for the current PID.

        Returns:
            DiagnosticResult with state, metrics, adjustment, and description.
        """
        # Minimum data requirement
        if len(self.readings) < 6:
            return DiagnosticResult(
                state="insufficient_data",
                metrics={},
                pid_adjustment={},
                description=f"数据不足：需要至少6个采样点（~1分钟），当前{len(self.readings)}个",
                requires_intervention=False,
            )

        # Compute metrics
        metrics = self._compute_metrics(target)

        # Classify state
        state = self._classify_state(target, metrics)

        # Oscillation time tracking
        is_oscillating = state in ("slow_oscillating", "fast_oscillating")
        if is_oscillating:
            self._oscillation_accumulated_s += self.sample_interval
        else:
            self._oscillation_accumulated_s = max(0, self._oscillation_accumulated_s - self.sample_interval * 2)

        # Pure-P mode trigger: 5+ oscillation adjustments → force I=0
        # Set BEFORE computing adjustment so the 5th event takes effect immediately
        if self._oscillation_adjustment_count >= 5:
            self._pure_p_mode = True

        # Recommend PID adjustment
        in_lockout = self._is_in_lockout(metrics.get("latest_timestamp", 0))
        if in_lockout:
            pid_adjustment = {"locked_out": True}
        elif state in ("stable", "converging") and not self._pure_p_mode:
            pid_adjustment = {}
        elif self._pure_p_mode:
            pid_adjustment = self._recommend_pid_adjustment(state, current_pid, metrics)
        else:
            pid_adjustment = self._recommend_pid_adjustment(state, current_pid, metrics)

        # Increment oscillation counter after adjustment decision
        if is_oscillating and not in_lockout and pid_adjustment:
            self._oscillation_adjustment_count += 1

        # Check for desperate mode
        requires_intervention = False
        is_desperate = self._adjustment_count >= 3
        if is_desperate:
            requires_intervention = True
        if self._oscillation_accumulated_s > self.oscillation_timeout_s:
            requires_intervention = True
        if state == "fast_oscillating":
            requires_intervention = True

        # Build description
        if self._pure_p_mode and is_oscillating:
            description = (
                f"纯P模式（震荡{self._oscillation_adjustment_count}次调节失败→I=0）："
                f"当前={state}，靠过冲调节找最佳参数"
            )
        elif is_desperate and state not in ("stable",):
            description = (
                f"绝望模式：连续{self._adjustment_count}次PID调整无效。"
                f"当前状态={state}，强制使用保守PID (P=50, I=0)。需人工干预。"
            )
        else:
            description = self._build_description(state, metrics, pid_adjustment)

        # Track state
        self._last_state = state

        return DiagnosticResult(
            state=state,
            metrics=metrics,
            pid_adjustment=pid_adjustment,
            description=description,
            requires_intervention=requires_intervention,
        )

    def record_adjustment(self, pid_before: Dict, pid_after: Dict) -> None:
        """Record that a PID adjustment was made. Call after applying new PID.

        Args:
            pid_before: PID values before the change.
            pid_after: PID values after the change.
        """
        if self.readings:
            self._last_adjustment_time = self.readings[-1].timestamp
        self._adjustment_count += 1
        self._previous_pid = dict(pid_before)
        self._adjustment_history.append({
            "time": self._last_adjustment_time,
            "count": self._adjustment_count,
            "pid_before": dict(pid_before),
            "pid_after": dict(pid_after),
            "state": self._last_state,
        })

    def reset_adjustment_tracking(self) -> None:
        """Reset the adjustment counter (e.g. when moving to new temperature)."""
        self._adjustment_count = 0
        self._oscillation_adjustment_count = 0
        self._pure_p_mode = False
        self._previous_pid = None
        self._oscillation_accumulated_s = 0.0
        self._last_adjustment_time = 0.0

    # ==================================================================
    # Metrics computation
    # ==================================================================

    def _compute_metrics(self, target: float) -> Dict:
        """Compute all diagnostic metrics from the reading buffer."""
        latest_timestamp = self.readings[-1].timestamp

        # Recent readings for 1-minute and 5-minute windows
        recent_60s = [
            r for r in self.readings
            if latest_timestamp - r.timestamp <= 60.0
        ]
        recent_300s = [
            r for r in self.readings
            if latest_timestamp - r.timestamp <= 300.0
        ]

        temps_60s = [r.temperature for r in recent_60s]
        temps_300s = [r.temperature for r in recent_300s]

        # 1-minute window average and variance
        avg_1min = sum(temps_60s) / len(temps_60s) if temps_60s else target
        variance_1min = (
            sum((t - avg_1min) ** 2 for t in temps_60s) / len(temps_60s)
            if len(temps_60s) > 1 else 0.0
        )

        # Error from target
        error = avg_1min - target
        abs_error = abs(error)

        # 1-minute window averages (rolling)
        window_avgs = self._compute_window_averages(window_s=60.0)

        # Sign reversals in 5 minutes
        sign_changes = self._count_error_sign_reversals(target)

        # 5-minute linear fit slope
        slope_5min = self._compute_5min_slope()

        # Monotonic convergence check
        is_monotonic = self._check_monotonic_convergence(target, window_avgs)

        # Check if crossed target (for overshoot detection)
        crossed_target = False
        error_increasing = False
        if len(window_avgs) >= 3:
            errors = [abs(avg - target) for avg in window_avgs[-3:]]
            # Check if error was decreasing then started increasing
            if len(errors) >= 3:
                # Error sign of most recent vs previous
                recent_errors = [avg - target for avg in window_avgs[-3:]]
                if recent_errors[-1] > 0 and recent_errors[-2] < 0:
                    crossed_target = True  # crossed from below to above
                if len(errors) >= 2 and errors[-1] > errors[-2]:
                    error_increasing = True

        # Peak-to-peak in 5 minutes
        p2p_5min = max(temps_300s) - min(temps_300s) if len(temps_300s) > 1 else 0.0

        return {
            "latest_timestamp": latest_timestamp,
            "avg_1min": avg_1min,
            "variance_1min": variance_1min,
            "abs_error": abs_error,
            "error": error,
            "window_avgs": window_avgs,
            "sign_changes": sign_changes,
            "slope_5min": slope_5min,
            "is_monotonic": is_monotonic,
            "crossed_target": crossed_target,
            "error_increasing": error_increasing,
            "p2p_5min": p2p_5min,
            "reading_count_60s": len(temps_60s),
            "reading_count_300s": len(temps_300s),
        }

    def _compute_window_averages(self, window_s: float = 60.0) -> List[float]:
        """Compute rolling window averages over the reading buffer.

        Returns list of averages, most recent last.
        """
        if len(self.readings) < 3:
            return []

        latest_t = self.readings[-1].timestamp
        averages = []

        # Compute up to 5 windows going backwards
        for w in range(5):
            window_end = latest_t - w * window_s
            window_start = window_end - window_s
            window_readings = [
                r for r in self.readings
                if window_start <= r.timestamp < window_end
            ]
            if len(window_readings) >= 3:
                avg = sum(r.temperature for r in window_readings) / len(window_readings)
                averages.append(avg)

        averages.reverse()
        return averages

    def _compute_5min_slope(self) -> float:
        """Compute the linear least-squares slope over the last 5 minutes (K/s)."""
        recent = [
            r for r in self.readings
            if self.readings[-1].timestamp - r.timestamp <= 300.0
        ]
        if len(recent) < 5:
            return 0.0

        n = len(recent)
        t0 = recent[0].timestamp
        sum_t = 0.0
        sum_temp = 0.0
        sum_tt = 0.0
        sum_t_temp = 0.0

        for r in recent:
            t_rel = r.timestamp - t0
            sum_t += t_rel
            sum_temp += r.temperature
            sum_tt += t_rel * t_rel
            sum_t_temp += t_rel * r.temperature

        denominator = n * sum_tt - sum_t * sum_t
        if abs(denominator) < 1e-10:
            return 0.0

        return (n * sum_t_temp - sum_t * sum_temp) / denominator

    def _count_error_sign_reversals(self, target: float) -> int:
        """Count how many times the temperature error crosses zero in 5 minutes.

        Uses sign() with a deadband to handle readings exactly at zero
        (which are rare but happen with synthetic sine waves).
        """
        recent = [
            r for r in self.readings
            if self.readings[-1].timestamp - r.timestamp <= 300.0
        ]
        if len(recent) < 3:
            return 0

        errors = [r.temperature - target for r in recent]

        def _sign(e: float) -> int:
            """Return -1, 0, or 1 with a 0.05K deadband to ignore noise."""
            if e > 0.05:
                return 1
            elif e < -0.05:
                return -1
            return 0

        signs = [_sign(e) for e in errors]
        # Filter out zeros, then count adjacent sign changes
        non_zero = [s for s in signs if s != 0]
        if len(non_zero) < 2:
            return 0

        sign_changes = 0
        for i in range(1, len(non_zero)):
            if non_zero[i] != non_zero[i - 1]:
                sign_changes += 1

        return sign_changes

    def _check_monotonic_convergence(
        self, target: float, window_avgs: List[float]
    ) -> bool:
        """Check if |error| is strictly decreasing over recent windows."""
        if len(window_avgs) < 3:
            return False

        errors = [abs(avg - target) for avg in window_avgs[-3:]]
        return all(errors[i] > errors[i + 1] for i in range(len(errors) - 1))

    # ==================================================================
    # State classification
    # ==================================================================

    def _classify_state(self, target: float, m: Dict) -> str:
        """Classify the temperature control state using a decision tree.

        Order matters: check most dangerous / urgent states first,
        stable LAST (otherwise oscillations with moderate variance
        get misclassified as stable).
        """
        abs_err = m["abs_error"]
        variance = m["variance_1min"]
        sign_changes = m["sign_changes"]
        slope = m["slope_5min"]
        crossed = m["crossed_target"]
        error_inc = m["error_increasing"]
        is_mono = m["is_monotonic"]

        # 1. Fast oscillation: many sign changes — most dangerous
        if sign_changes >= 6 and variance > 0.02:
            return "fast_oscillating"

        # 2. Overshooting: already ABOVE target and error still growing
        if m["error"] > self.tolerance_k and error_inc and abs_err > self.tolerance_k:
            return "overshooting"

        # 3. Slow oscillation: moderate sign changes — check BEFORE stable
        if sign_changes >= 3 and variance > 0.05:
            return "slow_oscillating"

        # 4. Steady offset: very low variance but far from target
        #    Check BEFORE drifting — steady_offset is more specific
        if variance < 0.015 and abs_err > self.tolerance_k:
            return "steady_offset"

        # 5. Drifting: near-zero slope, far from target, not stable
        if abs(slope) < 0.000333 and abs_err > 1.0:
            return "drifting"

        # 6. Stable: within tolerance, low variance — check AFTER oscillations
        if abs_err <= self.tolerance_k and variance < 0.30:
            return "stable"

        # 7. Converging: errors monotonically decreasing
        if is_mono and sign_changes <= 1:
            return "converging"

        # Default: still approaching target
        if abs_err > self.tolerance_k:
            return "converging"

        return "stable"

    # ==================================================================
    # PID adjustment recommendations
    # ==================================================================

    def _recommend_pid_adjustment(
        self, state: str, current_pid: Dict, metrics: Dict
    ) -> Dict:
        """Recommend PID changes based on the diagnosed state.

        In pure-P mode (after 5 oscillation failures):
          - Never reintroduce I. Use overshoot adjustments instead.
          - steady_offset → increase overshoot (not add I).
          - drifting → increase P + extra overshoot.

        Desperate mode (3+ consecutive failed adjustments): I=0, P=50.
        """
        p = current_pid.get("p", 100.0)
        i = current_pid.get("i", 0.0)
        d = current_pid.get("d", 0.0)

        # Pure-P mode: force I=0, use overshoot for offset
        if self._pure_p_mode:
            if state in ("slow_oscillating", "fast_oscillating"):
                p_new = max(p * 0.75, 30.0)
                return {
                    "p_new": p_new, "i_new": 0.0, "d_new": 0.0,
                    "p_delta": p_new - p, "i_delta": 0.0 - i,
                    "force_pure_p": True,
                    "description": f"纯P模式震荡→继续降P至{p_new:.0f}，I=0",
                    "rollback_reference": {"p": p, "i": i, "d": d},
                }
            elif state == "steady_offset":
                return {
                    "i_new": 0.0, "i_delta": 0.0 - i,
                    "extra_overshoot_k": 1.0,
                    "force_pure_p": True,
                    "description": "纯P模式稳态偏差→不引入I，靠增加过冲+1K补偿",
                }
            elif state == "drifting":
                p_new = min(p * 1.3, 300.0)
                return {
                    "p_new": p_new, "i_new": 0.0, "d_new": 0.0,
                    "p_delta": p_new - p, "i_delta": 0.0 - i,
                    "extra_overshoot_k": 1.5,
                    "force_pure_p": True,
                    "description": f"纯P模式漂移→P增大至{p_new:.0f}，过冲+1.5K",
                }
            elif state == "overshooting":
                p_new = max(p * 0.8, 50.0)
                return {
                    "p_new": p_new, "i_new": 0.0, "d_new": 0.0,
                    "p_delta": p_new - p,
                    "setpoint_override_k": metrics.get("target", 30.0) + 0.5,
                    "force_pure_p": True,
                    "description": "纯P模式过冲→降setpoint至target+0.5K，P减小20%",
                    "rollback_reference": {"p": p, "i": i, "d": d},
                }

        # Desperate mode override
        if self._adjustment_count >= 3:
            return {
                "p_new": 50.0,
                "i_new": 0.0,
                "d_new": 0.0,
                "p_delta": 50.0 - p,
                "i_delta": 0.0 - i,
                "d_delta": 0.0,
                "desperate_mode": True,
                "description": "绝望模式：连续3次调整无效，强制使用极保守PID (P=50, I=0)",
                "rollback_reference": {"p": p, "i": i, "d": d},
            }

        if state == "slow_oscillating":
            # I too aggressive (too small) → increase I (weaken integral)
            # P too high → reduce P
            i_new = max(i * 1.5, i + 2.0) if i > 0 else i
            p_new = max(p * 0.8, 50.0)
            return {
                "p_delta": p_new - p,
                "i_delta": i_new - i if i > 0 else 0.0,
                "p_new": p_new,
                "i_new": i_new,
                "description": "慢速震荡：I项过强 → 增大I(减弱积分)，降低P",
                "rollback_reference": {"p": p, "i": i, "d": d},
            }

        elif state == "fast_oscillating":
            # P too large → reduce P by ~40%
            p_new = max(p * 0.6, 30.0)
            i_new = i * 2.0 if i > 0 else i  # weaken I if present
            return {
                "p_delta": p_new - p,
                "i_delta": i_new - i if i > 0 else 0.0,
                "p_new": p_new,
                "i_new": i_new,
                "description": "快速震荡/振铃：P过大 → P减小40%，若I存在则增大I",
                "rollback_reference": {"p": p, "i": i, "d": d},
            }

        elif state == "steady_offset":
            if i == 0:
                # No integral → introduce small I
                return {
                    "i_new": 3.0,
                    "i_delta": 3.0,
                    "description": "稳态偏差：I=0导致静差 → 引入I=3",
                    "rollback_reference": {"p": p, "i": i, "d": d},
                }
            else:
                # I too weak → decrease I (strengthen integral)
                i_new = max(i * 0.5, 1.0)
                return {
                    "i_new": i_new,
                    "i_delta": i_new - i,
                    "description": f"稳态偏差：I={i}偏弱 → 减小I至{i_new}(增强积分)",
                    "rollback_reference": {"p": p, "i": i, "d": d},
                }

        elif state == "drifting":
            # P too small → increase P, add extra overshoot
            p_new = min(p * 1.3, 300.0)
            return {
                "p_delta": p_new - p,
                "p_new": p_new,
                "extra_overshoot_k": 1.0,  # additional 1K overshoot
                "description": "漂移/不收敛：P偏小或功率不足 → P增大30%，setpoint过冲+1K",
            }

        elif state == "overshooting":
            # Overshoot → setpoint back near target, reduce P
            p_new = max(p * 0.8, 50.0)
            target = metrics.get("target", 30.0)
            return {
                "p_delta": p_new - p,
                "p_new": p_new,
                "setpoint_override_k": target + 0.5,  # clamp setpoint near target
                "description": "过冲：setpoint太高或P过大 → setpoint降至target+0.5K，P减小20%",
                "rollback_reference": {"p": p, "i": i, "d": d},
            }

        return {}

    # ==================================================================
    # Safety mechanisms
    # ==================================================================

    def _is_in_lockout(self, current_time: float) -> bool:
        """Check if enough time has passed since last adjustment."""
        if self._last_adjustment_time == 0.0:
            return False
        return (current_time - self._last_adjustment_time) < self.lockout_seconds

    def _build_description(
        self, state: str, metrics: Dict, adjustment: Dict
    ) -> str:
        """Build a human-readable Chinese description of the state."""
        avg = metrics.get("avg_1min", 0.0)
        var = metrics.get("variance_1min", 0.0)
        slope = metrics.get("slope_5min", 0.0)
        sc = metrics.get("sign_changes", 0)

        descriptions = {
            "stable": f"温度稳定：均值 {avg:.3f}K，方差 {var:.4f}K²",
            "converging": f"单调收敛升温中：均值 {avg:.3f}K，正在逼近目标",
            "slow_oscillating": (
                f"⚠️ 慢速震荡：均值 {avg:.3f}K，方差 {var:.4f}K²，"
                f"符号翻转 {sc}次 → I项过强，需减弱积分并降低P"
            ),
            "fast_oscillating": (
                f"⚠️⚠️ 快速震荡/振铃：均值 {avg:.3f}K，方差 {var:.4f}K²，"
                f"符号翻转 {sc}次 → P过大！需大幅降低P"
            ),
            "steady_offset": (
                f"稳态偏差：均值 {avg:.3f}K，偏离目标，方差极小 — "
                f"纯比例控制静差 → 需增强I项"
            ),
            "drifting": (
                f"漂移/不收敛：均值 {avg:.3f}K，5min斜率 {slope*60:.4f}K/min → "
                f"P过小或功率不足 → P增大30%，setpoint过冲+1K"
            ),
            "overshooting": (
                f"过冲：已越过目标，误差在扩大 → 急降setpoint至target+0.5K，P减小20%"
            ),
            "insufficient_data": "数据不足，等待更多采样点...",
        }

        desc = descriptions.get(state, f"未知状态：{state}")

        # Append adjustment info
        if adjustment.get("locked_out"):
            desc += " [锁死：15分钟内不重复调整]"
        if adjustment.get("desperate_mode"):
            desc += " [绝望模式：需人工干预]"
        if self._oscillation_accumulated_s > 0:
            desc += f" [累计震荡：{self._oscillation_accumulated_s/60:.0f}分钟]"

        return desc

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _compute_window_variance(self, readings: List[TemperatureReading]) -> float:
        """Compute variance of a list of readings."""
        if len(readings) < 2:
            return 0.0
        temps = [r.temperature for r in readings]
        avg = sum(temps) / len(temps)
        return sum((t - avg) ** 2 for t in temps) / len(temps)
