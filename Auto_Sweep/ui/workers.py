# -*- coding: utf-8 -*-
"""
Background worker objects for instrument VISA communication.

Each worker lives on its own QThread.  All VISA calls happen off the
UI thread so the GUI never freezes.  Results are delivered back via
pyqtSignals.

Pattern based on DeviceWorker in Lakeshore335_output.py.
"""

from dataclasses import dataclass
from typing import Optional
import json
import os
import sys

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

# app_settings 模块在 Auto_Sweep/ 根下（ui/ 是子目录）；补路径以便 import。
_AUTOSWEEP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTOSWEEP_DIR not in sys.path:
    sys.path.insert(0, _AUTOSWEEP_DIR)

import app_settings

from memory_monitor import MemoryMonitor

import gc as _gc  # 实验结束后强制回收循环引用


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class LakeShoreReading:
    temperature_a: Optional[float] = None
    temperature_b: Optional[float] = None
    heater_1: Optional[float] = None
    heater_2: Optional[float] = None
    setpoint_1: Optional[float] = None
    setpoint_2: Optional[float] = None
    range_1: Optional[int] = None
    range_2: Optional[int] = None
    pid_p1: Optional[float] = None
    pid_i1: Optional[float] = None
    pid_d1: Optional[float] = None
    pid_p2: Optional[float] = None
    pid_i2: Optional[float] = None
    pid_d2: Optional[float] = None


# ---------------------------------------------------------------------------
# 超时软化 + 连续回退状态机（需求 A/B/C）
# ---------------------------------------------------------------------------

class TimeoutRollbackState:
    """追踪连续温度点稳定性问题，决策何时回退。

    需求 A: 超时后 |avg−target| ≤ soft_pass_band_k → "soft_pass"，否则 "hard_fail"
    需求 B: 连续 N 次问题 → 回退到第一个问题温度点，加时重试
    需求 C: 4K 目标跳过温度范围检定

    Attributes:
        temp_list: 温度点列表 (K)
        soft_pass_band_k: 超时软通过带 (K)
        consecutive_threshold: 连续问题阈值
        skip_validation_temp_k: 跳过检定的特殊温度 (K)
        rollback_max_wait_increase_s: 每次回退 max_wait 增量 (秒)
        rollback_pre_wait_increase_s: 每次回退 pre_wait 增量 (秒)
        consecutive_issues: 当前连续问题计数
        first_issue_index: 第一个问题温度在 temp_list 中的索引
        rollback_count: 已回退次数
        current_max_wait_increase_s: 累积 max_wait 增量
        current_pre_wait_increase_s: 累积 pre_wait 增量
    """

    def __init__(self, temp_list, *,
                 soft_pass_band_k=2.0,
                 consecutive_threshold=2,
                 skip_validation_temp_k=4.0,
                 rollback_max_wait_increase_s=1800,
                 rollback_pre_wait_increase_s=600):
        self.temp_list = list(temp_list)
        self.soft_pass_band_k = soft_pass_band_k
        self.consecutive_threshold = consecutive_threshold
        self.skip_validation_temp_k = skip_validation_temp_k
        self.rollback_max_wait_increase_s = rollback_max_wait_increase_s
        self.rollback_pre_wait_increase_s = rollback_pre_wait_increase_s

        # 运行时状态
        self.consecutive_issues = 0
        self.first_issue_index = None
        self.rollback_count = 0
        self.current_max_wait_increase_s = 0
        self.current_pre_wait_increase_s = 0

    # ---- 需求 A: 超时分类 ----

    def classify_timeout(self, avg_temp, target_k):
        """将超时结果分类为 'soft_pass' 或 'hard_fail'。

        需求 A: |avg−target| ≤ band → soft_pass，否则 hard_fail。
        需求 C: 4K 目标始终视为 soft_pass（液氦制冷达不到精确 4K）。
        """
        # 4K 豁免：始终软通过
        if self.is_skip_validation_temp(target_k):
            return "soft_pass"

        delta = abs(avg_temp - target_k)
        if delta <= self.soft_pass_band_k:
            return "soft_pass"
        return "hard_fail"

    # ---- 需求 B: 连续追踪与回退决策 ----

    def record_result(self, temp_index, result_type):
        """记录一个温度点的稳定性结果。

        Args:
            temp_index: 当前温度在 temp_list 中的索引
            result_type: "stable" | "good_enough" | "soft_pass" | "hard_fail" | "meltdown_skip"

        Returns:
            (should_rollback: bool, first_issue_index: int or None)
        """
        is_issue = result_type in ("soft_pass", "hard_fail", "meltdown_skip")

        if is_issue:
            if self.consecutive_issues == 0:
                # 新问题链开始
                self.first_issue_index = temp_index
            self.consecutive_issues += 1

            if self.consecutive_issues >= self.consecutive_threshold:
                # 触发回退
                self.rollback_count += 1
                self.current_max_wait_increase_s += self.rollback_max_wait_increase_s
                self.current_pre_wait_increase_s += self.rollback_pre_wait_increase_s
                first = self.first_issue_index
                return True, first
        else:
            # 正常稳定 → 重置
            self.consecutive_issues = 0
            self.first_issue_index = None

        return False, None

    def reset_after_rollback(self):
        """回退后重置连续计数（保留累加时间）。"""
        self.consecutive_issues = 0
        self.first_issue_index = None

    def get_rollback_params(self):
        """获取回退参数: (max_wait_increase_s, pre_wait_increase_s)。"""
        return (self.current_max_wait_increase_s,
                self.current_pre_wait_increase_s)

    # ---- 需求 C: 4K 豁免 ----

    def is_skip_validation_temp(self, target_k):
        """检查目标温度是否为应跳过检定的特殊温度（4K）。

        使用浮点容差 0.1K 判断。
        """
        return abs(target_k - self.skip_validation_temp_k) < 0.1


# ---------------------------------------------------------------------------
# LaserWorker
# ---------------------------------------------------------------------------

class LaserWorker(QObject):
    connected = pyqtSignal(str)       # identity string
    disconnected = pyqtSignal()
    status_updated = pyqtSignal(dict) # full status dict
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, resource_manager=None):
        super().__init__()
        self._laser = None
        self._resource_manager = resource_manager

    @pyqtSlot(str)
    def connect_device(self, address: str):
        try:
            from laser_driver import LaserController
            self._laser = LaserController(address)
            ok = self._laser.connect()
            if ok:
                status = self._laser.get_status()
                self.connected.emit(self._laser.inst.query("*IDN?").strip() if self._laser.inst else "Laser")
                self.status_updated.emit(status)
                self.log.emit(f"Laser connected: {address}")
            else:
                self.error.emit(f"Laser connection failed: {address}")
        except Exception as e:
            self._laser = None
            self.error.emit(f"Laser connection error: {e}")

    @pyqtSlot()
    def disconnect_device(self):
        if self._laser:
            try:
                self._laser.disconnect()
            except Exception as e:
                self.error.emit(f"Laser disconnect error: {e}")
        self._laser = None
        self.disconnected.emit()
        self.log.emit("Laser disconnected")

    @pyqtSlot(float)
    def set_power(self, power_mw: float):
        if not self._laser:
            self.error.emit("Laser not connected")
            return
        try:
            self._laser.set_power(power_mw)
            self.status_updated.emit(self._laser.get_status())
            self.log.emit(f"Laser power → {power_mw} mW")
        except Exception as e:
            self.error.emit(f"Laser set_power error: {e}")

    @pyqtSlot(float)
    def set_wavelength(self, nm: float):
        if not self._laser:
            self.error.emit("Laser not connected")
            return
        try:
            self._laser.set_wavelength(nm)
            self.status_updated.emit(self._laser.get_status())
            self.log.emit(f"Laser wavelength → {nm} nm")
        except Exception as e:
            self.error.emit(f"Laser set_wavelength error: {e}")

    @pyqtSlot()
    def output_on(self):
        if self._laser:
            self._laser.output_on()
            self.status_updated.emit(self._laser.get_status())

    @pyqtSlot()
    def output_off(self):
        if self._laser:
            self._laser.output_off()
            self.status_updated.emit(self._laser.get_status())

    @pyqtSlot()
    def physical_off(self):
        if self._laser:
            self._laser.physical_off()
            self.status_updated.emit(self._laser.get_status())
            self.log.emit("Laser physically OFF")

    @pyqtSlot()
    def refresh_status(self):
        if self._laser:
            try:
                self.status_updated.emit(self._laser.get_status())
            except Exception as e:
                self.error.emit(f"Laser status read error: {e}")


# ---------------------------------------------------------------------------
# LakeShoreWorker
# ---------------------------------------------------------------------------

class LakeShoreWorker(QObject):
    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    reading = pyqtSignal(object)     # LakeShoreReading
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, resource_manager=None):
        super().__init__()
        self._controller = None
        self._resource_manager = resource_manager

    @pyqtSlot(str)
    def connect_device(self, address: str):
        try:
            from lakeshore_control import LakeShore335
            self._controller = LakeShore335(address)
            self.connected.emit(self._controller.identity)
            self.log.emit(f"LakeShore connected: {self._controller.identity}")
        except Exception as e:
            self._controller = None
            self.error.emit(f"LakeShore connection failed: {e}")

    @pyqtSlot()
    def disconnect_device(self):
        if self._controller:
            try:
                self._controller.close()
            except Exception as e:
                self.error.emit(f"LakeShore disconnect error: {e}")
        self._controller = None
        self.disconnected.emit()
        self.log.emit("LakeShore disconnected")

    @pyqtSlot()
    def poll(self):
        """Read all live values. Called by a 1-second QTimer."""
        if self._controller is None:
            return
        try:
            pid1 = self._controller.get_pid(1)
            pid2 = self._controller.get_pid(2)
            r = LakeShoreReading(
                temperature_a=self._controller.get_temperature("A"),
                temperature_b=self._controller.get_temperature("B"),
                heater_1=self._controller.get_heater_percent(1),
                heater_2=self._controller.get_heater_percent(2),
                setpoint_1=self._controller.get_setpoint(1),
                setpoint_2=self._controller.get_setpoint(2),
                range_1=self._controller.get_heater_range(1),
                range_2=self._controller.get_heater_range(2),
                pid_p1=pid1[0], pid_i1=pid1[1], pid_d1=pid1[2],
                pid_p2=pid2[0], pid_i2=pid2[1], pid_d2=pid2[2],
            )
            self.reading.emit(r)
        except Exception as e:
            self.error.emit(f"LakeShore poll error: {e}")

    @pyqtSlot(int, float)
    def set_setpoint(self, loop: int, kelvin: float):
        """Set temperature setpoint with cooling-safety interlock.

        Four temperature parameters (see CLAUDE.md):
          - actual_temp    = ``KRDG? A`` (or B) — real sample temperature
          - target_sp      = value written to ``SETP {loop},{kelvin}``
          - (ramp rate means the internal setpoint ramps slowly toward target_sp)

        Safety rule (one-directional — only intervenes when "too hot"):
          If actual_temp > target_sp + 20 K → heater OFF,
          poll until actual_temp - target_sp < 20 K,
          then heater → Medium, then write SETP.
        """
        if not self._controller:
            self.error.emit("LakeShore not connected")
            return
        try:
            channel = "A" if loop == 1 else "B"
            actual = self._controller.get_temperature(channel)

            if actual > kelvin + 20.0:
                diff = actual - kelvin
                self.log.emit(
                    f"Loop {loop}: actual ({actual:.2f} K) is "
                    f"{diff:.1f} K above target ({kelvin:.2f} K). "
                    f"Turning heater OFF for cooldown safety..."
                )
                self._controller.set_heater_range(loop, 0)  # OFF

                import time
                timeout_s = 600  # 10-minute safety timeout
                started = time.time()
                while True:
                    if time.time() - started > timeout_s:
                        self.error.emit(
                            f"Loop {loop}: cooldown safety timeout "
                            f"(waited {timeout_s}s, actual still "
                            f"{actual:.2f} K, target {kelvin:.2f} K)")
                        return
                    time.sleep(2)
                    try:
                        actual = self._controller.get_temperature(channel)
                    except Exception:
                        continue
                    if actual - kelvin < 20.0:
                        break

                self.log.emit(
                    f"Loop {loop}: actual ({actual:.2f} K) now within "
                    f"20 K of target ({kelvin:.2f} K). "
                    f"Setting heater to Medium."
                )
                self._controller.set_heater_range(loop, 2)  # Medium

            self._controller.set_temperature(kelvin, loop)
            self.log.emit(f"Loop {loop} setpoint → {kelvin:.4f} K")
        except Exception as e:
            self.error.emit(f"LakeShore setpoint error: {e}")

    @pyqtSlot(int, int)
    def set_heater_range(self, output: int, range_level: int):
        """设置加热器档位。一律强制使用 Medium (2)。"""
        if not self._controller:
            self.error.emit("LakeShore not connected")
            return
        try:
            # 一律使用 Medium (2)，忽略传入的 range_level
            self._controller.set_heater_range(output, 2)
            self.log.emit(f"Output {output} heater range → Medium (2)")
        except Exception as e:
            self.error.emit(f"LakeShore heater range error: {e}")

    @pyqtSlot(int, float, float, float)
    def set_pid(self, loop: int, p: float, i: float, d: float):
        if not self._controller:
            self.error.emit("LakeShore not connected")
            return
        try:
            self._controller.set_pid(p, i, d, loop)
            self.log.emit(f"Loop {loop} PID → P={p:g}, I={i:g}, D={d:g}")
        except Exception as e:
            self.error.emit(f"LakeShore PID error: {e}")

    @pyqtSlot(int)
    def read_pid(self, loop: int):
        if not self._controller:
            self.error.emit("LakeShore not connected")
            return
        try:
            p, i, d = self._controller.get_pid(loop)
            self.log.emit(f"Loop {loop} PID readback: P={p:g}, I={i:g}, D={d:g}")
        except Exception as e:
            self.error.emit(f"LakeShore PID read error: {e}")

    @pyqtSlot()
    def all_heaters_off(self):
        if not self._controller:
            self.error.emit("LakeShore not connected")
            return
        try:
            self._controller.all_heaters_off()
            self.log.emit("ALL HEATERS OFF")
        except Exception as e:
            self.error.emit(f"LakeShore all-off error: {e}")


# ---------------------------------------------------------------------------
# VNAWorker (Keysight P5003A via HiSLIP)
# ---------------------------------------------------------------------------

class VNAWorker(QObject):
    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    settings_applied = pyqtSignal(dict)   # echo of applied settings
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, resource_manager=None):
        super().__init__()
        self._vna = None
        self._rm = resource_manager  # use shared ResourceManager (or create own)

    # ---- connection -------------------------------------------------

    @pyqtSlot(str)
    def connect_device(self, address: str):
        try:
            import pyvisa
            if self._rm is None:
                self._rm = pyvisa.ResourceManager("visa32.dll")
            self._vna = self._rm.open_resource(address)
            self._vna.timeout = 120000
            idn = self._vna.query("*IDN?").strip()
            self.connected.emit(idn)
            self.log.emit(f"VNA connected: {idn}")
        except Exception as e:
            self._vna = None
            self._rm = None
            self.error.emit(f"VNA connection failed: {e}")

    @pyqtSlot()
    def disconnect_device(self):
        if self._vna:
            try:
                self._vna.close()
            except Exception:
                pass
        if self._rm:
            try:
                self._rm.close()
            except Exception:
                pass
        self._vna = None
        self._rm = None
        self.disconnected.emit()
        self.log.emit("VNA disconnected")

    # ---- all-settings apply -----------------------------------------

    @pyqtSlot(dict)
    def apply_settings(self, settings: dict):
        """Apply all VNA settings from a dict.

        Keys: start_freq_hz, stop_freq_hz, s_parameter, power_dbm,
              points, if_bandwidth_hz

        Sends :ABORt and :INIT:CONT OFF first to avoid blocking
        when VNA is in continuous sweep mode.
        """
        if not self._vna:
            self.error.emit("VNA not connected")
            return
        try:
            # ---- 安全前置：停止扫描，避免后续命令阻塞 ----
            self._vna.write(":ABORt")
            self._vna.write(":INITiate:CONTinuous OFF")

            # frequency range — set start then stop
            if "start_freq_hz" in settings:
                self._vna.write(f":SENSe:FREQuency:STARt {settings['start_freq_hz']:.0f}")
            if "stop_freq_hz" in settings:
                self._vna.write(f":SENSe:FREQuency:STOP {settings['stop_freq_hz']:.0f}")

            # S-parameter
            if "s_parameter" in settings:
                sp = settings["s_parameter"]
                self._vna.write(f':CALCulate:PARameter:DELete:ALL')
                self._vna.write(f':CALCulate:PARameter:DEFine:EXTended "{sp}","{sp}"')
                self._vna.write(f':DISPlay:WINDow1:TRACe1:FEED "{sp}"')

            # power
            if "power_dbm" in settings:
                pwr = settings["power_dbm"]
                if isinstance(pwr, list):
                    pwr = pwr[0] if pwr else -45
                self._vna.write(f":SOURce:POWer {pwr}")

            # points
            if "points" in settings:
                self._vna.write(f":SENSe:SWEep:POINts {settings['points']}")

            # IF bandwidth
            if "if_bandwidth_hz" in settings:
                self._vna.write(f":SENSe:BANDwidth {settings['if_bandwidth_hz']}")

            # ---- 恢复连续扫描，使 VNA 屏幕显示 S 参数曲线 ----
            # :ABORt + :INIT:CONT OFF 确保了参数写入时不阻塞，
            # 写入完成后重新开启连续模式恢复实时曲线显示。
            self._vna.write(":INITiate:CONTinuous ON")

            self.settings_applied.emit(settings)
            self.log.emit(f"VNA settings applied: "
                          f"{settings.get('start_freq_hz',0)/1e9:.1f}–"
                          f"{settings.get('stop_freq_hz',0)/1e9:.1f} GHz, "
                          f"{settings.get('s_parameter','?')}, "
                          f"{settings.get('points','?')} pts, "
                          f"IFBW {settings.get('if_bandwidth_hz','?')} Hz")
        except Exception as e:
            self.error.emit(f"VNA apply_settings error: {e}")

    # ---- single sweep + save S2P -----------------------------------

    @pyqtSlot(str)
    def single_sweep(self, save_path: str):
        """Trigger a single sweep and save the result as .s2p."""
        if not self._vna:
            self.error.emit("VNA not connected")
            return
        try:
            from time import sleep
            vna_safe_path = save_path.replace("\\", "/")
            self._vna.write(":INITiate:CONTinuous OFF")
            self._vna.write(":INITiate:IMMediate")
            try:
                self._vna.query("*OPC?")
            except Exception:
                sleep(5)

            self._vna.write(f'MMEMory:STORe "{vna_safe_path}"')
            try:
                msg = self._vna.query(":SYSTem:ERRor?").strip()
                self.log.emit(f"VNA sweep saved: {vna_safe_path}  [{msg}]")
            except Exception:
                self.log.emit(f"VNA sweep saved: {vna_safe_path}")
        except Exception as e:
            self.error.emit(f"VNA sweep error: {e}")


# ---------------------------------------------------------------------------
# CheckpointManager — 实验断点续传
# ---------------------------------------------------------------------------

class CheckpointManager:
    """检查点文件的原子读写与恢复判断。

    检查点保存到实验输出目录的 ``checkpoint.json``。
    写入使用 .tmp + os.rename 保证原子性，崩溃不会产生损坏文件。

    所有方法均为静态方法，无状态 — 可被 ExperimentWorker 直接调用。
    """

    CHECKPOINT_VERSION = 1
    CHECKPOINT_FILENAME = "checkpoint.json"

    # ---- 保存 & 加载 ----

    @staticmethod
    def save(output_dir: str, state: dict, completed_points: list,
             experiment_id: str,
             original_temp_list: list, original_vna_power_list: list,
             original_power_list: list) -> None:
        """原子写入检查点文件。

        Args:
            output_dir: 实验输出根目录
            state: 当前运行状态字典 (temp_idx, overshoot_learning, 等)
            completed_points: 已完成测量点列表
            experiment_id: 实验标识 (YYYYMMDD_HHMMSS)
            original_temp_list: 实验开始时的温度列表（用于恢复验证）
            original_vna_power_list: 实验开始时的 VNA 功率列表
            original_power_list: 实验开始时的激光功率列表
        """
        import json as _json
        import os as _os
        import time as _time

        # 将原始列表注入 state，使 validate_lists 可通过 load() 返回值访问
        state = dict(state)
        state["original_temp_list"] = list(original_temp_list)
        state["original_vna_power_list"] = list(original_vna_power_list)
        state["original_power_list"] = list(original_power_list)

        checkpoint = {
            "version": CheckpointManager.CHECKPOINT_VERSION,
            "experiment_id": experiment_id,
            "timestamp": _time.strftime(
                "%Y-%m-%dT%H:%M:%S", _time.localtime(_time.time())),
            "original_temp_list": list(original_temp_list),
            "original_vna_power_list": list(original_vna_power_list),
            "original_power_list": list(original_power_list),
            "state": state,
            "completed_points": completed_points,
        }

        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        tmp_path = ckpt_path + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(checkpoint, f, indent=2, ensure_ascii=False)

        # 原子 rename（Windows 上如果目标存在会先删除）
        _os.replace(tmp_path, ckpt_path)

    @staticmethod
    def load(output_dir: str):
        """加载检查点文件。

        Returns:
            (state_dict, completed_points_list) 或 None（文件不存在/损坏）
        """
        import json as _json
        import os as _os

        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        if not _os.path.exists(ckpt_path):
            return None

        # 也检查 .tmp 残留（上次写入崩溃）
        tmp_path = ckpt_path + ".tmp"
        if _os.path.exists(tmp_path):
            # .tmp 存在但 .json 也存在 → 上次 rename 可能失败
            # 如果 .json 更新时间 >= .tmp 更新时间 → 使用 .json
            # 否则忽略 .tmp（不完整写入）
            pass  # .json 存在即可，忽略 .tmp

        try:
            with open(ckpt_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (_json.JSONDecodeError, OSError, IOError):
            return None

        # 基本结构验证
        if not isinstance(data, dict):
            return None
        if "state" not in data or "completed_points" not in data:
            return None

        state = dict(data["state"])
        # 注入 experiment_id 供恢复询问使用（保持两元组返回兼容）
        state["experiment_id"] = data.get("experiment_id")
        return (state, data["completed_points"])

    @staticmethod
    def append_point(output_dir: str, point: dict) -> None:
        """增量追加一个已完成测量点到检查点文件。

        如果检查点不存在，不执行任何操作（非致命）。
        """
        import json as _json
        import os as _os
        import time as _time

        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        if not _os.path.exists(ckpt_path):
            return

        tmp_path = ckpt_path + ".tmp"
        try:
            with open(ckpt_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (_json.JSONDecodeError, OSError, IOError):
            return

        data.setdefault("completed_points", []).append(point)
        data["timestamp"] = _time.strftime(
            "%Y-%m-%dT%H:%M:%S", _time.localtime(_time.time()))

        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
        _os.replace(tmp_path, ckpt_path)

    @staticmethod
    def delete(output_dir: str) -> None:
        """删除检查点文件（实验正常完成时调用）。"""
        import os as _os
        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        tmp_path = ckpt_path + ".tmp"
        for path in (ckpt_path, tmp_path):
            if _os.path.exists(path):
                try:
                    _os.remove(path)
                except OSError:
                    pass

    # ---- 恢复判断 ----

    @staticmethod
    def resume_from(completed_points: list, temp_list: list,
                    vna_power_list: list, power_list: list):
        """根据已完成测量点确定恢复起点。

        completed_points 是权威数据源 — 扫描所有 (temp, vna, power)
        组合，返回第一个未完成点的索引。

        Args:
            completed_points: 已完成测量点列表
            temp_list: 温度列表
            vna_power_list: VNA 功率列表
            power_list: 激光功率列表

        Returns:
            (temp_idx, vna_idx, power_idx) 或 None（全部完成）
        """
        # 构建已完成集合: {(temp_k, vna_dbm, power_mw), ...}
        done = set()
        for pt in completed_points:
            done.add((
                pt.get("temp_k"),
                pt.get("vna_dbm"),
                pt.get("power_mw"),
            ))

        for ti, temp_k in enumerate(temp_list):
            for vi, vna_dbm in enumerate(vna_power_list):
                for pi, power_mw in enumerate(power_list):
                    if (temp_k, vna_dbm, power_mw) not in done:
                        return (ti, vi, pi)

        return None  # 全部完成

    # ---- 参数验证 ----

    @staticmethod
    def validate_lists(loaded_state: dict, current_temp_list: list,
                       current_vna_power_list: list,
                       current_power_list: list) -> bool:
        """检查当前参数列表是否与保存时一致。

        Args:
            loaded_state: load() 返回的 state 字典（含 original_temp_list 等）
            current_temp_list: 当前配置的温度列表
            current_vna_power_list: 当前配置的 VNA 功率列表
            current_power_list: 当前配置的激光功率列表

        Returns:
            True 如果一致，False 如果有变更
        """
        orig_temp = loaded_state.get("original_temp_list")
        orig_vna = loaded_state.get("original_vna_power_list")
        orig_power = loaded_state.get("original_power_list")

        if orig_temp is None or orig_vna is None or orig_power is None:
            # 旧版本检查点可能没有这些字段 → 保守拒绝
            return False

        return (list(orig_temp) == list(current_temp_list) and
                list(orig_vna) == list(current_vna_power_list) and
                list(orig_power) == list(current_power_list))


# ---------------------------------------------------------------------------
# ExperimentWorker — full YBCO temperature + laser power sweep
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Overshoot 学习增量持久化（模块级辅助函数）
# ---------------------------------------------------------------------------

def _save_overshoot_learning_incremental(learning: dict,
                                         caller_file: str,
                                         log_fn=None) -> None:
    """增量保存 overshoot 学习数据到**机器层**设置文件（原子写入）。

    写 app_settings.machine.json（不入库），不写 app_settings.json：
    后者是入库文件，被自动保存改写会长期污染工作区。
    """
    if not learning:
        return
    try:
        _settings_path = str(app_settings.settings_path())
        _settings = app_settings.load_settings()
        _settings["overshoot_learning"] = {
            str(k): v for k, v in learning.items()}
        _tmp_path = _settings_path + ".tmp"
        with open(_tmp_path, "w", encoding="utf-8") as _f:
            json.dump(_settings, _f, indent=2, ensure_ascii=False)
        os.replace(_tmp_path, _settings_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ExperimentWorker — full YBCO temperature + laser power sweep
# ---------------------------------------------------------------------------

class ExperimentWorker(QObject):
    """Runs the temperature-and-power sweep experiment on a background thread.

    Uses direct controller references (not signals) for synchronous flow:
    stabilise temperature → sweep power levels → save S2P → next temp.

    Signals report progress back to the UI thread.
    """

    experiment_started = pyqtSignal()
    progress = pyqtSignal(str)
    temperature_stabilizing = pyqtSignal(float, float)       # target_k, actual_k
    temperature_stable = pyqtSignal(float, float)             # target_k, actual_k
    measurement_started = pyqtSignal(float, float)            # temp_k, power_mw
    measurement_complete = pyqtSignal(float, float, str)      # temp_k, power_mw, filepath
    experiment_finished = pyqtSignal(int)                     # total count
    experiment_error = pyqtSignal(str)
    experiment_aborted = pyqtSignal()
    memory_critical = pyqtSignal(str)                        # 内存严重不足弹窗告警

    # ---- 断点续传信号 ----
    experiment_recovering = pyqtSignal(str)           # 连接丢失，进入重连
    experiment_recovered = pyqtSignal()               # 重连成功
    experiment_recovery_timeout = pyqtSignal()        # 重连超时
    experiment_resume_prompt = pyqtSignal(str, int)   # 恢复询问 (exp_id, completed_n)

    def __init__(self):
        super().__init__()
        self._abort_flag = False
        self._lakeshore_ctrl = None
        self._laser_ctrl = None
        self._vna = None
        self._temp_list = []
        self._power_list = []
        self._vna_power_list = []
        self._output_dir = ""
        self._vna_settings = {}
        # Claude 主动监控
        self._status_writer = None
        self._meltdown_threshold_k = 0.25  # 默认值，configure 中覆盖
        self._drift_meltdown_count = 0        # 当前温度点漂移熔断计数
        self._settling_multiplier = 1.0       # 沉降倍率（1.0 = 默认）
        self._in_retry_mode = False           # 是否处于复测模式

    def configure(
        self,
        *,
        lakeshore_ctrl=None,
        laser_ctrl=None,
        vna_resource=None,
        temp_list=None,
        power_list=None,
        vna_power_list=None,
        output_dir="",
        vna_settings=None,
        pre_measurement_wait_s=0,
        max_wait_s=None,
    ):
        self._lakeshore_ctrl = lakeshore_ctrl
        self._laser_ctrl = laser_ctrl
        self._vna = vna_resource
        self._temp_list = list(temp_list or [])
        self._power_list = list(power_list or [])
        self._vna_power_list = list(vna_power_list or [])
        self._output_dir = output_dir
        self._vna_settings = dict(vna_settings or {})
        self._abort_flag = False
        self._pre_measurement_wait_s = pre_measurement_wait_s
        self._laser_was_off = True   # Fix 3: 初始状态激光关闭
        import config
        self._max_wait_s = max_wait_s if max_wait_s is not None else config.max_wait_seconds
        self._meltdown_threshold_k = getattr(
            config, "inter_measurement_max_delta_k", 0.25)
        # 自适应熔断实例变量（每个温度点重置）
        self._drift_meltdown_count = 0
        self._settling_multiplier = 1.0
        self._in_retry_mode = False

    @pyqtSlot()
    def abort(self):
        self._abort_flag = True
        self.progress.emit("Abort requested — finishing current step...")

    def _read_temperature_safe(self) -> Optional[float]:
        """安全读取 LakeShore 温度，失败返回 None（不抛异常）。

        用于快速预检等场景，调用者自行处理 None 返回值。
        """
        try:
            if self._lakeshore_ctrl:
                return self._lakeshore_ctrl.get_temperature("A")
        except Exception:
            pass
        return None

    def _apply_setpoint_adjustment(self, new_setpoint: float, overshoot: float,
                                     base_overshoot: float, actual_k: float = 0):
        """将新的设定点写入 LakeShore（仅调整设定点，不修改 PID）。

        包含基本安全保护：大温差时发出警告。

        Args:
            new_setpoint: 新设定点温度 (K)
            overshoot: 当前过冲量 (K)
            base_overshoot: 基础过冲量 (K)
            actual_k: 当前实际温度 (K)，用于安全检查
        """
        if not self._lakeshore_ctrl:
            return

        # 安全检查：实际温度远高于设定点时发出警告
        if actual_k > 0 and actual_k > new_setpoint + 30.0:
            self.progress.emit(
                f"  ⚠ 安全警告: 实际温度 {actual_k:.1f}K 比设定点 "
                f"{new_setpoint:.1f}K 高 {actual_k - new_setpoint:.0f}K，"
                f"建议先自然冷却")

        try:
            self._lakeshore_ctrl.set_temperature(new_setpoint, loop=1)
            extra = overshoot - base_overshoot
            if extra > 0.01:
                self.progress.emit(
                    f"  设定点过冲调整 → {new_setpoint:.3f} K "
                    f"(base +{base_overshoot:.1f}K + Δ{extra:.1f}K)")
            elif overshoot > 0.01:
                self.progress.emit(
                    f"  初始设定点 → {new_setpoint:.3f} K "
                    f"(overshoot +{overshoot:.1f}K)")
            else:
                self.progress.emit(
                    f"  设定点 → {new_setpoint:.3f} K (无过冲)")
        except Exception as e:
            self.progress.emit(f"  设定点写入失败: {e}")

    # ------------------------------------------------------------------
    # 断点续传: 异常分类
    # ------------------------------------------------------------------

    @staticmethod
    def _is_recoverable_error(exc: Exception) -> bool:
        """判断异常是否为可恢复的 VISA 连接错误。

        Returns:
            True 如果是连接错误（应触发断点续传）
            False 如果是逻辑错误（应终止实验）
        """
        msg = str(exc)

        # VISA 标准连接丢失错误码
        if "VI_ERROR_CONN_LOST" in msg:
            return True

        # 连接断开关键字（大小写不敏感）
        # 注意：移除了 "timeout"——正常 VISA 超时不应触发完整 recovery，
        # 由稳定性循环的连续失败计数器处理
        recoverable_keywords = [
            "disconnected", "closed", "lost",
            "not responding", "connection",
        ]
        msg_lower = msg.lower()
        for kw in recoverable_keywords:
            if kw in msg_lower:
                return True

        return False

    # ------------------------------------------------------------------
    # 断点续传: S2P 文件去重
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filename(temp_k: float, vna_dbm: int, power_mw: int,
                        actual_k: float, attempt: int = 0) -> str:
        """构建 S2P 文件名（支持 attempt 后缀用于去重）。"""
        base = (f"YBCO_{vna_dbm:+d}dBm_{power_mw:02d}mW_"
                f"target_{temp_k:.0f}K")
        if attempt > 0:
            return f"{base}_attempt{attempt}_actual_{actual_k:.3f}K.s2p"
        return f"{base}_actual_{actual_k:.3f}K.s2p"

    @staticmethod
    def _find_next_filename(folder: str, temp_k: float, vna_dbm: int,
                            power_mw: int, actual_k: float) -> str:
        """扫描已有 S2P 文件，返回不冲突的文件名。

        规则：仅当同 (temp_k, vna_dbm, power_mw) + 同 actual_k 时
        才递增 attempt。不同 actual_k 不视为冲突（温度自然漂移的结果）。
        """
        import os as _os

        attempt = 0
        while True:
            name = ExperimentWorker._build_filename(
                temp_k, vna_dbm, power_mw, actual_k, attempt)
            if not _os.path.exists(_os.path.join(folder, name)):
                return name
            attempt += 1

    # ------------------------------------------------------------------
    # 测量时温度监控
    # ------------------------------------------------------------------

    @staticmethod
    def _check_measurement_temp(pre_k: float, post_k: float,
                                 target_k: float,
                                 laser_power_mw: float = 0) -> tuple:
        """检查测量前后的温度是否满足稳态条件。

        Args:
            pre_k: 测量前实际温度 (K)
            post_k: 测量后实际温度 (K)
            target_k: 目标温度 (K)
            laser_power_mw: 当前激光功率 (mW)，>0 时放宽测量后容差

        Returns:
            (ok: bool, reason: str)
        """
        import config

        # 测量前温度偏离 > ±0.5K（始终严格 — 测量前激光尚未加热样品）
        if abs(pre_k - target_k) > 0.5:
            return (False,
                    f"测量前温度偏离: {pre_k:.3f}K vs target {target_k:.1f}K "
                    f"(Δ={abs(pre_k-target_k):.3f}K > 0.5K)")

        # 测量后温度偏离 — 激光加热时放宽容差
        post_tolerance = (config.laser_on_temp_tolerance_k
                          if laser_power_mw > 0 else 0.5)
        if abs(post_k - target_k) > post_tolerance:
            return (False,
                    f"测量后温度偏离: {post_k:.3f}K vs target {target_k:.1f}K "
                    f"(Δ={abs(post_k-target_k):.3f}K > {post_tolerance}K"
                    f"{' (激光加热放宽)' if laser_power_mw > 0 else ''})")

        # 测量期间温度跳变 > 0.3K（始终严格）
        delta = abs(post_k - pre_k)
        if delta > 0.3:
            return (False,
                    f"测量期间温度跳变: |{post_k:.3f} - {pre_k:.3f}|"
                    f" = {delta:.3f}K > 0.3K")

        return (True, "")

    # ------------------------------------------------------------------
    # 断点续传: 恢复检查点
    # ------------------------------------------------------------------

    def _resume_from_checkpoint(self, ckpt_state: dict,
                                 ckpt_completed: list) -> tuple:
        """从检查点恢复运行状态。

        Returns:
            (temp_idx, vna_idx, power_idx) — 恢复起点索引
        """
        # 恢复回退状态机变量
        self._checkpoint_consecutive = ckpt_state.get(
            "rollback_consecutive_issues", 0)
        self._checkpoint_first_issue_idx = ckpt_state.get(
            "rollback_first_issue_index", None)
        self._checkpoint_rollback_count = ckpt_state.get(
            "rollback_count", 0)
        self._checkpoint_overshoot = ckpt_state.get(
            "overshoot_learning", {})

        # 恢复扩展时间参数
        self._checkpoint_max_wait = ckpt_state.get(
            "extended_max_wait_s", self._max_wait_s)
        self._checkpoint_pre_wait = ckpt_state.get(
            "extended_pre_wait_s", self._pre_measurement_wait_s)

        # 恢复已完成测量点
        self._checkpoint_completed_points = list(ckpt_completed)
        self._checkpoint_total_count = ckpt_state.get("total_count", 0)

        # 确定恢复起点
        resume = CheckpointManager.resume_from(
            ckpt_completed,
            self._temp_list,
            self._vna_power_list,
            self._power_list,
        )
        if resume is None:
            # 全部完成 → 从最后一个温度点之后开始（正常结束）
            return (len(self._temp_list), 0, 0)

        temp_idx, vna_idx, power_idx = resume
        self._checkpoint_temp_idx = temp_idx
        self._checkpoint_vna_idx = vna_idx
        self._checkpoint_power_idx = power_idx

        self.progress.emit(
            f"  从检查点恢复: 温度 #{temp_idx + 1}/{len(self._temp_list)} "
            f"({self._temp_list[temp_idx]:.1f}K), "
            f"已完成 {len(ckpt_completed)} 个测量点")

        return resume

    # ------------------------------------------------------------------
    # 断点续传: 旧 attempt 清理
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_old_attempts(output_dir: str):
        """实验正常结束时，每个测量点仅保留最新的 attempt。

        扫描所有 S2P 文件，按 (temp, vna_dbm, power_mw) 分组，
        每组中按 attempt 降序保留最新文件，删除带旧 attempt 的文件。
        """
        import os as _os
        import glob as _glob
        import re as _re
        import config

        if not getattr(config, "checkpoint_keep_latest_attempt_only", True):
            return

        pattern = _os.path.join(output_dir, "**", "*.s2p")
        s2p_files = _glob.glob(pattern, recursive=True)

        # 按 (temp, vna_dbm, power_mw) 分组
        groups = {}
        for fpath in s2p_files:
            fname = _os.path.basename(fpath)
            match = _re.match(
                r"YBCO_([+-]\d+)dBm_(\d+)mW_target_(\d+)K"
                r"(?:_attempt(\d+))?_actual_([\d.]+)K\.s2p", fname)
            if not match:
                continue

            vna_dbm = int(match.group(1))
            power_mw = int(match.group(2))
            temp_k = int(match.group(3))
            attempt = int(match.group(4)) if match.group(4) else 0
            actual_k = float(match.group(5))

            key = (temp_k, vna_dbm, power_mw)
            if key not in groups:
                groups[key] = []
            groups[key].append((attempt, actual_k, fpath))

        # 每组仅保留最新的（按 attempt 降序，最高 attempt = 最新）
        for key, files in groups.items():
            if len(files) <= 1:
                continue
            files.sort(key=lambda x: x[0], reverse=True)
            for attempt, actual_k, fpath in files[1:]:
                try:
                    _os.remove(fpath)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # 断点续传: 保存检查点
    # ------------------------------------------------------------------

    def _save_checkpoint(self):
        """保存当前运行状态到 checkpoint.json。（Task 8 完成完整实现）"""
        import os as _os
        from datetime import datetime

        state = {
            "temp_idx": getattr(self, "_checkpoint_temp_idx", 0),
            "vna_dbm_idx": getattr(self, "_checkpoint_vna_idx", 0),
            "power_mw_idx": getattr(self, "_checkpoint_power_idx", 0),
            "current_temp_k": getattr(self, "_checkpoint_current_temp",
                                       self._temp_list[0] if self._temp_list else 0),
            "total_count": getattr(self, "_checkpoint_total_count", 0),
            "extended_max_wait_s": getattr(self, "_checkpoint_max_wait", 1800),
            "extended_pre_wait_s": getattr(self, "_checkpoint_pre_wait", 0),
            "rollback_consecutive_issues": getattr(
                self, "_checkpoint_consecutive", 0),
            "rollback_first_issue_index": getattr(
                self, "_checkpoint_first_issue_idx", None),
            "rollback_count": getattr(self, "_checkpoint_rollback_count", 0),
            "overshoot_learning": getattr(self, "_checkpoint_overshoot", {}),
        }

        completed = getattr(self, "_checkpoint_completed_points", [])

        CheckpointManager.save(
            self._output_dir, state, completed,
            experiment_id=_os.path.basename(self._output_dir),
            original_temp_list=self._temp_list,
            original_vna_power_list=self._vna_power_list,
            original_power_list=self._power_list,
        )

    # ------------------------------------------------------------------
    # 断点续传: 重连循环
    # ------------------------------------------------------------------

    def _enter_recovery(self, error: Exception):
        """连接丢失后的恢复流程: 保存检查点 → 循环重连。

        在 run() 的顶层 except 块中调用。

        Args:
            error: 触发恢复的异常
        """
        import time as _time
        import config

        self.progress.emit(f"  ⛔ VISA 连接丢失: {error}")
        self.progress.emit(f"  正在保存检查点...")

        # 保存当前状态
        self._save_checkpoint()

        # 通知 GUI
        self.experiment_recovering.emit(str(error))

        # 重连循环
        max_attempts = (config.reconnect_max_wait_minutes * 60 //
                        config.reconnect_retry_interval_s)
        for attempt in range(1, max_attempts + 1):
            if self._abort_flag:
                self.progress.emit("  用户中止 — 检查点已保存")
                self.experiment_aborted.emit()
                return

            self.progress.emit(
                f"  重连尝试 #{attempt}/{max_attempts} "
                f"(等待 {config.reconnect_retry_interval_s}s)...")
            _time.sleep(config.reconnect_retry_interval_s)

            # 尝试重新连接所有设备
            all_ok = True

            if self._lakeshore_ctrl:
                try:
                    # 先尝试修复 VISA session（RS-232 串口断开需重建连接，
                    # 不能直接用旧 session 重试——旧 session 已死透）
                    try:
                        if not self._lakeshore_ctrl.reconnect():
                            self._lakeshore_ctrl.hard_reconnect()
                    except AttributeError:
                        pass  # 非 LakeShore335 对象，跳过修复
                    t = self._lakeshore_ctrl.get_temperature("A")
                    if t is None:
                        raise Exception("temperature read returned None")
                    self.progress.emit(f"  ✓ LakeShore 重连成功 ({t:.3f} K)")
                except Exception as e:
                    self.progress.emit(f"  ✗ LakeShore 重连失败: {e}")
                    all_ok = False

            if self._laser_ctrl:
                try:
                    self._laser_ctrl.get_status()
                    self.progress.emit(f"  ✓ Laser 重连成功")
                except Exception as e:
                    self.progress.emit(f"  ✗ Laser 重连失败: {e}")
                    all_ok = False

            if self._vna:
                try:
                    idn = self._vna.query("*IDN?").strip()
                    self.progress.emit(f"  ✓ VNA 重连成功: {idn}")
                except Exception as e:
                    self.progress.emit(f"  ✗ VNA 重连失败: {e}")
                    all_ok = False

            if all_ok:
                self.progress.emit("  ✓ 所有设备重连成功，恢复实验")
                self.experiment_recovered.emit()
                # 递归调用 run() 从检查点恢复
                self.run()
                return

        # 超时
        self.progress.emit(
            f"  ⛔ 重连超时 ({config.reconnect_max_wait_minutes}min)，"
            f"检查点已保存，可稍后手动恢复")
        self.experiment_recovery_timeout.emit()

    # ------------------------------------------------------------------
    # 断点续传: 检查点辅助
    # ------------------------------------------------------------------

    def _update_checkpoint_state(self, temp_idx: int, vna_idx: int,
                                  power_idx: int, current_temp: float,
                                  total_count: int,
                                  extended_max_wait_s: float,
                                  extended_pre_wait_s: float):
        """更新运行中的检查点追踪变量（不写入磁盘）。"""
        self._checkpoint_temp_idx = temp_idx
        self._checkpoint_vna_idx = vna_idx
        self._checkpoint_power_idx = power_idx
        self._checkpoint_current_temp = current_temp
        self._checkpoint_total_count = total_count
        self._checkpoint_max_wait = extended_max_wait_s
        self._checkpoint_pre_wait = extended_pre_wait_s

    def _record_measurement_point(self, temp_k: float, vna_dbm: int,
                                   power_mw: int, actual_k: float):
        """记录一个已完成测量点到 completed_points。

        每 N 个点增量保存到磁盘（由 checkpoint_save_interval_points 控制）。
        """
        if not hasattr(self, "_checkpoint_completed_points"):
            self._checkpoint_completed_points = []
        self._checkpoint_completed_points.append({
            "temp_k": temp_k,
            "vna_dbm": vna_dbm,
            "power_mw": power_mw,
            "actual_k": actual_k,
        })

        # 每 N 个点增量保存
        import config
        interval = getattr(config, "checkpoint_save_interval_points", 5)
        if len(self._checkpoint_completed_points) % interval == 0:
            self._save_checkpoint()

    # ------------------------------------------------------------------
    # Claude 主动监控: status.json + commands.json
    # ------------------------------------------------------------------

    def _write_status(self, phase: str = None, target_k: float = None,
                      actual_k: float = None, vna_dbm: float = None,
                      laser_mw: float = None):
        """将当前状态写入 status.json（供 Claude Code 监控）。

        写入失败时静默降级（不抛异常），不影响实验运行。
        """
        if not self._output_dir:
            return
        try:
            if self._status_writer is None:
                from experiment_status import ExperimentStatusWriter
                self._status_writer = ExperimentStatusWriter(self._output_dir)
                # 首次写入：初始化完整状态
                import config
                self._status_writer.write_initial(
                    experiment_id=os.path.basename(self._output_dir),
                    temperature_plan=self._temp_list,
                    vna_power_plan=self._vna_power_list,
                    laser_power_plan=self._power_list,
                    runtime_params={
                        "max_wait_seconds": self._max_wait_s,
                        "meltdown_threshold_k": self._meltdown_threshold_k,
                        "max_meltdown_restarts": getattr(
                            config, "max_meltdown_restarts", 3),
                        "current_overshoot_k": getattr(
                            self, "_current_overshoot", 0),
                    },
                )

            self._status_writer.update_current(
                target_k=target_k, actual_k=actual_k,
                vna_dbm=vna_dbm, laser_mw=laser_mw, phase=phase,
            )
        except Exception:
            pass  # 状态写入失败不影响实验

    def _poll_commands(self):
        """读取 commands.json，应用 Claude Code 发出的干预命令。

        在温度点切换、熔断触发前等决策点调用。
        """
        if not self._output_dir:
            return
        cmd_path = os.path.join(self._output_dir, "commands.json")
        if not os.path.exists(cmd_path):
            return
        import json
        try:
            with open(cmd_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        modified = False
        for cmd in data.get("commands", []):
            if cmd.get("status") != "pending":
                continue

            action = cmd.get("action", "")
            params = cmd.get("params", {})

            if action == "extend_max_wait":
                add_min = int(params.get("add_minutes", 30))
                self._max_wait_s += add_min * 60
                self.progress.emit(
                    f"  [Claude] max_wait +{add_min}min → "
                    f"{self._max_wait_s // 60}min: {cmd.get('reason', '')}")
                cmd["status"] = "applied"
                modified = True

            elif action == "relax_meltdown":
                new_threshold = float(params.get("new_threshold_k", 0.35))
                self._meltdown_threshold_k = new_threshold
                self.progress.emit(
                    f"  [Claude] 熔断阈值放宽至 {new_threshold}K: "
                    f"{cmd.get('reason', '')}")
                cmd["status"] = "applied"
                modified = True

            elif action == "skip_temperature":
                self.progress.emit(
                    f"  [Claude] 建议跳过当前温度点: "
                    f"{cmd.get('reason', '')}")
                cmd["status"] = "applied"
                modified = True

        if modified:
            try:
                with open(cmd_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 补测模式 (fill mode)
    # ------------------------------------------------------------------

    def _run_fill(self, experiment_dir: str):
        """执行补测：冷却 → 升温稳定 → 测量缺失点 → 写入完成报告。

        Args:
            experiment_dir: 实验输出目录（含 fill_plan.json）
        """
        import json
        import config
        import time as _time

        fill_plan_path = os.path.join(experiment_dir, "fill_plan.json")
        if not os.path.exists(fill_plan_path):
            self.progress.emit(f"  [错误] 未找到 fill_plan.json: {fill_plan_path}")
            return

        with open(fill_plan_path, "r", encoding="utf-8") as f:
            fill_plan = json.load(f)

        temp_plan = fill_plan.get("temperature_plan", [])
        measurements = fill_plan.get("measurements", [])
        cooldown_offset = fill_plan.get(
            "cooldown_offset_k", config.fill_cooldown_offset_k)
        total_expected = sum(len(m.get("laser_powers_mw", []))
                             for m in measurements)
        total_measured = 0

        self._output_dir = experiment_dir
        self.progress.emit(f"补测开始 — {len(temp_plan)} 个温度点, "
                           f"{total_expected} 个测量点")

        for target_k in temp_plan:
            if self._abort_flag:
                break

            # 阶段 1: 冷却 (setpoint = target - offset)
            cool_setpoint = target_k - cooldown_offset
            cool_setpoint = max(cool_setpoint, config.fill_min_safe_temp_k)
            self.progress.emit(
                f"  → 冷却至 {cool_setpoint:.1f}K (target={target_k}K - "
                f"{cooldown_offset}K)")

            if self._lakeshore_ctrl:
                self._lakeshore_ctrl.set_temperature(cool_setpoint, loop=1)

            # 等待实际温度低于目标
            cool_start = _time.monotonic()
            cool_max_s = config.fill_cooldown_max_wait_minutes * 60
            cool_poll = config.fill_cooldown_poll_seconds
            cool_iter = 0
            cool_max_iter = int(cool_max_s / cool_poll) + 10  # 安全上限

            while not self._abort_flag and cool_iter < cool_max_iter:
                _time.sleep(cool_poll)
                cool_iter += 1
                actual = self._lakeshore_ctrl.get_temperature() if self._lakeshore_ctrl else target_k - 1
                if actual < target_k:
                    self.progress.emit(f"  冷却完成: {actual:.3f}K < {target_k}K")
                    break
                if _time.monotonic() - cool_start > cool_max_s:
                    self.progress.emit(
                        f"  ⚠ 冷却超时 ({cool_max_s // 60}min)，继续执行")
                    break

            if self._abort_flag:
                break

            # 阶段 2: 升温稳定 (setpoint = target + overshoot)
            self.progress.emit(f"  → 升温至 {target_k:.1f}K 并稳定")
            # 使用现有稳定逻辑：调用 set_temperature(target_k)
            if self._lakeshore_ctrl:
                self._lakeshore_ctrl.set_temperature(target_k, loop=1)

            # 简化稳定等待（补测模式用 basic wait）
            _time.sleep(10)  # 升温阶段等待
            # 简单轮询等待进入 target_k ± 1K
            stable_ok = False
            stable_max_iter = 120  # 最多 120 次迭代
            for stable_iter in range(stable_max_iter):
                if self._abort_flag:
                    break
                _time.sleep(10)
                actual = self._lakeshore_ctrl.get_temperature() if self._lakeshore_ctrl else target_k
                if abs(actual - target_k) < 1.0:
                    stable_ok = True
                    break
                self.progress.emit(
                    f"  等待稳定: {actual:.3f}K (target={target_k}K)")

            if not stable_ok or self._abort_flag:
                continue

            # 阶段 3: 补测扫描
            for m in measurements:
                if m["target_k"] != target_k:
                    continue

                vna_dbm = m["vna_dbm"]
                for power_mw in m.get("laser_powers_mw", []):
                    if self._abort_flag:
                        break

                    # 检查是否已有 .s2p 文件（去重）
                    from fill_planner import scan_s2p_files
                    existing = scan_s2p_files(experiment_dir)
                    existing_set = {(e["target_k"], e["vna_dbm"], e["power_mw"])
                                    for e in existing}
                    if (target_k, vna_dbm, power_mw) in existing_set:
                        self.progress.emit(
                            f"  跳过已存在: {target_k}K {vna_dbm:+d}dBm "
                            f"{power_mw}mW")
                        total_measured += 1
                        continue

                    # 设置 VNA 功率
                    if self._vna:
                        try:
                            self._vna.write(f":SOURce:POWer {vna_dbm}")
                        except Exception:
                            pass

                    # 设置激光功率
                    if self._laser_ctrl and power_mw > 0:
                        self._laser_ctrl.set_power(power_mw)
                        self._laser_ctrl.output_on()
                    elif self._laser_ctrl:
                        self._laser_ctrl.output_off()

                    _time.sleep(5)  # 简短沉降

                    actual_k = (self._lakeshore_ctrl.get_temperature()
                                if self._lakeshore_ctrl else target_k)

                    self.progress.emit(
                        f"  Measuring {vna_dbm:+d} dBm / {power_mw} mW "
                        f"@ {actual_k:.3f} K")

                    # 构建输出路径
                    temp_str = f"{target_k:.0f}K"
                    vna_str = f"{vna_dbm:+d}dBm"
                    pw_str = f"{power_mw:02d}mW"
                    folder = os.path.join(experiment_dir, temp_str, vna_str, pw_str)
                    os.makedirs(folder, exist_ok=True)
                    filename = (
                        f"YBCO_{vna_dbm:+d}dBm_{power_mw:02d}mW_"
                        f"target_{target_k:.0f}K_actual_{actual_k:.3f}K.s2p"
                    )
                    filepath = os.path.join(folder, filename)

                    # VNA S2P 保存
                    if self._vna:
                        try:
                            self._vna.write(":INITiate:CONTinuous OFF")
                            self._vna.write(":INITiate:IMMediate")
                            _time.sleep(2)
                            self._vna.write(f':MMEMory:STORe "{filepath}"')
                        except Exception as e:
                            self.progress.emit(f"  VNA 保存失败: {e}")

                    total_measured += 1

            # 温度点完成后关激光
            if self._laser_ctrl:
                self._laser_ctrl.output_off()

        # 阶段 4: 完成
        self._write_fill_complete(experiment_dir, total_measured, total_expected)
        self.progress.emit(
            f"补测完成 — {total_measured}/{total_expected} 个测量点")
        self.experiment_finished.emit(total_measured)

    def _write_fill_complete(self, output_dir: str,
                             total_measured: int, total_expected: int):
        """写入 fill_complete.json。"""
        import json
        from datetime import datetime, timezone

        completeness = (total_measured / total_expected
                        if total_expected > 0 else 1.0)
        report = {
            "completed_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S"),
            "total_measured": total_measured,
            "total_expected": total_expected,
            "completeness": completeness,
        }
        path = os.path.join(output_dir, "fill_complete.json")
        try:
            os.makedirs(output_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 实验主循环
    # ------------------------------------------------------------------

    @pyqtSlot()
    def run(self):
        """Public entry point — wraps _run_impl with recovery on connection loss."""
        try:
            self._run_impl()
        except Exception as e:
            if self._is_recoverable_error(e):
                self._enter_recovery(e)
            else:
                self.experiment_error.emit(f"Experiment failed: {e}")
                try:
                    log_file.close()
                except Exception:
                    pass

    def _run_impl(self):
        try:
            from datetime import datetime
            import time as _time
            import os
            import config

            self.experiment_started.emit()
            count = 0
            start_time = datetime.now()

            # ---- 初始化内存监控 ----
            mem_monitor = None
            mem_check_interval = getattr(config, "memory_check_interval_s", 60)
            if getattr(config, "memory_monitor_enabled", True):
                mem_monitor = MemoryMonitor(
                    warning_threshold_mb=getattr(
                        config, "memory_warning_threshold_mb", 8192),
                    critical_threshold_mb=getattr(
                        config, "memory_critical_threshold_mb", 4096),
                    log_callback=lambda msg: self.progress.emit(msg),
                )

            # ---- 创建日志目录并打开日志文件 ----
            log_dir = os.path.join(self._output_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(
                log_dir,
                f"experiment_log_{start_time.strftime('%Y%m%d_%H%M%S')}.txt")

            # 先定义 _log（使用临时占位，稍后替换为真实文件）
            def _log_fallback(msg: str):
                self.progress.emit(msg)

            def _log(msg: str):
                """同时写入 GUI 信号和日志文件。"""
                ts = _time.strftime(
                    "[%Y-%m-%d %H:%M:%S] ", _time.localtime(_time.time()))
                self.progress.emit(msg)
                log_file.write(ts + msg + "\n")
                log_file.flush()

            try:
                log_file = open(log_path, "w", encoding="utf-8")
            except (OSError, IOError) as _log_open_err:
                import io
                log_file = io.StringIO()
                self.progress.emit(
                    f"  ⚠ 无法创建日志文件: {_log_open_err}，使用内存缓冲")

            _log(f"实验开始 — 输出目录: {self._output_dir}")
            _log(f"温度列表: {self._temp_list}")
            _log(f"激光功率列表: {self._power_list} mW")
            _log(f"VNA 功率列表: {self._vna_power_list} dBm")

            # ---- 写入 manifest.json（供数据整合工具使用） ----
            try:
                import json as _json
                manifest = {
                    "experiment_id": os.path.basename(self._output_dir),
                    "start_time": start_time.isoformat(),
                    "temperature_plan": self._temp_list,
                    "vna_power_plan": self._vna_power_list,
                    "laser_power_plan": self._power_list,
                }
                manifest_path = os.path.join(self._output_dir, "manifest.json")
                with open(manifest_path, "w", encoding="utf-8") as _f:
                    _json.dump(manifest, _f, ensure_ascii=False, indent=2)
            except Exception:
                pass  # manifest 写入失败不影响实验

            # ---- 初始内存状态 + 进程诊断 ----
            if mem_monitor:
                info = mem_monitor.check()
                _log(f"内存监控启用 | {mem_monitor.format_info(info)}")
                _log(f"  告警阈值: <{mem_monitor.warning_threshold_mb:.0f}MB, "
                     f"严重阈值: <{mem_monitor.critical_threshold_mb:.0f}MB")
                if getattr(config, "memory_process_diag_enabled", True):
                    from memory_monitor import get_top_processes
                    _log(get_top_processes())

            # ---- 超时软化 + 连续回退状态机（需求 A/B/C） ----
            _rollback_state = TimeoutRollbackState(
                temp_list=self._temp_list,
                soft_pass_band_k=getattr(config, "timeout_soft_pass_band_k", 2.0),
                consecutive_threshold=getattr(config, "consecutive_issue_threshold", 2),
                skip_validation_temp_k=getattr(config, "skip_validation_temp_k", 4.0),
                rollback_max_wait_increase_s=(
                    getattr(config, "rollback_max_wait_increase_min", 30) * 60),
                rollback_pre_wait_increase_s=(
                    getattr(config, "rollback_pre_wait_increase_min", 10) * 60),
            )
            _extended_max_wait_s = self._max_wait_s
            _extended_pre_wait_s = self._pre_measurement_wait_s

            # ---- 初始化 checkpoint 追踪变量 ----
            self._checkpoint_completed_points = []
            self._checkpoint_temp_idx = 0
            self._checkpoint_vna_idx = 0
            self._checkpoint_power_idx = 0
            self._checkpoint_current_temp = self._temp_list[0] if self._temp_list else 0
            self._checkpoint_total_count = 0
            self._checkpoint_max_wait = _extended_max_wait_s
            self._checkpoint_pre_wait = _extended_pre_wait_s
            self._checkpoint_consecutive = 0
            self._checkpoint_first_issue_idx = None
            self._checkpoint_rollback_count = 0
            self._checkpoint_overshoot = {}

            # ---- 加载 overshoot 学习历史（跨实验持久化） ----
            _overshoot_learning = {}
            try:
                _settings = app_settings.load_settings()
                _raw = _settings.get("overshoot_learning", {})
                _overshoot_learning = {
                    float(k): float(v) for k, v in _raw.items()}
                if _overshoot_learning:
                    _log(f"已加载 overshoot 学习数据: "
                         f"{len(_overshoot_learning)} 个温度点")
            except Exception:
                pass  # 首次运行或无学习数据，使用默认值

            temp_idx = 0

            # ---- 检查点恢复询问 ----
            checkpoint = CheckpointManager.load(self._output_dir)
            if checkpoint is not None:
                ckpt_state, ckpt_completed = checkpoint
                import os as _os2
                exp_id = ckpt_state.get("experiment_id") or _os2.path.basename(
                    self._output_dir)
                self.experiment_resume_prompt.emit(exp_id, len(ckpt_completed))
                # 等待 GUI 回调（QMessageBox 模态 → BlockingQueuedConnection 同步返回）
                resume_action = getattr(self, "_resume_action", "cancel")
                if resume_action == "resume":
                    resume_idx = self._resume_from_checkpoint(ckpt_state, ckpt_completed)
                    if resume_idx is not None:
                        temp_idx, _vi, _pi = resume_idx
                        # 同步扩展时间参数
                        if hasattr(self, "_checkpoint_max_wait"):
                            _extended_max_wait_s = self._checkpoint_max_wait
                        if hasattr(self, "_checkpoint_pre_wait"):
                            _extended_pre_wait_s = self._checkpoint_pre_wait
                        # 同步回退状态机
                        _rollback_state.consecutive_issues = (
                            self._checkpoint_consecutive)
                        _rollback_state.first_issue_index = (
                            self._checkpoint_first_issue_idx)
                        _rollback_state.rollback_count = (
                            self._checkpoint_rollback_count)
                elif resume_action == "restart":
                    CheckpointManager.delete(self._output_dir)
                elif resume_action == "cancel":
                    self.progress.emit("用户取消恢复，实验不启动")
                    temp_idx = len(self._temp_list)  # 跳过所有温度点

            # ---- 合并 checkpoint overshoot 学习数据 ----
            _chkpt_overshoot = getattr(
                self, "_checkpoint_overshoot", {})
            if _chkpt_overshoot:
                _merged_count = 0
                for _ck_k, _ck_v in _chkpt_overshoot.items():
                    if _ck_k not in _overshoot_learning:
                        _overshoot_learning[_ck_k] = _ck_v
                        _merged_count += 1
                if _merged_count:
                    _log(f"从 checkpoint 合并 overshoot 学习数据: "
                         f"{_merged_count} 个温度点")
                self._checkpoint_overshoot = dict(_overshoot_learning)

            # ---- 恢复后第一个温度点标记（触发快速预检） ----
            _is_first_temp_after_resume = (
                checkpoint is not None
                and getattr(self, "_resume_action", "cancel") == "resume"
            )

            while temp_idx < len(self._temp_list):
                target_k = self._temp_list[temp_idx]

                if self._abort_flag:
                    self.experiment_aborted.emit()
                    log_file.close()
                    return

                # ---- 4K 豁免标记（需求 C） ----
                _skip_validation = _rollback_state.is_skip_validation_temp(target_k)
                if _skip_validation:
                    _log(f"→ Stabilising to {target_k:.1f} K ... "
                         f"[4K: 跳过温度范围检定，仅判定稳态]")
                else:
                    _log(f"→ Stabilising to {target_k:.1f} K ...")

                # Claude 监控: 开始稳定前先轮询命令
                self._poll_commands()

                # ---- 读取当前温度 ----
                actual_k = target_k
                try:
                    if self._lakeshore_ctrl:
                        actual_k = self._lakeshore_ctrl.get_temperature("A")
                        if actual_k is None:
                            actual_k = target_k
                except Exception as e:
                    _log(f"  ⚠ 初始温度读取失败: {e}，使用 target={target_k:.1f}K 作为回退")

                # ---- 快速预检（恢复后第一个温度点） ----
                _is_fast_track = False
                if _is_first_temp_after_resume:
                    _is_first_temp_after_resume = False
                    from stability_monitor import AdvancedStabilityMonitor as _ASM
                    _log(f"  恢复后快速预检 @ {target_k:.1f}K ...")
                    _check_result = _ASM.fast_stability_check(
                        lambda: self._read_temperature_safe(), target_k)
                    _log(f"  快速预检结果: skip_overshoot={_check_result['skip_overshoot']}, "
                         f"reason={_check_result['reason']}, "
                         f"avg_temp={_check_result.get('avg_temp', 'N/A')}, "
                         f"elapsed={_check_result.get('elapsed_s', 0):.0f}s")
                    if _check_result["skip_overshoot"]:
                        _is_fast_track = True
                        _log(f"  温度已稳定，跳过 overshoot，直接快速稳判")

                # ---- 初始化稳定性控制器 ----
                from ui.experiment_stability_controller import (
                    ExperimentStabilityController,
                )
                stability_ctrl = ExperimentStabilityController(log_callback=lambda msg: _log(msg))
                stability_ctrl.set_overshoot_learning(_overshoot_learning)
                stability_ctrl.MAX_WAIT_SECONDS = _extended_max_wait_s
                stability_ctrl.skip_zone_check = _skip_validation
                stability_ctrl.setup(
                    target_k=target_k,
                    current_temperature=actual_k,
                )

                # ---- 确保加热器在 Medium 档位 ----
                if self._lakeshore_ctrl:
                    self._lakeshore_ctrl.set_heater_range(1, 2)

                # ---- 写入固定 PID（仅一次，永不调整） ----
                fixed_pid = stability_ctrl.get_fixed_pid()
                if self._lakeshore_ctrl:
                    self._lakeshore_ctrl.set_pid(
                        fixed_pid["p"], fixed_pid["i"], fixed_pid["d"], loop=1)

                # ---- 写入初始设定点 ----
                if _is_fast_track:
                    stability_ctrl.force_skip_overshoot()
                    if self._lakeshore_ctrl:
                        self._lakeshore_ctrl.set_temperature(target_k, loop=1)
                    _log(f"  温区 PID: P={fixed_pid['p']:g}/"
                         f"I={fixed_pid['i']:g}/D={fixed_pid['d']:g}, "
                         f"设定点 → {target_k:.3f} K [快速预检: 跳过 overshoot]")
                else:
                    setpoint_k = stability_ctrl.needs_setpoint_adjustment()
                    if self._lakeshore_ctrl and setpoint_k is not None:
                        self._lakeshore_ctrl.set_temperature(setpoint_k, loop=1)
                        overshoot_info = ""
                        if stability_ctrl.current_overshoot > 0.01:
                            overshoot_info = (f" (overshoot "
                                             f"+{stability_ctrl.current_overshoot:.1f}K)")
                        _log(f"  温区 PID: P={fixed_pid['p']:g}/"
                             f"I={fixed_pid['i']:g}/D={fixed_pid['d']:g}, "
                             f"设定点 → {setpoint_k:.3f} K{overshoot_info}")

                # ---- 等待稳定性（2 阶段轮询） ----
                _temp_skip_measurement = False
                _was_soft_pass = False
                start_t = _time.time()
                prev_phase = "sparse"
                last_mem_check = 0.0
                _read_failures = 0              # 连续温度读取失败计数

                while True:
                    if self._abort_flag:
                        self.experiment_aborted.emit()
                        log_file.close()
                        return

                    # 始终计算 elapsed，即使读取失败也推进超时计时
                    elapsed = _time.time() - start_t

                    # 读取温度
                    try:
                        actual_k = self._lakeshore_ctrl.get_temperature("A") \
                            if self._lakeshore_ctrl else target_k
                        _read_failures = 0  # 成功后重置
                    except Exception as _read_exc:
                        _read_failures += 1
                        if _read_failures >= 5:
                            _log(f"  ⛔ 温度读取连续失败 {_read_failures} 次，"
                                 f"触发断点续传: {_read_exc}")
                            # 传播到 run() → _enter_recovery()
                            raise
                        if _read_failures == 1:
                            _log(f"  ⚠ 温度读取失败 ({_read_exc})，"
                                 f"重试中...")
                        else:
                            _log(f"  ⚠ 连续失败 {_read_failures}/5 次: "
                                 f"{_read_exc}")
                        _time.sleep(10)
                        continue

                    if actual_k is None:
                        actual_k = target_k

                    # 添加到稳定性控制器
                    stability_ctrl.add_reading(actual_k)
                    self.temperature_stabilizing.emit(target_k, actual_k)

                    result = stability_ctrl.check(elapsed)

                    # 阶段转换日志
                    if stability_ctrl.phase.value != prev_phase:
                        prev_phase = stability_ctrl.phase.value
                        if prev_phase == "fine":
                            _log(f"  进入 Phase 2（趋于平稳），提高轮询频率至 "
                                 f"{config.fine_poll_seconds}s，并行判目标区间+稳态")
                            self._write_status(
                                phase="stabilizing_fine", target_k=target_k,
                                actual_k=actual_k)

                    # 检查是否需要调整设定点过冲
                    new_sp = stability_ctrl.needs_setpoint_adjustment()
                    if new_sp is not None:
                        self._apply_setpoint_adjustment(
                            new_sp,
                            stability_ctrl.current_overshoot,
                            stability_ctrl.base_overshoot,
                            actual_k,
                        )

                    # 冷却模式进度日志
                    stability_ctrl.log_cooling_progress()

                    # 处理结果
                    if result.stable:
                        self.temperature_stable.emit(target_k, actual_k)
                        _log(f"  稳定: {actual_k:.3f} K "
                             f"(target {target_k:.1f} K, "
                             f"phase={stability_ctrl.phase.value})")
                        self._write_status(
                            phase="pre_measuring", target_k=target_k,
                            actual_k=actual_k)
                        break
                    elif result.reason == "good_enough":
                        _log(f"  good_enough — "
                             f"avg={result.avg_temp:.3f}K vs "
                             f"target={target_k:.1f}K "
                             f"(phase={stability_ctrl.phase.value})，继续测量")
                        self._write_status(
                            phase="pre_measuring", target_k=target_k,
                            actual_k=actual_k)
                        self.temperature_stable.emit(target_k, actual_k)
                        break
                    elif result.reason == "timeout":
                        _timeout_type = _rollback_state.classify_timeout(
                            result.avg_temp, target_k)
                        _delta = abs(result.avg_temp - target_k)

                        # 冷却模式超时：冷却需要时间，不视为 hard_fail，
                        # 使用更宽松的判定（即使 Δ > soft_pass_band 也尝试测量）
                        _is_cooling_timeout = getattr(
                            stability_ctrl, "is_cooling", False)
                        if _is_cooling_timeout:
                            _log(f"  冷却超时 @ {target_k:.1f}K — "
                                 f"当前={result.avg_temp:.3f}K, "
                                 f"Δ={_delta:.3f}K（冷却耗时超出预期），"
                                 f"以当前温度继续测量")
                            self.temperature_stable.emit(target_k, actual_k)
                            _was_soft_pass = True
                            _rollback_state.record_result(temp_idx, "soft_pass")
                            break

                        if _timeout_type == "soft_pass":
                            _log(f"  超时软通过 @ {target_k:.1f}K — "
                                 f"avg={result.avg_temp:.3f}K, "
                                 f"Δ={_delta:.3f}K ≤ "
                                 f"{_rollback_state.soft_pass_band_k}K")
                            self.temperature_stable.emit(target_k, actual_k)
                            _was_soft_pass = True
                            _rollback_state.record_result(temp_idx, "soft_pass")
                            break
                        else:
                            _log(f"  超时跳过 @ {target_k:.1f}K — "
                                 f"avg={result.avg_temp:.3f}K, "
                                 f"Δ={_delta:.3f}K > "
                                 f"{_rollback_state.soft_pass_band_k}K")
                            self.temperature_stable.emit(target_k, actual_k)
                            _rollback_state.record_result(temp_idx, "hard_fail")
                            _temp_skip_measurement = True
                            break

                    # ---- periodic memory check（含主动保护） ----
                    if mem_monitor:
                        now = _time.time()
                        if now - last_mem_check >= mem_check_interval:
                            info = mem_monitor.check()
                            _log(mem_monitor.format_info(info))
                            if info.warning:
                                _log(mem_monitor.format_warning(info))
                            last_mem_check = now

                            # 主动暂停保护: 可用内存低于自动暂停阈值
                            auto_pause_mb = getattr(
                                config, "memory_auto_pause_threshold_mb", 3072)
                            if info.avail_phys_mb < auto_pause_mb:
                                _log(
                                    f"  ⚠ 系统可用内存仅 "
                                    f"{info.avail_phys_mb:.0f} MB，"
                                    f"触发自动暂停保护")
                                self.memory_critical.emit(
                                    f"系统可用内存仅 "
                                    f"{info.avail_phys_mb:.0f} MB！\n\n"
                                    f"实验已自动暂停，等待内存恢复。\n"
                                    f"请关闭其他应用程序释放内存。\n\n"
                                    f"每 30 秒检查一次，可用内存恢复至 "
                                    f">{auto_pause_mb + 1024} MB 后自动继续。")
                                # 等待内存恢复循环
                                while True:
                                    if self._abort_flag:
                                        self.experiment_aborted.emit()
                                        log_file.close()
                                        return
                                    _time.sleep(30)
                                    pause_info = mem_monitor.check()
                                    _log(
                                        f"  [等待内存恢复] "
                                        f"{mem_monitor.format_info(pause_info)}")
                                    if (pause_info.avail_phys_mb
                                            >= auto_pause_mb + 1024):
                                        _log(
                                            f"  ✓ 内存恢复至 "
                                            f"{pause_info.avail_phys_mb:.0f} MB，"
                                            f"继续实验")
                                        break
                                last_mem_check = _time.time()

                    # 阶段感知轮询间隔
                    poll_s = (config.fine_poll_seconds
                              if stability_ctrl.phase.value == "fine"
                              else config.sparse_poll_seconds)
                    _time.sleep(poll_s)

                # ============================================================
                # VNA power sweep + laser power sweep（含测量熔断 + 重启）
                # ============================================================
                first_measurement_cycle = True

                # 确保至少有一个 VNA 功率值
                vna_powers = list(self._vna_power_list) if self._vna_power_list else []
                if not vna_powers:
                    fallback = self._vna_settings.get("power_dbm", [-45])
                    if isinstance(fallback, list):
                        vna_powers = list(fallback)
                    else:
                        vna_powers = [fallback]
                vna_powers = sorted(set(vna_powers))

                # 测量重启循环（不限次数，由 max_wait 超时终止）
                measurement_restarts = 0
                self._drift_meltdown_count = 0   # 每个温度点重置
                self._settling_multiplier = 1.0
                self._in_retry_mode = False
                self._laser_was_off = True   # Fix 3: 每个温度点初始时激光关闭
                while True:
                    # 需求 A: 超时硬失败 → 跳过此温度点所有测量
                    if _temp_skip_measurement:
                        _log(f"  跳过温度点 {target_k:.1f}K（超时硬失败）")
                        # Claude 监控: 记录跳过
                        try:
                            if self._status_writer:
                                self._status_writer.add_skipped(
                                    target_k=target_k,
                                    reason="timeout_hard_fail",
                                    vna_power_remaining=self._vna_power_list,
                                )
                        except Exception:
                            pass
                        break

                    # ---- 预测量等待（仅首次测量循环） ----
                    if first_measurement_cycle and _extended_pre_wait_s > 0:
                        _log(f"  预测量等待 "
                             f"{_extended_pre_wait_s / 60:.0f} min...")
                        _time.sleep(_extended_pre_wait_s)
                        try:
                            if self._lakeshore_ctrl:
                                t = self._lakeshore_ctrl.get_temperature("A")
                                if t is not None:
                                    actual_k = t
                                    _log(f"  等待结束，当前温度: {actual_k:.3f} K")
                        except Exception:
                            pass

                        # Fix 1: 验证等待后温度仍在目标公差内
                        post_wait_delta = abs(actual_k - target_k)
                        pre_wait_tolerance = getattr(
                            config, "pre_measurement_wait_temp_tolerance_k", 0.5)
                        if post_wait_delta > pre_wait_tolerance:
                            _log(f"  ⚠ 预等待后温度偏离 {post_wait_delta:.3f}K "
                                 f"> {pre_wait_tolerance}K，重新进入稳定性等待")
                            # 重新建立 stability controller 并等待稳定
                            stability_ctrl = ExperimentStabilityController(log_callback=lambda msg: _log(msg))
                            stability_ctrl.set_overshoot_learning(_overshoot_learning)
                            stability_ctrl.MAX_WAIT_SECONDS = self._max_wait_s
                            stability_ctrl.setup(
                                target_k=target_k,
                                current_temperature=actual_k,
                            )
                            if self._lakeshore_ctrl:
                                fixed_pid = stability_ctrl.get_fixed_pid()
                                self._lakeshore_ctrl.set_pid(
                                    fixed_pid["p"], fixed_pid["i"],
                                    fixed_pid["d"], loop=1)
                                new_sp = stability_ctrl.needs_setpoint_adjustment()
                                if new_sp is not None:
                                    self._lakeshore_ctrl.set_temperature(new_sp, loop=1)

                            wait_start = _time.time()
                            _rewait_read_failures = 0  # 温度读取连续失败计数
                            while True:
                                if self._abort_flag:
                                    self.experiment_aborted.emit()
                                    log_file.close()
                                    return
                                try:
                                    actual_k = self._lakeshore_ctrl.get_temperature("A") \
                                        if self._lakeshore_ctrl else target_k
                                    _rewait_read_failures = 0  # 成功后重置
                                except Exception as _read_exc:
                                    _rewait_read_failures += 1
                                    if _rewait_read_failures >= 5:
                                        _log(f"  ⛔ 重稳定: 温度读取连续失败 "
                                             f"{_rewait_read_failures} 次，"
                                             f"触发断点续传: {_read_exc}")
                                        raise  # 传播到 run() → _enter_recovery()
                                    if _rewait_read_failures == 1:
                                        _log(f"  ⚠ 重稳定: 温度读取失败 "
                                             f"({_read_exc})，重试中...")
                                    _time.sleep(10)
                                    continue
                                if actual_k is None:
                                    actual_k = target_k
                                stability_ctrl.add_reading(actual_k)
                                self.temperature_stabilizing.emit(target_k, actual_k)

                                elapsed = _time.time() - wait_start
                                result = stability_ctrl.check(elapsed)

                                new_sp = stability_ctrl.needs_setpoint_adjustment()
                                if new_sp is not None:
                                    self._apply_setpoint_adjustment(
                                        new_sp, stability_ctrl.current_overshoot,
                                        stability_ctrl.base_overshoot, actual_k)

                                if result.stable or result.reason == "good_enough":
                                    _log(f"  重稳定完成: {actual_k:.3f} K")
                                    self.temperature_stable.emit(target_k, actual_k)
                                    break
                                elif result.reason == "timeout":
                                    _log(f"  重稳定超时 — 以 good_enough 模式继续")
                                    break

                                poll_s = (config.fine_poll_seconds
                                          if stability_ctrl.phase.value == "fine"
                                          else config.sparse_poll_seconds)
                                _time.sleep(poll_s)
                    first_measurement_cycle = False

                    measurement_ok = True
                    deleted_any = False
                    measurement_temps = []  # 跟踪所有 pre_temp，用于 ΔT 熔断检测

                    # Apply VNA freq + S-param once per restart
                    if self._vna and self._vna_settings:
                        vs = self._vna_settings
                        self._vna.write(
                            f":SENSe:FREQuency:STARt "
                            f"{vs.get('start_freq_hz', 3e9):.0f}")
                        self._vna.write(
                            f":SENSe:FREQuency:STOP "
                            f"{vs.get('stop_freq_hz', 6e9):.0f}")
                        if vs.get("s_parameter"):
                            sp = vs["s_parameter"]
                            self._vna.write(
                                f':CALCulate:PARameter:DELete:ALL')
                            self._vna.write(
                                f':CALCulate:PARameter:DEFine:EXTended '
                                f'"{sp}","{sp}"')
                            self._vna.write(
                                f':DISPlay:WINDow1:TRACe1:FEED "{sp}"')

                    for vi, vna_dbm in enumerate(vna_powers):
                        if self._abort_flag or not measurement_ok:
                            break

                        # set VNA source power
                        if self._vna:
                            self._vna.write(f":SOURce:POWer {vna_dbm}")

                        for pi, power_mw in enumerate(self._power_list):
                            if self._abort_flag:
                                self.experiment_aborted.emit()
                                log_file.close()
                                return

                            # ---- 测量前读取温度 ----
                            pre_temp = actual_k
                            try:
                                if self._lakeshore_ctrl:
                                    t = self._lakeshore_ctrl.get_temperature("A")
                                    if t is not None:
                                        pre_temp = t
                            except Exception:
                                pass

                            self.measurement_started.emit(pre_temp, power_mw)
                            _log(f"  Measuring {vna_dbm:+d} dBm / "
                                 f"{power_mw} mW @ {pre_temp:.3f} K")
                            # Claude 监控: 测量开始
                            self._write_status(
                                phase="measuring", target_k=target_k,
                                actual_k=pre_temp, vna_dbm=vna_dbm,
                                laser_mw=power_mw)
                            measurement_temps.append(pre_temp)

                            # ---- ΔT 熔断检查: 任意两次测量 pre_temp 差 > 0.25K ----
                            if len(measurement_temps) >= 2:
                                temp_range = (max(measurement_temps)
                                              - min(measurement_temps))
                                if temp_range > self._meltdown_threshold_k:
                                    _log(
                                        f"  ⛔ 测量中温度漂移熔断: "
                                        f"max-min={temp_range:.3f}K "
                                        f"> {self._meltdown_threshold_k}K, "
                                        f"读数="
                                        f"{[f'{t:.3f}' for t in measurement_temps]}")
                                    # Claude 监控: 记录熔断
                                    self._write_status(
                                        phase="meltdown_recovery",
                                        target_k=target_k, actual_k=pre_temp)
                                    try:
                                        if self._status_writer:
                                            self._status_writer.add_issue(
                                                target_k=target_k,
                                                issue_type="meltdown",
                                                detail=f"max-min={temp_range:.3f}K",
                                                restart_count=measurement_restarts + 1,
                                            )
                                    except Exception:
                                        pass
                                    # 将当前文件移至 discarded/
                                    discarded_dir = os.path.join(
                                        self._output_dir, temp_str, "discarded")
                                    os.makedirs(discarded_dir, exist_ok=True)
                                    discarded_path = os.path.join(
                                        discarded_dir, filename)
                                    if os.path.exists(filepath):
                                        os.rename(filepath, discarded_path)
                                        _log(
                                            f"  异常数据已移至: "
                                            f"discarded/{filename}")
                                    # 放弃本轮所有已测数据，触发重启
                                    measurement_ok = False
                                    # ---- 自适应参数调整 ----
                                    self._drift_meltdown_count += 1
                                    if self._drift_meltdown_count == 1:
                                        self._settling_multiplier = float(
                                            config.meltdown_settling_multipliers[0])
                                        _log(
                                            f"  🔧 熔断 #{measurement_restarts + 1}"
                                            f" → 沉降时间 ×"
                                            f"{self._settling_multiplier:.0f}"
                                            f"（激光 {config.laser_settle_time_s * self._settling_multiplier:.0f}s"
                                            f" / 首次上电 {config.laser_first_on_settle_time_s * self._settling_multiplier:.0f}s）")
                                    elif self._drift_meltdown_count == 2:
                                        self._settling_multiplier = float(
                                            config.meltdown_settling_multipliers[1])
                                        self._meltdown_threshold_k = \
                                            config.meltdown_relaxed_threshold_k
                                        _log(
                                            f"  🔧 熔断 #{measurement_restarts + 1}"
                                            f" → 沉降时间 ×"
                                            f"{self._settling_multiplier:.0f}"
                                            f" + 阈值放宽至 "
                                            f"{self._meltdown_threshold_k}K")
                                    elif self._drift_meltdown_count == 3:
                                        _log(
                                            f"  🔧 熔断 #{measurement_restarts + 1}"
                                            f" → 进入复测模式（重测所有 VNA×laser）")
                                        self._in_retry_mode = True
                                    elif self._drift_meltdown_count > 3:
                                        _log(
                                            f"  🔧 复测熔断 "
                                            f"(#{self._drift_meltdown_count - 3}"
                                            f"/{config.retry_mode_max_meltdowns})")
                                    deleted_any = True
                                    break  # 跳出激光功率循环

                            # apply laser
                            if self._laser_ctrl:
                                # Fix 3: 跟踪激光是否首次上电，选择不同沉降时间
                                laser_was_off = self._laser_was_off
                                if power_mw == 0:
                                    self._laser_ctrl.output_off()
                                    self._laser_was_off = True
                                else:
                                    self._laser_ctrl.set_power(power_mw)
                                    self._laser_ctrl.output_on()
                                    self._laser_was_off = False
                                settle_s = (config.laser_first_on_settle_time_s
                                            if laser_was_off and power_mw > 0
                                            else config.laser_settle_time_s)
                                settle_s = settle_s * self._settling_multiplier
                                _time.sleep(settle_s)
                                # 沉降后读取并记录温度，方便诊断激光加热效应
                                try:
                                    if self._lakeshore_ctrl:
                                        t_after = self._lakeshore_ctrl.get_temperature("A")
                                        if t_after is not None:
                                            _log(f"  激光沉降后温度: {t_after:.3f} K "
                                                 f"(Δ={t_after - pre_temp:+.3f} K)")
                                except Exception:
                                    pass

                            # ---- 输出路径 ----
                            temp_str = f"{target_k:.0f}K"
                            folder = os.path.join(
                                self._output_dir, temp_str,
                                f"{vna_dbm:+d}dBm",
                                f"{power_mw:02d}mW")
                            os.makedirs(folder, exist_ok=True)
                            filename = self._find_next_filename(
                                folder, target_k, vna_dbm, power_mw, pre_temp)
                            filepath = os.path.join(folder, filename)

                            # sweep + save S2P
                            if self._vna:
                                vna_safe = filepath.replace("\\", "/")
                                self._vna.write(":INITiate:CONTinuous OFF")
                                self._vna.write(":INITiate:IMMediate")
                                try:
                                    self._vna.query("*OPC?")
                                except Exception:
                                    _time.sleep(5)
                                self._vna.write(
                                    f'MMEMory:STORe "{vna_safe}"')
                                count += 1

                            # ---- 测量后读取温度 ----
                            post_temp = pre_temp
                            try:
                                if self._lakeshore_ctrl:
                                    t = self._lakeshore_ctrl.get_temperature("A")
                                    if t is not None:
                                        post_temp = t
                            except Exception:
                                pass

                            # ---- 测量时温度监控 ----
                            temp_ok, temp_reason = self._check_measurement_temp(
                                pre_temp, post_temp, target_k,
                                laser_power_mw=power_mw)

                            if not temp_ok:
                                _log(f"  ⚠ 测量温度异常: {temp_reason}")
                                # 删除异常数据文件
                                try:
                                    if os.path.exists(filepath):
                                        os.remove(filepath)
                                        _log(f"  已删除异常数据: {filename}")
                                except Exception as e:
                                    _log(f"  删除文件失败: {e}")

                                count -= 1
                                measurement_ok = False
                                deleted_any = True
                                break  # 跳出功率循环

                            self.measurement_complete.emit(
                                post_temp, power_mw, filepath)
                            # 更新 actual_k 为最新温度
                            actual_k = post_temp

                            # ---- checkpoint: 记录已完成测量点 ----
                            self._record_measurement_point(
                                target_k, vna_dbm, power_mw, post_temp)
                            self._update_checkpoint_state(
                                temp_idx, vi, pi, post_temp, count,
                                _extended_max_wait_s, _extended_pre_wait_s)

                        if not measurement_ok:
                            break  # 跳出 VNA 功率循环

                    if measurement_ok:
                        # 测量全部通过
                        break

                    # Fix 2: 熔断重启上限检查（复测模式感知）
                    _skip_now = False
                    if self._in_retry_mode:
                        retry_meltdowns = (self._drift_meltdown_count
                                           - config.max_meltdown_restarts)
                        if retry_meltdowns > config.retry_mode_max_meltdowns:
                            _log(f"  ⛔ 复测熔断已达上限，跳过温度点 {target_k:.1f}K")
                            _skip_now = True
                    elif measurement_restarts >= config.max_meltdown_restarts:
                        _log(f"  ⛔ 熔断重启已达上限 "
                             f"({measurement_restarts}/{config.max_meltdown_restarts})，"
                             f"跳过温度点 {target_k:.1f}K")
                        _skip_now = True

                    if _skip_now:
                        # Claude 监控: 记录跳过
                        try:
                            if self._status_writer:
                                self._status_writer.add_skipped(
                                    target_k=target_k,
                                    reason="meltdown_limit",
                                    vna_power_remaining=self._vna_power_list,
                                )
                        except Exception:
                            pass
                        # 确保激光已关闭再跳至下一温度点
                        if self._laser_ctrl:
                            try:
                                self._laser_ctrl.set_power(0)
                                self._laser_ctrl.output_off()
                            except Exception:
                                pass
                        self._laser_was_off = True
                        # 需求 B: 熔断跳过计入连续问题
                        _rollback_state.record_result(temp_idx, "meltdown_skip")
                        break  # 跳出 while True → 下一温度点

                    # ---- 测量异常 → 重启稳定性（不限次数，由 max_wait 终止） ----
                    measurement_restarts += 1
                    if deleted_any:
                        _log(f"  ⚠ 测量熔断 #{measurement_restarts}"
                             f" — 重新等待温度稳定（跳过预等待）")
                    else:
                        _log(f"  ⚠ 测量重启 #{measurement_restarts}"
                             f" — 重新等待温度稳定")

                    # 重新等待稳定性（使用相同的 max_wait 超时）
                    stability_ctrl = ExperimentStabilityController(log_callback=lambda msg: _log(msg))
                    stability_ctrl.set_overshoot_learning(_overshoot_learning)
                    stability_ctrl.MAX_WAIT_SECONDS = self._max_wait_s
                    stability_ctrl.setup(
                        target_k=target_k,
                        current_temperature=actual_k,
                    )
                    if self._lakeshore_ctrl:
                        new_fixed_pid = stability_ctrl.get_fixed_pid()
                        self._lakeshore_ctrl.set_pid(
                            new_fixed_pid["p"], new_fixed_pid["i"],
                            new_fixed_pid["d"], loop=1)
                        new_sp = stability_ctrl.needs_setpoint_adjustment()
                        if new_sp is not None:
                            self._lakeshore_ctrl.set_temperature(new_sp, loop=1)

                    restart_start = _time.time()
                    _restart_read_failures = 0       # 温度读取连续失败计数（修复：静默死循环）
                    _restart_prev_phase = "sparse"    # 跟踪稳定性阶段转换
                    while True:
                        if self._abort_flag:
                            self.experiment_aborted.emit()
                            log_file.close()
                            return
                        try:
                            actual_k = self._lakeshore_ctrl.get_temperature("A") \
                                if self._lakeshore_ctrl else target_k
                            _restart_read_failures = 0  # 成功后重置
                        except Exception as _read_exc:
                            _restart_read_failures += 1
                            if _restart_read_failures >= 5:
                                _log(f"  ⛔ 重启稳定性: 温度读取连续失败 "
                                     f"{_restart_read_failures} 次，"
                                     f"触发断点续传: {_read_exc}")
                                raise  # 传播到 run() → _enter_recovery()
                            if _restart_read_failures == 1:
                                _log(f"  ⚠ 重启稳定性: 温度读取失败 "
                                     f"({_read_exc})，重试中...")
                            _time.sleep(10)
                            continue
                        if actual_k is None:
                            actual_k = target_k

                        stability_ctrl.add_reading(actual_k)
                        self.temperature_stabilizing.emit(target_k, actual_k)

                        elapsed = _time.time() - restart_start
                        result = stability_ctrl.check(elapsed)

                        # 阶段转换日志 + 监控
                        if stability_ctrl.phase.value != _restart_prev_phase:
                            _restart_prev_phase = stability_ctrl.phase.value
                            if _restart_prev_phase == "fine":
                                _log(f"  [重启稳定] 进入 Phase 2，提高轮询频率至 "
                                     f"{config.fine_poll_seconds}s")
                                self._write_status(
                                    phase="stabilizing_fine", target_k=target_k,
                                    actual_k=actual_k)

                        new_sp = stability_ctrl.needs_setpoint_adjustment()
                        if new_sp is not None:
                            self._apply_setpoint_adjustment(
                                new_sp,
                                stability_ctrl.current_overshoot,
                                stability_ctrl.base_overshoot,
                                actual_k,
                            )

                        if result.stable or result.reason == "good_enough":
                            _log(f"  重启后稳定: {actual_k:.3f} K")
                            self.temperature_stable.emit(target_k, actual_k)
                            self._write_status(
                                phase="pre_measuring", target_k=target_k,
                                actual_k=actual_k)
                            break
                        elif result.reason == "timeout":
                            _log(f"  重启超时 — 以 good_enough 模式继续")
                            self._write_status(
                                phase="pre_measuring", target_k=target_k,
                                actual_k=actual_k)
                            break

                        poll_s = (config.fine_poll_seconds
                                  if stability_ctrl.phase.value == "fine"
                                  else config.sparse_poll_seconds)
                        _time.sleep(poll_s)

                # 切换到下一个温度点前：激光功率归零
                if self._laser_ctrl:
                    self._laser_ctrl.set_power(0)
                    self._laser_ctrl.output_off()
                    _log(f"  温度点完成，激光功率 → 0 mW（准备升温）")

                # ---- 需求 B: 连续问题回退 / 正常前进 ----
                # 正常稳定通过 → 重置连续问题计数 + 记录 overshoot 学习
                if not _temp_skip_measurement and not _was_soft_pass:
                    _rollback_state.record_result(temp_idx, "stable")
                    try:
                        stability_ctrl.record_result()
                        _overshoot_learning.update(
                            stability_ctrl.get_overshoot_learning())
                        self._checkpoint_overshoot = dict(_overshoot_learning)
                        _save_overshoot_learning_incremental(
                            _overshoot_learning, __file__, _log)
                    except Exception:
                        pass  # stability_ctrl 可能已被外部重新赋值，安全忽略

                # 检查是否触发回退
                if (_rollback_state.consecutive_issues >=
                        _rollback_state.consecutive_threshold
                        and _rollback_state.first_issue_index is not None):
                    _rb_idx = _rollback_state.first_issue_index
                    _rb_temp = self._temp_list[_rb_idx]
                    _rb_max_wait, _rb_pre_wait = _rollback_state.get_rollback_params()
                    _log(f"  ↩ 连续 {_rollback_state.consecutive_issues} 次问题 → "
                         f"回退到 {_rb_temp:.1f}K "
                         f"(第 {_rb_idx + 1}/{len(self._temp_list)} 个温度点)")
                    _log(f"     max_wait +{_rb_max_wait / 60:.0f}min, "
                         f"pre_wait +{_rb_pre_wait / 60:.0f}min "
                         f"(第 {_rollback_state.rollback_count} 次回退)")

                    if _rollback_state.rollback_count >= 2:
                        # 同一回退点第二次 → 跳过，不再回退
                        _log(f"     ⚠ 第 {_rollback_state.rollback_count} 次回退，"
                             f"放弃回退，跳过 {_rb_temp:.1f}K 并继续")
                        _rollback_state.reset_after_rollback()
                        temp_idx += 1
                    else:
                        # 执行回退
                        _extended_max_wait_s = self._max_wait_s + _rb_max_wait
                        _extended_pre_wait_s = (
                            self._pre_measurement_wait_s + _rb_pre_wait)
                        _rollback_state.reset_after_rollback()
                        temp_idx = _rb_idx  # 跳回第一个问题温度点
                        continue  # 回到 while temp_idx < len(...) 顶部
                else:
                    temp_idx += 1

            # ================================================================
            # 实验完成 — GC + 内存摘要 + 长时间运行提示 + readme.txt
            # ================================================================
            end_time = datetime.now()
            _log(f"Experiment complete — {count} measurements")

            # Claude 监控: 标记实验完成
            try:
                if self._status_writer:
                    self._status_writer.set_status("completed")
            except Exception:
                pass

            # ---- 保存 overshoot 学习数据到机器层设置（不入库） ----
            if _overshoot_learning:
                try:
                    _settings = app_settings.load_settings()
                    _settings["overshoot_learning"] = {
                        str(k): v for k, v in _overshoot_learning.items()}
                    app_settings.save_settings(_settings)
                    _log(f"已保存 overshoot 学习数据: "
                         f"{len(_overshoot_learning)} 个温度点")
                except Exception as _save_err:
                    _log(f"  ⚠ 保存 overshoot 学习数据失败: {_save_err}")

            # ---- 强制垃圾回收，释放循环引用 ----
            _gc.collect()
            if mem_monitor:
                for line in mem_monitor.summary().split("\n"):
                    _log(line)

            # ---- 长时间运行建议重启 GUI ----
            elapsed_hours = (end_time - start_time).total_seconds() / 3600
            warn_hours = getattr(config, "long_experiment_warning_hours", 2.0)
            if elapsed_hours >= warn_hours:
                _log(
                    f"  💡 实验已运行 {elapsed_hours:.1f} 小时。")
                _log(
                    f"     建议重启 GUI 以释放 Python 进程累积的内存，"
                    f"避免下次实验时 OOM。")

            try:
                from readme_generator import generate_readme
                import config

                # 收集设备参数
                laser_params = {
                    "wavelength_nm": self._vna_settings.get("laser_wavelength_nm",
                        getattr(self._laser_ctrl, "_wavelength", None) or 1550.0),
                    "power_sequence_mw": self._power_list,
                }
                lakeshore_params = {
                    "setpoint_k": self._temp_list[0] if self._temp_list else "—",
                    "pid": fixed_pid if 'fixed_pid' in dir() else {},
                    "heater_range": 2,
                }
                vna_info = dict(self._vna_settings)
                vna_info["address"] = getattr(
                    self._vna, "_resource_name", "—") if self._vna else "—"

                readme_path = generate_readme(
                    output_dir=self._output_dir,
                    start_time=start_time,
                    end_time=end_time,
                    laser_params=laser_params,
                    lakeshore_params=lakeshore_params,
                    vna_params=vna_info,
                    measurement_count=count,
                    measurement_logic_version=getattr(
                        config, "MEASUREMENT_LOGIC_VERSION", ""),
                )
                _log(f"readme.txt generated: {readme_path}")
            except Exception as e:
                _log(f"⚠ readme.txt 生成失败: {e}")

            _log(f"日志文件: {log_path}")

            # ---- 实验正常完成: 删除检查点 + 清理旧 attempt 文件 ----
            CheckpointManager.delete(self._output_dir)
            self._cleanup_old_attempts(self._output_dir)

            log_file.close()
            self.experiment_finished.emit(count)

        except Exception as e:
            # 可恢复的连接错误 → 传播到外层 run() 触发断点续传
            if self._is_recoverable_error(e):
                try:
                    log_file.close()
                except Exception:
                    pass
                raise
            # 不可恢复的错误 → 终止实验
            self.experiment_error.emit(f"Experiment failed: {e}")
            try:
                log_file.close()
            except Exception:
                pass
