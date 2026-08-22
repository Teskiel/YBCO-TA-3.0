# -*- coding: utf-8 -*-
"""
BDD tests for pid_parameters.py — PID zone manager and setpoint calculator.

Covers: 4-zone PID lookup, zone boundary correctness, setpoint overshoot
with min/max clamp, no-overshoot below 20K, heater range constraints
(Low + Medium only, High forbidden), full 10K→80K sweep validation.
"""

import pytest
from pid_parameters import PIDZone, PIDZoneManager, SetpointCalculator, ZONES


# =========================================================================
# TestClass: PIDZone
# =========================================================================

class TestPIDZone:
    """Given a PIDZone dataclass."""

    def test_given_valid_values_when_creating_zone_then_fields_match(self):
        zone = PIDZone(
            zone_id=1, temp_min=10.0, temp_max=20.0,
            p=100.0, i=5.0, d=0.0,
            heater_range=1, description="Low zone",
        )
        assert zone.zone_id == 1
        assert zone.temp_min == 10.0
        assert zone.temp_max == 20.0
        assert zone.p == 100.0
        assert zone.i == 5.0
        assert zone.d == 0.0
        assert zone.heater_range == 1
        assert zone.description == "Low zone"

    def test_given_frozen_dataclass_when_modifying_then_raises(self):
        zone = PIDZone(1, 10.0, 20.0, 100.0, 5.0, 0.0, 1, "test")
        with pytest.raises(Exception):
            zone.p = 200.0  # type: ignore[misc]


# =========================================================================
# TestClass: PIDZoneManager — Zone Boundaries
# =========================================================================

class TestPIDZoneManagerBounds:
    """Given a default PIDZoneManager, zone boundaries are correct."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.manager = PIDZoneManager()

    def test_given_default_manager_when_initialized_then_has_4_zones(self):
        zones = self.manager.get_all_zones()
        assert len(zones) == 4

    # ---- Zone 1: Low (≤ 20K) ----

    def test_given_10K_when_getting_zone_then_returns_zone_1(self):
        zone = self.manager.get_zone(10.0)
        assert zone.zone_id == 1
        assert zone.p == 100.0
        assert zone.i == 5.0
        assert zone.d == 0.0
        assert zone.heater_range == 1  # Low

    def test_given_15K_when_getting_zone_then_returns_zone_1(self):
        zone = self.manager.get_zone(15.0)
        assert zone.zone_id == 1

    def test_given_exactly_20K_when_getting_zone_then_returns_zone_1(self):
        """20.0K is the upper bound of zone 1 (≤ 20)."""
        zone = self.manager.get_zone(20.0)
        assert zone.zone_id == 1

    # ---- Zone 2: Medium (20–40K) ----

    def test_given_20_001K_when_getting_zone_then_returns_zone_2(self):
        zone = self.manager.get_zone(20.001)
        assert zone.zone_id == 2
        assert zone.p == 100.0
        assert zone.i == 3.0
        assert zone.d == 0.0
        assert zone.heater_range == 2  # Medium

    def test_given_30K_when_getting_zone_then_returns_zone_2(self):
        zone = self.manager.get_zone(30.0)
        assert zone.zone_id == 2

    def test_given_exactly_40K_when_getting_zone_then_returns_zone_2(self):
        """40.0K is the upper bound of zone 2 (≤ 40)."""
        zone = self.manager.get_zone(40.0)
        assert zone.zone_id == 2

    # ---- Zone 3: High (40–70K) ----
    # ---- Zone 4: Very High (> 70K) ----

    def test_given_40_001K_when_getting_zone_then_returns_zone_3(self):
        zone = self.manager.get_zone(40.001)
        assert zone.zone_id == 3
        assert zone.p == 150.0
        assert zone.i == 0.0
        assert zone.d == 0.0
        assert zone.heater_range == 2  # Medium (High forbidden)

    def test_given_60K_when_getting_zone_then_returns_zone_3(self):
        zone = self.manager.get_zone(60.0)
        assert zone.zone_id == 3

    def test_given_exactly_70K_when_getting_zone_then_returns_zone_3(self):
        """70.0K is the upper bound of zone 3 (≤ 70)."""
        zone = self.manager.get_zone(70.0)
        assert zone.zone_id == 3

    def test_given_70_001K_when_getting_zone_then_returns_zone_4(self):
        zone = self.manager.get_zone(70.001)
        assert zone.zone_id == 4
        assert zone.p == 150.0
        assert zone.i == 0.0
        assert zone.d == 0.0
        assert zone.heater_range == 2  # Medium (High forbidden)

    def test_given_80K_when_getting_zone_then_returns_zone_4(self):
        zone = self.manager.get_zone(80.0)
        assert zone.zone_id == 4


# =========================================================================
# TestClass: PIDZoneManager — Out-of-Range Clamping
# =========================================================================

class TestPIDZoneManagerClamping:
    """Given a default PIDZoneManager, out-of-range temperatures clamp."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.manager = PIDZoneManager()

    def test_given_5K_below_min_when_getting_zone_then_clamps_to_zone_1(self):
        zone = self.manager.get_zone(5.0)
        assert zone.zone_id == 1

    def test_given_100K_above_max_when_getting_zone_then_clamps_to_zone_4(self):
        zone = self.manager.get_zone(100.0)
        assert zone.zone_id == 4


# =========================================================================
# TestClass: PIDZoneManager — get_params
# =========================================================================

class TestPIDZoneManagerGetParams:
    """Given PIDZoneManager.get_params()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.manager = PIDZoneManager()

    def test_given_10K_when_getting_params_then_returns_correct_dict(self):
        params = self.manager.get_params(10.0)
        assert params["p"] == 100.0
        assert params["i"] == 5.0
        assert params["d"] == 0.0
        assert params["heater_range"] == 1
        assert params["heater_range_name"] == "Low"
        assert params["zone_id"] == 1

    def test_given_25K_when_getting_params_then_medium_zone(self):
        params = self.manager.get_params(25.0)
        assert params["p"] == 100.0
        assert params["i"] == 3.0
        assert params["heater_range"] == 2
        assert params["heater_range_name"] == "Medium"

    def test_given_77K_when_getting_params_then_very_high_zone(self):
        params = self.manager.get_params(77.0)
        assert params["zone_id"] == 4
        assert params["p"] == 150.0
        assert params["i"] == 0.0
        assert params["heater_range"] == 2  # Medium, not High


# =========================================================================
# TestClass: PIDZoneManager — Heater Range Constraint
# =========================================================================

class TestHeaterRangeConstraint:
    """Given the heater range policy: Low + Medium only, High forbidden."""

    def test_given_all_zones_when_checking_heater_range_then_never_high(self):
        for zone in ZONES:
            assert zone.heater_range in (1, 2), (
                f"Zone {zone.zone_id} uses heater_range={zone.heater_range}, "
                f"expected 1 (Low) or 2 (Medium). High (3) is forbidden."
            )

    def test_given_all_zones_when_checking_range_then_monotonic_non_decreasing(self):
        """Heater range should never decrease as temperature rises."""
        ranges = [z.heater_range for z in ZONES]
        for i in range(1, len(ranges)):
            assert ranges[i] >= ranges[i - 1], (
                f"Heater range decreased from zone {i} to {i+1}"
            )


# =========================================================================
# TestClass: PIDZoneManager — Full Sweep 10K→80K
# =========================================================================

class TestFullSweep10Kto80K:
    """Given the full temperature sweep range, every 2K step returns valid params."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.manager = PIDZoneManager()

    def test_given_every_2k_step_10_to_80_when_getting_params_then_all_valid(self):
        for t in range(10, 82, 2):
            params = self.manager.get_params(float(t))
            assert params["p"] > 0
            assert params["i"] >= 0
            assert params["d"] == 0
            assert params["heater_range"] in (1, 2)
            assert params["zone_id"] in (1, 2, 3, 4)

    def test_given_every_2k_step_when_checking_zone_transition_then_below_20_is_zone_1(self):
        for t in [10, 12, 14, 16, 18, 20]:
            zone = self.manager.get_zone(float(t))
            assert zone.zone_id == 1, f"{t}K should be zone 1, got zone {zone.zone_id}"

    def test_given_every_2k_step_when_checking_zone_transition_then_20_to_40_is_zone_2(self):
        for t in [22, 24, 26, 28, 30, 32, 34, 36, 38, 40]:
            zone = self.manager.get_zone(float(t))
            assert zone.zone_id == 2, f"{t}K should be zone 2, got zone {zone.zone_id}"

    def test_given_every_2k_step_when_checking_zone_transition_then_40_to_70_is_zone_3(self):
        for t in [42, 50, 60, 70]:
            zone = self.manager.get_zone(float(t))
            assert zone.zone_id == 3, f"{t}K should be zone 3, got zone {zone.zone_id}"

    def test_given_every_2k_step_when_checking_zone_transition_then_above_70_is_zone_4(self):
        for t in [72, 80, 90, 100]:
            zone = self.manager.get_zone(float(t))
            assert zone.zone_id == 4, f"{t}K should be zone 4, got zone {zone.zone_id}"


# =========================================================================
# TestClass: ZONES Definition
# =========================================================================

class TestZONESDefinition:
    """Given the default ZONES constant."""

    def test_given_zones_when_checking_coverage_then_no_gaps(self):
        """Zone boundaries must be contiguous with no gaps."""
        for i in range(len(ZONES) - 1):
            gap = ZONES[i + 1].temp_min - ZONES[i].temp_max
            assert gap <= 0.001, (
                f"Gap between zone {i+1} (max={ZONES[i].temp_max}) "
                f"and zone {i+2} (min={ZONES[i+1].temp_min}): gap={gap:.4f}K"
            )

    def test_given_zones_when_checking_then_no_overlap(self):
        """Each zone's min/max must not overlap improperly."""
        for i in range(len(ZONES) - 1):
            assert ZONES[i].temp_max <= ZONES[i + 1].temp_min + 1e-9, (
                f"Overlap between zone {i+1} and zone {i+2}"
            )

    def test_given_zone_1_when_checking_limits_then_min_is_0(self):
        assert ZONES[0].temp_min == 0.0

    def test_given_last_zone_when_checking_limits_then_max_is_very_large(self):
        assert ZONES[-1].temp_max >= 500.0  # effectively unlimited upper bound

    def test_given_all_zones_when_checking_D_then_all_zero(self):
        for zone in ZONES:
            assert zone.d == 0.0, f"Zone {zone.zone_id}: D should be 0 (PI control)"


# =========================================================================
# SetpointCalculator — No Overshoot Below 20K
# =========================================================================

class TestSetpointCalculatorBelow20K:
    """Given SetpointCalculator, below 20K: setpoint = target (no overshoot)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.calc = SetpointCalculator()

    def test_given_target_10K_current_9K_when_calculating_then_setpoint_equals_target(self):
        sp = self.calc.calculate(10.0, 9.0)
        assert sp == 10.0

    def test_given_target_15K_current_5K_when_calculating_then_no_overshoot(self):
        """Even with large error, below 20K = no overshoot for cryo safety."""
        sp = self.calc.calculate(15.0, 5.0)
        assert sp == 15.0

    def test_given_target_19_999K_when_calculating_then_setpoint_equals_target(self):
        sp = self.calc.calculate(19.999, 18.0)
        assert sp == 19.999

    def test_given_target_20K_exactly_when_delta_is_large_then_no_overshoot(self):
        """At exactly 20K threshold (< 20 check), still no overshoot."""
        sp = self.calc.calculate(20.0, 15.0)
        assert sp == 20.0


# =========================================================================
# SetpointCalculator — Medium Zone (20–40K)
# =========================================================================

class TestSetpointCalculatorMediumZone:
    """Given SetpointCalculator, 20–40K: overshoot = clamp(delta × 0.3, 1.0, 5.0)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.calc = SetpointCalculator()

    def test_given_target_25K_current_20K_when_calculating_then_min_1K_overshoot(self):
        """delta=5, overshoot=5×0.3=1.5 > 1.0 min, so result=1.5."""
        sp = self.calc.calculate(25.0, 20.0)
        assert sp == pytest.approx(26.5)

    def test_given_target_22K_current_21K_when_small_delta_then_clamped_to_1K_min(self):
        """delta=1, overshoot=1×0.3=0.3 < 1.0 min → clamp to 1.0."""
        sp = self.calc.calculate(22.0, 21.0)
        assert sp == pytest.approx(23.0)  # 22 + 1.0

    def test_given_target_30K_current_10K_when_large_delta_then_clamped_to_5K_max(self):
        """delta=20, overshoot=20×0.3=6.0 > 5.0 max → clamp to 5.0."""
        sp = self.calc.calculate(30.0, 10.0)
        assert sp == pytest.approx(35.0)  # 30 + 5.0

    def test_given_target_35K_current_30K_when_moderate_delta_then_scaled(self):
        """delta=5, overshoot=5×0.3=1.5."""
        sp = self.calc.calculate(35.0, 30.0)
        assert sp == pytest.approx(36.5)


# =========================================================================
# SetpointCalculator — High Zone (> 40K)
# =========================================================================

class TestSetpointCalculatorHighZone:
    """Given SetpointCalculator, > 40K: overshoot = clamp(delta × 0.5, 1.0, 5.0)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.calc = SetpointCalculator()

    def test_given_target_50K_current_45K_when_calculating_then_overshoot_is_2_5(self):
        """delta=5, overshoot=5×0.5=2.5."""
        sp = self.calc.calculate(50.0, 45.0)
        assert sp == pytest.approx(52.5)

    def test_given_target_45K_current_44K_when_small_delta_then_clamped_to_1K_min(self):
        """delta=1, overshoot=1×0.5=0.5 < 1.0 min → clamp to 1.0."""
        sp = self.calc.calculate(45.0, 44.0)
        assert sp == pytest.approx(46.0)

    def test_given_target_80K_current_60K_when_large_delta_then_clamped_to_5K_max(self):
        """delta=20, overshoot=20×0.5=10 > 5.0 max → clamp to 5.0."""
        sp = self.calc.calculate(80.0, 60.0)
        assert sp == pytest.approx(85.0)

    def test_given_target_60K_current_40K_when_moderate_delta_then_scaled(self):
        """delta=20, overshoot=20×0.5=10 → clamp to 5.0."""
        sp = self.calc.calculate(60.0, 40.0)
        assert sp == pytest.approx(65.0)  # 60 + 5.0


# =========================================================================
# SetpointCalculator — Invariants
# =========================================================================

class TestSetpointCalculatorInvariants:
    """Given SetpointCalculator, setpoint ≥ target always."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.calc = SetpointCalculator()

    def test_given_any_valid_input_when_calculating_then_setpoint_never_less_than_target(self):
        for target, current in [
            (10, 9), (15, 5), (20, 18), (25, 20), (30, 10),
            (40, 30), (45, 44), (50, 30), (60, 30), (77, 40), (80, 50),
        ]:
            sp = self.calc.calculate(target, current)
            assert sp >= target, f"target={target}, current={current}, setpoint={sp}"

    def test_given_cooling_scenario_when_target_below_current_then_no_undershoot(self):
        """When cooling down (target < current), setpoint = target exactly."""
        sp = self.calc.calculate(20.0, 30.0)
        assert sp == 20.0

    def test_given_target_equal_current_when_calculating_then_setpoint_equals_target_or_min_overshoot(self):
        """When already at target, still apply overshoot to maintain (except < 20K)."""
        sp = self.calc.calculate(10.0, 10.0)
        assert sp == 10.0  # below 20K

        sp2 = self.calc.calculate(50.0, 50.0)
        # delta=0 → overshoot=0 → clamped to min 1.0K
        assert sp2 == pytest.approx(51.0)


# =========================================================================
# SetpointCalculator — get_strategy_info
# =========================================================================

class TestSetpointCalculatorStrategyInfo:
    """Given SetpointCalculator.get_strategy_info()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.calc = SetpointCalculator()

    def test_given_target_15K_when_getting_info_then_cryogenic_regime(self):
        info = self.calc.get_strategy_info(15.0)
        assert info["regime"] == "cryogenic"
        assert info["has_overshoot"] is False
        assert info["overshoot_factor"] == 0.0

    def test_given_target_30K_when_getting_info_then_medium_regime(self):
        info = self.calc.get_strategy_info(30.0)
        assert info["regime"] == "medium"
        assert info["has_overshoot"] is True
        assert info["overshoot_factor"] == 0.3

    def test_given_target_77K_when_getting_info_then_high_regime(self):
        info = self.calc.get_strategy_info(77.0)
        assert info["regime"] == "high"
        assert info["has_overshoot"] is True
        assert info["overshoot_factor"] == 0.5
        assert info["min_overshoot"] == 1.0
        assert info["max_overshoot"] == 5.0


# =========================================================================
# SetpointCalculator — Custom Thresholds
# =========================================================================

class TestSetpointCalculatorCustomThresholds:
    """Given SetpointCalculator with custom thresholds."""

    def test_given_custom_thresholds_when_constructing_then_uses_them(self):
        calc = SetpointCalculator(
            low_threshold=15.0, med_threshold=35.0,
            min_overshoot=0.5, max_overshoot=3.0,
            factor_medium=0.2, factor_high=0.4,
        )
        # Below 15K: no overshoot
        assert calc.calculate(14.0, 10.0) == 14.0
        # 15-35K: medium factor
        sp = calc.calculate(20.0, 15.0)  # delta=5, overshoot=5×0.2=1.0
        assert sp == pytest.approx(21.0)
        # > 35K: high factor
        sp = calc.calculate(40.0, 30.0)  # delta=10, overshoot=10×0.4=4.0 → clamp to 3.0
        assert sp == pytest.approx(43.0)
