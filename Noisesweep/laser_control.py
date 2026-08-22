# -*- coding: utf-8 -*-
"""
Keysight N7779C Laser Driver (standalone copy)
==============================================
从 `YBCO_TA/Auto_Sweep/laser_driver.py` 拷贝的自包含版本，供 noisesweep
的 standalone 后端使用。仅依赖 pyvisa；原版的 `print(...)` 全部改为
`logging`，方便与 noisesweep 的 stdlib logging 统一。

Provides:
  - Connection lifecycle (connect / disconnect / health check)
  - Power control with separate output on/off
  - Wavelength control (1520-1570 nm typical for C-band)
  - Emergency physical shutdown and restart
  - Automatic disconnection recovery with staged retries
  - Full status readback

Usage:
    from laser_control import LaserController

    laser = LaserController("TCPIP0::169.254.77.29::INSTR")
    if laser.connect():
        laser.set_wavelength(1550)
        laser.set_power(5)
        print(laser.get_status())
"""

import logging

import pyvisa

logger = logging.getLogger("noisesweep.laser")


class LaserController:
    """Keysight N7779C tunable laser controller.

    Communicates via TCPIP/VISA. Manages power, wavelength, output state,
    and provides emergency shutdown with automatic recovery.
    """

    def __init__(self, resource_address: str = "TCPIP0::100.65.11.65::INSTR",
                 resource_manager: object = None):
        self.resource_address = resource_address
        self.inst = None
        self.rm = None
        self._shared_rm = resource_manager  # 共享 ResourceManager（GUI 多线程安全）
        self.connected = False
        self.current_power = None
        self.target_wavelength = 1500  # nm

    # ==================== Connection ====================

    def connect(self) -> bool:
        """Open VISA connection and verify identity (single attempt).

        如果构造时传入了共享 ResourceManager 则复用它（GUI 多线程安全），
        否则创建独立的 ResourceManager（命令行模式向后兼容）。

        对于间歇性 VI_ERROR_RSRC_NFOUND，请使用 connect_with_retry()。
        """
        try:
            if self._shared_rm is not None:
                self.rm = self._shared_rm
            else:
                self.rm = pyvisa.ResourceManager("visa32.dll")
            self.inst = self.rm.open_resource(self.resource_address)
            self.inst.timeout = 5000
            idn = self.inst.query("*IDN?").strip()
            logger.info("[OK] Laser connected: %s", idn)
            self.connected = True
            return True
        except Exception as e:
            logger.error("[Error] Laser connection failed: %s", e)
            self.connected = False
            return False

    def connect_with_retry(self, max_attempts: int = 5,
                           base_delay_s: float = 3.0,
                           log_callback=None) -> bool:
        """带指数退避的连接尝试 — 解决间歇性 VI_ERROR_RSRC_NFOUND。

        失败模式: PyVISA ResourceManager 创建时扫描一次仪器列表。
        如果激光器启动慢 / 网络延迟 / DNS 未注册，第一次扫描不到，
        后续 connect() 会持续失败。

        此方法每次失败后：
          1. 关闭旧 ResourceManager
          2. 等待退避延迟
          3. 强制 list_resources() 触发 NI-VISA 重新扫描网络
          4. 创建新 ResourceManager 重新尝试

        Parameters
        ----------
        max_attempts: 最大尝试次数（默认 5，总共约 90s）
        base_delay_s: 基础等待秒数（默认 3.0，按 1.5× 递增）
        log_callback: 可选 callable(msg)，用于 GUI 线程安全日志。

        Returns
        -------
        True 如果连接成功，False 如果全部尝试失败。
        """
        import time as _time

        def _log(msg):
            logger.info("%s", msg)
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        for attempt in range(1, max_attempts + 1):
            try:
                # 关闭上一次失败的 RM（如有）
                if self.rm and self._shared_rm is None:
                    try:
                        self.rm.close()
                    except Exception:
                        pass
                    self.rm = None

                # 创建全新的 ResourceManager
                if self._shared_rm is not None:
                    self.rm = self._shared_rm
                else:
                    self.rm = pyvisa.ResourceManager("visa32.dll")

                # 强制扫描仪器列表（刷新 NI-VISA 缓存）
                try:
                    resources = self.rm.list_resources()
                    matching = [r for r in resources
                                if self.resource_address in r
                                or 'N7779' in r]
                    if matching:
                        _log(f"[Info] Laser found in VISA scan: {matching[0]}")
                    else:
                        _log(f"[Info] Laser not yet visible in VISA scan "
                             f"({len(resources)} resources found)")
                except Exception:
                    pass  # list_resources 失败不是致命的

                # 尝试打开
                self.inst = self.rm.open_resource(self.resource_address)
                self.inst.timeout = 5000
                idn = self.inst.query("*IDN?").strip()
                _log(f"[OK] Laser connected (attempt {attempt}/{max_attempts}): {idn}")
                self.connected = True
                return True

            except Exception as e:
                err_msg = str(e)
                if self.inst:
                    try:
                        self.inst.close()
                    except Exception:
                        pass
                    self.inst = None

                if attempt < max_attempts:
                    delay = base_delay_s * (1.5 ** (attempt - 1))
                    _log(f"[Retry] Laser connect attempt {attempt}/{max_attempts} "
                         f"failed: {err_msg[:80]}")
                    _log(f"[Retry] Waiting {delay:.1f}s before next attempt...")
                    _time.sleep(delay)
                else:
                    _log(f"[Error] Laser connection failed after "
                         f"{max_attempts} attempts: {err_msg}")
                    self.connected = False
                    return False

        return False

    def reconnect(self) -> bool:
        """关闭旧会话 + 重建 ResourceManager + 重新连接。

        比 connect_with_retry() 更激进 — 强制关闭 RM 再创建新的。
        适用于实验中检测到激光断连后的恢复。

        Returns
        -------
        True 如果重连成功。
        """
        import time as _time
        import gc as _gc

        # 1. 彻底关闭
        try:
            if self.inst:
                self.inst.close()
                self.inst = None
        except Exception:
            pass

        if self._shared_rm is None:
            try:
                if self.rm:
                    self.rm.close()
            except Exception:
                pass
            self.rm = None

        _gc.collect()
        _time.sleep(2.0)

        # 2. 带重试的重新连接
        return self.connect_with_retry(max_attempts=3, base_delay_s=2.0)

    def disconnect(self) -> None:
        """Close VISA session.

        仅关闭 instrument 句柄；共享 ResourceManager 由外部统一管理，
        不在此处关闭（避免影响其他设备）。
        """
        try:
            if self.inst:
                self.inst.close()
                self.inst = None
            # 仅自有 RM 才关闭，共享 RM 由外部管理
            if self._shared_rm is None and self.rm:
                self.rm.close()
        except Exception:
            pass
        self.rm = None
        self.connected = False

    def is_connected(self) -> bool:
        """Health check via *IDN? query."""
        if not self.inst or not self.connected:
            return False
        try:
            self.inst.query("*IDN?")
            return True
        except Exception:
            self.connected = False
            return False

    # ==================== Power control ====================

    def set_power(self, power_mw: float) -> bool:
        """Set laser output power in mW.

        power_mw == 0  →  output OFF only (keeps connection alive)
        power_mw  > 0  →  set power level and enable output
        """
        if not self.is_connected():
            logger.warning("[Warning] Laser not connected, skipping power set")
            return False

        try:
            if power_mw == 0:
                self.inst.write(":OUTPut:STATe OFF")
                self.current_power = 0
                logger.info("[OK] Laser power → 0 mW (output OFF, connection kept)")
            else:
                self.inst.write(f":SOURce:POWer {power_mw}MW")
                self.inst.write(":OUTPut:STATe ON")
                self.current_power = power_mw
                logger.info("[OK] Laser power → %s mW", power_mw)
            return True
        except Exception as e:
            logger.error("[Error] Failed to set laser power: %s", e)
            return False

    def get_power(self) -> float:
        """Read current power setting (mW). Returns -1 on failure."""
        if not self.is_connected():
            return -1
        try:
            return float(self.inst.query(":SOURce:POWer?").strip())
        except Exception:
            return -1

    # ---- Backward-compatible aliases (for code that used KeysightLaser) ----

    def set_power_mw(self, power_mw: float) -> bool:
        """Alias for set_power()."""
        return self.set_power(power_mw)

    def output_on(self) -> None:
        """Turn laser output ON at whatever power was last set."""
        if self.inst:
            try:
                self.inst.write(":OUTPut:STATe ON")
            except Exception as e:
                logger.error("[Error] output_on failed: %s", e)

    def output_off(self) -> None:
        """Turn laser output OFF without changing power setting."""
        if self.inst:
            try:
                self.inst.write(":OUTPut:STATe OFF")
                self.current_power = 0
            except Exception as e:
                logger.error("[Error] output_off failed: %s", e)

    def close(self) -> None:
        """Alias for disconnect()."""
        self.disconnect()

    # ==================== Emergency shutdown ====================

    def physical_off(self) -> None:
        """Emergency: turn off output, then close VISA session.

        Use this only for emergency situations. Normal measurement
        pause should use set_power(0) instead.
        """
        from time import sleep

        logger.warning("[Warning] Executing physical laser shutdown...")
        try:
            if self.inst:
                self.inst.write(":OUTPut:STATe OFF")
                sleep(1)
                self.inst.close()
                self.inst = None
            self.connected = False
            self.current_power = None
            logger.info("[OK] Laser physically turned off")
        except Exception as e:
            logger.error("[Error] Physical shutdown failed: %s", e)

    def physical_on(self) -> bool:
        """Re-power the laser after a physical_off().

        Reconnects and restores the target wavelength.
        """
        logger.info("[Info] Attempting laser re-power...")
        if self.connect():
            self.set_wavelength(self.target_wavelength)
            logger.info("[OK] Laser re-powered successfully")
            return True
        return False

    # ==================== Wavelength ====================

    def set_wavelength(self, wavelength_nm: float = 1500) -> bool:
        """Set laser wavelength in nm (default 1500)."""
        if not self.is_connected():
            return False
        try:
            self.inst.write(f":SOURce:WAV {wavelength_nm}NM")
            self.target_wavelength = wavelength_nm
            logger.info("[OK] Laser wavelength → %s nm", wavelength_nm)
            return True
        except Exception as e:
            logger.error("[Error] Failed to set wavelength: %s", e)
            return False

    def get_wavelength(self) -> float:
        """Read current wavelength (nm). Returns -1 on failure."""
        if not self.is_connected():
            return -1
        try:
            return float(self.inst.query(":SOURce:WAV?").strip())
        except Exception:
            return -1

    # ==================== Status ====================

    def get_status(self) -> dict:
        """Return full status dictionary."""
        status = {
            "connected": self.is_connected(),
            "power_mw": self.get_power() if self.is_connected() else None,
            "wavelength_nm": self.get_wavelength() if self.is_connected() else None,
            "output_enabled": False,
        }
        if self.is_connected():
            try:
                resp = self.inst.query(":OUTPut:STATe?").strip()
                status["output_enabled"] = resp == "1"
            except Exception:
                pass
        return status

    def print_status(self) -> None:
        """Log current status to the module logger."""
        s = self.get_status()
        logger.info("=" * 50)
        logger.info("Laser Status:")
        logger.info("  Connected:  %s", 'YES' if s['connected'] else 'NO')
        if s["connected"]:
            logger.info("  Power:      %s mW", s['power_mw'])
            logger.info("  Wavelength: %s nm", s['wavelength_nm'])
            logger.info("  Output:     %s", 'ON' if s['output_enabled'] else 'OFF')
        logger.info("=" * 50)

    # ==================== Reconnection recovery ====================

    def handle_disconnection(self) -> bool:
        """Multi-stage reconnection with staged back-off.

        Stage 1: 20s → 40s → 60s reconnect attempts
        Stage 2: Physical off + 30s wait, then retry 3× at 20s intervals

        Returns True if reconnection succeeded.
        """
        from time import sleep

        logger.warning("[Warning] Laser disconnection detected!")

        # Stage 1: normal reconnect
        for i, wait in enumerate([20, 40, 60]):
            logger.info("[Info] Waiting %ss before retry (%s/3)...", wait, i + 1)
            sleep(wait)
            if self.connect():
                logger.info("[OK] Reconnected!")
                self.set_wavelength(self.target_wavelength)
                return True
            logger.error("[Error] Retry %s failed", i + 1)

        # Stage 2: physical power cycle
        logger.warning("[Warning] All normal retries failed — physical shutdown...")
        self.physical_off()
        sleep(30)
        logger.info("[Info] Attempting re-power...")
        if self.physical_on():
            logger.info("[OK] Laser re-powered successfully")
            return True

        for i in range(3):
            logger.info("[Info] Waiting 20s before detection (%s/3)...", i + 1)
            sleep(20)
            if self.connect():
                logger.info("[OK] Reconnected!")
                self.set_wavelength(self.target_wavelength)
                return True
            logger.error("[Error] Detection retry %s failed", i + 1)

        logger.error("[Error] Laser recovery completely failed — manual intervention needed")
        return False
