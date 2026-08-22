# _lib/io_s2p.py — S2P 文件读写
"""S2P 文件加载与文件名解析。"""
import re
import skrf as rf


def load_s_param(filename, port1=1, port2=0):
    """加载 S2P 文件，返回 (freq_Hz, s21_complex)。

    Args:
        filename: S2P 文件路径
        port1: 输出端口 (默认 1)
        port2: 输入端口 (默认 0)

    Returns:
        (freq_hz, s21_complex) — 频率数组 (Hz) 和复 S21 参数
    """
    net = rf.Network(filename)
    freq = net.f
    s21 = net.s[:, port1, port2]
    return freq, s21


def parse_s2p_filename(filepath):
    """从 S2P 文件名提取测量元数据。

    期望格式: NAME_{vna}dBm_{laser}mW_target_{temp}K_actual_{actual}K.s2p

    Returns:
        dict with keys: vna_power_dbm, laser_power_mw, target_temp_k, actual_temp_k
        actual_temp_k 为 None 如果文件名中无 actual_ 段。
    """
    filename = str(filepath)
    info = {
        "vna_power_dbm": None,
        "laser_power_mw": None,
        "target_temp_k": None,
        "actual_temp_k": None,
    }

    # Parse VNA power: match -NdBm pattern
    m = re.search(r"-(\d+)dBm", filename)
    if m:
        info["vna_power_dbm"] = -int(m.group(1))

    m = re.search(r"(\d+)mW", filename)
    if m:
        info["laser_power_mw"] = int(m.group(1))

    m = re.search(r"target_(\d+)K", filename)
    if m:
        info["target_temp_k"] = int(m.group(1))

    m = re.search(r"actual_(\d+\.?\d*)K", filename)
    if m:
        info["actual_temp_k"] = float(m.group(1))

    return info
