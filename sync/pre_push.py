#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送前门禁：把"忘了上传/忘了同步/忘了标识"挡在 push 之前。

由 .git/hooks/pre-push 调用，也可手工跑：

    python sync/pre_push.py --check-only --range origin/master..HEAD
    python sync/pre_push.py --check-only --range ""          # 检查全部未推送

git 通过 stdin 传三列（旧版）或四列（新版）：

    <local-ref> <local-sha> <remote-ref> <remote-sha>

`<remote-sha>` 全零表示"远端还没有这个引用"（首次推送新分支）。

阻断项（🔴，退出码 1 → git 中止 push）
------------------------------------
1. 落后于上游——别的机器先推了，你现在推等于建立在旧基础上
2. 工作区有未提交改动——这些改动不会被推上去，等于白干
3. 存在 stash——最常见的"改动没上传"藏身处
4. 被推送的提交里缺 `Machine:` trailer——无法追溯是哪台机器做的

提示项（🟡，不阻断）
------------------
未跟踪的源码类文件、命中多机共写接缝文件、未推送提交条数过多。

逃生阀：`YBCO_SKIP_HOOKS=1 git push ...`，会打印醒目提示留痕。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_BLOCK, EXIT_CONFIG_ERROR, EXIT_OK, SKIP_ENV,
    ConfigError, configure_stdout, git, git_out, has_machine_trailer,
    is_whitelisted_untracked, looks_like_code, machine_identity,
    repo_root, trailer_value,
)

configure_stdout()

ZERO_SHA = "0" * 40
GIT_FMT = "%h%x1f%s"

#: 单次推送超过这个数量就提醒一句（通常是积压太久）
BIG_PUSH = 20


def read_stdin_refs() -> list[tuple[str, str, str, str]]:
    """读 git 传来的 ref 行；手工运行时 stdin 是终端则返回空。"""
    if sys.stdin is None or sys.stdin.isatty():
        return []
    rows = []
    for line in sys.stdin:
        parts = line.split()
        if len(parts) >= 4:
            rows.append((parts[0], parts[1], parts[2], parts[3]))
        elif len(parts) == 2:      # 极老版本 git 只有 two-column 形式
            rows.append((parts[0], parts[1], parts[0], ""))
    return rows


def commits_in_range(root: Path, revrange: str) -> list[dict]:
    out = git_out(["log", f"--format={GIT_FMT}", revrange], cwd=root, check=False)
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) >= 2:
            rows.append({"short": parts[0], "subject": parts[1]})
    return rows


def check(root: Path, revranges: list[str]) -> tuple[list[str], list[str], list[str]]:
    """返回 (阻断项, 提示项, 已通过项)。三者都是人类可读的整句。"""
    red: list[str] = []
    yellow: list[str] = []
    ok: list[str] = []

    # ── 1. 机器代号是否认得 ───────────────────────────────────────────────
    try:
        mid, source = machine_identity(root)
        ok.append(f"本机代号 {mid}（来源：{source}）")
    except ConfigError as exc:
        red.append("本机没有机器代号，提交无法追溯来源")
        red.append("    " + str(exc).replace("\n", "\n    "))
        mid = ""

    # ── 2. 落后于上游？ ───────────────────────────────────────────────────
    upstream = git_out(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                       cwd=root, check=False)
    if upstream:
        fetch = git(["fetch", "--quiet"], cwd=root, check=False, timeout=120)
        if fetch.returncode != EXIT_OK:
            yellow.append("fetch 失败，无法确认是否落后于远端"
                          "（网络或认证问题）")
        counts = git_out(["rev-list", "--left-right", "--count",
                          f"{upstream}...HEAD"], cwd=root, check=False)
        try:
            behind, ahead = (int(x) for x in counts.split())
        except (ValueError, TypeError):
            behind = ahead = 0
        if behind:
            red.append(f"落后 {upstream} {behind} 个提交——别的机器已经推过了")
            theirs = git_out(["log", "--format=" + GIT_FMT,
                              f"HEAD..{upstream}"], cwd=root, check=False)
            for line in theirs.splitlines()[:8]:
                p = line.split("\x1f")
                if len(p) >= 2:
                    red.append(f"    {p[0]} {p[1]}")
            red.append("    先同步：git pull --rebase")
        else:
            ok.append(f"不落后于 {upstream}")
    else:
        yellow.append("当前分支没有上游，跳过落后检查")

    # ── 3. 工作区干净吗 ───────────────────────────────────────────────────
    modified = [ln for ln in git_out(["status", "--porcelain"],
                                     cwd=root, check=False).splitlines()
                if ln.strip() and not ln.startswith("??")]
    if modified:
        red.append(f"{len(modified)} 个已跟踪文件有未提交改动——不会随 push 上传")
        for ln in modified[:10]:
            red.append(f"    {ln}")
        if len(modified) > 10:
            red.append(f"    ...还有 {len(modified) - 10} 个")

    others = [p for p in git_out(["ls-files", "--others", "--exclude-standard"],
                                 cwd=root, check=False).splitlines()
              if p.strip() and not is_whitelisted_untracked(p)]
    code_others = [p for p in others if looks_like_code(p)]
    if code_others:
        red.append(f"{len(code_others)} 个源码类文件未跟踪——换机器就丢")
        for p in code_others[:10]:
            red.append(f"    {p}")
        if len(code_others) > 10:
            red.append(f"    ...还有 {len(code_others) - 10} 个")
    elif others:
        yellow.append(f"{len(others)} 个未跟踪的其它文件（多为数据/产物，"
                      "确认确实不需要入库即可）")

    stashes = [ln for ln in git_out(["stash", "list"], cwd=root, check=False).splitlines()
               if ln.strip()]
    if stashes:
        red.append(f"有 {len(stashes)} 个 stash 没处理——最常见的『忘了上传』现场")
        for ln in stashes[:5]:
            red.append(f"    {ln}")

    if not modified and not code_others and not stashes:
        ok.append("工作区干净（已跟踪文件无改动、无待入库源码、无 stash）")

    # ── 4. 被推送的提交都有机器标识吗 ─────────────────────────────────────
    all_commits: list[dict] = []
    for rng in revranges:
        all_commits.extend(commits_in_range(root, rng))
    if all_commits:
        missing = []
        for c in all_commits:
            msg = git_out(["log", "-1", "--format=%B", c["short"]],
                          cwd=root, check=False)
            machine = trailer_value(msg, "Machine")
            if not machine:
                missing.append(c)
            else:
                c["machine"] = machine
        if missing:
            red.append(f"{len(missing)}/{len(all_commits)} 个待推送提交没有 "
                       "Machine: 标识，无法追溯是哪台机器做的")
            for c in missing[:10]:
                red.append(f"    {c['short']} {c['subject']}")
            red.append("    补法：git rebase --exec "
                       "'python sync/trailer.py --amend-head' " + (upstream or "<base>"))
            red.append("    （会给这些提交补上标识，并改写它们的 SHA——"
                       "仅对未推送提交安全）")
        else:
            ok.append(f"{len(all_commits)} 个待推送提交均带 Machine: 标识")
        if len(all_commits) > BIG_PUSH:
            yellow.append(f"本次要推 {len(all_commits)} 个提交，积压有点多——"
                          "建议以后每完成一小步就推一次")

    return red, yellow, ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pre_push.py",
        description="推送前门禁：同步、工作区、机器标识三项检查。")
    ap.add_argument("--check-only", action="store_true",
                    help="手工运行（不读 stdin refs，用 --range 指定）")
    ap.add_argument("--range", dest="ranges", action="append", default=[],
                    help="手工指定提交范围，可重复；空字符串=全部未推送")
    args = ap.parse_args(argv)

    if os.environ.get(SKIP_ENV):
        print("=" * 68)
        print(f" [!] {SKIP_ENV} 已设置——本次 pre-push 检查被跳过。")
        print("     这是留痕行为：绕过会记在这里，回头请自查一遍 sync/check.py。")
        print("=" * 68)
        return EXIT_OK

    root = repo_root()

    # 确定要检查的提交范围
    revranges = list(args.ranges)
    refs = read_stdin_refs()
    if not revranges and refs:
        for _local_ref, local_sha, _remote_ref, remote_sha in refs:
            if local_sha == ZERO_SHA:
                continue        # 删除远端分支，没有要检查的提交
            if remote_sha and remote_sha != ZERO_SHA:
                revranges.append(f"{remote_sha}..{local_sha}")
            else:
                upstream = git_out(
                    ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                    cwd=root, check=False)
                revranges.append(f"{upstream}..{local_sha}" if upstream
                                 else local_sha)
    if not revranges:
        upstream = git_out(["rev-parse", "--abbrev-ref",
                            "--symbolic-full-name", "@{u}"], cwd=root, check=False)
        revranges = [f"{upstream}..HEAD"] if upstream else []

    # 空范围字符串归一化为"全部未推送"
    revranges = [r for r in revranges if r.strip()]

    red, yellow, ok = check(root, revranges)

    print("=" * 68)
    print(" pre-push 门禁：YBCO-TA-3.0 多机同步检查")
    print("=" * 68)
    for line in ok:
        print(f"  ✓ {line}")
    for line in yellow:
        print(f"  ⚠ {line}")
    for line in red:
        print(f"  ✗ {line}")
    print("-" * 68)

    if red:
        print(f" 推送被中止：{len(red)} 项阻断。逐条处理后再推。")
        print(f" 确认要绕过：{SKIP_ENV}=1 git push   （会打印留痕提示）")
        print("-" * 68)
        return EXIT_BLOCK

    print(" 门禁通过，开始推送。")
    print("-" * 68)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
