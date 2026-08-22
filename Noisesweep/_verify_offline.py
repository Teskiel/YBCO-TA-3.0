# -*- coding: utf-8 -*-
"""noisesweep 离线验证脚本（计划 B 节 1-7 项断言）。dry-run 产物与真实数据无硬件断言。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import h5py
import numpy as np

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))

import resonance_table as rt          # noqa: E402
import noisesweep                     # noqa: E402

PASS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    PASS.append(cond)
    print("[{}] {}{}".format(status, name, ("  " + detail) if detail else ""))


# ---------------------------------------------------------------------------
# B1 schema 对齐：S21NoiseCalibration.load + plot_S21_hdf5.read_s21_hdf5
# ---------------------------------------------------------------------------
print("== B1 schema 对齐 ==")
# 新目录结构：<目标温度>K/<res>/<power>mW/<实际温度>K/fine_s21.h5（实际温度层漂移，按 glob）
_fine_candidates = sorted((PROJ / "data" / "S21" / "_dryrun_check" / "77K" / "res1" / "3mW").glob("*/fine_s21.h5"))
fine = _fine_candidates[0] if _fine_candidates else None
check("新目录结构 fine_s21.h5 存在 (77K/res1/3mW/<实际温度>)", fine is not None,
      str(_fine_candidates[:1]))
from S21_fitting import S21NoiseCalibration   # noqa: E402
try:
    cal = S21NoiseCalibration.load(fine)
    freq, idx = cal.measurement_frequency("F0_PLUS_DF", 3.4e9)
    check("S21NoiseCalibration.load 成功", True,
          "f0+df 测量频率={:.6f} GHz, s21_index={}".format(freq / 1e9, idx))
except Exception as exc:
    check("S21NoiseCalibration.load 成功", False, repr(exc))

import plot_S21_hdf5   # noqa: E402
try:
    dat = plot_S21_hdf5.read_s21_hdf5(fine)
    check("plot_S21_hdf5.read_s21_hdf5 无异常", True,
          "keys={}".format(sorted(dat.keys())[:8]))
except Exception as exc:
    check("plot_S21_hdf5.read_s21_hdf5 无异常", False, repr(exc))

# 下游契约：scraps_fit 组 + x0/y0 attrs
with h5py.File(fine, "r") as h:
    has_fit = "scraps_fit" in h and "x0" in h["scraps_fit"].attrs \
        and "y0" in h["scraps_fit"].attrs
    check("scraps_fit 组 + x0/y0 attrs", has_fit)
    # extra_attrs 三件套
    for k in ("temperature_k", "laser_power_mw", "laser_wavelength_nm"):
        check("extra_attr {}".format(k), k in h.attrs)
    # 噪声组
    groups = list(h["noise_measurements"].keys())
    check("噪声组存在", len(groups) >= 1, str(groups))
    g = h["noise_measurements"][groups[0]]
    psd_ok = "noise_spectrum_frequency_hz" in g and "amplitude_psd_per_hz" in g
    check("噪声组 PSD 数据集（total>=16 才加）", psd_ok)
    check("噪声 attrs psd_method=welch", g.attrs.get("psd_method") == "welch")

# ---------------------------------------------------------------------------
# B2 追踪读取：真实 resonance_table.txt → 8×5；插值可还原
# ---------------------------------------------------------------------------
print("\n== B2 追踪读取 ==")
txt = PROJ.parent / "Data_process" / "resonance_table.txt"
table = rt.load_resonance_table(txt)
# 内部约定 freq_hz 为 (n_res=5, n_temp=8)
check("txt 形状 5×8 (n_res×n_temp)", table.freq_hz.shape == (5, 8),
      "shape={}".format(table.freq_hz.shape))
t_target = float(table.temps_k[3])  # 表内温度，应精确还原
f_int = table.reference_frequency_hz(t_target, 2)
f_exact = table.freq_hz[2, 3]
rel = abs(f_int - f_exact) / f_exact
check("表内温度插值可还原 (rel<1e-6)", rel < 1e-6,
      "f_int={:.9f} f_exact={:.9f}".format(f_int, f_exact))
f_ext = table.reference_frequency_hz(4.0, 0)  # 外推到 4K
check("外推到 4K 单调合理 (>0)", f_ext > 0, "f_ext={:.6f} GHz".format(f_ext / 1e9))

# fit_results.json（真实数据）
fj = PROJ / "data" / "fit_results.json" if (PROJ / "data" / "fit_results.json").exists() else None
if fj is not None:
    jtable = rt.load_resonance_table(fj)
    check("json 追踪读取", jtable.freq_hz is not None)
    resp = jtable.responsivity_ppm_per_mw(float(jtable.temps_k[-1]), 0)
    check("responsivity 读为 None（真实数据为空列表）", resp is None)
else:
    print("  （跳过：项目目录无 fit_results.json）")

# ---------------------------------------------------------------------------
# B3 频移预测：符号与量级
# ---------------------------------------------------------------------------
print("\n== B3 频移预测 ==")
f_ref = 5.0e9
resp = -450.0  # 77K 量级 ppm/mW
f_pred = rt.predict_laser_shift(f_ref, resp, 17.0)
expected = f_ref * (1 + resp * 17.0 * 1e-6)
check("预测 = f_ref×(1+resp×P×1e-6)", abs(f_pred - expected) < 1e-6)
check("负 responsivity → 频率下移", f_pred < f_ref,
      "shift={:.1f} MHz".format((f_pred - f_ref) / 1e6))
check("None → 不变", rt.predict_laser_shift(f_ref, None, 17.0) == f_ref)

# ---------------------------------------------------------------------------
# B4 带宽映射：step 语义
# ---------------------------------------------------------------------------
print("\n== B4 带宽映射 ==")
rows = [[4.0, 15.0], [20.0, 20.0], [40.0, 20.0], [77.0, 25.0]]
expect = {4: 15, 10: 15, 20: 20, 55: 20, 77: 25, 90: 25}
for t, want in expect.items():
    got = noisesweep.fine_bandwidth_hz(t, rows, "step") / 1e6
    check("step {}K → {} MHz".format(t, want), got == want, "got={}".format(got))
got = noisesweep.fine_bandwidth_hz(10, rows, "linear") / 1e6
check("linear 10K 插值", 15.0 <= got <= 20.0, "got={}".format(got))

# ---------------------------------------------------------------------------
# B5 续跑幂等：第二次 dry-run 全部 skip，不新增噪声组
# ---------------------------------------------------------------------------
print("\n== B5 续跑幂等 ==")
def count_noise(paths):
    n = 0
    for p in paths:
        with h5py.File(p, "r") as h:
            if "noise_measurements" in h:
                n += len(h["noise_measurements"])
    return n

finals = sorted((PROJ / "data" / "S21" / "_dryrun_check").rglob("fine_s21.h5"))
before = count_noise(finals)
proc = subprocess.run(
    [sys.executable, "noisesweep.py", "--config", "_dryrun_config.json",
     "--dry-run", "run"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=str(PROJ),
    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
)
after = count_noise(finals)
out = proc.stdout + proc.stderr
n_skip = out.count("checkpoint 已完成，跳过") + out.count("文件已存在（coarse+fine）")
check("第二次运行返回 0", proc.returncode == 0,
      "rc={}".format(proc.returncode))
check("不新增噪声组", after == before,
      "before={} after={}".format(before, after))
check("跳过全部点", n_skip >= 2, "skip 计数={}".format(n_skip))

# ---------------------------------------------------------------------------
# B6 参数双轨：config 非 null 永远赢；null+inherit → UI
# ---------------------------------------------------------------------------
print("\n== B6 参数双轨 ==")
app = {"addresses": {"laser": "TCPIP::APP", "lakeshore": "GPIB::APP"},
       "laser": {"wavelength_nm": 1550.0, "power_sequence_mw": [0, 5, 9]},
       "temperature_sweep": {"max_wait_min": 30}}
gui = {"daq.sample_rate": 250000.0, "s21.points": 55}   # GUI 持久化 = 扁平点号键
raw = {
    "backend": "mock",
    "app_settings_file": "app.json", "kid_gui_settings_file": "gui.json",
    "inherit_ui_params": True,
    "laser_wavelength_nm": 1310.0,       # config 有值 → 赢
    "laser_power_mw": None,              # null + inherit → 从 app 继承
    "sample_rate": None,                 # null + inherit → 从 gui 继承
    "s21_points": None,
    "stability_max_wait_s": None,
    "experiment_name": "x",
}
import tempfile
with tempfile.TemporaryDirectory() as d:
    p_app = Path(d) / "app.json"; p_gui = Path(d) / "gui.json"
    p_app.write_text(json.dumps(app), encoding="utf-8")
    p_gui.write_text(json.dumps(gui), encoding="utf-8")
    raw["app_settings_file"] = str(p_app); raw["kid_gui_settings_file"] = str(p_gui)
    cfg = noisesweep.resolve_config(raw, str(p_app), str(p_gui), inherit=True)
    check("config 有值 → 永远采用 (1310)", cfg["laser_wavelength_nm"] == 1310.0)
    check("null+inherit → app 功率序列", cfg["laser_power_mw"] == [0, 5, 9])
    check("null+inherit → gui 采样率", cfg["sample_rate"] == 250000.0)
    check("null+inherit → gui s21 点数", cfg["s21_points"] == 55)
    check("null 且 UI 无 → 默认 (1800s)",
          cfg["stability_max_wait_s"] == noisesweep.DEFAULTS["stability_max_wait_s"])
    # inherit=false → null 字段回落默认而非 UI
    cfg2 = noisesweep.resolve_config(raw, str(p_app), str(p_gui), inherit=False)
    check("inherit=false → null 不继承 (sample_rate)",
          cfg2["sample_rate"] == noisesweep.DEFAULTS["sample_rate"])
    check("inherit=false → null 不继承 (points)",
          cfg2["s21_points"] == noisesweep.DEFAULTS["s21_points"])

# ---------------------------------------------------------------------------
# B7 Checkpoint：save/load 往返 + 损坏文件
# ---------------------------------------------------------------------------
print("\n== B7 Checkpoint ==")
with tempfile.TemporaryDirectory() as d:
    ck = noisesweep.Checkpoint(str(Path(d) / "ck.json"))
    ck.mark_temperature(77.0, 77.01, True)
    ck.mark_complete(77.0, "res1", 3.0, 3.3976e9, "fine.h5")
    ck.save()
    ck2 = noisesweep.Checkpoint(str(Path(d) / "ck.json"))
    check("完成判定往返", ck2.is_complete(77.0, "res1", 3.0))
    check("未做点未完成", not ck2.is_complete(77.0, "res1", 5.0))
    # 损坏文件 → 按空处理不崩
    Path(d, "bad.json").write_text("{ not json", encoding="utf-8")
    ck3 = noisesweep.Checkpoint(str(Path(d) / "bad.json"))
    check("损坏文件按空处理", not ck3.is_complete(77.0, "res1", 3.0))

# ---------------------------------------------------------------------------
# B8 GUI 持久化：无 PyQt5 的结构性验证（键集对称 + 模块语法）
# ---------------------------------------------------------------------------
print("\n== B8 GUI 持久化（无 Qt 结构性验证） ==")
import ast   # noqa: E402

gui_src = (PROJ / "kid_measurement_gui_v3.py").read_text(encoding="utf-8")
tree = ast.parse(gui_src)

def dotted_strings(body, pred):
    out = set()
    for node in ast.walk(body):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and pred(node.func.id) and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            out.add(node.args[0].value)
    return out

saved = set()
restored = set()
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        if node.name == "_gui_snapshots":
            # 收集返回 dict 字面量的字符串键
            for s in ast.walk(node):
                if isinstance(s, ast.Dict):
                    for k in s.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            saved.add(k.value)
        elif node.name == "load_gui_settings":
            restored = dotted_strings(node, lambda n: n == "restore")

check("保存键集 == 还原键集", saved == restored,
      "保存={} 还原={}".format(len(saved), len(restored)))
check("键集非空", len(saved) >= 40, "共 {} 键".format(len(saved)))

import py_compile   # noqa: E402
try:
    py_compile.compile(str(PROJ / "kid_measurement_gui_v3.py"), doraise=True)
    check("kid_measurement_gui_v3.py 语法编译", True)
except Exception as exc:
    check("kid_measurement_gui_v3.py 语法编译", False, repr(exc))

# ---------------------------------------------------------------------------
# B9 宽扫带宽三态（fixed / temperature / formula）
# ---------------------------------------------------------------------------
print("\n== B9 宽扫带宽三态 ==")
cfg_fixed = {"wide_bandwidth_mode": "fixed", "wide_bandwidth_mhz": 150.0}
check("fixed → 150 MHz", noisesweep.wide_bandwidth_hz(77.0, cfg_fixed) == 150e6)
cfg_temp = {"wide_bandwidth_mode": "temperature",
            "wide_bandwidth_mhz_by_temperature": [[4.0, 50.0], [77.0, 200.0]],
            "wide_bandwidth_interpolation": "linear"}
check("temperature 77K → 200 MHz",
      abs(noisesweep.wide_bandwidth_hz(77.0, cfg_temp) - 200e6) < 1.0)
check("temperature 4K → 50 MHz",
      abs(noisesweep.wide_bandwidth_hz(4.0, cfg_temp) - 50e6) < 1.0)
cfg_formula = {"wide_bandwidth_mode": "formula",
               "wide_bandwidth_formula": "50 + 2*T"}
check("formula 77K → (50+2×77) MHz",
      abs(noisesweep.wide_bandwidth_hz(77.0, cfg_formula) - 204e6) < 1.0)
try:
    noisesweep.wide_bandwidth_hz(77.0, {"wide_bandwidth_mode": "formula"})
    check("formula 缺表达式抛错", False)
except ValueError:
    check("formula 缺表达式抛错", True)

# ---------------------------------------------------------------------------
# B10 目录结构（目标温度顶层 + 实际温度层）+ 幂等三元组
# ---------------------------------------------------------------------------
print("\n== B10 目录结构 + 幂等三元组 ==")
base = PROJ / "data" / "S21" / "_dryrun_check"
point = base / "77K" / "res1" / "3mW"
act_dirs = sorted(d for d in point.iterdir() if d.is_dir())
check("实际温度层存在（77K/res1/3mW/<实际温度>）", len(act_dirs) >= 1,
      str([d.name for d in act_dirs]))
check("实际温度层下有 coarse+fine",
      bool(act_dirs) and (act_dirs[0] / "coarse_s21.h5").exists()
      and (act_dirs[0] / "fine_s21.h5").exists())
ck = noisesweep.Checkpoint(str(base / "checkpoint.json"))
check("checkpoint: 77K/res1/3mW 完成", ck.is_complete(77.0, "res1", 3.0))
check("checkpoint: 不同目标温度 20K 不误跳过",
      not ck.is_complete(20.0, "res1", 3.0))
check("checkpoint: 不同功率 5mW 不误跳过",
      not ck.is_complete(77.0, "res1", 5.0))

# ---------------------------------------------------------------------------
# B11 频率来源三选一 + 标定缺失自动回退（不中断）
# ---------------------------------------------------------------------------
print("\n== B11 频率来源三选一 + 兜底 ==")
res = {"name": "res1", "reference_frequency_hz": None}
cfg_manual = {"manual_reference_frequency_hz": 4.0e9, "predict_laser_shift": False}
f = noisesweep._fallback_reference(cfg_manual, table, res, 0, 77.0, 0.0,
                                   noisesweep.LOG)
check("全局手动优先", f == 4.0e9)
cfg_table = {"manual_reference_frequency_hz": None, "predict_laser_shift": False}
f = noisesweep._fallback_reference(cfg_table, table, res, 0, 77.0, 0.0,
                                   noisesweep.LOG)
check("无手动 → 表插值", abs(f - table.reference_frequency_hz(77.0, 0)) < 1e-9)
# 芯片标定库勾选但 chip_id 缺失 → 自动回退追踪表/手动并告警（half=None，不中断）
cfg_chip_missing = {"use_chip_library": True, "chip_id": None, "chip_run_id": None,
                    "data_process_dir": str(PROJ.parent / "Data_process"),
                    "manual_reference_frequency_hz": None, "predict_laser_shift": False}
f_ref, half = noisesweep._resolve_reference(cfg_chip_missing, table, res, 0,
                                            77.0, 0.0, noisesweep.LOG)
check("标定缺失 → 回退表插值（half=None）",
      half is None and abs(f_ref - table.reference_frequency_hz(77.0, 0)) < 1e-9)

# ---------------------------------------------------------------------------
# B12 芯片标定库预测（chip_library 薄壳）
# ---------------------------------------------------------------------------
print("\n== B12 芯片标定库预测 ==")
import chip_library as cl   # noqa: E402
dp = str(PROJ.parent / "Data_process")
f_pred, half = cl.predict_resonance_frequencies(
    chip_id="YBCO_unknown-batch", run_id="20260609-0624__6-80K__full",
    T_k=77.0, data_process_dir=dp)
check("标定库预测 5 谐振", f_pred is not None and len(f_pred) == 5)
if f_pred is not None:
    f_track = table.reference_frequency_hz(77.0, 0)
    err = abs(f_pred[0] - f_track) / 1e6
    check("res1 预测与追踪表 77K 误差 < 20 MHz", err < 20.0,
          "err={:.1f} MHz".format(err))
    check("窗口半宽全部 ≥ 15 MHz",
          all(float(half[i]) >= 15e6 for i in range(len(half))),
          "half=[{}]".format(",".join("{:.0f}".format(h / 1e6) for h in half)))

# ---------------------------------------------------------------------------
print("\n================ 汇总 ================")
total = len(PASS); ok = sum(PASS)
print("{}/{} 通过".format(ok, total))
sys.exit(0 if ok == total else 1)
