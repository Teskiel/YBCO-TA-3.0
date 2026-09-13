#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 docs/machine-map.md：按机器列出历史提交，回答"哪台电脑改了什么"。

机器归属规则（严格按优先级，**不确定就写 unknown，绝不猜**）
----------------------------------------------------------
1. 提交自带的 `Machine:` trailer   —— 最权威，本框架落地后的提交都有
2. `machines/history.json` 人工登记 —— 框架落地前的历史提交用这个兜底
3. 都没有                          —— 记为 `unknown`

为什么不直接改历史把 trailer 补上
--------------------------------
`git filter-branch` / rebase 会改变所有后继提交的 SHA，导致：
* 已有标签（v3.0/v3.1）失效
* 其它机器上的 clone 分叉，需要全员强制重置
* 引用这些 SHA 的文档、`history.json` 全部作废
收益（几行元数据）远小于代价，所以历史只做**事后登记**。

用法
----
    python sync/map_history.py                  # 写入 docs/machine-map.md
    python sync/map_history.py --stdout         # 只打印，不写文件
    python sync/map_history.py --limit 200      # 只看最近 200 条
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_CONFIG_ERROR, EXIT_OK, ConfigError, configure_stdout, git_out,
    load_registry, repo_root, trailer_value,
)

configure_stdout()

OUT_PATH = "docs/machine-map.md"
GIT_FMT = "%H%x1f%h%x1f%an%x1f%ad%x1f%s"
CONF_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def load_history(root: Path) -> dict[str, dict]:
    """读 machines/history.json；损坏时警告并返回空表（不崩）。"""
    path = root / "machines" / "history.json"
    if not path.exists():
        print(f"[!] 找不到 {path.as_posix()}，历史归属将全部记为 unknown",
              file=sys.stderr)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[!] {path.as_posix()} 解析失败（{exc}），"
              "历史归属将全部记为 unknown", file=sys.stderr)
        return {}
    return {c.get("sha", ""): c for c in data.get("commits", [])}


def collect(root: Path, limit: int = 0) -> tuple[list[dict], list[str]]:
    """返回 (提交列表, 警告列表)。"""
    warnings: list[str] = []
    hist = load_history(root)

    args = ["log", "--all", "--date=short", f"--format={GIT_FMT}",
            f"--max-count={limit}" if limit else "--max-count=1000"]
    raw = git_out(args, cwd=root, check=False)
    if not raw:
        raise ConfigError("读不到 git log（仓库为空或 git 不可用）")

    rows: list[dict] = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 5:
            continue
        sha, short, author, date, subject = parts[:5]
        msg = git_out(["log", "-1", "--format=%B", sha], cwd=root, check=False)
        trailer = trailer_value(msg, "Machine")
        ref = trailer_value(msg, "Machine-Ref")
        entry = hist.get(sha)
        if trailer:
            machine, confidence, evidence = trailer, "high", "提交自带 Machine: trailer"
        elif entry:
            machine = entry.get("machine", "unknown")
            confidence = entry.get("confidence", "low")
            evidence = entry.get("evidence", "")
        else:
            machine, confidence, evidence = "unknown", "unknown", ""
        rows.append({
            "sha": sha, "short": short, "author": author, "date": date[:10],
            "subject": subject, "machine": machine, "confidence": confidence,
            "evidence": evidence, "ref": ref,
        })

    # 校验 history.json 里登记的 SHA 是否真的存在（rebase 后会失效）
    known_shas = {r["sha"] for r in rows}
    for sha, entry in hist.items():
        if sha and sha not in known_shas:
            warnings.append(
                f"history.json 里的 {sha[:12]} 在当前历史中不存在"
                "（上游可能发生过 rebase/filter-branch）——已跳过")
    return rows, warnings


def render(rows: list[dict], warnings: list[str], root: Path,
           registry: dict) -> str:
    by_machine: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_machine[r["machine"]].append(r)

    lines: list[str] = []
    lines.append("# 机器活动总表")
    lines.append("")
    lines.append("> 由 `python sync/map_history.py` 生成——**不要手工编辑**，"
                 "改动会在下次生成时被覆盖。")
    lines.append("> 机器清单见 [`machines/machines.json`]"
                 "(../machines/machines.json)；历史归属登记见 "
                 "[`machines/history.json`](../machines/history.json)。")
    lines.append("")

    if warnings:
        lines.append("## ⚠ 数据完整性警告")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("## 已登记机器")
    lines.append("")
    lines.append("| 代号 | 显示名 | 角色 | 主机名 | 提交数 |")
    lines.append("|---|---|---|---|---|")
    for m in registry.get("machines", []):
        mid = m.get("id", "?")
        lines.append(f"| `{mid}` | {m.get('label', '')} | {m.get('role', '')} "
                     f"| {m.get('hostname') or '—'} "
                     f"| {len(by_machine.get(mid, []))} |")
    unknown_n = len(by_machine.get("unknown", []))
    if unknown_n:
        lines.append(f"| `unknown` | 归属未登记 | — | — | {unknown_n} |")
    lines.append("")

    lines.append("## 各机器的提交")
    lines.append("")
    order = [m.get("id") for m in registry.get("machines", [])] + ["unknown"]
    for mid in order:
        commits = by_machine.get(mid)
        if not commits:
            continue
        entry = next((m for m in registry.get("machines", [])
                      if m.get("id") == mid), None)
        title = f"{mid}（{entry.get('label', '')}）" if entry else "unknown（归属未登记）"
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"共 {len(commits)} 个提交。")
        lines.append("")
        lines.append("| 提交 | 日期 | 作者 | 摘要 | 归属依据 |")
        lines.append("|---|---|---|---|---|")
        for c in commits:
            basis = {"high": "提交 trailer", "medium": "人工登记（证据中等）",
                     "low": "人工登记（证据薄弱）",
                     "unknown": "—"}[c["confidence"]]
            subject = c["subject"].replace("|", "\\|")
            lines.append(f"| `{c['short']}` | {c['date']} | {c['author']} "
                         f"| {subject} | {basis} |")
        lines.append("")

    conf = Counter(c["confidence"] for c in rows)
    lines.append("## 归属置信度汇总")
    lines.append("")
    lines.append("| 置信度 | 含义 | 提交数 |")
    lines.append("|---|---|---|")
    for level in ("high", "medium", "low", "unknown"):
        meaning = {
            "high": "提交自带 Machine: trailer，直接可信",
            "medium": "人工登记，有间接证据（路径、配置文件、reflog）",
            "low": "人工登记，证据薄弱",
            "unknown": "无任何证据——需要人工补充 machines/history.json",
        }[level]
        lines.append(f"| {level} | {meaning} | {conf.get(level, 0)} |")
    lines.append("")
    lines.append("## 怎么补 unknown 的归属")
    lines.append("")
    lines.append("1. 判断该提交是哪台机器产出的（看它引入的文件路径、"
                 "配置里的主机名、当时的 reflog）")
    lines.append("2. 在 `machines/history.json` 的 `commits` 数组里加一条：")
    lines.append("")
    lines.append("```json")
    lines.append('{')
    lines.append('  "sha": "<完整 40 位 SHA>",')
    lines.append('  "machine": "<machines.json 里的代号>",')
    lines.append('  "confidence": "high|medium|low",')
    lines.append('  "evidence": "判定依据：看到了什么，而不是觉得像什么"')
    lines.append('}')
    lines.append("```")
    lines.append("")
    lines.append("3. 重跑 `python sync/map_history.py`")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="map_history.py",
        description="生成按机器分组的提交总表（docs/machine-map.md）。")
    ap.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    ap.add_argument("--limit", type=int, default=0,
                    help="只统计最近 N 条提交（默认全部，上限 1000）")
    args = ap.parse_args(argv)

    root = repo_root()
    try:
        rows, warnings = collect(root, args.limit)
    except ConfigError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    registry = load_registry(root)
    text = render(rows, warnings, root, registry)

    for w in warnings:
        print(f"[!] {w}", file=sys.stderr)

    if args.stdout:
        print(text)
        return EXIT_OK

    out = root / OUT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    n_unknown = sum(1 for r in rows if r["machine"] == "unknown")
    print(f"[+] 已写入 {out.as_posix()}")
    print(f"    共 {len(rows)} 个提交；"
          f"其中 {n_unknown} 个仍属 unknown"
          + ("（补 machines/history.json 可消除）" if n_unknown else ""))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
