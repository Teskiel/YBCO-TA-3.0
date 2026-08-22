# -*- coding: utf-8 -*-
"""
Figure: Qi vs Temperature — 内部品质因数温度演化

物理意义:
  随温度升高，YBCO 超导能隙减小，准粒子密度增加，
  准粒子损耗增大，导致内部品质因数 Qi 下降。
  在 Tc (~80K) 附近 Qi 急剧下降，对应超导-正常态转变。

数据来源: S2P 直接处理，scraps cmplxIQ 拟合提取 Qi
条件: Pl=0mW (暗场)，自动检测所有 VNA 功率

用法:
  # 小规模测试
  python qi_vs_temperature.py --temps 6,10,20

  # 全量运行
  python qi_vs_temperature.py

  # 指定数据集 / 输出
  python qi_vs_temperature.py --data-dir "D:/path/to/dataset" --output "D:/path/to/output"
"""

import sys
import os
import io
import re
import argparse
from pathlib import Path
from contextlib import redirect_stdout

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import UnivariateSpline
from scipy.signal import savgol_filter
import warnings
warnings.filterwarnings("ignore")

# ── scraps ──────────────────────────────────────────────
_SCRAPS_PATH = r"D:\YBCO\Measurement-System\Measurement-System"
if _SCRAPS_PATH not in sys.path:
    sys.path.insert(0, _SCRAPS_PATH)
import scraps.resonator as scr
from scraps.fitsS21 import cmplxIQ_fit, cmplxIQ_params

# ── dataprocess ─────────────────────────────────────────
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "Data_process", "otherwise")
)
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)
from dataprocess import load_s_param, find_true_resonances

# ── _style_config ───────────────────────────────────────
_DRAW_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _DRAW_DIR not in sys.path:
    sys.path.insert(0, _DRAW_DIR)
from _style_config import (apply_style, get_figsize, get_resonator_color,
                           save_figure, RESONATOR_COLORS)


# ═══════════════════════════════════════════════════════════
# 有限差分频率外推 (基于 _tracking_utils.py::predict_f0_fd)
# ═══════════════════════════════════════════════════════════

def predict_f0_fd(f0_history, temp_k):
    """有限差分外推预测温度 temp_k 的 f0 (GHz)。

    f0_history: [(T1, f0_1), (T2, f0_2), ...] 按温度升序排列。
    返回预测的 f0 (GHz)，如果历史不足则返回 None。
    """
    if not f0_history:
        return None

    # 精确匹配 (0.5 K 容差内)
    for tk, f0 in f0_history:
        if abs(tk - temp_k) < 0.5:
            return f0

    # 仅取温度低于目标的历史点
    below = [(tk, f0) for tk, f0 in f0_history if tk < temp_k]
    if not below:
        return f0_history[0][1]

    recent = below[-5:]
    f_vals = [f for _, f in recent]
    n = len(f_vals)

    if n == 1:
        return f_vals[0]
    elif n == 2:
        return f_vals[-1] + (f_vals[-1] - f_vals[-2])
    elif n == 3:
        f1, f2, f3 = f_vals
        return f3 + (f3 - f2) - (f3 - 2 * f2 + f1)
    elif n == 4:
        f1, f2, f3, f4 = f_vals
        return f4 + (f4 - f3) - (f4 - 2 * f3 + f2) + (f4 - 3 * f3 + 3 * f2 - f1)
    else:
        f1, f2, f3, f4, f5 = f_vals[-5:]
        return (f5 + (f5 - f4) - (f5 - 2 * f4 + f3)
                + (f5 - 3 * f4 + 3 * f3 - f2)
                - (f5 - 4 * f4 + 6 * f3 - 4 * f2 + f1))


# ═══════════════════════════════════════════════════════════
# 数据发现
# ═══════════════════════════════════════════════════════════

def discover_data(data_dir, laser_power_mw=0):
    """扫描数据集目录，返回结构化数据索引。

    目录结构: {T}K / {-XX}dBm / {Pl:02d}mW / *.s2p

    Returns:
        dict: {target_T_K: {vna_power_str: (actual_T_K, s2p_path)}}
        例如: {6: {"-25": (6.611, "path/to/file.s2p")}, ...}

        target_T_K 来自目录名，actual_T_K 来自文件名中的 actual_xxxK。
    """
    data_index = {}

    temp_pattern = re.compile(r"^(\d+)K$")
    pv_pattern = re.compile(r"^(-?\d+)dBm$")
    actual_pattern = re.compile(r"actual_([\d.]+)K")
    laser_str = f"{int(laser_power_mw):02d}mW"

    base = Path(data_dir)
    if not base.exists():
        print(f"[ERROR] 数据目录不存在: {data_dir}")
        return data_index

    for temp_dir in sorted(base.iterdir()):
        if not temp_dir.is_dir():
            continue
        tm = temp_pattern.match(temp_dir.name)
        if not tm:
            continue
        target_T = int(tm.group(1))

        pv_dict = {}
        for pv_dir in sorted(temp_dir.iterdir()):
            if not pv_dir.is_dir():
                continue
            pm = pv_pattern.match(pv_dir.name)
            if not pm:
                continue
            pv_str = pm.group(1)  # 保留负号, 如 "-25"

            laser_dir = pv_dir / laser_str
            if not laser_dir.exists():
                continue

            s2p_files = list(laser_dir.glob("*.s2p"))
            if not s2p_files:
                continue

            s2p_path = str(s2p_files[0])
            am = actual_pattern.search(s2p_path)
            actual_T = float(am.group(1)) if am else float(target_T)

            pv_dict[pv_str] = (actual_T, s2p_path)

        if pv_dict:
            data_index[target_T] = pv_dict

    return data_index


# ═══════════════════════════════════════════════════════════
# 谐振器识别 (参考条件下)
# ═══════════════════════════════════════════════════════════

# find_true_resonances 参数 — 来自 _tracking_utils.PEAK_KWARGS
PEAK_KWARGS = dict(
    min_prominence=1.0,
    distance=50,
    phase_window=20,
    phase_diff_snr_threshold=1.5,
    noise_inner_window=5,
    noise_outer_window=40,
    min_phase_diff_support_points=2,
    min_phase_diff_width=2,
    plot=False,
)


def identify_resonators(s2p_path):
    """在单个 S2P 文件中识别谐振器。

    Returns:
        list[dict]: 按频率升序排列的谐振峰列表，
        每个 dict 包含 'name' (R1~R5) 和 'f0_ghz'。
    """
    freq, s21 = load_s_param(s2p_path)

    with redirect_stdout(io.StringIO()):
        peaks, _, _ = find_true_resonances(freq=freq, s21=s21, **PEAK_KWARGS)

    # 过滤: dip 深度 < -1 dB (太浅的不是谐振器)
    peaks = [p for p in peaks if p["transmission"] < -1.0]

    # 按频率升序排列
    peaks_sorted = sorted(peaks, key=lambda p: p["frequency"])

    resonators = []
    for i, peak in enumerate(peaks_sorted[:5]):
        resonators.append({
            "name": f"R{i + 1}",
            "f0_ghz": peak["frequency"] / 1e9,  # Hz → GHz
            "dip_db": peak["transmission"],
        })

    return resonators


# ═══════════════════════════════════════════════════════════
# Qi 提取 (scraps cmplxIQ 拟合)
# ═══════════════════════════════════════════════════════════

def fit_one_resonance(freq, s21, f0_pred_ghz, span_mhz=50.0,
                      temp_k=0.0, pwr_label=""):
    """在预测频率附近做 cmplxIQ 拟合，提取 Qi 和实际 f0。

    Args:
        freq: 频率数组 (Hz)
        s21: 复数 S21
        f0_pred_ghz: 预测谐振频率 (GHz)
        span_mhz: 拟合窗口半宽 (MHz)
        temp_k: 温度标签
        pwr_label: 功率标签

    Returns:
        (qi, f0_fitted_ghz): qi 为 None 表示拟合失败
    """
    f0_hz = f0_pred_ghz * 1e9
    span_hz = span_mhz * 1e6
    df = np.mean(np.diff(freq))
    count = int(span_hz / df)
    idx_center = np.argmin(np.abs(freq - f0_hz))

    i0 = max(0, idx_center - count)
    i1 = min(len(freq), idx_center + count)

    if i1 - i0 < 20:
        return None, None

    freq_cut = freq[i0:i1]
    s21_cut = s21[i0:i1]

    try:
        res = scr.Resonator("r", temp_k, pwr_label,
                           freq_cut,
                           np.real(s21_cut),
                           np.imag(s21_cut))
        res.load_params(cmplxIQ_params)
        res.do_lmfit(cmplxIQ_fit)

        qi = getattr(res, "Qi", None)
        f0_raw = getattr(res, "f0", None)  # Hz (scraps 内部单位)

        # 合理性检查
        if qi is not None:
            if qi <= 0 or qi > 1e8:
                qi = None

        # 转换为 GHz (与 identify_resonators 保持一致)
        f0_fitted = f0_raw / 1e9 if f0_raw is not None else None

        return qi, f0_fitted

    except Exception:
        return None, None


# ═══════════════════════════════════════════════════════════
# 主处理流程
# ═══════════════════════════════════════════════════════════

def process_dataset(data_dir, laser_power_mw=0, temp_filter=None):
    """处理数据集，按 VNA 功率 × 温度逐点做 cmplxIQ 拟合提取 Qi。

    Returns:
        results: {pv_str: {"temps": [actual_T, ...],
                           "resonators": {"R1": [qi_or_None, ...], ...}}}
        r_names: ["R1", "R2", ...]
    """
    # ── 数据发现 ──
    data_index = discover_data(data_dir, laser_power_mw)
    if not data_index:
        print("[ERROR] 未找到任何数据")
        return {}, []

    target_temps = sorted(data_index.keys())
    if temp_filter:
        target_temps = [t for t in target_temps if t in temp_filter]

    # 收集所有 VNA 功率 (按数值降序 = -25, -35, -45)
    all_pv = sorted(
        {pv for td in data_index.values() for pv in td},
        key=lambda x: int(x), reverse=True
    )

    print(f"\n{'=' * 60}")
    print(f"数据集: {Path(data_dir).name}")
    print(f"温度: {len(target_temps)} 个 ({min(target_temps)}K → {max(target_temps)}K)")
    print(f"VNA 功率: {all_pv} ({len(all_pv)} 个)")
    print(f"激光功率: {laser_power_mw} mW")
    print(f"{'=' * 60}")

    # ── 参考温度下识别谐振器 ──
    # 对每个 VNA 功率，在最低可用温度识别谐振器
    ref_resonators = {}  # {pv_str: [{"name":, "f0_ghz":}, ...]}

    for pv in all_pv:
        # 找该 VNA 功率的最低温度数据
        for tT in target_temps:
            if tT in data_index and pv in data_index[tT]:
                actual_T, s2p_path = data_index[tT][pv]
                resonators = identify_resonators(s2p_path)
                ref_resonators[pv] = resonators
                print(f"\n参考识别 Pv={pv}dBm @ T≈{tT}K: "
                      f"找到 {len(resonators)} 个谐振器")
                for r in resonators:
                    print(f"  {r['name']}: f0={r['f0_ghz']:.4f} GHz, "
                          f"dip={r['dip_db']:.1f} dB")
                break
        else:
            print(f"[WARN] Pv={pv}dBm: 无可用参考数据")

    if not ref_resonators:
        print("[ERROR] 无任何谐振器被识别")
        return {}, []

    # 统一谐振器名称 (取所有 VNA 功率中识别到的并集)
    all_r_names = []
    for r in ref_resonators.get(all_pv[0], []):
        all_r_names.append(r["name"])
    if not all_r_names:
        print("[ERROR] 参考识别未找到谐振器")
        return {}, []

    print(f"\n谐振器: {all_r_names}")

    # ── 初始化 f0 追踪历史 ──
    # {pv: {r_name: [(T, f0), ...]}}
    f0_histories = {}
    for pv in all_pv:
        f0_histories[pv] = {}
        if pv in ref_resonators:
            # 获取参考温度
            for tT in target_temps:
                if tT in data_index and pv in data_index[tT]:
                    ref_T = data_index[tT][pv][0]
                    break
            for r in ref_resonators[pv]:
                f0_histories[pv][r["name"]] = [(ref_T, r["f0_ghz"])]
        # 对于没有参考数据的 VNA 功率，用相邻功率的 f0
        for r_name in all_r_names:
            if r_name not in f0_histories[pv]:
                f0_histories[pv][r_name] = []

    # ── 初始化结果 ──
    results = {}
    for pv in all_pv:
        results[pv] = {
            "temps": [],
            "resonators": {r: [] for r in all_r_names},
        }

    # ── 逐温度、VNA 功率处理 ──
    total = len(target_temps) * len(all_pv)
    done = 0
    n_fit_ok = 0
    n_fit_fail = 0

    print(f"\n{'=' * 60}")
    print(f"Qi 提取: {total} 次拟合 ({len(all_r_names)} resonator × "
          f"{len(target_temps)} T × {len(all_pv)} Pv)")
    print(f"{'=' * 60}")

    for tT in target_temps:
        if tT not in data_index:
            continue

        for pv in all_pv:
            done += 1

            # ── 获取 S2P ──
            if pv not in data_index[tT]:
                # 该 (T, Pv) 组合无数据，填 None
                for r_name in all_r_names:
                    results[pv]["resonators"][r_name].append(None)
                continue

            actual_T, s2p_path = data_index[tT][pv]
            results[pv]["temps"].append(actual_T)

            # ── 加载 S2P ──
            try:
                freq, s21 = load_s_param(s2p_path)
            except Exception as e:
                print(f"  [WARN] S2P 加载失败 T={tT}K Pv={pv}: {e}")
                for r_name in all_r_names:
                    results[pv]["resonators"][r_name].append(None)
                continue

            # ── 对每个谐振器做拟合 ──
            for r_name in all_r_names:
                f0_pred = predict_f0_fd(f0_histories[pv].get(r_name, []), actual_T)

                if f0_pred is None:
                    results[pv]["resonators"][r_name].append(None)
                    continue

                qi, f0_fitted = fit_one_resonance(
                    freq, s21, f0_pred, span_mhz=50.0,
                    temp_k=actual_T, pwr_label=f"{pv}dBm"
                )

                results[pv]["resonators"][r_name].append(qi)

                if f0_fitted is not None:
                    f0_histories[pv][r_name].append((actual_T, f0_fitted))
                    n_fit_ok += 1
                else:
                    n_fit_fail += 1

            # ── 进度 ──
            if done % 30 == 0 or done == total:
                print(f"  [{done:4d}/{total}] T={tT:3d}K Pv={pv:>3s}dBm  "
                      f"OK:{n_fit_ok} FAIL:{n_fit_fail}")

    print(f"\n拟合完成: 成功 {n_fit_ok}, 失败 {n_fit_fail}")
    return results, all_pv, all_r_names


# ═══════════════════════════════════════════════════════════
# 绘图
# ═══════════════════════════════════════════════════════════

def plot_qi_vs_temperature(results, all_pv, r_names, output_dir,
                           dataset_label="", preset="quick_check"):
    """生成 Qi(T) 出版级图片。

    N×1 垂直子图 (N = VNA 功率数)，每个子图 Qi(T) 五线叠加。
    """
    apply_style(preset)

    n_pv = len([pv for pv in all_pv if pv in results and results[pv]["temps"]])
    if n_pv == 0:
        print("[ERROR] 无有效数据可绘图")
        return None

    fig, axes = plt.subplots(n_pv, 1, figsize=(10, 3.6 * n_pv),
                             sharex=True, squeeze=False)
    axes = axes.flatten()

    ax_idx = 0
    for pv in all_pv:
        if pv not in results:
            continue
        data = results[pv]
        temps = np.array(data["temps"])
        if len(temps) == 0:
            continue

        ax = axes[ax_idx]
        ax_idx += 1

        # 为每条谐振器曲线绘图
        for r_name in r_names:
            qi_list = data["resonators"].get(r_name, [])

            if len(qi_list) != len(temps):
                continue

            # 提取有效数据点
            valid = []
            for t, q in zip(temps, qi_list):
                if q is None:
                    continue
                if isinstance(q, float) and (np.isnan(q) or q <= 0):
                    continue
                valid.append((t, q))

            if len(valid) < 2:
                continue

            tv = np.array([v[0] for v in valid])
            qv = np.array([v[1] for v in valid])

            color = get_resonator_color(r_name)

            # 散点 (纯 scatter, 无连线)
            ax.scatter(tv, qv, s=28, color=color, alpha=0.55,
                      edgecolors="none", zorder=3, label=r_name)

        # 坐标轴与标签
        ax.set_ylabel("Internal Quality Factor $Q_i$", fontsize=11)
        ax.set_yscale("log")
        ax.set_title(f"$P_v$ = {pv} dBm,  $P_l$ = 0 mW",
                    fontweight="bold", fontsize=13)
        ax.legend(loc="upper right", framealpha=0.85, fontsize=9,
                 ncol=1, edgecolor="#999999")
        ax.grid(True, alpha=0.3)

        # 正常态区域 (浅红色底)
        ax.axvspan(77, 93, alpha=0.06, color="red", zorder=0)

    # 共享 X 轴标签 (最后一个子图)
    axes[ax_idx - 1].set_xlabel("Temperature (K)", fontsize=11)

    # 总标题
    title = "$Q_i(T)$ — $P_l$ = 0 mW"
    if dataset_label:
        title += f"\n{dataset_label}"
    fig.suptitle(title, fontweight="bold", fontsize=14)

    fig.tight_layout(rect=[0, 0, 1, 0.97])

    # 保存
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    basepath = out_dir / "qi_vs_temperature"
    save_figure(fig, str(basepath), ["png", "svg", "pdf"])
    plt.close(fig)

    print(f"\n图片已保存:")
    for ext in ["png", "svg", "pdf"]:
        print(f"  {basepath}.{ext}")

    return fig


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

DEFAULT_DATA_DIR = (
    "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/"
    "20260605-0606__6-90K__774pts"
)
DEFAULT_OUTPUT_DIR = (
    "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/"
)


def main():
    parser = argparse.ArgumentParser(
        description="Qi vs Temperature — 内部品质因数温度演化图"
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                       help="数据集目录路径")
    parser.add_argument("--laser-power", type=float, default=0.0,
                       help="激光功率 (mW), 默认 0")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR,
                       help="输出目录")
    parser.add_argument("--temps", default=None,
                       help="逗号分隔的目标温度列表 (K), 如 '6,10,20'。默认: 全部")
    parser.add_argument("--preset", default="quick_check",
                       choices=["quick_check", "prb_single", "prb_double",
                                "presentation"],
                       help="绘图样式预设 (默认: quick_check)")
    args = parser.parse_args()

    # 解析温度筛选
    temp_filter = None
    if args.temps:
        temp_filter = {int(float(t)) for t in args.temps.split(",")}
        print(f"温度筛选: {sorted(temp_filter)} K")

    # 检查数据目录
    if not Path(args.data_dir).exists():
        print(f"[ERROR] 数据目录不存在: {args.data_dir}")
        sys.exit(1)

    dataset_label = Path(args.data_dir).name

    # ── 处理 ──
    results, all_pv, r_names = process_dataset(
        args.data_dir, args.laser_power, temp_filter
    )

    if not results:
        print("[ERROR] 处理失败, 无结果")
        sys.exit(1)

    # ── 绘图 ──
    plot_qi_vs_temperature(
        results, all_pv, r_names, args.output,
        dataset_label=dataset_label, preset=args.preset
    )

    print("\nDone! All tasks completed.")


if __name__ == "__main__":
    main()
