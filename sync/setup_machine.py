#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新机器一次性引导：登记机器代号、配置 git 身份、采样本机事实、安装钩子。

用法
----
    python sync/setup_machine.py --id lab-smlab              # 已登记，直接认机
    python sync/setup_machine.py --add --id lab-pc2 \\
        --label "实验机2" --role lab                          # 新机器入册并认机
    python sync/setup_machine.py --id pc-teski --no-hooks    # 只认机，不装钩子
    python sync/setup_machine.py --show                      # 只看当前状态，不改任何东西

它到底改了什么（全部是本机局部、可逆）
------------------------------------
1. `machines/machines.json` —— 仅在 `--add` 时追加一条（这是**入库**文件，需要提交）
2. `git config --local ybco.machine <id>` —— 机器代号的权威来源
3. `git config --local user.name/user.email` —— 消除两机邮箱漂移
   （历史上本仓出现过 teskiel7@gmail.com 与 teskiel@users.noreply.github.com 并存）
4. `machines/machines.local.json` —— **不入库**，记录本机真实路径/主机名/Python 版本
5. `.git/hooks/{prepare-commit-msg,commit-msg,pre-push}` —— 见 sync/install_hooks.py

为什么需要第 3 步
----------------
`git config --global user.email` 是每台机器各自设的，跨机提交时作者信息会漂移，
让"这条提交是哪台机器做的"更难判断。本脚本把身份钉在**仓库局部配置**里，
这样同一个仓库在任意机器上产出的作者信息是一致的，机器差异只体现在
`Machine:` trailer 上——职责分离。

退出码：0 成功 / 2 配置或参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CFG_MACHINE, EXIT_CONFIG_ERROR, EXIT_OK, MACHINE_ID_RE,
    ConfigError, configure_stdout, find_machine, git, git_out,
    load_registry, machine_identity, repo_root,
)

configure_stdout()

VALID_ROLES = ("dev", "lab", "archive")


# ── 展示 ──────────────────────────────────────────────────────────────────

def cmd_show(root: Path) -> int:
    """打印本机认定状态，不做任何修改。"""
    reg = load_registry(root)
    host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"

    print("── 本机识别 ─────────────────────────────────────────────")
    print(f"  主机名        : {host}")
    print(f"  操作系统      : {platform.system()} {platform.release()}")
    print(f"  Python        : {platform.python_version()}")
    print(f"  仓库根        : {root}")

    try:
        mid, source = machine_identity(root)
        print(f"  机器代号      : {mid}   (来源：{source})")
        entry = find_machine(reg, mid)
        if entry:
            print(f"  显示名        : {entry.get('label', '')}")
            print(f"  角色          : {entry.get('role', '')}")
        else:
            print("  ⚠ 代号不在注册表中——可能是手写 git config 后没入册")
    except ConfigError as exc:
        print("  机器代号      : ✗ 未配置")
        print(f"    {str(exc).replace(chr(10), chr(10) + '    ')}")

    local = root / "machines" / "machines.local.json"
    print(f"  本机事实文件  : {'存在' if local.exists() else '不存在'} "
          f"({local.as_posix()})")

    print("\n── git 局部身份 ─────────────────────────────────────────")
    for key in ("user.name", "user.email", CFG_MACHINE):
        print(f"  {key:<14}: {git_out(['config', '--local', key], check=False) or '(未设置)'}")

    print("\n── 已登记机器 ───────────────────────────────────────────")
    for m in reg.get("machines", []):
        hn = m.get("hostname") or "<待回填>"
        print(f"  {m.get('id', '?'):<12} {m.get('role', '?'):<8} "
              f"{hn:<20} {m.get('label', '')}")
    return EXIT_OK


# ── 入册 ──────────────────────────────────────────────────────────────────

def add_to_registry(root: Path, machine_id: str, label: str, role: str) -> dict:
    """把新机器追加进 machines/machines.json，返回新条目。"""
    if not MACHINE_ID_RE.match(machine_id):
        raise ConfigError(
            f"机器代号 '{machine_id}' 不合规。\n"
            "  规则：小写字母/数字/连字符，2–24 位，首字符须是字母或数字。\n"
            "  建议写法：pc-<用途> / lab-<用户名> ，例如 pc-teski、lab-smlab。")

    path = root / "machines" / "machines.json"
    data = load_registry(root)
    if find_machine(data, machine_id):
        raise ConfigError(f"代号 '{machine_id}' 已存在于注册表，去掉 --add 直接认机即可。")

    host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or ""
    entry = {
        "id": machine_id,
        "label": label or machine_id,
        "role": role,
        "hostname": host,
        "os": platform.system(),
        "python": platform.python_version(),
        "repo_path": root.as_posix(),
        "notes": f"由 sync/setup_machine.py --add 于 {_today()} 自动入册。",
    }
    data["machines"].append(entry)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[+] 已入册 {machine_id}（hostname={host or '空'}，role={role}）")
    print(f"    注册表已改动：{path.as_posix()}")
    print("    ⚠ 这是入库文件，请提交：git add machines/machines.json")
    return entry


def write_local_facts(root: Path, machine_id: str) -> Path:
    """写 machines/machines.local.json（不入库），记录本机真实事实。"""
    path = root / "machines" / "machines.local.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    existing.setdefault("_why", "本机真实路径与事实。**不入库**（.gitignore 已排除）："
                               "机器路径、仪器地址、用户名属于环境信息，不进版本历史。")
    existing.setdefault("machines", {})
    existing["machines"][machine_id] = {
        "hostname": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "repo_path": root.as_posix(),
        "detected_at": _today(),
    }
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ── 认机 ──────────────────────────────────────────────────────────────────

def configure_identity(root: Path, machine_id: str, email: str | None,
                       name: str | None) -> None:
    """把机器代号与作者身份写进**仓库局部** git 配置。"""
    git(["config", "--local", CFG_MACHINE, machine_id], cwd=root)
    print(f"[+] git config --local {CFG_MACHINE} = {machine_id}")

    if email:
        git(["config", "--local", "user.email", email], cwd=root)
        print(f"[+] git config --local user.email = {email}")
    if name:
        git(["config", "--local", "user.name", name], cwd=root)
        print(f"[+] git config --local user.name = {name}")

    cur_email = git_out(["config", "--local", "user.email"], cwd=root, check=False)
    if not cur_email:
        print("[!] 本仓库还没设 user.email，提交会落到全局配置上。")
        print("    建议固定为：--email teskiel@users.noreply.github.com")


def install_hooks(root: Path) -> int:
    """调用 sync/install_hooks.py 安装钩子（失败不阻断认机流程）。"""
    import subprocess
    script = root / "sync" / "install_hooks.py"
    if not script.exists():
        print("[!] 找不到 sync/install_hooks.py，跳过钩子安装。")
        return EXIT_CONFIG_ERROR
    proc = subprocess.run([sys.executable, str(script), "install"],
                          cwd=str(root), capture_output=True)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if out:
        print(out.rstrip())
    if err:
        print(err.rstrip(), file=sys.stderr)
    return proc.returncode


# ── 主流程 ────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="setup_machine.py",
        description="登记/认定本机机器代号，并安装同步钩子。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", dest="machine_id",
                    help="机器代号（例如 pc-teski、lab-smlab）")
    ap.add_argument("--add", action="store_true",
                    help="把 --id 作为新机器追加进 machines/machines.json")
    ap.add_argument("--label", default="", help="显示名，配合 --add 使用")
    ap.add_argument("--role", default="dev", choices=VALID_ROLES,
                    help="机器角色（默认 dev）")
    ap.add_argument("--email", default="",
                    help="写进仓库局部的 user.email（建议 noreply 地址）")
    ap.add_argument("--name", default="", help="写进仓库局部的 user.name")
    ap.add_argument("--no-hooks", action="store_true", help="跳过 git 钩子安装")
    ap.add_argument("--show", action="store_true",
                    help="只显示当前状态，不做任何修改")
    args = ap.parse_args(argv)

    root = repo_root()

    if args.show or not args.machine_id:
        return cmd_show(root)

    try:
        if args.add:
            add_to_registry(root, args.machine_id, args.label, args.role)
        else:
            if not find_machine(load_registry(root), args.machine_id):
                raise ConfigError(
                    f"代号 '{args.machine_id}' 不在注册表中。\n"
                    f"  已登记：{', '.join(m.get('id', '') for m in load_registry(root)['machines'])}\n"
                    f"  新机器请加 --add：python sync/setup_machine.py --add "
                    f"--id {args.machine_id} --label \"<显示名>\" --role <dev|lab|archive>")

        configure_identity(root, args.machine_id, args.email or None,
                           args.name or None)
        local = write_local_facts(root, args.machine_id)
        print(f"[+] 本机事实已写入 {local.as_posix()}（不入库）")

        if not args.no_hooks:
            rc = install_hooks(root)
            if rc != EXIT_OK:
                print("[!] 钩子安装未成功，可稍后单独重试："
                      "python sync/install_hooks.py install")

        print("\n[✓] 本机已认定为 " + args.machine_id)
        print("    下一步：python sync/check.py    # 看看与 origin 的差距")
        return EXIT_OK

    except ConfigError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":
    sys.exit(main())
