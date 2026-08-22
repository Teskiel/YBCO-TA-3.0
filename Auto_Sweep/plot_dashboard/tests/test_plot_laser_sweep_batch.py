# -*- coding: utf-8 -*-
"""plot_laser_sweep_batch 批量画图脚本的单元测试。

BDD 命名: test_given_<前提>_when_<操作>_then_<预期>
"""

import numpy as np
import pytest


class TestFindActualTempDirs:
    """查找 actual_X.XXXK 目录的测试。"""

    def test_given_path_itself_is_actual_when_finding_then_returns_self(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import find_actual_temp_dirs

        d = tmp_path / "actual_6.611K"
        d.mkdir()
        result = find_actual_temp_dirs(str(d))
        assert result == [str(d)]

    def test_given_parent_with_actual_subdirs_when_finding_then_returns_all(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import find_actual_temp_dirs

        base = tmp_path / "10K"
        for name in ["actual_9.941K", "actual_10.050K"]:
            (base / name).mkdir(parents=True)
        (base / "readme.txt").write_text("ignore")

        result = find_actual_temp_dirs(str(base))
        assert len(result) == 2

    def test_given_no_actual_dirs_when_finding_then_returns_empty(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import find_actual_temp_dirs

        d = tmp_path / "empty"
        d.mkdir()
        result = find_actual_temp_dirs(str(d))
        assert result == []


class TestFindDBmFolders:
    """查找 -xxdBm 文件夹的测试。"""

    def test_given_actual_temp_dir_with_three_pv_when_finding_then_returns_three(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import find_dbm_folders

        # 模拟 actual_6.611K/ 目录
        actual_dir = tmp_path / "actual_6.611K"
        for dbm in [-25, -35, -45]:
            (actual_dir / f"{dbm}dBm").mkdir(parents=True)
        # 无关文件
        (actual_dir / "readme.txt").write_text("hello")

        result = find_dbm_folders(str(actual_dir))
        assert len(result) == 3
        paths = {r[0] for r in result}
        dbms = {r[1] for r in result}
        assert dbms == {-25, -35, -45}

    def test_given_dir_with_no_dbm_folders_when_finding_then_returns_empty(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import find_dbm_folders

        d = tmp_path / "empty"
        d.mkdir()
        result = find_dbm_folders(str(d))
        assert result == []

    def test_given_dir_with_db0_folder_when_finding_then_correctly_parsed(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import find_dbm_folders

        d = tmp_path / "actual_6.611K"
        (d / "-10dBm").mkdir(parents=True)
        (d / "-50dBm").mkdir(parents=True)

        result = find_dbm_folders(str(d))
        assert len(result) == 2
        dbms = {r[1] for r in result}
        assert dbms == {-10, -50}


class TestExtractTrFromPath:
    """从路径提取实际温度 Tr 的测试。"""

    def test_given_actual_temp_path_when_extracting_then_returns_correct_value(self):
        from plot_dashboard.plot_laser_sweep_batch import extract_tr_from_path

        tr = extract_tr_from_path(
            r"D:\data\experiment_data\20260606_092046\6K\actual_6.611K"
        )
        assert tr == pytest.approx(6.611)

    def test_given_no_actual_in_path_when_extracting_then_returns_none(self):
        from plot_dashboard.plot_laser_sweep_batch import extract_tr_from_path

        tr = extract_tr_from_path(r"D:\data\6K")
        assert tr is None

    def test_given_actual_100k_when_extracting_then_handles_three_digit(self):
        from plot_dashboard.plot_laser_sweep_batch import extract_tr_from_path

        tr = extract_tr_from_path(r"D:\data\actual_100.500K")
        assert tr == pytest.approx(100.500)


class TestExtractPlFromPath:
    """从 .s2p 文件路径提取激光功率 Pl 的测试。"""

    def test_given_s2p_in_05mW_folder_when_extracting_then_returns_5(self):
        from plot_dashboard.plot_laser_sweep_batch import extract_pl_from_path

        pl = extract_pl_from_path(
            r"D:\data\-25dBm\05mW\YBCO.s2p"
        )
        assert pl == 5

    def test_given_s2p_in_17mW_folder_when_extracting_then_returns_17(self):
        from plot_dashboard.plot_laser_sweep_batch import extract_pl_from_path

        pl = extract_pl_from_path(
            r"D:\data\-25dBm\17mW\YBCO.s2p"
        )
        assert pl == 17

    def test_given_no_mW_in_path_when_extracting_then_returns_none(self):
        from plot_dashboard.plot_laser_sweep_batch import extract_pl_from_path

        pl = extract_pl_from_path(r"D:\data\file.s2p")
        assert pl is None


class TestLoadS2P:
    """S2P 加载函数测试。"""

    def test_given_valid_s2p_when_loading_then_returns_freq_and_s21(self, tmp_path):
        from plot_dashboard.plot_laser_sweep_batch import load_s2p

        # 写最小 Touchstone 文件
        s2p = tmp_path / "test.s2p"
        s2p.write_text(
            "! Test\n# Hz S dB R 50\n"
            "3000000000 -20 0 -20 0 -20 0 -20 0\n"
            "6000000000 -25 0 -25 0 -25 0 -25 0\n"
        )

        freq, s21 = load_s2p(str(s2p))
        assert len(freq) == 2
        assert len(s21) == 2
        assert freq[0] == pytest.approx(3.0)  # GHz
        assert s21[0] == pytest.approx(-20.0)

    def test_given_corrupt_s2p_when_loading_then_returns_none(self, tmp_path):
        from plot_dashboard.plot_laser_sweep_batch import load_s2p

        s2p = tmp_path / "bad.s2p"
        s2p.write_text("garbage")

        result = load_s2p(str(s2p))
        assert result is None


class TestCollectS2PFiles:
    """收集某个 -xxdBm 文件夹下所有 .s2p 文件的测试。"""

    def test_given_dbm_folder_with_six_laser_powers_when_collecting_then_returns_six(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import collect_s2p_files

        dbm_dir = tmp_path / "-25dBm"
        for pl in [0, 1, 3, 5, 7, 9]:
            d = dbm_dir / f"{pl:02d}mW"
            d.mkdir(parents=True)
            (d / "YBCO.s2p").write_text(
                "! Test\n# Hz S dB R 50\n"
                "3000000000 -20 0 -20 0 -20 0 -20 0\n"
            )

        result = collect_s2p_files(str(dbm_dir))
        assert len(result) == 6
        # 验证每个都有 file_path, pl, freq, s21
        for item in result:
            assert "file_path" in item
            assert "pl" in item
            assert "freq" in item
            assert "s21" in item

    def test_given_empty_dbm_folder_when_collecting_then_returns_empty(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import collect_s2p_files

        dbm_dir = tmp_path / "-25dBm"
        dbm_dir.mkdir()
        result = collect_s2p_files(str(dbm_dir))
        assert result == []

    def test_given_files_sorted_by_pl_when_collecting_then_order_is_ascending(
        self, tmp_path
    ):
        from plot_dashboard.plot_laser_sweep_batch import collect_s2p_files

        dbm_dir = tmp_path / "-25dBm"
        for pl in [9, 3, 0, 7, 5, 1]:  # 乱序创建
            d = dbm_dir / f"{pl:02d}mW"
            d.mkdir(parents=True)
            (d / "YBCO.s2p").write_text(
                "! Test\n# Hz S dB R 50\n"
                "3000000000 -20 0 -20 0 -20 0 -20 0\n"
            )

        result = collect_s2p_files(str(dbm_dir))
        pl_values = [r["pl"] for r in result]
        assert pl_values == [0, 1, 3, 5, 7, 9]


class TestPlotSingleFigure:
    """单图绘制函数的测试（不显示，仅验证无异常）。"""

    def test_given_six_traces_when_plotting_then_returns_figure(self):
        from plot_dashboard.plot_laser_sweep_batch import plot_single_figure
        import matplotlib
        matplotlib.use("Agg")  # 非交互后端

        traces = [
            {"pl": 0, "freq": np.linspace(3, 6, 100), "s21": np.zeros(100) - 20},
            {"pl": 3, "freq": np.linspace(3, 6, 100), "s21": np.zeros(100) - 22},
        ]
        fig = plot_single_figure(
            traces, tr=6.611, pv=-25, output_dir=None
        )
        assert fig is not None
        import matplotlib.pyplot as plt
        plt.close(fig)

    def test_given_title_when_plotting_then_contains_tr_and_pv(self):
        from plot_dashboard.plot_laser_sweep_batch import plot_single_figure
        import matplotlib
        matplotlib.use("Agg")

        traces = [
            {"pl": 0, "freq": np.linspace(3, 6, 100), "s21": np.zeros(100) - 20},
        ]
        fig = plot_single_figure(traces, tr=6.611, pv=-25, output_dir=None)
        ax = fig.axes[0]
        title = ax.get_title()
        assert "6.611" in title
        assert "-25" in title
        import matplotlib.pyplot as plt
        plt.close(fig)
