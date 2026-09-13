#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送前检查：把"忘了上传/忘了同步/忘了标识"在 push 前**提醒**出来。

**默认是询问，不是必须。** 检查发现的问题不会被无条件拦下：

* 人在终端前 → 询问（默认「继续推送」，按 `n` 中止）
* 非交互（AI agent / CI / 管道）→ **放行 + 醒目警告**
* 想硬拦 → `YBCO_PUSH_MODE=block`

由 .git/hooks/pre-push 调用，也可手工跑：

    python sync/pre_push.py --check-only --range origin/master..HEAD
    python sync/pre_push.py --check-only --range ""          # 检查全部未推送
    python sync/pre_push.py --check-only --range "..."       # 报告模式：只警告，绝不阻断

git 通过 stdin 传三列（旧版）或四列（新版）：

    <local-ref> <local-sha> <remote-ref> <remote-sha>

`<remote-sha>` 全零表示"远端还没有这个引用"（首次推送新分支）。

检查项（🔴 = 需要你确认）
------------------------
1. 落后于上游——别的机器先推了，你现在推等于建立在旧基础上
2. 工作区有未提交改动——这些改动不会被推上去，等于白干
3. 存在 stash——最常见的"改动没上传"藏身处
4. 被推送的提交里缺 `Machine:` trailer——无法追溯是哪台机器做的

提示项（🟡，从不询问）
--------------------
未跟踪的源码类文件、命中多机共写接缝文件、未推送提交条数过多。

环境变量
--------
`YBCO_PUSH_MODE` = `ask`（默认）/ `allow` / `block`
`YBCO_SKIP_HOOKS=1` 完全跳过本检查（打印留痕）。
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

#: 强制控制询问行为：ask（默认）/ allow / block。见 _ask_or_allow 的说明。
PUSH_MODE_ENV = "YBCO_PUSH_MODE"


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
        description="推送前检查：同步、工作区、机器标识检查（默认询问，不强制阻断）。")
    ap.add_argument("--check-only", action="store_true",
                    help="非交互报告模式（不读 stdin refs，用 --range 指定；发现问题只警告，绝不阻断）")
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
    refs = read_stdin_refs()          # 必须先读：git 会用管道喂 refs
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
    print(" pre-push 检查：YBCO-TA-3.0 多机同步")
    print("=" * 68)
    for line in ok:
        print(f"  ✓ {line}")
    for line in yellow:
        print(f"  ⚠ {line}")
    if red:
        # 紧凑列出问题标题，上一条的续行（缩进的明细）跟着它走
        print(f"  ✗ 发现 {len(red)} 项需要你确认：")
        for line in red:
            print(f"      {line}" if line.startswith((" ", "\t")) else f"    · {line}")
    print("-" * 68)

    if not red:
        print(" 检查通过，开始推送。")
        print("-" * 68)
        return EXIT_OK

    if args.check_only:
        # --check-only 是给脚本/CI 用的报告模式：只警告，绝不阻断
        print(f" [!] 上述 {len(red)} 项仅为警告（--check-only 模式不阻断推送）。")
        print("     请在方便时处理，或跑 python sync/check.py 看完整报告。")
        print("-" * 68)
        return EXIT_OK

    return _ask_or_allow(len(red))


def _ask_or_allow(n_red: int) -> int:
    """发现问题后怎么办：**默认询问**，不是必须。

    行为矩阵（为什么这么设计见 docs/multi-machine.md §3.3）：

    | 场景 | 行为 |
    |---|---|
    | 人在终端前 | **询问**：默认「继续推送」，按 n 中止。选 a 则本次会话不再问 |
    | 非交互（AI agent / CI / 管道 / stdin 已关闭） | **放行 + 醒目警告**：人不在场时宁可不拦，也不阻断自动化流程 |
    | `YBCO_PUSH_MODE=ask` | 强制询问（非交互时会得到 EOF，按"放行"处理） |
    | `YBCO_PUSH_MODE=allow` | 强制放行（只打印警告） |
    | `YBCO_PUSH_MODE=block` | 强制阻断（给"我不想被问、就要硬拦"的人） |

    设计取舍：把"提醒"与"拦截"分开。**默认放行**意味着这套机制不会成为
    自动化的路障；要真正硬拦，显式设 `YBCO_PUSH_MODE=block` 即可——
    这比"默认拦住、需要时到处加绕过环境变量"更不容易演变成
    "反正每次都要加 --no-verify"。
    """
    mode = os.environ.get(PUSH_MODE_ENV, "").strip().lower()

    if mode not in ("", "ask", "allow", "block"):
        print(f" [!] {PUSH_MODE_ENV}={mode!r} 不是有效值"
              "（可选 ask / allow / block），按 ask 处理。")
        mode = "ask"

    if mode == "block":
        print(f" 推送被中止：{n_red} 项需要确认，而 {PUSH_MODE_ENV}=block。")
        print(" 逐条处理后再推，或改成 allow / ask。")
        print("-" * 68)
        return EXIT_BLOCK

    if mode == "allow":
        _print_allowed_banner(n_red, reason=f"{PUSH_MODE_ENV}=allow")
        return EXIT_OK

    # 默认 ask：只有 stdin 是终端才真的问得出来。
    # 注意此时 refs 已经从 stdin 读完，isatty() 反映的是原始标准输入。
    if not sys.stdin or not sys.stdin.isatty():
        _print_allowed_banner(
            n_red,
            reason="当前不是交互式终端（AI agent / CI / 管道），无法询问")
        return EXIT_OK

    print(f" 以上 {n_red} 项通常意味着『有改动没上传』或『在旧基础上推送』。")
    print(" 请确认： [Enter/y] 继续推送    [n] 中止（我先去处理）"
          "    [a] 继续，且本次会话不再询问")
    try:
        answer = input(" 继续推送？ ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        _print_allowed_banner(n_red, reason="未收到答复（输入已关闭或被打断）")
        return EXIT_OK

    if answer in ("n", "no", "abort", "q", "quit"):
        print("-" * 68)
        print(" 已中止推送。建议先跑：")
        print("     python sync/check.py      # 看完整报告与补救命令")
        print("-" * 68)
        return EXIT_BLOCK

    if answer in ("a", "always"):
        print(f" [=] 本次会话不再询问。想永久生效请设置 {PUSH_MODE_ENV}=allow；")
        print(f"     想反过来硬拦请设置 {PUSH_MODE_ENV}=block。")
        # 同一次 push 只会跑一次钩子，所以这里改环境变量实际影响的是
        # 后续由本进程派生的子进程；保留它纯属给用户一个"我确认过了"的语义标记。
        os.environ[PUSH_MODE_ENV] = "allow"

    _print_allowed_banner(n_red, reason="你选择继续")
    return EXIT_OK


def _print_allowed_banner(n_red: int, reason: str) -> None:
    """放行时的醒目留痕。即使放行，也必须让人在滚动缓冲区里看得见。"""
    print("-" * 68)
    print(f" [!] 带着 {n_red} 项未处理的问题继续推送（{reason}）。")
    print("     这些项没有消失，只是没有被拦下来。建议稍后跑一次：")
    print("         python sync/check.py")
    print(f"     想改成硬拦：{PUSH_MODE_ENV}=block git push")
    print("-" * 68)


if __name__ == "__main__":
    sys.exit(main())
