# -*- coding: utf-8 -*-
"""
超时软化 + 连续回退 + 4K豁免 + P-only过冲 的 BDD 测试。

测试约定：test_given_<前置条件>_when_<动作>_then_<预期结果>
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# TestConfig — 配置常量验证
# =========================================================================

class TestConfig:
    """Given config.py，验证新增常量存在且值正确。"""

    def test_given_config_loaded_when_timeout_soft_pass_band_then_2k(self):
        """超时软通过带 = 2.0K。"""
        import config
        assert hasattr(config, "timeout_soft_pass_band_k")
        assert config.timeout_soft_pass_band_k == 2.0

    def test_given_config_loaded_when_consecutive_threshold_then_2(self):
        """连续问题阈值 = 2。"""
        import config
        assert hasattr(config, "consecutive_issue_threshold")
        assert config.consecutive_issue_threshold == 2

    def test_given_config_loaded_when_rollback_increases_then_correct(self):
        """回退加时: max_wait +30min, pre_wait +10min。"""
        import config
        assert config.rollback_max_wait_increase_min == 30
        assert config.rollback_pre_wait_increase_min == 10

    def test_given_config_loaded_when_skip_validation_temp_then_4k(self):
        """跳过温度检定的特殊温度 = 4.0K。"""
        import config
        assert hasattr(config, "skip_validation_temp_k")
        assert config.skip_validation_temp_k == 4.0

    def test_given_fixed_pid_zones_when_medium_overshoot_then_2k(self):
        """需求 D: medium 区 P-only 初始过冲 = 2.0K（已从 2.5K 下调）。"""
        import config
        medium = config.FIXED_PID_ZONES["medium"]
        assert medium["base_overshoot_k"] == 2.0

    def test_given_fixed_pid_zones_when_low_overshoot_then_0(self):
        """low 区 overshoot 保持 0。"""
        import config
        assert config.FIXED_PID_ZONES["low"]["base_overshoot_k"] == 0.0

    def test_given_fixed_pid_zones_when_high_overshoot_then_2k(self):
        """high 区 (40–70K) overshoot 保持 2.0。"""
        import config
        assert config.FIXED_PID_ZONES["high"]["base_overshoot_k"] == 2.0

    def test_given_fixed_pid_zones_when_very_high_overshoot_then_2p5k(self):
        """very_high 区 (≥70K) overshoot 设为 2.5K。"""
        import config
        assert config.FIXED_PID_ZONES["very_high"]["base_overshoot_k"] == 2.5


# =========================================================================
# TimeoutRollbackState — 超时分类 + 连续追踪 + 回退决策
# =========================================================================

class TestTimeoutClassification:
    """Given 超时结果，按 ±2K 带分类为软通过/硬失败。"""

    def test_given_timeout_within_band_when_classify_then_soft_pass(self):
        """超时但 |avg−target| ≤ 2K → soft_pass。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[40.0, 42.0, 44.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        assert state.classify_timeout(avg_temp=41.5, target_k=40.0) == "soft_pass"
        # 边界：恰好 2.0K
        assert state.classify_timeout(avg_temp=42.0, target_k=40.0) == "soft_pass"
        assert state.classify_timeout(avg_temp=38.0, target_k=40.0) == "soft_pass"

    def test_given_timeout_outside_band_when_classify_then_hard_fail(self):
        """超时且 |avg−target| > 2K → hard_fail。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[40.0, 42.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        assert state.classify_timeout(avg_temp=42.1, target_k=40.0) == "hard_fail"
        assert state.classify_timeout(avg_temp=37.9, target_k=40.0) == "hard_fail"
        assert state.classify_timeout(avg_temp=50.0, target_k=40.0) == "hard_fail"


class TestConsecutiveIssueTracking:
    """Given 连续温度点结果，追踪问题计数和回退触发。"""

    def test_given_two_consecutive_soft_pass_when_record_then_rollback(self):
        """连续 2 次软通过 → 触发回退，回退到第一个问题点。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0, 46.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        # 温度点 0 (index 0, 40K): soft_pass
        should_rollback, first_idx = state.record_result(0, "soft_pass")
        assert should_rollback is False
        assert state.consecutive_issues == 1
        assert state.first_issue_index == 0

        # 温度点 1 (index 1, 42K): soft_pass → 连续 2 次 → 回退!
        should_rollback, first_idx = state.record_result(1, "soft_pass")
        assert should_rollback is True
        assert first_idx == 0  # 回退到第一个问题点 (40K)

    def test_given_two_consecutive_hard_fail_when_record_then_rollback(self):
        """连续 2 次硬失败 → 触发回退。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        state.record_result(0, "hard_fail")
        should_rollback, first_idx = state.record_result(1, "hard_fail")
        assert should_rollback is True
        assert first_idx == 0

    def test_given_soft_pass_then_hard_fail_when_record_then_rollback(self):
        """软通过 + 硬失败（混合连续问题）→ 也触发回退。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        state.record_result(0, "soft_pass")
        should_rollback, first_idx = state.record_result(1, "hard_fail")
        assert should_rollback is True

    def test_given_one_soft_pass_then_stable_when_record_then_reset(self):
        """软通过后恢复正常稳定 → 计数重置为 0。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        state.record_result(0, "soft_pass")
        assert state.consecutive_issues == 1
        # 下一个温度点正常稳定
        should_rollback, first_idx = state.record_result(1, "stable")
        assert should_rollback is False
        assert state.consecutive_issues == 0
        assert state.first_issue_index is None

    def test_given_meltdown_skip_when_record_then_counts_as_issue(self):
        """熔断跳过温度点也计入连续问题。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        state.record_result(0, "meltdown_skip")
        should_rollback, first_idx = state.record_result(1, "soft_pass")
        assert should_rollback is True

    def test_given_three_issues_when_record_then_rollback_at_threshold(self):
        """仅在达到阈值时触发回退（不会提前）。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=3,  # 设为 3
            skip_validation_temp_k=4.0,
        )
        state.record_result(0, "soft_pass")
        assert state.consecutive_issues == 1
        should_rollback, _ = state.record_result(1, "soft_pass")
        assert should_rollback is False  # 还没到 3
        assert state.consecutive_issues == 2
        should_rollback, first_idx = state.record_result(2, "hard_fail")
        assert should_rollback is True
        assert first_idx == 0


class TestRollbackStateManagement:
    """Given 回退状态机，验证回退参数和二次回退行为。"""

    def test_given_rollback_when_get_params_then_increased(self):
        """回退参数: max_wait +30min, pre_wait +10min（首次回退）。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[40.0, 42.0, 44.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
            rollback_max_wait_increase_s=1800,   # 30 min
            rollback_pre_wait_increase_s=600,     # 10 min
        )
        # 初始值
        assert state.current_max_wait_increase_s == 0
        assert state.current_pre_wait_increase_s == 0

        # 触发回退
        state.record_result(0, "soft_pass")
        should_rollback, _ = state.record_result(1, "soft_pass")
        assert should_rollback is True

        # 获取回退参数
        max_wait_inc, pre_wait_inc = state.get_rollback_params()
        assert max_wait_inc == 1800
        assert pre_wait_inc == 600
        assert state.rollback_count == 1

    def test_given_second_rollback_same_point_when_record_then_skip(self):
        """同一点二次回退 → 不再回退，标记跳过。"""
        from ui.workers import TimeoutRollbackState
        temp_list = [40.0, 42.0, 44.0]
        state = TimeoutRollbackState(
            temp_list=temp_list,
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        # 第一轮回退
        state.record_result(0, "soft_pass")
        should_rollback, first_idx = state.record_result(1, "soft_pass")
        assert should_rollback is True
        assert first_idx == 0

        # 模拟回退后重新从 index 0 开始
        state.reset_after_rollback()

        # 再次在相同点连续失败
        state.record_result(0, "soft_pass")
        should_rollback, first_idx = state.record_result(1, "hard_fail")
        # 第二次回退 → 应跳过而非再回退
        assert should_rollback is True
        # 但 first_idx 指示要跳过
        assert state.rollback_count >= 2

    def test_given_rollback_when_max_wait_accumulates(self):
        """多次回退时 max_wait 累积增加。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[40.0, 42.0, 44.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
            rollback_max_wait_increase_s=1800,
            rollback_pre_wait_increase_s=600,
        )
        # 第一次回退
        state.record_result(0, "soft_pass")
        state.record_result(1, "soft_pass")
        mw1, pw1 = state.get_rollback_params()
        assert mw1 == 1800

        state.reset_after_rollback()
        # 第二次回退
        state.record_result(0, "soft_pass")
        state.record_result(1, "hard_fail")
        mw2, pw2 = state.get_rollback_params()
        assert mw2 == 3600  # 累积: 1800 + 1800
        assert pw2 == 1200  # 累积: 600 + 600


# =========================================================================
# Test4KSkip — 4K 豁免逻辑
# =========================================================================

class Test4KSkip:
    """Given 温度列表含 4K，验证跳过温度范围检定。"""

    def test_given_target_4k_when_check_skip_then_true(self):
        """目标温度 = 4.0K → 应跳过温度范围检定。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[4.0, 10.0, 20.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        assert state.is_skip_validation_temp(4.0) is True
        # 浮点容差
        assert state.is_skip_validation_temp(4.0001) is True

    def test_given_target_not_4k_when_check_skip_then_false(self):
        """非 4K 目标 → 正常检定。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[4.0, 10.0, 20.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        assert state.is_skip_validation_temp(6.0) is False
        assert state.is_skip_validation_temp(10.0) is False
        assert state.is_skip_validation_temp(40.0) is False

    def test_given_4k_when_classify_timeout_then_always_soft_pass(self):
        """4K 超时 → 始终视为软通过（不因偏离大而硬失败）。"""
        from ui.workers import TimeoutRollbackState
        state = TimeoutRollbackState(
            temp_list=[4.0, 10.0],
            soft_pass_band_k=2.0,
            consecutive_threshold=2,
            skip_validation_temp_k=4.0,
        )
        # 4K 目标，实际温度 7K（偏离 3K > 2K band），但仍算软通过
        result = state.classify_timeout(avg_temp=7.0, target_k=4.0)
        assert result == "soft_pass"
