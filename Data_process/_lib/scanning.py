# _lib/scanning.py — 数据目录扫描
"""实验数据目录结构的扫描与解析。

支持两种目录结构:
  hierarchical: {temp}K/actual_{tempMeas}K/{power}dBm/{laser}mW/*.s2p
  flat:         {temp}K/{power}dBm/{laser}mW/*.s2p (merged data)
"""
import os
import re
from pathlib import Path


def parse_temp(dirname):
    """解析温度目录名: '6K' -> 6, 非温度目录返回 None"""
    m = re.fullmatch(r"(\d+)K", dirname)
    return int(m.group(1)) if m else None


def parse_vna_power(dirname):
    """解析 VNA 功率目录名: '-25dBm' -> 25 (正值)"""
    m = re.fullmatch(r"-(\d+)dBm", dirname)
    return int(m.group(1)) if m else None


def parse_laser_power(dirname):
    """解析激光功率目录名: '00mW' -> 0, '03mW' -> 3"""
    m = re.fullmatch(r"(\d+)mW", dirname)
    return int(m.group(1)) if m else None


def scan_temperatures(data_dir, structure="flat"):
    """扫描数据目录下的所有温度子目录，返回排序后的 int 列表。

    Args:
        data_dir: 数据根目录
        structure: "flat" | "hierarchical" — 温度目录始终在顶层，此参数仅为 API 一致性保留
    """
    data_dir = Path(data_dir)
    temps = sorted(
        t for d in os.listdir(data_dir)
        if os.path.isdir(data_dir / d) and (t := parse_temp(d)) is not None
    )
    return temps


def scan_powers(data_dir, ref_temp, structure="flat"):
    """扫描某个温度下的所有 VNA 功率（返回正整数值）。

    Args:
        data_dir: 数据根目录
        ref_temp: 参考温度 (int K)
        structure: "flat" | "hierarchical" — hierarchical 时先进入 actual_*K/ 子目录
    """
    temp_dir = Path(data_dir) / f"{ref_temp}K"
    if not temp_dir.is_dir():
        return []
    if structure == "hierarchical":
        actual_dirs = [d for d in os.listdir(temp_dir)
                       if d.startswith("actual_") and os.path.isdir(temp_dir / d)]
        if not actual_dirs:
            return []
        temp_dir = temp_dir / actual_dirs[0]
    powers = sorted(
        p for d in os.listdir(temp_dir)
        if os.path.isdir(temp_dir / d) and (p := parse_vna_power(d)) is not None
    )
    return powers


def scan_laser_powers(data_dir, temps, meas_power, structure="flat"):
    """扫描所有温度下指定 VNA 功率的激光功率并集。

    Args:
        data_dir: 数据根目录
        temps: 温度列表 (int K)
        meas_power: VNA 功率 (正整数值)
        structure: "flat" | "hierarchical" — hierarchical 时先进入 actual_*K/ 子目录
    """
    all_lasers = set()
    for temp in temps:
        temp_dir = Path(data_dir) / f"{temp}K"
        if not temp_dir.is_dir():
            continue
        if structure == "hierarchical":
            actual_dirs = [d for d in os.listdir(temp_dir)
                           if d.startswith("actual_") and os.path.isdir(temp_dir / d)]
            if not actual_dirs:
                continue
            power_dir = temp_dir / actual_dirs[0] / f"-{meas_power}dBm"
        else:
            power_dir = temp_dir / f"-{meas_power}dBm"
        if not power_dir.is_dir():
            continue
        for d in os.listdir(power_dir):
            lp = parse_laser_power(d)
            if lp is not None and os.path.isdir(power_dir / d):
                all_lasers.add(lp)
    return sorted(all_lasers)


def find_s2p(data_dir, temp, power_dbm, laser_mw, structure="flat"):
    """查找指定条件下的第一个 .s2p 文件。

    Args:
        data_dir: 数据根目录
        temp: 目标温度 (int K)
        power_dbm: VNA 功率 (负值, 如 -25)
        laser_mw: 激光功率 (int mW)
        structure: "flat" | "hierarchical"

    Returns:
        Path 或 None
    """
    data_dir = Path(data_dir)
    abs_power = abs(power_dbm)
    laser_str = f"{laser_mw:02d}mW"
    power_str = f"-{abs_power}dBm"
    temp_str = f"{temp}K"

    if structure == "flat":
        search_dir = data_dir / temp_str / power_str / laser_str
    else:
        # hierarchical: need to find actual_ subdir first
        temp_dir = data_dir / temp_str
        if not temp_dir.is_dir():
            return None
        actual_dirs = [d for d in os.listdir(temp_dir)
                       if d.startswith("actual_") and os.path.isdir(temp_dir / d)]
        if not actual_dirs:
            return None
        search_dir = temp_dir / actual_dirs[0] / power_str / laser_str

    if not search_dir.is_dir():
        return None
    s2p_files = list(search_dir.glob("*.s2p"))
    return s2p_files[0] if s2p_files else None


def build_file_matrix(data_dir, temps, meas_powers, meas_laser_powers, structure="flat"):
    """构建 3D S2P 文件路径矩阵 [temp][power][laser]。

    Args:
        meas_powers: 正值列表, 如 [25, 30, 45]
        meas_laser_powers: 激光功率列表, 如 [0, 1, 3]

    Returns:
        list[list[list[str|None]]] — 不存在的条目为 None
    """
    matrix = []
    for temp in temps:
        temp_matrix = []
        for pwr in meas_powers:
            pwr_matrix = []
            for laser in meas_laser_powers:
                path = find_s2p(data_dir, temp, -pwr, laser, structure=structure)
                pwr_matrix.append(str(path) if path else None)
            temp_matrix.append(pwr_matrix)
        matrix.append(temp_matrix)
    return matrix


def extract_measured_temps(data_dir, temps, structure="flat"):
    """提取每个目标温度对应的实际测量温度。

    从第一个可用的 S2P 文件名中解析 actual_ 温度。
    若无法提取，回退到目标温度。

    Returns:
        list[float] — 与 temps 等长
    """
    from _lib.io_s2p import parse_s2p_filename

    measured = []
    for temp in temps:
        actual = float(temp)
        for pwr in [25, 30, 35, 45, 55]:
            for laser in [0]:
                path = find_s2p(data_dir, temp, -pwr, laser, structure=structure)
                if path:
                    info = parse_s2p_filename(str(path))
                    if info.get("actual_temp_k") is not None:
                        actual = info["actual_temp_k"]
                    break
            if actual != float(temp):
                break
        measured.append(actual)
    return measured
