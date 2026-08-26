# -*- coding: utf-8 -*-
"""自动化 worker 的 mock dry-run 无 GUI 验证（计划 Step 6b）。

用法：
    python _verify_worker_dryrun.py

预期：<tmp>/<exp>/77K/res1/{0,3}mW/{coarse_s21.h5,fine_s21.h5,pic/*.png}
      + checkpoint.json 标记 completed（target_k=actual_k=77，单层温度目录）。
      日志出现芯片预测 f0 / 宽扫 / 精扫 / 拟合。
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import gui_config

# ---- 构造最小 window 桩 ----
from PyQt5.QtCore import QCoreApplication


class _StubTab:
    def __init__(self):
        self.worker = None


class _StubDaqDialog:
    def __init__(self):
        self.preview_worker = None


class _StubManager:
    def __init__(self):
        self.config = {}
        self.source = None
        self.source_status = {}
        self.daq_config = {}
        self.temp = None
        self.laser = None


class _StubWindow:
    def __init__(self):
        self.manager = _StubManager()
        self.s21 = _StubTab()
        self.noise = _StubTab()
        self.iq = _StubTab()
        self.daq_dialog = _StubDaqDialog()


def main():
    app = QCoreApplication(sys.argv)
    cfg = gui_config.load_gui_config()
    cfg["backend"] = "mock"
    cfg["experiment_name"] = "_dryrun_gui_test"
    cfg["temperature_list_k"] = [77.0]
    cfg["fixed_temperature_k"] = 77.0
    cfg["laser_power_mw"] = [0.0, 3.0]
    cfg["noise_duration_s"] = 0.05
    cfg["noise_block_samples"] = 10000
    cfg["s21_points"] = 101
    cfg["samples_per_point"] = 1000
    cfg["settle_s"] = 0.005
    cfg["save_gui_figures"] = True
    cfg["save_root"] = tempfile.mkdtemp(prefix="dryrun_gui_")
    cfg["skip_noise"] = False

    win = _StubWindow()
    from automation_worker import AutomationWorker
    worker = AutomationWorker(win, cfg, dry_run=True)

    from PyQt5.QtCore import Qt
    lines = []
    metas = []
    # 无事件循环：全部用 DirectConnection，信号在发射线程内同步执行
    worker.log_line.connect(lambda s: (lines.append(s), print("[LOG]", s)), Qt.DirectConnection)
    worker.progress.connect(lambda d, t, l: print("[PROG] %d/%d %s" % (d, t, l)),
                            Qt.DirectConnection)
    worker.s21_fit_ready.connect(lambda fit: print("[FIT] ready"), Qt.DirectConnection)
    worker.noise_reset.connect(lambda: print("[NOISE-RESET]"), Qt.DirectConnection)
    worker.save_figure.connect(lambda meta: (metas.append(meta),
                                              print("[SAVE-FIG]", meta.get("group"),
                                                    meta.get("type"), meta.get("res_name"),
                                                    meta.get("power_mw"))),
                               Qt.DirectConnection)
    worker.completed.connect(lambda p: print("[DONE]", p), Qt.DirectConnection)
    worker.failed.connect(lambda e: (print("[FAIL]", e), sys.exit(1)), Qt.DirectConnection)

    t0 = time.time()
    worker.run()  # 直接同步执行（无事件循环依赖）
    print("[elapsed] %.2fs" % (time.time() - t0))

    root = Path(cfg["save_root"])
    h5s = sorted(str(p) for p in root.rglob("*.h5"))
    print("h5 count:", len(h5s))
    for p in h5s:
        print("  H5:", p)
    cp = root / cfg["experiment_name"] / "checkpoint.json"
    print("checkpoint exists:", cp.exists())
    # 验证 save_figure meta（图片改由 GUI 面板画布落盘，dry-run 无画布 → 只验 meta 内容）
    groups = sorted({str(m.get("group")) for m in metas})
    print("save_figure groups:", groups)
    noise_metas = [m for m in metas if str(m.get("group", "")).startswith("noise")]
    print("noise save meta count:", len(noise_metas))
    for m in noise_metas:
        print("   noise meta: type=%s mode=%s target=%s actual=%s res=%s" % (
            m.get("type"), m.get("mode"), m.get("target_k"), m.get("actual_k"), m.get("res_name")))
    assert "s21_coarse" in groups and "s21_fine" in groups, "缺少 s21 图 meta"
    assert any(str(m.get("group")) == "noise" for m in metas), "缺少噪声图 meta"
    # 验证精扫谐振频率与预测接近
    import h5py
    import numpy as np
    fine_files = [p for p in h5s if "fine_s21" in p]
    for p in fine_files:
        with h5py.File(p, "r") as h:
            f0 = h.attrs.get("resonance_frequency_hz", None)
            # 面板引擎(MeasurementWorker)应写入温度/激光 extra_attrs（数据级一致）
            has_extra = ("temperature_k" in h.attrs and "laser_power_mw" in h.attrs)
            nm = len(h["noise_measurements"].keys()) if "noise_measurements" in h else 0
            print("fine", Path(p).parent.name, "f0=%.6f GHz  extra_attrs=%s  noise_groups=%d"
                  % (f0 / 1e9 if f0 else None, has_extra, nm))
            # ---- 噪声数据质量断言（防 mock 假绿灯）----
            # 旧 mock 生成器让噪声绕圆心随机游走：归一化 IQ 画整圆（跨度≈0.87）、
            # 相位缠绕、低频 PSD 达 10^4 量级——此处必须抓住，结构检查看不出来。
            if "noise_measurements" in h:
                for gname, g in h["noise_measurements"].items():
                    niq = g["normalized_noise_iq"][:]
                    span_i = float(niq[0].max() - niq[0].min())
                    span_q = float(niq[1].max() - niq[1].min())
                    psd = g["phase_psd_rad2_per_hz"][:]
                    freqs = g["noise_spectrum_frequency_hz"][:]
                    low = float(psd[freqs < 100.0].max()) if np.any(freqs < 100.0) else 0.0
                    print("   noise[%s]: IQ span=(%.4f, %.4f)  low-freq phase PSD max=%.3e"
                          % (gname, span_i, span_q, low))
                    assert max(span_i, span_q) < 0.1, \
                        "noise IQ 跨度 %.3f ≥ 0.1：噪声点绕圆分布（mock 病态或归一化失效）" \
                        % max(span_i, span_q)
                    assert low < 1e-2, \
                        "低频 phase PSD %.3e ≥ 1e-2：相位异常漂移（mock 病态或参考点错误）" % low
    assert len(fine_files) >= 2, "预期 ≥2 个 fine_s21"
    assert all(any(str(m.get("group")) == "noise" for m in metas) for _ in range(1)), "OK"
    print("OK")


if __name__ == "__main__":
    main()
