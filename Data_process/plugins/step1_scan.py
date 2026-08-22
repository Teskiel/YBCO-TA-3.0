# plugins/step1_scan.py — S2P 数据目录扫描
"""扫描原始数据的温度/功率/激光功率结构, 写入 scan_result.json。"""
import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _lib.plugin_registry import plugin
from _lib.scanning import scan_temperatures, scan_powers, scan_laser_powers, extract_measured_temps, find_s2p
from _lib.io_med import write_json, resolve_data_med_dir, resolve_dataset_id
from _lib.io_s2p import parse_s2p_filename


def build_file_manifest(source, temps, vna_powers_dbm, laser_powers_mw, dataset_name, structure):
    """遍历所有 (T, Pv, Pl) 组合，构建 S2P 文件清单。

    每个条目记录:
      - 目标温度 / 即时温度 / VNA 功率 / 激光功率
      - 唯一对应 s2p 文件的相对路径

    Returns:
        dict — file_manifest 结构
    """
    entries = []
    by_target_temp = {}
    skipped = 0

    for temp in temps:
        temp_entries = []
        for pv in vna_powers_dbm:
            for pl in laser_powers_mw:
                s2p_path = find_s2p(source, temp, pv, pl, structure=structure)
                if s2p_path is None:
                    skipped += 1
                    continue

                info = parse_s2p_filename(str(s2p_path))
                actual_temp = info.get("actual_temp_k")

                # 相对路径 (相对于 source 目录)
                try:
                    rel_path = str(s2p_path.relative_to(source))
                except ValueError:
                    rel_path = str(s2p_path)

                entry = {
                    "target_temp_k": temp,
                    "actual_temp_k": actual_temp,
                    "vna_power_dbm": pv,
                    "laser_power_mw": pl,
                    "s2p_rel_path": rel_path,
                }
                entries.append(entry)
                temp_entries.append(entry)

        by_target_temp[str(temp)] = temp_entries

    # 统计即时温度偏差
    deviations = [
        abs(e["actual_temp_k"] - e["target_temp_k"])
        for e in entries
        if e["actual_temp_k"] is not None
    ]
    stats = {
        "total_entries": len(entries),
        "skipped_missing": skipped,
        "max_temp_deviation_k": round(max(deviations), 4) if deviations else None,
        "mean_temp_deviation_k": round(sum(deviations) / len(deviations), 4) if deviations else None,
    }

    return {
        "dataset_name": dataset_name,
        "structure_type": structure,
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "entries": entries,
        "by_target_temp": by_target_temp,
        "stats": stats,
    }


@plugin(
    phase="process",
    order=1,
    inputs=[],
    outputs=["scan_result.json", "file_manifest.json"],
    description="扫描 S2P 数据目录结构: 温度/功率/激光功率 + 文件清单",
)
def main(data_med_dir, source_data_dir, config=None):
    source = Path(source_data_dir)
    dataset_name = resolve_dataset_id(source)

    # 检测目录结构类型
    first_temp = None
    import re
    for d in sorted(os.listdir(source)):
        m = re.fullmatch(r"(\d+)K", d)
        if m and os.path.isdir(source / d):
            first_temp = int(m.group(1))
            break

    if first_temp is None:
        raise FileNotFoundError(f"No temperature directories found in {source}")

    # 检查是否有 actual_ 子目录
    temp_dir = source / f"{first_temp}K"
    has_actual = any(
        d.startswith("actual_") and os.path.isdir(temp_dir / d)
        for d in os.listdir(temp_dir)
    )
    structure = "hierarchical" if has_actual else "flat"

    print(f"[step1_scan] 数据集: {dataset_name}")
    print(f"[step1_scan] 目录结构: {structure}")

    temps = scan_temperatures(source, structure=structure)
    print(f"[step1_scan] 温度: {temps}")

    powers = scan_powers(source, temps[0], structure=structure)
    vna_powers_dbm = [-p for p in powers]
    print(f"[step1_scan] VNA 功率: {vna_powers_dbm} dBm")

    lasers = scan_laser_powers(source, temps, powers[0], structure=structure)
    print(f"[step1_scan] 激光功率: {lasers} mW")

    measured = extract_measured_temps(source, temps, structure=structure)
    print(f"[step1_scan] 实测温度: {[f'{t:.3f}' for t in measured]} K")

    scan_result = {
        "dataset_name": dataset_name,
        "source_data_dir": str(source),
        "structure_type": structure,
        "_structure_type_note": "hierarchical = {temp}K/actual_{tempMeas}K/{power}dBm/{laser}mW/; flat = {temp}K/{power}dBm/{laser}mW/ (merged data)",
        "temperatures": temps,
        "measured_temperatures": measured,
        "vna_powers_dbm": vna_powers_dbm,
        "laser_powers_mw": lasers,
        "n_resonators_expected": 5,
        "scanned_at": datetime.now().isoformat(),
    }

    write_json(data_med_dir, "scan_result.json", scan_result)
    print(f"[step1_scan] 写入 data_med/{dataset_name}/scan_result.json")

    # 生成 S2P 文件清单 (含即时温度)
    manifest = build_file_manifest(
        source, temps, vna_powers_dbm, lasers, dataset_name, structure
    )
    write_json(data_med_dir, "file_manifest.json", manifest)
    print(f"[step1_scan] 写入 file_manifest.json ({manifest['stats']['total_entries']} 条目, "
          f"{manifest['stats']['skipped_missing']} 缺失, "
          f"max delta T = {manifest['stats']['max_temp_deviation_k']} K)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    source = Path(args.data_dir)
    med_dir = resolve_data_med_dir(source)
    med_dir.mkdir(parents=True, exist_ok=True)
    main(str(med_dir), str(source))
