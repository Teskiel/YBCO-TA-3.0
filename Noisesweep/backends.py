# -*- coding: utf-8 -*-
"""noisesweep 拔插边界：温控 / 激光 / 信号源 / DAQ 后端工厂。

三类后端（config 的 ``backend`` 字段）：
  * ``"autosweep"``（默认）：``sys.path`` 插入 ``autosweep_dir`` 后直接
    import YBCO_TA 的 ``lakeshore_control.LakeShore335`` /
    ``laser_driver.LaserController`` —— 复用单一事实源。
  * ``"standalone"``：本目录自包含拷贝 ``lakeshore335_control`` /
    ``laser_control`` —— 不依赖 YBCO_TA 仓库。
  * ``"mock"``：``mock_instruments`` 假仪器 —— ``--dry-run`` 全链路空跑。

两个真实驱动的 __init__ 只依赖 pyvisa、不读 autosweep 的 config.py，
地址由 noisesweep config 显式传入，无隐式耦合。
"""

import sys
import time
from typing import Callable, Optional, Protocol


# =========================================================================
# 协议（鸭子类型，不强制继承）
# =========================================================================

class TemperatureBackend(Protocol):
    def get_temperature(self, channel: str = "A") -> float: ...
    def set_temperature(self, setpoint_k: float, loop: int = 1) -> None: ...
    def all_heaters_off(self) -> None: ...
    def close(self) -> None: ...


class LaserBackend(Protocol):
    def set_power(self, power_mw: float) -> bool: ...
    def set_wavelength(self, wavelength_nm: float) -> bool: ...
    def output_off(self) -> None: ...
    def close(self) -> None: ...


# =========================================================================
# 无硬件后端（激光/温控可选）：地址为空时工厂返回这些 no-op 实例
# =========================================================================


class NullLaser:
    """无激光硬件时的 no-op 后端。laser_visa_address 为空时由工厂返回。

    标记属性 is_null=True，noisesweep.py 用 getattr(laser, "is_null", False) 判别，
    避免对未连接的激光器产生"假成功"。
    """

    is_null = True
    resource_address = None
    connected = True

    def set_power(self, power_mw: float) -> bool:
        return True

    def set_power_mw(self, power_mw: float) -> bool:
        return True

    def set_wavelength(self, wavelength_nm: float) -> bool:
        return True

    def output_on(self) -> None:
        pass

    def output_off(self) -> None:
        pass

    def connect(self) -> bool:
        return True

    def is_connected(self) -> bool:
        return True

    def get_power(self) -> float:
        return 0.0

    def get_status(self) -> dict:
        return {}

    def close(self) -> None:
        pass


class FixedTemperature:
    """无温控硬件时的固定温度后端。lakeshore_visa_address 为空时由工厂返回。

    标记属性 is_fixed=True；get_temperature 恒返回固定值。noisesweep.py 用
    getattr(temp, "is_fixed", False) 判别，并在编排层跳过 set_temperature /
    wait_for_stability。
    """

    is_fixed = True
    identity = "FixedTemperature(no hardware)"

    def __init__(self, fixed_k, log=None):
        self._fixed_k = float(fixed_k)
        if log is not None:
            log.warning("[backend] 无温控，使用 FixedTemperature(%.2f K)", self._fixed_k)

    def get_temperature(self, channel: str = "A") -> float:
        return self._fixed_k

    def get_setpoint(self, loop: int = 1) -> float:
        return self._fixed_k

    def set_temperature(self, setpoint_k: float, loop: int = 1) -> None:
        pass

    def set_heater_range(self, loop: int, heater_range: int) -> None:
        pass

    def get_heater_percent(self, loop: int = 1) -> float:
        return 0.0

    def set_pid(self, loop: int, p: float, i: float, d: float) -> None:
        pass

    def all_heaters_off(self) -> None:
        pass

    def close(self) -> None:
        pass


# =========================================================================
# 工厂
# =========================================================================

_KINDS = ("autosweep", "standalone", "mock")


def _check_kind(kind: str) -> str:
    kind = str(kind).lower()
    if kind not in _KINDS:
        raise ValueError(
            "backend 必须是 {} 之一，得到 {!r}".format(" / ".join(_KINDS), kind)
        )
    return kind


def _import_autosweep_drivers(autosweep_dir) -> tuple:
    """从 YBCO_TA/Auto_Sweep 目录动态导入两个驱动模块。"""
    sys.path.insert(0, str(autosweep_dir))
    import lakeshore_control
    import laser_driver
    return lakeshore_control, laser_driver


def make_temperature_backend(kind: str, config: dict, log=None):
    """构建温控后端。config 需含 backend / autosweep_dir / lakeshore_visa_address。

    log 可传入 logging.Logger（连接失败时打警告）。
    """
    kind = _check_kind(kind)
    address = config.get("lakeshore_visa_address")

    if kind == "mock":
        backend = __import__(
            "mock_instruments", fromlist=["FakeLakeShore335"]
        ).FakeLakeShore335(
            visa_address=address or "mock:ASRL4",
            start_k=float(config.get("mock_start_temperature_k", 77.0)),
        )
        if log:
            log.info("[backend=mock] 温控使用 FakeLakeShore335")
        return backend

    # 无硬件路径：地址为空 → 固定温度后端（提前于真驱动 import，不依赖 Auto_Sweep）
    if not address:
        return FixedTemperature(
            float(config.get("fixed_temperature_k", 77.0)), log=log)

    if kind == "standalone":
        from lakeshore335_control import LakeShore335
    else:
        lakeshore_control, _ = _import_autosweep_drivers(config["autosweep_dir"])
        LakeShore335 = lakeshore_control.LakeShore335

    try:
        backend = LakeShore335(address)
    except Exception as exc:
        if log:
            log.error("连接温控失败 (%s): %s", address, exc)
        raise
    if log:
        log.info("[backend=%s] 温控已连接: %s", kind, backend.identity)
    return backend


def make_laser_backend(kind: str, config: dict, log=None):
    """构建激光后端。config 需含 backend / autosweep_dir / laser_visa_address。"""
    kind = _check_kind(kind)
    address = config.get("laser_visa_address")

    if kind == "mock":
        backend = __import__(
            "mock_instruments", fromlist=["FakeLaser"]
        ).FakeLaser(resource_address=address or "mock:TCPIP")
        if log:
            log.info("[backend=mock] 激光使用 FakeLaser")
        return backend

    # 无硬件路径：地址为空 → NullLaser no-op 后端
    if not address:
        if log:
            log.warning("[backend=%s] laser_visa_address 为空 → 使用 NullLaser（跳过激光控制）", kind)
        return NullLaser()

    if kind == "standalone":
        from laser_control import LaserController
    else:
        _, laser_driver = _import_autosweep_drivers(config["autosweep_dir"])
        LaserController = laser_driver.LaserController

    backend = LaserController(address)
    if not backend.connect_with_retry(max_attempts=3, base_delay_s=2.0):
        raise RuntimeError("激光器连接失败: {}".format(address))
    if log:
        log.info("[backend=%s] 激光已连接: %s", kind, address)
    return backend


# =========================================================================
# 信号源 + DAQ（S21/噪声的激励与采集）
# =========================================================================

def make_source(kind: str, config: dict, log=None):
    """构建 E8257D 信号源（或假信号源），已连接。

    kind == "mock" 时返回 mock_instruments.FakeE8257D；否则
    用 E8257D_controller.E8257D 打开 e8257d_visa_address。
    """
    kind = _check_kind(kind)
    if kind == "mock":
        source = __import__(
            "mock_instruments", fromlist=["FakeE8257D"]
        ).FakeE8257D()
        if log:
            log.info("[backend=mock] 信号源使用 FakeE8257D")
        return source

    from E8257D_controller import E8257D

    address = config.get("e8257d_visa_address")
    if not address:
        raise ValueError("config 缺少 e8257d_visa_address")
    source = E8257D(address)
    try:
        source.connect()
    except Exception as exc:
        raise RuntimeError("E8257D 连接失败 {}: {}".format(address, exc))
    if not source.is_connected:
        raise RuntimeError("E8257D 未连接: {}".format(address))
    if log:
        log.info("[backend=%s] E8257D 已连接: %s", kind, address)
    return source


def make_daq_factory(kind: str, config: dict, source,
                     log=None) -> Callable[[], object]:
    """构建 DAQ 工厂（每次测量新建）。kind == "mock" 时用 FakePXIe4480。"""
    kind = _check_kind(kind)
    if kind == "mock":
        def _mock_daq():
            return __import__(
                "mock_instruments", fromlist=["FakePXIe4480"]
            ).FakePXIe4480(source, config)
        return _mock_daq

    from PXIE4480_controller import PXIe4480

    def _real_daq():
        return PXIe4480(
            device_name=config["pxie_device_name"],
            channels=config["channels"],
            sample_rate=config["sample_rate"],
            voltage_range=config["voltage_range"],
            coupling=config["coupling"],
            terminal_config="DIFFERENTIAL",
        )

    return _real_daq


# =========================================================================
# 稳定等待（编排层，后端无关）
# =========================================================================

def wait_for_stability(
    backend: TemperatureBackend,
    target_k: float,
    tol_k: float,
    hold_s: float,
    poll_s: float = 5.0,
    max_wait_s: float = 1800.0,
    channel: str = "A",
    log=None,
) -> tuple:
    """轮询温度直至稳定，与后端实现无关。

    稳定判据：|T - target| <= tol_k 且持续 hold_s 秒（在此窗口内每次
    轮询都满足）。到达 max_wait_s 仍未稳定则返回 (当前温度, False)。

    Args:
        backend: 任何有 ``get_temperature(channel) -> float`` 的对象
            （LakeShore335 或 mock_instruments.FakeLakeShore335）。
        target_k: 目标温度 (K)。
        tol_k: 稳定容差 (K)。
        hold_s: 在容差内需保持的秒数。
        poll_s: 轮询间隔 (s)。
        max_wait_s: 最长等待秒数；0 或负数表示无限等待。
        channel: 温度读取通道。
        log: 可选 logging.Logger，用于打印轮询进度。

    Returns:
        (actual_T, stable)。actual_T 为最后一次读取的温度；
        stable 为是否在超时前达到稳定。
    """
    if log is None:
        import logging
        log = logging.getLogger("noisesweep.stability")

    deadline = time.monotonic() + max_wait_s
    within_tol_since = None  # monotonic 时间戳：何时首次进入容差带
    last_T = None
    stable = False

    while True:
        last_T = backend.get_temperature(channel)
        within = abs(last_T - target_k) <= tol_k

        if within:
            if within_tol_since is None:
                within_tol_since = time.monotonic()
                log.info("T=%.4f K 进入容差带 ±%.3f K (target %.2f K)",
                         last_T, tol_k, target_k)
            elif time.monotonic() - within_tol_since >= hold_s:
                stable = True
                log.info("T=%.4f K 已稳定 hold %.0f s (target %.2f K)",
                         last_T, hold_s, target_k)
                break
        else:
            if within_tol_since is not None:
                log.info("T=%.4f K 离开容差带，稳定计时重置", last_T)
            within_tol_since = None

        if max_wait_s and time.monotonic() >= deadline:
            log.warning("等待稳定超时 %.0f s，当前 T=%.4f K (target %.2f K)",
                        max_wait_s, last_T, target_k)
            break

        time.sleep(poll_s)

    return last_T, stable
