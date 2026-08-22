# -*- coding: utf-8 -*-
"""
YBCO Temperature & Laser Power Sweep — Experiment Orchestration
================================================================

Automated measurement loop:
  1. Stabilise temperature (via LakeShore 335)
  2. Sweep laser power levels
  3. Capture S2P data from VNA at each (temperature, power) point
  4. Repeat for next temperature

This module is the thin orchestration layer. All instrument drivers,
stability algorithms, PID logic, and configuration live in separate
modules so that swapping an instrument only requires changing one file.

Architecture:
    config.py               → all constants
    stability_monitor.py    → AdvancedStabilityMonitor
    pid_controller.py       → SmartPIDController
    lakeshore_control.py    → LakeShore335 + duck-typing helpers
    vna_control.py          → save_s2p, try_connect
    laser_driver.py         → LaserController
"""

import os
import sys
from time import sleep, time

import pyvisa

# ---- config ----
from config import (
    resource_vna, laser_resource, resource_lakeshore,
    date, base_folder,
    power_levels_mw, temperature_levels_k,
    temperature_poll_seconds, max_wait_seconds, stable_hold_seconds,
    custom_stability_settings, setpoint_adjust_settings,
    memory_monitor_enabled, memory_warning_threshold_mb,
    memory_critical_threshold_mb, memory_check_interval_s,
)

# ---- algorithms ----
from stability_monitor import AdvancedStabilityMonitor
from pid_controller import SmartPIDController

# ---- instruments ----
from lakeshore_control import (
    LakeShore335,
    configure_lakeshore_serial,
    set_lakeshore_temperature,
    set_lakeshore_pid,
    get_lakeshore_temperature,
)
from vna_control import save_s2p, try_connect
from laser_driver import LaserController

# ---- memory monitoring ----
from memory_monitor import MemoryMonitor


# =========================================================================
# Laser power helper (experiment-specific: includes 20 s settle delay)
# =========================================================================

def set_laser_power(laser, power_mw: float, time_elapse: float = 20):
    """Set laser power and wait for the optical path to settle."""
    if power_mw == 0:
        laser.output_off()
        print("Laser output OFF for 0 mW measurement.")
        return

    laser.set_power_mw(power_mw)
    laser.output_on()
    print(f"Laser set to {power_mw} mW.")
    sleep(time_elapse)


# =========================================================================
# Temperature stabilisation (orchestration: coordinates all subsystems)
# =========================================================================

def wait_for_temperature(temp_reader, target_k: float,
                         stability_monitor=None, memory_monitor=None):
    """Wait for cryostat temperature to stabilise at ``target_k``.

    Coordinates LakeShore, stability monitor, PID controller, and
    diagnostics according to experiment-specific protocol.

    Protocol:
      - Below 20 K: setpoint = target (no overshoot)
      - Above 20 K: apply calculated overshoot, then dynamically
        adjust once rate-of-change is stable
      - Final measurement only after ``stable_hold_seconds``
        continuously within ``final_stable_band_k``

    If ``memory_monitor`` is provided, memory usage is checked
    periodically and logged.
    """
    if stability_monitor is None:
        stability_monitor = AdvancedStabilityMonitor()

    start_time = time()
    last_stable_time = None
    last_memory_check = 0.0  # 上次内存检查的时间戳
    setpoint_adjusted = False
    current_setpoint = target_k

    # ---- initial PID & setpoint ----
    pid_params = SmartPIDController.get_params_for_temperature(target_k)
    set_lakeshore_pid(temp_reader, pid_params["p"], pid_params["i"], pid_params["d"])
    print(f"Set PID: P={pid_params['p']:.1f}, I={pid_params['i']:.1f}, D={pid_params['d']:.1f}")

    if target_k < setpoint_adjust_settings["low_temp_threshold"]:
        current_setpoint = target_k
        print(f"Target < 20K, setpoint = target: {current_setpoint:.3f} K")
    else:
        current_k = get_lakeshore_temperature(temp_reader) or target_k
        current_setpoint = SmartPIDController.calculate_adjusted_setpoint(
            target_k, current_k)
        print(f"Set adjusted setpoint: {current_setpoint:.3f} K "
              f"(target: {target_k:.3f} K)")

    set_lakeshore_temperature(temp_reader, current_setpoint)

    # ---- stabilisation loop ----
    while True:
        current_k = get_lakeshore_temperature(temp_reader)

        if current_k is None:
            print("[Warning] Could not read temperature")
            sleep(temperature_poll_seconds)
            continue

        stability_monitor.add_reading(current_k, target_k)
        elapsed = time() - start_time
        error_k = abs(current_k - target_k)

        # stability check
        stability_result = stability_monitor.check_stability(
            target_k, method="custom")

        print(
            f"Target {target_k:.3f}K | SP {current_setpoint:.3f}K | "
            f"Current {current_k:.3f}K | Error {error_k:.3f}K | "
            f"Elapsed {elapsed / 60:.1f}min | "
            f"Stable: {'YES' if stability_result['stable'] else 'NO'}"
        )

        if stability_result.get("minute_windows"):
            for i, window in enumerate(stability_result["minute_windows"][:2]):
                time_label = "Now" if i == 0 else "1min ago"
                print(f"  Avg[{time_label}]: {window['avg']:.3f}K "
                      f"({window['count']} readings)")

        # dynamic setpoint adjustment (20K+ only, once per target)
        if (target_k >= setpoint_adjust_settings["low_temp_threshold"]
                and not setpoint_adjusted):
            if (stability_result.get("ready_for_adjust")
                    and stability_result.get("avg_temp")):
                avg_temp = stability_result["avg_temp"]
                temp_error = target_k - avg_temp

                if abs(temp_error) > 0.3:
                    adjustment = temp_error
                    new_setpoint = current_setpoint + adjustment

                    print(f"\n[Setpoint Adjustment] "
                          f"Current avg: {avg_temp:.3f}K, "
                          f"Error: {temp_error:+.3f}K")
                    print(f"                    "
                          f"Old setpoint: {current_setpoint:.3f}K → "
                          f"New: {new_setpoint:.3f}K")
                    current_setpoint = new_setpoint
                    set_lakeshore_temperature(temp_reader, current_setpoint)
                    setpoint_adjusted = True

        # final stability hold
        if stability_result["stable"]:
            if last_stable_time is None:
                last_stable_time = time()
                print(f"Temperature within final band "
                      f"±{custom_stability_settings['final_stable_band_k']}K, "
                      f"holding...")

            hold_time = time() - last_stable_time
            if hold_time >= stable_hold_seconds:
                print(f"Temperature stable for {hold_time:.1f}s. "
                      f"Starting measurement.")
                return current_k
        else:
            last_stable_time = None

        if elapsed >= max_wait_seconds:
            print(f"[Warning] Max wait reached at target {target_k:.3f}K. "
                  f"Continuing.")
            return current_k

        # ---- periodic memory check ----
        if memory_monitor and memory_monitor_enabled:
            now = time()
            if now - last_memory_check >= memory_check_interval_s:
                info = memory_monitor.check()
                print(memory_monitor.format_info(info))
                if info.warning:
                    print(memory_monitor.format_warning(info))
                last_memory_check = now

        sleep(temperature_poll_seconds)


# =========================================================================
# Main experiment loop
# =========================================================================

def main():
    print("=" * 60)
    print("YBCO Temperature & Laser Power Sweep")
    print("=" * 60)
    print(f"Temperature range: {min(temperature_levels_k)} – "
          f"{max(temperature_levels_k)} K")
    print(f"Power levels: {power_levels_mw} mW")
    print(f"Stability method: custom")
    print(f"Custom criteria:")
    print(f"  • 1-min average within "
          f"±{custom_stability_settings['avg_tolerance_k']}K")
    print(f"  • Avg delta between minutes "
          f"< {custom_stability_settings['delta_tolerance_k']}K")
    print(f"  • Final band: "
          f"±{custom_stability_settings['final_stable_band_k']}K")
    print("=" * 60)

    # ensure output directory exists
    os.makedirs(base_folder, exist_ok=True)

    # ---- connect VNA ----
    vna = None
    vna_result = try_connect(resource_vna, "main")
    if vna_result[0] and not vna_result[1]:
        vna = vna_result[0]
    else:
        print("[Warning] Continuing without VNA")

    # ---- connect laser ----
    laser = None
    try:
        laser = LaserController(laser_resource)
        if not laser.connect_with_retry(max_attempts=5, base_delay_s=3.0):
            laser = None
            print("[Warning] Laser connection failed, continuing without laser")
    except Exception as e:
        print(f"[Warning] Laser connection error: {e}")
        print("[Warning] Continuing without laser")
        laser = None

    # ---- connect LakeShore ----
    temp_reader = LakeShore335(visa_address=resource_lakeshore)
    configure_lakeshore_serial(temp_reader)

    try:
        if hasattr(temp_reader, "read_all_status"):
            print("\n" + "=" * 60)
            print("[DEBUG] Reading complete Lakeshore 335 status...")
            temp_reader.read_all_status()
        print(f"[OK] LakeShore335 initial temperature: "
              f"{temp_reader.get_temperature()} K")
    except Exception as e:
        print(f"[Warning] Initial temperature read failed: {e}")

    # ---- sweep ----
    count = 0
    stability_monitor = AdvancedStabilityMonitor()

    # ---- memory monitor ----
    mem_monitor = None
    if memory_monitor_enabled:
        mem_monitor = MemoryMonitor(
            warning_threshold_mb=memory_warning_threshold_mb,
            critical_threshold_mb=memory_critical_threshold_mb,
        )
        info = mem_monitor.check()
        print(mem_monitor.format_info(info))
        print(f"Memory monitoring enabled. "
              f"Warning: <{memory_warning_threshold_mb}MB, "
              f"Critical: <{memory_critical_threshold_mb}MB")

    try:
        for target_temp_k in temperature_levels_k:
            print(f"\n>>> Preparing measurement at {target_temp_k} K")

            current_temp_k = wait_for_temperature(
                temp_reader, target_temp_k, stability_monitor, mem_monitor)

            target_temp_str = f"{target_temp_k:g}K"

            for power_mw in power_levels_mw:
                print(f"\n>>> Measuring {power_mw} mW at target "
                      f"{target_temp_str}")

                if laser:
                    set_laser_power(laser, power_mw, 20)
                else:
                    print("[Info] Laser not connected, skipping power setting")

                power_str = f"{power_mw:02d}"
                folder_data = os.path.join(
                    base_folder,
                    target_temp_str,
                    f"actual_{current_temp_k:.3f}K",
                    f"{power_str}mW",
                )
                os.makedirs(folder_data, exist_ok=True)

                filename = (
                    f"YBCO_{power_str}mW_target_{target_temp_str}"
                    f"_actual_{current_temp_k:.3f}K.s2p"
                )
                pc_filename = os.path.join(folder_data, filename)

                if vna:
                    save_s2p(vna, pc_filename)
                    count += 1
                else:
                    print("[Info] VNA not connected, skipping measurement")
                    count += 1

            print(f">>> Completed all power measurements at {target_temp_k} K")
            if laser:
                laser.output_off()
                print("Turning laser OFF")
            else:
                print("[Info] Laser not connected")

    except Exception as main_err:
        print(f"\n[System Error] Runtime exception: {main_err}")

    finally:
        print("\n" + "=" * 40)
        print("Safely closing devices...")
        print("=" * 40)
        try:
            if laser:
                laser.output_off()
                laser.close()
        except Exception as e:
            print(f"Resource close warning (laser): {e}")
        try:
            if vna:
                vna.close()
        except Exception as e:
            print(f"Resource close warning (VNA): {e}")
        print(f"Temperature sweep completed. {count} measurements taken.")

        # ---- memory summary ----
        if mem_monitor:
            print()
            print(mem_monitor.summary())


if __name__ == "__main__":
    main()
