# -*- coding: utf-8 -*-
"""实验数据目录扫描器。

遍历 experiment_data 目录树，解析路径参数（Tr, Pv, Pl），
加载 .s2p 文件为 S21Trace 对象。
"""

import os
import re
from typing import Dict, List, Optional

import numpy as np

from plot_dashboard.data_model import S21Trace, ScanSummary

# =========================================================================
# Regex patterns — 从路径段中提取实验参数
# =========================================================================

# 时间戳: experiment_data/20260605_215526/...
_TIMESTAMP_RE = re.compile(r"[\\/](\d{8}_\d{6})")

# 目标温度: /6K/, /100K/
_TARGET_TEMP_RE = re.compile(r"[\\/](\d+)K[\\/]")

# 实际温度: actual_6.452K
_ACTUAL_TEMP_RE = re.compile(r"actual_([\d.]+)K")

# VNA 源功率: /-25dBm/
_VNA_POWER_RE = re.compile(r"[\\/](-?\d+)dBm[\\/]")

# 激光功率: /00mW/, /17mW/
_LASER_POWER_RE = re.compile(r"[\\/](\d+)mW[\\/]")


# =========================================================================
# Public API
# =========================================================================


def parse_trace_path(path: str) -> Optional[Dict]:
    """从 .s2p 文件路径中提取实验参数。

    不读取文件内容，仅从目录名解析 (Tr, Pv, Pl, timestamp)。

    Args:
        path: .s2p 文件的完整路径。

    Returns:
        包含 timestamp, target_temp_k, actual_temp_k, vna_power_dbm,
        laser_power_mw 的 dict；路径格式不匹配时返回 None。
    """
    # 规范化路径分隔符
    norm = path.replace("\\", "/")

    # ---- timestamp ----
    m = _TIMESTAMP_RE.search(norm)
    if not m:
        return None
    timestamp = m.group(1)

    # ---- 目标温度 ----
    m = _TARGET_TEMP_RE.search(norm)
    if not m:
        return None
    target_temp_k = float(m.group(1))

    # ---- 实际温度 (可选) ----
    m = _ACTUAL_TEMP_RE.search(norm)
    actual_temp_k = float(m.group(1)) if m else None

    # ---- VNA 功率 ----
    m = _VNA_POWER_RE.search(norm)
    if not m:
        return None
    vna_power_dbm = int(m.group(1))

    # ---- 激光功率 ----
    m = _LASER_POWER_RE.search(norm)
    if not m:
        return None
    laser_power_mw = int(m.group(1))

    return {
        "timestamp": timestamp,
        "target_temp_k": target_temp_k,
        "actual_temp_k": actual_temp_k,
        "vna_power_dbm": vna_power_dbm,
        "laser_power_mw": laser_power_mw,
    }


def scan_experiment_dir(root_dir: str) -> List[S21Trace]:
    """遍历目录树，找到所有 .s2p 文件并加载为 S21Trace。

    对每个 .s2p 文件：
    1. 用 parse_trace_path() 从路径提取参数
    2. 用 skrf.Network 加载频率和 S21 数据
    3. 跳过加载失败的文件（损坏/格式不兼容）

    Args:
        root_dir: 实验数据的根目录（通常是 experiment_data/ 或
                  某次实验的时间戳目录）。

    Returns:
        S21Trace 对象列表，按文件路径排序。
    """
    traces: List[S21Trace] = []

    if not os.path.isdir(root_dir):
        return traces

    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fname in filenames:
            if not fname.lower().endswith(".s2p"):
                continue

            full_path = os.path.join(dirpath, fname)
            params = parse_trace_path(full_path)
            if params is None:
                continue

            try:
                trace = _load_trace(full_path, params)
                if trace is not None:
                    traces.append(trace)
            except Exception:
                # 跳过损坏或格式不兼容的文件
                continue

    traces.sort(key=lambda t: (t.timestamp, t.temp_k, t.vna_power_dbm, t.laser_power_mw))
    return traces


# =========================================================================
# Internal helpers
# =========================================================================


def _load_trace(file_path: str, params: Dict) -> Optional[S21Trace]:
    """从 .s2p 文件加载频率和 S21 数据。

    Args:
        file_path: .s2p 文件路径。
        params: parse_trace_path() 返回的参数字典。

    Returns:
        S21Trace 对象，或加载失败时返回 None。
    """
    import skrf as rf

    ntwk = rf.Network(file_path)
    freq_hz = ntwk.f
    # S21: 端口 1→2
    s21_complex = ntwk.s[:, 1, 0]
    s21_db = 20.0 * np.log10(np.abs(s21_complex) + 1e-300)

    return S21Trace(
        file_path=file_path,
        timestamp=params["timestamp"],
        target_temp_k=params["target_temp_k"],
        actual_temp_k=params["actual_temp_k"],
        vna_power_dbm=params["vna_power_dbm"],
        laser_power_mw=params["laser_power_mw"],
        frequency_hz=freq_hz,
        s21_db=s21_db,
    )
