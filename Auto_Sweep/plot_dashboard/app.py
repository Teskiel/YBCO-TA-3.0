# -*- coding: utf-8 -*-
"""Plot Dashboard 入口。

启动独立的 PyQt5 窗口用于实验后 S21 数据可视化。
不与主 Auto_Sweep GUI 共享状态，可同时运行。
"""

import os
import sys

# 确保 Auto_Sweep 在 sys.path 中（兼容 Spyder / 直接双击运行）
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PyQt5.QtWidgets import QApplication


# Deep Space Cyan 主题 — 与主 GUI 一致
QSS = """
QMainWindow, QWidget {
    background-color: #0C1014;
    color: #E6EDF3;
}
QGroupBox {
    background-color: #161B22;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 8px;
    margin-top: 18px;
    padding-top: 20px;
    font-size: 13px;
    font-weight: bold;
    color: #E6EDF3;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    color: #E6EDF3;
}
QDoubleSpinBox, QSpinBox {
    background-color: #0C1014;
    border: 1px solid #30363D;
    border-radius: 6px;
    padding: 6px 8px;
    color: #E6EDF3;
}
QDoubleSpinBox:focus, QSpinBox:focus {
    border: 1px solid #22D3EE;
}
QLabel {
    color: #E6EDF3;
}
QSplitter::handle {
    background-color: #30363D;
    width: 2px;
}
QStatusBar {
    background-color: #161B22;
    color: #8B949E;
}
QPushButton {
    border-radius: 6px;
    font-weight: bold;
}
"""


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

    from plot_dashboard.main_window import PlotDashboardMainWindow

    win = PlotDashboardMainWindow()
    win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
