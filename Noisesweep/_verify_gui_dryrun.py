# -*- coding: utf-8 -*-
"""通过 GUI 事件循环跑自动化 dry-run（计划 Step 6b，最接近真实使用）。

用法：
    python _verify_gui_dryrun.py

验证：auto_tab.begin() 从 V3 面板取参 → worker 跑 mock 全链路 → V3 S21/噪声
面板被实时更新 → pic/*.png 落盘 → completed 信号触发。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

import kid_measurement_gui_personal as gui


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # offscreen 测试不能弹模态框：把 QMessageBox 补丁为 no-op
    from PyQt5 import QtWidgets
    QtWidgets.QMessageBox.information = lambda *a, **k: None
    QtWidgets.QMessageBox.critical = lambda *a, **k: None
    QtWidgets.QMessageBox.warning = lambda *a, **k: None
    w = gui.MainWindow()

    # ---- 配置成快速 mock 跑 ----
    at = w.auto_tab
    at.chk_dryrun.setChecked(True)                    # 空跑 mock
    at.radio_custom.setChecked(True)                  # 自定义谐振
    at.custom_res.setText("1")                        # 只测 res1
    at.power_text.setText("0, 3")                     # 两个功率
    at.temperature.setValue(77.0)
    at.experiment_name.setText("_gui_dryrun")
    at.save_root.setText(tempfile.mkdtemp(prefix="gui_dryrun_"))
    at.chk_save_figs.setChecked(True)

    # 从 V3 面板取到的参数：调快
    w.s21.points.setValue(51)
    w.s21.samples.setValue(300)
    w.s21.settle.setValue(0.001)
    w.noise.duration.setValue(0.05)
    w.noise.block.setValue(10000)

    result = {"ok": False, "msg": ""}
    log_lines = []

    def on_completed(path):
        result["ok"] = True
        result["path"] = path
        app.quit()

    def on_failed(msg):
        result["msg"] = msg
        app.quit()

    at.worker_signal_probe = None
    at.begin()
    # 直接抓 worker 信号（begin 内部已连到 V3 面板）
    worker = at.worker
    worker.log_line.connect(log_lines.append)
    worker.completed.connect(on_completed)
    worker.failed.connect(on_failed)

    QTimer.singleShot(120000, app.quit)   # 超时保险
    app.exec_()

    if not result["ok"]:
        print("FAIL:", result.get("msg", "超时"))
        print("\n".join(log_lines[-30:]))
        sys.exit(1)

    root = Path(result["path"])
    h5s = sorted(str(p) for p in root.rglob("*.h5"))
    pngs = sorted(str(p) for p in root.rglob("*.png"))
    print("OK - completed at:", result["path"])
    print("  h5 count:", len(h5s))
    for p in h5s:
        print("   ", p)
    print("  png count:", len(pngs))
    for p in pngs:
        print("   ", p)
    # 检查目录结构
    expected = root / "77K" / "res1"
    dirs = sorted(str(d.relative_to(root)) for d in expected.glob("*/0mW") if d.is_dir())
    print("  0mW point dirs:", dirs)
    # 日志里应出现预测/宽扫/精扫/拟合
    joined = "\n".join(log_lines)
    for key in ("预测", "宽扫", "精扫", "拟合", "图像已保存"):
        if key in joined:
            print("  [log] 含 '{}': True".format(key))
    w.close()


if __name__ == "__main__":
    main()
