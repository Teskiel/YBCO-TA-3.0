# -*- coding: utf-8 -*-
"""参数自动保存/恢复测试（offscreen，隔离的用户设置文件）。

用法：
    python _verify_gui_persistence.py

覆盖：
  1) 6 个 Tab 代表性控件 + DAQ 改动 → 防抖落盘；
  2) 二次构造 MainWindow 恢复全部改动；
  3) 「恢复默认设置」删除用户文件并回启动默认；
  4) 首次运行（无用户文件）行为与现状一致。
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 必须在 import gui_config 之前设置（USER_SETTINGS_PATH 在模块 import 时计算）
os.environ["KID_GUI_USER_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(), "gui_user_settings.json")

from PyQt5.QtWidgets import QApplication, QMessageBox

import gui_config
import kid_measurement_gui_personal as gui


def build():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    # offscreen 不能弹模态框；question 也要吞掉（重置确认）
    QMessageBox.information = lambda *a, **k: None
    QMessageBox.critical = lambda *a, **k: None
    QMessageBox.warning = lambda *a, **k: None
    QMessageBox.question = lambda *a, **k: QMessageBox.Yes
    return gui.MainWindow()


def main():
    w1 = build()
    s, n, c, q = w1.s21, w1.noise, w1.combined, w1.iq
    a, it = w1.auto_tab, w1.instruments_tab

    # 1) 首次运行：V3 Tab 用硬编码默认；自动化 Tab 从 gui_config.json 播种
    assert s.points.value() == 201, "S21 默认频点数应为硬编码 201"
    assert a.temperature.value() == 77.0, "自动化默认温度应为 77.0 K"
    assert not os.path.exists(gui_config.USER_SETTINGS_PATH), "首次运行不应生成用户设置文件"

    # 2) 改动各 Tab 代表性控件 + DAQ
    s.points.setValue(333)
    s.center_f.setValue(5.55)
    s.q.setCurrentIndex(3)
    n.duration.setValue(22.5)
    n.psd_window.setCurrentText("boxcar")
    n.frequency_mode.setCurrentIndex(2)            # MAX_RESPONSE
    c.points.setValue(404)
    c.noise_location.setCurrentIndex(1)            # MAX_RESPONSE
    c.window_box.setCurrentText("hann")
    q.mode.setCurrentIndex(1)                      # 频率范围扫描
    q.start_frequency.setValue(4.4)
    q.frequency_points.setValue(55)
    a.radio_actual.setChecked(True)
    a.temperature.setValue(12.3)
    a.power_text.setText("0,1,3")
    a.custom_res.setText("1,3")
    a.wide_mode.setCurrentIndex(2)                 # formula
    a.fine_table.setPlainText("10, 5\n77, 25")
    a.experiment_name.setText("exp_auto")
    it._ls_setpoint.setValue(50.5)
    it._laser_wavelength.setValue(1510.0)
    w1.manager.daq_config.update({
        "device_name": "PXI2Slot5", "channels": [1, 2],
        "sample_rate": 2_000_000.0, "voltage_range": 5.0,
        "coupling": "AC", "trigger_mode": "DIGITAL",
        "trigger_source": "/PXI2Slot5/PFI0", "trigger_edge": "FALLING",
    })

    # 3) 触发防抖落盘（最后一次控件变化后手动发射 500ms 单发计时器）
    s.bandwidth_mhz.setValue(1234)
    w1._autosave_timer.timeout.emit()
    assert os.path.exists(gui_config.USER_SETTINGS_PATH), "防抖后应生成用户设置文件"

    # 4) 二次启动应恢复全部改动
    w2 = build()
    s2, n2, c2, q2 = w2.s21, w2.noise, w2.combined, w2.iq
    a2, it2 = w2.auto_tab, w2.instruments_tab
    assert s2.points.value() == 333 and s2.center_f.value() == 5.55, "S21 恢复失败"
    assert s2.bandwidth_mhz.value() == 1234 and s2.q.currentIndex() == 3, "S21 恢复失败(2)"
    assert n2.duration.value() == 22.5 and n2.psd_window.currentText() == "boxcar", "噪声恢复失败"
    assert n2.frequency_mode.currentData() == "MAX_RESPONSE", "噪声频点模式恢复失败"
    assert c2.points.value() == 404 and c2.noise_location.currentData() == "MAX_RESPONSE", "一键页恢复失败"
    assert c2.window_box.currentText() == "hann", "一键页窗口恢复失败"
    assert q2.mode.currentIndex() == 1 and q2.start_frequency.value() == 4.4, "IQ恢复失败"
    assert q2.frequency_points.value() == 55, "IQ频点数恢复失败"
    assert a2.radio_actual.isChecked() and a2.temperature.value() == 12.3, "自动化温度恢复失败"
    assert a2.power_text.text() == "0,1,3" and a2.custom_res.text() == "1,3", "自动化功率/谐振恢复失败"
    assert a2.wide_mode.currentData() == "formula", "宽扫模式恢复失败"
    assert a2.fine_table.toPlainText() == "10, 5\n77, 25", "精扫表恢复失败"
    assert a2.experiment_name.text() == "exp_auto", "实验名恢复失败"
    assert it2._ls_setpoint.value() == 50.5, "仪器 setpoint 恢复失败"
    assert it2._laser_wavelength.value() == 1510.0, "仪器波长恢复失败"
    assert w2.manager.daq_config["device_name"] == "PXI2Slot5", "DAQ 设备恢复失败"
    assert w2.manager.daq_config["channels"] == [1, 2], "DAQ 通道恢复失败"
    assert w2.manager.daq_config["sample_rate"] == 2_000_000.0 \
        and w2.manager.daq_config["coupling"] == "AC", "DAQ 采样率/耦合恢复失败"
    assert w2.manager.daq_config["trigger_mode"] == "DIGITAL", "DAQ 触发恢复失败"
    if a2._calibration_meta:
        assert a2.calibration_combo.currentIndex() >= 0, "标定下拉应能匹配保存的 chip/run"

    # 5) 恢复默认：删除用户文件 + 控件回启动默认
    w2.reset_user_settings()
    assert not os.path.exists(gui_config.USER_SETTINGS_PATH), "恢复默认应删除用户设置文件"
    assert s2.points.value() == 201, "恢复默认后 S21 频点数应为 201"
    assert a2.experiment_name.text() == "", "恢复默认后实验名应为空"

    print("PERSISTENCE OK")
    w1.close()
    w2.close()


if __name__ == "__main__":
    main()
