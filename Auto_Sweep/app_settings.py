# -*- coding: utf-8 -*-
"""Auto_Sweep 设置的机器分层（Qt-free，无硬件依赖）。

为什么要分层
------------
`app_settings.json` 把两类**完全不同性质**的数据混在一个文件里：

| 内容 | 性质 | 该不该入库 |
|---|---|---|
| `addresses`（laser/lakeshore/vna 的 VISA 地址） | **机器专属**：换台电脑就是另一组地址 | 不该 |
| `vna` / `laser` / `lakeshore` / `temperature_sweep` 参数 | 实验方法参数，跨机共享 | 该 |
| `overshoot_learning` | 实验累积的物理经验（越跑越准） | 该 |

以前三者同写一个文件，于是：换机器要先改文件（改完就变成"未提交改动"），
两台机器各自跑过实验后又会在同一文件上冲突。

分层方案
--------
    app_settings.example.json   入库   模板（占位值），新机器照抄
    app_settings.machine.json   不入库 本机真实地址与上次使用的参数
    YBCO_APP_SETTINGS           环境变量 JSON，最高优先级

`load_settings()` 合并两层；`settings_path()` 给出应该**写回**的路径
（机器层）。于是入库文件保持稳定，机器差异只存在于本机。

注意：GUI 的"自动保存"会把整份设置（含 overshoot_learning）写进机器层，
所以两台机器各自积累的 overshoot 经验**不会自动合并**。这是有意的取舍——
自动合并两个并发写入的 JSON 只会产生更难查的半混合状态。需要共享时，
把 `overshoot_learning` 段落手工搬进 `app_settings.example.json` 并提交。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = SCRIPT_DIR.parent

# 复用仓库级的配置分层加载器（与 Noisesweep/gui_config.py 同一套路）
if str(_REPO_ROOT / "sync") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "sync"))

import machine_config  # noqa: E402

#: 入库模板
EXAMPLE_PATH = SCRIPT_DIR / "app_settings.example.json"
#: 本机真实设置（不入库）
MACHINE_PATH = SCRIPT_DIR / "app_settings.machine.json"
#: 历史遗留的未分层文件（已入库）。存在但不再由本模块写入。
LEGACY_PATH = SCRIPT_DIR / "app_settings.json"

ENV_VAR = "YBCO_APP_SETTINGS"

#: 属于"机器专属"、必须走机器层的顶层键
MACHINE_SCOPED_KEYS = ("addresses",)


def settings_path() -> Path:
    """返回应该写回的路径：机器层。"""
    return MACHINE_PATH


def load_settings() -> dict:
    """按 环境变量 → 机器层 → 遗留文件 → 模板 读出设置 dict。

    任一层缺失都不抛异常（离线自检与无硬件启动必须照常工作）。
    """
    merged: dict = {}

    for candidate in (EXAMPLE_PATH, LEGACY_PATH):
        data = _read_json(candidate)
        if data:
            merged = machine_config.deep_merge(merged, data)

    overlay = _read_json(MACHINE_PATH)
    if overlay:
        merged = machine_config.deep_merge(merged, overlay)

    raw_env = os.environ.get(ENV_VAR, "").strip()
    if raw_env:
        try:
            env_data = json.loads(raw_env)
            if isinstance(env_data, dict):
                merged = machine_config.deep_merge(merged, env_data)
        except ValueError:
            pass

    return merged


def save_settings(data: dict) -> None:
    """把设置写进机器层（UTF-8，缩进 2）。

    不写 `app_settings.json`：那是入库文件，被 GUI 自动保存改写会让工作区
    长期处于"有未提交改动"状态——本框架的 pre-push 门禁会因此一直拦你。
    """
    MACHINE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def machine_addresses() -> dict:
    """只取本机地址（供需要单独读地址的调用方使用）。

    过滤掉 `_` 开头的键——分层文件里用它们承载 `_readme`/`_note` 这类说明，
    它们不是地址，混进下拉框只会制造占位字符串。
    """
    data = load_settings()
    addrs = data.get("addresses")
    if not isinstance(addrs, dict):
        return {}
    return {k: v for k, v in addrs.items() if not str(k).startswith("_")}


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


if __name__ == "__main__":  # pragma: no cover - 手工诊断
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    print(f"仓库根     : {_REPO_ROOT.as_posix()}")
    print(f"入库模板   : {EXAMPLE_PATH.as_posix()}"
          f"  {'存在' if EXAMPLE_PATH.exists() else '缺失'}")
    print(f"遗留文件   : {LEGACY_PATH.as_posix()}"
          f"  {'存在' if LEGACY_PATH.exists() else '缺失'}")
    print(f"机器层     : {MACHINE_PATH.as_posix()}"
          f"  {'存在' if MACHINE_PATH.exists() else '缺失'}")
    print(f"写回目标   : {settings_path().as_posix()}")
    print(f"本机地址   : {json.dumps(machine_addresses(), ensure_ascii=False)}")
