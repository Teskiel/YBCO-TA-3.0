# -*- coding: utf-8 -*-
"""自动化测量编排 worker（QThread）。

在后台线程跑 orchestrator_noisesweep.run_one_point 的编排循环，顺序为
用户要求的 温度(一期单值) → 激光功率 → 谐振（最内）：
    4K: res1@0mW, res2@0mW, res1@9mW, res2@9mW, ...

所有触达 GUI 的输出一律经 pyqtSignal（queued）发射，worker 线程绝不直接
碰 Qt 控件。

关键约束：
  * 测量统一走面板代码：测量引擎是 PanelMeasure（实例化 V3 面板的
    MeasurementWorker），core 在自动化路径禁用；绝不用 backends.make_source
    开第二条 E8257D（复用 manager 的 source/DAQ 会话）。mock 时把 mock 后端
    临时注入 manager 让面板引擎也能真跑，结束后 _restore_manager 恢复。
  * config["save_figures"] 强制 False —— run_one_point 内部 _save_figures/
    _save_noise_figures（headless）不跑；图统一由 GUI 线程对 V3 面板 canvas
    savefig 存到 <目标T>K/PIC/{类型}/（save_gui_figures 控制，见 auto_tab）。
"""

import logging
import threading
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal

import resonance_table as rt
from backends import (
    make_daq_factory,
    make_laser_backend,
    make_source,
    make_temperature_backend,
)
from orchestrator_noisesweep import (
    Checkpoint,
    fmt_K,
    fmt_mW,
    resolve_temperature,
    run_one_point,
    _s21_panel_p,
    _noise_panel_p,
)


class _SignalLogHandler(logging.Handler):
    """把 logging 记录桥到 GUI 的 log_line 信号。"""

    def __init__(self, signal_emit):
        super().__init__()
        self._emit = signal_emit

    def emit(self, record):
        try:
            self._emit(self.format(record))
        except Exception:
            pass


class _ManualReferenceTable:
    """追踪表缺失时的回退桩：仅手动频率 / 清晰报错，禁用激光频移预测。

    只实现 run_one_point._resolve_reference 用到的两个方法。
    """

    def __init__(self, config):
        self._cfg = config

    def resonator_names(self):
        return [r["name"] for r in self._cfg.get("resonators", [])]

    @property
    def n_res(self):
        return len(self._cfg.get("resonators", []))

    def resonator_index(self, name):
        names = self.resonator_names()
        if name in names:
            return names.index(name)
        s = str(name).strip().lower().replace("resonator", "").replace("res", "").replace("r", "")
        if s.isdigit():
            idx = int(s) - 1
            if 0 <= idx < len(names):
                return idx
        raise ValueError("无法识别谐振器名称: {!r}".format(name))

    def reference_frequency_hz(self, target_k, res_idx, manual_hz=None):
        per = self._cfg.get("resonators", [])[res_idx].get("reference_frequency_hz")
        glob = self._cfg.get("manual_reference_frequency_hz")
        if per is not None:
            return float(per)
        if glob is not None:
            return float(glob)
        raise ValueError(
            "缺少谐振追踪表且未提供手动参考频率（res%d）" % (res_idx + 1))

    def responsivity_ppm_per_mw(self, target_k, res_idx):
        return None  # 无追踪表 → 停用激光频移预测


class AutomationWorker(QThread):
    """单次自动化运行的编排线程。

    运行结束/出错后本实例可丢弃；重新运行请新建实例。
    """

    progress = pyqtSignal(int, int, str)      # (done, total, label)
    stage = pyqtSignal(str)
    log_line = pyqtSignal(str)
    s21_point = pyqtSignal(object, object, object, int, int)   # → V3 S21Tab.plot
    s21_fit_ready = pyqtSignal(object)                          # → V3 S21Tab.show_fit
    s21_fit_failed = pyqtSignal(str)
    noise_block = pyqtSignal(object, object, object, float)     # → V3 NoiseTab.plot
    noise_reset = pyqtSignal()                  # → NoiseTab.reset()（每段噪声前清空画布）
    save_figure = pyqtSignal(object)            # meta dict → GUI 线程按 PIC 树 savefig
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, window, config, dry_run=False, parent=None):
        super().__init__(parent)
        self.window = window
        self.manager = window.manager
        self.config = config
        self.dry_run = bool(dry_run)
        self.stop_event = threading.Event()
        # 图保存上下文（每点重置）
        self._experiment_root = None      # <save_root>/<experiment>
        self._cur = None                  # 当前点 {res_name,res_index,power_mw,target_k,actual_k,chip_id}
        self._fit_seq = 0
        self._save_gui_figures = True
        self._saved_mgr = None            # mock 注入前的 manager 状态（结束后恢复）

    def stop(self):
        self.stop_event.set()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _make_logger(self):
        logger = logging.getLogger("personal_gui.automation")
        logger.setLevel(logging.INFO)
        logger.handlers = []
        logger.propagate = False
        handler = _SignalLogHandler(self.log_line.emit)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logger.addHandler(handler)
        return logger

    def _check_no_conflict(self):
        w = self.window
        for label, tab in (("S21", getattr(w, "s21", None)),
                           ("噪声", getattr(w, "noise", None)),
                           ("IQ校准", getattr(w, "iq", None))):
            worker = getattr(tab, "worker", None)
            if worker is not None and worker.isRunning():
                raise RuntimeError("{} 测量正在运行，请先停止再启动自动化。".format(label))
        if w.daq_dialog.preview_worker and w.daq_dialog.preview_worker.isRunning():
            raise RuntimeError("PXIe-4480 电压预览正在运行，请先停止。")

    def _build_env(self, cfg, log):
        """构造 (source, temp, laser, measure, mock_mod)。测量引擎统一走面板 MeasurementWorker（core 禁用）。"""
        if cfg["backend"] == "mock":
            cfg["device_name"] = cfg.get("pxie_device_name")
            import mock_instruments
            mock_mod = mock_instruments
            # mock 需要 identity IQ 校准（真实校准参数会歪曲合成数据）
            cal = self._ensure_mock_calibration()
            cfg["iq_calibration_file"] = cal
            source = make_source("mock", cfg, log)
            temp = make_temperature_backend("mock", cfg, log)
            laser = make_laser_backend("mock", cfg, log)
            make_daq = make_daq_factory("mock", cfg, source, log)
            # 把 mock 后端临时注入 manager，使面板 MeasurementWorker 在 mock 下也真跑
            m = self.manager
            self._saved_mgr = (
                getattr(m, "source", None), getattr(m, "make_daq", None),
                getattr(m, "daq_config", None), getattr(m, "daq_connected", None),
                getattr(m, "source_lock", None), getattr(m, "daq_lock", None),
                getattr(m, "temp", None), getattr(m, "laser", None),
            )
            m.source = source
            m.make_daq = make_daq
            m.daq_config = cfg
            try:
                m.source_connected = True      # 普通属性（如 dry-run 桩）可设；真实 property 由 source 派生
            except AttributeError:
                pass
            m.daq_connected = True
            m.source_lock = threading.RLock()
            m.daq_lock = threading.RLock()
            m.temp = temp
            m.laser = laser
            measure = PanelMeasure(m, self, self.stop_event)
            return source, temp, laser, measure, mock_mod

        # 真实路径：温控/激光可能未在「仪器与连接」页连接（manager.temp/laser 为
        # None）——不能裸传 None，否则 run_one_point 里 temp.get_temperature() 崩
        # 溃（mock 路径自己造后端所以从没暴露，这是 mock/真实行为分叉点）。
        # 地址为空时工厂回退 FixedTemperature/NullLaser，与 orchestrator._build_env
        # 语义一致；连接失败会 raise 并提示，不会静默。
        m = self.manager
        temp = m.temp
        if temp is None:
            temp = make_temperature_backend(cfg["backend"], cfg, log)
        laser = m.laser
        if laser is None:
            laser = make_laser_backend(cfg["backend"], cfg, log)
        measure = PanelMeasure(m, self, self.stop_event)
        return m.source, temp, laser, measure, None

    def _restore_manager(self):
        """恢复 mock 注入前的 manager 状态（真机路径 no-op）。

        source_connected 是只读 property（由 m.source 派生），恢复 source 即自动恢复，
        不在元组里赋值。
        """
        s = self._saved_mgr
        if s is None:
            return
        m = self.manager
        (m.source, m.make_daq, m.daq_config, m.daq_connected,
         m.source_lock, m.daq_lock, m.temp, m.laser) = s
        self._saved_mgr = None

    @staticmethod
    def _ensure_mock_calibration():
        """生成 identity IQ 校准文件（mock 专用），返回其路径。"""
        from gui_config import SCRIPT_DIR
        cal = SCRIPT_DIR / "data" / "IQ_calibration" / "_dryrun_identity_cal.txt"
        if not cal.exists():
            cal.parent.mkdir(parents=True, exist_ok=True)
            import mock_instruments
            mock_instruments.write_identity_iq_calibration(str(cal))
        return str(cal)

    def _on_fit_ready(self, fit):
        self.s21_fit_ready.emit(fit)
        if self._experiment_root and self._save_gui_figures and self._cur:
            self._fit_seq += 1
            fine = self._fit_seq > 1
            meta = dict(self._cur)
            meta["save_root"] = self._experiment_root
            meta["group"] = "s21_fine" if fine else "s21_coarse"
            meta["type"] = "fine_s21" if fine else "coarse_s21"
            meta["mode"] = ""
            self.save_figure.emit(meta)

    def _load_table(self, cfg, log):
        tracking = cfg.get("tracking_file")
        if tracking:
            try:
                table = rt.load_resonance_table(tracking)
                log.info("谐振追踪表: %s (%s)", tracking, table)
                return table
            except Exception as exc:
                log.warning("追踪表加载失败 %s，回退手动频率：%s", tracking, exc)
        return _ManualReferenceTable(cfg)

    def _resolve_temperature(self, cfg, temp, log):
        """一期单温度。返回 (T_target, actual_T, stable)。

        委托 orchestrator_noisesweep.resolve_temperature：
          * 未连接 LakeShore（is_fixed，含 mock）→ 手动输入值同时作为目标/实测；
          * 已连接 → 实测优先，|目标−实测| > temperature_mismatch_limit_k
            抛 TemperatureMismatch（worker.failed → GUI 弹窗中止）。
        """
        T = float(cfg.get("fixed_temperature_k", 77.0))
        return resolve_temperature(T, cfg, temp, log)

    def _resolve_laser_powers(self, cfg, laser, log):
        powers = [float(p) for p in cfg.get("laser_power_mw", [])]
        if not powers:
            raise RuntimeError("激光功率列表为空。")
        if getattr(laser, "is_null", False) and len(powers) > 1:
            log.warning("激光未连接：仅用第一个功率 %.1f mW，其余忽略。", powers[0])
            powers = [powers[0]]
        return powers

    def _select_resonators(self, cfg):
        resonators = list(cfg.get("resonators", []))
        sel = cfg.get("resonator_selection", "all")
        if sel == "all" or not sel:
            return resonators
        indices = sorted(set(int(i) for i in sel))
        if any(i < 0 or i >= len(resonators) for i in indices):
            raise RuntimeError("自定义谐振器编号越界：%s" % indices)
        return [resonators[i] for i in indices]

    def _res_idx(self, table, res):
        if table is not None:
            try:
                return table.resonator_index(res["name"])
            except Exception:
                pass
        digits = "".join(ch for ch in str(res["name"]) if ch.isdigit())
        try:
            return int(digits) - 1
        except Exception:
            raise RuntimeError("无法识别谐振器名 %s（缺少追踪表）" % res["name"])

    @staticmethod
    def _auto_experiment_name(cfg):
        temps = sorted(float(t) for t in cfg.get("temperature_list_k", [77.0]))
        powers = sorted(float(p) for p in cfg.get("laser_power_mw", [0.0]))
        def _span(vals):
            if len(vals) == 1:
                return "{:g}".format(vals[0])
            return "{:g}-{:g}".format(vals[0], vals[-1])
        return "{}K&{}mW".format(_span(temps), _span(powers))

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self):
        log = self._make_logger()
        cfg = dict(self.config)
        if self.dry_run:
            cfg["backend"] = "mock"
            log.info("== 空跑模式（backend=mock），不接触硬件 ==")
        try:
            self._check_no_conflict()
            # 关键：禁用 run_one_point 内部的 matplotlib Agg 出图（与 Qt 冲突），
            # 图改由 GUI 线程对 V3 面板 canvas savefig（save_gui_figures 控制）。
            cfg["save_figures"] = False
            self._save_gui_figures = bool(cfg.get("save_gui_figures", True))

            source, temp, laser, measure, mock_mod = self._build_env(cfg, log)
            if cfg["backend"] != "mock":
                if not self.manager.source_connected:
                    raise RuntimeError("E8257D 未连接，请先在「仪器与连接」页连接。")
                if not self.manager.daq_connected:
                    raise RuntimeError("PXIe-4480 未连接，请先在「仪器与连接」页连接。")

            table = self._load_table(cfg, log)
            experiment = cfg.get("experiment_name") or self._auto_experiment_name(cfg)
            save_root = Path(cfg["save_root"]) / experiment
            save_root.mkdir(parents=True, exist_ok=True)
            self._experiment_root = str(save_root)
            checkpoint = Checkpoint(
                cfg.get("checkpoint_path") or str(save_root / "checkpoint.json"))

            T, actual_T, stable = self._resolve_temperature(cfg, temp, log)
            checkpoint.mark_temperature(T, actual_T, stable)
            powers = self._resolve_laser_powers(cfg, laser, log)
            resonators = self._select_resonators(cfg)
            total = len(powers) * len(resonators)
            done = 0

            log.info("保存根目录: %s", save_root)
            log.info("checkpoint: %s", checkpoint.path)
            log.info("计划: 温度 %.2f K（实际 %.4f）× %d 功率 × %d 谐振 = %d 点",
                     T, actual_T, len(powers), len(resonators), total)
            self.stage.emit("运行中")

            for power_mw in powers:                    # ★ power 外层
                for res in resonators:                 # res 内层
                    if self.stop_event.is_set():
                        break
                    done += 1
                    res_idx = self._res_idx(table, res)
                    label = "{} @ {} mW @ {:.1f} K".format(res["name"], power_mw, actual_T)
                    self.progress.emit(done, total, label)
                    log.info("=== 点 %d/%d: %s ===", done, total, label)

                    # 图片保存上下文（本点）
                    self._cur = {
                        "res_name": res["name"],
                        "res_index": res_idx,
                        "power_mw": power_mw,
                        "target_k": T,
                        "actual_k": actual_T,
                        "chip_id": str(cfg.get("chip_id", "")),
                    }
                    self._fit_seq = 0

                    run_one_point(
                        cfg, measure, source, laser, table, checkpoint,
                        self.stop_event, T, actual_T, res, res_idx, power_mw,
                        save_root, force=bool(cfg.get("force", False)),
                        skip_noise=bool(cfg.get("skip_noise", False)),
                        mock_instruments=mock_mod, log=log)

            if getattr(laser, "is_null", False) is False:
                try:
                    laser.set_power(0)
                except Exception:
                    pass
            log.info("=== 自动化完成 ===")
            self.completed.emit(str(save_root))
        except Exception as exc:
            log.error("运行异常中止: %s", exc)
            self.failed.emit("{}: {}".format(type(exc).__name__, exc))
        finally:
            self._restore_manager()


class _S21Result:
    """PanelMeasure 的 S21 结果契约（与 core.S21ScanResult 同构，供 run_one_point 用）。"""

    def __init__(self, resonance_frequency_hz=None, fit_error=None, stopped=False):
        self.resonance_frequency_hz = resonance_frequency_hz
        self.fit_error = fit_error
        self.fit_ok = resonance_frequency_hz is not None
        self.stopped = bool(stopped)


class PanelMeasure:
    """测量引擎：实例化面板的 MeasurementWorker（core 在自动化路径禁用）。

    每次测量创建 MeasurementWorker(self.manager, kind, 面板式 p dict, path)，
    桥接其 s21_point/s21_fit_ready/s21_fit_failed/noise_block 到 host worker 的
    对应信号（→ GUI 面板 plot/show_fit），等待 done/error，支持停止。
    噪声段：测量前发 noise_reset（清空面板画布），完成后发 save_figure(meta)
    （GUI 线程存到 <目标T>K/PIC/noise/）。
    """

    def __init__(self, manager, host, stop_event):
        self.manager = manager
        self.host = host          # AutomationWorker 实例（信号 + _cur + _experiment_root）
        self.stop_event = stop_event

    def _run_worker(self, worker):
        """启动 MeasurementWorker 并等待完成。

        所有 MW 信号用 DirectConnection（在 MW 线程内同步派发），因为本线程在
        wait() 阻塞期间不处理队列事件；host 信号再以 queued 转给 GUI 线程面板。
        """
        err = [None]
        worker.error.connect(lambda e: err.__setitem__(0, e), Qt.DirectConnection)
        worker.start()
        while worker.isRunning():
            worker.wait(50)
            if self.stop_event.is_set():
                worker.stop()
                worker.wait()
                break
        if err[0]:
            raise RuntimeError(err[0])
        return worker

    def s21(self, config, center_hz, bandwidth_hz, res_name, T_k, extra_attrs, path):
        from kid_measurement_gui_personal import MeasurementWorker
        p = _s21_panel_p(config, center_hz, bandwidth_hz, res_name, T_k, extra_attrs)
        w = MeasurementWorker(self.manager, "s21", p, str(path), rename_target=False)
        w.s21_point.connect(self.host.s21_point.emit, Qt.DirectConnection)
        w.s21_fit_ready.connect(self.host._on_fit_ready, Qt.DirectConnection)
        w.s21_fit_failed.connect(self.host.s21_fit_failed.emit, Qt.DirectConnection)
        box = []
        w.s21_fit_ready.connect(lambda fit: box.append(fit), Qt.DirectConnection)
        self._run_worker(w)
        resonance_frequency = None
        fit_error = None
        if box:
            fit = box[0]
            try:
                labels = [str(x) for x in getattr(fit, "labels", ())]
                vals = [float(v) for v in getattr(fit, "values", ())]
                d = dict(zip(labels, vals))
                resonance_frequency = float(d["f0"] + d["df"])
            except Exception as exc:
                fit_error = "{}: {}".format(type(exc).__name__, exc)
        else:
            fit_error = "no S21 fit emitted"
        return _S21Result(resonance_frequency_hz=resonance_frequency,
                          fit_error=fit_error, stopped=self.stop_event.is_set())

    def noise(self, config, frequency_mode, path, meta_base=None):
        from kid_measurement_gui_personal import MeasurementWorker
        p = _noise_panel_p(config, frequency_mode, path)
        self.host.noise_reset.emit()
        w = MeasurementWorker(self.manager, "noise", p, str(path))
        w.noise_block.connect(self.host.noise_block.emit, Qt.DirectConnection)
        self._run_worker(w)
        if meta_base and getattr(self.host, "_save_gui_figures", True):
            mode = str(frequency_mode)
            meta = dict(meta_base)
            meta["group"] = "noise"
            meta["mode"] = mode
            meta["type"] = ("noise" if mode.upper() == "F0_PLUS_DF"
                            else "noise_{}".format(mode.upper()))
            self.host.save_figure.emit(meta)
        return None
