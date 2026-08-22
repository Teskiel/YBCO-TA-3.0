# -*- coding: utf-8 -*-
"""
BDD 测试 — readme.txt 数据头部自动生成器

验证每次测量完成后自动生成的 readme.txt 内容完整性，
符合需求文档附录的数据头部规范。

被测模块: readme_generator.py

命名规范: test_given_<前置条件>_when_<动作>_then_<预期结果>
"""

import os
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from readme_generator import generate_readme, OPERATOR


# =========================================================================
# Helpers
# =========================================================================

def _sample_laser_params():
    return {"wavelength_nm": 1550.0, "power_sequence_mw": [0, 1, 3, 5, 7, 9]}

def _sample_lakeshore_params():
    return {"setpoint_k": 30.0, "pid": {"p": 100, "i": 0, "d": 0}, "heater_range": 2}

def _sample_vna_params():
    return {
        "start_freq_hz": 3_000_000_000.0,
        "stop_freq_hz": 6_000_000_000.0,
        "s_parameter": "S21",
        "power_dbm": [-45, -35, -25],
        "points": 50001,
        "if_bandwidth_hz": 10000,
    }


# =========================================================================
# TestClass: readme 内容完整性
# =========================================================================

class TestReadmeContentCompleteness:
    """验证 readme.txt 必须包含的所有四个部分的字段。"""

    def test_given_full_params_when_generating_readme_then_contains_all_sections(
        self
    ):
        """readme.txt 应包含①②③④四个部分的标题。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 14, 30, 0),
                end_time=datetime(2026, 6, 8, 16, 45, 30),
                laser_params=_sample_laser_params(),
                lakeshore_params=_sample_lakeshore_params(),
                vna_params=_sample_vna_params(),
                operator="胡洁",
                ambient_celsius=23.5,
                measurement_count=24,
                measurement_logic_version="2026-06-08",
            )

            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # ① 设备参数
            assert "设备基本参数" in content
            assert "Keysight N7779C" in content
            assert "LakeShore 335" in content
            assert "PXI VNA" in content
            assert "E36312A" in content

            # ② 日期 + 时长
            assert "日期与实验时长" in content
            assert "2026-06-08 14:30:00" in content
            assert "2026-06-08 16:45:30" in content

            # ③ 环境温度
            assert "环境温度" in content
            assert "23.5" in content
            # 23.5 + 273.15 = 296.65 → round 后为 296.6 或 296.7（浮点精度）
            assert "296.6" in content or "296.7" in content

            # ④ 实测人员
            assert "实测人员" in content
            assert "胡洁" in content

    def test_given_measurement_logic_version_when_generating_then_version_in_content(
        self
    ):
        """版本号应写入 readme 底部。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                measurement_logic_version="2026-06-08",
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "2026-06-08" in content

    def test_given_pid_params_when_generating_then_pid_recorded(self):
        """PID 参数应完整记录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                lakeshore_params={"setpoint_k": 30.0, "pid": {"p": 100, "i": 5, "d": 0}, "heater_range": 2},
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "P=100" in content
            assert "I=5" in content
            assert "D=0" in content

    def test_given_vna_settings_when_generating_then_freq_if_bandwidth_recorded(
        self
    ):
        """频率范围、扫描点数、中频带宽应记录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                vna_params=_sample_vna_params(),
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "3.0" in content
            assert "6.0" in content
            assert "S21" in content
            assert "50001" in content
            assert "10000" in content

    def test_given_empty_optional_params_when_generating_then_placeholder_not_error(
        self
    ):
        """可选参数缺失时应使用占位符，不崩溃。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                # 不传 laser/lakeshore/vna params
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "(未配置)" in content

    def test_given_retry_info_when_generating_then_extra_lines_included(self):
        """重测信息等附加内容应出现在附加信息节。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                extra_lines=[
                    "重测次数: 2",
                    "重测原因: S21 偏差超阈值 (0.8dB > 0.5dB)",
                ],
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "附加信息" in content
            assert "重测次数: 2" in content
            assert "S21 偏差超阈值" in content


# =========================================================================
# TestClass: readme 输出格式
# =========================================================================

class TestReadmeOutputFormat:
    """验证 readme.txt 的输出格式。"""

    def test_given_readme_when_written_then_utf8_encoded(self):
        """readme.txt 应为 UTF-8 编码。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                operator="赵思源",  # 中文名测试
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "赵思源" in content

    def test_given_output_dir_when_writing_then_placed_in_correct_folder(self):
        """readme.txt 应放在指定的 output_dir 根目录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
            )
            assert os.path.dirname(path) == tmpdir
            assert os.path.basename(path) == "readme.txt"
            assert os.path.exists(path)


# =========================================================================
# TestClass: 边界情况
# =========================================================================

class TestReadmeEdgeCases:
    """验证 readme 生成的边界情况。"""

    def test_given_very_long_operator_name_when_generating_then_not_truncated(self):
        """长名字不应被截断。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            long_name = "非常长的操作人员姓名测试"
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                operator=long_name,
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert long_name in content

    def test_given_negative_ambient_temp_when_generating_then_handled(self):
        """负环境温度应正确处理（开尔文转换）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                ambient_celsius=-5.0,
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "-5.0" in content
            # -5.0 + 273.15 = 268.15 → round 后为 268.1 或 268.2（浮点精度）
            assert "268.1" in content or "268.2" in content

    def test_given_large_point_count_when_generating_then_readable(self):
        """50001+ 点数应可读显示。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
                vna_params={"points": 50001, "power_dbm": [-45]},
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "50001" in content

    def test_given_default_operator_when_not_specified_then_zhao_siyuan(self):
        """未指定人员时默认为赵思源。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert OPERATOR in content

    def test_given_e36312a_params_when_generating_then_all_three_ports_listed(self):
        """E36312A 三个端口参数应全部列出。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "Port 1" in content
            assert "Port 2" in content
            assert "Port 3" in content
            assert "2.400 V" in content
            assert "0.022 A" in content
            assert "-0.016 mA" in content

    def test_given_ambient_temp_random_when_not_specified_then_between_18_to_25_celsius(
        self
    ):
        """未指定环境温度时随机生成 18-25°C 之间的值。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = generate_readme(
                output_dir=tmpdir,
                start_time=datetime(2026, 6, 8, 12, 0, 0),
            )
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # 提取室温数值
            import re
            match = re.search(r"室温: ([\d.]+) °C", content)
            assert match is not None
            celsius = float(match.group(1))
            assert 18.0 <= celsius <= 25.0
            # 验证开尔文转换
            match_k = re.search(r"室温 \(开尔文\): ([\d.]+) K", content)
            assert match_k is not None
            kelvin = float(match_k.group(1))
            assert abs(kelvin - (celsius + 273.15)) < 0.2
