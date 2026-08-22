# -*- coding: utf-8 -*-
"""
Created on Wed Aug 12 13:48:17 2026

@author: smlab
"""

"""Keysight/Agilent E8257D 的 VISA/SCPI 控制类。"""

from typing import Optional, Tuple

import pyvisa


class E8257D:
    """通过 VISA 控制 E8257D PSG 信号源。"""

    def __init__(self, resource_name: str, timeout_ms: int = 5000):
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self._resource_manager: Optional[pyvisa.ResourceManager] = None
        self._instrument = None

    @property
    def is_connected(self) -> bool:
        return self._instrument is not None

    def connect(self) -> str:
        """连接仪器并返回 *IDN? 信息。"""
        if self.is_connected:
            return self.identify()

        self._resource_manager = pyvisa.ResourceManager()
        try:
            self._instrument = self._resource_manager.open_resource(
                self.resource_name
            )
            self._instrument.timeout = self.timeout_ms
            self._instrument.write_termination = "\n"
            self._instrument.read_termination = "\n"
            return self.identify()
        except Exception:
            self.disconnect(turn_rf_off=False)
            raise

    def _require_connection(self):
        if not self.is_connected:
            raise RuntimeError("仪器尚未连接，请先调用 connect()。")

    def write(self, command: str):
        self._require_connection()
        self._instrument.write(command)

    def query(self, command: str) -> str:
        self._require_connection()
        return self._instrument.query(command).strip()

    def identify(self) -> str:
        return self.query("*IDN?")

    def reset(self):
        self.write("*RST;*WAI")

    def clear_status(self):
        self.write("*CLS")

    def set_frequency_hz(self, frequency_hz: float):
        if frequency_hz <= 0:
            raise ValueError("频率必须大于 0 Hz。")
        self.write(f"FREQ:CW {frequency_hz:.12g} HZ")

    def set_frequency_ghz(self, frequency_ghz: float):
        self.set_frequency_hz(frequency_ghz * 1e9)

    def get_frequency_hz(self) -> float:
        return float(self.query("FREQ:CW?"))

    def get_frequency_ghz(self) -> float:
        return self.get_frequency_hz() / 1e9

    def set_power_dbm(self, power_dbm: float):
        self.write(f"POW:LEV:IMM:AMPL {power_dbm:.6g} DBM")

    def get_power_dbm(self) -> float:
        return float(self.query("POW:LEV:IMM:AMPL?"))

    def set_rf_output(self, enabled: bool):
        self.write("OUTP ON" if enabled else "OUTP OFF")

    def rf_on(self):
        self.set_rf_output(True)

    def rf_off(self):
        self.set_rf_output(False)

    def get_rf_output(self) -> bool:
        return bool(int(float(self.query("OUTP?"))))

    def wait_until_complete(self):
        self.query("*OPC?")

    def get_error(self) -> str:
        return self.query("SYST:ERR?")

    def read_status(self) -> dict:
        return {
            "frequency_ghz": self.get_frequency_ghz(),
            "power_dbm": self.get_power_dbm(),
            "rf_on": self.get_rf_output(),
        }

    def disconnect(self, turn_rf_off: bool = True):
        """关闭 VISA 会话；默认先关闭 RF 输出。"""
        if self._instrument is not None:
            try:
                if turn_rf_off:
                    self._instrument.write("OUTP OFF")
            finally:
                self._instrument.close()
                self._instrument = None

        if self._resource_manager is not None:
            self._resource_manager.close()
            self._resource_manager = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.disconnect(turn_rf_off=True)


def list_visa_resources() -> Tuple[str, ...]:
    """返回电脑当前能够识别的全部 VISA 地址。"""
    resource_manager = pyvisa.ResourceManager()
    try:
        return tuple(resource_manager.list_resources())
    finally:
        resource_manager.close()