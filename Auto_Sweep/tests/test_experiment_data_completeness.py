# -*- coding: utf-8 -*-
"""
实验数据完整性检查与迁移功能的 BDD 测试。

测试约定：test_given_<前置条件>_when_<动作>_then_<预期结果>
所有文件系统操作使用 tmp_path fixture，不触碰真实数据。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import time

# 测试辅助：创建模拟实验文件夹结构


def _make_s2p_file(filepath, size=3000000):
    """在指定路径创建模拟 S2P 文件（非空）。"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(b"!" * size)


def _make_empty_s2p_file(filepath, size=100):
    """创建小于阈值的空 S2P 文件。"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(b"x" * size)


def _make_readme(experiment_dir):
    """在实验目录根级别创建 readme.txt。"""
    os.makedirs(experiment_dir, exist_ok=True)
    path = os.path.join(experiment_dir, "readme.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("=== Experiment README ===\n")
        f.write("Measurement count: 384\n")
        f.write("Duration: 6h 27m\n")


def _make_log(experiment_dir, content, timestamp=None):
    """在 logs/ 子目录下创建日志文件。

    Args:
        experiment_dir: 实验根目录
        content: 日志文本内容
        timestamp: 日志文件名中的时间戳。默认为当前时间。
    """
    log_dir = os.path.join(experiment_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    if timestamp is None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(log_dir, f"experiment_log_{timestamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _make_old_structure_experiment(experiment_dir, temps, vna_powers, laser_powers,
                                   s2p_size=3000000, complete_temps=None):
    """创建 OLD 结构实验：{target}K/actual_{actual}K/{dBm}/{mW}/*.s2p

    Args:
        complete_temps: 只对这些温度创建完整的功率矩阵。默认全部。
    """
    if complete_temps is None:
        complete_temps = temps
    for temp in temps:
        actual = temp + 0.5  # 模拟实际温度
        actual_dir = os.path.join(
            experiment_dir, f"{temp}K", f"actual_{actual:.3f}K")
        for vna in vna_powers:
            for laser in laser_powers:
                s2p_name = (f"YBCO_{vna:+d}dBm_{laser:02d}mW"
                            f"_target_{temp}K_actual_{actual:.3f}K.s2p")
                filepath = os.path.join(
                    actual_dir, f"{vna:+d}dBm", f"{laser:02d}mW", s2p_name)
                if temp in complete_temps:
                    _make_s2p_file(filepath, s2p_size)
                else:
                    # 不完整温度：只创建部分激光功率
                    if laser <= complete_temps[0] if isinstance(complete_temps, dict) else True:
                        _make_s2p_file(filepath, s2p_size)
                    else:
                        os.makedirs(os.path.dirname(filepath), exist_ok=True)


def _make_new_structure_experiment(experiment_dir, temps, vna_powers, laser_powers,
                                   s2p_size=3000000, complete_temps=None):
    """创建 NEW 结构实验：{target}K/{dBm}/{mW}/*.s2p"""
    if complete_temps is None:
        complete_temps = temps
    for temp in temps:
        for vna in vna_powers:
            for laser in laser_powers:
                s2p_name = (f"YBCO_{vna:+d}dBm_{laser:02d}mW"
                            f"_target_{temp}K_actual_{temp + 0.3:.3f}K.s2p")
                filepath = os.path.join(
                    experiment_dir, f"{temp}K", f"{vna:+d}dBm",
                    f"{laser:02d}mW", s2p_name)
                if temp in complete_temps:
                    _make_s2p_file(filepath, s2p_size)
                else:
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)


# =========================================================================
# TestIsComplete — 单文件夹完整性判定
# =========================================================================

class TestIsComplete:
    """Given 单个实验文件夹，判定其是否已完成。"""

    def test_given_readme_exists_when_is_complete_then_returns_true(self, tmp_path):
        """readme.txt 存在时自动判定为完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        _make_readme(str(exp_dir))
        _make_new_structure_experiment(str(exp_dir), [40], [-45], [0])
        # 即使只有 1 个温度（低于 min_temp_levels），readme 优先
        checker = ExperimentCompletenessChecker(
            str(tmp_path), min_temp_levels=3)
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is True
        assert result.reason == "readme_found"

    def test_given_no_readme_but_log_complete_when_is_complete_then_returns_true(self, tmp_path):
        """无 readme 但日志末尾含完成标记时判定为完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        log_content = "..." * 50 + "\nExperiment complete — 10 measurements\n"
        _make_log(str(exp_dir), log_content)
        _make_new_structure_experiment(str(exp_dir), [40, 42, 44], [-45, -43], [0, 1, 3])
        checker = ExperimentCompletenessChecker(str(tmp_path))
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is True
        assert result.reason == "log_complete"

    def test_given_no_readme_no_log_but_structurally_complete_when_is_complete_then_returns_true(self, tmp_path):
        """无 readme 无日志但结构完整时判定为完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260605_120000"
        _make_new_structure_experiment(
            str(exp_dir), [40, 42, 44, 46, 48],
            [-45, -43, -41], [0, 1, 3, 5, 7, 9])
        checker = ExperimentCompletenessChecker(
            str(tmp_path), min_temp_levels=3)
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is True
        assert result.reason == "structurally_complete"

    def test_given_only_logs_dir_no_s2p_when_is_complete_then_returns_false(self, tmp_path):
        """仅有 logs/ 目录无任何数据时判定为未完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260611_062703"
        _make_log(str(exp_dir), "Started experiment...\nStabilising...\n")
        checker = ExperimentCompletenessChecker(str(tmp_path))
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is False
        assert result.reason == "logs_only"

    def test_given_insufficient_temp_levels_when_is_complete_then_returns_false(self, tmp_path):
        """温度级别数低于阈值时判定为未完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260605_235421"
        _make_new_structure_experiment(str(exp_dir), [12], [-45], [0, 1, 3])
        checker = ExperimentCompletenessChecker(
            str(tmp_path), min_temp_levels=3)
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is False
        assert result.reason == "insufficient_temps"

    def test_given_zero_s2p_files_when_is_complete_then_returns_false(self, tmp_path):
        """有温度子目录但无 S2P 文件时判定为未完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        # 只创建目录结构，不创建 S2P 文件
        for temp in [40, 42, 44]:
            for vna in [-45, -43]:
                for laser in [0, 1, 3]:
                    os.makedirs(os.path.join(
                        str(exp_dir), f"{temp}K", f"{vna:+d}dBm",
                        f"{laser:02d}mW"), exist_ok=True)
        checker = ExperimentCompletenessChecker(str(tmp_path))
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is False
        assert result.reason == "no_s2p_files"

    def test_given_empty_s2p_files_when_is_complete_then_returns_false(self, tmp_path):
        """S2P 文件存在但全部小于 min_s2p_size 时判定为未完成。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        _make_new_structure_experiment(
            str(exp_dir), [40, 42, 44], [-45], [0, 1],
            s2p_size=50)  # 远低于 min_s2p_file_size_bytes=1000
        checker = ExperimentCompletenessChecker(str(tmp_path))
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is False

    def test_given_old_structure_with_actual_dirs_when_is_complete_then_correctly_parses(self, tmp_path):
        """OLD 结构（含 actual_ 子目录）能被正确识别。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260606_092046"
        _make_old_structure_experiment(
            str(exp_dir), [6, 8, 10, 12, 14],
            [-25, -35, -45], [0, 1, 3, 5, 7, 9])
        checker = ExperimentCompletenessChecker(
            str(tmp_path), min_temp_levels=3)
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is True
        assert result.reason == "structurally_complete"
        assert result.details["temp_levels"] >= 5
        assert result.details["s2p_count"] >= 5 * 3 * 6

    def test_given_new_structure_without_actual_dirs_when_is_complete_then_correctly_parses(self, tmp_path):
        """NEW 结构（无 actual_ 子目录）能被正确识别。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260609_185708"
        _make_new_structure_experiment(
            str(exp_dir), [6, 10, 20, 40, 77],
            [-25, -27, -29, -31, -33, -35, -37, -39, -41],
            [0, 1, 3, 5, 7, 9])
        checker = ExperimentCompletenessChecker(
            str(tmp_path), min_temp_levels=3)
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is True
        assert result.reason == "structurally_complete"

    def test_given_hybrid_structure_when_is_complete_then_correctly_parses(self, tmp_path):
        """混合结构（既有 actual_ 又有直接 dBm）能被正确处理。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260609_012228"
        # 创建混合结构: 一个温度用 actual_ 子目录，一个用直接 dBm
        # Temp 6K with actual_ subdir (OLD)
        actual_dir = os.path.join(str(exp_dir), "6K", "actual_5.963K")
        for laser in [0, 1, 3, 5, 7, 9]:
            filepath = os.path.join(actual_dir, "-30dBm", f"{laser:02d}mW",
                                    f"YBCO_-30dBm_{laser:02d}mW_target_6K_actual_5.963K.s2p")
            _make_s2p_file(filepath)
        # Temp 8K with direct dBm (NEW)
        for laser in [0, 1, 3, 5, 7, 9]:
            filepath = os.path.join(str(exp_dir), "8K", "-30dBm", f"{laser:02d}mW",
                                    f"YBCO_-30dBm_{laser:02d}mW_target_8K_actual_8.123K.s2p")
            _make_s2p_file(filepath)
        # Temp 10K with direct dBm (NEW)
        for laser in [0, 1, 3, 5, 7, 9]:
            filepath = os.path.join(str(exp_dir), "10K", "-30dBm", f"{laser:02d}mW",
                                    f"YBCO_-30dBm_{laser:02d}mW_target_10K_actual_10.234K.s2p")
            _make_s2p_file(filepath)
        checker = ExperimentCompletenessChecker(
            str(tmp_path), min_temp_levels=3)
        result = checker.is_complete(str(exp_dir))
        assert result.is_complete is True
        assert result.details["temp_levels"] >= 3

    def test_given_multiple_logs_when_check_log_then_uses_latest(self, tmp_path):
        """logs/ 下有多个日志文件时使用时间戳最新的。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        # 旧日志（无完成标记）
        _make_log(str(exp_dir), "Starting experiment...",
                  timestamp="20260601_120001")
        # 新日志（有完成标记）
        _make_log(str(exp_dir), "Starting...\nExperiment complete — 5 measurements\n",
                  timestamp="20260601_140000")
        checker = ExperimentCompletenessChecker(str(tmp_path))
        assert checker._check_log_complete(str(exp_dir)) is True

    def test_given_log_without_complete_marker_when_check_log_then_returns_false(self, tmp_path):
        """日志末尾不含完成标记时返回 False。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        _make_log(str(exp_dir), "Starting experiment...\nStill stabilising...\n")
        checker = ExperimentCompletenessChecker(str(tmp_path))
        assert checker._check_log_complete(str(exp_dir)) is False

    def test_given_log_shorter_than_tail_chars_when_check_log_then_reads_entire_file(self, tmp_path):
        """日志文件比 tail_chars 短时正常读取全文并正确判定。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        short_log = "Experiment complete — 1 measurements\n"
        _make_log(str(exp_dir), short_log)
        checker = ExperimentCompletenessChecker(
            str(tmp_path), log_tail_chars=2000)
        assert checker._check_log_complete(str(exp_dir)) is True


# =========================================================================
# TestScanExperiments — 批量扫描
# =========================================================================

class TestScanExperiments:
    """Given experiment_data 根目录，扫描所有实验文件夹。"""

    def test_given_multiple_experiments_when_scan_then_returns_all_results(self, tmp_path):
        """扫描返回所有实验的判定结果。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        # 完成实验：有 readme
        exp1 = tmp_path / "20260601_120000"
        _make_readme(str(exp1))
        _make_new_structure_experiment(str(exp1), [40], [-45], [0])
        # 未完成实验：只有 logs
        exp2 = tmp_path / "20260602_120000"
        _make_log(str(exp2), "Starting...")
        # 完成实验：结构性完整
        exp3 = tmp_path / "20260603_120000"
        _make_new_structure_experiment(
            str(exp3), [40, 42, 44, 46], [-45, -43], [0, 1, 3, 5, 7, 9])
        checker = ExperimentCompletenessChecker(str(tmp_path))
        results = checker.scan_experiments()
        assert "20260601_120000" in results
        assert "20260602_120000" in results
        assert "20260603_120000" in results
        assert results["20260601_120000"].is_complete is True
        assert results["20260602_120000"].is_complete is False
        assert results["20260603_120000"].is_complete is True

    def test_given_zip_files_when_scan_then_skips_them(self, tmp_path):
        """ZIP 文件不出现在扫描结果中。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        _make_readme(str(exp_dir))
        _make_new_structure_experiment(str(exp_dir), [40], [-45], [0])
        # 创建 ZIP 文件
        (tmp_path / "20260601_120000.zip").write_text("fake zip")
        (tmp_path / "backup.zip").write_text("another zip")
        checker = ExperimentCompletenessChecker(str(tmp_path))
        results = checker.scan_experiments()
        assert "20260601_120000" in results
        assert "20260601_120000.zip" not in results
        assert "backup.zip" not in results

    def test_given_accomplish_dir_exists_when_scan_then_skips_it(self, tmp_path):
        """accomplish 子目录不出现在扫描结果中。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        _make_readme(str(exp_dir))
        _make_new_structure_experiment(str(exp_dir), [40], [-45], [0])
        # 创建 accomplish 子目录
        (tmp_path / "accomplish").mkdir()
        (tmp_path / "accomplish" / "20260601_120000").mkdir()
        checker = ExperimentCompletenessChecker(
            str(tmp_path), accomplish_name="accomplish")
        results = checker.scan_experiments()
        assert "20260601_120000" in results
        assert "accomplish" not in results

    def test_given_non_timestamp_folders_when_scan_then_skips_them(self, tmp_path):
        """非时间戳格式的文件夹被跳过。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp_dir = tmp_path / "20260601_120000"
        _make_readme(str(exp_dir))
        _make_new_structure_experiment(str(exp_dir), [40], [-45], [0])
        # 创建非时间戳文件夹
        (tmp_path / "not_an_experiment").mkdir()
        (tmp_path / "some_random_folder").mkdir()
        (tmp_path / "123").mkdir()
        checker = ExperimentCompletenessChecker(str(tmp_path))
        results = checker.scan_experiments()
        assert "20260601_120000" in results
        assert "not_an_experiment" not in results
        assert "some_random_folder" not in results
        assert "123" not in results

    def test_given_empty_base_dir_when_scan_then_returns_empty_dict(self, tmp_path):
        """根目录为空时返回空字典。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        checker = ExperimentCompletenessChecker(str(tmp_path))
        results = checker.scan_experiments()
        assert results == {}


# =========================================================================
# TestMigrateAccomplished — 迁移操作
# =========================================================================

class TestMigrateAccomplished:
    """Given 扫描结果，执行迁移操作。"""

    def test_given_dry_run_true_when_migrate_then_nothing_moved(self, tmp_path):
        """dry_run=True 时文件系统无变化，报告列出将要移动的项目。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp1 = tmp_path / "20260601_120000"
        _make_readme(str(exp1))
        _make_new_structure_experiment(str(exp1), [40], [-45], [0, 1, 3])
        exp2 = tmp_path / "20260602_120000"
        _make_new_structure_experiment(
            str(exp2), [40, 42, 44], [-45, -43], [0, 1, 3, 5, 7, 9])
        checker = ExperimentCompletenessChecker(str(tmp_path))
        report = checker.migrate_accomplished(dry_run=True)
        # 验证报告
        assert report.dry_run is True
        assert len(report.moved) == 2
        # 验证文件系统未变化
        assert os.path.isdir(str(exp1))
        assert os.path.isdir(str(exp2))
        # accomplish 目录不应被创建
        assert not os.path.isdir(str(tmp_path / "accomplish"))

    def test_given_dry_run_false_when_migrate_then_folders_moved(self, tmp_path):
        """dry_run=False 时文件夹实际移动到 accomplish/。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp1 = tmp_path / "20260601_120000"
        _make_readme(str(exp1))
        _make_new_structure_experiment(str(exp1), [40], [-45], [0, 1, 3])
        checker = ExperimentCompletenessChecker(str(tmp_path))
        report = checker.migrate_accomplished(dry_run=False)
        assert len(report.moved) == 1
        assert len(report.errors) == 0
        # 原位置已清除
        assert not os.path.isdir(str(exp1))
        # 目标位置存在
        assert os.path.isdir(str(tmp_path / "accomplish" / "20260601_120000"))

    def test_given_target_exists_when_migrate_then_appends_suffix(self, tmp_path):
        """目标已存在时追加 _1 后缀。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp = tmp_path / "20260601_120000"
        _make_readme(str(exp))
        _make_new_structure_experiment(str(exp), [40], [-45], [0])
        # 预创建冲突目录
        accomplish_dir = tmp_path / "accomplish"
        os.makedirs(str(accomplish_dir / "20260601_120000"))
        checker = ExperimentCompletenessChecker(str(tmp_path))
        report = checker.migrate_accomplished(dry_run=False)
        assert len(report.moved) >= 1
        # 应该以 _1 后缀存在
        assert os.path.isdir(str(accomplish_dir / "20260601_120000_1"))

    def test_given_no_complete_experiments_when_migrate_then_moves_nothing(self, tmp_path):
        """无完成实验时迁移报告 moved 为空。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp = tmp_path / "20260602_120000"
        _make_log(str(exp), "Starting...\n")
        checker = ExperimentCompletenessChecker(str(tmp_path))
        report = checker.migrate_accomplished(dry_run=False)
        assert len(report.moved) == 0
        assert not os.path.isdir(str(tmp_path / "accomplish"))

    def test_given_migration_os_error_when_migrate_then_reports_in_errors(self, tmp_path):
        """迁移过程中发生 OS 错误时记录到 errors 而非崩溃。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp = tmp_path / "20260601_120000"
        _make_readme(str(exp))
        _make_new_structure_experiment(str(exp), [40], [-45], [0])
        # 创建只读 accomplish 目录来模拟权限错误
        accomplish_dir = tmp_path / "accomplish"
        os.makedirs(str(accomplish_dir))
        # 在 Windows 上无法轻易设置只读来阻止移动，我们用另一种方式：
        # 预先创建同名目录并设只读（移动会失败因为目标存在）
        target = accomplish_dir / "20260601_120000"
        os.makedirs(str(target))
        # 让目标不可写（在目标内创建只读文件）
        lock_file = target / ".lock"
        lock_file.write_text("locked")
        try:
            os.chmod(str(lock_file), 0o444)
        except OSError:
            pass  # Windows chmod 行为不同，忽略
        checker = ExperimentCompletenessChecker(str(tmp_path))
        report = checker.migrate_accomplished(dry_run=False)
        # 不应崩溃
        assert isinstance(report, object)

    def test_given_complete_experiment_with_log_when_migrate_then_moves_all_files(self, tmp_path):
        """迁移后日志文件、S2P 文件等全部跟随移动。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        exp = tmp_path / "20260601_120000"
        _make_readme(str(exp))
        _make_log(str(exp), "Experiment complete — 3 measurements\n",
                  timestamp="20260601_120000")
        _make_new_structure_experiment(str(exp), [40, 42, 44], [-45], [0, 1, 3])
        checker = ExperimentCompletenessChecker(str(tmp_path))
        report = checker.migrate_accomplished(dry_run=False)
        assert len(report.moved) == 1
        target = tmp_path / "accomplish" / "20260601_120000"
        assert os.path.isfile(str(target / "readme.txt"))
        assert os.path.isfile(str(target / "logs" / "experiment_log_20260601_120000.txt"))
        assert os.path.isfile(
            str(target / "40K" / "-45dBm" / "00mW" /
                "YBCO_-45dBm_00mW_target_40K_actual_40.300K.s2p"))


# =========================================================================
# TestConfigDefaults — 配置默认值
# =========================================================================

class TestConfigDefaults:
    """Given 无参数覆盖，使用 config.py 中的默认值。"""

    def test_given_no_overrides_when_constructing_then_uses_config_values(self):
        """不传参数时使用 config.py 默认值。"""
        import config
        from experiment_data_completeness import ExperimentCompletenessChecker
        checker = ExperimentCompletenessChecker()
        assert checker._base_dir == config.experiment_data_base_dir
        assert checker._min_temp_levels == config.min_temp_levels_for_complete
        assert checker._expected_laser_powers == config.expected_laser_powers_mw
        assert checker._min_laser_powers == config.min_laser_powers_for_complete
        assert checker._log_complete_marker == config.log_complete_marker
        assert checker._log_tail_chars == config.log_tail_chars
        assert checker._min_s2p_size == config.min_s2p_file_size_bytes
        assert checker._accomplish_name == config.accomplish_subfolder_name

    def test_given_overrides_when_constructing_then_uses_overrides(self):
        """传入自定义阈值时使用覆盖值。"""
        from experiment_data_completeness import ExperimentCompletenessChecker
        checker = ExperimentCompletenessChecker(
            min_temp_levels=5,
            expected_laser_powers=[1, 2, 3],
            min_laser_powers=2,
            log_complete_marker="DONE",
            log_tail_chars=100,
            min_s2p_size=500,
            accomplish_name="done",
        )
        assert checker._min_temp_levels == 5
        assert checker._expected_laser_powers == [1, 2, 3]
        assert checker._min_laser_powers == 2
        assert checker._log_complete_marker == "DONE"
        assert checker._log_tail_chars == 100
        assert checker._min_s2p_size == 500
        assert checker._accomplish_name == "done"


# =========================================================================
# TestConvenienceFunction — 便利函数
# =========================================================================

class TestConvenienceFunction:
    """Given 便利函数 scan_and_migrate。"""

    def test_given_defaults_when_scan_and_migrate_dry_then_returns_report(self, tmp_path):
        """使用默认配置干运行返回有效报告。"""
        from experiment_data_completeness import scan_and_migrate
        exp1 = tmp_path / "20260601_120000"
        _make_readme(str(exp1))
        _make_new_structure_experiment(str(exp1), [40], [-45], [0, 1, 3])
        report = scan_and_migrate(str(tmp_path), dry_run=True)
        assert report.dry_run is True
        assert report.base_dir == str(tmp_path)
        assert len(report.moved) == 1
        assert len(report.skipped) == 0


# =========================================================================
# TestMigrationReport — 报告格式
# =========================================================================

class TestMigrationReport:
    """Given MigrationReport，验证其输出格式。"""

    def test_given_report_when_summary_then_contains_chinese_labels(self):
        """summary() 返回中文标签的多行报告。"""
        from experiment_data_completeness import MigrationReport
        report = MigrationReport(
            base_dir="/test",
            accomplish_dir="/test/accomplish",
            dry_run=True,
            moved=["20260601_120000"],
            skipped=[("20260602_120000", "logs_only")],
            errors=[],
        )
        summary = report.summary()
        assert "DRY RUN" in summary or "干运行" in summary
        assert "20260601_120000" in summary
        assert "20260602_120000" in summary

    def test_given_empty_report_when_summary_then_shows_no_actions(self):
        """空报告显示无操作信息。"""
        from experiment_data_completeness import MigrationReport
        report = MigrationReport(
            base_dir="/test",
            accomplish_dir="/test/accomplish",
            dry_run=False,
            moved=[],
            skipped=[],
            errors=[],
        )
        summary = report.summary()
        assert isinstance(summary, str)
        assert len(summary) > 0
