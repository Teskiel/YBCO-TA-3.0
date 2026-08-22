# plugins/step2_detect.py — 谐振检测
"""对每个温度加载参考 S2P, 运行 find_true_resonances, 写入 detected_peaks.json。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from _lib.plugin_registry import plugin
from _lib.io_med import read_json, write_json, resolve_data_med_dir
from _lib.io_s2p import load_s_param
from _lib.scanning import find_s2p
from _lib.resonance import find_true_resonances


PEAK_KWARGS_DEFAULT = dict(
    min_prominence=3,
    distance=10,
    phase_window=10,
    phase_diff_snr_threshold=0.5,
    noise_inner_window=5,
    noise_outer_window=40,
    min_phase_diff_support_points=4,
    min_phase_diff_width=4,
    max_phase_diff_width=None,
)


@plugin(
    phase="process",
    order=2,
    inputs=["scan_result.json"],
    outputs=["detected_peaks.json"],
    description="谐振检测: find_true_resonances 双判据寻峰",
)
def main(data_med_dir, source_data_dir, config=None):
    scan = read_json(data_med_dir, "scan_result.json")
    if scan is None:
        raise FileNotFoundError("scan_result.json not found — run step1_scan first")

    source = Path(source_data_dir)
    structure = scan["structure_type"]
    temps = scan["temperatures"]
    measured = scan["measured_temperatures"]

    # 合并 config 中的参数覆盖
    peak_kwargs = dict(PEAK_KWARGS_DEFAULT)
    if config and config.get("peak_kwargs_overrides"):
        peak_kwargs.update(config["peak_kwargs_overrides"])

    print(f"[step2_detect] 检测 {len(temps)} 个温度点的谐振峰")
    print(f"[step2_detect] 参数: min_prominence={peak_kwargs['min_prominence']}, "
          f"phase_diff_snr_threshold={peak_kwargs['phase_diff_snr_threshold']}")

    by_temperature = {}

    for i, temp in enumerate(temps):
        ref_path = find_s2p(source, temp, -25, 0, structure=structure)
        if ref_path is None:
            # 尝试其他功率
            for pwr in [30, 35, 45, 55]:
                ref_path = find_s2p(source, temp, -pwr, 0, structure=structure)
                if ref_path:
                    break

        if ref_path is None:
            print(f"  [WARN] T={temp}K: 无参考 S2P 文件, 跳过")
            continue

        freq, s21 = load_s_param(str(ref_path))
        peaks, _, _ = find_true_resonances(freq, s21, plot=False, **peak_kwargs)

        peak_list = []
        for p in peaks:
            peak_list.append({
                "index": p["index"],
                "freq_hz": float(p["frequency"]),
                "freq_ghz": round(float(p["frequency"]) / 1e9, 4),
                "dip_db": round(float(p["transmission"]), 2),
                "phase_diff_snr": round(float(p["phase_diff_snr"]), 1),
                "prominence": round(float(p.get("transmission_prominence", 0)), 2),
                "width_hz": int(p.get("phase_diff_width", 0)),
            })

        by_temperature[str(temp)] = {
            "measured_temp_k": measured[i],
            "peaks": peak_list,
        }

        n_peaks = len(peak_list)
        top_freqs = ", ".join(
            f"{p['freq_ghz']:.3f}GHz" for p in sorted(peak_list, key=lambda x: x["dip_db"])[:5]
        )
        print(f"  T={temp}K ({measured[i]:.3f}K): {n_peaks} peaks -> {top_freqs}")

    result = {
        "params": peak_kwargs,
        "by_temperature": by_temperature,
    }

    write_json(data_med_dir, "detected_peaks.json", result)
    total_peaks = sum(len(v["peaks"]) for v in by_temperature.values())
    print(f"[step2_detect] 总计检测到 {total_peaks} 个谐振峰 -> detected_peaks.json")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    main(str(med_dir), str(source))
