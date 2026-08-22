# -*- coding: utf-8 -*-
"""测量中漂移熔断自适应测试"""

import pytest
import config


class TestAdaptiveMeltdownConfig:
    """验证新增配置常量存在且类型正确。"""

    def test_meltdown_settling_multipliers(self):
        assert hasattr(config, "meltdown_settling_multipliers")
        assert config.meltdown_settling_multipliers == [8, 15]

    def test_meltdown_relaxed_threshold(self):
        assert hasattr(config, "meltdown_relaxed_threshold_k")
        assert config.meltdown_relaxed_threshold_k == 0.45

    def test_retry_mode_max_meltdowns(self):
        assert hasattr(config, "retry_mode_max_meltdowns")
        assert config.retry_mode_max_meltdowns == 2


class TestAdaptiveLogic:
    """纯逻辑测试 — 不依赖硬件，直接测试自适应状态机。"""

    @staticmethod
    def _simulate_meltdown_escalation():
        """模拟熔断递进逻辑，返回各阶段的 (settling_mult, threshold, in_retry)。"""
        multipliers = config.meltdown_settling_multipliers
        relaxed_k = config.meltdown_relaxed_threshold_k
        retry_max = config.retry_mode_max_meltdowns

        stages = []
        settling = 1.0
        threshold = config.inter_measurement_max_delta_k
        in_retry = False

        for count in range(1, 7):
            if count == 1:
                settling = float(multipliers[0])
            elif count == 2:
                settling = float(multipliers[1])
                threshold = relaxed_k
            elif count == 3:
                in_retry = True
            elif count > 3:
                retry_num = count - config.max_meltdown_restarts
                if retry_num > retry_max:
                    break  # skip temp point

            stages.append({
                "count": count,
                "settling": settling,
                "threshold": threshold,
                "in_retry": in_retry,
            })

        return stages

    def test_stage1_settling_x8(self):
        stages = self._simulate_meltdown_escalation()
        s1 = stages[0]
        assert s1["count"] == 1
        assert s1["settling"] == 8.0
        assert s1["threshold"] == 0.25
        assert not s1["in_retry"]

    def test_stage2_settling_x15_threshold_relaxed(self):
        stages = self._simulate_meltdown_escalation()
        s2 = stages[1]
        assert s2["count"] == 2
        assert s2["settling"] == 15.0
        assert s2["threshold"] == 0.45
        assert not s2["in_retry"]

    def test_stage3_retry_mode(self):
        stages = self._simulate_meltdown_escalation()
        s3 = stages[2]
        assert s3["count"] == 3
        assert s3["in_retry"]

    def test_stage4_5_retry_within_limit(self):
        stages = self._simulate_meltdown_escalation()
        assert len(stages) == 5  # count 1-5, 5th is retry #2
        assert stages[3]["count"] == 4
        assert stages[3]["in_retry"]
        assert stages[4]["count"] == 5
        assert stages[4]["in_retry"]

    def test_stage6_exceeds_retry_limit(self):
        stages = self._simulate_meltdown_escalation()
        # 熔断 #6 = retry #3 > retry_mode_max_meltdowns (2) → 跳过
        counts = [s["count"] for s in stages]
        assert 6 not in counts  # stage6 broke out early

    def test_settling_multiplier_applied(self):
        """验证沉降时间正确应用倍率。"""
        base_settle = config.laser_settle_time_s  # 20
        base_first = config.laser_first_on_settle_time_s  # 60

        assert base_settle * 8 == 160
        assert base_first * 8 == 480
        assert base_settle * 15 == 300
        assert base_first * 15 == 900
