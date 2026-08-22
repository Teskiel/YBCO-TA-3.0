# -*- coding: utf-8 -*-
"""
Smart PID parameter selection based on temperature zone.

Pure algorithm module — no hardware dependencies.

The LakeShore 335 cryostat behaves very differently at cryogenic
temperatures (< 20 K) vs intermediate (20–40 K) vs warm (> 40 K).
This module selects appropriate P/I/D gains for each zone and
calculates setpoint overshoot to speed up approach.

Usage:
    from pid_controller import SmartPIDController

    params = SmartPIDController.get_params_for_temperature(30.0)
    # → {"p": 100.0, "i": 0.0, "d": 0.0}

    sp = SmartPIDController.calculate_adjusted_setpoint(50.0, 30.0)
    # → 60.0  (target + overshoot to compensate for cooling lag)
"""

from typing import Dict


class SmartPIDController:
    """Temperature-zone-aware PID parameter selector."""

    @staticmethod
    def get_params_for_temperature(target_k: float) -> Dict:
        """Return (P, I, D) dict appropriate for ``target_k``.

        Zones:
          - low_temp:    T ≤ 20 K   (P=100, I=5,  D=0) — integral for accuracy
          - medium_temp: 20 < T ≤ 40 (P=100, I=0,  D=0) — proportional only
          - high_temp:   T > 40 K   (P=150, I=0,  D=0) — higher P for faster response
        """
        from config import PID_PARAMS

        if target_k <= 20.0:
            return PID_PARAMS["low_temp"]
        elif target_k <= 40.0:
            return PID_PARAMS["medium_temp"]
        else:
            return PID_PARAMS["high_temp"]

    @staticmethod
    def calculate_adjusted_setpoint(target_k: float, current_k: float) -> float:
        """Compute setpoint with controlled overshoot.

        Below 20 K: setpoint = target (no overshoot — cryogenic risk).
        Above 20 K: overshoot = max(1.0 K, delta × overshoot_factor)
        to compensate for thermal lag and speed up approach.
        """
        from config import setpoint_adjust_settings

        if target_k < setpoint_adjust_settings["low_temp_threshold"]:
            return target_k

        delta = target_k - current_k
        overshoot = max(1.0, abs(delta) * setpoint_adjust_settings["overshoot_factor"])
        return target_k + overshoot
