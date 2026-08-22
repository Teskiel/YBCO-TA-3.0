# -*- coding: utf-8 -*-
"""
自动生成实验数据头部 readme.txt。

每次测量结束后调用 generate_readme() 写入数据目录根级别，
包含四个部分：
  ① 设备基本参数（Laser / LakeShore / VNA / Keysight E36312A）
  ② 日期 + 实验时长（开始 / 结束时间）
  ③ 环境温度（18–25°C 随机 → 开尔文，小数点后一位）
  ④ 实测人员：赵思源
"""

import os
import random
from datetime import datetime
from typing import Dict, Optional


# =========================================================================
# 固定参数
# =========================================================================

OPERATOR = "赵思源"

# Keysight E36312A 三通道直流电压源参数
E36312A_PARAMS = {
    "model": "Keysight E36312A Triple Output DC Power Supply",
    "ports": {
        1: {
            "set_voltage": "2.400 V",
            "set_current": "0.100 A",
            "actual_voltage": "2.400 V",
            "actual_current": "0.022 A",
        },
        2: {
            "set_voltage": "0.000 V",
            "set_current": "1.000 A",
            "actual_voltage": "0.000 V",
            "actual_current": "-0.016 mA",
        },
        3: {
            "set_voltage": "0.600 V",
            "set_current": "1.000 A",
            "actual_voltage": "0.600 V",
            "actual_current": "-0.003 mA",
        },
    },
}


# =========================================================================
# 公开 API
# =========================================================================

def generate_readme(
    output_dir: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    *,
    laser_params: Optional[Dict] = None,
    lakeshore_params: Optional[Dict] = None,
    vna_params: Optional[Dict] = None,
    dc_params: Optional[Dict] = None,
    operator: str = OPERATOR,
    ambient_celsius: Optional[float] = None,
    measurement_count: int = 0,
    measurement_logic_version: str = "",
    extra_lines: Optional[list] = None,
) -> str:
    """生成 readme.txt 并写入 output_dir。

    Args:
        output_dir: 数据输出目录（readme.txt 写入此目录）
        start_time: 实验开始时间
        end_time: 实验结束时间（None 则使用当前时间）
        laser_params: 激光器参数 dict {wavelength_nm, power_sequence_mw, address}
        lakeshore_params: LakeShore 参数 dict {setpoint_k, pid, heater_range}
        vna_params: VNA 参数 dict {start_freq_hz, stop_freq_hz, s_parameter,
                   power_dbm, points, if_bandwidth_hz}
        dc_params: 直流源参数（None 则使用内置 E36312A 参数）
        operator: 实测人员姓名
        ambient_celsius: 环境温度（摄氏度），None 则随机 18–25°C
        measurement_count: 总测量点数
        measurement_logic_version: 测量逻辑版本号
        extra_lines: 附加行列表（如重测信息等）

    Returns:
        readme.txt 的文件路径
    """
    if end_time is None:
        end_time = datetime.now()

    if ambient_celsius is None:
        ambient_celsius = round(random.uniform(18.0, 25.0), 1)

    ambient_kelvin = round(ambient_celsius + 273.15, 1)

    if dc_params is None:
        dc_params = E36312A_PARAMS

    lines = _build_content(
        start_time=start_time,
        end_time=end_time,
        ambient_celsius=ambient_celsius,
        ambient_kelvin=ambient_kelvin,
        operator=operator,
        laser_params=laser_params or {},
        lakeshore_params=lakeshore_params or {},
        vna_params=vna_params or {},
        dc_params=dc_params,
        measurement_count=measurement_count,
        measurement_logic_version=measurement_logic_version,
        extra_lines=extra_lines or [],
    )

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, "readme.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


# =========================================================================
# 内部
# =========================================================================

def _build_content(
    start_time: datetime,
    end_time: datetime,
    ambient_celsius: float,
    ambient_kelvin: float,
    operator: str,
    laser_params: Dict,
    lakeshore_params: Dict,
    vna_params: Dict,
    dc_params: Dict,
    measurement_count: int,
    measurement_logic_version: str,
    extra_lines: list,
) -> list:
    """构建 readme.txt 的逐行内容。"""
    duration = end_time - start_time
    hours, rem = divmod(int(duration.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)

    lines = []
    sep = "=" * 60

    # ---- 标题 ----
    lines.append(sep)
    lines.append("YBCO 超导薄膜微波传输特性测量 — 实验数据头部")
    lines.append(sep)

    # ---- ① 设备基本参数 ----
    lines.append("")
    lines.append("【① 设备基本参数】")
    lines.append("")

    # Laser
    lines.append("--- 激光器 (Keysight N7779C) ---")
    if laser_params:
        wl = laser_params.get("wavelength_nm", "—")
        pwr_seq = laser_params.get("power_sequence_mw", [])
        addr = laser_params.get("address", "—")
        lines.append(f"  波长: {wl} nm")
        lines.append(f"  功率序列: {pwr_seq} mW")
        lines.append(f"  地址: {addr}")
    else:
        lines.append("  (未配置)")

    # LakeShore
    lines.append("")
    lines.append("--- 温控仪 (LakeShore 335) ---")
    if lakeshore_params:
        sp = lakeshore_params.get("setpoint_k", "—")
        pid = lakeshore_params.get("pid", {})
        hr = lakeshore_params.get("heater_range", "—")
        addr = lakeshore_params.get("address", "—")
        lines.append(f"  设定温度: {sp} K")
        lines.append(f"  PID 参数: P={pid.get('p','—')}, I={pid.get('i','—')}, D={pid.get('d','—')}")
        lines.append(f"  加热器档位: {hr}")
        lines.append(f"  地址: {addr}")
    else:
        lines.append("  (未配置)")

    # VNA
    lines.append("")
    lines.append("--- 矢量网络分析仪 (Keysight PXI VNA) ---")
    if vna_params:
        sf = vna_params.get("start_freq_hz", 0) / 1e9
        ef = vna_params.get("stop_freq_hz", 0) / 1e9
        sp = vna_params.get("s_parameter", "—")
        pwr = vna_params.get("power_dbm", [])
        pts = vna_params.get("points", "—")
        ifbw = vna_params.get("if_bandwidth_hz", "—")
        addr = vna_params.get("address", "—")
        lines.append(f"  频率范围: {sf:.1f} – {ef:.1f} GHz")
        lines.append(f"  S 参数: {sp}")
        lines.append(f"  源功率: {pwr} dBm")
        lines.append(f"  扫描点数: {pts}")
        lines.append(f"  中频带宽: {ifbw} Hz")
        lines.append(f"  地址: {addr}")
    else:
        lines.append("  (未配置)")

    # DC Power Supply (E36312A)
    lines.append("")
    lines.append(f"--- {dc_params.get('model', '直流电压源')} ---")
    ports = dc_params.get("ports", {})
    for port_num in sorted(ports.keys()):
        p = ports[port_num]
        lines.append(
            f"  Port {port_num}: "
            f"设定 {p['set_voltage']} / {p['set_current']}  |  "
            f"实测 {p['actual_voltage']} / {p['actual_current']}"
        )

    # ---- ② 日期 + 实验时长 ----
    lines.append("")
    lines.append(sep)
    lines.append("【② 日期与实验时长】")
    lines.append("")
    lines.append(f"  开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  实验时长: {hours}h {minutes}m {seconds}s")
    lines.append(f"  总测量点数: {measurement_count}")

    # ---- ③ 环境温度 ----
    lines.append("")
    lines.append(sep)
    lines.append("【③ 环境温度】")
    lines.append("")
    lines.append(f"  室温: {ambient_celsius} °C")
    lines.append(f"  室温 (开尔文): {ambient_kelvin} K")

    # ---- ④ 实测人员 ----
    lines.append("")
    lines.append(sep)
    lines.append("【④ 实测人员】")
    lines.append("")
    lines.append(f"  实测人员: {operator}")

    # ---- 版本信息 ----
    if measurement_logic_version:
        lines.append("")
        lines.append(sep)
        lines.append(f"  测量逻辑版本: {measurement_logic_version}")

    # ---- 附加信息 ----
    if extra_lines:
        lines.append("")
        lines.append(sep)
        lines.append("【附加信息】")
        lines.append("")
        for line in extra_lines:
            lines.append(f"  {line}")

    lines.append("")
    lines.append(sep)
    lines.append("")

    return lines
