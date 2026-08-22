"""KID measurement GUI v2: shared E8257D and PXIe-4480 connections."""

"""KID measurement GUI v2: shared E8257D and PXIe-4480 connections."""

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAction, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget, QPlainTextEdit,
)

from E8257D_controller import E8257D, list_visa_resources
from P5002A_gui import P5002AWindow
from PXIE4480_controller import PXIe4480
from IQ_calibration import IQCalibrationTable, IQEllipseCalibrator
from S21_fitting import ScrapsS21Fitter, S21NoiseCalibration, welch_psd

import kid_measurement_core as core

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
DATA_DIRECTORY = SCRIPT_DIRECTORY / "data"
IQ_DATA_DIRECTORY = DATA_DIRECTORY / "IQ_calibration"
S21_DATA_DIRECTORY = DATA_DIRECTORY / "S21"
NOISE_DATA_DIRECTORY = DATA_DIRECTORY / "noise"
for _directory in (IQ_DATA_DIRECTORY, S21_DATA_DIRECTORY, NOISE_DATA_DIRECTORY):
    _directory.mkdir(parents=True, exist_ok=True)

# 参数持久化：用户在各 Tab 手调的值存这里，重启还原；noisesweep 也可继承。
GUI_SETTINGS_FILE = SCRIPT_DIRECTORY / "kid_gui_settings.json"


def latest_iq_summary():
    files = list(IQ_DATA_DIRECTORY.glob("IQ_scan_summary-*.txt"))
    return str(max(files, key=lambda item: item.stat().st_mtime)) if files else ""


def latest_fitted_s21():
    for path in sorted(S21_DATA_DIRECTORY.glob("*.h5"),
                       key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with h5py.File(path, "r") as h:
                if ("scraps_fit" in h and "x0" in h["scraps_fit"].attrs
                        and "y0" in h["scraps_fit"].attrs):
                    return str(path)
        except OSError:
            pass
    return ""


def output_path(folder, kind, frequency):
    directory = Path(folder).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stem = "{}-{}-{}".format(datetime.now().strftime("%Y%m%d%H%M%S"), kind, frequency)
    path = directory / (stem + ".h5")
    number = 1
    while path.exists():
        path = directory / ("{}-{:02d}.h5".format(stem, number))
        number += 1
    return path


def s21_resonance_path(path, resonance_frequency_hz, resonator_name,
                       temperature_k, readout_power_dbm):
    """Return a collision-free filename keyed by resonator and conditions."""
    source = Path(path)
    safe_name = "".join(
        character if character not in '<>:"/\\|?*' else "_"
        for character in str(resonator_name).strip()
    ) or "res0"
    frequency_text = "{:.5f}".format(float(resonance_frequency_hz)/1e9)
    temperature_text = "{:.3f}".format(float(temperature_k)*1000).rstrip("0").rstrip(".")
    power_text = "{:.3f}".format(float(readout_power_dbm)).rstrip("0").rstrip(".")
    stem = "{}-{}GHz-{}mK-{}dBm".format(
        safe_name, frequency_text, temperature_text, power_text,
    )
    target = source.with_name(stem+".h5")
    number = 1
    while target.exists() and target != source:
        target = source.with_name("{}-{:02d}.h5".format(stem, number))
        number += 1
    return target


def _gui_snapshots(window):
    """返回 {键: 取值函数} 映射，保存各 Tab 控件当前值。"""
    s21, noise, combined, iq = window.s21, window.noise, window.combined, window.iq
    px = window.daq_dialog

    def channels():
        return [i for i, c in enumerate(px.channels) if c.isChecked()]

    return {
        "daq.device_name": lambda: px.device.text().strip(),
        "daq.sample_rate": lambda: px.rate.value(),
        "daq.voltage_range": lambda: px.voltage.currentData(),
        "daq.coupling": lambda: px.coupling.currentText(),
        "daq.trigger_mode": lambda: "DIGITAL" if px.trigger.currentIndex() == 1 else "IMMEDIATE",
        "daq.trigger_source": lambda: px.source.text().strip() if px.trigger.currentIndex() == 1 else None,
        "daq.trigger_edge": lambda: px.edge.currentText(),
        "daq.channels": channels,

        "s21.center_f_ghz": lambda: s21.center_f.value(),
        "s21.bandwidth_mhz": lambda: s21.bandwidth_mhz.value(),
        "s21.points": lambda: s21.points.value(),
        "s21.power_dbm": lambda: s21.power.value(),
        "s21.settle_s": lambda: s21.settle.value(),
        "s21.samples": lambda: s21.samples.value(),
        "s21.i_channel": lambda: int(s21.i.currentData()),
        "s21.q_channel": lambda: int(s21.q.currentData()),
        "s21.fit_enabled": lambda: s21.fit_enabled.isChecked(),
        "s21.resonator_name": lambda: s21.resonator_name.text().strip(),
        "s21.temperature_mk": lambda: s21.temperature.value(),
        "s21.fit_power_dbm": lambda: s21.fit_power.value(),
        "s21.folder": lambda: s21.folder.text().strip(),
        "s21.calibration_file": lambda: s21.calibration_file.text().strip(),

        "noise.frequency_mode": lambda: str(noise.frequency_mode.currentData()),
        "noise.frequency_ghz": lambda: noise.frequency.value(),
        "noise.power_dbm": lambda: noise.power.value(),
        "noise.settle_s": lambda: noise.settle.value(),
        "noise.mode": lambda: noise.mode.currentText(),
        "noise.duration_s": lambda: noise.duration.value(),
        "noise.block": lambda: noise.block.value(),
        "noise.psd_window": lambda: noise.psd_window.currentText(),
        "noise.segment_seconds": lambda: noise.segment_seconds.value(),
        "noise.calibration_file": lambda: noise.calibration_file.text().strip(),
        "noise.s21_file": lambda: noise.s21_file.text().strip(),

        "combined.center_ghz": lambda: combined.center.value(),
        "combined.bandwidth_mhz": lambda: combined.bandwidth.value(),
        "combined.points": lambda: combined.points.value(),
        "combined.samples": lambda: combined.samples.value(),
        "combined.power_dbm": lambda: combined.power.value(),
        "combined.settle_s": lambda: combined.settle.value(),
        "combined.noise_location": lambda: str(combined.noise_location.currentData()),
        "combined.noise_duration_s": lambda: combined.noise_duration.value(),
        "combined.noise_block": lambda: combined.noise_block.value(),
        "combined.window_box": lambda: combined.window_box.currentText(),
        "combined.segment": lambda: combined.segment.value(),
        "combined.calibration_file": lambda: combined.calibration_file.text().strip(),
        "combined.folder": lambda: combined.folder.text().strip(),

        "iq.mode": lambda: iq.mode.currentIndex(),
        "iq.frequency_ghz": lambda: iq.frequency.value(),
        "iq.start_frequency_ghz": lambda: iq.start_frequency.value(),
        "iq.stop_frequency_ghz": lambda: iq.stop_frequency.value(),
        "iq.frequency_points": lambda: iq.frequency_points.value(),
        "iq.e8257d_power_dbm": lambda: iq.e8257d_power.value(),
        "iq.p5002a_power_dbm": lambda: iq.p5002a_power.value(),
        "iq.sample_count": lambda: iq.sample_count.value(),
        "iq.settling_time_s": lambda: iq.settling_time.value(),
        "iq.i_channel": lambda: int(iq.i_channel.currentData()),
        "iq.q_channel": lambda: int(iq.q_channel.currentData()),
        "iq.folder": lambda: iq.folder.text().strip(),
    }


def save_gui_settings(window):
    """把各 Tab 控件当前值写入 kid_gui_settings.json（原子写 .tmp + os.replace）。"""
    settings = {}
    for key, getter in _gui_snapshots(window).items():
        try:
            settings[key] = getter()
        except Exception:
            pass
    try:
        tmp = GUI_SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(GUI_SETTINGS_FILE))
    except Exception:
        pass


def load_gui_settings(window):
    """启动时用 kid_gui_settings.json 还原控件；缺失/损坏静默回落硬编码默认，绝不阻断启动。"""
    try:
        data = json.loads(GUI_SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return

    s21, noise, combined, iq = window.s21, window.noise, window.combined, window.iq
    px = window.daq_dialog

    def restore(key, apply):
        try:
            if key in data and data[key] is not None:
                apply(data[key])
        except Exception:
            pass

    restore("daq.device_name", lambda v: px.device.setText(str(v)))
    restore("daq.sample_rate", lambda v: px.rate.setValue(float(v)))
    restore("daq.voltage_range",
            lambda v: px.voltage.setCurrentIndex(max(0, px.voltage.findData(float(v)))))
    restore("daq.coupling", lambda v: px.coupling.setCurrentText(str(v)))
    restore("daq.trigger_mode",
            lambda v: px.trigger.setCurrentIndex(1 if v == "DIGITAL" else 0))
    restore("daq.trigger_source", lambda v: px.source.setText(str(v)))
    restore("daq.trigger_edge", lambda v: px.edge.setCurrentText(str(v)))
    restore("daq.channels",
            lambda v: [c.setChecked(i in v) for i, c in enumerate(px.channels)])

    restore("s21.center_f_ghz", lambda v: s21.center_f.setValue(float(v)))
    restore("s21.bandwidth_mhz", lambda v: s21.bandwidth_mhz.setValue(float(v)))
    restore("s21.points", lambda v: s21.points.setValue(int(v)))
    restore("s21.power_dbm", lambda v: s21.power.setValue(float(v)))
    restore("s21.settle_s", lambda v: s21.settle.setValue(float(v)))
    restore("s21.samples", lambda v: s21.samples.setValue(int(v)))
    restore("s21.i_channel",
            lambda v: s21.i.setCurrentIndex(max(0, s21.i.findData(int(v)))))
    restore("s21.q_channel",
            lambda v: s21.q.setCurrentIndex(max(0, s21.q.findData(int(v)))))
    restore("s21.fit_enabled", lambda v: s21.fit_enabled.setChecked(bool(v)))
    restore("s21.resonator_name", lambda v: s21.resonator_name.setText(str(v)))
    restore("s21.temperature_mk", lambda v: s21.temperature.setValue(float(v)))
    restore("s21.fit_power_dbm", lambda v: s21.fit_power.setValue(float(v)))
    restore("s21.folder", lambda v: s21.folder.setText(str(v)))
    restore("s21.calibration_file", lambda v: s21.calibration_file.setText(str(v)))

    restore("noise.frequency_mode",
            lambda v: noise.frequency_mode.setCurrentIndex(max(0, noise.frequency_mode.findData(str(v)))))
    restore("noise.frequency_ghz", lambda v: noise.frequency.setValue(float(v)))
    restore("noise.power_dbm", lambda v: noise.power.setValue(float(v)))
    restore("noise.settle_s", lambda v: noise.settle.setValue(float(v)))
    restore("noise.mode",
            lambda v: noise.mode.setCurrentIndex(max(0, noise.mode.findText(str(v)))))
    restore("noise.duration_s", lambda v: noise.duration.setValue(float(v)))
    restore("noise.block", lambda v: noise.block.setValue(int(v)))
    restore("noise.psd_window", lambda v: noise.psd_window.setCurrentText(str(v)))
    restore("noise.segment_seconds", lambda v: noise.segment_seconds.setValue(float(v)))
    restore("noise.calibration_file", lambda v: noise.calibration_file.setText(str(v)))
    restore("noise.s21_file", lambda v: noise.s21_file.setText(str(v)))

    restore("combined.center_ghz", lambda v: combined.center.setValue(float(v)))
    restore("combined.bandwidth_mhz", lambda v: combined.bandwidth.setValue(float(v)))
    restore("combined.points", lambda v: combined.points.setValue(int(v)))
    restore("combined.samples", lambda v: combined.samples.setValue(int(v)))
    restore("combined.power_dbm", lambda v: combined.power.setValue(float(v)))
    restore("combined.settle_s", lambda v: combined.settle.setValue(float(v)))
    restore("combined.noise_location",
            lambda v: combined.noise_location.setCurrentIndex(max(0, combined.noise_location.findData(str(v)))))
    restore("combined.noise_duration_s", lambda v: combined.noise_duration.setValue(float(v)))
    restore("combined.noise_block", lambda v: combined.noise_block.setValue(int(v)))
    restore("combined.window_box", lambda v: combined.window_box.setCurrentText(str(v)))
    restore("combined.segment", lambda v: combined.segment.setValue(float(v)))
    restore("combined.calibration_file", lambda v: combined.calibration_file.setText(str(v)))
    restore("combined.folder", lambda v: combined.folder.setText(str(v)))

    restore("iq.mode", lambda v: iq.mode.setCurrentIndex(int(v)))
    restore("iq.frequency_ghz", lambda v: iq.frequency.setValue(float(v)))
    restore("iq.start_frequency_ghz", lambda v: iq.start_frequency.setValue(float(v)))
    restore("iq.stop_frequency_ghz", lambda v: iq.stop_frequency.setValue(float(v)))
    restore("iq.frequency_points", lambda v: iq.frequency_points.setValue(int(v)))
    restore("iq.e8257d_power_dbm", lambda v: iq.e8257d_power.setValue(float(v)))
    restore("iq.p5002a_power_dbm", lambda v: iq.p5002a_power.setValue(float(v)))
    restore("iq.sample_count", lambda v: iq.sample_count.setValue(int(v)))
    restore("iq.settling_time_s", lambda v: iq.settling_time.setValue(float(v)))
    restore("iq.i_channel",
            lambda v: iq.i_channel.setCurrentIndex(max(0, iq.i_channel.findData(int(v)))))
    restore("iq.q_channel",
            lambda v: iq.q_channel.setCurrentIndex(max(0, iq.q_channel.findData(int(v)))))
    restore("iq.folder", lambda v: iq.folder.setText(str(v)))

    try:
        noise.update_selected_frequency()
    except Exception:
        pass


class PersistentP5002AWindow(P5002AWindow):
    """Keep the shared VNA session alive when the control window is closed."""

    def __init__(self, parent=None):
        super().__init__()

    def closeEvent(self, event):
        # The original standalone GUI disconnects the VNA here. In the
        # integrated application, closing an instrument panel means hide it.
        self.hide()
        event.ignore()


class InstrumentManager:
    """Own the only E8257D session and the shared PXIe configuration."""

    def __init__(self):
        self.source = None
        self.source_identity = ""
        self.source_status = {}
        self.source_lock = threading.RLock()
        self.p5002_lock = threading.RLock()
        self.p5002_window = None
        self.daq_connected = False
        self.daq_identity = ""
        self.daq_lock = threading.RLock()
        self.daq_config = {
            "device_name": "PXI2Slot2", "channels": [0, 1],
            "sample_rate": 1_000_000.0, "voltage_range": 10.0,
            "coupling": "DC", "trigger_mode": "IMMEDIATE",
            "trigger_source": None, "trigger_edge": "RISING",
        }

    @property
    def source_connected(self):
        return self.source is not None and self.source.is_connected

    @property
    def p5002_connected(self):
        return (
            self.p5002_window is not None
            and self.p5002_window.vna is not None
            and self.p5002_window.vna.is_connected
        )

    @property
    def p5002(self):
        if not self.p5002_connected:
            raise RuntimeError("P5002A尚未在仪器控制窗口中连接。")
        return self.p5002_window.vna

    def connect_source(self, address):
        with self.source_lock:
            if self.source_connected:
                self.source.disconnect(turn_rf_off=False)
            self.source = E8257D(address, timeout_ms=10000)
            self.source_identity = self.source.connect()
            self.source_status = self.source.read_status()
            return self.source_identity

    def disconnect_source(self, rf_off=True):
        with self.source_lock:
            if self.source is not None:
                self.source.disconnect(turn_rf_off=rf_off)
            self.source = None
            self.source_identity = ""
            self.source_status = {}

    def make_daq(self):
        if not self.daq_connected:
            raise RuntimeError("PXIe-4480尚未连接/验证。")
        config = dict(self.daq_config)
        return PXIe4480(
            device_name=config["device_name"], channels=config["channels"],
            sample_rate=config["sample_rate"], voltage_range=config["voltage_range"],
            coupling=config["coupling"], terminal_config="DIFFERENTIAL",
        )

    def disconnect_all(self):
        self.disconnect_source(rf_off=True)
        if self.p5002_connected:
            try:
                self.p5002.set_output(False)
                self.p5002.disconnect()
            except Exception:
                pass
        self.daq_connected = False
        self.daq_identity = ""


class E8257DDialog(QDialog):
    state_changed = pyqtSignal()

    def __init__(self, manager, parent=None):
        super().__init__(parent); self.manager = manager
        self.setWindowTitle("E8257D共享控制"); self.resize(650, 330)
        layout = QVBoxLayout(self); connection = QGroupBox("连接"); grid = QGridLayout(connection)
        self.address = QComboBox(); self.address.setEditable(True); self.address.addItem("TCPIP0::192.168.1.100::inst0::INSTR")
        scan = QPushButton("扫描VISA"); connect = QPushButton("连接"); disconnect = QPushButton("断开")
        scan.clicked.connect(self.scan); connect.clicked.connect(self.connect_device); disconnect.clicked.connect(self.disconnect_device)
        self.status = QLabel("● 未连接");self.current_state=QLabel("当前状态：尚未读取");self.current_state.setTextInteractionFlags(self.current_state.textInteractionFlags());grid.addWidget(QLabel("VISA地址"),0,0);grid.addWidget(self.address,0,1,1,3);grid.addWidget(scan,1,1);grid.addWidget(connect,1,2);grid.addWidget(disconnect,1,3);grid.addWidget(self.status,2,0,1,4);grid.addWidget(self.current_state,3,0,1,4);layout.addWidget(connection)
        control=QGroupBox("手动输出控制");g=QGridLayout(control)
        self.frequency=QDoubleSpinBox();self.frequency.setRange(.000001,1000);self.frequency.setDecimals(9);self.frequency.setValue(5);self.frequency.setSuffix(" GHz")
        self.power=QDoubleSpinBox();self.power.setRange(-150,30);self.power.setDecimals(3);self.power.setValue(-30);self.power.setSuffix(" dBm")
        set_frequency=QPushButton("设置频率");set_power=QPushButton("设置功率");rf_on=QPushButton("RF ON");rf_off=QPushButton("RF OFF");read=QPushButton("读取状态")
        set_frequency.clicked.connect(lambda:self.command("frequency"));set_power.clicked.connect(lambda:self.command("power"));rf_on.clicked.connect(lambda:self.command("on"));rf_off.clicked.connect(lambda:self.command("off"));read.clicked.connect(lambda:self.command("read"))
        g.addWidget(QLabel("频率"),0,0);g.addWidget(self.frequency,0,1);g.addWidget(set_frequency,0,2);g.addWidget(QLabel("功率"),1,0);g.addWidget(self.power,1,1);g.addWidget(set_power,1,2);g.addWidget(rf_on,2,0);g.addWidget(rf_off,2,1);g.addWidget(read,2,2);layout.addWidget(control);self.refresh()

    def scan(self):
        try:self.address.clear();self.address.addItems(list_visa_resources())
        except Exception as e:QMessageBox.critical(self,"扫描失败",str(e))
    def connect_device(self):
        try:
            self.manager.connect_source(self.address.currentText().strip())
            self.read_live_state()
            self.state_changed.emit()
        except Exception as e:QMessageBox.critical(self,"连接失败",str(e))
    def disconnect_device(self):
        try:self.manager.disconnect_source(True);self.refresh();self.state_changed.emit()
        except Exception as e:QMessageBox.critical(self,"断开失败",str(e))
    def command(self, command):
        try:
            if not self.manager.source_connected:raise RuntimeError("请先连接E8257D。")
            with self.manager.source_lock:
                source=self.manager.source
                if command=="frequency":source.set_frequency_ghz(self.frequency.value())
                elif command=="power":source.set_power_dbm(self.power.value())
                elif command=="on":source.rf_on()
                elif command=="off":source.rf_off()
            self.read_live_state()
        except Exception as e:QMessageBox.critical(self,"控制错误",str(e))
    def read_live_state(self):
        if not self.manager.source_connected:raise RuntimeError("请先连接E8257D。")
        with self.manager.source_lock:status=self.manager.source.read_status()
        self.manager.source_status=dict(status)
        self.frequency.setValue(status["frequency_ghz"]);self.power.setValue(status["power_dbm"])
        rf_text="ON" if status["rf_on"] else "OFF"
        self.status.setText("● 已连接：{}".format(self.manager.source_identity));self.status.setStyleSheet("color:green")
        self.current_state.setText("当前状态：频率 {:.9f} GHz；功率 {:.3f} dBm；RF {}".format(status["frequency_ghz"],status["power_dbm"],rf_text));self.current_state.setStyleSheet("color:green;font-weight:bold")
        return status
    def refresh(self):
        connected=self.manager.source_connected
        if connected:
            try:self.read_live_state()
            except Exception as e:self.status.setText("● 已连接，但状态读取失败：{}".format(e));self.status.setStyleSheet("color:#cc7a00");self.current_state.setText("当前状态：读取失败")
        else:self.status.setText("● 未连接");self.status.setStyleSheet("color:red");self.current_state.setText("当前状态：尚未读取");self.current_state.setStyleSheet("")

    def closeEvent(self, event):
        self.hide()
        event.ignore()


class PXIePreviewWorker(QThread):
    """Continuously read the PXIe-4480 for the control-window preview."""
    data_ready = pyqtSignal(object, object, object)
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, manager, samples_per_read=2000, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.samples_per_read = int(samples_per_read)
        self.daq = None
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True
        if self.daq is not None:
            self.daq.stop_continuous()

    def run(self):
        try:
            self.daq = self.manager.make_daq()
            config = self.manager.daq_config
            effective_samples_per_read=max(self.samples_per_read,int(round(config["sample_rate"]*.2)))
            with self.manager.daq_lock:
                for block in self.daq.acquire_continuous(
                    samples_per_read=effective_samples_per_read,
                    buffer_seconds=10.0,
                    trigger_mode=config["trigger_mode"],
                    trigger_source=config["trigger_source"],
                    trigger_edge=config["trigger_edge"],
                    read_timeout=120.0 if config["trigger_mode"] == "DIGITAL" else 10.0,
                ):
                    self.data_ready.emit(block.time, block.data, block.channels)
                    if self._stop_requested:
                        self.daq.stop_continuous()
                        break
        except Exception as error:
            self.failed.emit("{}: {}".format(type(error).__name__, error))
        finally:
            self.stopped.emit()


class PXIeDialog(QDialog):
    state_changed = pyqtSignal()

    def __init__(self, manager, parent=None):
        super().__init__(parent);self.manager=manager;self.preview_worker=None;self.preview_time=None;self.preview_data=None;self.preview_channels=None;self.setWindowTitle("PXIe-4480共享控制与配置");self.resize(900,700);layout=QVBoxLayout(self);box=QGroupBox("连接与公共采集配置");grid=QGridLayout(box)
        self.device=QLineEdit("PXI2Slot2");self.rate=QDoubleSpinBox();self.rate.setRange(100,20_000_000);self.rate.setDecimals(3);self.rate.setValue(1_000_000);self.rate.setSuffix(" S/s")
        self.voltage=QComboBox();[self.voltage.addItem("±{} V".format(v),v) for v in (.5,1.,5.,10.)];self.voltage.setCurrentIndex(3);self.coupling=QComboBox();self.coupling.addItems(("DC","AC"));self.trigger=QComboBox();self.trigger.addItems(("立即启动","PFI0外部触发"));self.source=QLineEdit("/PXI2Slot2/PFI0");self.edge=QComboBox();self.edge.addItems(("RISING","FALLING"));self.status=QLabel("● 未连接")
        grid.addWidget(QLabel("设备"),0,0);grid.addWidget(self.device,0,1);grid.addWidget(QLabel("采样率"),0,2);grid.addWidget(self.rate,0,3);grid.addWidget(QLabel("量程"),1,0);grid.addWidget(self.voltage,1,1);grid.addWidget(QLabel("耦合"),1,2);grid.addWidget(self.coupling,1,3);grid.addWidget(QLabel("触发"),2,0);grid.addWidget(self.trigger,2,1);grid.addWidget(self.source,2,2);grid.addWidget(self.edge,2,3)
        row=QHBoxLayout();row.addWidget(QLabel("采集通道"));self.channels=[]
        for i in range(6):c=QCheckBox("ai{}".format(i));c.setChecked(i in (0,1));self.channels.append(c);row.addWidget(c)
        grid.addLayout(row,3,0,1,4);connect=QPushButton("连接并应用配置");disconnect=QPushButton("断开");connect.clicked.connect(self.apply);disconnect.clicked.connect(self.disconnect);grid.addWidget(connect,4,1);grid.addWidget(disconnect,4,2);grid.addWidget(self.status,5,0,1,4);layout.addWidget(box)
        note=QLabel("说明：PXIe-4480由NI-DAQmx按任务访问。“连接”会验证设备，测量时使用这里的公共配置。修改配置后请重新点击“连接并应用配置”。");note.setWordWrap(True);layout.addWidget(note)
        preview_controls=QHBoxLayout();self.preview_start=QPushButton("开始电压预览");self.preview_stop=QPushButton("停止预览");self.preview_stop.setEnabled(False);self.preview_mode=QComboBox();self.preview_mode.addItems(("按时间显示","按点数显示"));self.preview_seconds=QDoubleSpinBox();self.preview_seconds.setRange(.001,3600);self.preview_seconds.setDecimals(3);self.preview_seconds.setValue(1);self.preview_seconds.setSuffix(" s");self.preview_points=QSpinBox();self.preview_points.setRange(100,100_000_000);self.preview_points.setValue(100_000);self.preview_points.setSuffix(" 点");self.preview_points.setEnabled(False);self.preview_block=QSpinBox();self.preview_block.setRange(100,1_000_000);self.preview_block.setValue(2000);self.preview_block.setSuffix(" 点/块");self.preview_mode.currentIndexChanged.connect(self.update_preview_mode);self.preview_start.clicked.connect(self.start_preview);self.preview_stop.clicked.connect(self.stop_preview);preview_controls.addWidget(self.preview_start);preview_controls.addWidget(self.preview_stop);preview_controls.addWidget(QLabel("显示窗口"));preview_controls.addWidget(self.preview_mode);preview_controls.addWidget(self.preview_seconds);preview_controls.addWidget(self.preview_points);preview_controls.addWidget(QLabel("读取块大小"));preview_controls.addWidget(self.preview_block);preview_controls.addStretch();layout.addLayout(preview_controls)
        self.preview_canvas=FigureCanvas(Figure(tight_layout=True));self.preview_axes=self.preview_canvas.figure.add_subplot(111);self.preview_axes.set_xlabel("Time (s)");self.preview_axes.set_ylabel("Voltage (V)");self.preview_axes.grid(True,alpha=.3);layout.addWidget(NavigationToolbar(self.preview_canvas,self));layout.addWidget(self.preview_canvas,1);self.refresh()

    def apply(self):
        try:
            if self.preview_worker and self.preview_worker.isRunning():raise RuntimeError("请先停止电压预览，再修改或应用配置。")
            channels=[i for i,c in enumerate(self.channels) if c.isChecked()]
            if not channels:raise ValueError("至少选择一个通道。")
            config={"device_name":self.device.text().strip(),"channels":channels,"sample_rate":self.rate.value(),"voltage_range":float(self.voltage.currentData()),"coupling":self.coupling.currentText(),"trigger_mode":"DIGITAL" if self.trigger.currentIndex()==1 else "IMMEDIATE","trigger_source":self.source.text().strip() if self.trigger.currentIndex()==1 else None,"trigger_edge":self.edge.currentText()}
            test=PXIe4480(device_name=config["device_name"],channels=config["channels"],sample_rate=config["sample_rate"],voltage_range=config["voltage_range"],coupling=config["coupling"],terminal_config="DIFFERENTIAL");info=test.read_device_info();self.manager.daq_config=config;self.manager.daq_identity="{}；序列号 {}；AI通道 {}；当前配置 {} S/s，±{} V，{}".format(info["product_type"],info["serial_number"],len(info["ai_channels"]),config["sample_rate"],config["voltage_range"],config["coupling"]);self.manager.daq_connected=True;self.refresh();self.state_changed.emit()
        except Exception as e:QMessageBox.critical(self,"PXIe连接失败",str(e))
    def disconnect(self):
        if self.preview_worker and self.preview_worker.isRunning():
            QMessageBox.warning(self,"正在预览","请先停止电压预览。")
            return
        self.manager.daq_connected=False;self.manager.daq_identity="";self.refresh();self.state_changed.emit()
    def refresh(self):
        connected=self.manager.daq_connected;self.status.setText("● 已连接：{}".format(self.manager.daq_identity) if connected else "● 未连接");self.status.setStyleSheet("color:{}".format("green" if connected else "red"));self.preview_start.setEnabled(connected and not (self.preview_worker and self.preview_worker.isRunning()))
    def update_preview_mode(self):
        by_time=self.preview_mode.currentIndex()==0;self.preview_seconds.setEnabled(by_time);self.preview_points.setEnabled(not by_time)
    def start_preview(self):
        if not self.manager.daq_connected:
            QMessageBox.warning(self,"尚未连接","请先点击“连接并应用配置”。");return
        if self.preview_worker and self.preview_worker.isRunning():return
        self.preview_time=None;self.preview_data=None;self.preview_channels=None;self.preview_worker=PXIePreviewWorker(self.manager,self.preview_block.value(),self);self.preview_worker.data_ready.connect(self.update_preview);self.preview_worker.failed.connect(self.preview_failed);self.preview_worker.stopped.connect(self.preview_finished);self.preview_start.setEnabled(False);self.preview_stop.setEnabled(True);self.preview_worker.start()
    def stop_preview(self):
        if self.preview_worker and self.preview_worker.isRunning():self.preview_worker.request_stop();self.preview_stop.setEnabled(False)
    def update_preview(self,time_data,data,channels):
        if self.preview_time is None:self.preview_time=np.asarray(time_data);self.preview_data=np.asarray(data);self.preview_channels=tuple(channels)
        else:self.preview_time=np.concatenate((self.preview_time,time_data));self.preview_data=np.concatenate((self.preview_data,data),axis=1)
        if self.preview_mode.currentIndex()==0:
            maximum=max(1,int(round(self.preview_seconds.value()*self.manager.daq_config["sample_rate"])))
        else:
            maximum=self.preview_points.value()
        if self.preview_time.size>maximum:self.preview_time=self.preview_time[-maximum:];self.preview_data=self.preview_data[:,-maximum:]
        self.preview_axes.clear();step=max(1,self.preview_time.size//20_000)
        for index,channel in enumerate(self.preview_channels):self.preview_axes.plot(self.preview_time[::step],self.preview_data[index,::step],label=channel,linewidth=.8)
        self.preview_axes.set_xlabel("Time (s)");self.preview_axes.set_ylabel("Voltage (V)");self.preview_axes.grid(True,alpha=.3);self.preview_axes.legend(loc="upper right");self.preview_canvas.draw_idle()
    def preview_failed(self,message):
        QMessageBox.critical(self,"电压预览错误",message)
    def preview_finished(self):
        self.preview_start.setEnabled(self.manager.daq_connected);self.preview_stop.setEnabled(False);self.preview_worker=None
    def closeEvent(self,event):
        if self.preview_worker and self.preview_worker.isRunning():
            # Hiding the shared panel must not destroy its active preview task.
            self.hide();event.ignore();return
        self.hide();event.ignore()


class MeasurementWorker(QThread):
    s21_point=pyqtSignal(object,object,object,int,int);s21_fit_ready=pyqtSignal(object);s21_fit_failed=pyqtSignal(str);noise_block=pyqtSignal(object,object,object,float);file_created=pyqtSignal(str);done=pyqtSignal(str);error=pyqtSignal(str)
    def __init__(self,manager,kind,parameters,path,parent=None):super().__init__(parent);self.manager=manager;self.kind=kind;self.p=parameters;self.path=path;self.stop_requested=False;self.daq=None
    def stop(self):
        self.stop_requested=True
        if self.daq:self.daq.stop_continuous()
    def run(self):
        try:
            if not self.manager.source_connected:raise RuntimeError("E8257D未连接。")
            if not self.manager.daq_connected:raise RuntimeError("PXIe-4480未连接。")
            if self.kind=="s21":self.run_s21()
            else:self.run_noise()
            self.done.emit(str(self.path))
        except Exception as e:self.error.emit("{}: {}".format(type(e).__name__,e))
    def _make_daq(self):
        self.daq = self.manager.make_daq()
        return self.daq

    def _record_source_status(self, frequency_hz, power_dbm):
        self.manager.source_status = {
            "frequency_ghz": float(frequency_hz)/1e9,
            "power_dbm": float(power_dbm), "rf_on": True,
        }

    def _make_context(self):
        return core.MeasurementContext(
            source=self.manager.source, source_lock=self.manager.source_lock,
            daq_config=self.manager.daq_config, daq_lock=self.manager.daq_lock,
            make_daq=self._make_daq,
            on_file_created=self.file_created.emit,
            on_s21_point=self.s21_point.emit,
            on_s21_fit_ready=self.s21_fit_ready.emit,
            on_s21_fit_failed=self.s21_fit_failed.emit,
            on_noise_block=self.noise_block.emit,
            on_source_configured=self._record_source_status,
            should_stop=lambda: self.stop_requested,
        )

    def run_s21(self):
        # 薄壳：算法在 kid_measurement_core.run_s21；这里只翻译参数 + 保留 GUI 的文件重命名习惯。
        result = core.run_s21(self._make_context(), core.S21Params(
            start_hz=self.p["start"], stop_hz=self.p["stop"],
            center_hz=self.p["center"], bandwidth_hz=self.p["bandwidth"],
            points=self.p["points"], samples=self.p["samples"],
            power_dbm=self.p["power"], settle_s=self.p["settle"],
            i_channel=int(self.p["i"]), q_channel=int(self.p["q"]),
            calibration_file=self.p["calibration_file"],
            fit_enabled=self.p["fit_enabled"],
            resonator_name=self.p["resonator_name"],
            temperature_k=self.p["temperature_k"],
            readout_power_dbm=self.p["readout_power_dbm"],
        ), self.path)
        if result.resonance_frequency_hz is not None:
            target = s21_resonance_path(
                self.path, result.resonance_frequency_hz,
                self.p["resonator_name"], self.p["temperature_k"],
                self.p["readout_power_dbm"],
            )
            if target != self.path:
                self.path.replace(target)
                self.path = target

    def run_noise(self):
        core.run_noise(self._make_context(), core.NoiseParams(
            s21_file=self.p["s21_file"], frequency_mode=self.p["frequency_mode"],
            manual_frequency_hz=self.p["manual_frequency"],
            power_dbm=self.p["power"], settle_s=self.p["settle"],
            continuous=self.p["continuous"], duration_s=self.p["duration"],
            block=self.p["block"], i_channel=int(self.p["i"]),
            q_channel=int(self.p["q"]), calibration_file=self.p["calibration_file"],
            window=self.p["window"], segment_seconds=self.p["segment_seconds"],
        ), self.path)

    def finish_rf(self):
        pass


class DeviceStatusBar(QWidget):
    def __init__(self,manager,parent=None):super().__init__(parent);self.manager=manager;row=QVBoxLayout(self);row.setContentsMargins(0,0,0,0);self.source=QLabel();self.vna=QLabel();self.daq=QLabel();row.addWidget(self.source);row.addWidget(self.vna);row.addWidget(self.daq);self.refresh()
    def refresh(self):
        a=self.manager.source_connected;b=self.manager.daq_connected;c=self.manager.p5002_connected;s=self.manager.source_status
        self.source.setText("● E8257D：频率 {:.9f} GHz；功率 {:.3f} dBm；RF {}".format(s.get("frequency_ghz",0.0),s.get("power_dbm",0.0),"ON" if s.get("rf_on",False) else "OFF") if a else "● E8257D：未连接")
        if c:
            w=self.manager.p5002_window;mode=w.mode.currentText();freq=w.cw_frequency.value() if w.mode.currentIndex()==1 else w.start_frequency.value();self.vna.setText("● P5002A：模式 {}；频率/起始频率 {:.9f} GHz；功率 {:.3f} dBm；IFBW {:.3f} Hz；输出 {}".format(mode,freq,w.power.value(),w.ifbw.value(),w.output_port.currentText()))
        else:self.vna.setText("● P5002A：未连接")
        q=self.manager.daq_config;self.daq.setText("● PXIe-4480：{}；采样率 {:.6g} S/s；量程 ±{:.6g} V；通道 {}；触发 {}".format("已连接" if b else "未连接",q["sample_rate"],q["voltage_range"],",".join("ai{}".format(x) for x in q["channels"]),q["trigger_mode"]))
        self.source.setStyleSheet("color:{}".format("green" if a else "red"));self.vna.setStyleSheet("color:{}".format("green" if c else "red"));self.daq.setStyleSheet("color:{}".format("green" if b else "red"))


class BaseMeasurementTab(QWidget):
    def __init__(self,window):super().__init__();self.window=window;self.manager=window.manager;self.worker=None;self.status=DeviceStatusBar(self.manager)
    def choose(self,edit):
        folder=QFileDialog.getExistingDirectory(self,"选择存储目录",edit.text())
        if folder:edit.setText(folder)
    def choose_calibration(self,edit):
        path,_=QFileDialog.getOpenFileName(self,"选择IQ扫描校准汇总文件",edit.text() or str(IQ_DATA_DIRECTORY),"IQ summary (*.txt);;All files (*)")
        if path:edit.setText(path)
    def choose_s21_fit(self,edit):
        path,_=QFileDialog.getOpenFileName(self,"选择包含SCRAPS拟合的S21文件",edit.text() or str(S21_DATA_DIRECTORY),"S21 HDF5 (*.h5 *.hdf5);;All files (*)")
        if path:edit.setText(path)
    def stop(self):
        if self.worker:self.worker.stop()
    def failure(self,msg):self.set_running(False);self.worker=None;self.window.sync_instrument_controls();QMessageBox.critical(self,"测量错误",msg)
    def finished(self,path):self.set_running(False);self.worker=None;self.window.sync_instrument_controls();self.window.statusBar().showMessage("测量完成：{}".format(path))
    def set_running(self,running):self.start.setEnabled(not running);self.stop_button.setEnabled(running)


class S21Tab(BaseMeasurementTab):
    def __init__(self,window):
        super().__init__(window);layout=QVBoxLayout(self);layout.addWidget(self.status);box=QGroupBox("S21测量参数");g=QGridLayout(box)
        self.center_f=QDoubleSpinBox();self.center_f.setRange(.000001,1000);self.center_f.setDecimals(9);self.center_f.setValue(5);self.center_f.setSuffix(" GHz");self.bandwidth_mhz=QDoubleSpinBox();self.bandwidth_mhz.setRange(.000001,1_000_000);self.bandwidth_mhz.setDecimals(6);self.bandwidth_mhz.setValue(2000);self.bandwidth_mhz.setSuffix(" MHz")
        self.points=QSpinBox();self.points.setRange(2,1_000_000);self.points.setValue(101);self.power=QDoubleSpinBox();self.power.setRange(-150,30);self.power.setValue(-30);self.power.setSuffix(" dBm");self.settle=QDoubleSpinBox();self.settle.setRange(0,60);self.settle.setDecimals(3);self.settle.setValue(.010);self.settle.setSuffix(" s");self.samples=QSpinBox();self.samples.setRange(1,10_000_000);self.samples.setValue(1000);self.i=QComboBox();self.q=QComboBox();[self.i.addItem("ai{}".format(x),x) for x in range(6)];[self.q.addItem("ai{}".format(x),x) for x in range(6)];self.q.setCurrentIndex(1);self.fit_enabled=QCheckBox("扫描完成后进行SCRAPS拟合");self.resonator_name=QLineEdit("res0");self.temperature=QDoubleSpinBox();self.temperature.setRange(.001,1_000_000);self.temperature.setDecimals(3);self.temperature.setValue(100);self.temperature.setSuffix(" mK");self.fit_power=QDoubleSpinBox();self.fit_power.setRange(-150,30);self.fit_power.setValue(-60);self.fit_power.setSuffix(" dBm");self.folder=QLineEdit(str(S21_DATA_DIRECTORY));choose=QPushButton("选择目录");choose.clicked.connect(lambda:self.choose(self.folder));self.calibration_file=QLineEdit(latest_iq_summary());choose_cal=QPushButton("选择校准文件");choose_cal.clicked.connect(lambda:self.choose_calibration(self.calibration_file))
        fields=[("中心频率",self.center_f),("扫描带宽",self.bandwidth_mhz),("频点数",self.points),("功率",self.power),("稳定时间",self.settle),("每频点采样数",self.samples),("I通道",self.i),("Q通道",self.q)]
        for n,(label,w) in enumerate(fields):r=n//4;c=(n%4)*2;g.addWidget(QLabel(label),r,c);g.addWidget(w,r,c+1)
        g.addWidget(self.fit_enabled,2,0,1,2);g.addWidget(QLabel("谐振器名称"),2,2);g.addWidget(self.resonator_name,2,3);g.addWidget(QLabel("温度"),2,4);g.addWidget(self.temperature,2,5);g.addWidget(QLabel("读出功率"),2,6);g.addWidget(self.fit_power,2,7);g.addWidget(QLabel("存储目录"),3,0);g.addWidget(self.folder,3,1,1,5);g.addWidget(choose,3,6);g.addWidget(QLabel("IQ校准文件"),4,0);g.addWidget(self.calibration_file,4,1,1,5);g.addWidget(choose_cal,4,6);layout.addWidget(box);row=QHBoxLayout();self.start=QPushButton("开始S21扫描");self.stop_button=QPushButton("停止");self.stop_button.setEnabled(False);self.progress=QProgressBar();self.file=QLabel("尚未生成文件");self.start.clicked.connect(self.begin);self.stop_button.clicked.connect(self.stop);row.addWidget(self.start);row.addWidget(self.stop_button);row.addWidget(self.progress);row.addWidget(self.file,1);layout.addLayout(row);self.canvas=FigureCanvas(Figure(tight_layout=True));self.fit_axes=self.canvas.figure.subplots(2,2);self.axes=(self.fit_axes[0,0],self.fit_axes[1,0]);self.fit_axes[0,1].set_visible(False);self.fit_axes[1,1].set_visible(False);layout.addWidget(NavigationToolbar(self.canvas,self));layout.addWidget(self.canvas,1);self.fit_text=QPlainTextEdit();self.fit_text.setReadOnly(True);self.fit_text.setMaximumHeight(145);self.fit_text.setPlaceholderText("SCRAPS拟合结果将在这里显示。");layout.addWidget(self.fit_text)
    def begin(self):
        try:
            if not self.manager.source_connected or not self.manager.daq_connected:raise RuntimeError("请先在“仪器控制”菜单中连接两台设备。")
            if self.window.daq_dialog.preview_worker and self.window.daq_dialog.preview_worker.isRunning():raise RuntimeError("PXIe-4480电压预览正在运行，请先在仪器控制窗口中停止预览。")
            c=self.manager.daq_config;i=int(self.i.currentData());q=int(self.q.currentData())
            if i==q or i not in c["channels"] or q not in c["channels"]:raise ValueError("I/Q必须不同，并且已在PXIe公共配置中选中。")
            half_bandwidth_hz=self.bandwidth_mhz.value()*1e6/2;center_hz=self.center_f.value()*1e9;start_hz=center_hz-half_bandwidth_hz;stop_hz=center_hz+half_bandwidth_hz
            if start_hz<=0:raise ValueError("中心频率减去一半带宽后必须大于0 Hz。")
            calibration_file=self.calibration_file.text().strip();IQCalibrationTable.load(calibration_file);p={"start":start_hz,"stop":stop_hz,"center":center_hz,"bandwidth":self.bandwidth_mhz.value()*1e6,"points":self.points.value(),"power":self.power.value(),"settle":self.settle.value(),"samples":self.samples.value(),"i":i,"q":q,"calibration_file":calibration_file,"fit_enabled":self.fit_enabled.isChecked(),"resonator_name":self.resonator_name.text().strip() or "res0","temperature_k":self.temperature.value()/1000,"readout_power_dbm":self.fit_power.value()};path=output_path(self.folder.text(),"S21","{}GHz-{}MHz".format(self.center_f.value(),self.bandwidth_mhz.value()));self.fit_text.clear();self.worker=MeasurementWorker(self.manager,"s21",p,path,self);self.worker.s21_point.connect(self.plot);self.worker.s21_fit_ready.connect(self.show_fit);self.worker.s21_fit_failed.connect(self.fit_failed);self.worker.file_created.connect(self.file.setText);self.worker.done.connect(self.finished);self.worker.error.connect(self.failure);self.progress.setRange(0,p["points"]);self.set_running(True);self.worker.start();save_gui_settings(self.window)
        except Exception as e:QMessageBox.critical(self,"无法开始",str(e))
    def plot(self,f,m,p,n,total):
        x=np.asarray(f)/1e9;self.axes[0].clear();self.axes[0].plot(x,m);self.axes[0].set_ylabel("Magnitude (dB)");self.axes[0].grid(True,alpha=.3);self.axes[1].clear();self.axes[1].plot(x,p);self.axes[1].set_ylabel("Phase (deg)");self.axes[1].set_xlabel("Frequency (GHz)");self.axes[1].grid(True,alpha=.3);self.canvas.draw_idle();self.progress.setValue(n)
    def show_fit(self,result):
        for axis in self.fit_axes.ravel():axis.set_visible(True);axis.clear()
        f=np.asarray(result.freq)/1e9;eps=np.finfo(float).tiny
        a=self.fit_axes[0,0];a.plot(result.INorm,result.QNorm,".",label="meas");a.plot(result.resultINorm,result.resultQNorm,"--",label="fit");a.set_xlabel("I");a.set_ylabel("Q");a.set_title("IQ plane");a.axis("equal");a.grid(True,alpha=.3);a.legend()
        a=self.fit_axes[0,1];a.plot(f,10*np.log10(np.maximum(result.I**2+result.Q**2,eps)),label="meas");a.plot(f,10*np.log10(np.maximum(result.resultI**2+result.resultQ**2,eps)),"--",label="fit");a.set_title("Raw magnitude");a.set_xlabel("Frequency (GHz)");a.set_ylabel("Magnitude (dB)");a.grid(True,alpha=.3);a.legend()
        a=self.fit_axes[1,0];a.plot(f,10*np.log10(np.maximum(result.INorm**2+result.QNorm**2,eps)),label="meas");a.plot(f,10*np.log10(np.maximum(result.resultINorm**2+result.resultQNorm**2,eps)),"--",label="fit");a.set_title("Normalized magnitude");a.set_xlabel("Frequency (GHz)");a.set_ylabel("Magnitude (dB)");a.grid(True,alpha=.3);a.legend()
        a=self.fit_axes[1,1];a.plot(f,np.arctan2(result.QNorm,result.INorm),label="meas");a.plot(f,np.arctan2(result.resultQNorm,result.resultINorm),"--",label="fit");a.set_title("Normalized phase");a.set_xlabel("Frequency (GHz)");a.set_ylabel("Phase (rad)");a.grid(True,alpha=.3);a.legend();self.fit_text.setPlainText(result.report);self.canvas.draw_idle()
    def finished(self,path):
        super().finished(path);self.file.setText(path)
        try:
            S21NoiseCalibration.load(path);self.window.noise.s21_file.setText(path);self.window.noise.update_selected_frequency();self.window.statusBar().showMessage("S21拟合完成并更新噪声文件：{}".format(path))
        except Exception:
            pass
        if hasattr(self.window,"combined") and self.window.combined.active and self.window.combined.stage=="s21":self.window.combined.s21_finished(path)
    def fit_failed(self,message):
        self.fit_text.setPlainText("S21扫描数据已正常保存，但SCRAPS拟合失败：\n"+message)
        self.window.statusBar().showMessage("S21扫描完成；SCRAPS拟合失败，原始数据已保留")


class NoiseTab(BaseMeasurementTab):
    def __init__(self,window):
        super().__init__(window);self.rt=None;self.rd=None;self.rni=None;self.rnq=None;self.s21_i_norm=None;self.s21_q_norm=None;self.test_point=None;layout=QVBoxLayout(self);layout.addWidget(self.status);box=QGroupBox("噪声测量参数");g=QGridLayout(box);self.frequency=QDoubleSpinBox();self.frequency.setRange(.000001,1000);self.frequency.setDecimals(9);self.frequency.setValue(5);self.frequency.setSuffix(" GHz");self.frequency_mode=QComboBox();self.frequency_mode.addItem("手动频率","MANUAL");self.frequency_mode.addItem("拟合频率 f0+df","F0_PLUS_DF");self.frequency_mode.addItem("最大响应点 dI²+dQ²","MAX_RESPONSE");self.frequency_mode.setCurrentIndex(1);self.frequency_mode.currentIndexChanged.connect(self.update_selected_frequency);self.power=QDoubleSpinBox();self.power.setRange(-150,30);self.power.setValue(-30);self.power.setSuffix(" dBm");self.settle=QDoubleSpinBox();self.settle.setRange(0,60);self.settle.setValue(.1);self.settle.setSuffix(" s");self.mode=QComboBox();self.mode.addItems(("指定时长","无限连续"));self.duration=QDoubleSpinBox();self.duration.setRange(.001,86400);self.duration.setValue(10);self.duration.setSuffix(" s");self.mode.currentIndexChanged.connect(lambda:self.duration.setEnabled(self.mode.currentIndex()==0));self.block=QSpinBox();self.block.setRange(100,10_000_000);self.block.setValue(10_000);self.i=QComboBox();self.q=QComboBox();[self.i.addItem("ai{}".format(x),x) for x in range(6)];[self.q.addItem("ai{}".format(x),x) for x in range(6)];self.q.setCurrentIndex(1);self.psd_window=QComboBox();self.psd_window.addItems(("hann","hamming","blackman","boxcar"));self.psd_window.setCurrentText("hamming");self.segment_seconds=QDoubleSpinBox();self.segment_seconds.setRange(.000001,86400);self.segment_seconds.setDecimals(6);self.segment_seconds.setValue(1.0);self.segment_seconds.setSuffix(" s");self.iq_plot_points=QSpinBox();self.iq_plot_points.setRange(10,10_000_000);self.iq_plot_points.setValue(10_000);self.folder=QLineEdit(str(NOISE_DATA_DIRECTORY));choose=QPushButton("选择目录");choose.clicked.connect(lambda:self.choose(self.folder));self.calibration_file=QLineEdit(latest_iq_summary());choose_cal=QPushButton("选择校准文件");choose_cal.clicked.connect(lambda:self.choose_calibration(self.calibration_file));self.s21_file=QLineEdit(latest_fitted_s21());self.s21_file.editingFinished.connect(self.update_selected_frequency);choose_s21=QPushButton("选择S21文件");choose_s21.clicked.connect(lambda:(self.choose_s21_fit(self.s21_file),self.update_selected_frequency()));fields=[("测量频点",self.frequency_mode),("固定频率",self.frequency),("读出功率",self.power),("稳定时间",self.settle),("模式",self.mode),("采集时长",self.duration),("分块点数",self.block),("I通道",self.i),("Q通道",self.q),("Welch window",self.psd_window),("Segment时长",self.segment_seconds),("IQ Norm显示点数",self.iq_plot_points)]
        for n,(label,w) in enumerate(fields):r=n//3;c=(n%3)*2;g.addWidget(QLabel(label),r,c);g.addWidget(w,r,c+1)
        g.addWidget(QLabel("数据写入位置"),4,0);g.addWidget(QLabel("所选S21文件的 /noise_measurements 组"),4,1,1,5);g.addWidget(QLabel("IQ校准文件"),5,0);g.addWidget(self.calibration_file,5,1,1,4);g.addWidget(choose_cal,5,5);g.addWidget(QLabel("S21拟合文件"),6,0);g.addWidget(self.s21_file,6,1,1,4);g.addWidget(choose_s21,6,5);layout.addWidget(box);row=QHBoxLayout();self.start=QPushButton("开始噪声采集");self.stop_button=QPushButton("停止");self.stop_button.setEnabled(False);self.file=QLabel("尚未生成文件");self.start.clicked.connect(self.begin);self.stop_button.clicked.connect(self.stop);row.addWidget(self.start);row.addWidget(self.stop_button);row.addWidget(self.file,1);layout.addLayout(row);self.canvas=FigureCanvas(Figure(tight_layout=True));self.axes=self.canvas.figure.subplots(2,2);layout.addWidget(NavigationToolbar(self.canvas,self));layout.addWidget(self.canvas,1)
        self.update_selected_frequency()
    def update_selected_frequency(self):
        mode=str(self.frequency_mode.currentData());self.frequency.setEnabled(mode=="MANUAL")
        if mode=="MANUAL" or not self.s21_file.text().strip():return
        try:
            calibration=S21NoiseCalibration.load(self.s21_file.text().strip());frequency,_=calibration.measurement_frequency(mode,self.frequency.value()*1e9);self.frequency.setValue(frequency/1e9)
        except Exception:
            pass
    def begin(self):
        try:
            if not self.manager.source_connected or not self.manager.daq_connected:raise RuntimeError("请先在“仪器控制”菜单中连接两台设备。")
            if self.window.daq_dialog.preview_worker and self.window.daq_dialog.preview_worker.isRunning():raise RuntimeError("PXIe-4480电压预览正在运行，请先在仪器控制窗口中停止预览。")
            c=self.manager.daq_config;i=int(self.i.currentData());q=int(self.q.currentData());
            if i==q or i not in c["channels"] or q not in c["channels"]:raise ValueError("I/Q必须不同，并且已在PXIe公共配置中选中。")
            calibration_file=self.calibration_file.text().strip();IQCalibrationTable.load(calibration_file);s21_file=self.s21_file.text().strip();noise_calibration=S21NoiseCalibration.load(s21_file);frequency_mode=str(self.frequency_mode.currentData());selected_frequency,_=noise_calibration.measurement_frequency(frequency_mode,self.frequency.value()*1e9);self.frequency.setValue(selected_frequency/1e9);effective_block=max(self.block.value(),int(round(c["sample_rate"]*1.0)));self.block.setValue(effective_block);p={"frequency_mode":frequency_mode,"manual_frequency":self.frequency.value()*1e9,"power":self.power.value(),"settle":self.settle.value(),"continuous":self.mode.currentIndex()==1,"duration":self.duration.value(),"block":effective_block,"i":i,"q":q,"calibration_file":calibration_file,"s21_file":s21_file,"window":self.psd_window.currentText(),"segment_seconds":self.segment_seconds.value()};path=Path(s21_file);self.rt=None;self.rd=None;self.rni=None;self.rnq=None;self.s21_i_norm=None;self.s21_q_norm=None;self.test_point=None;self.worker=MeasurementWorker(self.manager,"noise",p,path,self);self.worker.noise_block.connect(self.plot);self.worker.file_created.connect(self.file.setText);self.worker.done.connect(self.finished);self.worker.error.connect(self.failure);self.set_running(True);self.worker.start();save_gui_settings(self.window)
        except Exception as e:QMessageBox.critical(self,"无法开始",str(e))
    def plot(self,t,d,ch,rate):
        block=np.vstack((d["amplitude"],d["phase"]));ni=np.asarray(d["i_norm"]);nq=np.asarray(d["q_norm"]);self.s21_i_norm=np.asarray(d["s21_i_norm"]);self.s21_q_norm=np.asarray(d["s21_q_norm"]);self.test_point=d["test_point"]
        if self.rt is None:self.rt=np.asarray(t);self.rd=block;self.rni=ni;self.rnq=nq
        else:self.rt=np.concatenate((self.rt,t));self.rd=np.concatenate((self.rd,block),axis=1);self.rni=np.concatenate((self.rni,ni));self.rnq=np.concatenate((self.rnq,nq))
        if self.rt.size>1_000_000:self.rt=self.rt[-1_000_000:];self.rd=self.rd[:,-1_000_000:];self.rni=self.rni[-1_000_000:];self.rnq=self.rnq[-1_000_000:]
        step=max(1,self.rt.size//20_000);self.axes[0,0].clear();self.axes[0,0].plot(self.rt[::step],self.rd[0,::step],linewidth=.8);self.axes[0,0].set_xlabel("Time (s)");self.axes[0,0].set_ylabel("Amplitude");self.axes[0,0].grid(True,alpha=.3);self.axes[1,0].clear();self.axes[1,0].plot(self.rt[::step],self.rd[1,::step],linewidth=.8);self.axes[1,0].set_xlabel("Time (s)");self.axes[1,0].set_ylabel("Unwrapped phase (rad)");self.axes[1,0].grid(True,alpha=.3)
        self.axes[0,1].clear();freq,sxx,nperseg=welch_psd(self.rd[0],rate,self.segment_seconds.value(),self.psd_window.currentText());_,syy,_=welch_psd(self.rd[1],rate,self.segment_seconds.value(),self.psd_window.currentText(),unwrap=True)
        if freq.size>1:self.axes[0,1].loglog(freq[1:],sxx[1:],label="Amplitude PSD",linewidth=.8);self.axes[0,1].loglog(freq[1:],syy[1:],label="Phase PSD",linewidth=.8)
        self.axes[0,1].set_xlabel("Frequency (Hz)");self.axes[0,1].set_ylabel("PSD (1/Hz or rad²/Hz)");self.axes[0,1].set_title("Welch PSD: window={}, nperseg={}".format(self.psd_window.currentText(),nperseg));self.axes[0,1].grid(True,which="both",alpha=.3);self.axes[0,1].legend()
        self.axes[1,1].clear();self.axes[1,1].plot(self.s21_i_norm,self.s21_q_norm,".-",markersize=3,linewidth=.8,label="S21 INorm/QNorm");test_i,test_q=self.test_point;self.axes[1,1].plot([test_i],[test_q],"o",markersize=8,label="noise test point");count=min(self.iq_plot_points.value(),self.rni.size);plot_step=max(1,count//20_000);self.axes[1,1].plot(self.rni[-count::plot_step],self.rnq[-count::plot_step],".",markersize=2,label="noise IQ Norm");self.axes[1,1].set_xlabel("I Norm");self.axes[1,1].set_ylabel("Q Norm");self.axes[1,1].set_title("S21 curve and normalized noise IQ (last {} points)".format(count));self.axes[1,1].axis("equal");self.axes[1,1].grid(True,alpha=.3);self.axes[1,1].legend();self.canvas.draw_idle()
    def finished(self,path):
        super().finished(path)
        if hasattr(self.window,"combined") and self.window.combined.active and self.window.combined.stage=="noise":self.window.combined.noise_finished(path)


class CombinedMeasurementTab(QWidget):
    """Run fitted S21 followed by noise acquisition with one button."""
    def __init__(self,window):
        super().__init__();self.window=window;self.manager=window.manager;self.active=False;self.stage="idle";self.noise_amp=None;self.noise_phase=None;layout=QVBoxLayout(self);self.status=DeviceStatusBar(self.manager);layout.addWidget(self.status);box=QGroupBox("一键S21与噪声测量参数");g=QGridLayout(box)
        self.center=QDoubleSpinBox();self.center.setRange(.000001,1000);self.center.setDecimals(9);self.center.setValue(5);self.center.setSuffix(" GHz");self.bandwidth=QDoubleSpinBox();self.bandwidth.setRange(.000001,1_000_000);self.bandwidth.setDecimals(6);self.bandwidth.setValue(20);self.bandwidth.setSuffix(" MHz");self.points=QSpinBox();self.points.setRange(2,1_000_000);self.points.setValue(101);self.samples=QSpinBox();self.samples.setRange(1,10_000_000);self.samples.setValue(1000);self.power=QDoubleSpinBox();self.power.setRange(-150,30);self.power.setValue(-30);self.power.setSuffix(" dBm");self.settle=QDoubleSpinBox();self.settle.setRange(0,60);self.settle.setDecimals(3);self.settle.setValue(.010);self.settle.setSuffix(" s")
        self.noise_location=QComboBox();self.noise_location.addItem("拟合频率 f0+df","F0_PLUS_DF");self.noise_location.addItem("最大响应点 dI²+dQ²","MAX_RESPONSE");self.noise_duration=QDoubleSpinBox();self.noise_duration.setRange(.001,86400);self.noise_duration.setValue(10);self.noise_duration.setSuffix(" s");self.noise_block=QSpinBox();self.noise_block.setRange(100,10_000_000);self.noise_block.setValue(10_000);self.window_box=QComboBox();self.window_box.addItems(("hann","hamming","blackman","boxcar"));self.window_box.setCurrentText("hamming");self.segment=QDoubleSpinBox();self.segment.setRange(.000001,86400);self.segment.setDecimals(6);self.segment.setValue(1);self.segment.setSuffix(" s");self.i=QComboBox();self.q=QComboBox();[self.i.addItem("ai{}".format(x),x) for x in range(6)];[self.q.addItem("ai{}".format(x),x) for x in range(6)];self.q.setCurrentIndex(1)
        self.resonator=QLineEdit("res0");self.temperature=QDoubleSpinBox();self.temperature.setRange(.001,1_000_000);self.temperature.setDecimals(3);self.temperature.setValue(100);self.temperature.setSuffix(" mK");self.fit_power=QDoubleSpinBox();self.fit_power.setRange(-150,30);self.fit_power.setValue(-60);self.fit_power.setSuffix(" dBm");self.calibration_file=QLineEdit(latest_iq_summary());choose_cal=QPushButton("选择校准文件");choose_cal.clicked.connect(self.choose_calibration);self.folder=QLineEdit(str(S21_DATA_DIRECTORY));choose_folder=QPushButton("选择目录");choose_folder.clicked.connect(self.choose_folder)
        fields=(("中心频率",self.center),("扫描带宽",self.bandwidth),("S21频点数",self.points),("每频点采样数",self.samples),("输出功率",self.power),("稳定时间",self.settle),("噪声频点",self.noise_location),("噪声时长",self.noise_duration),("噪声分块点数",self.noise_block),("Welch window",self.window_box),("Segment时长",self.segment),("I通道",self.i),("Q通道",self.q),("谐振器名称",self.resonator),("温度",self.temperature),("读出功率",self.fit_power))
        for n,(label,widget) in enumerate(fields):row=n//4;column=(n%4)*2;g.addWidget(QLabel(label),row,column);g.addWidget(widget,row,column+1)
        g.addWidget(QLabel("IQ校准文件"),4,0);g.addWidget(self.calibration_file,4,1,1,5);g.addWidget(choose_cal,4,6);g.addWidget(QLabel("S21存储目录"),5,0);g.addWidget(self.folder,5,1,1,5);g.addWidget(choose_folder,5,6);layout.addWidget(box)
        controls=QHBoxLayout();self.start=QPushButton("一键开始S21+噪声");self.stop=QPushButton("停止");self.stop.setEnabled(False);self.progress=QProgressBar();self.message=QLabel("尚未开始");self.start.clicked.connect(self.begin);self.stop.clicked.connect(self.stop_measurement);controls.addWidget(self.start);controls.addWidget(self.stop);controls.addWidget(self.progress);controls.addWidget(self.message,1);layout.addLayout(controls);self.canvas=FigureCanvas(Figure(tight_layout=True));self.axes=self.canvas.figure.subplots(2,2);layout.addWidget(NavigationToolbar(self.canvas,self));layout.addWidget(self.canvas,1)
    def choose_calibration(self):
        path,_=QFileDialog.getOpenFileName(self,"选择IQ校准文件",self.calibration_file.text() or str(IQ_DATA_DIRECTORY),"IQ summary (*.txt);;All files (*)")
        if path:self.calibration_file.setText(path)
    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,"选择S21存储目录",self.folder.text())
        if path:self.folder.setText(path)
    def begin(self):
        if self.active:return
        if (self.window.s21.worker and self.window.s21.worker.isRunning()) or (self.window.noise.worker and self.window.noise.worker.isRunning()):QMessageBox.warning(self,"无法开始","S21或噪声测量正在运行。");return
        s=self.window.s21;s.center_f.setValue(self.center.value());s.bandwidth_mhz.setValue(self.bandwidth.value());s.points.setValue(self.points.value());s.samples.setValue(self.samples.value());s.power.setValue(self.power.value());s.settle.setValue(self.settle.value());s.i.setCurrentIndex(self.i.currentIndex());s.q.setCurrentIndex(self.q.currentIndex());s.fit_enabled.setChecked(True);s.resonator_name.setText(self.resonator.text());s.temperature.setValue(self.temperature.value());s.fit_power.setValue(self.fit_power.value());s.calibration_file.setText(self.calibration_file.text());s.folder.setText(self.folder.text());self.noise_amp=None;self.noise_phase=None;self.active=True;self.stage="s21";self.start.setEnabled(False);self.stop.setEnabled(True);self.progress.setRange(0,self.points.value());self.message.setText("正在测量S21...");s.begin()
        if s.worker is None:self.fail("S21测量未能启动。")
        else:s.worker.s21_point.connect(self.plot_s21);s.worker.s21_fit_ready.connect(self.plot_s21_fit);s.worker.error.connect(self.fail);save_gui_settings(self.window)
    def plot_s21(self,f,m,p,n,total):
        x=np.asarray(f)/1e9;self.axes[0,0].clear();self.axes[0,0].plot(x,m);self.axes[0,0].set_title("S21 magnitude");self.axes[0,0].set_xlabel("Frequency (GHz)");self.axes[0,0].set_ylabel("dB");self.axes[0,0].grid(True,alpha=.3);self.axes[0,1].clear();self.axes[0,1].plot(x,p);self.axes[0,1].set_title("S21 phase");self.axes[0,1].set_xlabel("Frequency (GHz)");self.axes[0,1].set_ylabel("deg");self.axes[0,1].grid(True,alpha=.3);self.progress.setValue(n);self.canvas.draw_idle()
    def plot_s21_fit(self,result):
        f=np.asarray(result.freq)/1e9;eps=np.finfo(float).tiny;self.axes[0,0].clear();self.axes[0,0].plot(f,10*np.log10(np.maximum(result.I**2+result.Q**2,eps)),label="meas");self.axes[0,0].plot(f,10*np.log10(np.maximum(result.resultI**2+result.resultQ**2,eps)),"--",label="fit");self.axes[0,0].set_title("S21 magnitude");self.axes[0,0].set_xlabel("Frequency (GHz)");self.axes[0,0].set_ylabel("dB");self.axes[0,0].grid(True,alpha=.3);self.axes[0,0].legend();self.axes[0,1].clear();self.axes[0,1].plot(f,np.degrees(np.arctan2(result.QNorm,result.INorm)),label="meas");self.axes[0,1].plot(f,np.degrees(np.arctan2(result.resultQNorm,result.resultINorm)),"--",label="fit");self.axes[0,1].set_title("Normalized S21 phase");self.axes[0,1].set_xlabel("Frequency (GHz)");self.axes[0,1].set_ylabel("deg");self.axes[0,1].grid(True,alpha=.3);self.axes[0,1].legend();self.canvas.draw_idle()
    def s21_finished(self,path):
        if not self.active or self.stage!="s21":return
        try:
            S21NoiseCalibration.load(path)
            with h5py.File(path,"a") as h:h.attrs["combined_s21_noise_workflow"]=True;h.attrs["combined_workflow_started_at"]=datetime.now().isoformat(timespec="seconds")
        except Exception as error:self.fail("S21拟合结果不能用于噪声测量：{}".format(error));return
        n=self.window.noise;n.s21_file.setText(path);n.calibration_file.setText(self.calibration_file.text());n.frequency_mode.setCurrentIndex(max(0,n.frequency_mode.findData(self.noise_location.currentData())));n.power.setValue(self.power.value());n.duration.setValue(self.noise_duration.value());n.mode.setCurrentIndex(0);n.block.setValue(self.noise_block.value());n.psd_window.setCurrentText(self.window_box.currentText());n.segment_seconds.setValue(self.segment.value());n.i.setCurrentIndex(self.i.currentIndex());n.q.setCurrentIndex(self.q.currentIndex());n.update_selected_frequency();self.stage="noise";self.progress.setRange(0,0);self.message.setText("S21完成，正在采集噪声：{}".format(path));n.begin()
        self.noise_block.setValue(n.block.value())
        if n.worker is None:self.fail("噪声测量未能启动。")
        else:n.worker.noise_block.connect(self.plot_noise);n.worker.error.connect(self.fail)
    def plot_noise(self,t,d,ch,rate):
        amp=np.asarray(d["amplitude"]);phase=np.asarray(d["phase"]);self.noise_amp=amp if self.noise_amp is None else np.concatenate((self.noise_amp,amp));self.noise_phase=phase if self.noise_phase is None else np.concatenate((self.noise_phase,phase));limit=1_000_000;self.noise_amp=self.noise_amp[-limit:];self.noise_phase=self.noise_phase[-limit:];f,sxx,nperseg=welch_psd(self.noise_amp,rate,self.segment.value(),self.window_box.currentText());_,syy,_=welch_psd(self.noise_phase,rate,self.segment.value(),self.window_box.currentText(),unwrap=True);self.axes[1,0].clear()
        if f.size>1:self.axes[1,0].loglog(f[1:],sxx[1:],label="Amplitude PSD");self.axes[1,0].loglog(f[1:],syy[1:],label="Phase PSD")
        self.axes[1,0].set_title("Welch noise PSD, nperseg={}".format(nperseg));self.axes[1,0].set_xlabel("Frequency (Hz)");self.axes[1,0].grid(True,which="both",alpha=.3);self.axes[1,0].legend();self.axes[1,1].clear();self.axes[1,1].plot(d["s21_i_norm"],d["s21_q_norm"],".-",markersize=3,label="S21 INorm/QNorm");ti,tq=d["test_point"];self.axes[1,1].plot([ti],[tq],"o",label="noise point");self.axes[1,1].plot(d["i_norm"],d["q_norm"],".",markersize=2,label="noise IQ");self.axes[1,1].axis("equal");self.axes[1,1].grid(True,alpha=.3);self.axes[1,1].legend();self.canvas.draw_idle()
    def noise_finished(self,path):
        self.active=False;self.stage="idle";self.start.setEnabled(True);self.stop.setEnabled(False);self.progress.setRange(0,1);self.progress.setValue(1);self.message.setText("S21和噪声测量完成：{}".format(path))
    def stop_measurement(self):
        if self.stage=="s21" and self.window.s21.worker:self.window.s21.worker.stop()
        elif self.stage=="noise" and self.window.noise.worker:self.window.noise.worker.stop()
    def fail(self,message):
        if not self.active:return
        self.active=False;self.stage="idle";self.start.setEnabled(True);self.stop.setEnabled(False);self.progress.setRange(0,1);self.message.setText("测量失败：{}".format(message))


class IQCalibrationWorker(QThread):
    point_ready = pyqtSignal(object)
    file_saved = pyqtSignal(str)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, manager, parameters, output_directory, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.p = parameters
        self.output_directory = Path(output_directory).expanduser().resolve()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    @staticmethod
    def frequency_name(frequency_hz):
        return "{:.9f}".format(float(frequency_hz)).rstrip("0").rstrip(".")

    def save_point(self, result, frequency_hz, i_position, q_position):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        filename = "{}Hz-{}.txt".format(self.frequency_name(frequency_hz), stamp)
        path = self.output_directory / filename
        i_data = result.data[i_position]
        q_data = result.data[q_position]
        calibrator = IQEllipseCalibrator()
        i_cal, q_cal, calibration = calibrator.fit_transform(i_data, q_data)
        table = np.column_stack((result.time, i_data, q_data, i_cal, q_cal))
        header = (
            "frequency_hz={}\n"
            "e8257d_power_dbm={}\n"
            "p5002a_power_dbm={}\n"
            "p5002a_output_selection={}\n"
            "sample_rate_hz={}\n"
            "i_channel=ai{}\n"
            "q_channel=ai{}\n"
            "i_offset_V={i_offset:.12e}\nq_offset_V={q_offset:.12e}\n"
            "i_axis_V={i_axis:.12e}\nq_axis_V={q_axis:.12e}\n"
            "rotation_rad={rotation_rad:.12e}\n"
            "amplitude_imbalance_db={amplitude_imbalance_db:.12e}\n"
            "phase_imbalance_deg={phase_imbalance_deg:.12e}\n"
            "fit_rms_V={fit_rms:.12e}\n"
            "columns=time_s I_raw_V Q_raw_V I_cal_V Q_cal_V"
        ).format(
            frequency_hz,
            self.p["e8257d_power"],
            self.p["p5002a_power"],
            self.p["p5002a_output_mode"],
            result.actual_sample_rate,
            self.p["i_channel"],
            self.p["q_channel"],
            **calibration.to_dict(),
        )
        np.savetxt(path, table, fmt="%.12e", delimiter="\t", header=header)
        parameter_path = path.with_name(path.stem + "-calibration.json")
        calibration.save_json(parameter_path)
        return path, parameter_path, calibrator.report(frequency_hz), {
            "frequency": float(frequency_hz), "time": result.time,
            "i_raw": i_data, "q_raw": q_data,
            "i_cal": i_cal, "q_cal": q_cal,
            "parameters": calibration,
        }

    def run(self):
        try:
            if not self.manager.source_connected:
                raise RuntimeError("E8257D尚未连接。")
            if not self.manager.p5002_connected:
                raise RuntimeError("P5002A尚未连接。")
            if not self.manager.daq_connected:
                raise RuntimeError("PXIe-4480尚未连接。")

            self.output_directory.mkdir(parents=True, exist_ok=True)
            config = self.manager.daq_config
            daq = self.manager.make_daq()
            selected = config["channels"]
            i_position = selected.index(self.p["i_channel"])
            q_position = selected.index(self.p["q_channel"])
            if self.p["scan"]:
                frequencies = np.linspace(
                    self.p["start_hz"], self.p["stop_hz"], self.p["points"]
                )
            else:
                frequencies = np.asarray([self.p["frequency_hz"]], dtype=float)

            summary = []
            for index, frequency_hz in enumerate(frequencies):
                if self._stop_requested:
                    break
                # Both RF instruments use exactly the same frequency and power.
                with self.manager.source_lock:
                    self.manager.source.set_frequency_hz(float(frequency_hz))
                    self.manager.source.set_power_dbm(self.p["e8257d_power"])
                    self.manager.source.rf_on()
                    self.manager.source.wait_until_complete()
                with self.manager.p5002_lock:
                    self.manager.p5002.configure_cw(
                        float(frequency_hz), self.p["p5002a_power"],
                        port=self.p["p5002a_port"],
                        output_mode=self.p["p5002a_output_mode"],
                    )
                    self.manager.p5002.set_output(True)
                    self.manager.p5002.wait_until_complete(
                        timeout_ms=self.p["instrument_timeout_ms"]
                    )
                time.sleep(self.p["settling_time"])
                with self.manager.daq_lock:
                    result = daq.acquire(
                        sample_count=self.p["sample_count"],
                        trigger_mode=config["trigger_mode"],
                        trigger_source=config["trigger_source"],
                        trigger_edge=config["trigger_edge"],
                        timeout=max(120.0, self.p["instrument_timeout_ms"] / 1000.0),
                    )
                path, parameter_path, report, payload = self.save_point(
                    result, frequency_hz, i_position, q_position
                )
                mean_i, mean_q = float(np.mean(payload["i_raw"])), float(np.mean(payload["q_raw"]))
                amplitude = float(np.hypot(mean_i, mean_q))
                phase = float(np.degrees(np.arctan2(mean_q, mean_i)))
                calibration = payload["parameters"]
                summary.append((
                    frequency_hz,
                    calibration.i_offset,
                    calibration.q_offset,
                    calibration.i_axis,
                    calibration.q_axis,
                    calibration.rotation_rad,
                    calibration.amplitude_imbalance_db,
                    calibration.phase_imbalance_deg,
                    calibration.fit_rms,
                    self.p["e8257d_power"],
                    self.p["p5002a_power"],
                    mean_i,
                    mean_q,
                    amplitude,
                    phase,
                ))
                self.file_saved.emit(str(path))
                self.file_saved.emit(str(parameter_path))
                payload.update({"report": report, "done": index + 1,
                                "total": len(frequencies)})
                self.point_ready.emit(payload)

            if self.p["scan"] and summary:
                summary_path = self.output_directory / "IQ_scan_summary-{}.txt".format(
                    datetime.now().strftime("%Y%m%d-%H%M%S")
                )
                columns = (
                    ("frequency", "Hz", 22),
                    ("I0", "V", 18), ("Q0", "V", 18),
                    ("A_I", "V", 18), ("A_Q", "V", 18),
                    ("rotation_q", "rad", 18),
                    ("amp_imbalance", "dB", 20),
                    ("phase_imbalance", "deg", 22),
                    ("fit_rms", "V", 18),
                    ("E8257D_power", "dBm", 20),
                    ("P5002A_power", "dBm", 20),
                    ("mean_I", "V", 18), ("mean_Q", "V", 18),
                    ("amplitude", "V", 18), ("phase", "deg", 18),
                )
                name_line = " ".join(
                    name.ljust(width) for name, _unit, width in columns
                )
                unit_line = " ".join(
                    ("[{}]".format(unit)).ljust(width)
                    for _name, unit, width in columns
                )
                separator = " ".join("-" * width for _n, _u, width in columns)
                header = (
                    "IQ calibration parameters are placed first.\n"
                    "Amplitude imbalance = 20*log10(A_I/A_Q); "
                    "phase imbalance = rotation q.\n" +
                    name_line + "\n" + unit_line + "\n" + separator
                )
                formats = ["%{}.12e".format(width) for _n, _u, width in columns]
                np.savetxt(
                    summary_path,
                    np.asarray(summary),
                    fmt=formats,
                    delimiter=" ",
                    header=header,
                )
                self.file_saved.emit(str(summary_path))
            self.completed.emit(str(self.output_directory))
        except Exception as error:
            self.failed.emit("{}: {}".format(type(error).__name__, error))


class IQCalibrationTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.manager = window.manager
        self.worker = None
        self.frequencies = []
        self.mean_i = []
        self.mean_q = []
        self.amplitude_imbalance = []
        self.phase_imbalance = []
        layout = QVBoxLayout(self)
        self.status = DeviceStatusBar(self.manager)
        layout.addWidget(self.status)

        box = QGroupBox("IQ校准设置")
        grid = QGridLayout(box)
        self.mode = QComboBox()
        self.mode.addItems(("单频点测量", "频率范围扫描"))
        self.frequency = QDoubleSpinBox()
        self.start_frequency = QDoubleSpinBox()
        self.stop_frequency = QDoubleSpinBox()
        for widget, value in (
            (self.frequency, 5.0), (self.start_frequency, 4.0),
            (self.stop_frequency, 6.0),
        ):
            widget.setRange(0.000001, 1000.0)
            widget.setDecimals(9)
            widget.setValue(value)
            widget.setSuffix(" GHz")
        self.frequency_points = QSpinBox()
        self.frequency_points.setRange(2, 1_000_000)
        self.frequency_points.setValue(101)
        self.e8257d_power = QDoubleSpinBox()
        self.e8257d_power.setRange(-150.0, 30.0)
        self.e8257d_power.setDecimals(2)
        self.e8257d_power.setValue(-30.0)
        self.e8257d_power.setSuffix(" dBm")
        self.p5002a_power = QDoubleSpinBox()
        self.p5002a_power.setRange(-100.0, 30.0)
        self.p5002a_power.setDecimals(2)
        self.p5002a_power.setValue(-30.0)
        self.p5002a_power.setSuffix(" dBm")
        self.sample_count = QSpinBox()
        self.sample_count.setRange(1, 100_000_000)
        self.sample_count.setValue(10_000)
        self.settling_time = QDoubleSpinBox()
        self.settling_time.setRange(0.0, 60.0)
        self.settling_time.setDecimals(3)
        self.settling_time.setValue(0.1)
        self.settling_time.setSuffix(" s")
        self.i_channel = QComboBox()
        self.q_channel = QComboBox()
        for channel in range(6):
            self.i_channel.addItem("ai{}".format(channel), channel)
            self.q_channel.addItem("ai{}".format(channel), channel)
        self.q_channel.setCurrentIndex(1)
        self.folder = QLineEdit(str(IQ_DATA_DIRECTORY))
        choose = QPushButton("选择目录")
        choose.clicked.connect(self.choose_folder)
        self.mode.currentIndexChanged.connect(self.update_mode)

        fields = (
            ("测量模式", self.mode), ("单点频率", self.frequency),
            ("起始频率", self.start_frequency), ("终止频率", self.stop_frequency),
            ("频点数", self.frequency_points),
            ("E8257D输出功率", self.e8257d_power),
            ("P5002A输出功率", self.p5002a_power),
            ("每频点采样数", self.sample_count), ("稳定时间", self.settling_time),
            ("I通道", self.i_channel), ("Q通道", self.q_channel),
        )
        for number, (label, widget) in enumerate(fields):
            row, column = divmod(number, 4)
            grid.addWidget(QLabel(label), row, column * 2)
            grid.addWidget(widget, row, column * 2 + 1)
        grid.addWidget(QLabel("存储目录"), 3, 0)
        grid.addWidget(self.folder, 3, 1, 1, 7)
        grid.addWidget(choose, 3, 8)
        layout.addWidget(box)

        controls = QHBoxLayout()
        self.start_button = QPushButton("开始IQ校准")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.progress = QProgressBar()
        self.last_file = QLabel("尚未保存文件")
        self.start_button.clicked.connect(self.start_measurement)
        self.stop_button.clicked.connect(self.stop_measurement)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.progress)
        controls.addWidget(self.last_file, 1)
        layout.addLayout(controls)
        self.canvas = FigureCanvas(Figure(tight_layout=True))
        axes = self.canvas.figure.subplots(2, 2)
        self.time_axes = axes[0, 0]
        self.raw_iq_axes = axes[0, 1]
        self.cal_iq_axes = axes[1, 0]
        self.imbalance_axes = axes[1, 1]
        self.phase_imbalance_axes = self.imbalance_axes.twinx()
        layout.addWidget(NavigationToolbar(self.canvas, self))
        layout.addWidget(self.canvas, 1)
        self.calibration_text = QPlainTextEdit()
        self.calibration_text.setReadOnly(True)
        self.calibration_text.setMaximumHeight(155)
        self.calibration_text.setPlaceholderText("IQ校准参数与拟合信息将在这里显示。")
        layout.addWidget(self.calibration_text)
        self.update_mode()

    def update_mode(self):
        scan = self.mode.currentIndex() == 1
        self.frequency.setEnabled(not scan)
        self.start_frequency.setEnabled(scan)
        self.stop_frequency.setEnabled(scan)
        self.frequency_points.setEnabled(scan)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择IQ校准数据目录", self.folder.text()
        )
        if folder:
            self.folder.setText(folder)

    def start_measurement(self):
        try:
            if not self.manager.source_connected:
                raise RuntimeError("请先连接E8257D。")
            if not self.manager.p5002_connected:
                raise RuntimeError("请先在仪器控制菜单中连接P5002A。")
            if not self.manager.daq_connected:
                raise RuntimeError("请先连接PXIe-4480。")
            if self.window.daq_dialog.preview_worker and self.window.daq_dialog.preview_worker.isRunning():
                raise RuntimeError("请先停止PXIe-4480电压预览。")
            selected = self.manager.daq_config["channels"]
            i_channel = int(self.i_channel.currentData())
            q_channel = int(self.q_channel.currentData())
            if i_channel == q_channel:
                raise ValueError("I通道和Q通道不能相同。")
            if i_channel not in selected or q_channel not in selected:
                raise ValueError("I、Q通道必须包含在PXIe公共通道配置中。")
            scan = self.mode.currentIndex() == 1
            if scan and self.stop_frequency.value() <= self.start_frequency.value():
                raise ValueError("终止频率必须大于起始频率。")
            parameters = {
                "scan": scan,
                "frequency_hz": self.frequency.value() * 1e9,
                "start_hz": self.start_frequency.value() * 1e9,
                "stop_hz": self.stop_frequency.value() * 1e9,
                "points": self.frequency_points.value(),
                "e8257d_power": self.e8257d_power.value(),
                "p5002a_power": self.p5002a_power.value(),
                "p5002a_output_mode": str(
                    self.window.p5002_window.output_port.currentData()
                ),
                "p5002a_port": 2 if
                    self.window.p5002_window.output_port.currentData() == "PORT2"
                    else 1,
                "sample_count": self.sample_count.value(),
                "settling_time": self.settling_time.value(),
                "i_channel": i_channel,
                "q_channel": q_channel,
                "instrument_timeout_ms": 300_000,
            }
            self.frequencies, self.mean_i, self.mean_q = [], [], []
            self.amplitude_imbalance, self.phase_imbalance = [], []
            self.calibration_text.clear()
            total = parameters["points"] if scan else 1
            self.progress.setRange(0, total)
            self.worker = IQCalibrationWorker(
                self.manager, parameters, self.folder.text(), self
            )
            self.worker.point_ready.connect(self.update_plot)
            self.worker.file_saved.connect(self.last_file.setText)
            self.worker.completed.connect(self.finished)
            self.worker.failed.connect(self.failed)
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.window.source_dialog.setEnabled(False)
            self.window.p5002_window.setEnabled(False)
            self.window.daq_dialog.setEnabled(False)
            self.worker.start()
            save_gui_settings(self.window)
        except Exception as error:
            QMessageBox.critical(self, "无法开始IQ校准", str(error))

    def stop_measurement(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.stop_button.setEnabled(False)

    def update_plot(self, payload):
        frequency = payload["frequency"]
        p = payload["parameters"]
        self.frequencies.append(frequency)
        self.amplitude_imbalance.append(p.amplitude_imbalance_db)
        self.phase_imbalance.append(p.phase_imbalance_deg)
        time_data = np.asarray(payload["time"])
        i_raw, q_raw = np.asarray(payload["i_raw"]), np.asarray(payload["q_raw"])
        i_cal, q_cal = np.asarray(payload["i_cal"]), np.asarray(payload["q_cal"])
        step = max(1, len(time_data)//20_000)

        self.time_axes.clear()
        self.time_axes.plot(time_data[::step], i_raw[::step], label="I")
        self.time_axes.plot(time_data[::step], q_raw[::step], label="Q")
        self.time_axes.set_title("IQ versus time @ {:.9f} GHz".format(frequency/1e9))
        self.time_axes.set_xlabel("Time (s)");self.time_axes.set_ylabel("Voltage (V)")
        self.time_axes.grid(True, alpha=.3);self.time_axes.legend()

        self.raw_iq_axes.clear()
        self.raw_iq_axes.plot(i_raw[::step], q_raw[::step], ".", markersize=2)
        self.raw_iq_axes.plot(p.i_offset, p.q_offset, "rx", label="center")
        self.raw_iq_axes.set_title("Raw IQ ellipse")
        self.raw_iq_axes.set_xlabel("I (V)");self.raw_iq_axes.set_ylabel("Q (V)")
        self.raw_iq_axes.axis("equal");self.raw_iq_axes.grid(True, alpha=.3)

        self.cal_iq_axes.clear()
        self.cal_iq_axes.plot(i_cal[::step], q_cal[::step], ".", markersize=2)
        self.cal_iq_axes.set_title("Calibrated IQ circle")
        self.cal_iq_axes.set_xlabel("I calibrated (V)");self.cal_iq_axes.set_ylabel("Q calibrated (V)")
        self.cal_iq_axes.axis("equal");self.cal_iq_axes.grid(True, alpha=.3)

        self.imbalance_axes.clear()
        self.phase_imbalance_axes.clear()
        if self.mode.currentIndex() == 1:
            frequencies = np.asarray(self.frequencies)/1e9
            self.imbalance_axes.plot(frequencies, self.amplitude_imbalance,
                                     "o-", label="Amplitude (dB)")
            self.phase_imbalance_axes.plot(
                frequencies, self.phase_imbalance,
                "s-", color="tab:red", label="Phase (deg)"
            )
            self.imbalance_axes.set_xlabel("Frequency (GHz)")
            self.imbalance_axes.set_ylabel("Amplitude imbalance (dB)")
            self.phase_imbalance_axes.set_ylabel(
                "Phase imbalance q (deg)", color="tab:red"
            )
            self.imbalance_axes.set_title("IQ imbalance versus frequency")
        else:
            names = ["Amplitude\n(dB)", "Phase q\n(deg)"]
            self.imbalance_axes.bar(names, [p.amplitude_imbalance_db,
                                            p.phase_imbalance_deg])
            self.imbalance_axes.set_title("IQ imbalance")
            self.phase_imbalance_axes.set_yticks([])
        self.imbalance_axes.grid(True, alpha=.3)
        self.calibration_text.setPlainText(payload["report"])
        self.canvas.draw_idle()
        self.progress.setValue(payload["done"])

    def finished(self, directory):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.window.source_dialog.setEnabled(True)
        self.window.p5002_window.setEnabled(True)
        self.window.daq_dialog.setEnabled(True)
        self.window.statusBar().showMessage("IQ校准结束：{}".format(directory))
        latest = latest_iq_summary()
        if latest:
            self.window.s21.calibration_file.setText(latest)
            self.window.noise.calibration_file.setText(latest)
        self.worker = None
        self.window.sync_instrument_controls()

    def failed(self, message):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.window.source_dialog.setEnabled(True)
        self.window.p5002_window.setEnabled(True)
        self.window.daq_dialog.setEnabled(True)
        self.worker = None
        self.window.sync_instrument_controls()
        QMessageBox.critical(self, "IQ校准错误", message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.manager=InstrumentManager();self.setWindowTitle("KID S21、噪声与IQ校准系统 v3");self.resize(1400,900);self.p5002_window=PersistentP5002AWindow();self.manager.p5002_window=self.p5002_window;self.s21=S21Tab(self);self.noise=NoiseTab(self);self.combined=CombinedMeasurementTab(self);self.iq=IQCalibrationTab(self);tabs=QTabWidget();tabs.addTab(self.combined,"S21+噪声一键测量");tabs.addTab(self.s21,"S21扫描测量");tabs.addTab(self.noise,"噪声采集");tabs.addTab(self.iq,"IQ校准");self.setCentralWidget(tabs);self.setStatusBar(QStatusBar());self.source_dialog=E8257DDialog(self.manager,self);self.daq_dialog=PXIeDialog(self.manager,self);self.source_dialog.state_changed.connect(self.refresh_status);self.daq_dialog.state_changed.connect(self.refresh_status);menu=self.menuBar().addMenu("仪器控制");a=QAction("E8257D控制与连接",self);b=QAction("PXIe-4480控制与配置",self);c=QAction("P5002A控制与连接",self);a.triggered.connect(self.open_source);b.triggered.connect(self.open_daq);c.triggered.connect(self.open_p5002);menu.addAction(a);menu.addAction(c);menu.addAction(b);self._last_connections=(False,False,False);self._pending_parameter_sync=set();self.status_timer=QTimer(self);self.status_timer.timeout.connect(self.refresh_status);self.status_timer.start(500);self.statusBar().showMessage("请从“仪器控制”菜单连接设备");load_gui_settings(self)
    def sync_measurement_defaults(self,instrument):
        if instrument=="source" and self.manager.source_connected:
            state=self.manager.source_status;frequency=state.get("frequency_ghz");power=state.get("power_dbm")
            if power is not None:self.s21.power.setValue(power);self.noise.power.setValue(power);self.combined.power.setValue(power);self.iq.e8257d_power.setValue(power)
            if frequency is not None:
                self.s21.center_f.setValue(frequency)
                if self.noise.frequency_mode.currentData()=="MANUAL":self.noise.frequency.setValue(frequency)
                else:self.noise.update_selected_frequency()
                self.combined.center.setValue(frequency);self.iq.frequency.setValue(frequency)
        elif instrument=="p5002" and self.manager.p5002_connected:
            window=self.p5002_window;self.iq.p5002a_power.setValue(window.power.value())
            if not self.manager.source_connected:self.iq.frequency.setValue(window.cw_frequency.value())
    def refresh_status(self):
        self.s21.status.refresh();self.noise.status.refresh();self.combined.status.refresh();self.iq.status.refresh();current=(self.manager.source_connected,self.manager.p5002_connected,self.manager.daq_connected)
        names=("source","p5002","daq")
        for index,name in enumerate(names):
            if current[index] and not self._last_connections[index]:self._pending_parameter_sync.add(name)
            elif not current[index]:self._pending_parameter_sync.discard(name)
        if "source" in self._pending_parameter_sync:self.sync_measurement_defaults("source");self._pending_parameter_sync.discard("source")
        if "p5002" in self._pending_parameter_sync and (self.p5002_window.task is None or not self.p5002_window.task.isRunning()):self.sync_measurement_defaults("p5002");self._pending_parameter_sync.discard("p5002")
        if "daq" in self._pending_parameter_sync:self._pending_parameter_sync.discard("daq")
        self._last_connections=current
    def sync_instrument_controls(self):
        if self.manager.source_connected:self.source_dialog.refresh()
        if self.manager.p5002_connected:self.p5002_window.refresh_instrument_status()
        self.daq_dialog.refresh()
    def open_source(self):
        self.source_dialog.refresh()
        self.source_dialog.show();self.source_dialog.raise_();self.source_dialog.activateWindow()
    def open_daq(self):self.daq_dialog.refresh();self.daq_dialog.show();self.daq_dialog.raise_();self.daq_dialog.activateWindow()
    def open_p5002(self):
        self.p5002_window.show();self.p5002_window.raise_();self.p5002_window.activateWindow()
        if self.manager.p5002_connected:self.p5002_window.refresh_instrument_status()
    def closeEvent(self,event):
        save_gui_settings(self)
        workers=[x.worker for x in (self.s21,self.noise,self.iq) if x.worker and x.worker.isRunning()]
        if self.daq_dialog.preview_worker and self.daq_dialog.preview_worker.isRunning():workers.append(self.daq_dialog.preview_worker)
        for w in workers:
            if isinstance(w,PXIePreviewWorker):w.request_stop()
            else:w.stop()
        for w in workers:
            if not w.wait(5000):event.ignore();QMessageBox.warning(self,"请稍候","测量仍在停止。");return
        self.manager.disconnect_all();event.accept()


def main():
    app=QApplication(sys.argv);app.setStyle("Fusion");window=MainWindow();window.show();sys.exit(app.exec_())


if __name__=="__main__":main()