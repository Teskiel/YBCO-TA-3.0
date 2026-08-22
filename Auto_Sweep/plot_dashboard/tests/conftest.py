# -*- coding: utf-8 -*-
"""Plot Dashboard 测试共享夹具。

提供 QApplication 实例和合成 S21Trace 数据工厂，
与项目根 tests/conftest.py 保持一致的风格。
"""

import sys

import numpy as np
import pytest
from PyQt5.QtWidgets import QApplication


# =========================================================================
# QApplication fixture (模块级，与 tests/conftest.py 模式一致)
# =========================================================================


@pytest.fixture(scope="module")
def qapp():
    """模块级 QApplication 实例，所有 GUI 测试共享。"""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# =========================================================================
# 合成数据工厂
# =========================================================================


def make_synthetic_trace(
    target_temp_k=6.0,
    actual_temp_k=6.452,
    vna_power_dbm=-25,
    laser_power_mw=0,
    timestamp="20260605_215526",
    n_pts=201,
    freq_start_ghz=3.0,
    freq_stop_ghz=6.0,
    dip_freq_ghz=4.5,
    dip_depth_db=-30.0,
    q_factor=5000,
):
    """创建一个带人工谐振谷的 S21Trace，用于测试绘图。

    生成一个包含单个 Lorentz 谐振谷的 S21 频谱，
    便于验证 plot_canvas 和 filter_panel 行为。

    Args:
        target_temp_k: 目标温度。
        actual_temp_k: 实测温度。
        vna_power_dbm: VNA 源功率。
        laser_power_mw: 激光功率。
        timestamp: 实验时间戳。
        n_pts: 频点数。
        freq_start_ghz: 起始频率 (GHz)。
        freq_stop_ghz: 终止频率 (GHz)。
        dip_freq_ghz: 谐振谷中心频率 (GHz)。
        dip_depth_db: 谷底深度 (dB)。
        q_factor: 品质因数 (决定谷宽)。

    Returns:
        plot_dashboard.data_model.S21Trace
    """
    from plot_dashboard.data_model import S21Trace

    freq = np.linspace(freq_start_ghz * 1e9, freq_stop_ghz * 1e9, n_pts)
    # Lorentz 谐振模型: S21 = 1 - (Q⁻¹) / (Q⁻¹ + 2j(f-f₀)/f₀)
    f0 = dip_freq_ghz * 1e9
    bandwidth = f0 / q_factor  # Δf_3dB = f₀/Q
    # 构建谐振谷: 谷底深度 = dip_depth_db (dB)
    dip_linear = 10.0 ** (dip_depth_db / 20.0)
    # S21(f) = dip_linear at f=f₀, → 1 far from f₀
    detuning = (freq - f0) / (bandwidth / 2)
    s21_linear = 1.0 - (1.0 - dip_linear) / (1.0 + detuning**2)
    s21_db = 20.0 * np.log10(np.abs(s21_linear) + 1e-300)

    return S21Trace(
        file_path=f"/mock/{timestamp}/{target_temp_k}K/"
                  f"actual_{actual_temp_k}K/{vna_power_dbm:+d}dBm/"
                  f"{laser_power_mw:02d}mW/YBCO.s2p",
        timestamp=timestamp,
        target_temp_k=target_temp_k,
        actual_temp_k=actual_temp_k,
        vna_power_dbm=vna_power_dbm,
        laser_power_mw=laser_power_mw,
        frequency_hz=freq,
        s21_db=s21_db,
    )


def make_trace_set(
    temp_list=None,
    pv_list=None,
    pl_list=None,
    timestamp="20260605_215526",
):
    """批量创建合成 trace，覆盖指定的温度/VNA功率/激光功率组合。

    Args:
        temp_list: [(target_k, actual_k), ...] 默认 [(6,6.5), (8,8.5), (10,10.5)]
        pv_list: [-25, -35, -45] 默认
        pl_list: [0, 1, 3, 5, 7, 9] 默认
        timestamp: 时间戳。

    Returns:
        List[S21Trace]
    """
    if temp_list is None:
        temp_list = [(6, 6.5), (8, 8.5), (10, 10.5)]
    if pv_list is None:
        pv_list = [-25, -35, -45]
    if pl_list is None:
        pl_list = [0, 1, 3, 5, 7, 9]

    traces = []
    for target, actual in temp_list:
        for pv in pv_list:
            for pl in pl_list:
                # 谐振频率随温度轻微偏移（模拟物理行为）
                dip_freq = 4.5 - (actual - 6.0) * 0.05
                trace = make_synthetic_trace(
                    target_temp_k=target,
                    actual_temp_k=actual,
                    vna_power_dbm=pv,
                    laser_power_mw=pl,
                    timestamp=timestamp,
                    dip_freq_ghz=dip_freq,
                    dip_depth_db=-30.0 - abs(pv) * 0.1,  # 功率越高谷越深
                )
                traces.append(trace)
    return traces
