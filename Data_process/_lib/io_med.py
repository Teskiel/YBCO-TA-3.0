# _lib/io_med.py — data_med/ JSON + npz 读写
"""中间参数文件的读写工具。"""
import json
import os
import numpy as np
from pathlib import Path


def _get_script_dir():
    try:
        return Path(__file__).resolve().parent.parent
    except NameError:
        return Path(os.getcwd())


def _read_chip_id(chip_json_path):
    """从 chip.json 读 chip_id，缺失返回 None。"""
    try:
        with open(chip_json_path, "r", encoding="utf-8") as f:
            return json.load(f).get("chip_id")
    except (OSError, ValueError):
        return None


def resolve_dataset_id(source_data_dir):
    """从源数据目录推导 dataset_id（data_med/output/temporary_resonance 的路径键）。

    探测信号是 chip.json 位于 source.parent（芯片层）：
      - 两级布局（{chip}/chip.json + {chip}/{run}/）→ "{chip_id}__{run}"，
        chip_id 缺失时兜底用芯片目录名，绝不退回裸 run 名（否则不同芯片的
        run 1 会互相覆盖）。
      - 扁平数据集 → source.name 原名，旧行为逐字节不变。

    Args:
        source_data_dir: 原始 S2P 数据根目录 (Path 或 str)

    Returns:
        str — dataset_id
    """
    source = Path(source_data_dir)
    chip_json = source.parent / "chip.json"
    if chip_json.is_file():
        chip_id = _read_chip_id(chip_json) or source.parent.name
        return f"{chip_id}__{source.name}"
    return source.name


def resolve_data_med_dir(source_data_dir):
    """从源数据目录名推导 data_med/ 子目录。

    Args:
        source_data_dir: 原始 S2P 数据根目录 (Path 或 str)

    Returns:
        data_med/{dataset_id}/ 的绝对 Path
    """
    return _get_script_dir() / "data_med" / resolve_dataset_id(source_data_dir)


def resolve_output_dir(source_data_dir, plot_type):
    """从源数据目录名推导 output/ 子目录。

    Args:
        source_data_dir: 原始 S2P 数据根目录
        plot_type: 图片类型, 如 "f0_vs_T", "Qi_vs_T"

    Returns:
        output/{dataset_id}/{plot_type}/ 的绝对 Path
    """
    return _get_script_dir() / "output" / resolve_dataset_id(source_data_dir) / plot_type


def get_temporary_resonance_path(source_data_dir):
    """temporary_resonance 持久文件路径 — 在 data_med 外, 删 data_med 不受影响。

    Returns:
        temporary_resonance/{dataset_id}.json 的绝对 Path
    """
    return _get_script_dir() / "temporary_resonance" / f"{resolve_dataset_id(source_data_dir)}.json"


def read_temporary_resonance(source_data_dir):
    """读取持久选点文件, 不存在返回 None。"""
    path = get_temporary_resonance_path(source_data_dir)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_temporary_resonance(source_data_dir, data):
    """写入持久选点文件。自动创建父目录。"""
    path = get_temporary_resonance_path(source_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def read_json(med_dir, filename):
    """从 data_med 目录读取 JSON 文件。

    Returns:
        dict 或 None (文件不存在时)
    """
    path = Path(med_dir) / filename
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(med_dir, filename, data):
    """将数据写入 data_med 目录的 JSON 文件。自动创建父目录。"""
    path = Path(med_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def read_npz(npz_path, key):
    """从 .npz 文件读取指定 key 的数组。"""
    with np.load(npz_path, allow_pickle=False) as archive:
        return archive[key]


def load_file_manifest(data_med_dir):
    """加载 file_manifest.json，不存在则返回 None。

    file_manifest.json 记录每个 (T_target, Pv, Pl) 组合的：
      - 唯一对应 s2p 文件路径
      - 即时温度 (actual_temp_k，从文件名解析)

    Returns:
        dict | None
    """
    return read_json(data_med_dir, "file_manifest.json")


def lookup_actual_temp(manifest, target_temp_k, vna_power_dbm=None, laser_power_mw=None):
    """从 file_manifest 查找特定 (T, Pv, Pl) 组合的即时温度。

    Args:
        manifest: load_file_manifest() 返回的 dict
        target_temp_k: 目标温度 (int 或 str)
        vna_power_dbm: VNA 功率 (dBm, 负值)。若为 None 则宽松匹配
        laser_power_mw: 激光功率 (mW)。若为 None 则宽松匹配

    Returns:
        float | None — 即时温度 (K)，找不到则返回 None
    """
    if manifest is None:
        return None
    by_temp = manifest.get("by_target_temp", {})
    entries = by_temp.get(str(target_temp_k), [])
    if not entries:
        return None

    # 精确匹配 (Pv, Pl)
    if vna_power_dbm is not None and laser_power_mw is not None:
        for entry in entries:
            if (entry.get("vna_power_dbm") == vna_power_dbm and
                    entry.get("laser_power_mw") == laser_power_mw):
                return entry.get("actual_temp_k")
        return None

    # 宽松匹配: 返回该目标温度下第一条记录的实际温度
    return entries[0].get("actual_temp_k") if entries else None


def write_npz(npz_path, **arrays):
    """将命名数组写入 .npz 文件。自动创建父目录。

    Usage:
        write_npz(path, freq=freq_arr, s21=s21_arr)
    """
    path = Path(npz_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(path), **arrays)
