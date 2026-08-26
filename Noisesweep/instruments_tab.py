# -*- coding: utf-8 -*-
"""仪器与连接 Tab：5 台仪器统一状态行 + 连接/断开 + 手动控制 + 验证连接。

E8257D / P5002A / PXIe-4480 沿用 V3 的「仪器控制」对话框（按钮打开）；
LakeShore 335 与激光在此直接连接、读取与控制（复用 backends 工厂）。

所有耗时的 VISA 操作（连接、读温、设温、激光控制、验证）都在一次性
QThread（_ControlWorker）里执行，防 GUI 冻结。
"""

import json
import time

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

import gui_config


class _ControlWorker(QThread):
    """一次性后台任务：连接 / 手动控制 / 验证连接，结果经信号回 GUI。"""

    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            msg = self._fn()
            self.done.emit(msg or "")
        except Exception as exc:
            self.failed.emit("{}: {}".format(type(exc).__name__, exc))


class InstrumentsTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.manager = window.manager
        self._task_worker = None
        self._poll_worker = None
        self._polling = False
        self._last_connections = None
        self._build_ui()
        self.update_connection_states()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tip = QLabel("统一查看 / 连接 5 台仪器。E8257D、P5002A、PXIe-4480 沿用 V3 的"
                     "「仪器控制」对话框；LakeShore 335 与激光在此直接连接与控制。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        # ---- E8257D ----
        box = QGroupBox("E8257D 信号源")
        row = QHBoxLayout(box)
        self._source_status = QLabel()
        btn = QPushButton("打开控制窗口")
        btn.clicked.connect(self.window.open_source)
        row.addWidget(self._source_status, 1)
        row.addWidget(btn)
        layout.addWidget(box)

        # ---- P5002A ----
        box = QGroupBox("P5002A 矢量网络分析仪")
        row = QHBoxLayout(box)
        self._p5002_status = QLabel()
        btn = QPushButton("打开控制窗口")
        btn.clicked.connect(self.window.open_p5002)
        row.addWidget(self._p5002_status, 1)
        row.addWidget(btn)
        layout.addWidget(box)

        # ---- PXIe-4480 ----
        box = QGroupBox("PXIe-4480 采集卡")
        row = QHBoxLayout(box)
        self._daq_status = QLabel()
        btn = QPushButton("打开配置对话框")
        btn.clicked.connect(self.window.open_daq)
        row.addWidget(self._daq_status, 1)
        row.addWidget(btn)
        layout.addWidget(box)

        # ---- LakeShore 335 ----
        layout.addWidget(self._build_lakeshore_box())

        # ---- 激光 N7779C ----
        layout.addWidget(self._build_laser_box())

        note = QLabel("提示：未连接 LakeShore/激光时，自动化流程的温度/功率文本框仍可用，"
                      "但仅可填单个值，用于路径命名与假设温度。")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def _build_lakeshore_box(self):
        cfg = self.manager.config or {}
        box = QGroupBox("LakeShore 335 温控")
        g = QGridLayout(box)
        self._ls_address = QLineEdit(cfg.get("lakeshore_visa_address") or "")
        self._ls_address.setPlaceholderText("如 ASRL4::INSTR（留空 = 无硬件，用固定温度）")
        self._temp_status = QLabel()
        btn_connect = QPushButton("连接")
        btn_disconnect = QPushButton("断开")
        btn_verify = QPushButton("验证连接")
        btn_read = QPushButton("读当前温度")
        self._ls_setpoint = QDoubleSpinBox()
        self._ls_setpoint.setRange(0.0, 1000.0)
        self._ls_setpoint.setDecimals(3)
        self._ls_setpoint.setValue(float(cfg.get("fixed_temperature_k", 77.0)))
        self._ls_setpoint.setSuffix(" K")
        btn_set = QPushButton("设 setpoint")
        self._heater_label = QLabel("加热器: -")
        btn_connect.clicked.connect(self._connect_lakeshore)
        btn_disconnect.clicked.connect(self._disconnect_lakeshore)
        btn_verify.clicked.connect(self._verify_lakeshore)
        btn_read.clicked.connect(self._read_lakeshore)
        btn_set.clicked.connect(self._set_lakeshore)
        g.addWidget(self._temp_status, 0, 0, 1, 3)
        g.addWidget(QLabel("VISA 地址"), 1, 0)
        g.addWidget(self._ls_address, 1, 1, 1, 3)
        g.addWidget(btn_connect, 1, 4)
        g.addWidget(btn_disconnect, 1, 5)
        g.addWidget(btn_verify, 1, 6)
        g.addWidget(btn_read, 2, 1)
        g.addWidget(QLabel("setpoint"), 2, 2)
        g.addWidget(self._ls_setpoint, 2, 3)
        g.addWidget(btn_set, 2, 4)
        g.addWidget(self._heater_label, 2, 5, 1, 2)
        return box

    def _build_laser_box(self):
        cfg = self.manager.config or {}
        box = QGroupBox("激光 N7779C")
        g = QGridLayout(box)
        self._laser_address = QLineEdit(cfg.get("laser_visa_address") or "")
        self._laser_address.setPlaceholderText("如 TCPIP0::…（留空 = 无硬件，跳过激光）")
        self._laser_status = QLabel()
        btn_connect = QPushButton("连接")
        btn_disconnect = QPushButton("断开")
        btn_verify = QPushButton("验证连接")
        self._laser_power = QDoubleSpinBox()
        self._laser_power.setRange(0.0, 1000.0)
        self._laser_power.setDecimals(2)
        self._laser_power.setValue(0.0)
        self._laser_power.setSuffix(" mW")
        btn_power = QPushButton("设功率")
        self._laser_wavelength = QDoubleSpinBox()
        self._laser_wavelength.setRange(100.0, 10000.0)
        self._laser_wavelength.setDecimals(1)
        self._laser_wavelength.setValue(float(cfg.get("laser_wavelength_nm", 1550.0)))
        self._laser_wavelength.setSuffix(" nm")
        btn_wave = QPushButton("设波长")
        btn_on = QPushButton("输出 ON")
        btn_off = QPushButton("输出 OFF")
        btn_connect.clicked.connect(self._connect_laser)
        btn_disconnect.clicked.connect(self._disconnect_laser)
        btn_verify.clicked.connect(self._verify_laser)
        btn_power.clicked.connect(self._set_laser_power)
        btn_wave.clicked.connect(self._set_laser_wavelength)
        btn_on.clicked.connect(lambda: self._laser_cmd("on"))
        btn_off.clicked.connect(lambda: self._laser_cmd("off"))
        g.addWidget(self._laser_status, 0, 0, 1, 4)
        g.addWidget(QLabel("VISA 地址"), 1, 0)
        g.addWidget(self._laser_address, 1, 1, 1, 3)
        g.addWidget(btn_connect, 1, 4)
        g.addWidget(btn_disconnect, 1, 5)
        g.addWidget(btn_verify, 1, 6)
        g.addWidget(QLabel("功率"), 2, 0)
        g.addWidget(self._laser_power, 2, 1)
        g.addWidget(btn_power, 2, 2)
        g.addWidget(QLabel("波长"), 2, 3)
        g.addWidget(self._laser_wavelength, 2, 4)
        g.addWidget(btn_wave, 2, 5)
        g.addWidget(btn_on, 2, 6)
        g.addWidget(btn_off, 2, 7)
        return box

    # ------------------------------------------------------------------
    # 状态刷新（500ms 连接态 + 2s 低频实时值）
    # ------------------------------------------------------------------

    def update_connection_states(self):
        """无 VISA 调用，仅重绘连接状态颜色与文字。由主窗口 refresh_status 调用。"""
        m = self.manager

        def _style(on, text):
            return text, "green" if on else "red"

        s, c = _style(m.source_connected,
                      "● E8257D：{}".format("已连接" if m.source_connected else "未连接"))
        self._source_status.setText(s)
        self._source_status.setStyleSheet("color: {}".format(c))

        s, c = _style(m.p5002_connected,
                      "● P5002A：{}".format("已连接" if m.p5002_connected else "未连接"))
        self._p5002_status.setText(s)
        self._p5002_status.setStyleSheet("color: {}".format(c))

        q = m.daq_config
        s, c = _style(m.daq_connected,
                      "● PXIe-4480：{}；采样率 {:.6g} S/s；量程 ±{:.6g} V；通道 {}".format(
                          "已连接" if m.daq_connected else "未连接",
                          q.get("sample_rate", 0), q.get("voltage_range", 0),
                          ",".join("ai{}".format(x) for x in q.get("channels", []))))
        self._daq_status.setText(s)
        self._daq_status.setStyleSheet("color: {}".format(c))

        s, c = _style(m.temp_connected,
                      "● LakeShore：{}".format("已连接" if m.temp_connected else "未连接（无硬件）"))
        self._temp_status.setText(s)
        self._temp_status.setStyleSheet("color: {}".format(c))

        s, c = _style(m.laser_connected,
                      "● 激光：{}".format("已连接" if m.laser_connected else "未连接（无硬件）"))
        self._laser_status.setText(s)
        self._laser_status.setStyleSheet("color: {}".format(c))

        # 连接状态跳变时，同步自动化页可用性 + 持久化地址
        current = (m.source_connected, m.p5002_connected, m.daq_connected,
                   m.temp_connected, m.laser_connected)
        if current != self._last_connections:
            self._last_connections = current
            if hasattr(self.window, "auto_tab"):
                self.window.auto_tab.refresh_availability()

    def poll_status(self):
        """低频（2s）轮询 LakeShore/激光实时值；自动化运行时挂起。"""
        if self._polling:
            return
        if hasattr(self.window, "auto_tab") and self.window.auto_tab.busy:
            return
        if not (self.manager.temp_connected or self.manager.laser_connected):
            return
        self._polling = True
        self._poll_worker = _ControlWorker(self._poll_fn, self)
        self._poll_worker.done.connect(self._on_poll_done)
        self._poll_worker.failed.connect(lambda _e: self._finish_poll())
        self._poll_worker.start()

    def _poll_fn(self):
        out = {}
        channel = (self.manager.config or {}).get("lakeshore_channel", "A")
        if self.manager.temp_connected:
            with self.manager.temp_lock:
                out["temp"] = float(self.manager.temp.get_temperature(channel))
        if self.manager.laser_connected:
            with self.manager.laser_lock:
                out["laser"] = dict(self.manager.laser.get_status())
        return json.dumps(out)

    def _on_poll_done(self, msg):
        try:
            d = json.loads(msg) if msg else {}
        except Exception:
            d = {}
        if "temp" in d:
            self._temp_status.setText(
                "● LakeShore：已连接；当前 {:.4f} K".format(d["temp"]))
        if "laser" in d:
            ls = d["laser"]
            self._laser_status.setText(
                "● 激光：已连接；功率 {:.2f} mW · 波长 {:.1f} nm · 输出 {}".format(
                    ls.get("power_mw", 0.0), ls.get("wavelength_nm", 0.0),
                    "ON" if ls.get("output_enabled") else "OFF"))
        self._finish_poll()

    def _finish_poll(self):
        self._polling = False
        self._poll_worker = None

    # ------------------------------------------------------------------
    # 后台任务辅助
    # ------------------------------------------------------------------

    def _run_task(self, fn, title):
        if self._task_worker is not None and self._task_worker.isRunning():
            QMessageBox.warning(self, "任务进行中", "请等待上一个任务完成。")
            return
        self._task_worker = _ControlWorker(fn, self)

        def _on_done(msg):
            self._task_worker = None
            self.update_connection_states()
            if msg:
                QMessageBox.information(self, title + " 完成", msg)

        def _on_failed(err):
            self._task_worker = None
            self.update_connection_states()
            QMessageBox.critical(self, title + " 失败", err)

        self._task_worker.done.connect(_on_done)
        self._task_worker.failed.connect(_on_failed)
        self._task_worker.start()

    def _save_addresses(self):
        cfg = self.manager.config or {}
        cfg["lakeshore_visa_address"] = self._ls_address.text().strip() or None
        cfg["laser_visa_address"] = self._laser_address.text().strip() or None
        if self.window.config is not None:
            self.window.config["lakeshore_visa_address"] = cfg["lakeshore_visa_address"]
            self.window.config["laser_visa_address"] = cfg["laser_visa_address"]
        gui_config.save_gui_config(self.window.config)

    # ------------------------------------------------------------------
    # LakeShore 操作
    # ------------------------------------------------------------------

    def _connect_lakeshore(self):
        cfg = dict(self.manager.config or {})
        cfg["lakeshore_visa_address"] = self._ls_address.text().strip() or None

        def fn():
            self.manager.connect_temp(cfg)
            t = self.manager.temp
            if getattr(t, "is_fixed", False):
                return "无硬件（地址为空），已启用固定温度后端。"
            T = t.get_temperature(cfg.get("lakeshore_channel", "A"))
            return "已连接 {}；当前 {:.4f} K".format(
                getattr(t, "identity", ""), T)

        self._save_addresses()
        self._run_task(fn, "连接 LakeShore")

    def _disconnect_lakeshore(self):
        def fn():
            self.manager.disconnect_temp()
            return "已断开 LakeShore。"
        self._run_task(fn, "断开 LakeShore")

    def _read_lakeshore(self):
        def fn():
            t = self.manager.temp
            if t is None or getattr(t, "is_fixed", False):
                raise RuntimeError("LakeShore 未连接。")
            channel = (self.manager.config or {}).get("lakeshore_channel", "A")
            T = t.get_temperature(channel)
            return "通道 {} = {:.4f} K".format(channel, T)
        self._run_task(fn, "读温度")

    def _set_lakeshore(self):
        def fn():
            t = self.manager.temp
            if t is None or getattr(t, "is_fixed", False):
                raise RuntimeError("LakeShore 未连接。")
            with self.manager.temp_lock:
                t.set_temperature(self._ls_setpoint.value())
            return "setpoint 已设为 {:.3f} K".format(self._ls_setpoint.value())
        self._run_task(fn, "设温")

    def _verify_lakeshore(self):
        def fn():
            t = self.manager.temp
            if t is None or getattr(t, "is_fixed", False):
                raise RuntimeError("LakeShore 未连接（地址为空时无硬件可用）。")
            channel = (self.manager.config or {}).get("lakeshore_channel", "A")
            with self.manager.temp_lock:
                T = t.get_temperature(channel)
                sp = T + 1.0
                t.set_temperature(sp)
                time.sleep(0.5)
                back = t.get_setpoint()
                t.set_temperature(T)   # 还原
            return ("温控验证通过\nidentity: {}\n通道 {} = {:.4f} K\n"
                    "设 setpoint {:.4f} K，回读 {:.4f} K".format(
                        getattr(t, "identity", "n/a"), channel, T, sp, back))
        self._run_task(fn, "验证 LakeShore")

    # ------------------------------------------------------------------
    # 激光操作
    # ------------------------------------------------------------------

    def _connect_laser(self):
        cfg = dict(self.manager.config or {})
        cfg["laser_visa_address"] = self._laser_address.text().strip() or None

        def fn():
            self.manager.connect_laser(cfg)
            l = self.manager.laser
            if getattr(l, "is_null", False):
                return "无硬件（地址为空），激光操作将跳过（NullLaser）。"
            return "已连接激光；当前状态：{}".format(
                json.dumps(l.get_status(), ensure_ascii=False))
        self._save_addresses()
        self._run_task(fn, "连接激光")

    def _disconnect_laser(self):
        def fn():
            self.manager.disconnect_laser()
            return "已断开激光。"
        self._run_task(fn, "断开激光")

    def _set_laser_power(self):
        self._laser_cmd("power")

    def _set_laser_wavelength(self):
        self._laser_cmd("wavelength")

    def _laser_cmd(self, cmd):
        def fn():
            l = self.manager.laser
            if l is None or getattr(l, "is_null", False):
                raise RuntimeError("激光未连接。")
            with self.manager.laser_lock:
                if cmd == "power":
                    l.set_power(self._laser_power.value())
                elif cmd == "wavelength":
                    l.set_wavelength(self._laser_wavelength.value())
                elif cmd == "on":
                    l.output_on()
                elif cmd == "off":
                    l.output_off()
            status = l.get_status()
            return "激光操作完成：{}".format(
                json.dumps(status, ensure_ascii=False))
        self._run_task(fn, "激光控制")

    def _verify_laser(self):
        def fn():
            l = self.manager.laser
            if l is None or getattr(l, "is_null", False):
                raise RuntimeError("激光未连接（地址为空时无硬件可用）。")
            with self.manager.laser_lock:
                l.set_wavelength(self._laser_wavelength.value())
                l.set_power(0.5)
                time.sleep(0.5)
                status = dict(l.get_status())
                l.output_off()
            return ("激光验证通过\n" +
                    json.dumps(status, ensure_ascii=False, indent=2))
        self._run_task(fn, "验证激光")
