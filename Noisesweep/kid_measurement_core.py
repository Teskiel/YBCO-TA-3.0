# -*- coding: utf-8 -*-
"""KID 测量共享核心：headless 的 S21 扫描与噪声采集。

kid_measurement_gui_v3.MeasurementWorker 与 noisesweep 共用这里的
run_s21 / run_noise，保证两条路径产出的 HDF5 schema 完全一致。

本模块禁止 import kid_measurement_gui_v3（那会连带 PyQt5，破坏 headless）。
算法逐字搬自 kid_measurement_gui_v3.MeasurementWorker.run_s21 / run_noise，
只做了三处机械替换：
  * self.progress/self.s21_point/... 等 Qt 信号  ->  ctx.on_* 回调
  * self.stop_requested                          ->  ctx.should_stop()
  * self.manager.source/source_lock/daq_config/daq_lock  ->  ctx.*
  * self.daq（worker 持有的实例）                ->  本函数内局部 daq（由 ctx.make_daq() 创建）
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import h5py
import numpy as np

from IQ_calibration import IQCalibrationTable
from S21_fitting import ScrapsS21Fitter, S21NoiseCalibration, welch_psd


def _noop(*args, **kwargs) -> None:
    pass


def _never() -> bool:
    return False


@dataclass
class MeasurementContext:
    """把「仪器会话 + 锁 + DAQ 工厂 + 回调」打包传给 run_s21/run_noise。

    GUI 侧：source = manager.source、锁 = manager 的 RLock、回调 = pyqtSignal.emit。
    noisesweep 侧：source = 自己连的 E8257D、锁 = threading.RLock()、回调默认 no-op。
    """

    source: object                     # E8257D（有 set_frequency_hz/set_power_dbm/rf_on/wait_until_complete/resource_name）
    source_lock: object                # RLock，读写 source 前必须持有
    daq_config: dict                   # device_name/channels/sample_rate/voltage_range/coupling/trigger_*
    daq_lock: object                   # RLock，读写 DAQ 前必须持有
    make_daq: Callable[[], object]     # 工厂：每次测量新建 PXIe4480（mock 后端换成 FakePXIe4480）

    # 可选回调，默认 no-op。GUI 把它们映射到 pyqtSignal；headless 可全部留空。
    on_file_created: Callable[[str], None] = _noop
    on_s21_point: Callable[[object, object, object, int, int], None] = _noop
    on_s21_fit_ready: Callable[[object], None] = _noop
    on_s21_fit_failed: Callable[[str], None] = _noop
    on_noise_block: Callable[[object, dict, object, float], None] = _noop
    on_source_configured: Callable[[float, float], None] = _noop  # GUI 用它同步 manager.source_status
    should_stop: Callable[[], bool] = _never                        # 协作式停止，默认永不停止


@dataclass
class S21Params:
    start_hz: float
    stop_hz: float
    center_hz: float
    bandwidth_hz: float
    points: int
    samples: int
    power_dbm: float
    settle_s: float
    i_channel: int
    q_channel: int
    calibration_file: str
    fit_enabled: bool = True
    resonator_name: str = "res0"
    temperature_k: float = 0.1
    readout_power_dbm: float = -60.0
    extra_attrs: dict = field(default_factory=dict)  # noisesweep 塞 laser_power_mw / temperature_k 等


@dataclass
class NoiseParams:
    s21_file: str
    frequency_mode: str
    manual_frequency_hz: float
    power_dbm: float
    settle_s: float
    continuous: bool
    duration_s: float
    block: int
    i_channel: int
    q_channel: int
    calibration_file: str
    window: str = "hamming"
    segment_seconds: float = 1.0


@dataclass
class S21ScanResult:
    path: str
    completed_points: int
    resonance_frequency_hz: Optional[float]     # f0+df；拟合成功才有
    fit_ok: bool
    fit_error: Optional[str]
    stopped: bool


@dataclass
class NoiseResult:
    s21_file: str
    group_name: str
    sample_count: int
    stopped: bool


def _configure_source(ctx, frequency_hz, power_dbm):
    """设置 E8257D 频率/功率并打开 RF、等仪器就绪。"""
    with ctx.source_lock:
        ctx.source.set_frequency_hz(frequency_hz)
        ctx.source.set_power_dbm(power_dbm)
        ctx.source.rf_on()
        ctx.source.wait_until_complete()
    ctx.on_source_configured(float(frequency_hz), float(power_dbm))


def _acquire(ctx, daq, **kwargs):
    c = ctx.daq_config
    with ctx.daq_lock:
        return daq.acquire(
            trigger_mode=c["trigger_mode"],
            trigger_source=c["trigger_source"],
            trigger_edge=c["trigger_edge"],
            timeout=120,
            **kwargs,
        )


def _metadata(ctx, h):
    c = ctx.daq_config
    h.attrs["created_at"] = datetime.now().isoformat(timespec="seconds")
    h.attrs["e8257d_visa_address"] = ctx.source.resource_name
    h.attrs["pxie_device_name"] = c["device_name"]
    h.attrs["requested_sample_rate_hz"] = c["sample_rate"]
    h.attrs["voltage_range_V"] = c["voltage_range"]
    h.attrs["coupling"] = c["coupling"]
    h.attrs["trigger_mode"] = c["trigger_mode"]


def run_s21(ctx: MeasurementContext, p: S21Params, path) -> S21ScanResult:
    """逐频点 S21 扫描 + 可选 SCRAPS 拟合，写单个 HDF5。

    与 GUI 的唯一差异：不做 s21_resonance_path 重命名（那是 GUI 的文件命名习惯，
    由 GUI 薄壳自己处理）；noisesweep 用固定文件名。
    """
    c = ctx.daq_config
    daq = ctx.make_daq()
    calibration_table = IQCalibrationTable.load(p.calibration_file)
    freq = np.linspace(p.start_hz, p.stop_hz, p.points)
    selected = c["channels"]
    ip = selected.index(p.i_channel)
    qp = selected.index(p.q_channel)
    resonance_frequency = None
    fit_error = None
    path = Path(path)

    with h5py.File(path, "w") as h:
        _metadata(ctx, h)
        h.attrs["measurement"] = "KID S21"
        h.attrs["resonator_name"] = p.resonator_name
        h.attrs["temperature_mK"] = p.temperature_k * 1000
        h.attrs["readout_power_dbm"] = p.readout_power_dbm
        h.attrs["center_frequency_hz"] = p.center_hz
        h.attrs["bandwidth_hz"] = p.bandwidth_hz
        h.attrs["power_dbm"] = p.power_dbm
        h.attrs["iq_calibration_file"] = calibration_table.source_path
        for key, value in p.extra_attrs.items():  # noisesweep 扩展维度；GUI 传空 dict → 产物不变
            h.attrs[key] = value
        h.create_dataset("frequency_hz", data=freq)
        dt = h5py.string_dtype("utf-8")
        h.create_dataset("channels", data=np.asarray(daq.channels, dtype=object), dtype=dt)
        raw = h.create_dataset(
            "raw_voltage_V", shape=(len(freq), len(selected), p.samples),
            dtype="f8", chunks=(1, len(selected), p.samples), compression="gzip",
        )
        mean = h.create_dataset("mean_voltage_V", shape=(len(freq), len(selected)), dtype="f8")
        cal = h.create_dataset(
            "calibrated_iq_voltage_V", shape=(len(freq), 2, p.samples),
            dtype="f8", chunks=(1, 2, p.samples), compression="gzip",
        )
        calmean = h.create_dataset("calibrated_mean_iq_V", shape=(len(freq), 2), dtype="f8")
        calparams = h.create_dataset("iq_calibration_parameters", shape=(len(freq), 5), dtype="f8")
        calparams.attrs["columns"] = "I0_V,Q0_V,A_I_V,A_Q_V,rotation_q_rad"
        mag = h.create_dataset("s21_magnitude_db", shape=(len(freq),), dtype="f8")
        phase = h.create_dataset("s21_phase_deg", shape=(len(freq),), dtype="f8")
        h.attrs["completed_points"] = 0
        ctx.on_file_created(str(path))

        for n, f in enumerate(freq):
            if ctx.should_stop():
                break
            _configure_source(ctx, f, p.power_dbm)
            time.sleep(p.settle_s)
            r = _acquire(ctx, daq, sample_count=p.samples)
            m = np.mean(r.data, axis=1)
            ic, qc, calp = calibration_table.transform(f, r.data[ip], r.data[qp])
            cm = np.array([np.mean(ic), np.mean(qc)])
            s = complex(cm[0], cm[1])
            raw[n] = r.data
            mean[n] = m
            cal[n] = np.vstack((ic, qc))
            calmean[n] = cm
            calparams[n] = [calp.i_offset, calp.q_offset,
                            calp.i_axis, calp.q_axis, calp.rotation_rad]
            mag[n] = 20*np.log10(max(abs(s), np.finfo(float).tiny))
            phase[n] = np.degrees(np.angle(s))
            h.attrs["actual_sample_rate_hz"] = r.actual_sample_rate
            h.attrs["completed_points"] = n + 1
            h.flush()
            ctx.on_s21_point(freq[:n+1].copy(), mag[:n+1], phase[:n+1], n+1, len(freq))

        completed = int(h.attrs["completed_points"])
        if p.fit_enabled and not ctx.should_stop() and completed == len(freq):
            try:
                fitter = ScrapsS21Fitter(p.resonator_name, p.temperature_k,
                                         p.readout_power_dbm)
                fit = fitter.fit(freq, calmean[:, 0], calmean[:, 1])
                group = h.create_group("scraps_fit")
                group.attrs["Qi"] = fit.Qi
                group.attrs["Qc"] = fit.Qc
                group.attrs["report"] = fit.report
                group.create_dataset("labels", data=np.asarray(fit.labels, dtype=object),
                                     dtype=h5py.string_dtype("utf-8"))
                group.create_dataset("values", data=fit.values)
                group.create_dataset("I", data=fit.I)
                group.create_dataset("Q", data=fit.Q)
                group.create_dataset("resultI", data=fit.resultI)
                group.create_dataset("resultQ", data=fit.resultQ)
                group.create_dataset("INorm", data=fit.INorm)
                group.create_dataset("QNorm", data=fit.QNorm)
                group.create_dataset("resultINorm", data=fit.resultINorm)
                group.create_dataset("resultQNorm", data=fit.resultQNorm)
                group.attrs["x0"] = float(fit.res.x0)
                group.attrs["y0"] = float(fit.res.y0)
                group.attrs["Ioffset"] = float(getattr(fit.res, "Ioffset", 0.0))
                group.attrs["Qoffset"] = float(getattr(fit.res, "Qoffset", 0.0))
                fit_parameters = dict(zip(fit.labels, fit.values))
                resonance_frequency = float(fit_parameters["f0"] + fit_parameters["df"])
                h.attrs["resonance_frequency_hz"] = resonance_frequency
                h.flush()
                ctx.on_s21_fit_ready(fit)
            except Exception as exc:
                fit_error = "{}: {}".format(type(exc).__name__, exc)
                h.attrs["scraps_fit_error"] = fit_error
                h.flush()
                ctx.on_s21_fit_failed(fit_error)
        h.attrs["stopped_by_user"] = ctx.should_stop()

    return S21ScanResult(
        path=str(path),
        completed_points=completed,
        resonance_frequency_hz=resonance_frequency,
        fit_ok=resonance_frequency is not None,
        fit_error=fit_error,
        stopped=bool(ctx.should_stop()),
    )


def run_noise(ctx: MeasurementContext, p: NoiseParams, s21_file) -> NoiseResult:
    """在指定 S21 HDF5 上追加一个 /noise_measurements/<时间戳> 噪声组。"""
    c = ctx.daq_config
    daq = ctx.make_daq()
    calibration_table = IQCalibrationTable.load(p.calibration_file)
    s21_path = Path(s21_file) if s21_file else Path(p.s21_file)
    noise_calibration = S21NoiseCalibration.load(s21_path)
    frequency, s21_index = noise_calibration.measurement_frequency(
        p.frequency_mode, p.manual_frequency_hz
    )
    selected = c["channels"]
    ip = selected.index(p.i_channel)
    qp = selected.index(p.q_channel)
    _configure_source(ctx, frequency, p.power_dbm)
    time.sleep(p.settle_s)
    target = None if p.continuous else round(p.duration_s * c["sample_rate"])

    with h5py.File(s21_path, "a") as s21_file_handle:
        compression = None if c["sample_rate"] >= 500_000 else "gzip"
        root = s21_file_handle.require_group("noise_measurements")
        group_name = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        h = root.create_group(group_name)
        _metadata(ctx, h)
        h.attrs["measurement"] = "KID noise"
        h.attrs["frequency_hz"] = frequency
        h.attrs["frequency_selection_mode"] = p.frequency_mode
        h.attrs["manual_frequency_hz"] = p.manual_frequency_hz
        h.attrs["power_dbm"] = p.power_dbm
        h.attrs["iq_calibration_file"] = calibration_table.source_path
        h.attrs["s21_fit_file"] = noise_calibration.source_path
        h.attrs["psd_method"] = "welch"
        h.attrs["welch_window"] = p.window
        h.attrs["welch_segment_seconds"] = p.segment_seconds
        h.attrs["samples_per_read"] = p.block
        h.attrs["daq_buffer_seconds"] = 10.0
        h.attrs["storage_compression"] = "none" if compression is None else compression
        calp = calibration_table.parameters_at(frequency)
        h.attrs["iq_calibration_parameters"] = [calp.i_offset, calp.q_offset,
                                                calp.i_axis, calp.q_axis,
                                                calp.rotation_rad]
        dt = h5py.string_dtype("utf-8")
        h.create_dataset("channels", data=np.asarray(daq.channels, dtype=object), dtype=dt)
        v = h.create_dataset("raw_voltage_V", shape=(len(c["channels"]), 0), dtype="f8",
                             maxshape=(len(c["channels"]), None),
                             chunks=(len(c["channels"]), p.block), compression=compression)
        cv = h.create_dataset("calibrated_iq_voltage_V", shape=(2, 0), dtype="f8",
                              maxshape=(2, None), chunks=(2, p.block), compression=compression)
        nv = h.create_dataset("normalized_noise_iq", shape=(2, 0), dtype="f8",
                              maxshape=(2, None), chunks=(2, p.block), compression=compression)
        amp = h.create_dataset("noise_amplitude", shape=(0,), dtype="f8",
                               maxshape=(None,), chunks=(p.block,), compression=compression)
        phase = h.create_dataset("noise_phase_rad", shape=(0,), dtype="f8",
                                 maxshape=(None,), chunks=(p.block,), compression=compression)
        t = h.create_dataset("time_s", shape=(0,), dtype="f8",
                             maxshape=(None,), chunks=(p.block,), compression=compression)
        ctx.on_file_created("{}::{}".format(s21_path, h.name))
        total = 0
        last_phase = None
        last_flush = 0
        with ctx.daq_lock:
            for b in daq.acquire_continuous(
                samples_per_read=p.block, buffer_seconds=10.0,
                trigger_mode=c["trigger_mode"], trigger_source=c["trigger_source"],
                trigger_edge=c["trigger_edge"], read_timeout=120,
            ):
                keep = b.sample_count if target is None else min(b.sample_count, target-total)
                if keep <= 0:
                    break
                ic, qc, _ = calibration_table.transform(
                    frequency, b.data[ip, :keep], b.data[qp, :keep]
                )
                calblock = np.vstack((ic, qc))
                noise_amp, noise_phase, noise_i, noise_q, s21_index = \
                    noise_calibration.transform(frequency, ic, qc)
                noise_phase = np.unwrap(noise_phase)
                noise_phase += 0.0 if last_phase is None else \
                    2*np.pi*np.round((last_phase - noise_phase[0])/(2*np.pi))
                last_phase = float(noise_phase[-1])
                new = total + keep
                block_time = np.arange(total, new, dtype=float)/b.actual_sample_rate
                v.resize((len(c["channels"]), new))
                v[:, total:new] = b.data[:, :keep]
                cv.resize((2, new))
                cv[:, total:new] = calblock
                nv.resize((2, new))
                nv[:, total:new] = np.vstack((noise_i, noise_q))
                amp.resize((new,))
                amp[total:new] = noise_amp
                phase.resize((new,))
                phase[total:new] = noise_phase
                t.resize((new,))
                t[total:new] = block_time
                total = new
                h.attrs["actual_sample_rate_hz"] = b.actual_sample_rate
                h.attrs["sample_count_per_channel"] = total
                h.attrs["s21_reference_index"] = s21_index
                h.attrs["s21_reference_frequency_hz"] = noise_calibration.freq[s21_index]
                h.attrs["s21_circle_center"] = [noise_calibration.x0, noise_calibration.y0]
                if total - last_flush >= 2*b.actual_sample_rate:
                    s21_file_handle.flush()
                    last_flush = total
                payload = {
                    "amplitude": noise_amp, "phase": noise_phase,
                    "i_norm": noise_i, "q_norm": noise_q,
                    "s21_i_norm": noise_calibration.i_norm,
                    "s21_q_norm": noise_calibration.q_norm,
                    "test_point": (noise_calibration.i_norm[s21_index],
                                   noise_calibration.q_norm[s21_index]),
                }
                ctx.on_noise_block(block_time, payload, ("Amplitude", "Phase"),
                                   b.actual_sample_rate)
                if ctx.should_stop() or (target is not None and total >= target):
                    daq.stop_continuous()
                    break
        if total >= 16:
            frequency_psd, amplitude_psd, nperseg = welch_psd(
                amp[:], h.attrs["actual_sample_rate_hz"], p.segment_seconds, p.window
            )
            _, phase_psd, _ = welch_psd(
                phase[:], h.attrs["actual_sample_rate_hz"], p.segment_seconds,
                p.window, unwrap=True
            )
            h.create_dataset("noise_spectrum_frequency_hz", data=frequency_psd)
            h.create_dataset("amplitude_psd_per_hz", data=amplitude_psd)
            h.create_dataset("phase_psd_rad2_per_hz", data=phase_psd)
            h.attrs["welch_nperseg"] = nperseg
        h.attrs["stopped_by_user"] = ctx.should_stop()
        s21_file_handle.flush()

    return NoiseResult(
        s21_file=str(s21_path),
        group_name=str(group_name),
        sample_count=int(total),
        stopped=bool(ctx.should_stop()),
    )
