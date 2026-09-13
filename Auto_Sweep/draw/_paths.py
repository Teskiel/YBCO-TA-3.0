# -*- coding: utf-8 -*-
r"""绘图脚本的**路径解析**（多机协作，见 docs/multi-machine.md §5）。

背景
----
`draw/` 下的脚本曾把缓存与数据根目录写死成旧机器数据盘的绝对路径
（形如 `D:/<旧机器>/VNAMeas/Auto_Sweep/experiment_data/~merged/...`）：

后果有两个，且都不是"将来某天才会疼"：

1. **换机器即失效**——路径不存在，脚本直接找不到缓存；
2. **泄露环境信息**——仓库是 PUBLIC，等于公开了那台机器的目录结构。

约定
----
优先级：**环境变量 → 仓库内相对路径**。

    YBCO_DRAW_CACHE_ROOT   合并后的实验数据根（含 experiment_data/~merged/...）

机器专属路径请走环境变量或 `*.machine.json`，**不要写回脚本里的常量**。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["draw_root", "cache_root", "cache_path", "merged_dir", "output_dir",
           "CACHE_ROOT_ENV"]

CACHE_ROOT_ENV = "YBCO_DRAW_CACHE_ROOT"

#: 本文件位于 <repo>/Auto_Sweep/draw/
DRAW_DIR = Path(__file__).resolve().parent
REPO_ROOT = DRAW_DIR.parent.parent

#: 相对默认：仓库内 experiment_data/~merged/（与 config.EXPERIMENT_DATA_DIR 一致）
DEFAULT_CACHE_ROOT = REPO_ROOT / "Auto_Sweep" / "experiment_data" / "~merged"


def draw_root() -> Path:
    """返回本文件所在目录（供需要相对定位其它资源的脚本使用）。"""
    return DRAW_DIR


def cache_root() -> Path:
    """合并数据根目录。环境变量 YBCO_DRAW_CACHE_ROOT 优先。"""
    raw = os.environ.get(CACHE_ROOT_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_CACHE_ROOT


def cache_path(dataset: str) -> str:
    """某个数据集的缓存 pkl 完整路径（字符串，方便直接喂给现有代码）。"""
    name = dataset if dataset.startswith("_cache_") else f"_cache_{dataset}"
    if not name.endswith(".pkl"):
        name += ".pkl"
    return str(cache_root() / "output" / "_cache" / name)


def merged_dir(dataset: str = "") -> str:
    """合并数据目录（可选再拼一个数据集名）。"""
    return str(cache_root() / dataset) if dataset else str(cache_root())


def output_dir(subdir: str = "plot_output") -> str:
    """绘图输出目录。"""
    return str(cache_root() / "output" / "_cache" / subdir)
