# -*- coding: utf-8 -*-
"""
PID parameter zone definitions and setpoint calculator.

Pure algorithm module — no hardware dependencies.

Based on the LakeShore 335 manual's zone tuning recommendations.
Cryogenic systems are highly nonlinear: copper specific heat changes by
orders of magnitude between 10K and 100K, and cooling power varies
dramatically.  A single PID set cannot work across the full range.

Four-zone scheme:
  - Low       (≤ 20K):  P=100, I=5,  D=0, Range=Low    — cryogenic safety
  - Med       (20–40K): P=100, I=3,  D=0, Range=Medium — transitional
  - High      (40–70K): P=150, I=0,  D=0, Range=Medium — high cooling power
  - Very High (> 70K):  P=150, I=0,  D=0, Range=Medium — 更强过冲 2.5K

Heater range policy: Low + Medium only.  High is forbidden to protect
the sample and heater element.

Setpoint overshoot strategy:
  Setpoint is set ABOVE the actual target temperature so the LakeShore
  internal PID drives the heater harder, compensating for cryocooler
  cooling power.  The actual temperature "coasts" to equilibrium near
  the physical target.

  - Below 20K:  NO overshoot (setpoint = target).  Tiny thermal mass
                at cryogenic temperatures makes overshoot dangerous.
  - 20–40K:     Moderate overshoot, factor 0.3, clamped [1.0, 5.0] K.
  - Above 40K:  Aggressive overshoot, factor 0.5, clamped [1.0, 5.0] K.

Usage:
    from pid_parameters import PIDZoneManager, SetpointCalculator

    manager = PIDZoneManager()
    zone = manager.get_zone(30.0)
    params = manager.get_params(30.0)

    calc = SetpointCalculator()
    setpoint = calc.calculate(target=50.0, current=45.0)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# =========================================================================
# PIDZone dataclass
# =========================================================================

@dataclass(frozen=True)
class PIDZone:
    """Immutable definition of one PID parameter zone.

    Attributes:
        zone_id: 1-based zone number.
        temp_min: inclusive lower bound in kelvin.
        temp_max: inclusive upper bound in kelvin.
        p, i, d: PID parameters.
        heater_range: 1=Low, 2=Medium (3=High is forbidden).
        description: human-readable description.
    """

    zone_id: int
    temp_min: float
    temp_max: float
    p: float
    i: float
    d: float
    heater_range: int
    description: str


# =========================================================================
# Default zone definitions
# =========================================================================

ZONES: Tuple[PIDZone, ...] = (
    PIDZone(
        zone_id=1,
        temp_min=0.0,
        temp_max=20.0,
        p=100.0,
        i=5.0,
        d=0.0,
        heater_range=1,  # Low
        description="低温区 ≤20K: 沿用原方案 P=100/I=5, Low档, 不过冲",
    ),
    PIDZone(
        zone_id=2,
        temp_min=20.0,
        temp_max=40.0,
        p=100.0,
        i=3.0,
        d=0.0,
        heater_range=2,  # Medium
        description="中温区 20–40K: 引入小I项(3s)消除稳态偏差, Med档, 温和过冲",
    ),
    PIDZone(
        zone_id=3,
        temp_min=40.0,
        temp_max=70.0,
        p=150.0,
        i=0.0,
        d=0.0,
        heater_range=2,  # Medium (High forbidden)
        description="高温区 40–70K: 沿用原方案 P=150/I=0, Med档, 较强过冲 2.0K",
    ),
    PIDZone(
        zone_id=4,
        temp_min=70.0,
        temp_max=999.0,  # effectively unlimited upper bound
        p=150.0,
        i=0.0,
        d=0.0,
        heater_range=2,  # Medium (High forbidden)
        description="超高温区 >70K: P=150/I=0, Med档, 更强过冲 2.5K",
    ),
)

RANGE_NAMES = {1: "Low", 2: "Medium", 3: "High"}


# =========================================================================
# PIDZoneManager
# =========================================================================

class PIDZoneManager:
    """Manages lookup of PID parameters by temperature across multiple zones.

    Uses a linear scan across zones (3 zones is trivially fast).
    Out-of-range temperatures are clamped to the nearest zone.
    """

    def __init__(self, zones: Optional[List[PIDZone]] = None) -> None:
        """Initialise with optional custom zone list.

        Args:
            zones: list of PIDZone.  If None, uses the default ZONES.
                   Zones are automatically sorted by temp_min.
        """
        raw = list(zones) if zones is not None else list(ZONES)
        self._zones: List[PIDZone] = sorted(raw, key=lambda z: z.temp_min)
        if not self._zones:
            raise ValueError("At least one zone is required")

    # ---- public API ----

    def get_zone(self, temperature: float) -> PIDZone:
        """Return the PIDZone for a given temperature.

        Temperatures below the min of the first zone clamp to zone 1.
        Temperatures above the max of the last zone clamp to the last zone.
        At shared boundaries (e.g. exactly 20.0K), the lower-numbered zone
        wins because zones are scanned in order.

        Args:
            temperature: temperature in kelvin.

        Returns:
            The matching PIDZone instance.

        Raises:
            ValueError: if no zone matches (should never happen with valid zones).
        """
        # Clamp low
        if temperature < self._zones[0].temp_min:
            return self._zones[0]

        # Scan for matching zone
        for zone in self._zones:
            if zone.temp_min <= temperature <= zone.temp_max:
                return zone

        # Clamp high
        return self._zones[-1]

    def get_params(self, temperature: float) -> Dict:
        """Return PID parameters and metadata for a temperature.

        Convenience wrapper around get_zone() — unpacks the zone into a
        dict compatible with callers that used the old config.PID_PARAMS dict.

        Args:
            temperature: temperature in kelvin.

        Returns:
            dict with keys: p, i, d, heater_range, heater_range_name,
            zone_id, zone_description.
        """
        zone = self.get_zone(temperature)
        return {
            "p": zone.p,
            "i": zone.i,
            "d": zone.d,
            "heater_range": zone.heater_range,
            "heater_range_name": RANGE_NAMES.get(zone.heater_range, "Unknown"),
            "zone_id": zone.zone_id,
            "zone_description": zone.description,
        }

    def get_all_zones(self) -> List[PIDZone]:
        """Return a copy of all zone definitions."""
        return list(self._zones)

    # ---- validation ----

    @staticmethod
    def validate_zones(zones: List[PIDZone]) -> List[str]:
        """Validate a zone list for coverage and correctness.

        Checks: non-empty, sorted, no gaps, no overlaps, all fields valid,
        heater_range in {1, 2}, D == 0 throughout.

        Args:
            zones: list of PIDZone to validate.

        Returns:
            List of error strings.  Empty list means valid.
        """
        errors: List[str] = []

        if not zones:
            errors.append("Zone list is empty")
            return errors

        sorted_zones = sorted(zones, key=lambda z: z.temp_min)

        for i, zone in enumerate(sorted_zones):
            # Field value checks
            if zone.p <= 0:
                errors.append(f"Zone {zone.zone_id}: P={zone.p} must be > 0")
            if zone.i < 0:
                errors.append(f"Zone {zone.zone_id}: I={zone.i} must be >= 0")
            if zone.d != 0:
                errors.append(f"Zone {zone.zone_id}: D={zone.d} must be 0 (PI control)")
            if zone.heater_range not in (1, 2):
                errors.append(
                    f"Zone {zone.zone_id}: heater_range={zone.heater_range} "
                    f"must be 1 (Low) or 2 (Medium). High (3) is forbidden."
                )
            if zone.temp_min >= zone.temp_max:
                errors.append(
                    f"Zone {zone.zone_id}: temp_min ({zone.temp_min}) >= "
                    f"temp_max ({zone.temp_max})"
                )

        # Gap check
        for i in range(len(sorted_zones) - 1):
            gap = sorted_zones[i + 1].temp_min - sorted_zones[i].temp_max
            if gap > 0.001:
                errors.append(
                    f"Gap of {gap:.4f}K between zone {sorted_zones[i].zone_id} "
                    f"(max={sorted_zones[i].temp_max}) and zone "
                    f"{sorted_zones[i+1].zone_id} (min={sorted_zones[i+1].temp_min})"
                )

        # Overlap check
        for i in range(len(sorted_zones) - 1):
            if sorted_zones[i].temp_max > sorted_zones[i + 1].temp_min + 1e-9:
                errors.append(
                    f"Overlap between zone {sorted_zones[i].zone_id} "
                    f"(max={sorted_zones[i].temp_max}) and zone "
                    f"{sorted_zones[i+1].zone_id} (min={sorted_zones[i+1].temp_min})"
                )

        return errors


# =========================================================================
# SetpointCalculator
# =========================================================================

class SetpointCalculator:
    """Calculates temperature setpoints with zone-dependent overshoot.

    Strategy (conservative, based on LakeShore 335 manual recommendations):

      - Below low_threshold (default 20K):
            setpoint = target  (NO overshoot — cryogenic safety).
      - low_threshold to med_threshold (default 20–40K):
            overshoot = clamp(delta × factor_medium, min_overshoot, max_overshoot).
      - Above med_threshold (default > 40K):
            overshoot = clamp(delta × factor_high, min_overshoot, max_overshoot).

      When cooling down (delta <= 0), setpoint = target (no undershoot).
    """

    def __init__(
        self,
        low_threshold: float = 20.0,
        med_threshold: float = 40.0,
        min_overshoot: float = 1.0,
        max_overshoot: float = 5.0,
        factor_medium: float = 0.3,
        factor_high: float = 0.5,
    ) -> None:
        """Initialise with customisable thresholds and factors.

        Args:
            low_threshold: below this, no overshoot (K).
            med_threshold: below this but >= low, medium-factor overshoot (K).
            min_overshoot: floor on overshoot amount (K).
            max_overshoot: ceiling on overshoot amount (K).
            factor_medium: multiplier for medium zone (20–40K).
            factor_high: multiplier for high zone (> 40K).
        """
        if low_threshold >= med_threshold:
            raise ValueError(
                f"low_threshold ({low_threshold}) must be < med_threshold ({med_threshold})"
            )
        self.low_threshold = low_threshold
        self.med_threshold = med_threshold
        self.min_overshoot = min_overshoot
        self.max_overshoot = max_overshoot
        self.factor_medium = factor_medium
        self.factor_high = factor_high

    # ---- public API ----

    def calculate(self, target_k: float, current_k: float) -> float:
        """Compute the setpoint value to write to the LakeShore.

        Args:
            target_k: desired final temperature in kelvin.
            current_k: current actual temperature in kelvin.

        Returns:
            setpoint value (always >= target_k).
        """
        delta = target_k - current_k

        # Cooling down — no overshoot (but delta==0 still gets overshoot above 20K)
        if delta < 0:
            return target_k

        # At or below low threshold: no overshoot (cryogenic safety)
        if target_k <= self.low_threshold:
            return target_k

        # Medium zone
        if target_k < self.med_threshold:
            factor = self.factor_medium
        else:
            # High zone
            factor = self.factor_high

        raw_overshoot = delta * factor
        clamped = max(self.min_overshoot, min(raw_overshoot, self.max_overshoot))
        return target_k + clamped

    def get_strategy_info(self, target_k: float) -> Dict:
        """Return human-readable info about the overshoot strategy.

        Args:
            target_k: target temperature in kelvin.

        Returns:
            dict with keys: regime, overshoot_factor, min_overshoot,
            max_overshoot, has_overshoot.
        """
        if target_k <= self.low_threshold:
            return {
                "regime": "cryogenic",
                "overshoot_factor": 0.0,
                "min_overshoot": 0.0,
                "max_overshoot": 0.0,
                "has_overshoot": False,
            }
        elif target_k < self.med_threshold:
            return {
                "regime": "medium",
                "overshoot_factor": self.factor_medium,
                "min_overshoot": self.min_overshoot,
                "max_overshoot": self.max_overshoot,
                "has_overshoot": True,
            }
        else:
            return {
                "regime": "high",
                "overshoot_factor": self.factor_high,
                "min_overshoot": self.min_overshoot,
                "max_overshoot": self.max_overshoot,
                "has_overshoot": True,
            }
