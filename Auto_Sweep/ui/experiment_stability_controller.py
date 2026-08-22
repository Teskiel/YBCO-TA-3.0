# -*- coding: utf-8 -*-
"""
实验稳定性控制器 — 2 阶段 + 并行双轨判定。

核心理念：
  - PID 参数按温区硬编码，**永不调整**
  - 三概念分离：趋于平稳 / 进入目标区间 / 进入稳态
  - 只有「进入目标区间」且「进入稳态」同时满足才可测量

2 阶段轮询：
  Phase 1 (sparse) — 低频（20s），等待 trending_stable
  Phase 2 (fine)   — 高频（5s）， 并行判 in_target_zone + steady_state

过冲调整：
  触发条件: trending_stable=True AND in_target_zone=False
  冷却时间: 120s 内不重复调整
  调整量:   overshoot = base + (target − avg)，钳位 [base, MAX_OVERSHOOT_K]

用法：
    ctrl = ExperimentStabilityController()
    ctrl.setup(target_k=30.0, current_temperature=25.0)

    # Phase 1: 每 30s
    ctrl.add_reading(actual_k)
    result = ctrl.check(elapsed_s)
    if ctrl.phase == "fine":
        ...  # 切换到 5s 轮询

    # Phase 2: 每 5s
    if result.stable:  测量()
    sp = ctrl.needs_setpoint_adjustment()
    if sp:  lakeshore.set_temperature(sp, loop=1)
"""

import time as _time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional

from stability_monitor import AdvancedStabilityMonitor


# =========================================================================
# 数据定义
# =========================================================================

class StabilityPhase(str, Enum):
    """轮询阶段。"""
    SPARSE = "sparse"   # 低频 — 等待趋于平稳
    FINE = "fine"       # 高频 — 并行判目标区间 + 稳态


@dataclass
class StabilityResult:
    """稳定性检查的结果。"""
    stable: bool                     # in_target_zone AND steady_state 同时满足
    reason: str                      # "stable", "waiting", "good_enough", "timeout"
    phase: StabilityPhase            # 当前轮询阶段
    avg_temp: float                  # 平均温度
    final_temp: float = 0.0
    stable_duration_s: float = 0.0
    total_elapsed_s: float = 0.0
    setpoint_adjustments: int = 0    # 已应用的设定点调整次数


# =========================================================================
# ExperimentStabilityController
# =========================================================================

class ExperimentStabilityController:
    """2 阶段稳定性控制器：固定 PID + 并行双轨判定。

    PID 参数永不改变，仅通过设定点过冲推动温度。
    三概念分离：趋于平稳 → 进入目标区间 + 进入稳态 → 可测量。

    支持冷却模式：当 current > target + COOLING_DETECTION_BAND_K 时，
    跳过 overshoot 调整，设定点 = target，依靠自然冷却使温度下降。
    """

    # 默认参数
    MAX_OVERSHOOT_K = 10.0
    GOOD_ENOUGH_BAND_K = 0.5
    STABLE_HOLD_SECONDS = 120
    MAX_WAIT_SECONDS = 30 * 60            # FINE 阶段 + 总超时 (可被 Dashboard override)
    SPARSE_MAX_WAIT_SECONDS = 90 * 60     # SPARSE 阶段独立超时
    OVERSHOOT_COOLDOWN_S = 120       # 过冲调整冷却时间
    MAX_OVERSHOOT_ADJUSTMENTS = 2    # 已废弃 — 不再使用计数上限，改用 OVERSHOOT_TARGET_BAND_K
    GOOD_ENOUGH_PHASE2_TIMEOUT_S = 600  # Phase 2 中 10 分钟后 good_enough
    OVERSHOOT_TARGET_BAND_K = 0.7  # |avg−target| ≤ 此值 → 停止 overshoot 调整

    # Phase 1 → Phase 2 转换判据（稀疏轮询下不做分钟窗口分析）
    SPARSE_BAND_K = 1.0              # 原始读数在 ±1.0K 内即认为接近目标
    SPARSE_MIN_READINGS = 4          # 需要最近 4 个读数 (~80s at 20s polling)
    SPARSE_MIN_TIME_S = 60           # 最少等待 60 秒再判断

    # 冷却方向检测
    COOLING_DETECTION_BAND_K = 5.0   # |current − target| > 此值且 current > target → 冷却模式
    COOLING_SPARSE_TIMEOUT_S = 180 * 60  # 冷却 SPARSE 超时 (180 min, 比 90 min 更宽松)
    COOLING_PROGRESS_INTERVAL_S = 60     # 冷却进度日志间隔

    def __init__(self, log_callback=None):
        self._target_k: float = 0.0
        self._phase: StabilityPhase = StabilityPhase.SPARSE
        self._monitor = AdvancedStabilityMonitor()
        self._fixed_pid: Dict[str, float] = {"p": 100.0, "i": 0.0, "d": 0.0}
        self._base_overshoot: float = 0.0
        self._current_overshoot: float = 0.0
        self._setpoint_adjust_count: int = 0
        self._last_overshoot_time: float = 0.0
        self._last_stable_time: Optional[float] = None
        self._start_time: float = 0.0
        self._phase2_entry_time: float = 0.0
        self._adj_returned_this_cycle: bool = False
        self._initial_setpoint_written: bool = False
        self.skip_zone_check: bool = False   # 需求 C: 4K 跳过温度区间检定
        self._log_callback = log_callback     # 可选诊断回调
        # 节流：同原因至少间隔此秒数才再次输出
        self._last_diag_time: float = 0.0
        self._last_diag_reason: str = ""
        # overshoot 学习: target_k → 上次稳定时的最终 overshoot 值
        self._overshoot_learning: Dict[float, float] = {}
        # 冷却模式：current ≫ target，无需 overshoot，自然冷却等待
        self.is_cooling: bool = False
        self._last_cooling_progress_time: float = 0.0

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def setup(self, target_k: float, current_temperature: float):
        """为新温度目标初始化。

        根据温区查找固定 PID 和基础过冲。
        检测冷却方向：当 current > target + COOLING_DETECTION_BAND_K 时，
        进入冷却模式（无 overshoot，自然冷却等待）。

        Args:
            target_k: 目标温度 (K)
            current_temperature: 当前实际温度 (K)
        """
        import config

        self._target_k = target_k
        self._phase = StabilityPhase.SPARSE
        self._monitor.clear()
        self._last_stable_time = None
        self._last_overshoot_time = 0.0
        self._start_time = 0.0
        self._phase2_entry_time = 0.0
        self._setpoint_adjust_count = 0
        self._adj_returned_this_cycle = False
        self._initial_setpoint_written = False
        self._last_cooling_progress_time = 0.0

        # ---- 冷却方向检测 ----
        cooling_delta = current_temperature - target_k
        self.is_cooling = (cooling_delta > self.COOLING_DETECTION_BAND_K)

        zone = self._find_zone(target_k)
        self._fixed_pid = {"p": zone["p"], "i": zone["i"], "d": zone["d"]}
        self._base_overshoot = zone["base_overshoot_k"]

        if self.is_cooling:
            # 冷却模式：无需 overshoot 推动，设定点 = 目标温度
            self._current_overshoot = 0.0
            if self._log_callback:
                self._log_callback(
                    f"  [冷却模式] 当前 {current_temperature:.1f}K → "
                    f"目标 {target_k:.1f}K ({cooling_delta:.1f}K 差距)，"
                    f"设定点={target_k:.1f}K（无过冲），"
                    f"预计速率 ~0.2 K/min，约需 {cooling_delta / 0.2:.0f} min")
        elif target_k in self._overshoot_learning:
            # 优先使用学习到的 overshoot（上次在此温度点稳定后的最终值）
            self._current_overshoot = self._overshoot_learning[target_k]
        else:
            self._current_overshoot = zone["base_overshoot_k"]

    def add_reading(self, temperature: float):
        """添加温度读数到稳定性监视器。

        Args:
            temperature: 当前实际温度 (K)
        """
        now = _time.monotonic()
        if self._start_time == 0.0:
            self._start_time = now

        self._monitor.add_reading(temperature, self._target_k)

    def check(self, elapsed_s: float) -> StabilityResult:
        """检查稳定性并管理阶段转换。

        Phase 1 (sparse, 20s): 简单判据 — 最近 N 个原始读数在 ±1K 内即可
        切换到 Phase 2，不依赖分钟窗口分析（20s 轮询密度已足够形成分钟窗口）。

        Phase 2 (fine, 5s): 完整双轨判定 — in_target_zone AND steady_state。

        Args:
            elapsed_s: 自稳定性等待开始以来的总秒数

        Returns:
            StabilityResult — 包含稳定状态、原因、当前阶段
        """
        now = _time.monotonic()

        # ---- 阶段转换: SPARSE → FINE（简化判据，不依赖 check_stability） ----
        if self._phase == StabilityPhase.SPARSE:
            if self._sparse_ready(elapsed_s):
                self._phase = StabilityPhase.FINE
                self._phase2_entry_time = elapsed_s
                # SPARSE 读数保留，180s 滑动窗口内自然老化退出。
                # 不 clear() 以避免 trending_stable 重新累积 ~130s 死时间。
                self._last_stable_time = None

        # 底层稳定性判定
        stability = self._monitor.check_stability(self._target_k)
        in_target_zone = stability.get("in_target_zone", False)
        steady_state = stability.get("steady_state", False)
        avg_temp = stability.get("avg_temp", self._target_k)
        if avg_temp is None:
            avg_temp = self._target_k

        # ---- 稳定判定: 双轨同时满足（4K 豁免时仅需 steady_state） ----
        _stable_condition = (steady_state if self.skip_zone_check
                             else (in_target_zone and steady_state))
        if _stable_condition:
            if self._last_stable_time is None:
                self._last_stable_time = now
            hold_duration = now - self._last_stable_time
            if hold_duration >= self.STABLE_HOLD_SECONDS:
                return StabilityResult(
                    stable=True,
                    reason="stable",
                    phase=self._phase,
                    avg_temp=avg_temp,
                    final_temp=avg_temp,
                    stable_duration_s=hold_duration,
                    total_elapsed_s=elapsed_s,
                    setpoint_adjustments=self._setpoint_adjust_count,
                )
        else:
            self._last_stable_time = None

        # ---- good_enough 回退: Phase 2 中 10 分钟后 in_target_zone 但未稳态 ----
        # 4K 豁免时跳过 good_enough（不要求进入目标区间）
        if (not self.skip_zone_check
                and self._phase == StabilityPhase.FINE
                and in_target_zone
                and not steady_state):
            time_in_phase2 = elapsed_s - self._phase2_entry_time
            if time_in_phase2 >= self.GOOD_ENOUGH_PHASE2_TIMEOUT_S:
                return StabilityResult(
                    stable=False,
                    reason="good_enough",
                    phase=self._phase,
                    avg_temp=avg_temp,
                    final_temp=avg_temp,
                    total_elapsed_s=elapsed_s,
                    setpoint_adjustments=self._setpoint_adjust_count,
                )

        # ---- SPARSE 阶段独立超时（冷却模式 180 min，正常 90 min） ----
        _sparse_timeout = (
            self.COOLING_SPARSE_TIMEOUT_S if self.is_cooling
            else self.SPARSE_MAX_WAIT_SECONDS
        )
        if self._phase == StabilityPhase.SPARSE \
                and elapsed_s >= _sparse_timeout:
            # 使用最后实际读数作为 avg_temp，而非 target_k 默认值
            last_temp = self._target_k
            if self._monitor.readings:
                last_temp = self._monitor.readings[-1].temperature
            return StabilityResult(
                stable=False,
                reason="timeout",
                phase=self._phase,
                avg_temp=last_temp,
                final_temp=last_temp,
                total_elapsed_s=elapsed_s,
                setpoint_adjustments=self._setpoint_adjust_count,
            )

        # ---- FINE 阶段超时（MAX_WAIT_SECONDS，可被 Dashboard override） ----
        if self._phase == StabilityPhase.FINE and elapsed_s >= self.MAX_WAIT_SECONDS:
            last_temp = self._target_k
            if self._monitor.readings:
                last_temp = self._monitor.readings[-1].temperature
            return StabilityResult(
                stable=False,
                reason="timeout",
                phase=self._phase,
                avg_temp=last_temp,
                final_temp=last_temp,
                total_elapsed_s=elapsed_s,
                setpoint_adjustments=self._setpoint_adjust_count,
            )

        # ---- 仍在等待 ----
        return StabilityResult(
            stable=False,
            reason="waiting",
            phase=self._phase,
            avg_temp=avg_temp,
            final_temp=avg_temp,
            total_elapsed_s=elapsed_s,
            setpoint_adjustments=self._setpoint_adjust_count,
        )

    def needs_setpoint_adjustment(self) -> Optional[float]:
        """返回新的设定点温度（如果需要调整），否则 None。

        调整不再有计数上限 — 终止条件由 |avg − target| ≤ OVERSHOOT_TARGET_BAND_K
        控制。只要温度尚未进入目标区间且趋势平稳，就可以继续调整。

        双向调整（v1.2）：
          - delta = target − avg > 0（温度低于目标）→ overshoot 增大，推高 setpoint
          - delta = target − avg < 0（温度越过目标）→ overshoot 减小，缩回 setpoint
          - 从 _current_overshoot 出发累积 delta，钳位 [0, MAX_OVERSHOOT_K]
          - ramp rate 0.2K/min 控制实际的升/降温速率，无需操作 heater range

        冷却模式（is_cooling=True）：
          首次调用后始终返回 None，不进行任何 overshoot 调整。
          自然冷却不需要 setpoint 推动，只需等待温度降到目标附近。

        触发条件（同时满足）：
          1. trending_stable（趋于平稳）
          2. |avg − target| > OVERSHOOT_TARGET_BAND_K（尚未进入目标区间）
             OR 温度低于目标 |avg < target（即使已在 band 内——避免死区）
          3. 距上次调整 ≥ OVERSHOOT_COOLDOWN_S

        条件 2 设计意图：Overshoot 调整的停止条件是"温度高于目标且偏差在 0.7K
        以内"。仅"温度低于目标但偏差在 0.7K 以内"时不应停止——此时 P-only
        稳态误差很可能落在 0.5K 测量熔断阈值与 0.7K band 之间，形成死锁。

        Returns:
            新的设定点温度 (K)，或 None
        """
        # 冷却模式：首次调用返回 target（无 overshoot），之后不再调整
        if self.is_cooling:
            if not self._initial_setpoint_written:
                self._initial_setpoint_written = True
                now = _time.monotonic()
                self._last_overshoot_time = now
                return self._target_k  # 设定点 = 目标温度，不加 overshoot
            return None

        # 首次调用 — 无条件返回初始设定点
        if not self._initial_setpoint_written:
            self._initial_setpoint_written = True
            now = _time.monotonic()
            self._last_overshoot_time = now
            return self._calculate_setpoint_from_overshoot()

        # 后续调整需满足: trending_stable AND |avg − target| > 目标区间
        stability = self._monitor.check_stability(self._target_k)
        trending_stable = stability.get("trending_stable", False)
        avg_temp = stability.get("avg_temp", self._target_k)
        if avg_temp is None:
            avg_temp = self._target_k
        delta_k = abs(avg_temp - self._target_k)
        now = _time.monotonic()
        cooldown_remaining = max(
            0.0, self.OVERSHOOT_COOLDOWN_S - (now - self._last_overshoot_time))

        if not trending_stable:
            self._diag("trending_stable=False", avg_temp, delta_k,
                       cooldown_remaining)
            return None
        if delta_k <= self.OVERSHOOT_TARGET_BAND_K and avg_temp >= self._target_k:
            self._diag("in_target_band", avg_temp, delta_k,
                       cooldown_remaining)
            return None

        # 冷却时间保护
        if cooldown_remaining > 0:
            self._diag("cooldown", avg_temp, delta_k, cooldown_remaining)
            return None

        # 双向过冲调整: delta>0 增大, delta<0 减小, 钳位 [0, MAX_OVERSHOOT_K]
        delta = self._target_k - avg_temp
        self._current_overshoot = max(0.0, min(
            self._current_overshoot + delta,
            self.MAX_OVERSHOOT_K,
        ))
        self._setpoint_adjust_count += 1
        self._last_overshoot_time = now

        return self._calculate_setpoint_from_overshoot()

    def get_fixed_pid(self) -> Dict[str, float]:
        """返回当前温区的固定 PID 参数（永不改变）。"""
        return dict(self._fixed_pid)

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def phase(self) -> StabilityPhase:
        """当前轮询阶段（sparse / fine）。"""
        return self._phase

    @property
    def current_overshoot(self) -> float:
        """当前过冲量 (K)。"""
        return self._current_overshoot

    @property
    def base_overshoot(self) -> float:
        """基础过冲量 (K)。"""
        return self._base_overshoot

    @property
    def setpoint_adjust_count(self) -> int:
        return self._setpoint_adjust_count

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _diag(self, reason: str, avg_temp: float, delta_k: float,
              cooldown_remaining: float):
        """输出过冲调整跳过诊断（节流：同原因 ≥60s 才再次输出）。

        Args:
            reason: 跳过原因标识（trending_stable=False / in_target_band / cooldown）
            avg_temp: 当前平均温度 (K)
            delta_k: |avg − target| (K)
            cooldown_remaining: 冷却剩余秒数 (0 表示冷却已完成)
        """
        if self._log_callback is None:
            return
        now = _time.monotonic()
        if (reason == self._last_diag_reason
                and (now - self._last_diag_time) < 60.0):
            return  # 节流：同原因 60s 内不重复输出
        self._last_diag_reason = reason
        self._last_diag_time = now

        reason_map = {
            "trending_stable=False":
                f"trending_stable=False（温度尚未趋于平稳）",
            "in_target_band":
                f"|avg−target|={delta_k:.3f}K ≤ "
                f"band={self.OVERSHOOT_TARGET_BAND_K}K（已进入目标区间）",
            "cooldown":
                f"冷却剩余 {cooldown_remaining:.0f}s / "
                f"{self.OVERSHOOT_COOLDOWN_S}s",
        }
        reason_text = reason_map.get(reason, reason)

        self._log_callback(
            f"  [诊断] 跳过过冲调整 #{self._setpoint_adjust_count + 1}: "
            f"{reason_text}, avg={avg_temp:.3f}K, "
            f"|avg−target|={delta_k:.3f}K"
        )

    def log_cooling_progress(self):
        """输出冷却进度日志（节流：≥COOLING_PROGRESS_INTERVAL_S 才输出）。

        冷却模式下由 ExperimentWorker 轮询调用，向用户展示降温进度。
        """
        if not self.is_cooling or self._log_callback is None:
            return
        now = _time.monotonic()
        if (now - self._last_cooling_progress_time
                < self.COOLING_PROGRESS_INTERVAL_S):
            return
        self._last_cooling_progress_time = now

        readings = self._monitor.readings
        if not readings:
            return
        current = readings[-1].temperature
        remaining = current - self._target_k
        if remaining <= 0:
            return  # 温度已达或低于目标

        # 估算冷却速率（最近 5 分钟的趋势）
        rate = None
        if len(readings) >= 15:  # SPARSE 20s → 15 reads ≈ 5 min
            recent = readings[-15:]
        elif len(readings) >= 5:
            recent = readings[-5:]
        else:
            recent = readings
        if len(recent) >= 2:
            dt = recent[-1].timestamp - recent[0].timestamp
            dtemp = recent[-1].temperature - recent[0].temperature
            if dt > 60 and abs(dtemp) > 0.01:
                rate = dtemp / (dt / 60.0)  # K/min (负值 = 降温)

        if rate is not None and rate < 0:
            eta_min = remaining / abs(rate)  # 剩余时间（分钟）
            self._log_callback(
                f"  [冷却进度] {current:.1f}K → {self._target_k:.1f}K "
                f"(Δ={remaining:.1f}K, 速率 {rate:.2f}K/min, "
                f"预计 ~{eta_min:.0f} min)")
        else:
            self._log_callback(
                f"  [冷却进度] {current:.1f}K → {self._target_k:.1f}K "
                f"(Δ={remaining:.1f}K, 等待自然冷却...)")

    def _sparse_ready(self, elapsed_s: float) -> bool:
        """Phase 1 → Phase 2 简化判据。

        20s 轮询下可形成有效分钟窗口（每 60s 有 3 个读数 ≥ 3 阈值），
        但仍使用简单原始读数检查以保持快速转换。
        改用简单检查：最近 SPARSE_MIN_READINGS 个原始读数是否全在
        ±SPARSE_BAND_K 范围内。

        Args:
            elapsed_s: 自稳定性等待开始以来的总秒数

        Returns:
            True 如果应切换到 Phase 2 (fine)
        """
        if elapsed_s < self.SPARSE_MIN_TIME_S:
            return False

        readings = self._monitor.readings
        if len(readings) < self.SPARSE_MIN_READINGS:
            return False

        recent = readings[-self.SPARSE_MIN_READINGS:]
        return all(
            abs(r.temperature - self._target_k) <= self.SPARSE_BAND_K
            for r in recent
        )

    @staticmethod
    def _find_zone(target_k: float) -> dict:
        """根据目标温度查找温区配置。

        70K 及以上 → very_high 区（base_overshoot_k=2.5K），
        其余按 max_temp 边界依次匹配。
        """
        import config
        # 70K 及以上显式进入 very_high 区
        if target_k >= 70.0:
            return config.FIXED_PID_ZONES["very_high"]
        for zone_name in ("low", "medium", "high"):
            zone = config.FIXED_PID_ZONES[zone_name]
            if target_k <= zone["max_temp"]:
                return zone
        return config.FIXED_PID_ZONES["high"]

    def _calculate_setpoint_from_overshoot(self) -> float:
        """根据当前过冲量计算设定点温度。

        冷却时（无过冲）：设定点 = 目标温度。
        升温时：设定点 = 目标温度 + 过冲量（钳位）。
        """
        if self._current_overshoot <= 0:
            return self._target_k
        sp = self._target_k + self._current_overshoot
        return min(sp, self._target_k + self.MAX_OVERSHOOT_K)

    # ------------------------------------------------------------------
    # 快速预检通过后跳过 overshoot
    # ------------------------------------------------------------------

    def force_skip_overshoot(self):
        """快速预检通过后调用：overshoot 归零，直接跳入 FINE phase。

        跳过 overshoot 调整和 SPARSE 阶段，从 FINE phase 开始密集判稳。
        如果 FINE 阶段在短时间内确认稳定，即可直接测量；
        如果不稳定（正常 2-phase 逻辑），仍然可能触发 overshoot 调整。
        """
        self._current_overshoot = 0.0
        self._phase = StabilityPhase.FINE
        self._phase2_entry_time = 0.0  # 从当前时间开始计时
        self._initial_setpoint_written = True  # 阻止首次无条件 overshoot
        self._monitor.clear()  # 清空预检期间的读数，用 FINE 5s 密度重建

    # ------------------------------------------------------------------
    # Overshoot 学习（跨实验持久化）
    # ------------------------------------------------------------------

    def record_result(self):
        """温度点稳定完成 — 记录当前 overshoot 以用于下次实验。

        调用时机: ExperimentWorker 在温度点测量完毕后调用。
        记录的 overshoot 值通过 get/set_overshoot_learning 持久化。
        """
        target = round(self._target_k, 1)
        self._overshoot_learning[target] = round(self._current_overshoot, 1)

    def get_overshoot_learning(self) -> Dict[float, float]:
        """返回学习到的 overshoot 映射，供上层持久化到 app_settings.json。"""
        return dict(self._overshoot_learning)

    def set_overshoot_learning(self, data: Dict[float, float]):
        """从持久化存储加载学习到的 overshoot 映射。

        由 ExperimentWorker 在实验开始前调用，
        数据来源为 app_settings.json 的 ``overshoot_learning`` section。
        """
        self._overshoot_learning = {float(k): float(v) for k, v in data.items()}

    # ------------------------------------------------------------------
    # 测试辅助方法
    # ------------------------------------------------------------------

    def _set_phase_for_test(self, phase: StabilityPhase):
        """测试辅助：直接设置阶段。"""
        self._phase = phase
        if phase == StabilityPhase.FINE and self._phase2_entry_time == 0.0:
            self._phase2_entry_time = 60.0
        if self._start_time == 0.0:
            self._start_time = _time.monotonic()

    def _set_monitor_readings_for_test(self, readings: list):
        """测试辅助：直接写入监视器读数。

        Args:
            readings: list of (temperature, seconds_ago) tuples
        """
        now = _time.time()
        from stability_monitor import TemperatureReading
        self._monitor.readings = [
            TemperatureReading(
                timestamp=now - ago,
                temperature=temp,
                target=self._target_k,
            )
            for temp, ago in readings
        ]
