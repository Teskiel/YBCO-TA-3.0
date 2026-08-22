"""PyQt5 graphical interface for P5002A_controller.P5002A.

Install dependencies in the same Python environment used by Spyder:
    pip install PyQt5 matplotlib pyvisa numpy

Keep this file and P5002A_controller.py in the same directory.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QColor, QCloseEvent
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QStatusBar, QVBoxLayout,
    QWidget, QPlainTextEdit,
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from P5002A_controller import (
    DEFAULT_P5002A_ADDRESS,
    P5002A,
    SParameterData,
)


class TaskThread(QThread):
    """Execute one blocking VISA operation without freezing the GUI."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, operation: Callable[[], object], parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.succeeded.emit(self.operation())
        except Exception as error:
            self.failed.emit(str(error), traceback.format_exc())


class P5002AWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.vna: Optional[P5002A] = None
        self.last_data: Optional[dict[str, SParameterData]] = None
        self.task: Optional[TaskThread] = None
        self.rf_enabled = False

        self.setWindowTitle("Keysight P5002A Vector Network Analyzer")
        self.resize(1250, 820)
        self._build_ui()
        self._set_connected(False)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_connection_group())

        body = QHBoxLayout()
        controls = QVBoxLayout()
        controls.addWidget(self._build_sweep_group())
        controls.addWidget(self._build_measurement_group())
        controls.addWidget(self._build_file_group())
        controls.addStretch(1)
        body.addLayout(controls, 0)

        self.figure = Figure(tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.ax_mag, self.ax_phase = self.figure.subplots(2, 1, sharex=True)
        self._prepare_empty_plot()
        body.addWidget(NavigationToolbar(self.canvas, self))
        body.addWidget(self.canvas, 1)
        root.addLayout(body, 1)

        self.command_log = QPlainTextEdit()
        self.command_log.setReadOnly(True)
        self.command_log.setMaximumHeight(150)
        self.command_log.setPlaceholderText(
            "SCPI command verification log will appear here after each operation."
        )
        root.addWidget(QLabel("SCPI command verification"))
        root.addWidget(self.command_log)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Not connected")

    def _build_connection_group(self):
        group = QGroupBox("Instrument connection")
        layout = QGridLayout(group)

        self.address = QLineEdit(DEFAULT_P5002A_ADDRESS)
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.refresh_state_button = QPushButton("Read current state")
        self.connection_light = QLabel("●")
        self.connection_text = QLabel("Disconnected")
        self.identity = QLabel("No instrument information")
        self.live_state = QLabel("Current instrument state: not read")
        self.live_state.setWordWrap(True)
        self.live_state.setStyleSheet("color: #666666")
        self.identity.setTextInteractionFlags(self.identity.textInteractionFlags())

        self.connect_button.clicked.connect(self.connect_instrument)
        self.disconnect_button.clicked.connect(self.disconnect_instrument)
        self.refresh_state_button.clicked.connect(self.refresh_instrument_status)

        layout.addWidget(QLabel("HiSLIP address"), 0, 0)
        layout.addWidget(self.address, 0, 1, 1, 5)
        layout.addWidget(self.connect_button, 0, 6)
        layout.addWidget(self.disconnect_button, 0, 7)
        layout.addWidget(self.connection_light, 1, 0)
        layout.addWidget(self.connection_text, 1, 1)
        layout.addWidget(self.identity, 1, 2, 1, 5)
        layout.addWidget(self.refresh_state_button, 1, 7)
        layout.addWidget(self.live_state, 2, 0, 1, 8)
        return group

    @staticmethod
    def _frequency_box(value_ghz: float):
        box = QDoubleSpinBox()
        box.setDecimals(9)
        box.setRange(0.000009, 1000.0)
        box.setValue(value_ghz)
        box.setSuffix(" GHz")
        box.setSingleStep(0.1)
        return box

    def _build_sweep_group(self):
        group = QGroupBox("Source and sweep")
        form = QFormLayout(group)

        self.mode = QComboBox()
        self.mode.addItems(["Linear sweep", "CW output"])
        self.output_port = QComboBox()
        self.output_port.addItem("Port 1 only", "PORT1")
        self.output_port.addItem("Port 2 only", "PORT2")
        self.output_port.addItem("Port 1 + Port 2", "BOTH")
        self.trigger_mode = QComboBox()
        self.trigger_mode.addItem("Continuous", True)
        self.trigger_mode.addItem("Hold", False)
        self.start_frequency = self._frequency_box(4.0)
        self.stop_frequency = self._frequency_box(6.0)
        self.cw_frequency = self._frequency_box(5.0)

        self.points = QSpinBox()
        self.points.setRange(1, 100001)
        self.points.setValue(1001)

        self.power = QDoubleSpinBox()
        self.power.setRange(-100.0, 30.0)
        self.power.setDecimals(2)
        self.power.setValue(-30.0)
        self.power.setSuffix(" dBm")

        self.ifbw = QDoubleSpinBox()
        self.ifbw.setRange(1.0, 15_000_000.0)
        self.ifbw.setDecimals(1)
        self.ifbw.setValue(1000.0)
        self.ifbw.setSuffix(" Hz")

        self.sweep_time = QDoubleSpinBox()
        self.sweep_time.setRange(100.0, 86400.0)
        self.sweep_time.setDecimals(3)
        self.sweep_time.setValue(100.0)
        self.sweep_time.setSuffix(" s")

        self.measurement_timeout = QSpinBox()
        self.measurement_timeout.setRange(10, 3600)
        self.measurement_timeout.setValue(300)
        self.measurement_timeout.setSuffix(" s")

        self.average_enabled = QCheckBox("Enable")
        self.average_count = QSpinBox()
        self.average_count.setRange(1, 65536)
        self.average_count.setValue(1)
        average_row = QHBoxLayout()
        average_row.addWidget(self.average_enabled)
        average_row.addWidget(self.average_count)

        self.apply_button = QPushButton("Apply settings")
        self.rf_button = QPushButton("RF output: OFF")
        self.apply_trigger_button = QPushButton("Apply trigger mode")
        self.single_trigger_button = QPushButton("Single trigger")
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.rf_button)
        trigger_buttons = QHBoxLayout()
        trigger_buttons.addWidget(self.apply_trigger_button)
        trigger_buttons.addWidget(self.single_trigger_button)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.output_port.currentIndexChanged.connect(self._output_port_changed)
        self.apply_button.clicked.connect(self.apply_settings)
        self.rf_button.clicked.connect(self.toggle_rf)
        self.apply_trigger_button.clicked.connect(self.apply_trigger_mode)
        self.single_trigger_button.clicked.connect(self.single_trigger)

        form.addRow("Output mode", self.mode)
        form.addRow("CW output selection", self.output_port)
        form.addRow("Trigger mode", self.trigger_mode)
        form.addRow("Start frequency", self.start_frequency)
        form.addRow("Stop frequency", self.stop_frequency)
        form.addRow("CW frequency", self.cw_frequency)
        form.addRow("Sweep points", self.points)
        form.addRow("Output power", self.power)
        form.addRow("IF bandwidth", self.ifbw)
        form.addRow("Sweep time (minimum 100 s)", self.sweep_time)
        form.addRow("Status fail-safe", self.measurement_timeout)
        form.addRow("Averaging", average_row)
        form.addRow(trigger_buttons)
        form.addRow(buttons)
        self._mode_changed()
        return group

    def _output_port_changed(self):
        """Immediately switch the real CW stimulus when Port 1/2 changes."""
        if self.vna is None or not self.vna.is_connected:
            return
        if self.task is not None and self.task.isRunning():
            return
        output_mode = str(self.output_port.currentData())
        output_port = 2 if output_mode == "PORT2" else 1

        def operation():
            vna = self._require_vna()
            vna.set_cw_output_mode(output_mode)
            return vna.read_status(port=output_port)

        self._run_task(
            operation,
            self._cw_port_changed,
            f"Switching CW output to {output_mode}...",
        )

    def _cw_port_changed(self, state):
        self._display_instrument_status(state)
        self.status.showMessage(
            "CW physical output verified: {} (Port1={}, Port2={})".format(
                state["output_mode"], state["port1_mode"], state["port2_mode"]
            )
        )

    def _build_measurement_group(self):
        group = QGroupBox("S-parameter measurement")
        layout = QGridLayout(group)

        self.curve_checks = {}
        for column, parameter in enumerate(("S11", "S21", "S12", "S22")):
            check = QCheckBox(parameter)
            check.setChecked(parameter == "S21")
            check.stateChanged.connect(self._plot_selected_curves)
            self.curve_checks[parameter] = check
            layout.addWidget(check, 0, column)

        self.measure_button = QPushButton("Measure all S-parameters")
        self.measure_button.clicked.connect(self.measure)

        layout.addWidget(QLabel("Displayed curves"), 1, 0, 1, 4)
        layout.addWidget(self.measure_button, 2, 0, 1, 4)
        return group

    def _build_file_group(self):
        group = QGroupBox("Data storage")
        layout = QGridLayout(group)

        self.folder = QLineEdit(str(Path.cwd()))
        browse = QPushButton("Browse")
        self.save_csv_button = QPushButton("Save CSV")
        self.save_s2p_button = QPushButton("Save .s2p")

        browse.clicked.connect(self.choose_folder)
        self.save_csv_button.clicked.connect(self.save_csv)
        self.save_s2p_button.clicked.connect(self.save_s2p)

        layout.addWidget(QLabel("Folder"), 0, 0)
        layout.addWidget(self.folder, 0, 1)
        layout.addWidget(browse, 0, 2)
        layout.addWidget(self.save_csv_button, 1, 0, 1, 2)
        layout.addWidget(self.save_s2p_button, 1, 2)
        return group

    def _prepare_empty_plot(self):
        self.ax_mag.clear()
        self.ax_phase.clear()
        self.ax_mag.set_ylabel("Magnitude (dB)")
        self.ax_phase.set_ylabel("Phase (deg)")
        self.ax_phase.set_xlabel("Frequency (GHz)")
        self.ax_mag.grid(True, alpha=0.3)
        self.ax_phase.grid(True, alpha=0.3)
        self.canvas.draw_idle()

    def _mode_changed(self):
        linear = self.mode.currentIndex() == 0
        if not linear:
            self.trigger_mode.setCurrentIndex(0)
        self.start_frequency.setEnabled(linear)
        self.stop_frequency.setEnabled(linear)
        self.points.setEnabled(linear)
        self.ifbw.setEnabled(linear)
        self.sweep_time.setEnabled(linear)
        self.cw_frequency.setEnabled(not linear)
        if hasattr(self, "measure_button"):
            self.measure_button.setEnabled(linear and self.vna is not None)

    def _set_connected(self, connected: bool):
        self.connect_button.setEnabled(not connected)
        self.disconnect_button.setEnabled(connected)
        self.address.setEnabled(not connected)
        color = QColor("#22a559" if connected else "#c23b3b")
        self.connection_light.setStyleSheet(f"color: {color.name()}; font-size: 20px")
        self.connection_text.setText("Connected" if connected else "Disconnected")
        for widget in (
            self.apply_button, self.rf_button, self.save_s2p_button,
            self.refresh_state_button, self.apply_trigger_button,
            self.single_trigger_button,
        ):
            widget.setEnabled(connected)
        self.save_csv_button.setEnabled(self.last_data is not None)
        self.save_s2p_button.setEnabled(connected and self.last_data is not None)
        self._mode_changed()

    def _set_busy(self, busy: bool, message: str = ""):
        self.centralWidget().setEnabled(not busy)
        if busy:
            self.status.showMessage(message or "Working...")

    def _run_task(self, operation, success, message):
        if self.task is not None and self.task.isRunning():
            QMessageBox.information(self, "Busy", "An instrument operation is already running.")
            return
        self._set_busy(True, message)
        self.task = TaskThread(operation, self)
        self.task.succeeded.connect(success)
        self.task.failed.connect(self._task_failed)
        self.task.finished.connect(self._task_finished)
        self.task.start()

    def _task_finished(self):
        self._refresh_command_log()
        self._set_busy(False)
        self.task = None

    def _refresh_command_log(self):
        if self.vna is None:
            return
        self.command_log.setPlainText("\n".join(self.vna.command_history[-200:]))
        bar = self.command_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _task_failed(self, message: str, details: str):
        self.status.showMessage("Operation failed")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("P5002A error")
        box.setText(message)
        box.setDetailedText(details)
        box.exec_()

    def _require_vna(self) -> P5002A:
        if self.vna is None or not self.vna.is_connected:
            raise RuntimeError("Connect the P5002A first.")
        return self.vna

    def connect_instrument(self):
        address = self.address.text().strip()
        output_mode = str(self.output_port.currentData())
        output_port = 2 if output_mode == "PORT2" else 1

        def operation():
            controller = P5002A(resource_name=address)
            try:
                identity = controller.connect()
                initial_status = controller.read_status(port=output_port)
                return controller, identity, initial_status
            except Exception:
                controller.disconnect()
                raise

        self._run_task(operation, self._connected, "Connecting to P5002A...")

    def _connected(self, result):
        self.vna, identity, initial_status = result
        self.identity.setText(identity)
        self._display_instrument_status(initial_status)
        self._set_connected(True)
        self.status.showMessage("P5002A connected; initial state loaded")

    def _display_instrument_status(self, state):
        """Populate all controls from values read from the connected VNA."""
        both_item = self.output_port.model().item(2)
        both_item.setEnabled(bool(state["dual_source_supported"]))
        both_item.setToolTip(
            "" if state["dual_source_supported"] else
            "Requires P5002A second-source Option 402"
        )
        output_index = self.output_port.findData(state["output_mode"])
        if output_index >= 0:
            self.output_port.blockSignals(True)
            self.output_port.setCurrentIndex(output_index)
            self.output_port.blockSignals(False)
        sweep_type = str(state["sweep_type"]).upper()
        self.mode.setCurrentIndex(1 if sweep_type in ("CW", "POIN") else 0)
        self.start_frequency.setValue(state["start_frequency_hz"] / 1e9)
        self.stop_frequency.setValue(state["stop_frequency_hz"] / 1e9)
        self.cw_frequency.setValue(state["cw_frequency_hz"] / 1e9)
        self.points.setValue(state["points"])
        self.power.setValue(state["power_dbm"])
        self.ifbw.setValue(state["if_bandwidth_hz"])
        self.sweep_time.setValue(max(100.0, state["sweep_time_s"]))
        self.average_enabled.setChecked(state["averaging_enabled"])
        self.average_count.setValue(state["average_count"])
        self.rf_enabled = state["output_enabled"]
        self.rf_button.setText(
            "RF output: ON" if self.rf_enabled else "RF output: OFF"
        )
        self.rf_button.setStyleSheet(
            "background-color: #cf4646; color: white" if self.rf_enabled else ""
        )
        mode_text = "CW output" if sweep_type in ("CW", "POIN") else sweep_type
        self.live_state.setText(
            "Current instrument state: mode {} (sweep state {}); CW output {} "
            "[P1 {} / {:.3f} dBm, P2 {} / {:.3f} dBm]; start {:.9f} GHz; "
            "stop {:.9f} GHz; CW {:.9f} GHz; points {}; selected power {:.3f} dBm; "
            "IFBW {:.3f} Hz; sweep time {:.3f} s (auto {}); "
            "averaging {} (count {}); trigger {}; RF {}".format(
                mode_text,
                state["sweep_mode"],
                state["output_mode"],
                state["port1_mode"],
                state["port1_power_dbm"],
                state["port2_mode"],
                state["port2_power_dbm"],
                state["start_frequency_hz"] / 1e9,
                state["stop_frequency_hz"] / 1e9,
                state["cw_frequency_hz"] / 1e9,
                state["points"],
                state["power_dbm"],
                state["if_bandwidth_hz"],
                state["sweep_time_s"],
                "ON" if state["sweep_time_auto"] else "OFF",
                "ON" if state["averaging_enabled"] else "OFF",
                state["average_count"],
                "CONTINUOUS" if state["trigger_continuous"] else "HOLD",
                "ON" if state["output_enabled"] else "OFF",
            )
        )
        self.live_state.setStyleSheet("color: #16833b; font-weight: bold")
        if not state["dual_source_supported"]:
            self.live_state.setText(
                self.live_state.text() +
                "; dual output unavailable (Option 402 not installed)"
            )
        self._mode_changed()
        self.trigger_mode.setCurrentIndex(
            0 if state["trigger_continuous"] else 1
        )

    def refresh_instrument_status(self):
        """Read the live VNA state and refresh the shared control widgets."""
        if self.vna is None or not self.vna.is_connected:
            return
        if self.task is not None and self.task.isRunning():
            return
        output_mode = str(self.output_port.currentData())
        output_port = 2 if output_mode == "PORT2" else 1
        self._run_task(
            lambda: self._require_vna().read_status(port=output_port),
            self._status_refreshed,
            "Reading current P5002A state...",
        )

    def _status_refreshed(self, state):
        self._display_instrument_status(state)
        self.status.showMessage("Current P5002A state loaded")

    def disconnect_instrument(self):
        if self.vna is None:
            return
        controller = self.vna

        def operation():
            try:
                controller.set_output(False)
            finally:
                controller.disconnect()
            return None

        self._run_task(operation, self._disconnected, "Disconnecting...")

    def _disconnected(self, _result=None):
        self.vna = None
        self.rf_enabled = False
        self.rf_button.setText("RF output: OFF")
        self.identity.setText("No instrument information")
        self.live_state.setText("Current instrument state: not read")
        self.live_state.setStyleSheet("color: #666666")
        self._set_connected(False)
        self.status.showMessage("Disconnected")

    def _current_settings(self):
        """Copy widget values on the GUI thread before starting a worker."""
        return {
            "linear": self.mode.currentIndex() == 0,
            "output_mode": str(self.output_port.currentData()),
            "output_port": 2 if self.output_port.currentData() == "PORT2" else 1,
            "trigger_continuous": bool(self.trigger_mode.currentData()),
            "start_hz": self.start_frequency.value() * 1e9,
            "stop_hz": self.stop_frequency.value() * 1e9,
            "cw_hz": self.cw_frequency.value() * 1e9,
            "points": self.points.value(),
            "power_dbm": self.power.value(),
            "ifbw_hz": self.ifbw.value(),
            "sweep_time_s": self.sweep_time.value(),
            "average_enabled": self.average_enabled.isChecked(),
            "average_count": self.average_count.value(),
            "timeout_ms": self.measurement_timeout.value() * 1000,
        }

    def _settings_operation(self, settings):
        vna = self._require_vna()
        if settings["linear"]:
            vna.configure_linear_sweep(
                settings["start_hz"], settings["stop_hz"],
                settings["points"], settings["ifbw_hz"],
                settings["power_dbm"], settings["sweep_time_s"],
            )
            vna.set_averaging(
                settings["average_count"], settings["average_enabled"]
            )
        else:
            vna.configure_cw(
                settings["cw_hz"], settings["power_dbm"],
                port=settings["output_port"],
                output_mode=settings["output_mode"],
            )
            settings["trigger_continuous"] = True
        vna.set_trigger_continuous(settings["trigger_continuous"])
        errors = vna.check_errors()
        if errors:
            raise RuntimeError("\n".join(errors))
        return None

    def apply_trigger_mode(self):
        continuous = bool(self.trigger_mode.currentData())

        output_mode = str(self.output_port.currentData())
        output_port = 2 if output_mode == "PORT2" else 1
        def safe_operation():
            vna = self._require_vna()
            vna.set_trigger_continuous(continuous)
            return vna.read_status(port=output_port)
        self._run_task(safe_operation, self._status_refreshed, "Applying trigger mode...")

    def single_trigger(self):
        output_mode = str(self.output_port.currentData())
        output_port = 2 if output_mode == "PORT2" else 1
        def operation():
            vna = self._require_vna()
            vna.trigger_single()
            return vna.read_status(port=output_port)
        self._run_task(operation, self._status_refreshed, "Sending single trigger...")

    def apply_settings(self):
        settings = self._current_settings()
        def operation():
            self._settings_operation(settings)
            return self._require_vna().read_status(port=settings["output_port"])
        self._run_task(
            operation,
            self._settings_applied,
            "Applying settings...",
        )

    def _settings_applied(self, state):
        self._display_instrument_status(state)
        self.status.showMessage("Settings applied and read back")

    def toggle_rf(self):
        target = not self.rf_enabled

        def operation():
            self._require_vna().set_output(target)
            return target

        self._run_task(operation, self._rf_changed, "Changing RF output...")

    def _rf_changed(self, enabled):
        self.rf_enabled = bool(enabled)
        self.rf_button.setText(f"RF output: {'ON' if enabled else 'OFF'}")
        self.rf_button.setStyleSheet(
            "background-color: #cf4646; color: white" if enabled else ""
        )
        self.status.showMessage(f"RF output {'enabled' if enabled else 'disabled'}")

    def measure(self):
        settings = self._current_settings()
        measurement_prefix = f"GUI_{datetime.now().strftime('%H%M%S%f')}"

        def operation():
            self._settings_operation(settings)
            return self._require_vna().read_all_s_parameters(
                measurement_prefix=measurement_prefix,
                timeout_ms=settings["timeout_ms"],
            )

        self._run_task(operation, self._measurement_ready, "Measuring S11/S21/S12/S22...")

    def _measurement_ready(self, data: dict[str, SParameterData]):
        self.last_data = data
        self._plot_selected_curves()
        self.save_csv_button.setEnabled(True)
        self.save_s2p_button.setEnabled(True)
        points = len(data["S11"].frequency_hz)
        self.status.showMessage(f"All four S-parameters acquired: {points} points each")

    def _plot_selected_curves(self):
        if self.last_data is None:
            return
        self.ax_mag.clear()
        self.ax_phase.clear()
        colors = {
            "S11": "#1565c0",
            "S21": "#c45100",
            "S12": "#2e7d32",
            "S22": "#7b1fa2",
        }
        selected = [
            parameter for parameter, check in self.curve_checks.items()
            if check.isChecked()
        ]
        for parameter in selected:
            data = self.last_data[parameter]
            frequency_ghz = data.frequency_hz / 1e9
            self.ax_mag.plot(
                frequency_ghz, data.magnitude_db,
                color=colors[parameter], label=parameter,
            )
            self.ax_phase.plot(
                frequency_ghz, data.phase_deg,
                color=colors[parameter], label=parameter,
            )
        self.ax_mag.set_ylabel("Magnitude (dB)")
        self.ax_phase.set_ylabel("Phase (deg)")
        self.ax_phase.set_xlabel("Frequency (GHz)")
        self.ax_mag.grid(True, alpha=0.3)
        self.ax_phase.grid(True, alpha=0.3)
        if selected:
            self.ax_mag.legend(loc="best")
            self.ax_phase.legend(loc="best")
        self.canvas.draw_idle()

    def choose_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "Select data folder", self.folder.text())
        if selected:
            self.folder.setText(selected)

    def _default_stem(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path(self.folder.text().strip()) / f"P5002A_2Port_{stamp}"

    def save_csv(self):
        if self.last_data is None:
            QMessageBox.information(self, "No data", "Perform a measurement first.")
            return
        default = str(self._default_stem().with_suffix(".csv"))
        filename, _ = QFileDialog.getSaveFileName(self, "Save CSV", default, "CSV files (*.csv)")
        if not filename:
            return
        try:
            if self.vna is None:
                raise RuntimeError("The P5002A is not connected.")
            path = self.vna.save_all_csv(self.last_data, filename)
            self.status.showMessage(f"CSV saved: {path}")
        except Exception as error:
            QMessageBox.critical(self, "Save failed", str(error))

    def save_s2p(self):
        if self.last_data is None:
            QMessageBox.information(self, "No data", "Perform a measurement first.")
            return
        default = str(self._default_stem().with_suffix(".s2p"))
        filename, _ = QFileDialog.getSaveFileName(self, "Save Touchstone", default, "Touchstone (*.s2p)")
        if not filename:
            return

        try:
            path = self._require_vna().save_s2p_data(self.last_data, filename)
            self.status.showMessage(f"Touchstone saved: {path}")
        except Exception as error:
            QMessageBox.critical(self, "Save failed", str(error))

    def closeEvent(self, event: QCloseEvent):
        if self.task is not None and self.task.isRunning():
            QMessageBox.warning(self, "Instrument busy", "Wait for the current operation to finish.")
            event.ignore()
            return
        if self.vna is not None:
            try:
                self.vna.set_output(False)
            except Exception:
                pass
            self.vna.disconnect()
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = P5002AWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())