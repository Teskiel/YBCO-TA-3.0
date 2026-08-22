# -*- coding: utf-8 -*-
"""
Created on Mon May 11 14:36:16 2026

@author: DELL
"""

"""
PyQt control panel for a Lake Shore Model 335 Temperature Controller.

Requirements:
    pip install pyqt5 pyvisa pyvisa-py

Run:
    python lakeshore335_pyqt_control.py

Typical VISA addresses:
    USB virtual COM on Windows: ASRL3::INSTR, ASRL4::INSTR, ...
    GPIB: GPIB0::12::INSTR or GPIB::12::INSTR
"""
"""
PyQt control panel for a Lake Shore Model 335 Temperature Controller.

Requirements:
    pip install pyqt5 pyvisa pyvisa-py

Run:
    python lakeshore335_pyqt_control.py

Typical VISA addresses:
    USB virtual COM on Windows: ASRL3::INSTR, ASRL4::INSTR, ...
    GPIB: GPIB0::12::INSTR or GPIB::12::INSTR
"""
"""
PyQt control panel for a Lake Shore Model 335 Temperature Controller.

Requirements:
    pip install pyqt5 pyvisa pyvisa-py

Run:
    python lakeshore335_pyqt_control.py

Typical VISA addresses:
    USB virtual COM on Windows: ASRL3::INSTR, ASRL4::INSTR, ...
    GPIB: GPIB0::12::INSTR or GPIB::12::INSTR
"""

import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import pyvisa
from pyvisa.constants import Parity, StopBits
from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LakeShore335:
    """Small PyVISA driver for common Lake Shore 335 operations."""

    def __init__(self, visa_address: str, resource_manager: Optional[pyvisa.ResourceManager] = None):
        if not visa_address:
            raise ValueError("visa_address must not be empty")

        self.visa_address = visa_address
        self.resource_manager = resource_manager or pyvisa.ResourceManager()
        self.device = self.resource_manager.open_resource(visa_address)

        # The 335 USB interface appears as a virtual serial port.
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

    def write(self, command: str) -> None:
        self.device.write(command)

    def query(self, command: str) -> str:
        return self.device.query(command).strip()

    def get_temperature(self, channel: str = "A") -> float:
        return float(self.query(f"KRDG? {channel}"))

    def set_temperature(self, setpoint: float, loop: int = 1) -> None:
        self.write(f"SETP {loop},{setpoint}")

    def get_setpoint(self, loop: int = 1) -> float:
        return float(self.query(f"SETP? {loop}"))

    def set_heater_range(self, output: int, range_level: int) -> None:
        # 0 = off, 1 = low, 2 = medium, 3 = high
        self.write(f"RANGE {output},{range_level}")

    def get_heater_range(self, output: int = 1) -> int:
        return int(float(self.query(f"RANGE? {output}")))

    def get_heater_percent(self, output: int = 1) -> float:
        return float(self.query(f"HTR? {output}"))

    def set_pid(self, p: float, i: float, d: float, loop: int = 1) -> None:
        self.write(f"PID {loop},{p},{i},{d}")

    def get_pid(self, loop: int = 1) -> Tuple[float, float, float]:
        values = self.query(f"PID? {loop}").split(",")
        if len(values) != 3:
            raise RuntimeError(f"Unexpected PID response: {values!r}")
        return tuple(map(float, values))  # type: ignore[return-value]

    def all_heaters_off(self) -> None:
        # The safest generic approach: explicitly set both output ranges to off.
        self.set_heater_range(1, 0)
        self.set_heater_range(2, 0)

    def close(self) -> None:
        try:
            self.device.close()
        finally:
            pass


@dataclass
class Reading:
    temperature_a: Optional[float] = None
    temperature_b: Optional[float] = None
    heater_1: Optional[float] = None
    heater_2: Optional[float] = None
    setpoint_1: Optional[float] = None
    setpoint_2: Optional[float] = None
    range_1: Optional[int] = None
    range_2: Optional[int] = None


class DeviceWorker(QObject):
    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    reading = pyqtSignal(object)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.controller: Optional[LakeShore335] = None

    @pyqtSlot(str)
    def connect_device(self, visa_address: str) -> None:
        try:
            self.controller = LakeShore335(visa_address=visa_address)
            self.connected.emit(self.controller.identity)
            self.log.emit(f"Connected: {self.controller.identity}")
        except Exception as exc:
            self.controller = None
            self.error.emit(f"Connection failed: {exc}")

    @pyqtSlot()
    def disconnect_device(self) -> None:
        if self.controller is not None:
            try:
                self.controller.close()
            except Exception as exc:
                self.error.emit(f"Disconnect error: {exc}")
        self.controller = None
        self.disconnected.emit()
        self.log.emit("Disconnected")

    @pyqtSlot()
    def poll(self) -> None:
        if self.controller is None:
            return
        try:
            r = Reading(
                temperature_a=self.controller.get_temperature("A"),
                temperature_b=self.controller.get_temperature("B"),
                heater_1=self.controller.get_heater_percent(1),
                heater_2=self.controller.get_heater_percent(2),
                setpoint_1=self.controller.get_setpoint(1),
                setpoint_2=self.controller.get_setpoint(2),
                range_1=self.controller.get_heater_range(1),
                range_2=self.controller.get_heater_range(2),
            )
            self.reading.emit(r)
        except Exception as exc:
            self.error.emit(f"Poll failed: {exc}")

    @pyqtSlot(int, float)
    def set_setpoint(self, loop: int, setpoint: float) -> None:
        if self.controller is None:
            self.error.emit("Not connected")
            return
        try:
            self.controller.set_temperature(setpoint, loop)
            self.log.emit(f"Set loop {loop} setpoint to {setpoint:.4f} K")
        except Exception as exc:
            self.error.emit(f"Setpoint write failed: {exc}")

    @pyqtSlot(int, int)
    def set_heater_range(self, output: int, range_level: int) -> None:
        if self.controller is None:
            self.error.emit("Not connected")
            return
        try:
            self.controller.set_heater_range(output, range_level)
            self.log.emit(f"Set output {output} heater range to {range_level}")
        except Exception as exc:
            self.error.emit(f"Heater range write failed: {exc}")

    @pyqtSlot(int, float, float, float)
    def set_pid(self, loop: int, p: float, i: float, d: float) -> None:
        if self.controller is None:
            self.error.emit("Not connected")
            return
        try:
            self.controller.set_pid(p, i, d, loop)
            self.log.emit(f"Set loop {loop} PID to P={p:g}, I={i:g}, D={d:g}")
        except Exception as exc:
            self.error.emit(f"PID write failed: {exc}")

    @pyqtSlot()
    def read_pid(self) -> None:
        if self.controller is None:
            self.error.emit("Not connected")
            return
        try:
            p1, i1, d1 = self.controller.get_pid(1)
            p2, i2, d2 = self.controller.get_pid(2)
            self.log.emit(f"Loop 1 PID: P={p1:g}, I={i1:g}, D={d1:g}")
            self.log.emit(f"Loop 2 PID: P={p2:g}, I={i2:g}, D={d2:g}")
        except Exception as exc:
            self.error.emit(f"PID read failed: {exc}")

    @pyqtSlot()
    def all_heaters_off(self) -> None:
        if self.controller is None:
            self.error.emit("Not connected")
            return
        try:
            self.controller.all_heaters_off()
            self.log.emit("Both heater ranges set to OFF")
        except Exception as exc:
            self.error.emit(f"All-off failed: {exc}")


class MainWindow(QMainWindow):
    request_connect = pyqtSignal(str)
    request_disconnect = pyqtSignal()
    request_poll = pyqtSignal()
    request_setpoint = pyqtSignal(int, float)
    request_heater_range = pyqtSignal(int, int)
    request_pid = pyqtSignal(int, float, float, float)
    request_read_pid = pyqtSignal()
    request_all_off = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lake Shore 335 Control")
        self.resize(860, 620)

        self.worker_thread = QThread(self)
        self.worker = DeviceWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.start()

        self._build_ui()
        self._wire_signals()
        

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self.request_poll)
        
        self._set_connected_state(False)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        conn_box = QGroupBox("Connection")
        conn_layout = QHBoxLayout(conn_box)
        self.address_edit = QLineEdit("ASRL4::INSTR")
        self.address_edit.setPlaceholderText("ASRL3::INSTR or GPIB0::12::INSTR")
        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.status_label = QLabel("Not connected")
        conn_layout.addWidget(QLabel("VISA address:"))
        conn_layout.addWidget(self.address_edit, 1)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addWidget(self.disconnect_btn)
        conn_layout.addWidget(self.status_label, 2)
        layout.addWidget(conn_box)

        read_box = QGroupBox("Live readings")
        read_layout = QGridLayout(read_box)
        self.temp_a_label = QLabel("-- K")
        self.temp_b_label = QLabel("-- K")
        self.htr1_label = QLabel("-- %")
        self.htr2_label = QLabel("-- %")
        self.sp1_label = QLabel("-- K")
        self.sp2_label = QLabel("-- K")
        self.range1_label = QLabel("--")
        self.range2_label = QLabel("--")
        labels = [
            ("Input A", self.temp_a_label), ("Input B", self.temp_b_label),
            ("Heater 1", self.htr1_label), ("Heater 2", self.htr2_label),
            ("Setpoint 1", self.sp1_label), ("Setpoint 2", self.sp2_label),
            ("Range 1", self.range1_label), ("Range 2", self.range2_label),
        ]
        for row, (name, widget) in enumerate(labels):
            read_layout.addWidget(QLabel(name + ":"), row // 2, (row % 2) * 2)
            read_layout.addWidget(widget, row // 2, (row % 2) * 2 + 1)
        layout.addWidget(read_box)

        control_row = QHBoxLayout()
        control_row.addWidget(self._make_setpoint_box(1))
        control_row.addWidget(self._make_setpoint_box(2))
        control_row.addWidget(self._make_heater_box())
        layout.addLayout(control_row)

        pid_row = QHBoxLayout()
        pid_row.addWidget(self._make_pid_box(1))
        pid_row.addWidget(self._make_pid_box(2))
        layout.addLayout(pid_row)

        self.all_off_btn = QPushButton("EMERGENCY: all heaters OFF")
        layout.addWidget(self.all_off_btn)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

    def _make_setpoint_box(self, loop: int) -> QGroupBox:
        box = QGroupBox(f"Loop {loop} setpoint")
        form = QFormLayout(box)
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 2000.0)
        spin.setDecimals(4)
        spin.setSuffix(" K")
        spin.setSingleStep(0.1)
        btn = QPushButton(f"Apply loop {loop}")
        setattr(self, f"setpoint_spin_{loop}", spin)
        setattr(self, f"setpoint_btn_{loop}", btn)
        form.addRow("Target:", spin)
        form.addRow(btn)
        return box

    def _make_heater_box(self) -> QGroupBox:
        box = QGroupBox("Heater range")
        form = QFormLayout(box)
        self.output_combo = QComboBox()
        self.output_combo.addItems(["1", "2"])
        self.range_combo = QComboBox()
        self.range_combo.addItems(["0 - Off", "1 - Low", "2 - Medium", "3 - High"])
        self.range_btn = QPushButton("Apply range")
        form.addRow("Output:", self.output_combo)
        form.addRow("Range:", self.range_combo)
        form.addRow(self.range_btn)
        return box

    def _make_pid_box(self, loop: int) -> QGroupBox:
        box = QGroupBox(f"Loop {loop} PID")
        form = QFormLayout(box)
        p = QDoubleSpinBox(); p.setRange(0, 10000); p.setDecimals(4); p.setValue(10.0)
        i = QDoubleSpinBox(); i.setRange(0, 10000); i.setDecimals(4); i.setValue(20.0)
        d = QDoubleSpinBox(); d.setRange(0, 10000); d.setDecimals(4); d.setValue(0.0)
        btn = QPushButton(f"Apply PID loop {loop}")
        read_btn = QPushButton("Read both PID")
        setattr(self, f"pid_p_{loop}", p)
        setattr(self, f"pid_i_{loop}", i)
        setattr(self, f"pid_d_{loop}", d)
        setattr(self, f"pid_btn_{loop}", btn)
        if loop == 1:
            self.read_pid_btn = read_btn
        form.addRow("P:", p)
        form.addRow("I:", i)
        form.addRow("D:", d)
        form.addRow(btn)
        if loop == 1:
            form.addRow(read_btn)
        return box

    def _wire_signals(self) -> None:
        self.connect_btn.clicked.connect(lambda: self.request_connect.emit(self.address_edit.text().strip()))
        self.disconnect_btn.clicked.connect(self.request_disconnect)
        self.request_connect.connect(self.worker.connect_device)
        self.request_disconnect.connect(self.worker.disconnect_device)
        self.request_poll.connect(self.worker.poll)
        self.request_setpoint.connect(self.worker.set_setpoint)
        self.request_heater_range.connect(self.worker.set_heater_range)
        self.request_pid.connect(self.worker.set_pid)
        self.request_read_pid.connect(self.worker.read_pid)
        self.request_all_off.connect(self.worker.all_heaters_off)

        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(lambda: self._set_connected_state(False))
        self.worker.reading.connect(self._update_reading)
        self.worker.error.connect(self._show_error)
        self.worker.log.connect(self._log)

        self.setpoint_btn_1.clicked.connect(lambda: self.request_setpoint.emit(1, self.setpoint_spin_1.value()))
        self.setpoint_btn_2.clicked.connect(lambda: self.request_setpoint.emit(2, self.setpoint_spin_2.value()))
        self.range_btn.clicked.connect(self._apply_range)
        self.pid_btn_1.clicked.connect(lambda: self._apply_pid(1))
        self.pid_btn_2.clicked.connect(lambda: self._apply_pid(2))
        self.read_pid_btn.clicked.connect(self.request_read_pid)
        self.all_off_btn.clicked.connect(self._confirm_all_off)

    def _set_connected_state(self, connected: bool) -> None:
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        for widget in [
            self.setpoint_btn_1, self.setpoint_btn_2, self.range_btn,
            self.pid_btn_1, self.pid_btn_2, self.read_pid_btn, self.all_off_btn,
        ]:
            widget.setEnabled(connected)
        self.status_label.setText("Connected" if connected else "Not connected")
        if connected:
            self.poll_timer.start()
        else:
            self.poll_timer.stop()

    def _on_connected(self, identity: str) -> None:
        self.status_label.setText(identity)
        self._set_connected_state(True)
        self.request_poll.emit()

    def _apply_range(self) -> None:
        output = int(self.output_combo.currentText())
        range_level = self.range_combo.currentIndex()
        self.request_heater_range.emit(output, range_level)

    def _apply_pid(self, loop: int) -> None:
        p = getattr(self, f"pid_p_{loop}").value()
        i = getattr(self, f"pid_i_{loop}").value()
        d = getattr(self, f"pid_d_{loop}").value()
        self.request_pid.emit(loop, p, i, d)

    def _confirm_all_off(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Confirm heater off",
            "Set both heater output ranges to OFF?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.request_all_off.emit()

    def _update_reading(self, r: Reading) -> None:
        self.temp_a_label.setText(self._fmt(r.temperature_a, "K", 4))
        self.temp_b_label.setText(self._fmt(r.temperature_b, "K", 4))
        self.htr1_label.setText(self._fmt(r.heater_1, "%", 2))
        self.htr2_label.setText(self._fmt(r.heater_2, "%", 2))
        self.sp1_label.setText(self._fmt(r.setpoint_1, "K", 4))
        self.sp2_label.setText(self._fmt(r.setpoint_2, "K", 4))
        self.range1_label.setText(str(r.range_1) if r.range_1 is not None else "--")
        self.range2_label.setText(str(r.range_2) if r.range_2 is not None else "--")

    @staticmethod
    def _fmt(value: Optional[float], unit: str, decimals: int) -> str:
        if value is None:
            return f"-- {unit}"
        return f"{value:.{decimals}f} {unit}"

    def _show_error(self, message: str) -> None:
        self._log("ERROR: " + message)
        QMessageBox.critical(self, "Lake Shore 335 error", message)

    def _log(self, message: str) -> None:
        self.log_text.append(time.strftime("[%H:%M:%S] ") + message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self.poll_timer.stop()
        self.request_disconnect.emit()
        self.worker_thread.quit()
        self.worker_thread.wait(2000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
