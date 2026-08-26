# -*- coding: utf-8 -*-
"""GUI 输入参数自动保存/恢复（Qt-aware，gui_config.py 的补充）。

gui_config.json        = 默认基线（SEED + "保存当前设置为默认" + 连接时地址）。
gui_user_settings.json = 上次使用值叠加层（防抖自动保存 + 关窗落盘）。

启动顺序（MainWindow.__init__ 内）：
    建 Tab（AutoTab/InstrumentsTab 已从 gui_config 播种）→ 快照默认 →
    apply_all(load_user_settings()) 叠加 → wire_auto_save 接变更信号。

全部函数仅在 GUI 线程调用；worker 从不触碰本模块。
"""

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit,
    QPlainTextEdit, QRadioButton, QSpinBox,
)

import gui_config


# =========================================================================
# 设值小助手
# =========================================================================

def _block(widget, fn):
    """运行 fn 期间屏蔽 widget 信号（防止程序化恢复触发保存）。"""
    widget.blockSignals(True)
    try:
        fn()
    finally:
        widget.blockSignals(False)


def _set_combo(combo, value):
    """按 itemData 优先、itemText 回退恢复 QComboBox。"""
    if value is None:
        return
    idx = combo.findData(value)
    if idx < 0:
        idx = combo.findText(str(value))
    if idx >= 0:
        combo.setCurrentIndex(idx)


def _apply_spin(w, key, v, cast):
    if key in v:
        w.setValue(cast(v[key]))


def _apply_text(w, key, v):            # QLineEdit
    if key in v:
        w.setText(str(v[key]))


def _apply_plain(w, key, v):           # QPlainTextEdit（用 setPlainText）
    if key in v:
        w.setPlainText(str(v[key]))


def _apply_check(w, key, v):
    if key in v:
        w.setChecked(bool(v[key]))


def _apply_combo(w, key, v):
    if key in v:
        _set_combo(w, v[key])


def _set_radio(radio_true, radio_false, value):
    _block(radio_true, lambda: radio_true.setChecked(bool(value)))
    _block(radio_false, lambda: radio_false.setChecked(not bool(value)))


# =========================================================================
# 收集全部控件 → 配置 dict
# =========================================================================

def collect_all(window):
    """把 6 个 Tab + DAQ 配置读成扁平 dict（键带 Tab 前缀，命名空间隔离）。"""
    out = {}

    s = getattr(window, "s21", None)
    if s is not None:
        out.update({
            "s21.center_f_ghz": s.center_f.value(),
            "s21.bandwidth_mhz": s.bandwidth_mhz.value(),
            "s21.points": int(s.points.value()),
            "s21.power_dbm": s.power.value(),
            "s21.settle_s": s.settle.value(),
            "s21.samples": int(s.samples.value()),
            "s21.i_channel": s.i.currentData(),
            "s21.q_channel": s.q.currentData(),
            "s21.fit_enabled": s.fit_enabled.isChecked(),
            "s21.resonator_name": s.resonator_name.text(),
            "s21.temperature_mk": s.temperature.value(),
            "s21.fit_power_dbm": s.fit_power.value(),
            "s21.folder": s.folder.text(),
            "s21.calibration_file": s.calibration_file.text(),
        })

    n = getattr(window, "noise", None)
    if n is not None:
        out.update({
            "noise.frequency_ghz": n.frequency.value(),
            "noise.frequency_mode": n.frequency_mode.currentData(),
            "noise.power_dbm": n.power.value(),
            "noise.settle_s": n.settle.value(),
            "noise.mode": n.mode.currentText(),
            "noise.duration_s": n.duration.value(),
            "noise.block": int(n.block.value()),
            "noise.i_channel": n.i.currentData(),
            "noise.q_channel": n.q.currentData(),
            "noise.psd_window": n.psd_window.currentText(),
            "noise.segment_seconds": n.segment_seconds.value(),
            "noise.iq_plot_points": int(n.iq_plot_points.value()),
            "noise.folder": n.folder.text(),
            "noise.calibration_file": n.calibration_file.text(),
            "noise.s21_file": n.s21_file.text(),
        })

    c = getattr(window, "combined", None)
    if c is not None:
        out.update({
            "combined.center_ghz": c.center.value(),
            "combined.bandwidth_mhz": c.bandwidth.value(),
            "combined.points": int(c.points.value()),
            "combined.samples": int(c.samples.value()),
            "combined.power_dbm": c.power.value(),
            "combined.settle_s": c.settle.value(),
            "combined.noise_location": c.noise_location.currentData(),
            "combined.noise_duration_s": c.noise_duration.value(),
            "combined.noise_block": int(c.noise_block.value()),
            "combined.window": c.window_box.currentText(),
            "combined.segment_s": c.segment.value(),
            "combined.i_channel": c.i.currentData(),
            "combined.q_channel": c.q.currentData(),
            "combined.resonator_name": c.resonator.text(),
            "combined.temperature_mk": c.temperature.value(),
            "combined.fit_power_dbm": c.fit_power.value(),
            "combined.calibration_file": c.calibration_file.text(),
            "combined.folder": c.folder.text(),
        })

    q = getattr(window, "iq", None)
    if q is not None:
        out.update({
            "iq.mode": q.mode.currentText(),
            "iq.frequency_ghz": q.frequency.value(),
            "iq.start_frequency_ghz": q.start_frequency.value(),
            "iq.stop_frequency_ghz": q.stop_frequency.value(),
            "iq.frequency_points": int(q.frequency_points.value()),
            "iq.e8257d_power_dbm": q.e8257d_power.value(),
            "iq.p5002a_power_dbm": q.p5002a_power.value(),
            "iq.sample_count": int(q.sample_count.value()),
            "iq.settling_time_s": q.settling_time.value(),
            "iq.i_channel": q.i_channel.currentData(),
            "iq.q_channel": q.q_channel.currentData(),
            "iq.folder": q.folder.text(),
        })

    a = getattr(window, "auto_tab", None)
    if a is not None:
        out.update({
            "auto.radio_actual": a.radio_actual.isChecked(),
            "auto.temperature_k": a.temperature.value(),
            "auto.drive_temp": a.chk_drive_temp.isChecked(),
            "auto.laser_sweep": a.chk_laser_sweep.isChecked(),
            "auto.power_text": a.power_text.text(),
            "auto.laser_wavelength_nm": a.wavelength.value(),
            "auto.data_process_dir": a.data_process_dir.text(),
            "auto.radio_all": a.radio_all.isChecked(),
            "auto.custom_res": a.custom_res.text(),
            "auto.wide_mode": a.wide_mode.currentData(),
            "auto.wide_mhz": a.wide_mhz.value(),
            "auto.fine_table": a.fine_table.toPlainText(),
            "auto.save_root": a.save_root.text(),
            "auto.experiment_name": a.experiment_name.text(),
            "auto.checkpoint_path": a.checkpoint_path.text(),
            "auto.save_figs": a.chk_save_figs.isChecked(),
            "auto.skip_noise": a.chk_skip_noise.isChecked(),
            "auto.dryrun": a.chk_dryrun.isChecked(),
            "auto.force": a.chk_force.isChecked(),
        })
        chip_id, run_id = a._current_calibration()
        if chip_id:
            out["auto.calibration_chip_id"] = chip_id
            out["auto.calibration_run_id"] = run_id

    it = getattr(window, "instruments_tab", None)
    if it is not None:
        out.update({
            "instr.lakeshore_address": it._ls_address.text(),
            "instr.lakeshore_setpoint_k": it._ls_setpoint.value(),
            "instr.laser_address": it._laser_address.text(),
            "instr.laser_power_mw": it._laser_power.value(),
            "instr.laser_wavelength_nm": it._laser_wavelength.value(),
        })

    m = getattr(window, "manager", None)
    if m is not None:
        d = m.daq_config
        out["daq.device_name"] = d.get("device_name")
        out["daq.channels"] = list(d.get("channels") or [])
        out["daq.sample_rate"] = float(d.get("sample_rate", 0.0))
        out["daq.voltage_range"] = float(d.get("voltage_range", 0.0))
        out["daq.coupling"] = d.get("coupling")
        out["daq.trigger_mode"] = d.get("trigger_mode")
        out["daq.trigger_source"] = d.get("trigger_source")
        out["daq.trigger_edge"] = d.get("trigger_edge")

    return out


# =========================================================================
# 把配置 dict 写回控件
# =========================================================================

def apply_all(window, settings):
    """把上次使用值叠加到控件（键缺失即跳过；全程屏蔽信号防递归保存）。

    首次运行 settings={} 时安全（no-op）；重置时传启动快照。
    """
    if not isinstance(settings, dict):
        return

    s = getattr(window, "s21", None)
    if s is not None:
        _block(s.center_f, lambda: _apply_spin(s.center_f, "s21.center_f_ghz", settings, float))
        _block(s.bandwidth_mhz, lambda: _apply_spin(s.bandwidth_mhz, "s21.bandwidth_mhz", settings, float))
        _block(s.points, lambda: _apply_spin(s.points, "s21.points", settings, int))
        _block(s.power, lambda: _apply_spin(s.power, "s21.power_dbm", settings, float))
        _block(s.settle, lambda: _apply_spin(s.settle, "s21.settle_s", settings, float))
        _block(s.samples, lambda: _apply_spin(s.samples, "s21.samples", settings, int))
        _block(s.i, lambda: _apply_combo(s.i, "s21.i_channel", settings))
        _block(s.q, lambda: _apply_combo(s.q, "s21.q_channel", settings))
        _block(s.fit_enabled, lambda: _apply_check(s.fit_enabled, "s21.fit_enabled", settings))
        _block(s.resonator_name, lambda: _apply_text(s.resonator_name, "s21.resonator_name", settings))
        _block(s.temperature, lambda: _apply_spin(s.temperature, "s21.temperature_mk", settings, float))
        _block(s.fit_power, lambda: _apply_spin(s.fit_power, "s21.fit_power_dbm", settings, float))
        _block(s.folder, lambda: _apply_text(s.folder, "s21.folder", settings))
        _block(s.calibration_file, lambda: _apply_text(s.calibration_file, "s21.calibration_file", settings))

    n = getattr(window, "noise", None)
    if n is not None:
        _block(n.frequency, lambda: _apply_spin(n.frequency, "noise.frequency_ghz", settings, float))
        _block(n.frequency_mode, lambda: _apply_combo(n.frequency_mode, "noise.frequency_mode", settings))
        _block(n.power, lambda: _apply_spin(n.power, "noise.power_dbm", settings, float))
        _block(n.settle, lambda: _apply_spin(n.settle, "noise.settle_s", settings, float))
        _block(n.mode, lambda: _apply_combo(n.mode, "noise.mode", settings))
        _block(n.duration, lambda: _apply_spin(n.duration, "noise.duration_s", settings, float))
        _block(n.block, lambda: _apply_spin(n.block, "noise.block", settings, int))
        _block(n.i, lambda: _apply_combo(n.i, "noise.i_channel", settings))
        _block(n.q, lambda: _apply_combo(n.q, "noise.q_channel", settings))
        _block(n.psd_window, lambda: _apply_combo(n.psd_window, "noise.psd_window", settings))
        _block(n.segment_seconds, lambda: _apply_spin(n.segment_seconds, "noise.segment_seconds", settings, float))
        _block(n.iq_plot_points, lambda: _apply_spin(n.iq_plot_points, "noise.iq_plot_points", settings, int))
        _block(n.folder, lambda: _apply_text(n.folder, "noise.folder", settings))
        _block(n.calibration_file, lambda: _apply_text(n.calibration_file, "noise.calibration_file", settings))
        _block(n.s21_file, lambda: _apply_text(n.s21_file, "noise.s21_file", settings))

    c = getattr(window, "combined", None)
    if c is not None:
        _block(c.center, lambda: _apply_spin(c.center, "combined.center_ghz", settings, float))
        _block(c.bandwidth, lambda: _apply_spin(c.bandwidth, "combined.bandwidth_mhz", settings, float))
        _block(c.points, lambda: _apply_spin(c.points, "combined.points", settings, int))
        _block(c.samples, lambda: _apply_spin(c.samples, "combined.samples", settings, int))
        _block(c.power, lambda: _apply_spin(c.power, "combined.power_dbm", settings, float))
        _block(c.settle, lambda: _apply_spin(c.settle, "combined.settle_s", settings, float))
        _block(c.noise_location, lambda: _apply_combo(c.noise_location, "combined.noise_location", settings))
        _block(c.noise_duration, lambda: _apply_spin(c.noise_duration, "combined.noise_duration_s", settings, float))
        _block(c.noise_block, lambda: _apply_spin(c.noise_block, "combined.noise_block", settings, int))
        _block(c.window_box, lambda: _apply_combo(c.window_box, "combined.window", settings))
        _block(c.segment, lambda: _apply_spin(c.segment, "combined.segment_s", settings, float))
        _block(c.i, lambda: _apply_combo(c.i, "combined.i_channel", settings))
        _block(c.q, lambda: _apply_combo(c.q, "combined.q_channel", settings))
        _block(c.resonator, lambda: _apply_text(c.resonator, "combined.resonator_name", settings))
        _block(c.temperature, lambda: _apply_spin(c.temperature, "combined.temperature_mk", settings, float))
        _block(c.fit_power, lambda: _apply_spin(c.fit_power, "combined.fit_power_dbm", settings, float))
        _block(c.calibration_file, lambda: _apply_text(c.calibration_file, "combined.calibration_file", settings))
        _block(c.folder, lambda: _apply_text(c.folder, "combined.folder", settings))

    q = getattr(window, "iq", None)
    if q is not None:
        _block(q.mode, lambda: _apply_combo(q.mode, "iq.mode", settings))
        _block(q.frequency, lambda: _apply_spin(q.frequency, "iq.frequency_ghz", settings, float))
        _block(q.start_frequency, lambda: _apply_spin(q.start_frequency, "iq.start_frequency_ghz", settings, float))
        _block(q.stop_frequency, lambda: _apply_spin(q.stop_frequency, "iq.stop_frequency_ghz", settings, float))
        _block(q.frequency_points, lambda: _apply_spin(q.frequency_points, "iq.frequency_points", settings, int))
        _block(q.e8257d_power, lambda: _apply_spin(q.e8257d_power, "iq.e8257d_power_dbm", settings, float))
        _block(q.p5002a_power, lambda: _apply_spin(q.p5002a_power, "iq.p5002a_power_dbm", settings, float))
        _block(q.sample_count, lambda: _apply_spin(q.sample_count, "iq.sample_count", settings, int))
        _block(q.settling_time, lambda: _apply_spin(q.settling_time, "iq.settling_time_s", settings, float))
        _block(q.i_channel, lambda: _apply_combo(q.i_channel, "iq.i_channel", settings))
        _block(q.q_channel, lambda: _apply_combo(q.q_channel, "iq.q_channel", settings))
        _block(q.folder, lambda: _apply_text(q.folder, "iq.folder", settings))

    it = getattr(window, "instruments_tab", None)
    if it is not None:
        _block(it._ls_address, lambda: _apply_text(it._ls_address, "instr.lakeshore_address", settings))
        _block(it._ls_setpoint, lambda: _apply_spin(it._ls_setpoint, "instr.lakeshore_setpoint_k", settings, float))
        _block(it._laser_address, lambda: _apply_text(it._laser_address, "instr.laser_address", settings))
        _block(it._laser_power, lambda: _apply_spin(it._laser_power, "instr.laser_power_mw", settings, float))
        _block(it._laser_wavelength, lambda: _apply_spin(it._laser_wavelength, "instr.laser_wavelength_nm", settings, float))

    _apply_auto_tab(window, settings)
    _apply_daq(window, settings)
    _post_apply(window)


def _apply_auto_tab(window, settings):
    """自动化 Tab：标定下拉顺序敏感（目录 → 重建 → 匹配 chip/run）。"""
    a = getattr(window, "auto_tab", None)
    if a is None:
        return
    _block(a.temperature, lambda: _apply_spin(a.temperature, "auto.temperature_k", settings, float))
    if "auto.radio_actual" in settings:
        _set_radio(a.radio_actual, a.radio_manual, settings["auto.radio_actual"])
    _block(a.chk_drive_temp, lambda: _apply_check(a.chk_drive_temp, "auto.drive_temp", settings))
    _block(a.chk_laser_sweep, lambda: _apply_check(a.chk_laser_sweep, "auto.laser_sweep", settings))
    _block(a.power_text, lambda: _apply_text(a.power_text, "auto.power_text", settings))
    _block(a.wavelength, lambda: _apply_spin(a.wavelength, "auto.laser_wavelength_nm", settings, float))
    if "auto.radio_all" in settings:
        _set_radio(a.radio_all, a.radio_custom, settings["auto.radio_all"])
    _block(a.custom_res, lambda: _apply_text(a.custom_res, "auto.custom_res", settings))
    _block(a.wide_mode, lambda: _apply_combo(a.wide_mode, "auto.wide_mode", settings))
    _block(a.wide_mhz, lambda: _apply_spin(a.wide_mhz, "auto.wide_mhz", settings, float))
    _block(a.fine_table, lambda: _apply_plain(a.fine_table, "auto.fine_table", settings))
    _block(a.save_root, lambda: _apply_text(a.save_root, "auto.save_root", settings))
    _block(a.experiment_name, lambda: _apply_text(a.experiment_name, "auto.experiment_name", settings))
    _block(a.checkpoint_path, lambda: _apply_text(a.checkpoint_path, "auto.checkpoint_path", settings))
    _block(a.chk_save_figs, lambda: _apply_check(a.chk_save_figs, "auto.save_figs", settings))
    _block(a.chk_skip_noise, lambda: _apply_check(a.chk_skip_noise, "auto.skip_noise", settings))
    _block(a.chk_dryrun, lambda: _apply_check(a.chk_dryrun, "auto.dryrun", settings))
    _block(a.chk_force, lambda: _apply_check(a.chk_force, "auto.force", settings))

    def _set_dir():
        if "auto.data_process_dir" in settings:
            a.data_process_dir.setText(str(settings["auto.data_process_dir"]))
    _block(a.data_process_dir, _set_dir)

    chip = settings.get("auto.calibration_chip_id")
    run = settings.get("auto.calibration_run_id")

    def _rebuild_and_select():
        a.refresh_calibrations()
        if chip and run:
            for i, meta in enumerate(a._calibration_meta):
                if meta["chip_id"] == chip and meta["run_id"] == run:
                    a.calibration_combo.setCurrentIndex(i)
                    break
    _block(a.calibration_combo, _rebuild_and_select)


def _apply_daq(window, settings):
    """DAQ 配置：就地更新 dict（不替换对象，PXIeDialog.apply 会整体替换），
    并同步 PXIe 对话框控件，使打开对话框即显示恢复值。"""
    m = getattr(window, "manager", None)
    if m is None:
        return
    d = m.daq_config
    fields = {
        "device_name": "daq.device_name",
        "channels": "daq.channels",
        "sample_rate": "daq.sample_rate",
        "voltage_range": "daq.voltage_range",
        "coupling": "daq.coupling",
        "trigger_mode": "daq.trigger_mode",
        "trigger_source": "daq.trigger_source",
        "trigger_edge": "daq.trigger_edge",
    }
    for field, key in fields.items():
        if key not in settings:
            continue
        if field == "channels":
            d[field] = [int(x) for x in settings[key]]
        else:
            d[field] = settings[key]

    dlg = getattr(window, "daq_dialog", None)
    if dlg is None or not hasattr(dlg, "device"):
        return
    _block(dlg.device, lambda: dlg.device.setText(str(d["device_name"])))
    _block(dlg.rate, lambda: dlg.rate.setValue(float(d["sample_rate"])))
    _block(dlg.voltage, lambda: _set_combo(dlg.voltage, float(d["voltage_range"])))
    _block(dlg.coupling, lambda: _set_combo(dlg.coupling, d["coupling"]))
    _block(dlg.trigger, lambda: dlg.trigger.setCurrentIndex(
        1 if d["trigger_mode"] == "DIGITAL" else 0))
    _block(dlg.source, lambda: dlg.source.setText(d["trigger_source"] or "/PXI2Slot2/PFI0"))
    _block(dlg.edge, lambda: _set_combo(dlg.edge, d["trigger_edge"]))
    for i, cb in enumerate(dlg.channels):
        _block(cb, lambda i=i, cb=cb: cb.setChecked(i in d["channels"]))


def _post_apply(window):
    """恢复被 blockSignals 压制的副作用使能态。

    故意不调 noise.update_selected_frequency()：它会用 s21_file 重算并覆盖
    恢复的 noise.frequency_ghz，破坏"原样恢复"语义。
    """
    q = getattr(window, "iq", None)
    if q is not None and hasattr(q, "update_mode"):
        q.update_mode()
    n = getattr(window, "noise", None)
    if n is not None:
        n.frequency.setEnabled(n.frequency_mode.currentData() == "MANUAL")
        n.duration.setEnabled(n.mode.currentIndex() == 0)
    a = getattr(window, "auto_tab", None)
    if a is not None:
        a.refresh_n_modes()
        a.refresh_availability()


# =========================================================================
# 保存 / 重置 / 信号接线
# =========================================================================

def save_now(window):
    """收集全部控件并写 gui_user_settings.json（仅 GUI 线程）。"""
    gui_config.save_user_settings(collect_all(window))


def reset_to_defaults(window):
    """删除叠加层文件并回放启动时的默认快照。"""
    try:
        gui_config.USER_SETTINGS_PATH.unlink()
    except OSError:
        pass
    default_state = getattr(window, "_default_widget_state", {})
    apply_all(window, default_state)


_CHANGE_SIGNALS = {
    QDoubleSpinBox: "valueChanged",
    QSpinBox: "valueChanged",
    QComboBox: "currentIndexChanged",
    QCheckBox: "toggled",
    QRadioButton: "toggled",
    QLineEdit: "textChanged",
    QPlainTextEdit: "textChanged",
}


def _connect_change(widget, on_change):
    for cls, sig in _CHANGE_SIGNALS.items():
        if isinstance(widget, cls):
            getattr(widget, sig).connect(on_change)
            return True
    return False


def wire_auto_save(window, on_change):
    """把所有持久化控件的变更信号接到防抖槽（on_change）。"""
    for tab_attr, names in (
        ("s21", ("center_f", "bandwidth_mhz", "points", "power", "settle", "samples",
                 "i", "q", "fit_enabled", "resonator_name", "temperature",
                 "fit_power", "folder", "calibration_file")),
        ("noise", ("frequency", "frequency_mode", "power", "settle", "mode", "duration",
                   "block", "i", "q", "psd_window", "segment_seconds", "iq_plot_points",
                   "folder", "calibration_file", "s21_file")),
        ("combined", ("center", "bandwidth", "points", "samples", "power", "settle",
                      "noise_location", "noise_duration", "noise_block", "window_box",
                      "segment", "i", "q", "resonator", "temperature", "fit_power",
                      "calibration_file", "folder")),
        ("iq", ("mode", "frequency", "start_frequency", "stop_frequency",
                "frequency_points", "e8257d_power", "p5002a_power", "sample_count",
                "settling_time", "i_channel", "q_channel", "folder")),
    ):
        tab = getattr(window, tab_attr, None)
        if tab is None:
            continue
        for name in names:
            w = getattr(tab, name, None)
            if w is not None:
                _connect_change(w, on_change)

    a = getattr(window, "auto_tab", None)
    if a is not None:
        for name in ("radio_manual", "radio_actual", "temperature", "chk_drive_temp",
                     "chk_laser_sweep", "power_text", "wavelength", "data_process_dir",
                     "calibration_combo", "radio_all", "radio_custom", "custom_res",
                     "wide_mode", "wide_mhz", "fine_table", "save_root",
                     "experiment_name", "checkpoint_path", "chk_save_figs",
                     "chk_skip_noise", "chk_dryrun", "chk_force"):
            w = getattr(a, name, None)
            if w is not None:
                _connect_change(w, on_change)

    it = getattr(window, "instruments_tab", None)
    if it is not None:
        for name in ("_ls_address", "_ls_setpoint", "_laser_address",
                     "_laser_power", "_laser_wavelength"):
            w = getattr(it, name, None)
            if w is not None:
                _connect_change(w, on_change)
