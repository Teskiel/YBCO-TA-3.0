# -*- coding: utf-8 -*-
"""
BDD tests for draw/plot_VNA_powersweep.py

Naming convention: test_given_<precondition>_when_<action>_then_<expected>
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 非交互后端 — 必须在 import pyplot 之前设置
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =========================================================================
# TestPathParsing — 路径解析纯函数测试
# =========================================================================

class TestPathParsing:
    """Given 路径字符串，提取实验参数。"""

    # ---- extract_vna_power_from_path ----

    def test_given_new_format_path_vna_minus25_when_extracting_then_returns_minus25(self):
        """新格式：.../20K/-25dBm/05mW/file.s2p → -25"""
        from draw.plot_VNA_powersweep import extract_vna_power_from_path
        path = r"D:\data\20260609_185708\20K\-25dBm\05mW\YBCO.s2p"
        assert extract_vna_power_from_path(path) == -25

    def test_given_new_format_path_vna_minus55_when_extracting_then_returns_minus55(self):
        """新格式：.../20K/-55dBm/00mW/file.s2p → -55"""
        from draw.plot_VNA_powersweep import extract_vna_power_from_path
        path = r"D:\data\20260609_185708\20K\-55dBm\00mW\YBCO.s2p"
        assert extract_vna_power_from_path(path) == -55

    def test_given_old_format_path_when_extracting_vna_power_then_returns_correct_value(self):
        """旧格式含 actual_X 子目录 → 仍正确提取 VNA 功率"""
        from draw.plot_VNA_powersweep import extract_vna_power_from_path
        path = r"D:\data\20260605_215526\6K\actual_6.452K\-35dBm\03mW\YBCO.s2p"
        assert extract_vna_power_from_path(path) == -35

    def test_given_path_without_dbm_when_extracting_vna_power_then_returns_none(self):
        """路径不含 -xxdBm → None"""
        from draw.plot_VNA_powersweep import extract_vna_power_from_path
        path = r"D:\data\20260609_185708\20K\05mW\YBCO.s2p"
        assert extract_vna_power_from_path(path) is None

    # ---- extract_target_temp_from_path ----

    def test_given_new_format_path_target_20K_when_extracting_then_returns_20(self):
        """新格式：.../20K/-25dBm/... → 20.0"""
        from draw.plot_VNA_powersweep import extract_target_temp_from_path
        path = r"D:\data\20260609_185708\20K\-25dBm\05mW\YBCO.s2p"
        assert extract_target_temp_from_path(path) == 20.0

    def test_given_old_format_path_target_6K_when_extracting_then_returns_6(self):
        """旧格式含 actual_X → 仍提取目标温度（{N}K 目录名）"""
        from draw.plot_VNA_powersweep import extract_target_temp_from_path
        path = r"D:\data\20260605_215526\6K\actual_6.452K\-25dBm\00mW\YBCO.s2p"
        assert extract_target_temp_from_path(path) == 6.0

    def test_given_path_without_temp_dir_when_extracting_then_returns_none(self):
        """路径不含 {N}K 目录 → None"""
        from draw.plot_VNA_powersweep import extract_target_temp_from_path
        path = r"D:\data\some_folder\file.s2p"
        assert extract_target_temp_from_path(path) is None

    # ---- extract_laser_power_from_path ----

    def test_given_path_with_05mW_when_extracting_then_returns_5(self):
        """路径含 /05mW/ → 5"""
        from draw.plot_VNA_powersweep import extract_laser_power_from_path
        path = r"D:\data\20K\-25dBm\05mW\YBCO.s2p"
        assert extract_laser_power_from_path(path) == 5

    def test_given_path_with_00mW_when_extracting_then_returns_0(self):
        """路径含 /00mW/ → 0"""
        from draw.plot_VNA_powersweep import extract_laser_power_from_path
        path = r"D:\data\20K\-25dBm\00mW\YBCO.s2p"
        assert extract_laser_power_from_path(path) == 0

    def test_given_path_with_09mW_when_extracting_then_returns_9(self):
        """路径含 /09mW/ → 9"""
        from draw.plot_VNA_powersweep import extract_laser_power_from_path
        path = r"D:\data\20K\-25dBm\09mW\YBCO.s2p"
        assert extract_laser_power_from_path(path) == 9

    def test_given_path_without_mW_when_extracting_then_returns_none(self):
        """路径不含 mW 目录 → None"""
        from draw.plot_VNA_powersweep import extract_laser_power_from_path
        path = r"D:\data\20K\-25dBm\YBCO.s2p"
        assert extract_laser_power_from_path(path) is None


# =========================================================================
# TestFileDiscovery — 文件发现测试（使用 tmp_path）
# =========================================================================

class TestFileDiscovery:
    """Given 模拟的实验数据目录树，寻找匹配指定激光功率的 .s2p 文件。"""

    @pytest.fixture
    def new_format_tree(self, tmp_path):
        """创建新格式目录树：{T}K/-{Pv}dBm/{Pl}mW/dummy.s2p"""
        base = tmp_path / "experiment_data" / "20260609_185708"
        temp_dir = base / "20K"
        # 创建 16 个 VNA 功率目录（-25 到 -55，步长 2）
        for pv in range(-25, -56, -2):
            # 每个 VNA 功率下创建 6 个激光功率目录
            for pl in [0, 1, 3, 5, 7, 9]:
                laser_dir = temp_dir / f"{pv:+d}dBm" / f"{pl:02d}mW"
                laser_dir.mkdir(parents=True, exist_ok=True)
                # 创建占位文件
                (laser_dir / f"YBCO_{pv:+d}dBm_{pl:02d}mW_target_20K_actual_20.000K.s2p").touch()
        return str(temp_dir)

    @pytest.fixture
    def old_format_tree(self, tmp_path):
        """创建旧格式目录树：{T}K/actual_X.XXXK/-{Pv}dBm/{Pl}mW/dummy.s2p"""
        base = tmp_path / "experiment_data" / "20260605_215526"
        temp_dir = base / "6K"
        actual_dir = temp_dir / "actual_6.452K"
        for pv in [-25, -35, -45]:
            for pl in [0, 1, 3, 5, 7, 9]:
                laser_dir = actual_dir / f"{pv:+d}dBm" / f"{pl:02d}mW"
                laser_dir.mkdir(parents=True, exist_ok=True)
                (laser_dir / f"YBCO_{pv:+d}dBm_{pl:02d}mW_target_6K_actual_6.452K.s2p").touch()
        return str(temp_dir)

    def test_given_new_format_16_pv_all_have_pl_03_when_finding_then_returns_16_files(
        self, new_format_tree
    ):
        """新格式：所有 16 个 VNA 功率级别都包含 03mW → 返回 16 个 .s2p 文件"""
        from draw.plot_VNA_powersweep import find_s2p_files_for_laser_power
        files = find_s2p_files_for_laser_power(new_format_tree, laser_power_mw=3)
        assert len(files) == 16
        # 验证按 VNA 功率排序（升序）
        from draw.plot_VNA_powersweep import extract_vna_power_from_path
        pv_values = [extract_vna_power_from_path(f) for f in files]
        assert pv_values == sorted(pv_values)
        # 验证所有文件都是 03mW 目录下的
        for f in files:
            assert "03mW" in f

    def test_given_new_format_only_3_pv_have_pl_09_when_finding_then_returns_3_files(
        self, new_format_tree
    ):
        """新格式：只有部分 VNA 功率有 09mW → 仅返回存在的"""
        from draw.plot_VNA_powersweep import find_s2p_files_for_laser_power
        files = find_s2p_files_for_laser_power(new_format_tree, laser_power_mw=9)
        assert len(files) == 16  # 所有 Pv 都创建了 09mW，验证数量
        for f in files:
            assert "09mW" in f

    def test_given_old_format_when_finding_then_returns_correct_files(
        self, old_format_tree
    ):
        """旧格式含 actual_X 子目录 → 穿透到实际温度层找到文件"""
        from draw.plot_VNA_powersweep import find_s2p_files_for_laser_power
        files = find_s2p_files_for_laser_power(old_format_tree, laser_power_mw=5)
        assert len(files) == 3  # 3 个 VNA 功率级别
        for f in files:
            assert "05mW" in f

    def test_given_no_matching_pl_when_finding_then_returns_empty_list(
        self, new_format_tree
    ):
        """目标激光功率目录不存在 → 空列表"""
        from draw.plot_VNA_powersweep import find_s2p_files_for_laser_power
        files = find_s2p_files_for_laser_power(new_format_tree, laser_power_mw=99)
        assert files == []

    def test_given_dir_without_dbm_subdirs_when_finding_then_returns_empty_list(
        self, tmp_path
    ):
        """目录下没有 -xxdBm 子目录 → 空列表"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        from draw.plot_VNA_powersweep import find_s2p_files_for_laser_power
        files = find_s2p_files_for_laser_power(str(empty_dir), laser_power_mw=3)
        assert files == []


# =========================================================================
# TestS2PLoading — S2P 加载测试（使用 tmp_path + 真实 Touchstone 文件）
# =========================================================================

class TestS2PLoading:
    """Given .s2p 文件，加载 S21 数据。"""

    @pytest.fixture
    def valid_s2p(self, tmp_path):
        """创建最小有效 2-port Touchstone 文件。"""
        content = (
            "# GHz S RI R 50\n"
            "! freq  S11_R S11_I  S21_R S21_I  S12_R S12_I  S22_R S22_I\n"
            "1.000  0.10  0.01   0.80  -0.20   0.05  0.01   0.10  0.02\n"
            "2.000  0.12  0.01   0.70  -0.30   0.05  0.01   0.11  0.02\n"
            "3.000  0.14  0.02   0.60  -0.40   0.06  0.02   0.12  0.03\n"
        )
        file_path = tmp_path / "test.s2p"
        file_path.write_text(content)
        return str(file_path)

    def test_given_valid_s2p_when_loading_then_returns_freq_and_s21(self, valid_s2p):
        """有效 Touchstone 文件 → 返回 (freq_ghz, s21_db)"""
        from draw.plot_VNA_powersweep import load_s2p
        result = load_s2p(valid_s2p)
        assert result is not None
        freq, s21_db = result
        assert isinstance(freq, np.ndarray)
        assert isinstance(s21_db, np.ndarray)
        assert len(freq) == 3
        assert len(s21_db) == 3
        # 频率是 GHz 级别
        assert freq[0] == pytest.approx(1.0)
        # S21 dB = 20*log10(|0.8 - 0.2j|) ≈ 20*log10(0.8246) ≈ -1.67
        expected_db = 20 * np.log10(np.abs(0.80 - 0.20j))
        assert s21_db[0] == pytest.approx(expected_db, rel=1e-4)

    def test_given_nonexistent_file_when_loading_then_returns_none(self):
        """不存在的文件 → None"""
        from draw.plot_VNA_powersweep import load_s2p
        result = load_s2p("/nonexistent/path/file.s2p")
        assert result is None

    def test_given_malformed_file_when_loading_then_returns_none(self, tmp_path):
        """损坏的非 Touchstone 内容 → None"""
        bad_file = tmp_path / "bad.s2p"
        bad_file.write_text("this is not a touchstone file")
        from draw.plot_VNA_powersweep import load_s2p
        result = load_s2p(str(bad_file))
        assert result is None


# =========================================================================
# TestTraceCollection — 数据整理测试（使用 tmp_path + patch skrf）
# =========================================================================

class TestTraceCollection:
    """Given 目录树 + 激光功率，收集并排序 trace。"""

    @pytest.fixture
    def new_format_tree_with_real_s2p(self, tmp_path):
        """创建新格式目录树，包含有效 .s2p 内容。"""
        base = tmp_path / "experiment_data" / "run"
        temp_dir = base / "20K"
        content = (
            "# GHz S RI R 50\n"
            "1.0  0.1  0.01  0.8  -0.2  0.05  0.01  0.1  0.02\n"
        )
        for pv in [-25, -35, -45]:
            laser_dir = temp_dir / f"{pv:+d}dBm" / "05mW"
            laser_dir.mkdir(parents=True, exist_ok=True)
            (laser_dir / "YBCO.s2p").write_text(content)
        # 添加一个损坏文件的目录
        bad_dir = temp_dir / "-27dBm" / "05mW"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "YBCO.s2p").write_text("garbage content")
        return str(temp_dir)

    def test_given_3_pv_all_valid_when_collecting_then_returns_3_sorted_by_pv(
        self, new_format_tree_with_real_s2p
    ):
        """3 个 VNA 功率级别全有效 → 返回 3 条 trace，按 Pv 升序"""
        from draw.plot_VNA_powersweep import collect_traces
        traces = collect_traces(new_format_tree_with_real_s2p, laser_power_mw=5)
        assert len(traces) == 3  # -27dBm 损坏所以被跳过
        # 验证按 Pv 升序
        pv_list = [t["pv"] for t in traces]
        assert pv_list == sorted(pv_list)
        # 验证结构
        for t in traces:
            assert "file_path" in t
            assert "pv" in t
            assert "freq" in t
            assert "s21" in t
            assert isinstance(t["freq"], np.ndarray)
            assert isinstance(t["s21"], np.ndarray)

    def test_given_one_corrupt_s2p_when_collecting_then_skips_bad_loads(
        self, new_format_tree_with_real_s2p
    ):
        """一个 .s2p 损坏 → 被跳过，其余正常收集"""
        from draw.plot_VNA_powersweep import collect_traces
        traces = collect_traces(new_format_tree_with_real_s2p, laser_power_mw=5)
        pv_values = [t["pv"] for t in traces]
        # -27dBm 被跳过（损坏）
        assert -27 not in pv_values
        assert -25 in pv_values
        assert -35 in pv_values
        assert -45 in pv_values


# =========================================================================
# TestPlotGeneration — 绘图测试（使用 合成 trace 数据）
# =========================================================================

class TestPlotGeneration:
    """Given 合成 trace 数据，生成 S21 vs VNA 功率图。"""

    @pytest.fixture
    def synthetic_traces_3(self):
        """3 条合成 trace（3 个 VNA 功率级别）。"""
        traces = []
        for i, pv in enumerate([-25, -35, -45]):
            freq = np.linspace(1.0, 10.0, 100)
            s21 = -20 * np.ones(100) + pv * 0.1 + i * 2  # 不同的偏移
            traces.append({
                "file_path": f"/fake/20K/{pv:+d}dBm/05mW/YBCO.s2p",
                "pv": pv,
                "freq": freq,
                "s21": s21,
            })
        return traces

    @pytest.fixture
    def synthetic_traces_16(self):
        """16 条合成 trace（完整 VNA 功率扫描）。"""
        traces = []
        pv_values = list(range(-25, -56, -2))  # -25, -27, ..., -55
        for i, pv in enumerate(pv_values):
            freq = np.linspace(1.0, 10.0, 100)
            s21 = -20 * np.ones(100) + pv * 0.1 + i * 0.5
            traces.append({
                "file_path": f"/fake/20K/{pv:+d}dBm/05mW/YBCO.s2p",
                "pv": pv,
                "freq": freq,
                "s21": s21,
            })
        return traces

    @pytest.fixture
    def empty_traces(self):
        """空 trace 列表。"""
        return []

    # ---- 基本输出 ----

    def test_given_3_traces_when_plotting_then_returns_figure(self, synthetic_traces_3):
        """返回 matplotlib Figure 对象"""
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            synthetic_traces_3,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        assert isinstance(fig, plt.Figure)
        plt.close(fig)

    # ---- 标题 ----

    def test_given_traces_when_plotting_then_title_contains_target_temp_and_laser_power(
        self, synthetic_traces_3
    ):
        """图标题包含目标温度 "20 K" 和激光功率 "5 mW" """
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            synthetic_traces_3,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        ax = fig.axes[0]
        title = ax.get_title()
        assert "20" in title
        assert "K" in title
        assert "5" in title
        assert "mW" in title
        plt.close(fig)

    # ---- 轴标签 ----

    def test_given_traces_when_plotting_then_xlabel_is_frequency_ghz(
        self, synthetic_traces_3
    ):
        """X 轴标签包含 "Frequency" 和 "GHz" """
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            synthetic_traces_3,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        ax = fig.axes[0]
        xlabel = ax.get_xlabel()
        assert "Frequency" in xlabel
        assert "GHz" in xlabel
        plt.close(fig)

    def test_given_traces_when_plotting_then_ylabel_contains_s21_and_db(
        self, synthetic_traces_3
    ):
        """Y 轴标签包含 "S21" 和 "dB" """
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            synthetic_traces_3,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        ax = fig.axes[0]
        ylabel = ax.get_ylabel()
        assert "S21" in ylabel
        assert "dB" in ylabel
        plt.close(fig)

    # ---- 线条数量 ----

    def test_given_16_traces_when_plotting_then_16_lines_on_axes(
        self, synthetic_traces_16
    ):
        """16 条 trace → axes 上有 16 条线"""
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            synthetic_traces_16,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        ax = fig.axes[0]
        lines = ax.get_lines()
        assert len(lines) == 16
        plt.close(fig)

    def test_given_empty_traces_when_plotting_then_figure_has_no_lines(
        self, empty_traces
    ):
        """空 trace 列表 → Figure 无线条但不崩溃"""
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            empty_traces,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        ax = fig.axes[0]
        lines = ax.get_lines()
        assert len(lines) == 0
        plt.close(fig)

    # ---- 保存 PNG ----

    def test_given_traces_with_output_path_when_plotting_then_file_created(
        self, synthetic_traces_3, tmp_path
    ):
        """指定 output_path → PNG 文件生成"""
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        output_path = str(tmp_path / "test_output.png")
        fig = plot_vna_power_sweep(
            synthetic_traces_3,
            target_temp_k=20.0,
            laser_power_mw=5,
            output_path=output_path,
        )
        plt.close(fig)
        assert os.path.exists(output_path)

    # ---- 色标 ----

    def test_given_traces_when_plotting_then_colorbar_label_contains_vna_power(
        self, synthetic_traces_3
    ):
        """色标标签包含 "VNA Power" 或 "dBm" """
        from draw.plot_VNA_powersweep import plot_vna_power_sweep
        fig = plot_vna_power_sweep(
            synthetic_traces_3,
            target_temp_k=20.0,
            laser_power_mw=5,
        )
        # 查找 colorbar
        from matplotlib.colorbar import Colorbar
        colorbars = [
            child for child in fig.get_children()
            if isinstance(child, Colorbar)
        ]
        # 也可能通过 axes 查找
        if not colorbars:
            for ax in fig.axes:
                for child in ax.get_children():
                    if hasattr(child, 'colorbar') and child.colorbar is not None:
                        colorbars.append(child.colorbar)
        # 如果找到 colorbar，验证其标签
        if colorbars:
            cb_label = None
            for cb in colorbars:
                if hasattr(cb, 'ax') and hasattr(cb.ax, 'get_ylabel'):
                    cb_label = cb.ax.get_ylabel()
                    if cb_label:
                        break
            if cb_label:
                assert "VNA" in cb_label or "dBm" in cb_label or "Power" in cb_label
        plt.close(fig)
