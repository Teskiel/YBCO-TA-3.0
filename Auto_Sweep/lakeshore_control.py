# -*- coding: utf-8 -*-
"""
Lake Shore Model 335 Temperature Controller Driver
===================================================
Reusable driver for the Lake Shore 335 cryogenic temperature controller.

Communicates via RS-232 serial (ASRL) or GPIB. Handles:
  - Temperature readout (channels A, B, C, D)
  - Setpoint control (Loop 1 / Loop 2)
  - Heater range and heater output percentage
  - PID parameter read/write
  - Safety: all-heaters-off

Serial settings (auto-configured for ASRL):
  Baud 57600, 7 data bits, odd parity, 1 stop bit

Usage:
    from lakeshore_control import LakeShore335

    ls = LakeShore335("ASRL4::INSTR")
    print(ls.get_temperature("A"))
    ls.set_temperature(50.0, loop=1)
    ls.set_pid(100, 5, 0, loop=1)
"""

from typing import Optional, Tuple

import pyvisa
from pyvisa.constants import Parity, StopBits


class LakeShore335:
    """PyVISA driver for the Lake Shore Model 335 temperature controller."""

    RANGE_NAMES = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}

    def __init__(
        self,
        visa_address: str,
        resource_manager: Optional[pyvisa.ResourceManager] = None,
    ):
        if not visa_address:
            raise ValueError("visa_address must not be empty")

        self.visa_address = visa_address
        self.resource_manager = resource_manager or pyvisa.ResourceManager()
        self.device = self.resource_manager.open_resource(visa_address)

        # Serial port configuration for Model 335 USB
        if "ASRL" in visa_address.upper():
            self.device.baud_rate = 57600
            self.device.data_bits = 7
            self.device.parity = Parity.odd
            self.device.stop_bits = StopBits.one
            self.device.timeout = 3000
            self.device.read_termination = "\n"
            self.device.write_termination = "\n"
        else:
            self.device.timeout = 3000
            self.device.read_termination = "\n"
            self.device.write_termination = "\n"

        self.identity = self.query("*IDN?")

    # ---- low-level I/O ----

    def write(self, command: str) -> None:
        self.device.write(command)

    def query(self, command: str) -> str:
        return self.device.query(command).strip()

    # ---- temperature ----

    def get_temperature(self, channel: str = "A") -> float:
        """Read temperature from input channel (A, B, C, D)."""
        return float(self.query(f"KRDG? {channel}"))

    # ---- setpoint ----

    def set_temperature(self, setpoint: float, loop: int = 1) -> None:
        """Set target temperature for control loop (1 or 2)."""
        self.write(f"SETP {loop},{setpoint}")

    def get_setpoint(self, loop: int = 1) -> float:
        """Read current setpoint for control loop."""
        return float(self.query(f"SETP? {loop}"))

    # ---- heater ----

    def set_heater_range(self, output: int, range_level: int) -> None:
        """Set heater range: 0=Off, 1=Low, 2=Medium, 3=High."""
        self.write(f"RANGE {output},{range_level}")

    def get_heater_range(self, output: int = 1) -> int:
        """Read heater range setting."""
        return int(float(self.query(f"RANGE? {output}")))

    def get_heater_percent(self, output: int = 1) -> float:
        """Read heater output percentage."""
        return float(self.query(f"HTR? {output}"))

    # ---- PID ----

    def set_pid(self, p: float, i: float, d: float, loop: int = 1) -> None:
        """Set PID parameters for control loop."""
        self.write(f"PID {loop},{p},{i},{d}")

    def get_pid(self, loop: int = 1) -> Tuple[float, float, float]:
        """Read PID parameters. Returns (P, I, D) tuple."""
        values = self.query(f"PID? {loop}").split(",")
        if len(values) != 3:
            raise RuntimeError(f"Unexpected PID response: {values!r}")
        return tuple(map(float, values))  # type: ignore[return-value]

    # ---- safety ----

    def all_heaters_off(self) -> None:
        """Emergency: set both heater output ranges to OFF."""
        self.set_heater_range(1, 0)
        self.set_heater_range(2, 0)

    # ---- cleanup ----

    def close(self) -> None:
        """Close the VISA session."""
        try:
            self.device.close()
        except Exception:
            pass

    def reconnect(self) -> bool:
        """重新连接 VISA 会话（用于 GPIB/USB 连接丢失后的恢复）。

        关闭旧会话，重新打开资源，恢复串口参数。
        失败时返回 False，调用方可据此决定是否重试。

        Returns:
            True 如果重连成功，False 如果失败。
        """
        import time

        # 1. 关闭旧会话
        try:
            self.device.close()
        except Exception:
            pass

        # 2. 短暂延迟让硬件复位
        time.sleep(1.0)

        # 3. 重新打开资源
        try:
            self.device = self.resource_manager.open_resource(self.visa_address)
        except Exception:
            return False

        # 4. 恢复串口参数
        try:
            if "ASRL" in self.visa_address.upper():
                self.device.baud_rate = 57600
                self.device.data_bits = 7
                self.device.parity = pyvisa.constants.Parity.odd
                self.device.stop_bits = pyvisa.constants.StopBits.one
                self.device.timeout = 3000
                self.device.read_termination = "\n"
                self.device.write_termination = "\n"
            else:
                self.device.timeout = 3000
                self.device.read_termination = "\n"
                self.device.write_termination = "\n"

            # 5. 验证连接 — 尝试读取身份
            self.identity = self.device.query("*IDN?").strip()
            return True
        except Exception:
            return False

    def hard_reconnect(self) -> bool:
        """深度重连：重建整个 PyVISA ResourceManager（用于驱动进程被 OOM 杀后恢复）。

        当普通 reconnect() 失败时调用此方法 — 关闭 ResourceManager，
        创建全新的 VISA 资源管理器实例，重新打开设备。

        Returns:
            True 如果深度重连成功，False 如果失败。
        """
        import time
        import gc

        # 1. 彻底关闭旧连接
        try:
            self.device.close()
        except Exception:
            pass

        # 2. 关闭旧的 ResourceManager
        try:
            self.resource_manager.close()
        except Exception:
            pass

        # 3. 强制 GC 释放旧 VISA 资源
        gc.collect()
        time.sleep(2.0)

        # 4. 创建全新的 ResourceManager
        try:
            self.resource_manager = pyvisa.ResourceManager()
        except Exception:
            return False

        # 5. 重新打开设备
        try:
            self.device = self.resource_manager.open_resource(self.visa_address)
        except Exception:
            return False

        # 6. 恢复串口参数
        try:
            if "ASRL" in self.visa_address.upper():
                self.device.baud_rate = 57600
                self.device.data_bits = 7
                self.device.parity = pyvisa.constants.Parity.odd
                self.device.stop_bits = pyvisa.constants.StopBits.one
                self.device.timeout = 3000
                self.device.read_termination = "\n"
                self.device.write_termination = "\n"
            else:
                self.device.timeout = 3000
                self.device.read_termination = "\n"
                self.device.write_termination = "\n"

            self.identity = self.device.query("*IDN?").strip()
            return True
        except Exception:
            return False

    def probe_connection(self) -> bool:
        """主动探测 VISA 连接是否存活。

        发送 *IDN? 查询验证双向通信。与 get_temperature_safe 不同，
        此方法不读取温度，仅验证连接活性，开销更小。

        Returns:
            True 如果连接正常，False 如果已断开。
        """
        try:
            result = self.device.query("*IDN?")
            return bool(result and result.strip())
        except Exception:
            return False

    def get_temperature_safe(self, channel: str = "A",
                             retries: int = 2,
                             retry_delay_s: float = 3.0) -> float | None:
        """安全读取温度，含自动重试 + 陈旧数据检测 + 深度重连。

        三级升级策略：
          1. 轻微故障: 等待 retry_delay_s 后重试
          2. 持续故障: 调用 reconnect() 重置 VISA 会话
          3. 重连失败: 调用 hard_reconnect() 重建 ResourceManager

        同时追踪连续相同返回值 — 如果连续 3 次返回完全相同的值
        （浮点相等），视为陈旧数据，触发主动探测 + 重连。

        Args:
            channel: 输入通道 (A/B/C/D)
            retries: 重试次数（默认 2）
            retry_delay_s: 重试前等待秒数（默认 3.0）

        Returns:
            温度值 (K) 或 None
        """
        import time

        # 陈旧数据追踪（模块级：跨调用持久）
        if not hasattr(self, "_stale_tracker"):
            self._stale_tracker = {"last_value": None, "count": 0,
                                   "_consecutive_none": 0}

        for attempt in range(retries + 1):
            try:
                value = float(self.query(f"KRDG? {channel}"))

                # --- 陈旧数据检测 ---
                last = self._stale_tracker["last_value"]
                if last is not None and value == last:
                    self._stale_tracker["count"] += 1
                    if self._stale_tracker["count"] >= 3:
                        # 连续 3 次相同值 → 可能是缓冲回放，触发主动探测
                        self._stale_tracker["count"] = 0
                        if not self.probe_connection():
                            # 连接确实断了 → 触发重连
                            if not self.reconnect():
                                self.hard_reconnect()
                            # 重连后重新读取
                            time.sleep(retry_delay_s)
                            continue
                else:
                    self._stale_tracker["last_value"] = value
                    self._stale_tracker["count"] = 0

                self._stale_tracker["_consecutive_none"] = 0
                return value

            except Exception:
                self._stale_tracker["_consecutive_none"] += 1

                if attempt < retries:
                    time.sleep(retry_delay_s)
                    # 第 1 次失败 → reconnect; 第 2 次 → hard_reconnect
                    if attempt == retries - 2:
                        if not self.reconnect():
                            self.hard_reconnect()
                    elif attempt == retries - 1:
                        self.hard_reconnect()
                else:
                    # 全部重试+重连失败
                    self._stale_tracker["last_value"] = None
                    self._stale_tracker["count"] = 0
                    return None

        return None

    @property
    def consecutive_read_failures(self) -> int:
        """返回 get_temperature_safe 连续返回 None 的次数。"""
        if not hasattr(self, "_stale_tracker"):
            return 0
        return self._stale_tracker.get("_consecutive_none", 0)


# =========================================================================
# Duck-typing helper functions
# =========================================================================
# These functions work with *any* object that looks like a LakeShore 335
# driver — whether it's the LakeShore335 class above, the external
# Lakeshore335 pip package, or the fallback class from power_sweep_auto.py.
#
# They try multiple method names and fall back to raw VISA writes, so
# they survive API variations across different driver versions.
# =========================================================================

def configure_lakeshore_serial(temp_reader, resource_string: str = None):
    """Set RS-232 parameters if the resource is a serial (ASRL) connection.

    Args:
        temp_reader: a LakeShore driver object
        resource_string: VISA address (defaults to config.resource_lakeshore)
    """
    if resource_string is None:
        from config import resource_lakeshore as resource_string

    if "ASRL" not in resource_string.upper():
        return

    for attr in ["inst", "visa", "device", "ser", "com"]:
        if hasattr(temp_reader, attr):
            target = getattr(temp_reader, attr)
            if hasattr(target, "baud_rate"):
                target.baud_rate = 57600
                target.data_bits = 7
                target.parity = pyvisa.constants.Parity.odd
                target.read_termination = "\n"
                target.write_termination = "\n"


def _raw_lakeshore_handle(temp_reader):
    """Find the raw VISA handle on a LakeShore driver object.

    Introspects common attribute names and returns the first one
    that has a ``.write()`` method.
    """
    for attr in ["inst", "visa", "device", "ser", "com"]:
        if hasattr(temp_reader, attr):
            target = getattr(temp_reader, attr)
            if hasattr(target, "write"):
                return target
    return None


def set_lakeshore_temperature(temp_reader, target_k: float):
    """Set temperature setpoint on *any* LakeShore driver.

    Tries method names in order: set_temperature, set_setpoint,
    setpoint, set_temperature_setpoint.  Falls back to raw VISA
    ``SETP 1,{target_k}``.
    """
    method_names = [
        "set_temperature",
        "set_setpoint",
        "setpoint",
        "set_temperature_setpoint",
    ]

    print(f"[DEBUG] Setting LakeShore target: {target_k:.3f} K")

    for method_name in method_names:
        method = getattr(temp_reader, method_name, None)
        if callable(method):
            try:
                method(target_k)
            except TypeError:
                method(1, target_k)
            return

    raw = _raw_lakeshore_handle(temp_reader)
    if raw is not None:
        print(f"[DEBUG] Using raw VISA to set target: {target_k:.3f} K")
        raw.write(f"SETP 1,{target_k}")
        return

    raise AttributeError(
        "Could not find a LakeShore setpoint method or raw VISA handle."
    )


def set_lakeshore_pid(temp_reader, p: float, i: float, d: float):
    """Set PID parameters on *any* LakeShore driver.

    Tries ``temp_reader.set_pid(p, i, d)`` first, then falls back
    to raw VISA ``PID 1,{p},{i},{d}``.
    """
    print(f"[DEBUG] Setting LakeShore PID: P={p}, I={i}, D={d}")

    if hasattr(temp_reader, "set_pid"):
        try:
            temp_reader.set_pid(p, i, d)
            return
        except Exception as e:
            print(f"[DEBUG] set_pid failed: {e}")

    raw = _raw_lakeshore_handle(temp_reader)
    if raw is not None:
        print(f"[DEBUG] Using raw VISA to set PID: P={p}, I={i}, D={d}")
        raw.write(f"PID 1,{p},{i},{d}")
        return

    print(f"[Warning] Could not set PID: P={p}, I={i}, D={d}")


def get_lakeshore_temperature(temp_reader) -> float:
    """Read temperature from *any* LakeShore driver.

    Tries ``.get_temperature_safe()`` first (含自动重试+重连),
    then ``.get_temperature()``, then raw ``KRDG? A`` query.
    Returns None if all fail.
    """
    # 优先使用安全方法（含自动重试+重连）
    try:
        result = temp_reader.get_temperature_safe()
        if result is not None:
            return result
    except Exception:
        pass

    # 兼容旧版 get_temperature()
    try:
        return float(temp_reader.get_temperature())
    except Exception:
        pass

    # 原始 VISA 查询
    try:
        return float(temp_reader.query("KRDG? A"))
    except Exception:
        pass

    return None
