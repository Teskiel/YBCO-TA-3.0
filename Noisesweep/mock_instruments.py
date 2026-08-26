# -*- coding: utf-8 -*-
"""Fake instruments for noisesweep ``--dry-run`` self-test.

四条假仪器对齐真实驱动的接口子集，使 ``kid_measurement_core.run_s21`` /
``run_noise`` 与编排器能全链路空跑：

  * FakeE8257D        —— 记录频率/功率/RF 状态，提供 ``resource_name``。
  * FakePXIe4480      —— ``acquire`` 返回 SCRAPS cmplxIQ 前向模型的合成
                        I/Q（保证 SCRAPS 能稳定拟合出 f0）；``acquire_continuous``
                        产固定块数的共振点附近噪声。
  * FakeLakeShore335  —— ``get_temperature`` 指数逼近 ``set_temperature``
                        的目标，使 ``wait_for_stability`` 快速判定稳定。
  * FakeLaser         —— 记录 set_power/set_wavelength/output_off 调用。

合成谐振位置由模块级 ``RESONANCE_F0_HZ`` 控制；编排器在进入每个
(res, power) 前调 ``set_resonance_position()`` 把它放到扫描中心，保证
粗扫/精扫/噪声全部命中谐振。identity IQ 校准由
``write_identity_iq_calibration()`` 生成。
"""

import threading
import time
from typing import Iterator, Optional

import numpy as np

_RNG = np.random.default_rng(20260821)  # 确定性种子，dry-run 可复现

# =========================================================================
# 模块级共享仿真状态
# =========================================================================

RESONANCE_F0_HZ = 4.5e9   # 当前仿真谐振位置（Hz）
RESONANCE_QI = 2000.0      # 内部 Q
RESONANCE_QC = 400.0       # 耦合 Q
RESONANCE_NOISE_SIGMA = 1e-3  # 每采样点 I/Q 噪声（V）


def set_resonance_position(f0_hz: float) -> None:
    """把合成谐振放到指定频率（编排器每 (res, power) 设置一次）。"""
    global RESONANCE_F0_HZ
    RESONANCE_F0_HZ = float(f0_hz)


def get_resonance_position() -> float:
    return float(RESONANCE_F0_HZ)


def scraps_cmplx_s21(freqs, f0_hz, qi=RESONANCE_QI, qc=RESONANCE_QC,
                     df_hz=0.0) -> np.ndarray:
    """SCRAPS cmplxIQ 前向模型（与 fitsS21.cmplxIQ_fit 逐式一致）。"""
    freqs = np.asarray(freqs, dtype=float)
    fs = f0_hz + df_hz
    ff = (freqs - fs) / fs
    fm = freqs[int(np.round((len(freqs) - 1) / 2.0))]
    ffm = (freqs - fm) / fm
    q0 = 1.0 / (1.0 / qi + 1.0 / qc)
    gain = np.ones_like(freqs)
    pgain = np.ones_like(freqs, dtype=np.complex128)
    return gain * pgain * (1.0 / qi + 1j * 2.0 * (ff + df_hz / fs)) \
        / (1.0 / q0 + 1j * 2.0 * ff)


def write_identity_iq_calibration(path, frequency_hz=1.0e9) -> None:
    """写一份 identity IQ 校准汇总 txt（I0=0,Q0=0,A_I=1,A_Q=1,q=0）。

    ``IQCalibrationTable.load`` 用 ``np.loadtxt(comments="#")`` 读取，
    前 6 列固定为 frequency,I0,Q0,A_I,A_Q,rotation_q。
    """
    import numpy as _np

    header = (
        "# identity IQ calibration (mock)\n"
        "# frequency_hz  I0_V  Q0_V  A_I_V  A_Q_V  rotation_q_rad\n"
    )
    row = "{:.12e}  0.0  0.0  1.0  1.0  0.0\n".format(float(frequency_hz))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + row)


# =========================================================================
# 假仪器
# =========================================================================

class FakeAcquisitionResult:
    """对齐 PXIE4480.AcquisitionResult 的返回壳。"""

    def __init__(self, data, channels, requested_sample_rate, actual_sample_rate):
        self.data = np.asarray(data, dtype=float)
        self.channels = tuple(channels)
        self.requested_sample_rate = float(requested_sample_rate)
        self.actual_sample_rate = float(actual_sample_rate)

    @property
    def sample_count(self) -> int:
        return self.data.shape[1]

    @property
    def duration(self) -> float:
        return self.data.shape[1] / self.actual_sample_rate


class FakeE8257D:
    """记录调用的信号源假体；``resource_name`` 供 HDF5 元数据。"""

    def __init__(self):
        self.resource_name = "mock:E8257D"
        self.current_frequency_hz = 0.0
        self.current_power_dbm = 0.0
        self.rf = False
        self.call_log = []  # [(op, value), ...]

    def set_frequency_hz(self, frequency_hz: float) -> None:
        self.current_frequency_hz = float(frequency_hz)
        self.call_log.append(("set_frequency_hz", float(frequency_hz)))

    def set_frequency_ghz(self, frequency_ghz: float) -> None:
        self.set_frequency_hz(frequency_ghz * 1e9)

    def set_power_dbm(self, power_dbm: float) -> None:
        self.current_power_dbm = float(power_dbm)
        self.call_log.append(("set_power_dbm", float(power_dbm)))

    def rf_on(self) -> None:
        self.rf = True
        self.call_log.append(("rf_on", None))

    def rf_off(self) -> None:
        self.rf = False
        self.call_log.append(("rf_off", None))

    def wait_until_complete(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def read_status(self) -> dict:
        return {
            "frequency_ghz": self.current_frequency_hz / 1e9,
            "power_dbm": self.current_power_dbm,
            "rf_on": self.rf,
        }

    def disconnect(self, turn_rf_off: bool = True) -> None:
        if turn_rf_off:
            self.rf = False


class FakePXIe4480:
    """合成 I/Q 采集卡假体：读共享 FakeE8257D 当前频率生成谐振响应。"""

    def __init__(self, source: FakeE8257D, config: dict):
        self.source = source
        self._config_channels = list(config["channels"])
        self.channels = tuple("mock/ai{}".format(i) for i in self._config_channels)
        self.sample_rate = float(config["sample_rate"])
        self._stop = threading.Event()
        self.acquire_calls = 0
        self.continuous_blocks = 0

    def _synthesize_point(self, frequency_hz: float, count: int) -> np.ndarray:
        """在频率 frequency_hz 处的 I/Q 时序（2 行 × count 列）。"""
        s = scraps_cmplx_s21(np.asarray([frequency_hz]), get_resonance_position())
        base = complex(s[0])
        i = np.full(count, base.real, dtype=float)
        q = np.full(count, base.imag, dtype=float)
        i += _RNG.normal(0.0, RESONANCE_NOISE_SIGMA, count)
        q += _RNG.normal(0.0, RESONANCE_NOISE_SIGMA, count)
        return np.vstack((i, q))

    def acquire(self, sample_count: int, trigger_mode: str = "IMMEDIATE",
                trigger_source: Optional[str] = None,
                trigger_edge: str = "RISING",
                timeout: Optional[float] = None) -> FakeAcquisitionResult:
        self.acquire_calls += 1
        count = int(sample_count)
        data = self._synthesize_point(self.source.current_frequency_hz, count)
        return FakeAcquisitionResult(
            data, self.channels, self.sample_rate, self.sample_rate
        )

    def acquire_continuous(
        self, samples_per_read: int = 10_000, buffer_seconds: float = 10.0,
        trigger_mode: str = "IMMEDIATE", trigger_source: Optional[str] = None,
        trigger_edge: str = "RISING", read_timeout: float = 10.0,
    ) -> Iterator[FakeAcquisitionResult]:
        """产固定数量的共振点噪声块；噪声为偏置点（=S21(freq)，即画布上的
        noise test point）附近的小幅高斯扰动（σ 与 _synthesize_point 一致），
        模拟真实测量的"小簇贴近 test point"形态。

        注：旧实现让噪声绕圆心随机游走（σ=0.5 rad/点），归一化后必然画整圆、
        相位缠绕、低频 PSD 巨大——与真实噪声形态完全不符，且会误导基于 mock
        的验证（dry-run 检查若只看文件结构则永远"通过"）。
        """
        self._stop.clear()
        block = int(samples_per_read)
        total = 0
        freq = self.source.current_frequency_hz
        s = complex(scraps_cmplx_s21(np.asarray([freq]), get_resonance_position())[0])
        while not self._stop.is_set():
            self.continuous_blocks += 1
            i = np.full(block, s.real) + _RNG.normal(0.0, RESONANCE_NOISE_SIGMA, block)
            q = np.full(block, s.imag) + _RNG.normal(0.0, RESONANCE_NOISE_SIGMA, block)
            result = FakeAcquisitionResult(
                np.vstack((i, q)), self.channels, self.sample_rate, self.sample_rate
            )
            total += block
            yield result
            if total >= 1_000_000_000:  # 防呆：无限跑上限
                break

    def stop_continuous(self) -> None:
        self._stop.set()


class FakeLakeShore335:
    """温度控制器假体：指数逼近 setpoint，使稳定判据快速通过。"""

    is_fixed = True   # mock 视为未连接 → 手动温度模式（目标即实测，不读假传感器）

    def __init__(self, visa_address: str = "mock:ASRL4", start_k: float = 77.0):
        self.visa_address = visa_address
        self._temp = float(start_k)
        self._target = float(start_k)
        self._tau = 0.01  # 每次调用逼近 1-1/e
        self.call_log = []

    def get_temperature(self, channel: str = "A") -> float:
        # 指数逼近 target，加 0.5 mK 噪声（不影响稳定判据收敛）
        self._temp += (self._target - self._temp) * (1.0 - np.exp(-1.0 / self._tau))
        self._temp += float(_RNG.normal(0.0, 0.0005))
        return self._temp

    def set_temperature(self, setpoint: float, loop: int = 1) -> None:
        self._target = float(setpoint)
        self.call_log.append(("set_temperature", float(setpoint)))

    def get_setpoint(self, loop: int = 1) -> float:
        return self._target

    def set_heater_range(self, output: int, range_level: int) -> None:
        self.call_log.append(("set_heater_range", (output, range_level)))

    def get_heater_percent(self, output: int = 1) -> float:
        return 0.0

    def set_pid(self, p: float, i: float, d: float, loop: int = 1) -> None:
        self.call_log.append(("set_pid", (p, i, d)))

    def all_heaters_off(self) -> None:
        self.call_log.append(("all_heaters_off", None))

    def close(self) -> None:
        self.call_log.append(("close", None))


class FakeLaser:
    """激光器假体：记录调用，get_status 返回记录值。"""

    def __init__(self, resource_address: str = "mock:TCPIP"):
        self.resource_address = resource_address
        self.power_mw = 0.0
        self.wavelength_nm = 1550.0
        self.output = False
        self.connected = True
        self.call_log = []

    def connect(self) -> bool:
        self.connected = True
        return True

    def set_power(self, power_mw: float) -> bool:
        self.power_mw = float(power_mw)
        self.output = float(power_mw) > 0
        self.call_log.append(("set_power", float(power_mw)))
        return True

    def set_power_mw(self, power_mw: float) -> bool:
        return self.set_power(power_mw)

    def set_wavelength(self, wavelength_nm: float) -> bool:
        self.wavelength_nm = float(wavelength_nm)
        self.call_log.append(("set_wavelength", float(wavelength_nm)))
        return True

    def output_on(self) -> None:
        self.output = True

    def output_off(self) -> None:
        self.output = False
        self.power_mw = 0.0
        self.call_log.append(("output_off", None))

    def get_power(self) -> float:
        return self.power_mw

    def get_wavelength(self) -> float:
        return self.wavelength_nm

    def get_status(self) -> dict:
        return {
            "connected": self.connected,
            "power_mw": self.power_mw,
            "wavelength_nm": self.wavelength_nm,
            "output_enabled": self.output,
        }

    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.connected = False
        self.call_log.append(("close", None))
