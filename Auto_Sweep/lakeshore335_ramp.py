# -*- coding: utf-8 -*-
"""
LakeShore 335 Standalone Temperature Ramp Controller
=====================================================
Ramps temperature from 10K to 80K (2K steps) with zone-optimised
PID parameters and dynamic setpoint overshoot.

Key features:
  - 3-zone PID scheme (≤20K, 20–40K, >40K) — Low/Med heater only
  - Setpoint overshoot: min 1K, max 5K, none below 20K
  - 20–40K zone: real-time temperature state diagnostics
  - CSV data logging + JSON optimal parameter summary on completion
  - Emergency stop: Ctrl+C → all heaters off
  - Timeout protection per temperature step

Reuses:
  - LakeShore335 driver (lakeshore_control.py)
  - AdvancedStabilityMonitor (stability_monitor.py)
  - PIDZoneManager + SetpointCalculator (pid_parameters.py)
  - TemperatureStateDiagnostics (temperature_state_diagnostics.py)

Usage:
    python lakeshore335_ramp.py
    python lakeshore335_ramp.py --start 20 --end 30 --step 2 --hold-seconds 30
    python lakeshore335_ramp.py --address ASRL3::INSTR
"""

import argparse
import atexit
import csv
import json
import os
import signal
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# =========================================================================
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                    EXPERIMENT CONFIGURATION                              ║
# ║   Edit these values to configure your temperature ramp.                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝
# =========================================================================

RAMP_START_K = 10.0        # 起始温度 (K) — Starting temperature
RAMP_END_K = 80.0          # 终止温度 (K) — Ending temperature
RAMP_STEP_K = 2.0          # 温度步进间隔 (K) — Temperature step interval

# =========================================================================

# ---- config defaults ----
from config import (
    resource_lakeshore,
    custom_stability_settings,
)

# ---- algorithms ----
from stability_monitor import AdvancedStabilityMonitor
from pid_parameters import PIDZoneManager, SetpointCalculator
from temperature_state_diagnostics import (
    TemperatureStateDiagnostics,
    TemperatureReading,
)

# ---- instrument driver ----
from lakeshore_control import LakeShore335, configure_lakeshore_serial


# =========================================================================
# Ramp Controller
# =========================================================================

class LakeShore335RampController:
    """Orchestrates a LakeShore 335 temperature ramp with zone-optimised PID.

    Reuses:
      - LakeShore335 driver (lakeshore_control.py)
      - AdvancedStabilityMonitor (stability_monitor.py)
      - PIDZoneManager + SetpointCalculator (pid_parameters.py)
      - TemperatureStateDiagnostics (temperature_state_diagnostics.py)

    Does NOT touch VNA or laser — pure temperature control.
    """

    def __init__(
        self,
        visa_address: str,
        zone_manager: Optional[PIDZoneManager] = None,
        setpoint_calculator: Optional[SetpointCalculator] = None,
        stability_method: str = "custom",
        poll_seconds: float = 10.0,
        stable_hold_seconds: float = 60.0,
        max_wait_seconds: float = 1800.0,
        log_dir: Optional[str] = None,
    ) -> None:
        self.visa_address = visa_address
        self.zone_manager = zone_manager or PIDZoneManager()
        self.setpoint_calculator = setpoint_calculator or SetpointCalculator()
        self.stability_method = stability_method
        self.poll_seconds = poll_seconds
        self.stable_hold_seconds = stable_hold_seconds
        self.max_wait_seconds = max_wait_seconds
        self.log_dir = log_dir or self._default_log_dir()

        # Lazy init
        self._device: Optional[LakeShore335] = None
        self._stability_monitor: Optional[AdvancedStabilityMonitor] = None
        self._diagnostics: Optional[TemperatureStateDiagnostics] = None
        self._aborted: bool = False
        self._current_heater_range: int = 1  # tracks current heater range (1=Low, 2=Med)
        self._pure_p_mode: bool = False       # True after 5 oscillation failures
        self._pure_p_p_value: float = 100.0   # P value to use in pure-P mode
        self._csv_path: str = ""
        self._csv_file: Any = None
        self._csv_writer: Any = None
        self._optimal_params: List[Dict] = []

    # ==================================================================
    # Connection
    # ==================================================================

    def connect(self) -> None:
        """Open the VISA connection and verify instrument identity."""
        self._device = LakeShore335(visa_address=self.visa_address)
        configure_lakeshore_serial(self._device, self.visa_address)
        print(f"[OK] Connected: {self._device.identity}")
        temp = self._device.get_temperature("A")
        print(f"[OK] Current temperature: {temp:.3f} K")

    # ==================================================================
    # State queries
    # ==================================================================

    def get_current_state(self) -> Dict[str, Any]:
        """Read current temperature, heater%, PID, and heater range."""
        if self._device is None:
            return {}
        try:
            p, i, d = self._device.get_pid(1)
            return {
                "temperature_k": self._device.get_temperature("A"),
                "heater_percent": self._device.get_heater_percent(1),
                "heater_range": self._device.get_heater_range(1),
                "setpoint_k": self._device.get_setpoint(1),
                "p": p, "i": i, "d": d,
            }
        except Exception as e:
            print(f"[Warning] State read failed: {e}")
            return {}

    # ==================================================================
    # Heater range auto-upgrade
    # ==================================================================

    def _check_and_upgrade_heater_range(self) -> bool:
        """Check heater% and auto-upgrade range if > 85%.

        Low → Medium when heater% > 85%.
        Never upgrades to High (forbidden by safety policy).

        Returns True if an upgrade was performed.
        """
        if self._device is None:
            return False
        try:
            heater_pct = self._device.get_heater_percent(1)
            current_range = self._device.get_heater_range(1)
            self._current_heater_range = current_range
            if heater_pct > 85.0 and current_range == 1:
                print(f"  [Auto-Range] Heater at {heater_pct:.0f}% on Low → upgrading to Medium")
                self._device.set_heater_range(1, 2)
                self._current_heater_range = 2
                return True
        except Exception as e:
            print(f"  [Warning] Heater range check failed: {e}")
        return False

    # ==================================================================
    # Zone settings application
    # ==================================================================

    def apply_zone_settings(self, target_k: float) -> Dict[str, Any]:
        """Look up PID zone, compute setpoint, apply PID + heater range + setpoint.

        SCPI order: heater range → PID → setpoint.
        """
        if self._device is None:
            raise RuntimeError("Device not connected")

        # 1. Look up zone
        zone = self.zone_manager.get_zone(target_k)
        params = self.zone_manager.get_params(target_k)

        # 2. Set heater range
        self._device.set_heater_range(1, zone.heater_range)
        time.sleep(0.5)

        # 3. Set PID (respect pure-P mode: force I=0)
        if self._pure_p_mode:
            self._device.set_pid(
                self._pure_p_p_value if self._pure_p_p_value > 0 else zone.p,
                0.0, 0.0, loop=1,
            )
            print(f"  [Pure-P] I=0 forced (震荡{5}+次失败，靠过冲调节)")
        else:
            self._device.set_pid(zone.p, zone.i, zone.d, loop=1)
        time.sleep(0.5)

        # 4. Read current temperature
        try:
            current_k = self._device.get_temperature("A")
        except Exception:
            current_k = target_k

        # 5. Compute setpoint with overshoot
        setpoint_k = self.setpoint_calculator.calculate(target_k, current_k)
        overshoot_k = setpoint_k - target_k

        # 6. Apply setpoint
        self._device.set_temperature(setpoint_k, loop=1)

        return {
            "zone_id": zone.zone_id,
            "zone_description": zone.description,
            "p": zone.p, "i": zone.i, "d": zone.d,
            "heater_range": zone.heater_range,
            "heater_range_name": params["heater_range_name"],
            "setpoint_k": setpoint_k,
            "overshoot_k": overshoot_k,
            "target_k": target_k,
            "current_k": current_k,
        }

    # ==================================================================
    # Stability wait
    # ==================================================================

    def wait_for_stability(
        self,
        target_k: float,
        enable_diagnostics: bool = True,
    ) -> Dict[str, Any]:
        """Poll temperature until stability criteria are met or timeout.

        Args:
            target_k: target temperature in kelvin.
            enable_diagnostics: if True, run state diagnostics (20–40K zone).

        Returns:
            dict with keys: stable, final_temp, elapsed_s, heater_percent,
            diagnostic_events, reason.
        """
        monitor = AdvancedStabilityMonitor()
        self._stability_monitor = monitor

        # Enable diagnostics for 20–40K zone
        zone = self.zone_manager.get_zone(target_k)
        diag: Optional[TemperatureStateDiagnostics] = None
        diag_events: List[Dict] = []
        if enable_diagnostics and zone.zone_id == 2:
            diag = TemperatureStateDiagnostics(sample_interval=self.poll_seconds)
            self._diagnostics = diag

        start_time = time.monotonic()
        last_stable_time: Optional[float] = None
        last_diag_time = 0.0
        current_pid = {"p": zone.p, "i": zone.i, "d": zone.d}

        while True:
            if self._aborted:
                return {
                    "stable": False, "final_temp": 0.0,
                    "elapsed_s": time.monotonic() - start_time,
                    "heater_percent": 0.0, "diagnostic_events": diag_events,
                    "reason": "aborted",
                }

            # Read temperature with retry
            current_k = self._read_temperature_with_retry()
            if current_k is None:
                time.sleep(self.poll_seconds)
                continue

            # Add to stability monitor
            monitor.add_reading(current_k, target_k)

            # Add to diagnostics
            if diag is not None:
                diag.add_reading(TemperatureReading(
                    timestamp=time.monotonic(),
                    temperature=current_k,
                    target=target_k,
                ))

            elapsed = time.monotonic() - start_time

            # Heater auto-range check (every poll cycle, all zones)
            self._check_and_upgrade_heater_range()

            # Periodic diagnostics (every 60s in 20–40K zone)
            if diag is not None and elapsed - last_diag_time >= 60.0:
                last_diag_time = elapsed
                result = diag.diagnose(target_k, current_pid)
                print(f"  [Diagnostic] {result.description}")

                # Track pure-P mode from diagnostics
                if result.pid_adjustment.get("force_pure_p"):
                    self._pure_p_mode = True
                    # Use the recommended P value in pure-P mode
                    if "p_new" in result.pid_adjustment:
                        self._pure_p_p_value = result.pid_adjustment["p_new"]
                    print(f"  [Pure-P] Activated! 震荡≥5次调节失败 → I=0, P={self._pure_p_p_value:.0f}")

                if result.pid_adjustment and not result.pid_adjustment.get("locked_out"):
                    # Apply PID adjustment
                    try:
                        self._apply_diagnostic_adjustment(result, current_pid)
                        diag.record_adjustment(
                            pid_before=current_pid,
                            pid_after=current_pid,  # will be updated below
                        )
                        # Update current_pid with new values
                        new_p, new_i, new_d = self._device.get_pid(1)  # type: ignore[union-attr]
                        current_pid = {"p": new_p, "i": new_i, "d": new_d}
                        diag_events.append({
                            "time_s": elapsed,
                            "state": result.state,
                            "adjustment": result.pid_adjustment,
                            "description": result.description,
                        })
                        print(f"  [Adjust] Applied: {result.pid_adjustment}")
                    except Exception as e:
                        print(f"  [Adjust] Failed: {e}")

            # Stability check
            stability_result = monitor.check_stability(target_k, method=self.stability_method)

            error_k = abs(current_k - target_k)
            heater_pct = 0.0
            try:
                if self._device:
                    heater_pct = self._device.get_heater_percent(1)
            except Exception:
                pass

            print(
                f"  Target {target_k:.1f}K | Current {current_k:.3f}K | "
                f"Error {error_k:.3f}K | Heater {heater_pct:.0f}% | "
                f"Elapsed {elapsed/60:.1f}min | "
                f"{'STABLE' if stability_result.get('stable') else 'waiting'}"
            )

            if stability_result.get("stable"):
                if last_stable_time is None:
                    last_stable_time = time.monotonic()
                    print(f"  → Within tolerance, holding {self.stable_hold_seconds}s...")

                hold_duration = time.monotonic() - last_stable_time
                if hold_duration >= self.stable_hold_seconds:
                    print(f"  → Stable for {hold_duration:.0f}s. Ready.")
                    return {
                        "stable": True,
                        "final_temp": current_k,
                        "elapsed_s": elapsed,
                        "heater_percent": heater_pct,
                        "diagnostic_events": diag_events,
                        "reason": "stable",
                    }
            else:
                last_stable_time = None

            # Timeout check
            if elapsed >= self.max_wait_seconds:
                print(f"  [Timeout] {self.max_wait_seconds/60:.0f}min reached at {target_k:.1f}K")
                return {
                    "stable": False,
                    "final_temp": current_k,
                    "elapsed_s": elapsed,
                    "heater_percent": heater_pct,
                    "diagnostic_events": diag_events,
                    "reason": "timeout",
                }

            time.sleep(self.poll_seconds)

    # ==================================================================
    # Diagnostic adjustment
    # ==================================================================

    def _apply_diagnostic_adjustment(
        self, result, current_pid: Dict
    ) -> None:
        """Apply PID adjustment recommended by diagnostics."""
        if self._device is None:
            return

        adj = result.pid_adjustment

        # Calculate new PID
        if "p_new" in adj:
            new_p = adj["p_new"]
        elif "p_delta" in adj:
            new_p = current_pid["p"] + adj["p_delta"]
        else:
            new_p = current_pid["p"]

        if "i_new" in adj:
            new_i = adj["i_new"]
        elif "i_delta" in adj:
            new_i = current_pid["i"] + adj["i_delta"]
        else:
            new_i = current_pid["i"]

        new_d = current_pid.get("d", 0.0)

        # Clamp to safe ranges
        new_p = max(10.0, min(new_p, 500.0))
        new_i = max(0.0, min(new_i, 100.0))
        new_d = 0.0

        self._device.set_pid(new_p, new_i, new_d, loop=1)
        print(f"  [Adjust] New PID: P={new_p:.1f}, I={new_i:.1f}, D={new_d:.1f}")

        # Override setpoint if recommended (overshooting)
        if "setpoint_override_k" in adj:
            self._device.set_temperature(adj["setpoint_override_k"], loop=1)
            print(f"  [Adjust] Setpoint override: {adj['setpoint_override_k']:.2f}K")

    # ==================================================================
    # Emergency stop
    # ==================================================================

    def emergency_stop(self) -> None:
        """Set all heaters OFF. Safe to call from signal handler."""
        self._aborted = True
        print("\n[EMERGENCY] Turning all heaters OFF...")
        if self._device is not None:
            try:
                self._device.set_heater_range(1, 0)
                self._device.set_heater_range(2, 0)
                print("[EMERGENCY] Heaters OFF confirmed.")
            except Exception as e:
                print(f"[EMERGENCY] Heater-off command failed: {e}")

    # ==================================================================
    # Main ramp loop
    # ==================================================================

    def _generate_targets(
        self, start: float, end: float, step: float
    ) -> List[float]:
        """Generate the list of target temperatures."""
        n_steps = int(round((end - start) / step)) + 1
        return [round(start + i * step, 2) for i in range(n_steps)]

    def run_ramp(
        self,
        start_k: float = None,  # type: ignore[assignment]
        end_k: float = None,    # type: ignore[assignment]
        step: float = None,     # type: ignore[assignment]
    ) -> Dict[str, Any]:
        if start_k is None:
            start_k = RAMP_START_K
        if end_k is None:
            end_k = RAMP_END_K
        if step is None:
            step = RAMP_STEP_K
        """Execute the full temperature ramp.

        Returns summary dict.
        """
        targets = self._generate_targets(start_k, end_k, step)
        self._optimal_params = []

        self._open_csv_log()
        ramp_start = time.monotonic()
        completed = 0

        print(f"\n{'='*60}")
        print(f"LakeShore 335 Temperature Ramp")
        print(f"{'='*60}")
        print(f"Range: {start_k}K → {end_k}K, step {step}K ({len(targets)} steps)")
        print(f"Method: {self.stability_method}")
        print(f"Heater policy: Low + Medium only")
        print(f"Log: {self._csv_path}")
        print(f"{'='*60}\n")

        for i, target_k in enumerate(targets):
            if self._aborted:
                break

            step_start = time.monotonic()
            print(f"\n{'─'*60}")
            print(f"Step {i+1}/{len(targets)}: Target {target_k:.1f}K")
            print(f"{'─'*60}")

            # 1. Apply zone-optimised PID and setpoint
            try:
                settings = self.apply_zone_settings(target_k)
                print(
                    f"Zone {settings['zone_id']} ({settings['zone_description'][:40]}...)\n"
                    f"  P={settings['p']:.0f}, I={settings['i']:.0f}, D={settings['d']:.0f}, "
                    f"Range={settings['heater_range_name']}\n"
                    f"  Setpoint={settings['setpoint_k']:.3f}K "
                    f"(overshoot={settings['overshoot_k']:.2f}K)"
                )
            except Exception as e:
                print(f"[ERROR] Failed to apply zone settings: {e}")
                self._log_csv_point(target_k, None, None, {}, False, 0.0, "zone_error")
                continue

            # 2. Reset diagnostics + pure-P mode for this new temperature step
            self._pure_p_mode = False
            self._pure_p_p_value = settings["p"]
            if self._diagnostics:
                self._diagnostics.reset_adjustment_tracking()

            # 3. Wait for stability
            enable_diag = (settings["zone_id"] == 2)  # only 20–40K
            result = self.wait_for_stability(target_k, enable_diagnostics=enable_diag)

            step_elapsed = time.monotonic() - step_start

            # 4. Log
            param_entry = {
                "target_k": target_k,
                "actual_k": result["final_temp"],
                "setpoint_k": settings["setpoint_k"],
                "overshoot_k": settings["overshoot_k"],
                "p": settings["p"],
                "i": settings["i"],
                "d": settings["d"],
                "heater_range": settings["heater_range_name"],
                "zone_id": settings["zone_id"],
                "stable": result["stable"],
                "elapsed_s": step_elapsed,
                "heater_percent_final": result.get("heater_percent", 0.0),
                "diagnostic_events": result.get("diagnostic_events", []),
                "notes": "" if result["stable"] else result.get("reason", "unknown"),
            }
            self._optimal_params.append(param_entry)

            self._log_csv_point(
                target_k,
                result["final_temp"],
                settings,
                result["stable"],
                step_elapsed,
                result.get("reason", ""),
            )

            if result["stable"]:
                completed += 1
            else:
                print(f"  [Note] Not stable — reason: {result.get('reason')}")

        total_elapsed = time.monotonic() - ramp_start

        # Save optimal params JSON
        json_path = self.save_optimal_params(self._optimal_params)

        self._close_csv_log()

        print(f"\n{'='*60}")
        print(f"Ramp complete!")
        print(f"  Steps: {completed}/{len(targets)} stable")
        print(f"  Duration: {total_elapsed/60:.1f} min")
        print(f"  Params: {json_path}")
        print(f"  Log: {self._csv_path}")
        print(f"{'='*60}")

        return {
            "total_steps": len(targets),
            "completed_steps": completed,
            "aborted": self._aborted,
            "total_elapsed_s": total_elapsed,
            "json_path": json_path,
            "csv_path": self._csv_path,
        }

    # ==================================================================
    # JSON optimal parameters output
    # ==================================================================

    def save_optimal_params(
        self,
        results: List[Dict],
        output_dir: Optional[str] = None,
    ) -> str:
        """Save the optimal ramp parameters as JSON.

        Args:
            results: list of per-temperature-step result dicts.
            output_dir: directory to save to (default: self.log_dir).

        Returns:
            path to the saved JSON file.
        """
        directory = output_dir or self.log_dir
        os.makedirs(directory, exist_ok=True)

        # Determine start/end from results
        targets = [r["target_k"] for r in results]
        start_k = min(targets) if targets else 0.0
        end_k = max(targets) if targets else 0.0

        data = {
            "metadata": {
                "start_k": start_k,
                "end_k": end_k,
                "step_k": 2.0,
                "total_points": len(results),
                "completed_points": sum(1 for r in results if r.get("stable")),
                "date": datetime.now().isoformat(),
                "heater_range_policy": "Low + Medium only, High forbidden",
                "stability_method": self.stability_method,
                "setpoint_overshoot_range": "min=1.0K, max=5.0K",
                "pid_strategy": (
                    "≤20K: P=100,I=5,D=0,Low; "
                    "20-40K: P=100,I=3,D=0,Med; "
                    ">40K: P=150,I=0,D=0,Med"
                ),
            },
            "parameters": results,
        }

        path = os.path.join(directory, "optimal_ramp_params.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\n[OK] Optimal parameters saved: {path}")
        return path

    # ==================================================================
    # CSV logging
    # ==================================================================

    def _default_log_dir(self) -> str:
        base = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "experiment_data",
            datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        return base

    def _open_csv_log(self) -> None:
        os.makedirs(self.log_dir, exist_ok=True)
        self._csv_path = os.path.join(self.log_dir, "ramp_log.csv")
        self._csv_file = open(self._csv_path, "w", newline="", encoding="utf-8-sig")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow([
            "timestamp", "target_k", "actual_k", "setpoint_k",
            "range_name", "p", "i", "d", "range_code", "zone_id",
            "stable", "elapsed_s", "reason",
        ])

    def _log_csv_point(
        self,
        target_k: float,
        actual_k: Optional[float],
        settings: Dict,
        stable: bool,
        elapsed_s: float,
        reason: str,
    ) -> None:
        if self._csv_writer is None:
            return
        self._csv_writer.writerow([
            datetime.now().isoformat(),
            f"{target_k:.1f}",
            f"{actual_k:.3f}" if actual_k else "",
            f"{settings.get('setpoint_k', ''):.3f}" if settings else "",
            f"{settings.get('heater_range_name', '')}" if settings else "",
            f"{settings.get('p', ''):.1f}" if settings else "",
            f"{settings.get('i', ''):.1f}" if settings else "",
            f"{settings.get('d', ''):.1f}" if settings else "",
            f"{settings.get('heater_range', '')}" if settings else "",
            f"{settings.get('zone_id', '')}" if settings else "",
            "YES" if stable else "NO",
            f"{elapsed_s:.1f}",
            reason,
        ])
        self._csv_file.flush()  # type: ignore[union-attr]

    def _close_csv_log(self) -> None:
        if self._csv_file:
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None

    # ==================================================================
    # Helpers
    # ==================================================================

    def _read_temperature_with_retry(self, max_retries: int = 3) -> Optional[float]:
        """Read temperature, retrying on failure."""
        for attempt in range(max_retries):
            try:
                if self._device is None:
                    return None
                return self._device.get_temperature("A")
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2.0)
                else:
                    print(f"[Warning] Temperature read failed after {max_retries} attempts: {e}")
                    return None
        return None

    # ==================================================================
    # Cleanup
    # ==================================================================

    def close(self) -> None:
        """Close VISA session. Does NOT turn heaters off."""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None


# =========================================================================
# CLI entry point
# =========================================================================

def main() -> int:
    """CLI entry point for the standalone ramp program."""
    parser = argparse.ArgumentParser(
        description="LakeShore 335 Temperature Ramp Controller (10K→80K)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python lakeshore335_ramp.py
  python lakeshore335_ramp.py --start 20 --end 30 --hold-seconds 30
  python lakeshore335_ramp.py --address ASRL3::INSTR --max-wait 600
        """,
    )
    parser.add_argument(
        "--address", default=None,
        help=f"VISA address (default: {resource_lakeshore})",
    )
    parser.add_argument(
        "--start", type=float, default=RAMP_START_K,
        help=f"Start temperature K (default: {RAMP_START_K})",
    )
    parser.add_argument(
        "--end", type=float, default=RAMP_END_K,
        help=f"End temperature K (default: {RAMP_END_K})",
    )
    parser.add_argument(
        "--step", type=float, default=RAMP_STEP_K,
        help=f"Temperature step K (default: {RAMP_STEP_K})",
    )
    parser.add_argument(
        "--stability-method", default="custom",
        choices=["simple", "v1", "v2", "v3", "custom"],
        help="Stability check method (default: custom)",
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=10.0,
        help="Temperature poll interval (default: 10)",
    )
    parser.add_argument(
        "--hold-seconds", type=float, default=60.0,
        help="Required stable-hold duration (default: 60)",
    )
    parser.add_argument(
        "--max-wait", type=float, default=1800.0,
        help="Max wait per step in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--log-dir", default=None,
        help="Log directory (default: experiment_data/{timestamp}/)",
    )

    args = parser.parse_args()

    address = args.address or resource_lakeshore

    controller = LakeShore335RampController(
        visa_address=address,
        stability_method=args.stability_method,
        poll_seconds=args.poll_seconds,
        stable_hold_seconds=args.hold_seconds,
        max_wait_seconds=args.max_wait,
        log_dir=args.log_dir,
    )

    # --- Signal handlers ---
    def _handle_interrupt(signum, frame):
        controller.emergency_stop()
        print("\n[Interrupted] Exiting.")
        sys.exit(1)

    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_interrupt)

    # --- atexit fallback ---
    def _atexit_cleanup():
        if not controller._aborted:
            pass  # normal exit, no emergency needed

    atexit.register(_atexit_cleanup)

    # --- Run ---
    try:
        controller.connect()
        result = controller.run_ramp(
            start_k=args.start,
            end_k=args.end,
            step=args.step,
        )
        return 0 if result["completed_steps"] > 0 else 1

    except Exception as e:
        print(f"\n[FATAL] {e}")
        import traceback
        traceback.print_exc()
        controller.emergency_stop()
        return 1

    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
