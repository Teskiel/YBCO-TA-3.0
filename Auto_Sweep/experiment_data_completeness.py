# -*- coding: utf-8 -*-
"""
实验数据完整性检查与迁移模块。

判定逻辑（优先级递减）：
  1. readme.txt 存在                     → 自动判定完成
  2. 日志末尾含 "Experiment complete"    → 判定完成
  3. 结构性检查（对无 readme/日志的老实验）→ 阈值判定

支持两种目录结构：
  OLD: {target}K/actual_{actual}K/{dBm}/{mW}/*.s2p  (Jun 5-6)
  NEW: {target}K/{dBm}/{mW}/*.s2p                    (Jun 9+)

用法：
  from experiment_data_completeness import scan_and_migrate
  report = scan_and_migrate(dry_run=True)    # 干运行查看报告
  report = scan_and_migrate(dry_run=False)   # 实际迁移
"""

import os
import re
import shutil
import glob as glob_mod
from dataclasses import dataclass, field
from typing import Optional


# =========================================================================
# 时间戳格式正则（YYYYMMDD_HHMMSS 或 YYYYMMDD_HHMMSS-N）
# =========================================================================

_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}(?:-\d+)?$")

# 温度级别匹配：形如 "6K", "40K", "77K"（排除 actual_ 子目录）
_TEMP_LEVEL_RE = re.compile(r"^(\d+)K$")

# S2P 文件扩展名
_S2P_GLOB = "**/*.s2p"


# =========================================================================
# 数据结构
# =========================================================================

@dataclass
class CompletenessResult:
    """单个实验文件夹的完整性判定结果。

    Attributes:
        folder_name: 实验文件夹名（如 "20260605_215526"）
        is_complete: 是否判定为完成
        reason: 机器可读的判定原因码
        details: 补充信息字典（温度级数、S2P 数量等）
    """
    folder_name: str
    is_complete: bool
    reason: str
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        """返回中文单行摘要。"""
        status = "完成" if self.is_complete else "未完成"
        return f"{self.folder_name}: {status} ({self.reason})"


@dataclass
class MigrationReport:
    """迁移操作报告。

    Attributes:
        base_dir: 实验数据根目录
        accomplish_dir: 迁移目标目录
        dry_run: 是否为干运行模式
        moved: 已迁移（或将要迁移）的文件夹名列表
        skipped: 跳过的 (文件夹名, 原因) 列表
        errors: 错误信息列表
    """
    base_dir: str
    accomplish_dir: str
    dry_run: bool
    moved: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # list of (name, reason)
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        """返回中文多行迁移摘要。"""
        lines = []
        mode = "干运行 (DRY RUN) — 未实际移动文件" if self.dry_run else "实际迁移"
        lines.append("=" * 60)
        lines.append(f"  实验数据迁移报告 ({mode})")
        lines.append("=" * 60)
        lines.append(f"  源目录: {self.base_dir}")
        lines.append(f"  目标目录: {self.accomplish_dir}")
        lines.append(f"  完成实验数: {len(self.moved)}")
        lines.append(f"  跳过实验数: {len(self.skipped)}")
        lines.append(f"  错误数: {len(self.errors)}")
        lines.append("-" * 60)

        if self.moved:
            lines.append(f"  {'>> 将迁移' if self.dry_run else '>> 已迁移'}:")
            for name in self.moved:
                lines.append(f"    [OK] {name}")

        if self.skipped:
            lines.append(f"  [SKIP] 跳过（未完成）:")
            for name, reason in self.skipped:
                lines.append(f"    [--] {name} — {reason}")

        if self.errors:
            lines.append(f"  [ERR] 错误:")
            for err in self.errors:
                lines.append(f"    [!!] {err}")

        lines.append("=" * 60)
        return "\n".join(lines)


# =========================================================================
# 核心检查器
# =========================================================================

class ExperimentCompletenessChecker:
    """实验数据完整性判定与迁移工具。

    判断逻辑（优先级递减）:
      1. readme.txt 存在                   → 自动判定完成
      2. 日志末尾含 "Experiment complete"  → 判定完成
      3. 结构性检查（无 readme/日志时）    → 阈值判定

    Args:
        base_dir: 实验数据根目录。默认从 config 读取。
        min_temp_levels: 最小温度级别数，默认从 config 读取。
        expected_laser_powers: 期望的完整激光功率列表。
        min_laser_powers: 最小激光功率级别数。
        log_complete_marker: 日志中的完成标记字符串。
        log_tail_chars: 检查日志末尾的字符数。
        min_s2p_size: S2P 文件最小大小（字节），低于此值视为空。
        accomplish_name: 迁移目标子文件夹名。
    """

    def __init__(self, base_dir: Optional[str] = None, *,
                 min_temp_levels: Optional[int] = None,
                 expected_laser_powers: Optional[list] = None,
                 min_laser_powers: Optional[int] = None,
                 log_complete_marker: Optional[str] = None,
                 log_tail_chars: Optional[int] = None,
                 min_s2p_size: Optional[int] = None,
                 accomplish_name: Optional[str] = None):
        import config
        self._base_dir = (base_dir if base_dir is not None
                          else config.experiment_data_base_dir)
        self._min_temp_levels = (min_temp_levels if min_temp_levels is not None
                                 else config.min_temp_levels_for_complete)
        self._expected_laser_powers = (expected_laser_powers
                                       if expected_laser_powers is not None
                                       else list(config.expected_laser_powers_mw))
        self._min_laser_powers = (min_laser_powers if min_laser_powers is not None
                                  else config.min_laser_powers_for_complete)
        self._log_complete_marker = (log_complete_marker
                                     if log_complete_marker is not None
                                     else config.log_complete_marker)
        self._log_tail_chars = (log_tail_chars if log_tail_chars is not None
                                else config.log_tail_chars)
        self._min_s2p_size = (min_s2p_size if min_s2p_size is not None
                              else config.min_s2p_file_size_bytes)
        self._accomplish_name = (accomplish_name if accomplish_name is not None
                                 else config.accomplish_subfolder_name)

    # ---- 公开方法 ----

    def is_complete(self, experiment_dir: str) -> CompletenessResult:
        """判定单个实验文件夹是否已完成。

        Args:
            experiment_dir: 实验文件夹完整路径。

        Returns:
            CompletenessResult，含 is_complete、reason、details。
        """
        folder_name = os.path.basename(experiment_dir.rstrip(os.sep))
        details: dict = {}

        # Priority 1: readme.txt 存在 → 自动完成
        if self._has_readme(experiment_dir):
            details["temp_levels"] = self._count_temperature_levels(experiment_dir)
            details["s2p_count"] = self._count_s2p_files(experiment_dir)
            return CompletenessResult(
                folder_name=folder_name,
                is_complete=True,
                reason="readme_found",
                details=details,
            )

        # Priority 2: 日志末尾含完成标记
        if self._check_log_complete(experiment_dir):
            details["temp_levels"] = self._count_temperature_levels(experiment_dir)
            details["s2p_count"] = self._count_s2p_files(experiment_dir)
            return CompletenessResult(
                folder_name=folder_name,
                is_complete=True,
                reason="log_complete",
                details=details,
            )

        # Priority 3: 结构性检查
        is_struct_complete, details = self._check_structural_complete(experiment_dir)
        if is_struct_complete:
            return CompletenessResult(
                folder_name=folder_name,
                is_complete=True,
                reason="structurally_complete",
                details=details,
            )

        # 不完整 — details 中已包含原因
        return CompletenessResult(
            folder_name=folder_name,
            is_complete=False,
            reason=details.get("reason", "unknown"),
            details=details,
        )

    def scan_experiments(self) -> dict:
        """扫描 base_dir 下所有实验文件夹。

        Returns:
            {folder_name: CompletenessResult} 字典。
            跳过 ZIP 文件、accomplish 子目录、非时间戳格式的文件夹。
        """
        results = {}
        if not os.path.isdir(self._base_dir):
            return results

        for entry_name in os.listdir(self._base_dir):
            entry_path = os.path.join(self._base_dir, entry_name)

            # 跳过非目录
            if not os.path.isdir(entry_path):
                continue

            # 跳过 accomplish 子目录自身
            if entry_name == self._accomplish_name:
                continue

            # 跳过非时间戳格式
            if not self._is_valid_experiment_folder(entry_name):
                continue

            try:
                result = self.is_complete(entry_path)
                results[entry_name] = result
            except Exception as e:
                results[entry_name] = CompletenessResult(
                    folder_name=entry_name,
                    is_complete=False,
                    reason="error",
                    details={"error": str(e)},
                )

        return results

    def migrate_accomplished(self, *, dry_run: bool = True) -> MigrationReport:
        """将判定为完成的实验文件夹移动到 accomplish 子目录。

        Args:
            dry_run: True 时仅生成报告不实际移动，False 时执行移动。

        Returns:
            MigrationReport 含移动/跳过/错误详情。
        """
        accomplish_dir = os.path.join(self._base_dir, self._accomplish_name)
        results = self.scan_experiments()

        moved: list = []
        skipped: list = []
        errors: list = []

        for folder_name, result in results.items():
            if not result.is_complete:
                skipped.append((folder_name, result.reason))
                continue

            src = os.path.join(self._base_dir, folder_name)
            dst = os.path.join(accomplish_dir, folder_name)

            if dry_run:
                moved.append(folder_name)
            else:
                try:
                    # 创建 accomplish 目录（如果不存在）
                    os.makedirs(accomplish_dir, exist_ok=True)

                    # 处理目标冲突：追加 _1, _2 ...
                    if os.path.exists(dst):
                        dst = self._resolve_conflict(dst)

                    shutil.move(src, dst)
                    moved.append(folder_name)
                except Exception as e:
                    errors.append(f"移动 {folder_name} 失败: {e}")

        return MigrationReport(
            base_dir=self._base_dir,
            accomplish_dir=accomplish_dir,
            dry_run=dry_run,
            moved=moved,
            skipped=skipped,
            errors=errors,
        )

    # ---- 内部方法 ----

    def _has_readme(self, experiment_dir: str) -> bool:
        """检查 experiment_dir 根级别是否存在 readme.txt。"""
        return os.path.isfile(os.path.join(experiment_dir, "readme.txt"))

    def _check_log_complete(self, experiment_dir: str) -> bool:
        """检查 logs/ 下最新日志文件末尾是否含完成标记。

        定位策略：
          1. 优先匹配 experiment_log_{folder_timestamp}.txt
          2. 回退到 logs/ 下文件名时间戳最新的 experiment_log_*.txt
        """
        log_dir = os.path.join(experiment_dir, "logs")
        if not os.path.isdir(log_dir):
            return False

        folder_name = os.path.basename(experiment_dir.rstrip(os.sep))
        # 尝试精确匹配
        expected_log = os.path.join(
            log_dir, f"experiment_log_{folder_name}.txt")
        log_path = None

        if os.path.isfile(expected_log):
            log_path = expected_log
        else:
            # 回退：查找所有 experiment_log_*.txt，取文件名中时间戳最新的
            candidates = glob_mod.glob(
                os.path.join(log_dir, "experiment_log_*.txt"))
            if not candidates:
                return False
            # 按文件名排序（时间戳大的在后），取最后一个
            candidates.sort()
            log_path = candidates[-1]

        # 读取日志末尾
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                # 先尝试 seek 到末尾
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                read_size = min(self._log_tail_chars, file_size)
                if read_size <= 0:
                    return False
                f.seek(max(0, file_size - read_size), os.SEEK_SET)
                tail_content = f.read()
        except OSError:
            return False

        return self._log_complete_marker in tail_content

    def _check_structural_complete(self, experiment_dir: str) -> tuple:
        """对无 readme/日志的老实验执行结构性完整性检查。

        检查内容：
          1. 温度子目录数量 >= min_temp_levels_for_complete
          2. 每个温度至少有一个 VNA 功率子目录有 S2P 数据
          3. 至少 min_laser_powers 个激光功率级别有非空 S2P 文件
          4. 非空 S2P 文件总数 > 0

        支持两种目录结构：
          OLD: {target}K/actual_{actual}K/{dBm}/{mW}/*.s2p
          NEW: {target}K/{dBm}/{mW}/*.s2p

        Returns:
            (is_complete: bool, details: dict)
        """
        details: dict = {}

        # 统计温度级别
        temp_levels = self._count_temperature_levels(experiment_dir)
        details["temp_levels"] = temp_levels

        # 仅有 logs/ 无温度子目录 → 实验从未开始测量
        if temp_levels == 0:
            details["reason"] = "logs_only" if os.path.isdir(
                os.path.join(experiment_dir, "logs")) else "no_data"
            return False, details

        if temp_levels < self._min_temp_levels:
            details["reason"] = "insufficient_temps"
            return False, details

        # 统计非空 S2P 文件
        s2p_count = self._count_s2p_files(experiment_dir)
        details["s2p_count"] = s2p_count

        if s2p_count == 0:
            details["reason"] = "no_s2p_files"
            return False, details

        # 检查是否仅有 logs/ 但无实际数据（S2P 数为 0 已在上面处理）
        # 现在检查激光功率覆盖度
        laser_powers_found = self._count_laser_power_levels(experiment_dir)
        details["laser_power_levels"] = laser_powers_found

        if laser_powers_found < self._min_laser_powers:
            details["reason"] = "insufficient_laser_powers"
            return False, details

        # 检查 VNA 功率覆盖度（至少要有 1 个 VNA 功率级别有数据）
        vna_powers_found = self._count_vna_power_levels(experiment_dir)
        details["vna_power_levels"] = vna_powers_found

        if vna_powers_found == 0:
            details["reason"] = "no_vna_power_levels"
            return False, details

        # 通过所有检查
        return True, details

    def _count_temperature_levels(self, experiment_dir: str) -> int:
        """统计实验文件夹下的温度级别数量。

        匹配形如 \"{N}K\" 的顶层子目录（排除 actual_ 子目录、logs/、文件）。
        """
        count = 0
        try:
            for entry in os.listdir(experiment_dir):
                entry_path = os.path.join(experiment_dir, entry)
                if os.path.isdir(entry_path) and _TEMP_LEVEL_RE.match(entry):
                    count += 1
        except OSError:
            pass
        return count

    def _count_laser_power_levels(self, experiment_dir: str) -> int:
        """统计所有非空 S2P 文件覆盖的激光功率级别数量。

        遍历所有 {mW} 形式的叶子目录，只要该目录下存在非空 S2P 文件即计数。
        """
        laser_powers: set = set()
        for root, dirs, files in os.walk(experiment_dir):
            # 检查当前目录名是否匹配 {N}mW
            dir_name = os.path.basename(root)
            m = re.match(r"^(\d+)mW$", dir_name)
            if m:
                # 检查该目录是否有非空 S2P 文件
                for f in files:
                    if f.endswith(".s2p"):
                        fpath = os.path.join(root, f)
                        try:
                            if os.path.getsize(fpath) >= self._min_s2p_size:
                                laser_powers.add(int(m.group(1)))
                                break  # 该功率级别已有有效数据
                        except OSError:
                            pass
        return len(laser_powers)

    def _count_vna_power_levels(self, experiment_dir: str) -> int:
        """统计包含非空 S2P 文件的 VNA 功率级别数量。

        匹配形如 \"{N}dBm\" 或 \"{-N}dBm\" 的目录。
        S2P 文件可能在 dBm 目录的直接子目录中（如 {N}mW/）。
        """
        vna_powers: set = set()
        for root, dirs, files in os.walk(experiment_dir):
            dir_name = os.path.basename(root)
            m = re.match(r"^([+-]?\d+)dBm$", dir_name)
            if m:
                if self._dir_contains_s2p(root):
                    vna_powers.add(int(m.group(1)))
        return len(vna_powers)

    def _dir_contains_s2p(self, directory: str) -> bool:
        """递归检查目录及其子目录是否包含非空 S2P 文件。"""
        try:
            for root, dirs, files in os.walk(directory):
                for f in files:
                    if f.endswith(".s2p"):
                        fpath = os.path.join(root, f)
                        try:
                            if os.path.getsize(fpath) >= self._min_s2p_size:
                                return True
                        except OSError:
                            pass
        except OSError:
            pass
        return False

    def _count_s2p_files(self, experiment_dir: str) -> int:
        """递归统计非空 S2P 文件数量。"""
        count = 0
        try:
            for root, dirs, files in os.walk(experiment_dir):
                for f in files:
                    if f.endswith(".s2p"):
                        fpath = os.path.join(root, f)
                        try:
                            if os.path.getsize(fpath) >= self._min_s2p_size:
                                count += 1
                        except OSError:
                            pass
        except OSError:
            pass
        return count

    def _is_valid_experiment_folder(self, folder_name: str) -> bool:
        r"""检查文件夹名是否为有效的时间戳格式 YYYYMMDD_HHMMSS[-\d+]"""
        return bool(_TIMESTAMP_RE.match(folder_name))

    @staticmethod
    def _resolve_conflict(dst: str) -> str:
        """为目标路径解决命名冲突，追加 _1, _2 ... 后缀。"""
        if not os.path.exists(dst):
            return dst
        base = dst
        counter = 1
        while os.path.exists(f"{base}_{counter}"):
            counter += 1
        return f"{base}_{counter}"


# =========================================================================
# 便利函数
# =========================================================================

def scan_and_migrate(base_dir: Optional[str] = None, *,
                     dry_run: bool = True) -> MigrationReport:
    """便利函数：用默认配置扫描并迁移。

    Args:
        base_dir: 实验数据根目录，默认从 config 读取。
        dry_run: True 为干运行（仅报告），False 为实际迁移。

    Returns:
        MigrationReport 迁移报告。

    Example:
        >>> from experiment_data_completeness import scan_and_migrate
        >>> report = scan_and_migrate(dry_run=True)
        >>> print(report.summary())
    """
    checker = ExperimentCompletenessChecker(base_dir)
    return checker.migrate_accomplished(dry_run=dry_run)
