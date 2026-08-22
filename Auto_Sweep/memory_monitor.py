# -*- coding: utf-8 -*-
"""
轻量级系统内存监控模块
======================

通过 ctypes 调用 Windows GlobalMemoryStatusEx API，零外部依赖。
用于长时间实验运行期间监控系统内存使用，在可用内存不足时提前告警，
防止 OOM 级联崩溃。

典型用法::

    from memory_monitor import MemoryMonitor
    monitor = MemoryMonitor(warning_threshold_mb=8192, critical_threshold_mb=4096)

    # 每轮迭代中调用
    info = monitor.check()
    if info.warning:
        print(monitor.format_warning(info))

    # 或作为上下文管理器记录内存趋势
    with monitor.track("laser_sweep"):
        do_heavy_work()
"""

import ctypes
import time
from dataclasses import dataclass, field
from typing import Optional, List


# ---------------------------------------------------------------------------
# Windows API 绑定
# ---------------------------------------------------------------------------

class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength",                ctypes.c_ulong),
        ("dwMemoryLoad",            ctypes.c_ulong),
        ("ullTotalPhys",            ctypes.c_ulonglong),
        ("ullAvailPhys",            ctypes.c_ulonglong),
        ("ullTotalPageFile",        ctypes.c_ulonglong),
        ("ullAvailPageFile",        ctypes.c_ulonglong),
        ("ullTotalVirtual",         ctypes.c_ulonglong),
        ("ullAvailVirtual",         ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _get_memory_status() -> MEMORYSTATUSEX:
    """调用 GlobalMemoryStatusEx 获取系统内存状态。"""
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise OSError("GlobalMemoryStatusEx 调用失败")
    return stat


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class MemoryInfo:
    """内存状态快照。"""
    timestamp: float = 0.0
    total_phys_mb: float = 0.0       # 物理内存总量 (MB)
    avail_phys_mb: float = 0.0       # 可用物理内存 (MB)
    used_phys_mb: float = 0.0        # 已用物理内存 (MB)
    load_percent: int = 0            # 内存使用率 (0-100)
    total_pagefile_mb: float = 0.0   # 页面文件总量 (MB)
    avail_pagefile_mb: float = 0.0   # 可用页面文件 (MB)
    warning: bool = False            # 低于警告阈值
    critical: bool = False           # 低于严重阈值
    early_warning: bool = False      # 使用率超过 early_warning_percent
    graceful_exit: bool = False      # 低于优雅退出阈值


@dataclass
class MemorySnapshot:
    """带标签的内存快照（用于趋势记录）。"""
    label: str
    info: MemoryInfo
    timestamp: float = 0.0

    def __post_init__(self):
        self.timestamp = self.info.timestamp


# ---------------------------------------------------------------------------
# MemoryMonitor
# ---------------------------------------------------------------------------

class MemoryMonitor:
    """系统内存监控器。

    Parameters
    ----------
    warning_threshold_mb: float
        可用内存低于此值 (MB) 时发出警告。默认 8 GB。
    critical_threshold_mb: float
        可用内存低于此值 (MB) 时发出严重告警。默认 4 GB。
    early_warning_percent: int
        内存使用率超过此百分比时触发早期预警。默认 80%。
    graceful_exit_threshold_mb: float
        可用内存低于此值 (MB) 时触发优雅退出。默认 2 GB。
    log_callback: callable or None
        可选的日志回调函数，签名为 ``callback(message: str)``。
        传入 None 则使用内置 print。
    """

    # 日志级别前缀
    LEVEL_INFO = "[MEM-INFO]"
    LEVEL_WARN = "[MEM-WARN]"
    LEVEL_CRIT = "[MEM-CRIT]"
    LEVEL_EARLY = "[MEM-EARLY]"
    LEVEL_EXIT = "[MEM-EXIT]"

    def __init__(
        self,
        warning_threshold_mb: float = 8192,
        critical_threshold_mb: float = 4096,
        early_warning_percent: int = 80,
        graceful_exit_threshold_mb: float = 2048,
        log_callback=None,
    ):
        self.warning_threshold_mb = warning_threshold_mb
        self.critical_threshold_mb = critical_threshold_mb
        self.early_warning_percent = early_warning_percent
        self.graceful_exit_threshold_mb = graceful_exit_threshold_mb
        self._log = log_callback if log_callback else print

        # 趋势记录
        self.snapshots: List[MemorySnapshot] = []
        self._start_time: Optional[float] = None
        self._peak_used_mb: float = 0.0
        self._min_avail_mb: float = float("inf")

    # ------------------------------------------------------------------
    # 核心查询
    # ------------------------------------------------------------------

    def get_info(self, check_thresholds: bool = True) -> MemoryInfo:
        """获取当前内存状态，可选检查是否触发告警阈值。

        Returns
        -------
        MemoryInfo
            当前内存状态快照。
        """
        stat = _get_memory_status()

        total_phys = stat.ullTotalPhys / (1024 * 1024)
        avail_phys = stat.ullAvailPhys / (1024 * 1024)
        used_phys = total_phys - avail_phys
        total_pf = stat.ullTotalPageFile / (1024 * 1024)
        avail_pf = stat.ullAvailPageFile / (1024 * 1024)

        info = MemoryInfo(
            timestamp=time.time(),
            total_phys_mb=total_phys,
            avail_phys_mb=avail_phys,
            used_phys_mb=used_phys,
            load_percent=int(stat.dwMemoryLoad),
            total_pagefile_mb=total_pf,
            avail_pagefile_mb=avail_pf,
        )

        if check_thresholds:
            # 百分比早期预警（先于绝对值阈值触发）
            if info.load_percent >= self.early_warning_percent:
                info.early_warning = True
            # 优雅退出阈值
            if avail_phys < self.graceful_exit_threshold_mb:
                info.graceful_exit = True
                info.critical = True
                info.warning = True
                info.early_warning = True
            elif avail_phys < self.critical_threshold_mb:
                info.critical = True
                info.warning = True
                info.early_warning = True
            elif avail_phys < self.warning_threshold_mb:
                info.warning = True

        # 更新峰值追踪
        if used_phys > self._peak_used_mb:
            self._peak_used_mb = used_phys
        if avail_phys < self._min_avail_mb:
            self._min_avail_mb = avail_phys

        return info

    def check(self) -> MemoryInfo:
        """获取内存状态并自动记录快照。相当于 ``get_info()`` + 快照保存。

        Returns
        -------
        MemoryInfo
        """
        info = self.get_info(check_thresholds=True)
        return info

    def snapshot(self, label: str = "") -> MemorySnapshot:
        """手动记录一个带标签的内存快照。

        Parameters
        ----------
        label: str
            快照标签（例如 "before_sweep", "after_stabilize"）。

        Returns
        -------
        MemorySnapshot
        """
        info = self.get_info(check_thresholds=False)
        snap = MemorySnapshot(label=label, info=info)
        self.snapshots.append(snap)
        return snap

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    def format_info(self, info: MemoryInfo) -> str:
        """格式化内存信息为单行字符串。"""
        return (
            f"Memory: {info.used_phys_mb:.0f}/{info.total_phys_mb:.0f} MB "
            f"({info.load_percent}%) used, "
            f"{info.avail_phys_mb:.0f} MB available, "
            f"PageFile: {info.avail_pagefile_mb:.0f} MB available"
        )

    def format_warning(self, info: MemoryInfo) -> str:
        """根据严重级别生成告警消息。"""
        if info.graceful_exit:
            return (
                f"{self.LEVEL_EXIT} GRACEFUL_EXIT: Only {info.avail_phys_mb:.0f} MB "
                f"available (< {self.graceful_exit_threshold_mb:.0f} MB)! "
                f"Saving checkpoint and exiting to prevent crash."
            )
        elif info.critical:
            return (
                f"{self.LEVEL_CRIT} CRITICAL: Only {info.avail_phys_mb:.0f} MB "
                f"available (< {self.critical_threshold_mb:.0f} MB)! "
                f"System may crash. Consider pausing or reducing load."
            )
        elif info.early_warning:
            return (
                f"{self.LEVEL_EARLY} EARLY WARNING: Memory usage at "
                f"{info.load_percent}% (threshold: {self.early_warning_percent}%). "
                f"Auto-saving checkpoint as precaution."
            )
        elif info.warning:
            return (
                f"{self.LEVEL_WARN} WARNING: Only {info.avail_phys_mb:.0f} MB "
                f"available (< {self.warning_threshold_mb:.0f} MB). "
                f"Monitor closely."
            )
        return f"{self.LEVEL_INFO} {self.format_info(info)}"

    # ------------------------------------------------------------------
    # 上下文管理器
    # ------------------------------------------------------------------

    def track(self, label: str):
        """返回一个上下文管理器，自动在进入/退出时记录内存快照。

        使用示例::

            monitor = MemoryMonitor()
            with monitor.track("laser_sweep"):
                run_sweep()
            # 自动记录进入时和退出时的内存快照
        """
        return _MemoryTrackContext(self, label)

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------

    @property
    def peak_used_mb(self) -> float:
        """运行期间物理内存峰值使用量 (MB)。"""
        return self._peak_used_mb

    @property
    def min_avail_mb(self) -> float:
        """运行期间最低可用物理内存 (MB)。"""
        return self._min_avail_mb

    def summary(self) -> str:
        """生成内存使用摘要报告。"""
        lines = [
            "=" * 50,
            "Memory Usage Summary",
            "=" * 50,
            f"Peak physical memory used:  {self._peak_used_mb:.0f} MB",
            f"Minimum available memory:   {self._min_avail_mb:.0f} MB",
            f"Warning threshold:          {self.warning_threshold_mb:.0f} MB",
            f"Critical threshold:         {self.critical_threshold_mb:.0f} MB",
            f"Early warning at:           {self.early_warning_percent}%",
            f"Graceful exit at:           {self.graceful_exit_threshold_mb:.0f} MB",
        ]
        if self.snapshots:
            lines.append(f"Snapshots recorded:         {len(self.snapshots)}")
            lines.append("-" * 50)
            for s in self.snapshots:
                info = s.info
                lines.append(
                    f"  [{s.label}] "
                    f"used={info.used_phys_mb:.0f}MB "
                    f"avail={info.avail_phys_mb:.0f}MB "
                    f"load={info.load_percent}%"
                )
        lines.append("=" * 50)
        return "\n".join(lines)


class _MemoryTrackContext:
    """track() 方法的上下文管理器实现。"""

    def __init__(self, monitor: MemoryMonitor, label: str):
        self._monitor = monitor
        self._label = label

    def __enter__(self):
        self._monitor.snapshot(f"{self._label}_enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._monitor.snapshot(f"{self._label}_exit")
        return False  # 不抑制异常


# ---------------------------------------------------------------------------
# 系统进程内存诊断
# ---------------------------------------------------------------------------

def get_top_processes(n: int = 5, timeout: float = 5.0) -> str:
    """获取系统当前内存消耗最大的 Top-N 进程信息。

    通过 PowerShell 查询（Windows 内置，零额外依赖）。
    在实验启动时调用，帮助区分 Python 进程泄漏 vs 系统级内存压力。

    Parameters
    ----------
    n: int
        返回的进程数量（默认 5）。
    timeout: float
        PowerShell 查询超时秒数。

    Returns
    -------
    str
        格式化的进程列表字符串，供日志输出。
    """
    import subprocess

    ps_script = (
        f"Get-Process | Sort-Object WS -Descending | "
        f"Select-Object -First {n} | "
        f"Format-Table Name, Id, "
        f'@{{N="Mem(MB)";E={{[math]::Round($_.WS/1MB,1)}}}}, '
        f'@{{N="PM(MB)";E={{[math]::Round($_.PM/1MB,1)}}}} '
        f"-AutoSize"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "System Top-{} memory processes:\n{}".format(
                n, result.stdout.rstrip())
        else:
            return f"(process diag: PowerShell returned rc={result.returncode})"
    except subprocess.TimeoutExpired:
        return "(process diag: PowerShell query timed out)"
    except FileNotFoundError:
        return "(process diag: PowerShell not available)"
    except Exception as e:
        return f"(process diag: {e})"


# ---------------------------------------------------------------------------
# 便捷函数（向后兼容）
# ---------------------------------------------------------------------------

# 模块级默认监控器实例
_default_monitor: Optional[MemoryMonitor] = None


def get_default_monitor(
    warning_threshold_mb: float = 8192,
    critical_threshold_mb: float = 4096,
    early_warning_percent: int = 80,
    graceful_exit_threshold_mb: float = 2048,
    log_callback=None,
) -> MemoryMonitor:
    """获取或创建模块级默认 MemoryMonitor 实例。"""
    global _default_monitor
    if _default_monitor is None:
        _default_monitor = MemoryMonitor(
            warning_threshold_mb=warning_threshold_mb,
            critical_threshold_mb=critical_threshold_mb,
            early_warning_percent=early_warning_percent,
            graceful_exit_threshold_mb=graceful_exit_threshold_mb,
            log_callback=log_callback,
        )
    return _default_monitor


def quick_check() -> MemoryInfo:
    """快速检查当前内存状态（使用默认阈值，不记录快照）。"""
    stat = _get_memory_status()
    total_phys = stat.ullTotalPhys / (1024 * 1024)
    avail_phys = stat.ullAvailPhys / (1024 * 1024)
    used_phys = total_phys - avail_phys
    return MemoryInfo(
        timestamp=time.time(),
        total_phys_mb=total_phys,
        avail_phys_mb=avail_phys,
        used_phys_mb=used_phys,
        load_percent=int(stat.dwMemoryLoad),
        total_pagefile_mb=stat.ullTotalPageFile / (1024 * 1024),
        avail_pagefile_mb=stat.ullAvailPageFile / (1024 * 1024),
    )
