#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器标识 trailer 的补齐与校验。由 git 钩子调用，也可手工用。

为什么用 trailer 而不是别的办法
------------------------------
commit 的作者信息（user.name/user.email）是**人**的属性，跨机器本来就应该一致；
"这条提交是哪台机器做的"是**环境**的属性。两者混在一起就没法查。git 的
trailer 机制（末尾的 `Key: value` 行）正是为此设计的：机器信息独立成行，
`git log --format=%(trailers:key=Machine)` 可直接提取。

约定格式
--------
    feat: 新增 xxx 功能

    正文……

    Machine: pc-teski
    Machine-Ref: DESKTOP-FJA4R3U/teski/Windows/py3.12.10

用法
----
    python sync/trailer.py --file .git/COMMIT_EDITMSG              # 补齐（prepare-commit-msg）
    python sync/trailer.py --check .git/COMMIT_EDITMSG             # 只校验（commit-msg）
    python sync/trailer.py --amend-head                            # 给 HEAD 补 trailer
    python sync/trailer.py --message-file <文件> --print           # 只看结果不落盘

退出码：0 通过 / 1 校验失败或缺 trailer（钩子用 1 中止 commit）/ 2 环境错误。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_BLOCK, EXIT_CONFIG_ERROR, EXIT_OK, SKIP_ENV, TRAILER_MACHINE,
    TRAILER_MACHINE_REF, ConfigError, configure_stdout, git,
    has_machine_trailer, load_registry, machine_identity, machine_ref,
    repo_root, trailer_value,
)

configure_stdout()

#: git 生成"空消息"或脚本化消息时给我们的 source。
#: `message` 表示消息由 `-m` 直接给出，prepare-commit-msg 阶段塞 trailer 是
#: 安全的；`template`/`merge`/`squash`/`commit` 各有语义，统一按"已存在则跳过、
#: 不存在则追加"处理即可。
SKIP_SOURCES = frozenset({"merge"})   # merge 消息由 git 决定，别动


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _identity(root: Path):
    mid, source = machine_identity(root)
    ref = machine_ref(load_registry(root), mid)
    return mid, ref, source


def build_trailer_block(mid: str, ref: str) -> str:
    return f"{TRAILER_MACHINE}: {mid}\n{TRAILER_MACHINE_REF}: {ref}\n"


def append_trailers(message: str, mid: str, ref: str) -> tuple[str, bool]:
    """在消息末尾追加 trailer 块。返回 (新消息, 是否改动)。

    规则：
    * 已有 `Machine:` → 原样返回（**不覆盖**，尊重历史）
    * 已有 `Machine-Ref:` 而无 `Machine:` → 只补 `Machine:`
    * 注释行（`#` 开头）与 scissors 行之后的内容不属于提交消息正文，但
      追加以空白行开头即可让 git 正确剥离——这里统一追加到全文末尾并保证
      前面有空行分隔。
    """
    changed = False
    lines = message.splitlines()

    has_machine = has_machine_trailer(message)
    has_ref = bool(trailer_value(message, TRAILER_MACHINE_REF))

    add: list[str] = []
    if not has_machine:
        add.append(f"{TRAILER_MACHINE}: {mid}")
        changed = True
    if not has_ref:
        add.append(f"{TRAILER_MACHINE_REF}: {ref}")
        changed = True

    if not changed:
        return message, False

    body = "\n".join(lines).rstrip("\n")
    # 补一个空行分隔，避免和上一行正文粘连导致 git 不认它是 trailer
    return body + "\n\n" + "\n".join(add) + "\n", True


def cmd_file(path: Path, root: Path, do_check: bool, show: bool) -> int:
    if not path.exists():
        print(f"[X] 消息文件不存在：{path}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    message = _read(path)

    # 空消息/纯注释（用户没写东西）：交给 git 自己报 "empty commit message"，
    # 这里不插手，否则会把"我忘了写消息"变成"机器标识缺失"，误导人。
    meaningful = [ln for ln in message.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful:
        return EXIT_OK

    try:
        mid, ref, _ = _identity(root)
    except ConfigError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if do_check:
        if has_machine_trailer(message):
            return EXIT_OK
        print("", file=sys.stderr)
        print("[X] 这条提交没有机器标识（Machine: trailer）。", file=sys.stderr)
        print(f"    本机代号是 {mid}。提交会被拒绝，因为跨机协作时", file=sys.stderr)
        print("    『这条改动出自哪台电脑』是排查进度不一致的第一手信息。", file=sys.stderr)
        print("", file=sys.stderr)
        print("    补法 A（推荐，钩子会自动做）——重新提交：", file=sys.stderr)
        print("        git commit --amend --no-edit", file=sys.stderr)
        print("    补法 B ——手工在消息末尾加两行：", file=sys.stderr)
        print(f"        {TRAILER_MACHINE}: {mid}", file=sys.stderr)
        print(f"        {TRAILER_MACHINE_REF}: {ref}", file=sys.stderr)
        print("", file=sys.stderr)
        print(f"    确要绕过：{SKIP_ENV}=1 git commit ...", file=sys.stderr)
        return EXIT_BLOCK

    new_message, changed = append_trailers(message, mid, ref)
    if show:
        print(new_message, end="")
        return EXIT_OK
    if changed:
        path.write_text(new_message, encoding="utf-8")
    return EXIT_OK


def cmd_amend_head(root: Path) -> int:
    """给 HEAD 补 trailer（供 rebase --exec 批量修补历史用）。

    只改提交消息，不改树、不改作者。会改变该提交及其后继的 SHA——
    所以**只能用在尚未推送的提交上**。
    """
    try:
        mid, ref, _ = _identity(root)
    except ConfigError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    head_msg = git(["log", "-1", "--format=%B"], cwd=root, check=False)
    msg = getattr(head_msg, "stdout_text", "")
    if has_machine_trailer(msg):
        return EXIT_OK

    missing = []
    if not has_machine_trailer(msg):
        missing.append(f"{TRAILER_MACHINE}: {mid}")
    if not trailer_value(msg, TRAILER_MACHINE_REF):
        missing.append(f"{TRAILER_MACHINE_REF}: {ref}")

    args = ["commit", "--amend", "--no-edit", "--no-verify"]
    for t in missing:
        args += ["--trailer", t]
    proc = git(args, cwd=root, check=False)
    if proc.returncode != EXIT_OK:
        print(getattr(proc, "stderr_text", ""), file=sys.stderr)
        return EXIT_BLOCK
    print(f"[+] HEAD 已补 trailer：{', '.join(missing)}")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="trailer.py",
        description="补齐/校验提交消息里的 Machine: 机器标识 trailer。")
    ap.add_argument("--file", dest="file", help="commit message 文件路径")
    ap.add_argument("--check", action="store_true", help="只校验，不写回")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="打印结果而不写回文件")
    ap.add_argument("--amend-head", action="store_true",
                    help="给 HEAD 提交补 trailer（改写其 SHA）")
    ap.add_argument("--source", default="", help="prepare-commit-msg 的 source 参数")
    args = ap.parse_args(argv)

    root = repo_root()

    if os.environ.get(SKIP_ENV):
        print(f"[!] {SKIP_ENV} 已设置，跳过机器标识检查（已记录在案）",
              file=sys.stderr)
        return EXIT_OK

    if args.amend_head:
        return cmd_amend_head(root)

    if not args.file:
        ap.print_help()
        return EXIT_CONFIG_ERROR

    if args.source in SKIP_SOURCES:
        return EXIT_OK

    return cmd_file(Path(args.file), root, args.check, args.show)


if __name__ == "__main__":
    sys.exit(main())
