# -*- coding: utf-8 -*-
"""
YBCO Auto Sweep Control Panel
==============================
Launch the unified GUI for Laser, LakeShore 335, and VNA control.

Design: Deep Space Cyan (极简深色模式仪表板)
Usage:
    python app.py

Requirements:
    pip install pyqt5 pyvisa
"""

import argparse
import os
import sys
from PyQt5.QtWidgets import QApplication
from ui.main_window import MainWindow


def parse_args():
    """解析 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="YBCO Auto Sweep Control Panel"
    )
    parser.add_argument(
        "--resume", type=str, default=None, metavar="DIR",
        help="从 checkpoint 恢复实验（需指定实验输出目录）",
    )
    parser.add_argument(
        "--watchdog", action="store_true",
        help="看门狗模式（监控实验进程心跳）",
    )
    parser.add_argument(
        "--child-pid", type=int, default=None,
        help="被监控进程 PID（仅 --watchdog）",
    )
    parser.add_argument(
        "--resume-path", type=str, default=None, metavar="DIR",
        help="实验输出目录（仅 --watchdog）",
    )
    parser.add_argument(
        "--fill", type=str, default=None, metavar="DIR",
        help="补测模式：指定实验输出目录，读取 fill_plan.json 执行补测",
    )
    parser.add_argument(
        "--no-gui", action="store_true",
        help="无 GUI 模式（配合 --fill 使用，后台执行补测）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # ---- 模式 1: 看门狗 ----
    if args.watchdog:
        if not args.child_pid or not args.resume_path:
            print("Error: --watchdog requires --child-pid and --resume-path")
            return 1
        from watchdog import run as watchdog_run
        import config
        timeout = getattr(config, "heartbeat_timeout_s", 300)
        watchdog_run(args.child_pid, args.resume_path, timeout)
        return 0

    # ---- 模式 4: 补测 fill mode (headless) ----
    if args.fill and args.no_gui:
        fill_dir = os.path.abspath(args.fill)
        if not os.path.isdir(fill_dir):
            print(f"Error: fill directory not found: {fill_dir}")
            return 1
        fill_plan_path = os.path.join(fill_dir, "fill_plan.json")
        if not os.path.exists(fill_plan_path):
            print(f"Error: fill_plan.json not found in {fill_dir}")
            return 1

        import json
        print(f"补测模式: {fill_dir}")
        with open(fill_plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        print(f"  温度点: {plan.get('temperature_plan', [])}")
        print(f"  测量点: {sum(len(m.get('laser_powers_mw', []))
                          for m in plan.get('measurements', []))}")

        from PyQt5.QtCore import QCoreApplication
        _ = QCoreApplication(sys.argv)
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        worker.configure(
            temp_list=plan.get("temperature_plan", []),
            power_list=[],
            vna_power_list=[],
            output_dir=fill_dir,
        )
        worker._run_fill(fill_dir)
        return 0

    # ---- 模式 2 & 3 & 4: GUI (normal + resume + fill GUI) ----
    fill_path = os.path.abspath(args.fill) if args.fill and not args.no_gui else None
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ==================================================================
    # Deep Space Cyan — global QSS theme
    # ==================================================================
    app.setStyleSheet("""
        /* ---- base ---- */
        QMainWindow, QWidget {
            background-color: #0C1014;
            color: #E6EDF3;
        }

        /* ---- cards / sections ---- */
        QGroupBox {
            background-color: #161B22;
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 8px;
            margin-top: 18px;
            padding-top: 20px;
            font-weight: bold;
            color: #8B949E;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            color: #E6EDF3;
            font-size: 13px;
        }

        /* ---- buttons ---- */
        QPushButton {
            background-color: #21262D;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 8px 18px;
            color: #E6EDF3;
            font-weight: bold;
            min-height: 20px;
        }
        QPushButton:hover {
            background-color: #30363D;
            border-color: #22D3EE;
        }
        QPushButton:pressed {
            background-color: #1C2128;
            border-color: #06B6D4;
        }
        QPushButton:disabled {
            background-color: #161B22;
            color: #484F58;
            border-color: rgba(255,255,255,0.06);
        }

        /* ---- inputs ---- */
        QLineEdit, QDoubleSpinBox, QSpinBox {
            background-color: #0C1014;
            border: 1px solid #30363D;
            border-radius: 6px;
            padding: 6px 8px;
            color: #E6EDF3;
        }
        QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {
            border-color: #22D3EE;
        }

        /* ---- combobox ---- */
        QComboBox {
            background-color: #21262D;
            border: 1px solid #30363D;
            border-radius: 6px;
            padding: 6px 10px;
            color: #E6EDF3;
        }
        QComboBox:hover {
            border-color: #22D3EE;
        }
        QComboBox::drop-down {
            border: none;
            width: 22px;
        }
        QComboBox QAbstractItemView {
            background-color: #21262D;
            border: 1px solid #30363D;
            color: #E6EDF3;
            selection-background-color: #22D3EE;
            selection-color: #0C1014;
            outline: none;
        }

        /* ---- log / text edit ---- */
        QTextEdit {
            background-color: #0C1014;
            border: 1px solid #30363D;
            border-radius: 6px;
            color: #8B949E;
            font-family: "JetBrains Mono", "Consolas", "Courier New", monospace;
            font-size: 11px;
        }

        /* ---- labels ---- */
        QLabel {
            color: #E6EDF3;
            background: transparent;
        }

        /* ---- radio buttons ---- */
        QRadioButton {
            color: #E6EDF3;
            spacing: 6px;
        }
        QRadioButton::indicator {
            width: 16px;
            height: 16px;
            border-radius: 9px;
            border: 2px solid #30363D;
            background-color: #0C1014;
        }
        QRadioButton::indicator:checked {
            border-color: #22D3EE;
            background-color: #22D3EE;
        }

        /* ---- scrollbars ---- */
        QScrollBar:vertical {
            background: #0C1014;
            width: 8px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #30363D;
            border-radius: 4px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover {
            background: #22D3EE;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }

        /* ---- tooltips ---- */
        QToolTip {
            background-color: #21262D;
            color: #E6EDF3;
            border: 1px solid #30363D;
            border-radius: 4px;
            padding: 4px 8px;
        }
    """)

    # resume_path 传递给 MainWindow
    resume_path = args.resume

    win = MainWindow(resume_path=resume_path, fill_path=fill_path)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
