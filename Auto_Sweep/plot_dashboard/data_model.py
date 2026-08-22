# -*- coding: utf-8 -*-
"""Plot Dashboard 数据模型。

定义实验数据的核心数据结构：S21Trace（单次 S21 测量）和
ScanSummary（一次目录扫描的统计摘要）。
"""

from dataclasses import dataclass, field
from typing import Optional, Set

import numpy as np


@dataclass
class S21Trace:
    """一次 S21 测量及其完整实验参数。

    每个实例对应一个 .s2p 文件。频率和 S21 幅值作为 numpy 数组
    存储，以便直接用于 matplotlib 绘图和未来的 Q 值拟合。

    Attributes:
        file_path: .s2p 文件的绝对路径。
        timestamp: 实验批次时间戳，如 "20260605_215526"。
        target_temp_k: 目标温度 (K)，从目录名解析。
        actual_temp_k: LakeShore 实测温度 (K)，从目录名解析；可为 None。
        vna_power_dbm: VNA 源功率 (dBm)。
        laser_power_mw: 激光输出功率 (mW)。
        frequency_hz: S21 扫频的频率轴 (Hz)。
        s21_db: 20*log10(|S21|)，单位为 dB。
    """

    file_path: str
    timestamp: str
    target_temp_k: float
    actual_temp_k: Optional[float]
    vna_power_dbm: int
    laser_power_mw: int
    frequency_hz: np.ndarray = field(repr=False)
    s21_db: np.ndarray = field(repr=False)

    @property
    def temp_k(self) -> float:
        """有效温度：优先用实测值，回退到目标温度。"""
        if self.actual_temp_k is not None:
            return self.actual_temp_k
        return self.target_temp_k

    def __repr__(self) -> str:
        n_pts = len(self.frequency_hz)
        return (
            f"S21Trace(Tr={self.target_temp_k:.1f}K,"
            f" actual={self.actual_temp_k or '—'}K,"
            f" Pv={self.vna_power_dbm:+d}dBm,"
            f" Pl={self.laser_power_mw}mW,"
            f" Npts={n_pts})"
        )


@dataclass
class ScanSummary:
    """一次目录扫描的统计摘要。

    用于填充 FilterPanel 的控件范围，避免每次筛选都要遍历全部数据。

    Attributes:
        root_dir: 扫描的根目录。
        total_files: 成功解析的 .s2p 文件总数。
        vna_powers: 数据中出现的所有 VNA 功率值。
        laser_powers: 数据中出现的所有激光功率值。
        timestamps: 数据中出现的所有实验批次时间戳。
        temp_min_k: 最小有效温度 (K)；无数据时为 None。
        temp_max_k: 最大有效温度 (K)；无数据时为 None。
    """

    root_dir: str = ""
    total_files: int = 0
    vna_powers: Set[int] = field(default_factory=set)
    laser_powers: Set[int] = field(default_factory=set)
    timestamps: Set[str] = field(default_factory=set)
    temp_min_k: Optional[float] = None
    temp_max_k: Optional[float] = None

    def add(self, trace: S21Trace) -> None:
        """将一条 trace 的参数纳入统计。"""
        self.total_files += 1
        self.vna_powers.add(trace.vna_power_dbm)
        self.laser_powers.add(trace.laser_power_mw)
        self.timestamps.add(trace.timestamp)
        t = trace.temp_k
        if self.temp_min_k is None or t < self.temp_min_k:
            self.temp_min_k = t
        if self.temp_max_k is None or t > self.temp_max_k:
            self.temp_max_k = t
