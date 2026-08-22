# _lib/plugin_registry.py — 插件注册基础设施
"""@plugin decorator, 注册表, 执行计划构建, 增量检查。"""
import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional


@dataclass
class PluginInfo:
    func_name: str
    func: Callable
    phase: str          # "process" | "plot"
    order: int
    inputs: List[str]
    outputs: List[str]
    description: str
    backend: Optional[str] = None


_REGISTRY: Dict[str, PluginInfo] = {}


def plugin(phase="process", order=0, inputs=None, outputs=None,
           description="", backend=None):
    """插件注册 decorator。

    用法:
        @plugin(phase="process", order=1, inputs=["scan.json"],
                outputs=["peaks.json"], description="detect resonances")
        def main(data_med_dir, source_data_dir, config=None):
            ...
    """
    if inputs is None:
        inputs = []
    if outputs is None:
        outputs = []

    def decorator(func):
        qualified_name = func.__module__ + "." + func.__name__
        info = PluginInfo(
            func_name=qualified_name,
            func=func,
            phase=phase,
            order=order,
            inputs=list(inputs),
            outputs=list(outputs),
            description=description,
            backend=backend,
        )
        _REGISTRY[qualified_name] = info

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

    return decorator


def get_plugins():
    """返回所有已注册插件的 dict {func_name: PluginInfo}。"""
    return dict(_REGISTRY)


def build_execution_plan(phase="all"):
    """构建排序后的执行计划。

    Args:
        phase: "process" | "plot" | "all"

    Returns:
        按 (phase, order) 排序的 PluginInfo 列表
    """
    all_plugins = list(_REGISTRY.values())

    if phase == "process":
        filtered = [p for p in all_plugins if p.phase == "process"]
    elif phase == "plot":
        filtered = [p for p in all_plugins if p.phase == "plot"]
    else:
        filtered = all_plugins

    # 排序: process 在前, plot 在后; 同 phase 内按 order
    phase_order = {"process": 0, "plot": 1}
    return sorted(filtered, key=lambda p: (phase_order.get(p.phase, 99), p.order))


def check_incremental(plugin_info, data_med_dir):
    """增量检查 — 所有输出文件存在且新于所有输入文件。

    Returns:
        True = 跳过, False = 需要执行
    """
    med_dir = Path(data_med_dir)

    # 无输出声明 → 无法增量判断 → 始终执行
    if not plugin_info.outputs:
        return False

    # 检查所有输出是否存在
    for output_file in plugin_info.outputs:
        output_path = med_dir / output_file
        if not output_path.exists():
            return False

    # 检查所有输入是否存在
    input_mtimes = []
    for input_file in plugin_info.inputs:
        input_path = med_dir / input_file
        if not input_path.exists():
            return False  # 输入不存在, 无法运行
        input_mtimes.append(input_path.stat().st_mtime)

    # 所有输出必须比最新的输入新
    max_input_mtime = max(input_mtimes) if input_mtimes else 0
    for output_file in plugin_info.outputs:
        output_path = med_dir / output_file
        if output_path.stat().st_mtime < max_input_mtime:
            return False

    return True
