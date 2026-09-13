#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布一个新版本：打**附注**标签（含机器归属）+ 追加发布索引。

为什么必须是附注标签
------------------
轻量标签（`git tag v3.1`）只是一个指向提交的指针，没有自己的对象，因此
**存不下任何信息**。本仓库的 `v3.1` 原本就是轻量标签，结果事后完全看不出
它由哪台机器产出。附注标签有自己的对象，可以写 message，于是能记录：

    YBCO-TA 3.2.0 — <摘要>

    Machine: lab-smlab (LAB-PC/smlab/Windows/py3.14.2)
    Repo:    C:/Users/smlab/YBCO-TA-3.0
    Commit:  abc123...
    Date:    2026-09-13T10:20:30+08:00

用法
----
    python sync/release.py --version 3.1.1 --message "修正 3.1 的路径硬编码"
    python sync/release.py --bump patch                # 3.1.0 → 3.1.1
    python sync/release.py --version 3.2.0 --dry-run   # 只打印，不改任何东西

前置条件（任一不满足就拒绝，不做半截事）
-------------------------------------
* 工作区干净（否则这个版本里含什么说不清）
* 已与上游同步（否则版本标签打在别人没有的提交上）
* 所有待推送提交都有 Machine: 标识
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_BLOCK, EXIT_CONFIG_ERROR, EXIT_OK, ConfigError, configure_stdout,
    git, git_out, has_machine_trailer, load_registry, machine_identity,
    machine_ref, repo_root, trailer_value,
)

configure_stdout()

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VERSION_FILE = "VERSION"
INDEX_MD = "sync/releases/INDEX.md"
INDEX_JSONL = "sync/releases/log.jsonl"


# ── 版本号 ────────────────────────────────────────────────────────────────

def current_version(root: Path) -> str:
    """从最新版本标签读当前版本；没有标签则从 VERSION 文件读；都没有返回 0.0.0。"""
    tag = git_out(["describe", "--tags", "--abbrev=0", "--match", "v*"],
                  cwd=root, check=False)
    if tag:
        return tag.lstrip("v")
    vf = root / VERSION_FILE
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return "0.0.0"


def normalize(version: str) -> str:
    """把 '3.1' 补成 '3.1.0'，'3.1.2' 原样返回。"""
    m = re.match(r"^(\d+)\.(\d+)$", version)
    return f"{m.group(1)}.{m.group(2)}.0" if m else version


def bump(version: str, part: str) -> str:
    m = SEMVER_RE.match(normalize(version))
    if not m:
        raise ConfigError(f"当前版本 '{version}' 不是 X.Y.Z 形式，无法自动递增；"
                          "请用 --version 显式指定")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


# ── 前置校验 ──────────────────────────────────────────────────────────────

def preflight(root: Path) -> list[str]:
    """返回阻断原因列表（空列表=可以发布）。"""
    problems: list[str] = []

    dirty = [ln for ln in git_out(["status", "--porcelain"], cwd=root,
                                  check=False).splitlines() if ln.strip()]
    if dirty:
        problems.append(f"工作区不干净（{len(dirty)} 项），版本内容将无法界定")
        for ln in dirty[:10]:
            problems.append(f"    {ln}")

    fetch = git(["fetch", "--all", "--prune", "--quiet"], cwd=root, check=False,
                timeout=120)
    if fetch.returncode != EXIT_OK:
        problems.append("fetch 失败，无法确认是否落后于远端")
    else:
        upstream = git_out(["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                            "@{u}"], cwd=root, check=False)
        if not upstream:
            problems.append("当前分支没有上游，不知道该和谁同步")
        else:
            counts = git_out(["rev-list", "--left-right", "--count",
                              f"{upstream}...HEAD"], cwd=root, check=False)
            try:
                behind, ahead = (int(x) for x in counts.split())
            except (ValueError, TypeError):
                behind = ahead = 0
            if behind:
                problems.append(f"落后 {upstream} {behind} 个提交，先 git pull --rebase")
            if ahead:
                problems.append(f"有 {ahead} 个提交还没推送，先 push 再发版")
                for line in git_out(["log", "--format=%h %s", f"{upstream}..HEAD"],
                                    cwd=root, check=False).splitlines()[:10]:
                    msg = git_out(["log", "-1", "--format=%B",
                                   line.split()[0]], cwd=root, check=False)
                    if not has_machine_trailer(msg):
                        problems.append(f"    {line}   ← 缺 Machine: 标识")

    return problems


# ── 索引 ──────────────────────────────────────────────────────────────────

def machine_label(root: Path, mid: str) -> str:
    entry = next((m for m in load_registry(root).get("machines", [])
                  if m.get("id") == mid), None)
    if not entry:
        return mid
    return f"{mid}（{entry.get('label', '')}）"


def append_jsonl(root: Path, record: dict) -> None:
    path = root / INDEX_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_markdown(root: Path, record: dict) -> None:
    path = root / INDEX_MD
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# 发布索引（机器 × 版本）\n\n"
            "> 由 `python sync/release.py` 自动追加。**不要手工删行**——\n"
            "> 这张表就是「哪个版本从哪台电脑上传的」的唯一权威记录。\n"
            "> 机读版本见 `sync/releases/log.jsonl`。\n\n"
            "| 版本 | 日期 | 机器 | Tag 指向 | 关键内容 |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8")
    notes = record["notes"].replace("|", "\\|").replace("\n", " ")
    row = (f"| {record['version']} | {record['date']} | {record['machine']} "
           f"| `{record['commit_short']}` | {notes} |\n")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(row)


# ── 主流程 ────────────────────────────────────────────────────────────────

def build_tag_message(version: str, summary: str, mid: str, ref: str,
                      root: Path, commit: str, when: str) -> str:
    return (
        f"YBCO-TA {version} — {summary}\n"
        "\n"
        f"Machine: {mid} ({ref})\n"
        f"Repo:    {root.as_posix()}\n"
        f"Commit:  {commit}\n"
        f"Date:    {when}\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="release.py",
        description="发布版本：打附注标签（含机器归属）并追加发布索引。")
    ap.add_argument("--version", help="显式版本号 X.Y.Z（与 --bump 二选一）")
    ap.add_argument("--bump", choices=("major", "minor", "patch"),
                    help="按当前版本自动递增")
    ap.add_argument("--message", default="", help="一句话摘要（写进标签与索引）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不做任何改动")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="跳过工作区干净检查（不推荐）")
    ap.add_argument("--no-push", action="store_true",
                    help="只打标签与改索引，不推送")
    args = ap.parse_args(argv)

    root = repo_root()

    try:
        mid, source = machine_identity(root)
    except ConfigError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    cur = current_version(root)
    if args.version:
        version = args.version
    elif args.bump:
        try:
            version = bump(cur, args.bump)
        except ConfigError as exc:
            print(f"[X] {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
    else:
        print(f"[X] 需要 --version X.Y.Z 或 --bump {{major,minor,patch}}"
              f"（当前版本 {cur}）", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if not SEMVER_RE.match(version):
        print(f"[X] 版本号 '{version}' 不合规，应为 X.Y.Z（例如 3.2.0）",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR

    tag = f"v{version}"
    if git_out(["tag", "-l", tag], cwd=root, check=False):
        print(f"[X] 标签 {tag} 已存在。版本号只增不改——"
              "要改已发布的版本请另发一个新版本号。", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if not args.allow_dirty:
        problems = preflight(root)
        if problems:
            print("[X] 发布前置检查未通过：", file=sys.stderr)
            for p in problems:
                print(f"    {p}", file=sys.stderr)
            print("\n    确认要强行发布：--allow-dirty --no-push", file=sys.stderr)
            return EXIT_BLOCK

    commit = git_out(["rev-parse", "HEAD"], cwd=root, check=False)
    commit_short = git_out(["rev-parse", "--short", "HEAD"], cwd=root, check=False)
    when = _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    date_only = when[:10]
    ref = machine_ref(load_registry(root), mid)
    summary = args.message or f"发布于 {date_only}"

    message = build_tag_message(version, summary, mid, ref, root, commit, when)

    print("=" * 68)
    print(f" 准备发布 {tag}")
    print("=" * 68)
    print(f"  当前版本    : {cur}   →  归一化 {normalize(cur)}")
    print(f"  新版本      : {version}")
    print(f"  机器        : {machine_label(root, mid)}  (来源：{source})")
    print(f"  提交        : {commit_short}")
    print(f"  标签类型    : 附注（annotated）——携带机器归属信息")
    print("  标签内容：")
    for line in message.rstrip("\n").splitlines():
        print(f"      {line}")
    print("-" * 68)

    if args.dry_run:
        print("  [dry-run] 未做任何改动。去掉 --dry-run 即真正执行。")
        print("-" * 68)
        return EXIT_OK

    # 1) 附注标签。用 stdin 传 message，避免中文在 Windows 控制台/ argv 上被转码。
    tag_proc = subprocess.run(
        ["git", "tag", "-a", tag, "-F", "-"],
        cwd=str(root), input=message.encode("utf-8"),
        capture_output=True)
    if tag_proc.returncode != EXIT_OK:
        print("[X] 打标签失败：", file=sys.stderr)
        print(tag_proc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return EXIT_BLOCK
    print(f"[+] 已创建附注标签 {tag}")

    # 2) VERSION 文件（唯一版本号来源，供 requirements/README 引用）
    (root / VERSION_FILE).write_text(version + "\n", encoding="utf-8")
    print(f"[+] 已更新 {VERSION_FILE} = {version}")

    # 3) 发布索引
    record = {
        "version": version,
        "tag": tag,
        "date": date_only,
        "machine": mid,
        "machine_ref": ref,
        "commit": commit,
        "commit_short": commit_short,
        "notes": summary,
    }
    append_jsonl(root, record)
    append_markdown(root, record)
    print(f"[+] 已追加发布索引 {INDEX_MD} 与 {INDEX_JSONL}")

    # 4) 提交索引改动（标签已经指向"不含索引改动"的提交，这是有意的：
    #    标签应对应"代码状态"，索引只是记录这件事）
    git(["add", VERSION_FILE, INDEX_MD, INDEX_JSONL], cwd=root, check=False)
    commit_proc = git(["commit", "-m", f"chore(release): {tag} {summary}",
                       "--trailer", f"Machine: {mid}",
                       "--trailer", f"Machine-Ref: {ref}"],
                      cwd=root, check=False)
    if commit_proc.returncode != EXIT_OK:
        print("[!] 索引提交失败（可能没有改动或钩子拦下）：", file=sys.stderr)
        print(getattr(commit_proc, "stderr_text", ""), file=sys.stderr)
    else:
        print("[+] 索引改动已提交")

    if args.no_push:
        print("\n[=] --no-push：记得稍后手工推送：")
        print(f"      git push origin {tag}")
        print("      git push")
        return EXIT_OK

    push = git(["push", "origin", tag], cwd=root, check=False, timeout=180)
    if push.returncode != EXIT_OK:
        print("[X] 推送标签失败：", file=sys.stderr)
        print(getattr(push, "stderr_text", ""), file=sys.stderr)
        return EXIT_BLOCK
    print(f"[+] 标签 {tag} 已推送")

    push2 = git(["push", "origin"], cwd=root, check=False, timeout=180)
    if push2.returncode != EXIT_OK:
        print("[!] 分支推送失败（标签已推），请手工 git push：", file=sys.stderr)
        print(getattr(push2, "stderr_text", ""), file=sys.stderr)
        return EXIT_BLOCK
    print("[+] 分支已推送")
    print("=" * 68)
    print(f" 发布完成：{tag}  ({mid})")
    print("=" * 68)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
