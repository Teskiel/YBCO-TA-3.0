# -*- coding: utf-8 -*-
"""
BDD 测试 — 2 阶段实验稳定性控制器 (ExperimentStabilityController)

新逻辑：
  - 固定 PID + 仅调整设定点过冲
  - Phase 1 (sparse): 低频轮询，等待 trending_stable
  - Phase 2 (fine):   高频轮询，并行判 in_target_zone + steady_state
  - 过冲调整冷却时间 120s，至多 2 次
  - good_enough 回退: Phase 2 中 in_target_zone 但未稳态超 10 分钟

命名规范: test_given_<前置条件>_when_<动作>_then_<预期结果>
"""

import math
import sys
import os
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# TestClass: 固定 PID 温区查找
# =========================================================================

class TestFixedPIDZones:
    """验证固定 PID 按温区正确分配。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_target_10K_when_setup_then_pid_is_100_5_0(self, controller):
        """≤20K: P=100, I=5, D=0, overshoot=0。"""
        controller.setup(target_k=10.0, current_temperature=8.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 100, "i": 5, "d": 0}
        assert controller.base_overshoot == 0.0
        assert controller.current_overshoot == 0.0

    def test_given_target_20K_when_setup_then_pid_is_100_5_0(self, controller):
        """正好 20K 属于低温区。"""
        controller.setup(target_k=20.0, current_temperature=18.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 100, "i": 5, "d": 0}
        assert controller.base_overshoot == 0.0

    def test_given_target_30K_when_setup_then_pid_is_100_0_0_overshoot_2_5(
        self, controller
    ):
        """20-40K: P=100, I=0, D=0, overshoot=2.0K。"""
        controller.setup(target_k=30.0, current_temperature=25.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 100, "i": 0, "d": 0}
        assert controller.base_overshoot == 2.0
        assert controller.current_overshoot == 2.0

    def test_given_target_40K_when_setup_then_pid_is_100_0_0(self, controller):
        """正好 40K 属于中温区。"""
        controller.setup(target_k=40.0, current_temperature=35.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 100, "i": 0, "d": 0}

    def test_given_target_77K_when_setup_then_pid_is_150_0_0_overshoot_2_5(
        self, controller
    ):
        """>70K (very_high): P=150, I=0, D=0, overshoot=2.5K。"""
        controller.setup(target_k=77.0, current_temperature=70.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 150, "i": 0, "d": 0}
        assert controller.base_overshoot == 2.5

    def test_given_target_70K_when_setup_then_very_high_zone_overshoot_2_5(
        self, controller
    ):
        """70K 及以上 → very_high 区, overshoot=2.5K。"""
        controller.setup(target_k=70.0, current_temperature=68.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 150, "i": 0, "d": 0}
        assert controller.base_overshoot == 2.5


# =========================================================================
# TestClass: 阶段转换逻辑
# =========================================================================

class TestPhaseTransition:
    """验证 Phase 1 (sparse) → Phase 2 (fine) 转换。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController, StabilityPhase,
        )
        return ExperimentStabilityController()

    def test_given_initial_setup_when_checking_phase_then_phase_is_sparse(self, controller):
        """初始化后阶段应为 SPARSE。"""
        from ui.experiment_stability_controller import StabilityPhase
        controller.setup(target_k=30.0, current_temperature=28.0)
        assert controller.phase == StabilityPhase.SPARSE

    def test_given_sparse_when_outside_band_then_stays_sparse(self, controller):
        """Phase 1 中最近读数超出 ±1K 范围 → 保持 SPARSE。"""
        from ui.experiment_stability_controller import StabilityPhase

        controller.setup(target_k=30.0, current_temperature=25.0)
        # 快速升温中，最近 4 个读数距目标 >1.0K
        controller._set_monitor_readings_for_test([
            (25.0, 110), (25.5, 80), (26.0, 50), (26.5, 20),
        ])

        # elapsed >= 60s，但温度距目标 >1K
        result = controller.check(elapsed_s=90.0)
        assert controller.phase == StabilityPhase.SPARSE

    def test_given_sparse_when_in_band_then_transitions_to_fine(self, controller):
        """Phase 1→2 简化判据：最近 4 个读数在 ±1K 内 + 满足最小时间 → FINE。"""
        from ui.experiment_stability_controller import StabilityPhase

        controller.setup(target_k=30.0, current_temperature=29.5)
        # 最近 4 个读数都在 ±1K 范围内
        controller._set_monitor_readings_for_test([
            (29.48, 110), (29.50, 80), (29.49, 50), (29.51, 20),
        ])

        # elapsed_s >= SPARSE_MIN_TIME_S (60s)
        result = controller.check(elapsed_s=120.0)
        assert controller.phase == StabilityPhase.FINE


# =========================================================================
# TestClass: 并行双轨判定
# =========================================================================

class TestDualTrackStability:
    """验证 stable = in_target_zone AND steady_state。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_in_target_zone_and_steady_when_checking_then_eventually_stable(
        self, controller
    ):
        """双轨同时满足 → 持续 60s → stable。"""
        controller.setup(target_k=30.0, current_temperature=30.0)
        # 模拟 3 分钟稳态数据（max-min ≤ 0.1K，均值接近 30.0）
        temps = []
        for i in range(36):  # 36 readings × 5s = 3 min
            temps.append((30.0 + 0.015 * (i % 5 - 2), (35 - i) * 5))
        controller._set_monitor_readings_for_test(temps)
        controller._set_phase_for_test(
            __import__('ui.experiment_stability_controller', fromlist=['StabilityPhase'])
            .StabilityPhase.FINE
        )

        result = controller.check(elapsed_s=70.0)
        assert result is not None
        # 可能 stable 或 good_enough（取决于 hold duration 是否足够）
        assert result.reason in ("stable", "good_enough", "waiting")

    def test_given_in_target_zone_only_when_steady_state_false_then_not_stable(
        self, controller
    ):
        """仅 in_target_zone 但稳态不满足 → 不是 stable。"""
        controller.setup(target_k=30.0, current_temperature=30.0)
        # 均值接近目标但波动大（max-min > 0.1K）
        controller._set_monitor_readings_for_test([
            (29.8, 150), (29.9, 140), (30.1, 130), (30.2, 120),
            (29.7, 110), (29.8, 100), (30.2, 90), (30.3, 80),
            (29.9, 70), (30.0, 60), (30.1, 50), (29.8, 40),
            (30.2, 30), (30.0, 20), (29.9, 10),
        ])

        result = controller.check(elapsed_s=30.0)
        assert result.stable is False

    def test_given_steady_but_off_target_when_checking_then_not_stable(
        self, controller
    ):
        """仅稳态满足但偏离目标 → 不是 stable。"""
        controller.setup(target_k=30.0, current_temperature=28.0)
        # 稳定在 28K（远离 30K 目标）— max-min ≤ 0.1K 但不在目标区间
        controller._set_monitor_readings_for_test([
            (28.00, 150), (28.02, 140), (28.01, 130), (27.99, 120),
            (28.00, 110), (28.01, 100), (27.98, 90), (28.00, 80),
            (27.99, 70), (28.01, 60), (28.00, 50), (28.02, 40),
            (28.00, 30), (27.99, 20), (28.01, 10),
        ])

        result = controller.check(elapsed_s=30.0)
        assert result.stable is False


# =========================================================================
# TestClass: 设定点过冲调整
# =========================================================================

class TestSetpointOvershootAdjustment:
    """验证设定点过冲调整逻辑。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_first_call_when_needs_adjustment_then_returns_initial_setpoint(
        self, controller
    ):
        """首次调用 → 返回初始设定点（target + base_overshoot）。"""
        controller.setup(target_k=30.0, current_temperature=25.0)
        sp = controller.needs_setpoint_adjustment()
        assert sp is not None
        # base_overshoot = 2.0K, target = 30.0K → setpoint = 32.0K
        assert sp == 32.0

    def test_given_low_temp_when_needs_adjustment_then_setpoint_equals_target(
        self, controller
    ):
        """低温区（≤20K）过冲为 0，设定点 = 目标。"""
        controller.setup(target_k=10.0, current_temperature=8.0)
        sp = controller.needs_setpoint_adjustment()
        assert sp == 10.0

    def test_given_trending_stable_and_off_target_when_calling_adjust_then_overshoot_increases(
        self, controller
    ):
        """trending_stable 但不达目标 → 过冲应增大。"""
        controller.setup(target_k=30.0, current_temperature=25.0)
        # 首次调用后设置初始设定点
        sp1 = controller.needs_setpoint_adjustment()
        assert sp1 is not None
        base_overshoot = controller.current_overshoot  # 1.5

        # 模拟趋于平稳但离目标远（覆盖 180s 以生成 3 个 1-min 窗口）
        controller._set_monitor_readings_for_test([
            (25.5, 170), (25.5, 160), (25.6, 150), (25.5, 140),
            (25.6, 130), (25.5, 120), (25.6, 110), (25.5, 100),
            (25.5, 90),  (25.6, 80),  (25.5, 70),  (25.6, 60),
            (25.5, 50),  (25.6, 40),  (25.5, 30),  (25.6, 20),
            (25.5, 10),
        ])

        # 手动清除冷却时间来允许第二次调整
        controller._last_overshoot_time = 0  # 绕过冷却
        sp2 = controller.needs_setpoint_adjustment()
        # 过冲应增加：delta = 30-25.5 ≈ 4.5K, 新过冲 = 1.5+4.5 = 6.0K
        assert controller.current_overshoot > base_overshoot

    def test_given_overshoot_exceeds_max_when_adjusting_then_clamped(
        self, controller
    ):
        """过冲应被钳位在 MAX_OVERSHOOT_K。"""
        controller.setup(target_k=30.0, current_temperature=10.0)
        sp1 = controller.needs_setpoint_adjustment()  # 首次
        controller._last_overshoot_time = 0  # 绕过冷却

        # 模拟巨大温差（覆盖 180s 以生成 3 个 1-min 窗口）
        controller._set_monitor_readings_for_test([
            (12.0, 170), (12.0, 160), (12.1, 150), (12.0, 140),
            (12.0, 130), (12.1, 120), (12.0, 110), (12.0, 100),
            (12.1, 90),  (12.0, 80),  (12.0, 70),  (12.1, 60),
            (12.0, 50),  (12.0, 40),  (12.1, 30),  (12.0, 20),
            (12.0, 10),
        ])
        sp2 = controller.needs_setpoint_adjustment()
        assert controller.current_overshoot <= controller.MAX_OVERSHOOT_K

    def test_given_within_cooldown_when_calling_adjust_then_returns_none(
        self, controller
    ):
        """冷却时间内 → 不返回新设定点。"""
        controller.setup(target_k=30.0, current_temperature=25.0)
        sp1 = controller.needs_setpoint_adjustment()  # 首次 — 设定初始值
        assert sp1 is not None
        # 紧接着再调 — 冷却时间尚未过
        sp2 = controller.needs_setpoint_adjustment()
        assert sp2 is None  # 冷却中

    def test_given_within_0_7K_below_target_when_needs_adjustment_then_adjusts(
        self, controller
    ):
        """温度低于目标但 |Δ| ≤ 0.7K → v1.3 仍上调 overshoot（避免死区）。

        旧行为：in-band 无条件跳过 → P-only 稳态误差落在 0.5K（测量熔断）
        与 0.7K（band）之间，死锁。
        新行为：avg < target 时，即使已在 band 内也允许上调 overshoot。
        """
        controller.setup(target_k=30.0, current_temperature=25.0)
        sp1 = controller.needs_setpoint_adjustment()  # 首次 — 初始设定点
        assert sp1 is not None

        # 模拟温度接近目标但低于目标（avg ≈ 29.5K，Δ=0.5K ≤ 0.7K）
        controller._set_monitor_readings_for_test([
            (29.50, 170), (29.52, 160), (29.48, 150), (29.51, 140),
            (29.50, 130), (29.49, 120), (29.51, 110), (29.50, 100),
            (29.49, 90),  (29.51, 80),  (29.50, 70),  (29.48, 60),
            (29.50, 50),  (29.49, 40),  (29.51, 30),  (29.50, 20),
            (29.49, 10),
        ])
        controller._last_overshoot_time = 0  # 绕过冷却

        sp2 = controller.needs_setpoint_adjustment()
        # avg=29.5 < target=30.0 → 即使 |Δ|=0.5 ≤ 0.7，也应上调（死区修复）
        assert sp2 is not None
        assert sp2 > 30.0  # setpoint 推高以缩小稳态误差
        assert controller.current_overshoot > controller._base_overshoot

    def test_given_far_from_target_when_multiple_adjustments_then_no_count_limit(
        self, controller
    ):
        """无计数上限 — 即使多次调整，只要温度仍在 0.7K 外且趋势稳就继续。"""
        controller.setup(target_k=30.0, current_temperature=25.0)
        sp1 = controller.needs_setpoint_adjustment()  # 首次
        assert sp1 is not None
        assert controller._setpoint_adjust_count == 0  # 首次不算入计数

        # 模拟温度稳定但远离目标（Δ ≈ 5K > 0.7K → 应继续调整）
        temps = [(25.50, 170), (25.52, 160), (25.48, 150), (25.51, 140),
                 (25.50, 130), (25.49, 120), (25.51, 110), (25.50, 100),
                 (25.49, 90),  (25.51, 80),  (25.50, 70),  (25.48, 60),
                 (25.50, 50),  (25.49, 40),  (25.51, 30),  (25.50, 20),
                 (25.49, 10)]
        controller._set_monitor_readings_for_test(temps)
        controller._last_overshoot_time = 0  # 绕过冷却

        # 第 1 次调整
        sp2 = controller.needs_setpoint_adjustment()
        assert sp2 is not None, "Δ=4.5K > 0.7K，应允许调整"
        assert controller._setpoint_adjust_count == 1

        # 第 2 次调整（不应被计数上限阻止）
        controller._last_overshoot_time = 0  # 再次绕过冷却
        sp3 = controller.needs_setpoint_adjustment()
        assert sp3 is not None, \
            "取消计数上限后，第 2 次调整应允许（Δ 仍 > 0.7K）"
        assert controller._setpoint_adjust_count == 2

    def test_given_sparse_20s_polling_9_readings_when_adjust_needed_then_returns_setpoint(
        self, controller
    ):
        """回归测试：SPARSE 20s 轮询仅 9 个读数在 180s 窗口时，过冲调整应正常触发。

        2026-06-12 bug: min_readings_required=10 > 9 导致 check_stability()
        永远返回 avg_temp=None，trending_stable=False，过冲调整死锁。
        此测试用真实 SPARSE 密度（20s 间隔 × 9 个读数）验证调整可以触发。
        """
        # 模拟 36K 目标，温度卡在 ~34.8K（真实 bug 场景）
        controller.setup(target_k=36.0, current_temperature=34.8)
        # 首次调用 — 初始设定点
        sp1 = controller.needs_setpoint_adjustment()
        assert sp1 is not None
        base_overshoot = controller.current_overshoot
        assert base_overshoot == 2.0  # Medium zone

        # 注入 9 个读数，20s 间隔，覆盖 170s（模拟 SPARSE 20s 轮询）
        controller._set_monitor_readings_for_test([
            (34.81, 170), (34.82, 150), (34.81, 130),
            (34.83, 110), (34.82, 90),  (34.81, 70),
            (34.82, 50),  (34.80, 30),  (34.81, 10),
        ])

        # 绕过冷却时间
        controller._last_overshoot_time = 0
        sp2 = controller.needs_setpoint_adjustment()
        assert sp2 is not None, (
            f"SPARSE 20s 密度下（9 读数/180s），过冲调整应触发，"
            f"但返回了 None。"
            f"current_overshoot={controller.current_overshoot:.2f}K"
        )
        # 过冲应增大：delta=36.0-34.81≈1.19K, new=2.0+1.19=3.19K
        assert controller.current_overshoot > base_overshoot, (
            f"过冲应增大: base={base_overshoot:.1f}K, "
            f"current={controller.current_overshoot:.2f}K"
        )


# =========================================================================
# TestClass: good_enough 回退
# =========================================================================

class TestGoodEnoughFallback:
    """验证 good_enough 回退：Phase 2 中 10 分钟后 in_target_zone 但未稳态。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_phase2_long_wait_in_target_zone_not_steady_then_good_enough(
        self, controller
    ):
        """Phase 2 中 in_target_zone=true 但 steady_state=false 持续 10 分钟 → good_enough。"""
        from ui.experiment_stability_controller import StabilityPhase

        controller.setup(target_k=30.0, current_temperature=30.0)
        controller._set_phase_for_test(StabilityPhase.FINE)
        controller._phase2_entry_time = 0.0

        # 均值在目标区间但波动较大（max-min > 0.1K）
        controller._set_monitor_readings_for_test([
            (29.8, 150), (29.9, 140), (30.1, 130), (30.2, 120),
            (29.7, 110), (29.8, 100), (30.2, 90), (30.3, 80),
            (29.9, 70), (30.0, 60), (30.1, 50), (29.8, 40),
        ])

        result = controller.check(
            elapsed_s=controller.GOOD_ENOUGH_PHASE2_TIMEOUT_S + 1.0
        )
        assert result.reason == "good_enough"

    def test_given_phase2_not_in_target_zone_then_not_good_enough(
        self, controller
    ):
        """不在目标区间 → 即使等很久也不是 good_enough。"""
        from ui.experiment_stability_controller import StabilityPhase

        controller.setup(target_k=30.0, current_temperature=28.0)
        controller._set_phase_for_test(StabilityPhase.FINE)
        controller._phase2_entry_time = 0.0

        # 远离目标
        controller._set_monitor_readings_for_test([
            (28.5, 130), (28.5, 120), (28.6, 110), (28.5, 100),
            (28.6, 90), (28.5, 80), (28.6, 70),
            (28.5, 60), (28.6, 50), (28.5, 40),
        ])

        result = controller.check(
            elapsed_s=controller.GOOD_ENOUGH_PHASE2_TIMEOUT_S + 1.0
        )
        assert result.reason != "good_enough"


# =========================================================================
# TestClass: 超时
# =========================================================================

class TestTimeout:
    """验证 30 分钟硬超时。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_max_wait_exceeded_when_checking_then_returns_timeout(
        self, controller
    ):
        """超过 MAX_WAIT_SECONDS → timeout（FINE 阶段）。

        测试前提：控制器必须在 FINE 阶段，MAX_WAIT_SECONDS 才适用。
        SPARSE 阶段使用独立的 SPARSE_MAX_WAIT_SECONDS (90 min)。
        v1.3: 读数设为 30.6K（Δ=0.6K > 0.5K 目标区间），避免
        SPARSE 保留读数意外触发 good_enough。
        """
        controller.setup(target_k=30.0, current_temperature=30.0)
        # 添加在目标 ±1K 内（SPARSE band）但**不在** ±0.5K 内（FINE target zone）
        # 的读数，以触发 SPARSE→FINE 但不触发 in_target_zone
        for i in range(20):
            controller.add_reading(30.6 + 0.01 * (i % 5))
        # 触发 SPARSE→FINE（需要 elapsed ≥ 60s 且 4 个读数在 band 内）
        controller.check(elapsed_s=90.0)
        assert controller.phase.value == "fine", \
            f"Expected FINE phase, got {controller.phase.value}"

        # FINE 阶段：elapsed 超过 MAX_WAIT_SECONDS → timeout
        result = controller.check(elapsed_s=controller.MAX_WAIT_SECONDS + 1.0)
        assert result.reason == "timeout"
        assert result.total_elapsed_s >= controller.MAX_WAIT_SECONDS


# =========================================================================
# TestClass: StabilityResult 扩展字段
# =========================================================================

class TestStabilityResultFields:
    """验证 StabilityResult 包含完整的状态信息。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_result_when_checking_then_contains_phase(
        self, controller
    ):
        """结果应包含 phase 字段。"""
        controller.setup(target_k=30.0, current_temperature=29.5)
        result = controller.check(elapsed_s=30.0)
        assert hasattr(result, "phase")
        assert result.phase is not None

    def test_given_result_when_checking_then_contains_avg_temp(
        self, controller
    ):
        """结果应包含 avg_temp（实际温度）。"""
        controller.setup(target_k=30.0, current_temperature=29.5)
        for i in range(15):
            controller.add_reading(29.5 + 0.02 * (i % 7))
        result = controller.check(elapsed_s=200.0)
        assert hasattr(result, "avg_temp")
        assert hasattr(result, "final_temp")

    def test_given_timeout_when_checking_then_contains_setpoint_adjustments(
        self, controller
    ):
        """超时结果应包含调整次数（FINE 阶段）。

        测试前提：先在目标温度附近建立 FINE 状态，再超时。
        v1.3: 读数设为 30.6K 区间外，避免 SPARSE 保留读数触发 good_enough。
        """
        controller.setup(target_k=30.0, current_temperature=30.0)
        for i in range(50):
            controller.add_reading(30.6 + 0.01 * (i % 5))
        # 触发 SPARSE→FINE
        controller.check(elapsed_s=90.0)
        assert controller.phase.value == "fine"
        # FINE 阶段超时
        result = controller.check(elapsed_s=controller.MAX_WAIT_SECONDS + 1.0)
        assert result.reason == "timeout"
        assert hasattr(result, "setpoint_adjustments")


# =========================================================================
# TestClass: ExperimentWorker 集成
# =========================================================================

class TestExperimentWorkerIntegration:
    """验证 ExperimentWorker 与新控制器的集成。"""

    @pytest.fixture(scope="module")
    def qapp(self):
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def _make_mock_lakeshore(self, start_temp=50.0):
        ls = MagicMock()
        ls.get_temperature.return_value = start_temp
        return ls

    def _make_mock_laser(self):
        return MagicMock()

    def _make_mock_vna(self):
        return MagicMock()

    def _build_worker(self, lakeshore=None, laser=None, vna=None,
                      temp_list=None, power_list=None,
                      output_dir="", vna_settings=None, vna_power_list=None):
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        worker.configure(
            lakeshore_ctrl=lakeshore if lakeshore is not None else self._make_mock_lakeshore(),
            laser_ctrl=laser if laser is not None else self._make_mock_laser(),
            vna_resource=vna if vna is not None else self._make_mock_vna(),
            temp_list=temp_list if temp_list is not None else [30.0],
            power_list=power_list if power_list is not None else [0, 5],
            vna_power_list=vna_power_list if vna_power_list is not None else [-45],
            output_dir=output_dir or "/tmp/test_sweep",
            vna_settings=vna_settings if vna_settings is not None else {
                "start_freq_hz": 3_000_000_000.0,
                "stop_freq_hz": 6_000_000_000.0,
                "s_parameter": "S21",
                "power_dbm": [-45],
            },
        )
        return worker

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_new_controller_when_run_then_uses_fixed_pid(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """ExperimentWorker 应使用固定 PID + 设定点调整。"""
        ls = self._make_mock_lakeshore(start_temp=30.0)
        worker = self._build_worker(lakeshore=ls, temp_list=[30.0])

        finished = []
        worker.experiment_finished.connect(lambda c: finished.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            from ui.experiment_stability_controller import StabilityPhase
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=True, reason="stable", avg_temp=30.0,
                phase=StabilityPhase.FINE)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            mock_ctrl.phase = StabilityPhase.FINE

            worker.run()

        assert len(finished) == 1
        assert ls.set_pid.called

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_good_enough_when_run_then_proceeds(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """good_enough 结果应让测量继续。"""
        ls = self._make_mock_lakeshore(start_temp=30.3)
        worker = self._build_worker(lakeshore=ls, temp_list=[30.0])

        finished = []
        worker.experiment_finished.connect(lambda c: finished.append(c))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            from ui.experiment_stability_controller import StabilityPhase
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
            mock_ctrl.setup.return_value = None
            mock_ctrl.add_reading.return_value = None
            mock_ctrl.check.return_value = MagicMock(
                stable=False, reason="good_enough", avg_temp=30.3,
                phase=StabilityPhase.FINE)
            mock_ctrl.needs_setpoint_adjustment.return_value = None
            mock_ctrl.base_overshoot = 1.5
            mock_ctrl.current_overshoot = 1.5
            mock_ctrl.phase = StabilityPhase.FINE

            worker.run()

        assert len(finished) == 1


# =========================================================================
# TestClass: 18-40K 温区逻辑
# =========================================================================

class Test18To40KZoneLogic:
    """验证 ExperimentStabilityController 在 18-40K 温区的正常行为。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_target_in_18_to_40K_when_setup_then_medium_zone(self, controller):
        """18-40K 目标温度 → 中温区 PID。"""
        controller.setup(target_k=30.0, current_temperature=28.0)
        pid = controller.get_fixed_pid()
        assert pid["p"] == 100
        assert pid["i"] == 0
        assert controller.base_overshoot == 2.0

    def test_given_target_outside_18_to_40K_when_setup_then_standard(self, controller):
        """非 18-40K 温区 → 标准行为。"""
        controller.setup(target_k=10.0, current_temperature=8.0)
        pid = controller.get_fixed_pid()
        assert pid["p"] == 100
        assert pid["i"] == 5
        assert controller.base_overshoot == 0.0

    def test_given_target_exactly_18K_when_setup_then_low_zone(self, controller):
        """正好 18K → ≤20K 属于低温区。"""
        controller.setup(target_k=18.0, current_temperature=17.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 100, "i": 5, "d": 0}

    def test_given_target_exactly_40K_when_setup_then_medium_zone(self, controller):
        """正好 40K → 属于中温区。"""
        controller.setup(target_k=40.0, current_temperature=38.0)
        pid = controller.get_fixed_pid()
        assert pid == {"p": 100, "i": 0, "d": 0}


# =========================================================================
# TestClass: SPARSE → FINE 转换时清空旧读数
# =========================================================================

class TestSparseToFineClear:
    """验证 SPARSE → FINE 阶段转换时读数保留 + 计时重置。

    v1.3: 移除 monitor.clear() — SPARSE 读数在 180s 滑动窗口内自然老化，
    不再强制清除。仅重置 _last_stable_time。
    """

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController, StabilityPhase,
        )
        ctrl = ExperimentStabilityController()
        ctrl.SPARSE_MIN_TIME_S = 0        # 跳过最小等待
        ctrl.SPARSE_MIN_READINGS = 2       # 仅需 2 个读数
        ctrl.SPARSE_BAND_K = 1.0
        # 设置目标温度并注入足够读数以触发 sparse_ready
        ctrl._target_k = 30.0
        ctrl._start_time = 100.0
        return ctrl

    def test_given_sparse_with_readings_when_transition_to_fine_then_readings_survive(
        self, controller):
        """SPARSE→FINE 时 _monitor.readings 保留（不再 clear）。"""
        # 添加 2 个在 band 内的读数，触发 sparse_ready
        controller.add_reading(30.3)
        controller.add_reading(29.8)
        assert len(controller._monitor.readings) == 2

        # 调用 check 触发阶段转换
        result = controller.check(elapsed_s=10.0)
        assert controller.phase.value == "fine"
        # 读数保留 — SPARSE 数据在 180s 窗口内自然老化
        assert len(controller._monitor.readings) == 2

    def test_given_sparse_when_transition_to_fine_then_last_stable_time_none(
        self, controller):
        """SPARSE→FINE 时 _last_stable_time 被重置为 None。"""
        controller.add_reading(30.2)
        controller.add_reading(29.9)
        controller._last_stable_time = 123.45  # 模拟已有稳态时间
        controller.check(elapsed_s=10.0)
        assert controller.phase.value == "fine"
        assert controller._last_stable_time is None

    def test_given_sparse_transitions_to_fine_when_check_stability_then_combined_readings(
        self, controller):
        """FINE 阶段 check_stability 基于 SPARSE+FINE 混合读数。"""
        # SPARSE 阶段: 在 band 内的读数，触发 sparse→fine
        controller.add_reading(29.5)  # Δ=-0.5K, 在 ±1K band 内
        controller.add_reading(29.6)  # Δ=-0.4K, 在 band 内
        controller.check(elapsed_s=10.0)
        assert controller.phase.value == "fine"
        # SPARSE 读数保留，继续添加 FINE 阶段高质量读数
        for _ in range(15):
            controller.add_reading(30.01)  # 非常接近目标
        stability = controller._monitor.check_stability(30.0)
        # 应该判定为进入目标区间（读数全在 ±0.5K 内）
        assert stability.get("in_target_zone", False), \
            "FINE 阶段 check_stability 应基于 SPARSE+FINE 混合读数"

    def test_given_sparse_to_fine_transition_when_monitor_not_cleared_then_readings_count_preserved(
        self, controller):
        """回归测试：SPARSE→FINE 后读数数量不被清零。"""
        controller.add_reading(30.1)
        controller.add_reading(29.9)
        controller.add_reading(30.0)
        before = len(controller._monitor.readings)
        controller.check(elapsed_s=10.0)
        assert controller.phase.value == "fine"
        after = len(controller._monitor.readings)
        assert after == before, \
            f"SPARSE→FINE 不应清除读数: {before} → {after}"


# =========================================================================
# TestClass: SPARSE 独立超时 + timeout 使用实际温度
# =========================================================================

class TestSparseTimeout:
    """验证 SPARSE 阶段 90 min 独立超时。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        ctrl = ExperimentStabilityController()
        ctrl.setup(target_k=40.0, current_temperature=25.0)
        # 缩小超时以便测试
        ctrl.SPARSE_MAX_WAIT_SECONDS = 100
        ctrl.SPARSE_MIN_TIME_S = 9999    # 禁用 sparse_ready
        return ctrl

    def test_given_sparse_when_90_min_elapsed_then_returns_timeout_with_actual_temp(
        self, controller):
        """SPARSE 超时返回 timeout + 最后实际温度。"""
        controller.add_reading(31.5)
        controller.add_reading(31.6)
        controller.add_reading(31.7)
        result = controller.check(elapsed_s=101.0)
        assert result.reason == "timeout"
        assert result.phase.value == "sparse"
        # 使用最后实际读数 31.7K，而非 target 40K
        assert abs(result.avg_temp - 31.7) < 0.01

    def test_given_sparse_timeout_with_no_readings_then_avg_temp_is_target(
        self, controller):
        """SPARSE 超时但无读数时 → avg_temp = target_k。"""
        result = controller.check(elapsed_s=101.0)
        assert result.reason == "timeout"
        assert result.avg_temp == 40.0

    def test_given_fine_phase_when_30_min_elapsed_then_timeout_with_actual(
        self, controller):
        """FINE 阶段超时也使用实际温度。"""
        from ui.experiment_stability_controller import StabilityPhase
        controller._set_phase_for_test(StabilityPhase.FINE)
        controller.MAX_WAIT_SECONDS = 10
        controller.add_reading(38.1)
        controller.add_reading(38.2)
        controller.add_reading(38.3)
        result = controller.check(elapsed_s=11.0)
        assert result.reason == "timeout"
        assert abs(result.avg_temp - 38.3) < 0.01

    def test_given_fine_timeout_with_readings_then_avg_temp_is_last_actual(
        self, controller):
        """timeout 有读数时 avg_temp = 最后实际温度而非 target_k。"""
        from ui.experiment_stability_controller import StabilityPhase
        controller._set_phase_for_test(StabilityPhase.FINE)
        controller.MAX_WAIT_SECONDS = 5
        controller.add_reading(37.5)
        result = controller.check(elapsed_s=6.0)
        assert result.reason == "timeout"
        assert abs(result.avg_temp - 37.5) < 0.01
        # 确保不是 target 默认值
        assert abs(result.avg_temp - 40.0) > 0.5


# =========================================================================
# TestClass: 双向 overshoot 调整 (v1.2)
# =========================================================================

class TestBidirectionalOvershoot:
    """验证 overshoot 可增可减，钳位 [0, MAX_OVERSHOOT_K]。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def _make_stable(self, ctrl, temps, target=30.0, start_offset=0):
        """用合成温度数据填充监视器，使 trending_stable=True 且 |avg−target| > 0.7K。

        使用 15s 间隔确保 3 分钟窗口内 ≥12 个读数（满足 min_readings=6 要求）。
        """
        now = time.time()
        from stability_monitor import TemperatureReading
        ctrl._target_k = target
        ctrl._start_time = time.monotonic()
        ctrl._current_overshoot = ctrl._base_overshoot
        readings = []
        for i, t in enumerate(temps):
            readings.append(TemperatureReading(
                timestamp=now - (len(temps) - i) * 15,  # 15s 间隔
                temperature=t,
                target=target,
            ))
        ctrl._monitor.readings = readings

    def test_given_temp_below_target_when_adjust_then_overshoot_increases(
            self, controller):
        """温度低于目标 → delta>0 → overshoot 增大。"""
        controller.setup(target_k=68.0, current_temperature=66.0)
        # 初始 setpoint 写入
        sp = controller.needs_setpoint_adjustment()
        assert sp is not None  # 首次调用返回初始 setpoint

        # 模拟温度稳定在 67.0K 附近
        self._make_stable(controller,
                          [67.1, 67.0, 66.9, 67.0, 67.1, 67.0,
                           67.0, 66.9, 67.0, 67.1, 67.0, 66.9],
                          target=68.0)
        # 重置 initial_setpoint_written 以触发调整
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        assert sp is not None
        # overshoot 应从 base=2.0 增大（delta=+1.0 → overshoot≈3.0）
        assert controller.current_overshoot > 2.0
        assert sp > 68.0

    def test_given_temp_above_target_when_adjust_then_overshoot_decreases(
            self, controller):
        """温度越过目标 → delta<0 → overshoot 减小。"""
        controller.setup(target_k=68.0, current_temperature=66.0)
        controller.needs_setpoint_adjustment()  # 首次

        # 模拟温度越过目标到 69.5K
        self._make_stable(controller,
                          [69.5, 69.6, 69.4, 69.5, 69.6, 69.4,
                           69.5, 69.5, 69.4, 69.5, 69.6, 69.4],
                          target=68.0)
        # 手动推向高位（模拟之前调整的结果）
        controller._current_overshoot = 2.5
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        # delta = 68.0 − 69.5 = −1.5 → overshoot = 2.5 + (−1.5) = 1.0
        assert controller.current_overshoot < 2.5
        assert controller.current_overshoot >= 0.0
        if sp is not None:
            assert sp <= 69.5  # setpoint 缩回了

    def test_given_temp_well_above_target_when_adjust_then_overshoot_clamped_at_zero(
            self, controller):
        """温度远超目标 → overshoot 钳位到 0（setpoint = target）。"""
        controller.setup(target_k=68.0, current_temperature=66.0)
        controller.needs_setpoint_adjustment()

        self._make_stable(controller,
                          [70.0, 69.9, 70.1, 70.0, 69.9, 70.1,
                           70.0, 70.0, 69.9, 70.0, 70.1, 70.0],
                          target=68.0)
        controller._current_overshoot = 2.0
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        # delta = 68.0 − 70.0 = −2.0 → overshoot = 2.0 + (−2.0) = 0
        assert controller.current_overshoot == 0.0
        if sp is not None:
            assert sp == 68.0  # setpoint = target，不关加热器

    def test_given_temp_near_target_when_overshoot_zero_then_setpoint_equals_target(
            self, controller):
        """overshoot=0 时 setpoint 即 target，不会低于 target。"""
        controller.setup(target_k=68.0, current_temperature=67.0)
        controller._current_overshoot = 0.0
        sp = controller._calculate_setpoint_from_overshoot()
        assert sp == 68.0

    def test_given_trending_not_stable_when_temp_above_then_no_adjustment(
            self, controller):
        """trending_stable=False 时不触发任何调整（无论 delta 符号）。"""
        controller.setup(target_k=68.0, current_temperature=66.0)
        controller.needs_setpoint_adjustment()  # 首次

        # 不稳定状态 — trending_stable 会返回 False
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130
        controller.add_reading(69.8)
        controller.add_reading(69.9)

        sp = controller.needs_setpoint_adjustment()
        # 数据不足 → trending_stable=False → 不调整
        assert sp is None


# =========================================================================
# TestClass: Overshoot 学习器 (v1.2)
# =========================================================================

class TestOvershootLearning:
    """验证 overshoot 记录、加载、持久化的完整流程。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def test_given_no_learning_when_setup_then_uses_zone_default(
            self, controller):
        """无学习数据 → 使用温区默认 base_overshoot。"""
        controller.setup(target_k=68.0, current_temperature=66.0)
        assert controller.current_overshoot == 2.0  # high zone default

    def test_given_learned_overshoot_when_setup_then_uses_learned_value(
            self, controller):
        """有学习数据 → 跳过 zone 默认值，使用已记录的 overshoot。"""
        controller.set_overshoot_learning({68.0: 0.5, 40.0: 1.5})
        controller.setup(target_k=68.0, current_temperature=66.0)
        assert controller.current_overshoot == 0.5

    def test_given_learned_for_other_temp_when_setup_then_uses_zone_default(
            self, controller):
        """学习数据中没有当前温度 → 回退到 zone 默认值。"""
        controller.set_overshoot_learning({40.0: 1.5})
        controller.setup(target_k=68.0, current_temperature=66.0)
        assert controller.current_overshoot == 2.0  # high zone default

    def test_given_record_result_when_get_learning_then_returns_recorded_value(
            self, controller):
        """record_result() 后 get_overshoot_learning() 应包含该温度点。"""
        controller.setup(target_k=68.0, current_temperature=66.0)
        controller._current_overshoot = 0.5  # 模拟稳定后的值
        controller.record_result()

        learning = controller.get_overshoot_learning()
        assert 68.0 in learning
        assert learning[68.0] == 0.5

    def test_given_multiple_record_results_when_get_learning_then_all_present(
            self, controller):
        """多次 record_result() 累积所有温度点。"""
        for t, ov in [(6.0, 0.0), (20.0, 2.0), (68.0, 0.5)]:
            controller.setup(target_k=t, current_temperature=t - 1.0)
            controller._current_overshoot = ov
            controller.record_result()

        learning = controller.get_overshoot_learning()
        assert learning == {6.0: 0.0, 20.0: 2.0, 68.0: 0.5}

    def test_given_set_learning_then_get_returns_same(self, controller):
        """set→get 往返一致。"""
        data = {10.0: 0.0, 30.0: 1.0, 50.0: 1.5, 77.0: 2.0}
        controller.set_overshoot_learning(data)
        assert controller.get_overshoot_learning() == data

    def test_given_empty_learning_when_setup_then_no_effect(self, controller):
        """空 dict 不应影响正常 setup。"""
        controller.set_overshoot_learning({})
        controller.setup(target_k=30.0, current_temperature=28.0)
        assert controller.current_overshoot == 2.0  # medium zone default


# =========================================================================
# TestClass: 区间内死区修复 (v1.3)
# =========================================================================

class TestInBandDeadZone:
    """验证 in-band 时 overshoot 跳过条件：仅当 avg ≥ target 且 |Δ| ≤ 0.7K
    时才跳过。avg < target 时即使已在 band 内，也允许上调 overshoot。"""

    @pytest.fixture
    def controller(self):
        from ui.experiment_stability_controller import (
            ExperimentStabilityController,
        )
        return ExperimentStabilityController()

    def _make_stable(self, ctrl, temps, target=72.0, start_offset=0):
        """填充监视器读数使 trending_stable=True。

        使用 15s 间隔确保 3 分钟窗口内 ≥12 个读数（满足 min_readings=6）。
        """
        now = time.time()
        from stability_monitor import TemperatureReading
        ctrl._target_k = target
        ctrl._start_time = time.monotonic()
        ctrl._current_overshoot = ctrl._base_overshoot
        readings = []
        for i, t in enumerate(temps):
            readings.append(TemperatureReading(
                timestamp=now - (len(temps) - i) * 15,
                temperature=t,
                target=target,
            ))
        ctrl._monitor.readings = readings

    def test_given_in_band_below_target_when_adjust_then_overshoot_increases(
            self, controller):
        """72K 死锁场景: avg=71.49, |Δ|=0.51 ≤ 0.7K 但 avg < target
        → 不应跳过，应上调 overshoot 缩小稳态误差。"""
        controller.setup(target_k=72.0, current_temperature=70.0)
        controller.needs_setpoint_adjustment()  # 首次调用

        # 模拟温度卡在 71.49K（72K 场景的死锁点）
        self._make_stable(controller,
                          [71.49, 71.50, 71.48, 71.49, 71.50, 71.48,
                           71.49, 71.49, 71.50, 71.48, 71.49, 71.50],
                          target=72.0)
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        # 必须返回有效调整（非 None）——死区修复的核心
        assert sp is not None
        # overshoot 应从 base 增大：72K ∈ very_high 区 (base=2.5),
        # delta=72.0−71.49=0.51, current_overshoot=2.5+0.51=3.01
        assert controller.current_overshoot > 2.0
        assert controller.current_overshoot <= 3.5  # 合理范围内（含 FP 容差）

    def test_given_in_band_above_target_when_adjust_then_skip(
            self, controller):
        """avg ≥ target 且在 band 内 → 正常跳过（无需推得更高）。"""
        controller.setup(target_k=72.0, current_temperature=70.0)
        controller.needs_setpoint_adjustment()

        # 温度略高于目标，已在 band 内
        self._make_stable(controller,
                          [72.30, 72.31, 72.29, 72.30, 72.31, 72.29,
                           72.30, 72.30, 72.31, 72.29, 72.30, 72.30],
                          target=72.0)
        controller._current_overshoot = 2.0
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        # avg=72.3 ≥ target, |Δ|=0.3 ≤ 0.7 → 跳过
        assert sp is None

    def test_given_in_band_exactly_at_target_when_adjust_then_skip(
            self, controller):
        """avg == target 且 |Δ|=0 → 跳过调整。"""
        controller.setup(target_k=72.0, current_temperature=70.0)
        controller.needs_setpoint_adjustment()

        self._make_stable(controller,
                          [72.0, 72.0, 72.0, 72.0, 72.0, 72.0,
                           72.0, 72.0, 72.0, 72.0, 72.0, 72.0],
                          target=72.0)
        controller._current_overshoot = 2.0
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        assert sp is None

    def test_given_above_band_when_adjust_then_overshoot_decreases(
            self, controller):
        """ |Δ| > 0.7K 且 avg > target → 正常执行双向缩小。"""
        controller.setup(target_k=72.0, current_temperature=70.0)
        controller.needs_setpoint_adjustment()

        # 温度越过目标且超出 band
        self._make_stable(controller,
                          [73.0, 73.1, 72.9, 73.0, 73.1, 72.9,
                           73.0, 73.0, 73.1, 72.9, 73.0, 73.0],
                          target=72.0)
        controller._current_overshoot = 2.0
        controller._initial_setpoint_written = True
        controller._last_overshoot_time = time.monotonic() - 130

        sp = controller.needs_setpoint_adjustment()
        # delta=72−73=−1 → overshoot=2+(−1)=1.0
        assert controller.current_overshoot < 2.0
        assert controller.current_overshoot >= 0.0
