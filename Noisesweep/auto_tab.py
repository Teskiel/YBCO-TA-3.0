# -*- coding: utf-8 -*-
"""自动化测量流程 Tab（一期：自动扫描，单温度）。

6 区布局：自动化模块 / 温度 / 激光 / 谐振选择 / 扫描策略 / 保存区 + 底部
控制条（开始/停止/进度/日志）。

S21/噪声测量参数不在此重复画 —— 运行时由 gui_config.collect_config 直接
读取 V3 的 S21/噪声 Tab 控件值。
"""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

import chip_library as cl
import gui_config


def _fmt_fine_table(rows):
    return "\n".join("{:g}, {:g}".format(r[0], r[1]) for r in rows)


class AutoTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.manager = window.manager
        self.worker = None
        self._calibration_meta = []
        self._build_ui()
        self.refresh_availability()

    @property
    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        cfg = self.window.config or {}
        layout = QVBoxLayout(self)

        # 1) 自动化模块
        box1 = QGroupBox("自动化模块")
        row = QHBoxLayout(box1)
        self.module_combo = QComboBox()
        self.module_combo.addItem("自动扫描")
        idx = self.module_combo.count()
        self.module_combo.addItem("变温扫描（二期预留）")
        self.module_combo.setItemData(idx, "二期开放", Qt.UserRole)
        self.module_combo.model().item(idx).setEnabled(False)
        row.addWidget(self.module_combo)
        row.addStretch(1)
        layout.addWidget(box1)

        # 2) 温度区
        box2 = QGroupBox("温度")
        g2 = QGridLayout(box2)
        self.radio_manual = QRadioButton("手动输入目标温度")
        self.radio_actual = QRadioButton("已连接时使用实测温度（超差自动中止）")
        self.radio_manual.setChecked(True)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.001, 1000.0)
        self.temperature.setDecimals(3)
        self.temperature.setValue(float(cfg.get("fixed_temperature_k", 77.0)))
        self.temperature.setSuffix(" K")
        self.chk_temp_sweep = QCheckBox("温度 sweep（二期预留）")
        self.chk_temp_sweep.setEnabled(False)
        self.chk_drive_temp = QCheckBox("驱动温控到目标温度并等稳定")
        self.chk_drive_temp.setToolTip(
            "勾选后调用 LakeShore set_temperature 并等待稳定；仅连接时可勾选。")
        g2.addWidget(self.radio_manual, 0, 0)
        g2.addWidget(self.radio_actual, 0, 1)
        g2.addWidget(QLabel("温度"), 1, 0)
        g2.addWidget(self.temperature, 1, 1)
        g2.addWidget(self.chk_drive_temp, 2, 0, 1, 2)
        g2.addWidget(self.chk_temp_sweep, 3, 0, 1, 2)
        layout.addWidget(box2)

        # 3) 激光区
        box3 = QGroupBox("激光")
        g3 = QGridLayout(box3)
        self.chk_laser_sweep = QCheckBox("激光 sweep")
        self.power_text = QLineEdit(",".join("{:g}".format(p) for p in
                                             cfg.get("laser_power_mw", [0.0])))
        self.power_text.setPlaceholderText("如 0,1,3（未连接激光时仅可填 1 个数）")
        self.wavelength = QDoubleSpinBox()
        self.wavelength.setRange(100.0, 10000.0)
        self.wavelength.setDecimals(1)
        self.wavelength.setValue(float(cfg.get("laser_wavelength_nm", 1550.0)))
        self.wavelength.setSuffix(" nm")
        g3.addWidget(self.chk_laser_sweep, 0, 0, 1, 2)
        g3.addWidget(QLabel("功率 (mW)"), 1, 0)
        g3.addWidget(self.power_text, 1, 1)
        g3.addWidget(QLabel("波长"), 2, 0)
        g3.addWidget(self.wavelength, 2, 1)
        layout.addWidget(box3)

        # 4) 谐振选择区
        box4 = QGroupBox("谐振选择（芯片标定库）")
        g4 = QGridLayout(box4)
        self.data_process_dir = QLineEdit(cfg.get("data_process_dir", ""))
        btn_dp = QPushButton("选择目录")
        btn_dp.clicked.connect(self._choose_data_process_dir)
        self.calibration_combo = QComboBox()
        btn_refresh_cal = QPushButton("刷新标定")
        btn_refresh_cal.clicked.connect(self.refresh_calibrations)
        self.n_modes_label = QLabel("谐振数: -")
        self.btn_predict = QPushButton("预测谐振频率")
        self.btn_predict.clicked.connect(self.refresh_prediction)
        self.predict_text = QPlainTextEdit()
        self.predict_text.setReadOnly(True)
        self.predict_text.setMaximumHeight(120)
        self.radio_all = QRadioButton("全部谐振器")
        self.radio_custom = QRadioButton("自定义")
        self.radio_all.setChecked(True)
        self.custom_res = QLineEdit()
        self.custom_res.setPlaceholderText("仅数字，逗号分隔，如 1,3,5")
        g4.addWidget(QLabel("标定资产仓库目录"), 0, 0)
        g4.addWidget(self.data_process_dir, 0, 1, 1, 3)
        g4.addWidget(btn_dp, 0, 4)
        g4.addWidget(QLabel("标定存档"), 1, 0)
        g4.addWidget(self.calibration_combo, 1, 1, 1, 3)
        g4.addWidget(btn_refresh_cal, 1, 4)
        g4.addWidget(self.n_modes_label, 2, 0)
        g4.addWidget(self.btn_predict, 2, 1)
        g4.addWidget(self.predict_text, 3, 0, 2, 5)
        g4.addWidget(self.radio_all, 5, 0)
        g4.addWidget(self.radio_custom, 5, 1)
        g4.addWidget(self.custom_res, 5, 2, 1, 3)
        layout.addWidget(box4)

        # 5) 扫描策略区
        box5 = QGroupBox("扫描策略（S21/噪声参数实时取自 S21/噪声 Tab 控件）")
        g5 = QGridLayout(box5)
        self.wide_mode = QComboBox()
        for text, data in (("固定带宽", "fixed"),
                           ("随温度插值", "temperature"),
                           ("公式", "formula")):
            self.wide_mode.addItem(text, data)
        self.wide_mode.setCurrentIndex(
            max(0, self.wide_mode.findData(cfg.get("wide_bandwidth_mode", "fixed"))))
        self.wide_mhz = QDoubleSpinBox()
        self.wide_mhz.setRange(0.1, 100000.0)
        self.wide_mhz.setDecimals(1)
        self.wide_mhz.setValue(float(cfg.get("wide_bandwidth_mhz", 150.0)))
        self.wide_mhz.setSuffix(" MHz")
        self.fine_table = QPlainTextEdit()
        self.fine_table.setMaximumHeight(90)
        self.fine_table.setPlainText(
            _fmt_fine_table(cfg.get("fine_bandwidth_mhz_by_temperature",
                                    [[4, 15], [20, 20], [40, 20], [77, 25]])))
        btn_fine_reset = QPushButton("恢复默认")
        btn_fine_reset.clicked.connect(self._reset_fine_table)
        g5.addWidget(QLabel("宽扫带宽模式"), 0, 0)
        g5.addWidget(self.wide_mode, 0, 1)
        g5.addWidget(QLabel("固定带宽"), 0, 2)
        g5.addWidget(self.wide_mhz, 0, 3)
        g5.addWidget(QLabel("精扫带宽表 (T K, MHz)"), 1, 0)
        g5.addWidget(self.fine_table, 1, 1, 1, 3)
        g5.addWidget(btn_fine_reset, 2, 3)
        layout.addWidget(box5)

        # 6) 保存区
        box6 = QGroupBox("保存")
        g6 = QGridLayout(box6)
        self.save_root = QLineEdit(cfg.get("save_root", ""))
        btn_sr = QPushButton("选择目录")
        btn_sr.clicked.connect(self._choose_save_root)
        self.experiment_name = QLineEdit(cfg.get("experiment_name", ""))
        self.experiment_name.setPlaceholderText("留空则自动生成（如 77K&0mW）")
        self.checkpoint_path = QLineEdit(cfg.get("checkpoint_path", "") or "")
        self.checkpoint_path.setPlaceholderText("留空 = save_root/实验名/checkpoint.json")
        self.chk_save_figs = QCheckBox("保存 V3 面板图")
        self.chk_save_figs.setChecked(bool(cfg.get("save_figures", True)))
        self.chk_skip_noise = QCheckBox("跳过噪声")
        self.chk_dryrun = QCheckBox("空跑（mock，不接触硬件）")
        self.chk_force = QCheckBox("强制重跑（忽略 checkpoint）")
        g6.addWidget(QLabel("保存根目录"), 0, 0)
        g6.addWidget(self.save_root, 0, 1, 1, 3)
        g6.addWidget(btn_sr, 0, 4)
        g6.addWidget(QLabel("实验名"), 1, 0)
        g6.addWidget(self.experiment_name, 1, 1, 1, 4)
        g6.addWidget(QLabel("checkpoint"), 2, 0)
        g6.addWidget(self.checkpoint_path, 2, 1, 1, 4)
        g6.addWidget(self.chk_save_figs, 3, 0, 1, 2)
        g6.addWidget(self.chk_skip_noise, 3, 2)
        g6.addWidget(self.chk_dryrun, 3, 3)
        g6.addWidget(self.chk_force, 3, 4)
        btn_save = QPushButton("保存当前设置为默认")
        btn_save.clicked.connect(self.save_settings)
        g6.addWidget(btn_save, 4, 4)
        layout.addWidget(box6)

        # 控制条 + 日志
        row = QHBoxLayout()
        self.start = QPushButton("开始自动扫描")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.progress = QProgressBar()
        self.current_label = QLabel("尚未开始")
        self.start.clicked.connect(self.begin)
        self.stop_button.clicked.connect(self.stop_measurement)
        row.addWidget(self.start)
        row.addWidget(self.stop_button)
        row.addWidget(self.progress, 1)
        row.addWidget(self.current_label)
        layout.addLayout(row)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("自动化日志将在这里显示。")
        layout.addWidget(self.log_text, 1)

        self.refresh_calibrations()

    # ------------------------------------------------------------------
    # 仪器状态相关
    # ------------------------------------------------------------------

    def refresh_availability(self):
        """连接状态变化时调用：按 LakeShore/激光是否连接启用相关控件。"""
        temp_on = self.manager.temp_connected
        laser_on = self.manager.laser_connected
        self.radio_actual.setEnabled(temp_on)
        self.radio_manual.setEnabled(True)
        self.chk_drive_temp.setEnabled(temp_on)
        self.chk_laser_sweep.setEnabled(laser_on)
        tip = "" if temp_on else "（未连接 LakeShore，温度仅按手动值假设）"
        self.radio_actual.setToolTip("已连接时读实测温度用于预测/命名；|目标-实测|>限值自动中止" + tip)

    # ------------------------------------------------------------------
    # 谐振标定
    # ------------------------------------------------------------------

    def _choose_data_process_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择标定资产仓库目录",
                                                self.data_process_dir.text())
        if path:
            self.data_process_dir.setText(path)
            self.refresh_calibrations()

    def _choose_save_root(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存根目录",
                                                self.save_root.text())
        if path:
            self.save_root.setText(path)

    def refresh_calibrations(self):
        self.calibration_combo.clear()
        self._calibration_meta = []
        dp = self.data_process_dir.text().strip()
        cals = cl.list_calibrations(dp) if dp else []
        self._calibration_meta = cals
        for c in cals:
            self.calibration_combo.addItem("{}__{}".format(c["chip_id"], c["run_id"]))
        cfg = self.window.config or {}
        default_chip = cfg.get("chip_id")
        default_run = cfg.get("chip_run_id")
        for i, c in enumerate(cals):
            if c["chip_id"] == default_chip and c["run_id"] == default_run:
                self.calibration_combo.setCurrentIndex(i)
                break
        self.refresh_n_modes()

    def _current_calibration(self):
        idx = self.calibration_combo.currentIndex()
        if 0 <= idx < len(self._calibration_meta):
            c = self._calibration_meta[idx]
            return c["chip_id"], c["run_id"]
        return None, None

    def refresh_n_modes(self):
        chip_id, run_id = self._current_calibration()
        dp = self.data_process_dir.text().strip()
        if not chip_id:
            self.n_modes_label.setText("谐振数: -")
            return
        n = cl.n_modes(chip_id=chip_id, run_id=run_id, data_process_dir=dp)
        self.n_modes_label.setText("谐振数: {}".format(n if n else "未知"))

    def refresh_prediction(self):
        chip_id, run_id = self._current_calibration()
        dp = self.data_process_dir.text().strip()
        T = float(self.temperature.value())
        if not chip_id:
            self.predict_text.setPlainText("请先选择标定存档。")
            return
        f_pred, half = cl.predict_resonance_frequencies(
            chip_id=chip_id, run_id=run_id, T_k=T, data_process_dir=dp)
        if f_pred is None:
            self.predict_text.setPlainText("预测失败（标定缺失或谐振器数不符）。")
            return
        lines = ["预测谐振频率 @ {:.2f} K:".format(T), "-" * 46]
        for i, (f, h) in enumerate(zip(f_pred, half)):
            lines.append("  res{}: {:.6f} GHz  (±{:.1f} MHz)".format(
                i + 1, f / 1e9, h / 1e6))
        self.predict_text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # 收集配置
    # ------------------------------------------------------------------

    def collect(self):
        cfg = {}
        # 温度
        cfg["read_actual_temperature"] = self.radio_actual.isChecked()
        cfg["drive_temperature"] = self.chk_drive_temp.isChecked()
        T = float(self.temperature.value())
        cfg["fixed_temperature_k"] = T
        cfg["temperature_list_k"] = [T]
        # 激光
        powers = gui_config.parse_number_list(self.power_text.text())
        cfg["laser_power_mw"] = powers if powers else [0.0]
        cfg["laser_wavelength_nm"] = float(self.wavelength.value())
        # 谐振
        dp = self.data_process_dir.text().strip()
        cfg["data_process_dir"] = dp
        chip_id, run_id = self._current_calibration()
        if chip_id:
            cfg["chip_id"] = chip_id
            cfg["chip_run_id"] = run_id
            cfg["use_chip_library"] = True
        # 谐振选择
        if self.radio_all.isChecked() or not self.custom_res.text().strip():
            cfg["resonator_selection"] = "all"
        else:
            idxs = gui_config.parse_res_index_list(self.custom_res.text())
            cfg["resonator_selection"] = idxs if idxs else "all"
        # 扫描策略
        cfg["wide_bandwidth_mode"] = self.wide_mode.currentData()
        cfg["wide_bandwidth_mhz"] = float(self.wide_mhz.value())
        cfg["fine_bandwidth_mhz_by_temperature"] = self._parse_fine_table()
        # 保存
        cfg["save_root"] = self.save_root.text().strip()
        cfg["experiment_name"] = self.experiment_name.text().strip()
        cp = self.checkpoint_path.text().strip()
        cfg["checkpoint_path"] = cp or None
        cfg["save_gui_figures"] = self.chk_save_figs.isChecked()
        cfg["skip_noise"] = self.chk_skip_noise.isChecked()
        cfg["force"] = self.chk_force.isChecked()
        cfg["backend"] = "mock" if self.chk_dryrun.isChecked() \
            else (self.window.config or {}).get("backend", "autosweep")
        return cfg

    def _parse_fine_table(self):
        rows = []
        for line in self.fine_table.toPlainText().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.replace(",", " ").split()]
            if len(parts) >= 2:
                try:
                    rows.append([float(parts[0]), float(parts[1])])
                except ValueError:
                    pass
        return rows or [[4.0, 15.0], [20.0, 20.0], [40.0, 20.0], [77.0, 25.0]]

    def _reset_fine_table(self):
        self.fine_table.setPlainText(_fmt_fine_table(
            [[4, 15], [20, 20], [40, 20], [77, 25]]))

    def save_settings(self):
        """把自动化页当前值写回 gui_config.json（下次启动作为默认）。"""
        cfg = self.window.config
        cfg["save_root"] = self.save_root.text().strip()
        cfg["experiment_name"] = self.experiment_name.text().strip()
        cfg["fixed_temperature_k"] = float(self.temperature.value())
        powers = gui_config.parse_number_list(self.power_text.text())
        cfg["laser_power_mw"] = powers if powers else [0.0]
        cfg["laser_wavelength_nm"] = float(self.wavelength.value())
        cfg["data_process_dir"] = self.data_process_dir.text().strip()
        cfg["wide_bandwidth_mode"] = self.wide_mode.currentData()
        cfg["wide_bandwidth_mhz"] = float(self.wide_mhz.value())
        cfg["fine_bandwidth_mhz_by_temperature"] = self._parse_fine_table()
        cfg["save_gui_figures"] = self.chk_save_figs.isChecked()
        chip_id, run_id = self._current_calibration()
        if chip_id:
            cfg["chip_id"] = chip_id
            cfg["chip_run_id"] = run_id
        gui_config.save_gui_config(cfg)
        try:
            self.window.statusBar().showMessage("自动化页设置已保存到 gui_config.json")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------

    def begin(self):
        if self.busy:
            return
        try:
            from automation_worker import AutomationWorker
            from gui_config import collect_config
            cfg = collect_config(self.window, self.window.config, self)
            if cfg["backend"] == "mock":
                # 防呆：mock 空跑产出的是模拟数据，与真实仪器形态完全不同
                # （旧 mock 噪声绕圆游走 → IQ 画整圆、phase PSD 巨大），
                # 用户极易拿假数据当真。dry-run 验证脚本把 QMessageBox patch
                # 为 no-op（返回 None）→ 验证路径自动继续，不弹窗。
                warn = ("当前为 MOCK 模式（backend=mock）：仪器为模拟器，"
                        "测量数据为模拟值，不是真实测量！\n"
                        "如需真实测量，请先修改 gui_config.json 的 backend 配置。")
                self.log_text.appendPlainText(">>> ⚠ " + warn)
                ans = QMessageBox.warning(self, "MOCK 模式", warn + "\n\n确定继续？",
                                          QMessageBox.Ok | QMessageBox.Cancel)
                if ans is not None and ans != QMessageBox.Ok:
                    return
            if cfg["backend"] != "mock":
                if not self.manager.source_connected:
                    raise RuntimeError("E8257D 未连接，请先在「仪器与连接」页连接。")
                if not self.manager.daq_connected:
                    raise RuntimeError("PXIe-4480 未连接，请先在「仪器与连接」页连接。")
            if not cfg.get("iq_calibration_file"):
                raise RuntimeError("S21 页的 IQ 校准文件为空，请先选择。")
            if not cfg.get("resonators"):
                raise RuntimeError("谐振列表为空。")
            powers = gui_config.parse_number_list(self.power_text.text())
            if not self.manager.laser_connected and len(powers) > 1:
                QMessageBox.warning(
                    self, "激光未连接",
                    "激光未连接，仅使用第一个功率 {:.1f} mW，其余忽略。".format(powers[0]))

            self.worker = AutomationWorker(self.window, cfg,
                                           dry_run=(cfg["backend"] == "mock"),
                                           parent=self)
            self.worker.progress.connect(self._on_progress)
            self.worker.stage.connect(self._on_stage)
            self.worker.log_line.connect(self._on_log_line)
            self.worker.s21_point.connect(self.window.s21.plot)
            self.worker.s21_fit_ready.connect(self.window.s21.show_fit)
            self.worker.s21_fit_failed.connect(self.window.s21.fit_failed)
            self.worker.noise_block.connect(self.window.noise.plot)
            self.worker.noise_reset.connect(self.window.noise.reset)
            self.worker.save_figure.connect(self._on_save_figure)
            self.worker.completed.connect(self._on_completed)
            self.worker.failed.connect(self._on_failed)
            self.progress.setRange(0, 0)
            self.log_text.clear()
            self.set_running(True)
            self.worker.start()
        except Exception as exc:
            QMessageBox.critical(self, "无法开始自动化", str(exc))

    def stop_measurement(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.log_text.appendPlainText(">>> 已请求停止，等待当前点完成后退出…")
            self.stop_button.setEnabled(False)

    def set_running(self, running):
        self.start.setEnabled(not running)
        self.stop_button.setEnabled(running)
        for tab in (self.window.s21, self.window.noise):
            tab.start.setEnabled(not running)
            tab.stop_button.setEnabled(False)
        self.window.iq.start_button.setEnabled(not running)
        self.window.iq.stop_button.setEnabled(False)
        self.window.source_dialog.setEnabled(not running)
        self.window.daq_dialog.setEnabled(not running)
        self.window.p5002_window.setEnabled(not running)

    # ------------------------------------------------------------------
    # 信号处理
    # ------------------------------------------------------------------

    def _on_progress(self, done, total, label):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.current_label.setText(label)

    def _on_stage(self, stage):
        self.current_label.setText(stage)

    def _on_log_line(self, line):
        self.log_text.appendPlainText(line)

    def _on_save_figure(self, meta):
        """GUI 线程保存 V3 面板 canvas 到 <实验根>/<目标T>K/PIC/{类型}/。

        worker 只发 meta（含 save_root/目标/实际温度/chip/功率/谐振/group/type/mode），
        本方法在 GUI 线程对面板画布 savefig，图上加 suptitle 标注。
        """
        from orchestrator_noisesweep import _pic_annotation, _pic_name, fmt_K
        try:
            pic_dir = Path(meta["save_root"]) / fmt_K(float(meta["target_k"])) \
                / "PIC" / str(meta["group"])
            pic_dir.mkdir(parents=True, exist_ok=True)
            name = _pic_name(meta)
            fig = self.window.noise.canvas.figure \
                if str(meta.get("group", "")).startswith("noise") \
                else self.window.s21.canvas.figure
            fig.suptitle(_pic_annotation(meta), fontsize=9)
            # 注意：不在此处调用 fig.tight_layout(rect=...) —— 对活 QtAgg 画布触发
            # tight 引擎→渲染器会原生崩溃（闪退）。仅 suptitle（文字 artist）+ savefig
            # 与面板工具栏保存等价，安全；保存时 figure 的 on-draw tight 引擎会为 suptitle
            # 留出顶部空间。
            fig.savefig(str(pic_dir / name), dpi=150)
            self.log_text.appendPlainText("图像已保存: {}".format(pic_dir / name))
        except Exception as exc:
            self.log_text.appendPlainText("保存图像失败（不影响测量）: {}".format(exc))

    def _on_completed(self, path):
        self.set_running(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.current_label.setText("完成")
        QMessageBox.information(self, "自动化完成", "保存目录：\n{}".format(path))

    def _on_failed(self, msg):
        self.set_running(False)
        self.current_label.setText("失败")
        QMessageBox.critical(self, "自动化失败", msg)
