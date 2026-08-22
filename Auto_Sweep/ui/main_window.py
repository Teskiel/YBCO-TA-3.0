# -*- coding: utf-8 -*-
"""
Main window — owns the QStackedWidget that switches between:
  0: DashboardPage
  1: LaserPage
  2: LakeShorePage
  3: VNAPage

Also owns the three Worker threads, wires all signals/slots,
and persists/restores last-used settings automatically (no manual presets).
"""

import json
import os
import time
from typing import Optional

import pyvisa

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QMainWindow, QStackedWidget, QMessageBox

from ui.dashboard_page import DashboardPage
from ui.laser_page import LaserPage
from ui.lakeshore_page import LakeShorePage
from ui.vna_page import VNAPage
from ui.workers import LaserWorker, LakeShoreWorker, VNAWorker, ExperimentWorker


SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "..", "app_settings.json")


def _ts() -> str:
    return time.strftime("[%H:%M:%S] ", time.localtime(time.time()))


class MainWindow(QMainWindow):

    def __init__(self, resume_path: str = None, fill_path: str = None):
        super().__init__()
        self._resume_path = resume_path
        self._fill_path = fill_path
        self.setWindowTitle("YBCO Auto Sweep Control")
        self.resize(1000, 820)

        # ---- pages ----
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.dashboard = DashboardPage()
        self.laser_page = LaserPage()
        self.lakeshore_page = LakeShorePage()
        self.vna_page = VNAPage()

        self.stack.addWidget(self.dashboard)     # 0
        self.stack.addWidget(self.laser_page)    # 1
        self.stack.addWidget(self.lakeshore_page) # 2
        self.stack.addWidget(self.vna_page)       # 3

        # ---- 共享 VISA ResourceManager（避免多线程竞争 VI_ERROR_RSRC_NFOUND） ----
        self._visa_rm = pyvisa.ResourceManager("visa32.dll")

        # ---- workers ----
        self.laser_worker = LaserWorker(resource_manager=self._visa_rm)
        self.lakeshore_worker = LakeShoreWorker(resource_manager=self._visa_rm)
        self.vna_worker = VNAWorker(resource_manager=self._visa_rm)

        self.laser_thread = QThread()
        self.lakeshore_thread = QThread()
        self.vna_thread = QThread()

        self.laser_worker.moveToThread(self.laser_thread)
        self.lakeshore_worker.moveToThread(self.lakeshore_thread)
        self.vna_worker.moveToThread(self.vna_thread)

        self.laser_thread.start()
        self.lakeshore_thread.start()
        self.vna_thread.start()

        # ---- LakeShore poll timer ----
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)

        # ---- connection tracking ----
        self._connected: dict[str, bool] = {
            "laser": False, "lakeshore": False, "vna": False,
        }

        # ---- 自动重连追踪 ----
        self._reconnect_attempts: dict[str, int] = {
            "laser": 0, "lakeshore": 0, "vna": 0}
        self._reconnect_max_attempts: int = 3
        self._reconnect_timers: dict[str, QTimer] = {}
        self._reconnect_addresses: dict[str, str] = {
            "laser": "", "lakeshore": "", "vna": ""}
        # 用户主动断开的标记，阻止自动重连
        self._user_disconnect: dict[str, bool] = {
            "laser": False, "lakeshore": False, "vna": False}

        # ---- experiment thread (created on demand) ----
        self.experiment_worker: Optional[ExperimentWorker] = None
        self.experiment_thread: Optional[QThread] = None

        # ---- wire everything ----
        self._wire_dashboard()
        self._wire_laser()
        self._wire_lakeshore()
        self._wire_vna()

        # ---- restore last settings ----
        self._load_settings()

        # start on dashboard
        self.stack.setCurrentIndex(0)

        # 如果指定了 resume_path，延迟自动恢复实验
        if self._resume_path:
            QTimer.singleShot(500, self._auto_resume_experiment)

    # ==================================================================
    # Settings auto-persist (replaces manual preset system)
    # ==================================================================

    def _collect_settings(self) -> dict:
        """Gather current settings from all pages into a dict."""
        vna = self.vna_page.get_all_settings()
        temp = self.dashboard.get_temp_sweep().get_settings()
        return {
            "addresses": {
                "laser": self.dashboard.get_address("laser"),
                "lakeshore": self.dashboard.get_address("lakeshore"),
                "vna": self.dashboard.get_address("vna"),
            },
            "laser": {
                "wavelength_nm": self.laser_page.get_wavelength(),
                "power_sequence_mw": self.laser_page.get_power_sequence(),
            },
            "lakeshore": {
                "loop1": self.lakeshore_page.get_loop_settings(1),
                "loop2": self.lakeshore_page.get_loop_settings(2),
            },
            "vna": {
                "start_freq_hz": vna["start_freq_hz"],
                "stop_freq_hz": vna["stop_freq_hz"],
                "s_parameter": vna["s_parameter"],
                "power_dbm": vna["power_dbm"],
                "power_dbm_button": vna.get("power_dbm_button", vna["power_dbm"]),
                "power_range_settings": vna.get("power_range_settings", {}),
                "points": vna["points"],
                "if_bandwidth_hz": vna["if_bandwidth_hz"],
            },
            "temperature_sweep": temp,
        }

    def _save_settings(self):
        try:
            data = self._collect_settings()
            path = os.path.normpath(SETTINGS_FILE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.dashboard.log(_ts() + "Settings auto-saved")
        except Exception as e:
            self.dashboard.log(_ts() + f"Settings save failed: {e}")

    def _load_settings(self):
        try:
            path = os.path.normpath(SETTINGS_FILE)
            if not os.path.exists(path):
                self.dashboard.log(_ts() + "No saved settings — using defaults")
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # addresses
            for k in ("laser", "lakeshore", "vna"):
                if "addresses" in data and k in data["addresses"] and data["addresses"][k]:
                    combo = getattr(self.dashboard, f"_{k}_addr", None)
                    if combo:
                        idx = combo.findText(data["addresses"][k])
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                        else:
                            combo.setEditText(data["addresses"][k])
                # also update detail page address labels
                page = self._page_for(k)
                if page and hasattr(page, "set_address") and "addresses" in data:
                    page.set_address(data["addresses"].get(k, ""))

            # laser
            if "laser" in data:
                ls = data["laser"]
                if "wavelength_nm" in ls:
                    self.laser_page.set_wavelength(ls["wavelength_nm"])
                if "power_sequence_mw" in ls:
                    self.laser_page.set_power_sequence(ls["power_sequence_mw"])

            # lakeshore
            if "lakeshore" in data:
                lk = data["lakeshore"]
                for loop in (1, 2):
                    loop_data = lk.get(f"loop{loop}")
                    if loop_data:
                        self.lakeshore_page.set_loop_settings(loop, loop_data)

            # vna
            if "vna" in data:
                self.vna_page.set_all_settings(data["vna"])

            # temperature sweep
            if "temperature_sweep" in data:
                self.dashboard.get_temp_sweep().set_settings(data["temperature_sweep"])

            self.dashboard.log(_ts() + "Previous settings restored")
        except Exception as e:
            self.dashboard.log(_ts() + f"Settings load failed: {e}")

    # ==================================================================
    # Dashboard wiring
    # ==================================================================

    def _wire_dashboard(self):
        d = self.dashboard
        d.navigate_to.connect(self._on_navigate)
        d.connect_all_requested.connect(self._on_connect_all)
        d.connect_device.connect(self._on_dashboard_connect)
        d.disconnect_device.connect(self._on_dashboard_disconnect)
        d.experiment_start_requested.connect(self._on_start_experiment)
        d.experiment_abort_requested.connect(self._on_abort_experiment)

    def _on_navigate(self, page_key: str):
        index_map = {"laser": 1, "lakeshore": 2, "vna": 3}
        self.stack.setCurrentIndex(index_map.get(page_key, 0))

    def _on_dashboard_connect(self, key: str, address: str):
        # 清除用户断开标记，允许后续自动重连
        self._user_disconnect[key] = False
        self._connect_device(key, address)

    def _on_dashboard_disconnect(self, key: str):
        # 标记为用户主动断开，阻止自动重连
        self._user_disconnect[key] = True
        self._disconnect_device(key)

    def _on_connect_all(self):
        self.dashboard.log(_ts() + "Connecting all devices...")
        self._connect_device("laser", self.dashboard.get_address("laser"))
        self._connect_device("lakeshore", self.dashboard.get_address("lakeshore"))
        self._connect_device("vna", self.dashboard.get_address("vna"))

    # ==================================================================
    # Laser wiring
    # ==================================================================

    def _wire_laser(self):
        lp = self.laser_page
        w = self.laser_worker

        lp.back_clicked.connect(lambda: self.stack.setCurrentIndex(0))
        lp.connect_requested.connect(lambda addr: self._on_dashboard_connect("laser", addr))
        lp.disconnect_requested.connect(lambda: self._on_dashboard_disconnect("laser"))
        lp.power_set_requested.connect(w.set_power)
        lp.wavelength_set_requested.connect(w.set_wavelength)
        lp.output_on_requested.connect(w.output_on)
        lp.output_off_requested.connect(w.output_off)
        lp.physical_off_requested.connect(w.physical_off)

        w.connected.connect(lambda idn: self._on_device_connected("laser", idn))
        w.disconnected.connect(lambda: self._on_device_disconnected("laser"))
        w.status_updated.connect(lp.update_status)
        w.error.connect(lambda msg: self._on_error("laser", msg))
        w.log.connect(lambda msg: self._log("laser", msg))

    # ==================================================================
    # LakeShore wiring
    # ==================================================================

    def _wire_lakeshore(self):
        lsp = self.lakeshore_page
        w = self.lakeshore_worker

        lsp.back_clicked.connect(lambda: self.stack.setCurrentIndex(0))
        lsp.connect_requested.connect(lambda addr: self._on_dashboard_connect("lakeshore", addr))
        lsp.disconnect_requested.connect(lambda: self._on_dashboard_disconnect("lakeshore"))
        lsp.setpoint_requested.connect(w.set_setpoint)
        lsp.pid_requested.connect(w.set_pid)
        lsp.heater_range_requested.connect(w.set_heater_range)
        lsp.all_heaters_off_requested.connect(w.all_heaters_off)
        lsp.read_pid_requested.connect(w.read_pid)

        w.connected.connect(lambda idn: self._on_device_connected("lakeshore", idn))
        w.disconnected.connect(lambda: self._on_device_disconnected("lakeshore"))
        w.reading.connect(lsp.update_reading)
        w.error.connect(lambda msg: self._on_error("lakeshore", msg))
        w.log.connect(lambda msg: self._log("lakeshore", msg))

        self._poll_timer.timeout.connect(w.poll)

    # ==================================================================
    # VNA wiring
    # ==================================================================

    def _wire_vna(self):
        vp = self.vna_page
        w = self.vna_worker

        vp.back_clicked.connect(lambda: self.stack.setCurrentIndex(0))
        vp.connect_requested.connect(lambda addr: self._on_dashboard_connect("vna", addr))
        vp.disconnect_requested.connect(lambda: self._on_dashboard_disconnect("vna"))
        vp.settings_apply_requested.connect(w.apply_settings)
        vp.single_sweep_requested.connect(w.single_sweep)

        w.connected.connect(lambda idn: self._on_device_connected("vna", idn))
        w.disconnected.connect(lambda: self._on_device_disconnected("vna"))
        w.settings_applied.connect(lambda s: self._log("vna", "Settings applied"))
        w.error.connect(lambda msg: self._on_error("vna", msg))
        w.log.connect(lambda msg: self._log("vna", msg))

    # ==================================================================
    # Device connection / disconnection
    # ==================================================================

    def _connect_device(self, key: str, address: str):
        self.dashboard.log(_ts() + f"[{key}] Connecting to {address}...")
        self._set_device_connecting(key)
        if key == "laser":
            self.laser_worker.connect_device(address)
        elif key == "lakeshore":
            self.lakeshore_worker.connect_device(address)
        elif key == "vna":
            self.vna_worker.connect_device(address)

    def _disconnect_device(self, key: str):
        self.dashboard.log(_ts() + f"[{key}] Disconnecting...")
        if key == "laser":
            self.laser_worker.disconnect_device()
        elif key == "lakeshore":
            self.lakeshore_worker.disconnect_device()
        elif key == "vna":
            self.vna_worker.disconnect_device()

    def _set_device_connecting(self, key: str):
        """设置设备为连接中状态（黄灯）。"""
        page = self._page_for(key)
        if hasattr(page, "set_connecting"):
            page.set_connecting()
        # 同时更新 dashboard
        if hasattr(self.dashboard, "set_device_connecting"):
            self.dashboard.set_device_connecting(key)
        else:
            # 回退：在 dashboard 卡上显示黄色
            if key in self.dashboard._cards:
                self.dashboard._cards[key].set_connecting()

    def _on_device_connected(self, key: str, identity: str):
        self._connected[key] = True
        # 记录连接地址用于自动重连
        self._reconnect_addresses[key] = self.dashboard.get_address(key)
        # 重置重连计数
        self._reconnect_attempts[key] = 0
        self.dashboard.set_device_connected(key, identity)
        self.dashboard.log(_ts() + f"[{key}] Connected: {identity}")

        page = self._page_for(key)
        if hasattr(page, "set_connected"):
            page.set_connected(identity)

        n = sum(self._connected.values())
        self.dashboard.set_connect_count(n)

        if key == "lakeshore":
            self._poll_timer.start()

    def _on_device_disconnected(self, key: str):
        self._connected[key] = False
        self.dashboard.set_device_disconnected(key)
        self.dashboard.log(_ts() + f"[{key}] Disconnected")

        page = self._page_for(key)
        if hasattr(page, "set_disconnected"):
            page.set_disconnected()

        n = sum(self._connected.values())
        self.dashboard.set_connect_count(n)

        if key == "lakeshore":
            self._poll_timer.stop()

    # ==================================================================
    # helpers
    # ==================================================================

    def _page_for(self, key: str):
        return {
            "laser": self.laser_page,
            "lakeshore": self.lakeshore_page,
            "vna": self.vna_page,
        }.get(key)

    def _on_error(self, device: str, message: str):
        self.dashboard.log(_ts() + f"[{device}] ERROR: {message}")

        # 未连接的设备：只记录日志，不做任何处理
        if not self._connected.get(device, False):
            return

        # 检查是否为连接断开错误 → 触发自动重连
        if (self._is_connection_error(message)
                and not self._user_disconnect.get(device, False)):
            self._start_reconnect(device)
            return

        # 非连接错误（如数据解析失败、串口瞬时干扰）：
        # 设备仍在连接状态，不断开，只记录错误日志
        # 这类错误通常是瞬时的，下次轮询会恢复正常

    def _log(self, device: str, message: str):
        page = self._page_for(device)
        if page and hasattr(page, "log"):
            page.log(message)

    # ==================================================================
    # 自动重连
    # ==================================================================

    @staticmethod
    def _is_connection_error(message: str) -> bool:
        """判断错误消息是否为 VISA 连接断开错误。

        检测关键词：VI_ERROR, timeout, disconnect, closed,
        lost, read error, write error, IO error, socket, aborted
        """
        connection_keywords = [
            "VI_ERROR", "timeout", "disconnect", "closed",
            "lost", "read error", "write error", "IO error",
            "socket", "aborted", "connection",
        ]
        msg_lower = message.lower()
        return any(kw.lower() in msg_lower for kw in connection_keywords)

    def _start_reconnect(self, device: str):
        """开始自动重连流程。

        设置黄灯状态，延迟后尝试重连。
        最多重试 _reconnect_max_attempts 次。
        """
        attempts = self._reconnect_attempts.get(device, 0)

        if attempts >= self._reconnect_max_attempts:
            # 达到最大重连次数 → 保持断开
            self._connected[device] = False
            self.dashboard.set_device_error(device)
            self.dashboard.log(
                _ts() + f"[{device}] 自动重连失败："
                f"已达最大重试次数 ({self._reconnect_max_attempts})")
            page = self._page_for(device)
            if page and hasattr(page, "set_disconnected"):
                page.set_disconnected()
            n = sum(self._connected.values())
            self.dashboard.set_connect_count(n)
            if device == "lakeshore":
                self._poll_timer.stop()
            return

        self._reconnect_attempts[device] = attempts + 1
        attempt = self._reconnect_attempts[device]

        # 设置黄灯
        self._set_device_connecting(device)
        self.dashboard.log(
            _ts() + f"[{device}] 自动重连中... "
            f"（第 {attempt}/{self._reconnect_max_attempts} 次尝试）")

        # 停止旧定时器（防止泄漏和并发重连）
        old = self._reconnect_timers.get(device)
        if old is not None:
            old.stop()
            old.deleteLater()

        # 延迟重连（给硬件恢复时间）
        import config
        delay_ms = config.reconnect_delay_seconds * 1000
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda d=device: self._attempt_reconnect(d))
        timer.start(delay_ms)
        self._reconnect_timers[device] = timer

    def _attempt_reconnect(self, device: str):
        """执行一次重连尝试。获取保存的地址并调用 _connect_device。"""
        address = self._reconnect_addresses.get(device, "")
        if not address:
            address = self.dashboard.get_address(device)

        if not address:
            self.dashboard.log(
                _ts() + f"[{device}] 自动重连中止：无可用地址")
            self._connected[device] = False
            self.dashboard.set_device_error(device)
            page = self._page_for(device)
            if page and hasattr(page, "set_disconnected"):
                page.set_disconnected()
            return

        # 清理旧连接
        self._disconnect_device_quiet(device)
        # 重新连接
        self._connect_device(device, address)

    def _disconnect_device_quiet(self, key: str):
        """静默断开设备（不设置 user_disconnect 标记，不更新 UI）。

        用于自动重连时清理旧连接。
        """
        if key == "laser":
            self.laser_worker.disconnect_device()
        elif key == "lakeshore":
            self.lakeshore_worker.disconnect_device()
        elif key == "vna":
            self.vna_worker.disconnect_device()

    # ==================================================================
    # Experiment control
    # ==================================================================

    def _on_start_experiment(self):
        if not all(self._connected.values()):
            self.dashboard.log(_ts() +
                "ERROR: All three devices must be connected first")
            QMessageBox.warning(
                self, "Cannot Start",
                "All three devices (Laser, LakeShore, VNA) "
                "must be connected before starting the experiment.")
            return

        temp_list = self.dashboard.get_temp_sweep().get_temperatures()
        if not temp_list:
            self.dashboard.log(_ts() +
                "ERROR: No temperature points configured")
            QMessageBox.warning(
                self, "Cannot Start",
                "Please configure at least one temperature point "
                "in the Temperature Sweep section.")
            return

        power_list = self.laser_page.get_power_sequence()
        if not power_list:
            self.dashboard.log(_ts() +
                "ERROR: No laser power levels selected")
            QMessageBox.warning(
                self, "Cannot Start",
                "Please select at least one laser power level "
                "on the Laser page.")
            return

        # ---- 清理上次实验残留 ----
        # 1) 终止当前进程中仍在运行的旧 worker / thread
        if self.experiment_worker or self.experiment_thread:
            self.dashboard.log(_ts() + "清理上次实验 worker…")
            if self.experiment_worker:
                self.experiment_worker.abort()
            self._cleanup_experiment()

        # 2) 终止泄漏的僵尸 Python 子进程（watchdog 等）
        try:
            from zombie_killer import kill_zombie_processes
            killed, freed_mb = kill_zombie_processes(
                log_callback=lambda msg: self.dashboard.log(_ts() + msg))
            if killed:
                self.dashboard.log(
                    _ts() +
                    f"僵尸进程清理: 终止 {killed} 个, 释放 ~{freed_mb:.0f} MB")
        except Exception:
            pass  # 非致命，不阻塞实验启动

        vna_settings = self.vna_page.get_all_settings()
        output_dir = os.path.join(
            os.path.dirname(__file__), "..", "experiment_data",
            time.strftime("%Y%m%d_%H%M%S"))

        self.experiment_worker = ExperimentWorker()
        self.experiment_thread = QThread()
        self.experiment_worker.moveToThread(self.experiment_thread)

        timing = self.dashboard.get_temp_sweep().get_timing_settings()

        self.experiment_worker.configure(
            lakeshore_ctrl=self.lakeshore_worker._controller,
            laser_ctrl=self.laser_worker._laser,
            vna_resource=self.vna_worker._vna,
            temp_list=temp_list,
            power_list=power_list,
            vna_power_list=vna_settings.get("power_dbm", [-45]),
            output_dir=output_dir,
            vna_settings=vna_settings,
            pre_measurement_wait_s=timing["pre_measurement_wait_min"] * 60,
            max_wait_s=timing["max_wait_min"] * 60,
        )

        w = self.experiment_worker
        w.progress.connect(lambda msg: self.dashboard.log(_ts() + msg))
        w.temperature_stabilizing.connect(
            lambda tgt, cur: self.dashboard.log(
                _ts() + f"Temp → tgt={tgt:.1f} K  cur={cur:.3f} K"))
        w.temperature_stable.connect(
            lambda tgt, act: self.dashboard.log(
                _ts() + f"Stable: {act:.3f} K (tgt {tgt:.1f} K)"))
        w.measurement_started.connect(
            lambda tk, pw: self.dashboard.log(
                _ts() + f"Measuring: {tk:.3f} K / {pw} mW"))
        w.measurement_complete.connect(
            lambda tk, pw, fp: self.dashboard.log(
                _ts() + f"Saved: {os.path.basename(fp)}"))
        w.experiment_finished.connect(self._on_experiment_finished)
        w.experiment_error.connect(self._on_experiment_error)
        w.experiment_aborted.connect(self._on_experiment_aborted)
        w.memory_critical.connect(lambda msg: QMessageBox.warning(
            self, "⚠ Memory Critical", msg))

        # ---- 断点续传信号 ----
        w.experiment_resume_prompt.connect(
            self._on_experiment_resume_prompt,
            Qt.BlockingQueuedConnection)
        w.experiment_recovering.connect(
            lambda msg: self.dashboard.log(
                f"[{time.strftime('%H:%M:%S')}] 🔴 VISA 连接丢失: {msg}"))
        w.experiment_recovered.connect(
            lambda: self.dashboard.log(
                f"[{time.strftime('%H:%M:%S')}] 🟢 所有设备重连成功，实验恢复"))
        w.experiment_recovery_timeout.connect(
            lambda: QMessageBox.warning(
                self, "重连超时",
                "设备重连已超时。\n\n"
                "检查点文件已保存，您可稍后手动恢复实验。"))

        self.experiment_thread.started.connect(w.run)
        self.experiment_thread.start()

        self.dashboard.set_experiment_running(True)
        # 实验期间停止轮询定时器，避免串口抢占导致 LakeShore 断联
        self._poll_timer.stop()
        self.dashboard.log(_ts() +
            f"Experiment started — "
            f"{len(temp_list)} temps × {len(power_list)} powers")

    def _on_abort_experiment(self):
        if self.experiment_worker:
            self.experiment_worker.abort()
        self.dashboard.log(_ts() + "Experiment abort requested")

    def _on_experiment_finished(self, count: int):
        self._cleanup_experiment()
        self.dashboard.log(_ts() +
            f"Experiment complete — {count} measurements taken")

    def _on_experiment_error(self, message: str):
        self._cleanup_experiment()
        self.dashboard.log(_ts() + f"Experiment ERROR: {message}")

    def _on_experiment_aborted(self):
        self._cleanup_experiment()
        self.dashboard.log(_ts() + "Experiment aborted by user")

    def _on_experiment_resume_prompt(self, experiment_id: str,
                                      completed_count: int):
        """检测到未完成实验的检查点，询问用户是否恢复。

        通过 BlockingQueuedConnection 从实验线程同步调用。
        """
        msg = QMessageBox(self)
        msg.setWindowTitle("恢复实验")
        msg.setIcon(QMessageBox.Question)
        msg.setText(
            f"检测到未完成的实验\n\n"
            f"实验 ID: {experiment_id}\n"
            f"已完成: {completed_count} 个测量点\n\n"
            f"是否恢复？")
        msg.setStandardButtons(
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        msg.button(QMessageBox.Yes).setText("恢复 (Resume)")
        msg.button(QMessageBox.No).setText("重新开始 (Restart)")
        msg.button(QMessageBox.Cancel).setText("取消 (Cancel)")
        msg.setDefaultButton(QMessageBox.Yes)

        result = msg.exec_()

        if result == QMessageBox.Yes:
            self.dashboard.log(
                _ts() + f"从检查点恢复实验 {experiment_id}")
            self.experiment_worker._resume_action = "resume"
        elif result == QMessageBox.No:
            self.dashboard.log(
                _ts() + f"放弃检查点，重新开始实验")
            self.experiment_worker._resume_action = "restart"
        else:  # Cancel
            self.dashboard.log(
                _ts() + f"用户取消恢复，实验不启动")
            self.experiment_worker._resume_action = "cancel"

    def _cleanup_experiment(self):
        # ---- 断开所有信号连接，打破循环引用，防止僵尸 worker 累积 ----
        if self.experiment_worker:
            w = self.experiment_worker
            for sig_name in (
                "progress", "temperature_stabilizing", "temperature_stable",
                "measurement_started", "measurement_complete",
                "experiment_finished", "experiment_error", "experiment_aborted",
                "experiment_started", "memory_critical",
            ):
                sig = getattr(w, sig_name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except TypeError:
                        pass  # 已断开或无连接

        if self.experiment_thread:
            self.experiment_thread.quit()
            self.experiment_thread.wait(5000)
            self.experiment_thread = None
        self.experiment_worker = None
        self.dashboard.set_experiment_running(False)
        # 实验结束后恢复 LakeShore 轮询
        if self._connected.get("lakeshore", False):
            self._poll_timer.start()

    # ==================================================================
    # --resume 自动恢复
    # ==================================================================

    def _auto_resume_experiment(self):
        """--resume 模式下自动加载 checkpoint 并配置 dashboard。"""
        if not self._resume_path:
            return

        checkpoint_path = os.path.join(self._resume_path, "checkpoint.json")
        if not os.path.exists(checkpoint_path):
            self.dashboard.log("⚠ --resume 模式但 checkpoint.json 不存在，无法自动恢复")
            return

        # 加载 checkpoint
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                ckpt = json.load(f)
        except (json.JSONDecodeError, OSError):
            self.dashboard.log("⚠ checkpoint.json 损坏，无法自动恢复")
            return

        state = ckpt.get("state", {})
        original_temps = ckpt.get("original_temp_list",
                                  state.get("original_temp_list", []))
        original_vna = ckpt.get("original_vna_power_list",
                                state.get("original_vna_power_list", []))
        original_powers = ckpt.get("original_power_list",
                                   state.get("original_power_list", []))
        temp_idx = state.get("temp_idx", 0)

        if temp_idx >= len(original_temps):
            self.dashboard.log("✓ 实验已完成，无需恢复")
            return

        self.dashboard.log(f"=== 从 {self._resume_path} 恢复实验 ===")
        self.dashboard.log(f"  跳过 {temp_idx}/{len(original_temps)} 个温度点")
        self.dashboard.log(f"  从 {original_temps[temp_idx]:.1f}K 开始")

        # 将 resume 信息传递给 Dashboard
        if hasattr(self.dashboard, "set_resume_config"):
            self.dashboard.set_resume_config(
                resume_path=self._resume_path,
                temp_list=original_temps,
                vna_power_list=original_vna,
                power_list=original_powers,
                start_temp_idx=temp_idx,
            )

    # ==================================================================
    # cleanup
    # ==================================================================

    def closeEvent(self, event):  # noqa: N802
        self._save_settings()

        # 中止实验并等待线程结束（最多等 10s 让 VISA 操作完成）
        if self.experiment_worker:
            self.experiment_worker.abort()
        if self.experiment_thread:
            self.experiment_thread.quit()
            self.experiment_thread.wait(10000)

        # 停止轮询（避免关闭设备时的竞争）
        self._poll_timer.stop()

        # 断开所有设备前再等一会儿
        for key in list(self._connected.keys()):
            self._disconnect_device(key)

        for thr in (self.laser_thread, self.lakeshore_thread, self.vna_thread):
            thr.quit()
            thr.wait(2000)

        # 关闭共享 VISA ResourceManager
        if self._visa_rm:
            try:
                self._visa_rm.close()
            except Exception:
                pass
            self._visa_rm = None

        event.accept()
