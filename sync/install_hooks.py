#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 sync/hooks/ 里的三个钩子装进 .git/hooks/（本机、一次性）。

为什么不用 `git config core.hooksPath`
------------------------------------
`core.hooksPath` 会把**整个** hooks 目录指向别处，于是任何其它工具（编辑器插件、
安全扫描、`pre-commit` 框架）装在同目录的钩子都会一起失效。本仓库已有
`Auto_Sweep/.claude/` 这类工具痕迹，风险不值得冒。因此这里只做三件事：
写文件、加可执行位、把已存在的同名钩子备份走。

用法
----
    python sync/install_hooks.py install      # 安装/更新（幂等）
    python sync/install_hooks.py --status     # 看装了没、是不是最新
    python sync/install_hooks.py --uninstall  # 卸掉（只删本工具装的那些）

安装后想确认：`ls -l .git/hooks/` 应看到三个可执行文件，
或直接 `python sync/check.py`。
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_CONFIG_ERROR, EXIT_OK, configure_stdout, git_out, repo_root,
)

configure_stdout()

HOOK_NAMES = ("prepare-commit-msg", "commit-msg", "pre-push")
BACKUP_SUFFIX = ".bak-ybco"
MARKER = "YBCO-TA-3.0"


def hooks_dir(root: Path) -> Path:
    """取 git 真实的 hooks 路径（尊重 core.hooksPath，虽然我们不设它）。"""
    p = git_out(["rev-parse", "--git-path", "hooks"], cwd=root, check=False)
    if p:
        path = Path(p)
        return path if path.is_absolute() else (root / path)
    return root / ".git" / "hooks"


def is_ours(path: Path) -> bool:
    try:
        return MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def make_executable(path: Path) -> None:
    """加可执行位。Windows 上 chmod 基本是空操作，git-for-windows 靠后缀/内容执行，
    但为了 Linux/macOS 上的 clone 一致，仍然设上。"""
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def install(root: Path) -> int:
    src_dir = root / "sync" / "hooks"
    dst_dir = hooks_dir(root)
    dst_dir.mkdir(parents=True, exist_ok=True)

    missing = [n for n in HOOK_NAMES if not (src_dir / n).exists()]
    if missing:
        print(f"[X] 源钩子缺失：{', '.join(missing)}（应在 {src_dir.as_posix()}）",
              file=sys.stderr)
        return EXIT_CONFIG_ERROR

    installed, updated, backed_up, unchanged = [], [], [], []
    for name in HOOK_NAMES:
        src, dst = src_dir / name, dst_dir / name
        if dst.exists() and not is_ours(dst):
            backup = dst.with_name(dst.name + BACKUP_SUFFIX)
            shutil.copy2(dst, backup)
            backed_up.append(backup.name)
            print(f"[!] {name} 已被别的工具占用，原文件备份为 {backup.name}")
        existed = dst.exists()
        same = existed and is_ours(dst) and filecmp.cmp(src, dst, shallow=False)
        shutil.copyfile(src, dst)
        make_executable(dst)
        if same:
            unchanged.append(name)
        elif existed:
            updated.append(name)
        else:
            installed.append(name)

    print(f"[+] 钩子目录：{dst_dir.as_posix()}")
    if installed:
        print(f"[+] 新装：{', '.join(installed)}")
    if updated:
        print(f"[+] 更新：{', '.join(updated)}")
    if unchanged:
        print(f"[=] 已是最新：{', '.join(unchanged)}")
    if backed_up:
        print(f"[!] 备份了原有钩子：{', '.join(backed_up)}"
              "（要恢复就删掉我们的版本并改回名字）")

    print("\n现在起：")
    print("  · 提交会自动带 Machine: <机器代号> 标识")
    print("  · 忘记同步/忘记提交/忘记标识时，push 会被挡下来并说明原因")
    print(f"  · 紧急绕过：YBCO_SKIP_HOOKS=1 git push")
    return EXIT_OK


def status(root: Path) -> int:
    src_dir = root / "sync" / "hooks"
    dst_dir = hooks_dir(root)
    print(f"钩子目录：{dst_dir.as_posix()}")
    rc = EXIT_OK
    for name in HOOK_NAMES:
        src, dst = src_dir / name, dst_dir / name
        if not dst.exists():
            print(f"  ✗ {name:<20} 未安装")
            rc = 1
            continue
        if not is_ours(dst):
            print(f"  ⚠ {name:<20} 已存在但不是本工具装的（可能是别的工具）")
            rc = 1
            continue
        same = filecmp.cmp(src, dst, shallow=False)
        if same:
            print(f"  ✓ {name:<20} 已安装且是最新版")
        else:
            print(f"  ⚠ {name:<20} 已安装但内容落后于 sync/hooks/"
                  "（重跑 install 更新）")
            rc = 1
    return rc


def uninstall(root: Path) -> int:
    dst_dir = hooks_dir(root)
    removed, restored = [], []
    for name in HOOK_NAMES:
        dst = dst_dir / name
        if not dst.exists() or not is_ours(dst):
            continue
        dst.unlink()
        removed.append(name)
        backup = dst.with_name(dst.name + BACKUP_SUFFIX)
        if backup.exists():
            shutil.copy2(backup, dst)
            backup.unlink()
            restored.append(name)
    if removed:
        print(f"[-] 已卸载：{', '.join(removed)}")
    if restored:
        print(f"[+] 已恢复原有钩子：{', '.join(restored)}")
    if not removed:
        print("[=] 没有本工具安装的钩子，无需卸载")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="install_hooks.py",
        description="安装/查看/卸载 YBCO-TA-3.0 的 git 同步钩子。")
    ap.add_argument("action", nargs="?", default="install",
                    choices=("install", "uninstall"),
                    help="install（默认）或 uninstall")
    ap.add_argument("--status", action="store_true", help="只看状态")
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    args = ap.parse_args(argv)

    root = repo_root()
    if args.status:
        return status(root)
    if args.uninstall or args.action == "uninstall":
        return uninstall(root)
    return install(root)


if __name__ == "__main__":
    sys.exit(main())
