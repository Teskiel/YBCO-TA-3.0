#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器配置分层加载器：让"换台电脑就跑不起来"变成"每台机器一份本地覆盖"。

问题
----
本仓库历史上把实验机的绝对路径直接提交进了基准配置：

    Noisesweep/gui_config.json → "C:/Windows/System32/YBCO-TA-3.0/Auto_Sweep"

于是换台电脑（或实验机把仓库从 System32 挪走）就立刻坏掉；仓库又是 PUBLIC，
这些路径还顺带泄露了他人机器的目录结构。

分层约定（本项目统一使用）
------------------------
    foo.json               入库   基准/示例值，必须对任意机器都成立（用占位符）
    foo.machine.json       不入库 本机的真实值（.gitignore 已排除）
    YBCO_<名字>_CONFIG     环境变量 临时/CI 覆盖，优先级最高

行为契约（**这是硬约束，改这里前先想清楚**）
------------------------------------------
本地覆盖文件缺失时**绝不抛异常**，而是回退到基准值并打印一行提示。原因：
本仓库的离线自检链路（`Noisesweep/_verify_*.py`、`--dry-run`）必须在
"没配过任何机器路径"的全新 clone 上也能跑通；配置缺失不该表现为崩溃。

零依赖：本模块只 import 标准库，且**不 import 同目录的 _common**。
因为 Noisesweep 的扁平结构要直接 `import` 它（`sys.path` 插入 sync/），
任何多余依赖都会污染那条链路。
"""

from __future__ import annotations

import configparser
import json
import os
import sys
from pathlib import Path

__all__ = [
    "repo_root", "machine_id", "override_path", "load_layered_config",
    "deep_merge", "config_search_paths", "describe_layers",
]

#: 本地覆盖文件的后缀。例：gui_config.json → gui_config.machine.json
OVERRIDE_SUFFIX = ".machine"

_ANNOUNCED: set[str] = set()


def repo_root(start: Path | None = None) -> Path:
    """定位仓库根：从本文件按 `sync/machine_config.py` 的相对位置回推。

    不调用 git（保持零依赖与零副作用），因此在 .git 损坏时也能工作。
    """
    if start is not None:
        return Path(start).resolve()
    return Path(__file__).resolve().parent.parent


def machine_id(root: Path | None = None) -> str:
    """读本机机器代号。

    顺序：环境变量 YBCO_MACHINE → .git/config 的 [ybco] machine。
    找不到返回空字符串（**不抛异常**：配置层要能在未初始化时工作）。
    """
    env = os.environ.get("YBCO_MACHINE", "").strip()
    if env:
        return env

    root = root or repo_root()
    cfg = root / ".git" / "config"
    if not cfg.exists():
        return ""
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg, encoding="utf-8")
        return parser.get("ybco", "machine", fallback="").strip()
    except (configparser.Error, OSError, UnicodeDecodeError):
        return ""


def override_path(base_path: Path) -> Path:
    """由基准文件路径推出本地覆盖路径。

    gui_config.json → gui_config.machine.json
    """
    base_path = Path(base_path)
    return base_path.with_name(base_path.stem + OVERRIDE_SUFFIX + base_path.suffix)


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def deep_merge(base: dict, overlay: dict) -> dict:
    """递归合并：overlay 覆盖 base；两侧都是 dict 时递归，否则整体替换。

    列表按整体替换（不做逐元素合并）——仪器地址列表、温度列表这类
    "要么全用我的、要么全用你的"，逐元素合并只会产生诡异的半混合状态。
    """
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config_search_paths(base_path: Path) -> list[Path]:
    """返回该配置的分层查找顺序（低优先级在前），供诊断用。"""
    base_path = Path(base_path)
    found = [base_path]
    ov = override_path(base_path)
    if ov != base_path:
        found.append(ov)
    return found


def describe_layers(base_path: Path) -> str:
    """人读的分层说明，用于 --show / 报错信息。"""
    lines = []
    for p in config_search_paths(base_path):
        mark = "存在" if p.exists() else "缺失"
        kind = "基准(入库)" if p == Path(base_path) else "本机覆盖(不入库)"
        lines.append(f"    {kind:<16} {mark}  {p.as_posix()}")
    return "\n".join(lines)


def load_layered_config(
    base_path: Path | str,
    env_var: str | None = None,
    *,
    quiet: bool = False,
    root: Path | None = None,
) -> dict:
    """按 环境变量 → 本机覆盖 → 基准 三层读出配置 dict。

    参数
    ----
    base_path : 入库的基准 JSON（例如 Noisesweep/gui_config.json）
    env_var   : 可选的最高优先级环境变量名
    quiet     : True 时不打印"用占位值"的提示（供测试/批量调用）

    返回
    ----
    dict。本地覆盖缺失时返回基准值，**不抛异常**。
    """
    base_path = Path(base_path)
    merged: dict = {}

    base = _read_json(base_path)
    if base is None and not quiet:
        _announce(f"[config] 基准配置缺失或不是合法 JSON：{base_path.as_posix()}")

    if base:
        merged = deep_merge(merged, base)

    ov = override_path(base_path)
    overlay = _read_json(ov) if ov.exists() else None
    if overlay:
        merged = deep_merge(merged, overlay)
    elif not quiet:
        _announce(
            f"[config] 未找到本机覆盖 {ov.name}——正在使用基准/占位值。\n"
            f"         本机专属路径与仪器地址请写进 {ov.as_posix()}\n"
            "         （该文件不入库；模板见同目录 *.machine.example.json）\n"
            "         一次性生成：python sync/setup_machine.py --id <代号>")

    if env_var:
        raw = os.environ.get(env_var, "").strip()
        if raw:
            try:
                env_data = json.loads(raw)
                if isinstance(env_data, dict):
                    merged = deep_merge(merged, env_data)
                else:
                    _announce(f"[config] {env_var} 不是 JSON 对象，已忽略")
            except ValueError:
                # 允许把路径类变量当"单值覆盖"用（例如 YBCO_DATA_DIR=/x/y）
                _announce(f"[config] {env_var} 不是合法 JSON，当作路径忽略")

    return merged


def _announce(message: str) -> None:
    """同一台机器同一条消息只提示一次，避免刷屏淹没真正的报错。"""
    key = message.splitlines()[0]
    if key in _ANNOUNCED:
        return
    _ANNOUNCED.add(key)
    print(message, file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - 手工诊断入口
    import argparse
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="查看机器配置的分层解析结果")
    ap.add_argument("base", help="基准配置文件路径，如 Noisesweep/gui_config.json")
    args = ap.parse_args()

    r = repo_root()
    b = (r / args.base) if not Path(args.base).is_absolute() else Path(args.base)
    print(f"仓库根      : {r.as_posix()}")
    print(f"机器代号    : {machine_id(r) or '(未设置)'}")
    print("分层查找：")
    print(describe_layers(b))
    print("合并结果：")
    print(json.dumps(load_layered_config(b), ensure_ascii=False, indent=2))
