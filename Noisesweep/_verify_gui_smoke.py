# -*- coding: utf-8 -*-
"""主窗口冒烟测试（offscreen）：验证 6 个 Tab、配置、新 Tab 可用性。

用法：
    python _verify_gui_smoke.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox

import kid_measurement_gui_personal as gui


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # offscreen 不能弹模态框
    QMessageBox.information = lambda *a, **k: None
    QMessageBox.critical = lambda *a, **k: None
    QMessageBox.warning = lambda *a, **k: None
    w = gui.MainWindow()
    names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    print("Tab count:", w.tabs.count())
    print("Tabs:", names)
    assert w.tabs.count() == 6, "预期 6 个 Tab"
    assert names[-2] == "自动化测量流程" and names[-1] == "仪器与连接"
    assert w.manager.config.get("chip_id") == "YBCO#1145"
    print("config chip_id:", w.manager.config["chip_id"])
    print("save_root:", w.manager.config["save_root"])
    # 自动化页：预测谐振
    w.auto_tab.refresh_prediction()
    print("prediction preview:")
    print(w.auto_tab.predict_text.toPlainText())
    # 仪器页连接态（未连接）
    w.instruments_tab.update_connection_states()
    print("temp_connected:", w.manager.temp_connected,
          "laser_connected:", w.manager.laser_connected)
    # 自动化页可用性
    w.auto_tab.refresh_availability()
    print("chk_drive_temp enabled:", w.auto_tab.chk_drive_temp.isEnabled())
    w.manager.config["backend"] = "mock"
    w.manager.config["lakeshore_visa_address"] = "mock:ASRL4"
    w.manager.config["laser_visa_address"] = "mock:TCPIP"
    w.manager.connect_temp(w.manager.config)
    w.manager.connect_laser(w.manager.config)
    w.auto_tab.refresh_availability()
    print("after mock connect: temp_connected=", w.manager.temp_connected,
          "laser_connected=", w.manager.laser_connected,
          "| chk_drive_temp enabled:", w.auto_tab.chk_drive_temp.isEnabled(),
          "radio_actual enabled:", w.auto_tab.radio_actual.isEnabled())
    print("SMOKE OK")
    w.close()


if __name__ == "__main__":
    main()
