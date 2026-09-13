#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开工 / 收工体检：一眼看清"本机 vs 其它机器"的全部差距。

用法
----
    python sync/check.py            # 人类可读报告（开工、收工各跑一次）
    python sync/check.py --json     # 机读，给脚本/CI/AI 用
    python sync/check.py --strict   # 有 🔴 就退出码 1（可用于自动化门禁）
    python sync/check.py --offline  # 不联网（跳过 fetch 与落后比较）

它回答的五个问题
----------------
1. 我是谁        —— 本机机器代号，以及这个认定是从哪来的
2. 我落后了吗    —— 相对 origin 领先/落后几个提交；落后时对方新增了什么
3. 我漏交了吗    —— 未跟踪但"看起来该入库"的文件、stash、接缝文件改动
4. 我漏推了吗    —— 已提交但没推送的，逐条列出，缺 Machine: trailer 的单独标出
5. 有什么风险    —— master 上的 merge 提交（违反 pull --rebase 铁律）等

设计原则
--------
* **只读**：任何模式下都不改工作区、不改索引、不打标签。fetch 是唯一的外部
  动作（`--offline` 可关），且 fetch 不改变工作区。
* **不猜**：机器认定失败直接报错并给命令，不静默回退到 unknown。
* **可解释**：每条结论都附证据（哪个提交、哪个文件、哪个命令），
  让"为什么不让我推"永远有答案。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    EXIT_BLOCK, EXIT_CONFIG_ERROR, EXIT_OK, SKIP_ENV,
    ConfigError, configure_stdout, git, git_out, has_machine_trailer,
    is_whitelisted_untracked, load_registry, looks_like_code,
    machine_identity, repo_root, trailer_value,
)

configure_stdout()

#: 多机共写的"接缝文件"——两台机器同时改它就会冲突或互相覆盖。
#: 判定依据：这些文件存放的是**测量状态/仪器默认值**，不是代码逻辑。
SEAM_FILES = (
    "Data_process/resonance_table.txt",
    "Auto_Sweep/app_settings.json",
    "Auto_Sweep/presets/lakeshore_default.json",
    "Auto_Sweep/presets/laser_default.json",
    "Auto_Sweep/presets/vna_default.json",
    "Noisesweep/gui_config.json",
    "Noisesweep/noisesweep_config.json",
)

#: 合并类提交的判定：一个提交有 2 个以上父提交
GIT_FMT = "%H%x1f%h%x1f%an%x1f%ad%x1f%s"


@dataclass
class Issue:
    level: str          # "red" | "yellow" | "info" | "ok"
    area: str           # "identity" | "origin" | "worktree" | "unpushed" | "risk"
    text: str
    detail: list[str] = field(default_factory=list)

    @property
    def icon(self) -> str:
        return {"red": "✗", "yellow": "⚠", "info": "·", "ok": "✓"}[self.level]


@dataclass
class Report:
    root: Path
    machine_id: str = ""
    machine_source: str = ""
    hostname: str = ""
    branch: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    fetched: bool = True
    issues: list[Issue] = field(default_factory=list)
    their_commits: list[dict] = field(default_factory=list)
    my_commits: list[dict] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    stashes: list[str] = field(default_factory=list)
    seams_touched: list[str] = field(default_factory=list)

    def add(self, level, area, text, detail=None):
        self.issues.append(Issue(level, area, text, list(detail or [])))

    @property
    def reds(self):
        return [i for i in self.issues if i.level == "red"]

    @property
    def yellows(self):
        return [i for i in self.issues if i.level == "yellow"]

    def to_dict(self) -> dict:
        return {
            "machine": {"id": self.machine_id, "source": self.machine_source,
                        "hostname": self.hostname},
            "repo": str(self.root),
            "branch": self.branch,
            "upstream": self.upstream,
            "ahead": self.ahead,
            "behind": self.behind,
            "fetched": self.fetched,
            "theirs": self.their_commits,
            "mine": self.my_commits,
            "untracked": self.untracked,
            "modified": self.modified,
            "stashes": self.stashes,
            "seams_touched": self.seams_touched,
            "issues": [{"level": i.level, "area": i.area, "text": i.text,
                        "detail": i.detail} for i in self.issues],
            "summary": {"red": len(self.reds), "yellow": len(self.yellows)},
        }


# ── 采集 ──────────────────────────────────────────────────────────────────

def _log_lines(fmt: str, revrange: str, root: Path) -> list[dict]:
    """把 git log 结果解析成 [{sha, short, author, date, subject}]。"""
    out = git_out(["log", f"--format={fmt}", revrange], cwd=root, check=False)
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 5:
            continue
        rows.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                     "date": parts[3], "subject": parts[4]})
    return rows


def _history_map(root: Path) -> dict:
    """读 machines/history.json → {sha: {machine, confidence}}。缺失返回空表。"""
    p = root / "machines" / "history.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {c.get("sha", ""): c for c in data.get("commits", [])}


def _machine_of(root: Path, sha: str, hist: dict) -> str:
    """提交的机器归属：trailer 优先，history.json 兜底，否则 unknown。"""
    msg = git_out(["log", "-1", "--format=%B", sha], cwd=root, check=False)
    t = trailer_value(msg, "Machine")
    if t:
        return t
    return (hist.get(sha) or {}).get("machine", "unknown")


def collect(root: Path, offline: bool = False) -> Report:
    rep = Report(root=root)
    rep.hostname = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"

    # ── 1. 我是谁 ─────────────────────────────────────────────────────────
    try:
        rep.machine_id, rep.machine_source = machine_identity(root)
        if rep.machine_source == "hostname":
            rep.add("info", "identity",
                    f"机器代号 {rep.machine_id} 由主机名自动认定；"
                    "建议固化：python sync/setup_machine.py --id " + rep.machine_id)
        else:
            rep.add("ok", "identity",
                    f"机器代号 {rep.machine_id}（来源：{rep.machine_source}）")
    except ConfigError as exc:
        rep.add("red", "identity", "本机没有机器代号，无法判断这是哪台机器",
                str(exc).splitlines())

    # ── 2. 与 origin 的差距 ───────────────────────────────────────────────
    rep.branch = git_out(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root, check=False)
    if not rep.branch or rep.branch == "HEAD":
        rep.add("yellow", "origin", "处于分离头指针（detached HEAD）状态，"
                                    "提交容易丢；请先切回分支")
    rep.upstream = git_out(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=root, check=False)

    if not offline:
        fetch = git(["fetch", "--all", "--prune", "--quiet"], cwd=root, check=False,
                    timeout=120)
        rep.fetched = fetch.returncode == EXIT_OK
        if not rep.fetched:
            rep.add("yellow", "origin", "fetch 失败（网络或认证问题），"
                                        "以下落后比较基于本地已知的 origin 状态",
                    [getattr(fetch, "stderr_text", "").strip()])

    if not rep.upstream:
        rep.add("yellow", "origin", f"分支 {rep.branch} 没有上游，无法比较落后；"
                                    "推送前请确认目标分支")
    else:
        counts = git_out(["rev-list", "--left-right", "--count",
                          f"{rep.upstream}...HEAD"], cwd=root, check=False)
        try:
            behind, ahead = (int(x) for x in counts.split())
        except (ValueError, TypeError):
            behind = ahead = 0
        rep.behind, rep.ahead = behind, ahead

        hist = _history_map(root)
        if behind:
            rep.their_commits = _log_lines(GIT_FMT, f"HEAD..{rep.upstream}", root)
            for c in rep.their_commits:
                c["machine"] = _machine_of(root, c["sha"], hist)
            rep.add("red", "origin",
                    f"落后 origin/{rep.branch.rsplit('/', 1)[-1]} {behind} 个提交"
                    "——别的机器已经推过东西，你现在的工作可能建立在旧基础上",
                    [f"{c['short']} [{c['machine']}] {c['date'][:10]} {c['subject']}"
                     for c in rep.their_commits])
        elif ahead:
            rep.add("yellow", "origin", f"领先 origin {ahead} 个提交（还没推送）")
        else:
            rep.add("ok", "origin", f"与 {rep.upstream} 完全同步")

    # ── 4. 我漏推了吗 ─────────────────────────────────────────────────────
    if rep.upstream:
        rep.my_commits = _log_lines(GIT_FMT, f"{rep.upstream}..HEAD", root)
        hist = _history_map(root)
        missing = []
        for c in rep.my_commits:
            msg = git_out(["log", "-1", "--format=%B", c["sha"]], cwd=root, check=False)
            c["machine"] = trailer_value(msg, "Machine") or ""
            c["machine_ref"] = trailer_value(msg, "Machine-Ref") or ""
            if not has_machine_trailer(msg):
                missing.append(c["short"])
        if rep.my_commits:
            rep.add("yellow" if not missing else "red", "unpushed",
                    f"{len(rep.my_commits)} 个提交尚未推送"
                    + (f"，其中 {len(missing)} 个没有 Machine: 标识" if missing else ""),
                    [f"{c['short']} [{c['machine'] or '缺 Machine:'}] {c['subject']}"
                     for c in rep.my_commits])
            if missing:
                rep.add("red", "unpushed",
                        "缺机器标识的提交：" + "、".join(missing),
                        ["补法：git rebase --exec 'python sync/trailer.py "
                         "--amend-head' @{u}  （会改写这些提交的 SHA）",
                         "或认下这个缺口，用 machines/history.json 事后登记"])

    # ── 3. 我漏交了吗 ─────────────────────────────────────────────────────
    porcelain = git_out(["status", "--porcelain"], cwd=root, check=False)
    # 已跟踪文件的改动：从 porcelain 读（能区分 M/A/D/R）
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if code != "??":
            rep.modified.append(path)
            if path.replace("\\", "/") in SEAM_FILES:
                rep.seams_touched.append(path)

    # 未跟踪文件：改用 ls-files --others，把整目录展开成逐个文件。
    # porcelain 对未跟踪目录只给一行 "?? sync/"，会掩盖里面真正的 .py 文件。
    others = git_out(["ls-files", "--others", "--exclude-standard"],
                     cwd=root, check=False)
    for path in others.splitlines():
        path = path.strip().strip('"')
        if not path or is_whitelisted_untracked(path):
            continue
        rep.untracked.append(path)

    pending = [p for p in rep.untracked if looks_like_code(p)]
    if pending:
        rep.add("red", "worktree",
                f"{len(pending)} 个源码类文件未跟踪——它们不会随 git 走，"
                "换机器就丢",
                pending[:20] + (["..."] if len(pending) > 20 else []))
    other = [p for p in rep.untracked if p not in pending]
    if other:
        rep.add("yellow", "worktree", f"{len(other)} 个其它未跟踪文件",
                other[:20] + (["..."] if len(other) > 20 else []))

    if rep.modified:
        rep.add("red", "worktree",
                f"{len(rep.modified)} 个已跟踪文件有未提交改动——"
                "push 不会带上它们，等于白干",
                rep.modified[:20] + (["..."] if len(rep.modified) > 20 else []))
    elif not pending:
        rep.add("ok", "worktree", "工作区干净")

    if rep.seams_touched:
        rep.add("yellow", "worktree",
                "改动了多机共写接缝文件，另一台机器也在写这些文件",
                rep.seams_touched + ["建议：改动前后各跑一次 sync/check.py，"
                                     "并把值写进 *.machine.json 而不是基准文件"])

    # stash
    stash_raw = git_out(["stash", "list", "--format=%gd|%s"], cwd=root, check=False)
    rep.stashes = [ln for ln in stash_raw.splitlines() if ln.strip()]
    if rep.stashes:
        rep.add("red", "worktree",
                f"有 {len(rep.stashes)} 个 stash——最常见的『改动忘了上传』藏身处",
                rep.stashes)

    # ── 5. 风险 ───────────────────────────────────────────────────────────
    if rep.branch in ("master", "main"):
        merges = git_out(["log", "--merges", "-20", "--format=" + GIT_FMT],
                         cwd=root, check=False)
        if merges.strip():
            rows = []
            for line in merges.splitlines():
                p = line.split("\x1f")
                if len(p) >= 5:
                    rows.append(f"{p[1]} {p[3][:10]} {p[4]}")
            rep.add("yellow", "risk",
                    f"{rep.branch} 上有 merge 提交（最近 20 条内 {len(rows)} 个）",
                    rows[:5] + ["铁律：master 只做 pull --rebase + 快进推送；"
                                "并行改动请开 feat/<机器>-<主题> 分支"])

    if os.environ.get(SKIP_ENV):
        rep.add("yellow", "risk",
                f"{SKIP_ENV} 已设置——本次钩子检查会被跳过。"
                "记得回头看这一条，别把它当成常态")

    # 机器专属路径是否被提交进库（红线：仓库是 PUBLIC）
    leak = git_out(["grep", "-n", "-I", "-E",
                    r"C:\\\\Users\\\\smlab|C:/Users/smlab|C:/Windows/System32/YBCO-TA-3.0",
                    "HEAD", "--", "."], cwd=root, check=False)
    leak_lines = [ln for ln in leak.splitlines()
                  if not ln.startswith("HEAD:Noisesweep/README_personal.md")]
    if leak_lines:
        rep.add("red", "risk",
                "库内仍存在机器专属绝对路径（换机器即坏，且仓库是 PUBLIC）",
                leak_lines[:10])

    return rep


# ── 输出 ──────────────────────────────────────────────────────────────────

LEVEL_ORDER = {"red": 0, "yellow": 1, "info": 2, "ok": 3}
AREA_TITLE = {
    "identity": "① 我是谁",
    "origin": "② 与 origin 的差距",
    "worktree": "③ 工作区 / 漏交",
    "unpushed": "④ 未推送提交",
    "risk": "⑤ 风险",
}


def render(rep: Report) -> str:
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append(f" YBCO-TA-3.0 同步体检  |  机器 {rep.machine_id or '?'}"
                 f" @ {rep.hostname}  |  分支 {rep.branch or '?'}")
    lines.append("=" * 68)

    by_area: dict[str, list[Issue]] = {}
    for issue in rep.issues:
        by_area.setdefault(issue.area, []).append(issue)

    for area in ("identity", "origin", "worktree", "unpushed", "risk"):
        issues = sorted(by_area.get(area, []), key=lambda i: LEVEL_ORDER[i.level])
        if not issues:
            continue
        lines.append("")
        lines.append(AREA_TITLE[area])
        for issue in issues:
            lines.append(f"  {issue.icon} {issue.text}")
            for d in issue.detail:
                lines.append(f"      {d}")

    lines.append("")
    lines.append("-" * 68)
    n_red, n_yellow = len(rep.reds), len(rep.yellows)
    if n_red:
        lines.append(f" 结论：🔴 {n_red} 项待处理"
                     + (f"，🟡 {n_yellow} 项提示" if n_yellow else ""))
        lines.append("        逐条处理完再开工/推送；确认要绕过时用 "
                     f"{SKIP_ENV}=1（会记录在案）")
    elif n_yellow:
        lines.append(f" 结论：🟡 {n_yellow} 项提示，无阻断项——可以继续")
    else:
        lines.append(" 结论：✅ 全部干净，可以放心开工 / 推送")
    lines.append("-" * 68)
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="check.py",
        description="开工/收工体检：本机身份、与 origin 的差距、漏交、漏推、风险。")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    ap.add_argument("--strict", action="store_true",
                    help="存在 🔴 时退出码 1（默认永远 0，方便人工阅读）")
    ap.add_argument("--offline", action="store_true",
                    help="不联网：跳过 fetch 与落后比较")
    args = ap.parse_args(argv)

    root = repo_root()
    try:
        rep = collect(root, offline=args.offline)
    except ConfigError as exc:
        print(f"[X] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render(rep))

    if args.strict and rep.reds:
        return EXIT_BLOCK
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
