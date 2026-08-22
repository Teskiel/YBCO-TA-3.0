#!/usr/bin/env python3
"""Crash recovery helper for Claude Code /recover command.

Phase 1: 崩溃诊断 — 分析 debug.log + transcript JSONL → 诊断 JSON
Phase 3: 文件恢复 — 从 transcript 重建丢失的代码

Usage:
  python scripts/crash_recovery.py diagnose --debug-log PATH --transcript-dir PATH --project-root PATH [--memory-dir PATH] [--output json]
  python scripts/crash_recovery.py recover --transcript-dir PATH --project-root PATH --files f1.py,f2.py [--dry-run] [--output json]
"""

import argparse
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# 崩溃特征码匹配
# ---------------------------------------------------------------------------
CRASH_PATTERNS = [
    # (regex, crash_type, trigger)
    (r"exit code 137|SIGKILL", "bun_sigkill", "subprocess_killed"),
    (r"ShellError", "shell_error", "shell_command_failed"),
    (r"Bun.*(?:panic|crash|SIGABRT)", "bun_panic", "bun_runtime_bug"),
    (r"out of memory|OOM|MemoryError", "oom_kill", "system_oom"),
    (r"queryDepth[=:]\s*(\d+)", None, None),  # extract depth for context
]


# ---------------------------------------------------------------------------
# Phase 1: 日志扫描器
# ---------------------------------------------------------------------------

def scan_debug_log(log_path: str, tail_lines: int = 200) -> dict:
    """扫描 debug.log 最後 N 行，返回 crash_type, trigger, key_lines, query_depth."""
    if not os.path.exists(log_path):
        return {"crash_type": "unknown", "trigger": "no_log_found",
                "key_lines": [], "query_depth": 0}

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    recent = lines[-tail_lines:]

    crash_type = "unknown"
    trigger = "unknown"
    query_depth = 0

    for pattern, ctype, ctrigger in CRASH_PATTERNS:
        for line in recent:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                if ctype and crash_type == "unknown":
                    crash_type = ctype
                    trigger = ctrigger
                if pattern.startswith(r"queryDepth"):
                    query_depth = int(m.group(1))

    # Extract key lines (lines matching crash patterns, ±1 context)
    key_pattern = re.compile(
        r"SIGKILL|exit 137|ShellError|panic|crash|OOM|out of memory|queryDepth|ERROR|FATAL",
        re.IGNORECASE
    )
    key_lines = []
    key_lines_set = set()
    for i, line in enumerate(recent):
        if key_pattern.search(line):
            # Include 1 line of context before/after
            start = max(0, i - 1)
            end = min(len(recent), i + 2)
            for j in range(start, end):
                stripped = recent[j].rstrip()
                if stripped not in key_lines_set:
                    key_lines_set.add(stripped)
                    key_lines.append(stripped)
            if len(key_lines) >= 30:
                break

    return {
        "crash_type": crash_type,
        "trigger": trigger,
        "key_lines": key_lines[:30],
        "query_depth": query_depth,
    }


# ---------------------------------------------------------------------------
# Phase 1: transcript 解析器
# ---------------------------------------------------------------------------

def parse_transcript(transcript_dir: str, last_commit_time: Optional[float] = None) -> dict:
    """解析最新 transcript JSONL，提取崩溃前未落盘的操作。

    Returns:
        {"operation_chain": [...], "lost_files": {...}}
    """
    # Find latest JSONL files
    jsonl_files = sorted(
        Path(transcript_dir).glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )[:2]

    operations = []
    file_ops = {}  # file_path -> list of operations

    for jf in jsonl_files:
        try:
            with open(jf, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if obj.get("type") != "assistant":
                        continue

                    ts = obj.get("timestamp", "")
                    for block in obj.get("message", {}).get("content", []):
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        if name not in ("Write", "Edit"):
                            continue
                        inp = block.get("input", {})
                        filepath = inp.get("file_path", "")
                        op = {
                            "time": ts,
                            "tool": name,
                            "file": filepath,
                        }
                        if name == "Write":
                            op["content"] = inp.get("content", "")
                        elif name == "Edit":
                            op["old_string"] = inp.get("old_string", "")
                            op["new_string"] = inp.get("new_string", "")

                        operations.append(op)
                        if filepath not in file_ops:
                            file_ops[filepath] = []
                        file_ops[filepath].append(op)
        except Exception:
            continue

    # Determine which operations happened after last commit
    if last_commit_time:
        operations = [op for op in operations
                      if _parse_timestamp(op["time"]) > last_commit_time]

        # Also filter file_ops to only include post-commit operations
        filtered_file_ops = {}
        for op in operations:
            fpath = op["file"]
            if fpath not in filtered_file_ops:
                filtered_file_ops[fpath] = []
            filtered_file_ops[fpath].append(op)
        file_ops = filtered_file_ops

    # Classify each file: has Write (full recovery) vs Edit-only (patch recovery)
    lost_files = {}
    for fpath, ops in file_ops.items():
        has_write = any(op["tool"] == "Write" for op in ops)

        if has_write:
            last_write = next((op for op in reversed(ops) if op["tool"] == "Write"), None)
            lost_files[fpath] = {
                "ops": ops,
                "recovery_type": "write",
                "content": last_write["content"] if last_write else "",
            }
        else:
            lost_files[fpath] = {
                "ops": ops,
                "recovery_type": "edit_chain",
                "edits": [{"old": op["old_string"], "new": op["new_string"]} for op in ops],
            }

    # Keep only last 10 operations for the chain display
    operation_chain = operations[-10:]

    return {"operation_chain": operation_chain, "lost_files": lost_files}


def _parse_timestamp(ts: str) -> float:
    """Parse ISO timestamp to epoch. Returns 0 on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _confidence(crash_type: str, trigger: str) -> str:
    """Determine confidence level for crash diagnosis."""
    if crash_type == "unknown":
        return "low"
    if trigger == "unknown":
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# Phase 1: 历史崩溃匹配
# ---------------------------------------------------------------------------

def match_historical(memory_dir: str, current_signature: dict) -> dict:
    """对比历史崩溃记录，返回最佳匹配及相似度。

    Returns: {"id": "...", "similarity": 0.89} or {"id": None, "similarity": 0}
    """
    if not memory_dir or not os.path.isdir(memory_dir):
        return {"id": None, "similarity": 0}

    crash_files = sorted(Path(memory_dir).glob("crash-*.md"))
    if not crash_files:
        return {"id": None, "similarity": 0}

    best_match = None
    best_score = 0

    for cf in crash_files:
        try:
            content = cf.read_text(encoding="utf-8", errors="replace")
            sig_type = _extract_frontmatter(content, "type", "")
            sig_trigger = _extract_frontmatter(content, "trigger", "")

            score = 0
            if sig_type == current_signature.get("crash_type"):
                score += 0.5
            if sig_trigger == current_signature.get("trigger"):
                score += 0.5

            if score > best_score:
                best_score = score
                best_match = cf.stem
        except Exception:
            continue

    return {"id": best_match, "similarity": round(best_score, 2)}


def _extract_frontmatter(content: str, key: str, default: str) -> str:
    """Extract a value from YAML-style frontmatter."""
    m = re.search(rf"^{key}:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else default


# ---------------------------------------------------------------------------
# Phase 1: 主诊断流程
# ---------------------------------------------------------------------------

def diagnose(debug_log_path: str, transcript_dir: str, project_root: str,
             memory_dir: Optional[str] = None) -> dict:
    """Phase 1: 完整诊断流程。"""
    import subprocess

    # 1. Scan debug log
    log_result = scan_debug_log(debug_log_path)

    # 2. Get last commit time via git
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "log", "-1", "--format=%ct", "HEAD"],
            capture_output=True, text=True
        )
        last_commit_time = float(result.stdout.strip()) if result.stdout.strip() else None
        last_commit_hash = subprocess.run(
            ["git", "-C", project_root, "log", "-1", "--format=%h", "HEAD"],
            capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        last_commit_time = None
        last_commit_hash = "unknown"

    # 3. Parse transcript
    transcript_result = parse_transcript(transcript_dir, last_commit_time)

    # Add status field to operation chain items
    for op in transcript_result["operation_chain"]:
        if last_commit_time and _parse_timestamp(op["time"]) <= last_commit_time:
            op["status"] = "committed"
        elif os.path.exists(os.path.join(project_root, op["file"])):
            op["status"] = "on_disk"
        else:
            op["status"] = "transcript_only"

    # 4. Check git working tree
    try:
        diff_result = subprocess.run(
            ["git", "-C", project_root, "diff", "HEAD", "--stat"],
            capture_output=True, text=True
        )
        git_diff = diff_result.stdout.strip() or "(clean)"
    except Exception:
        git_diff = "(git unavailable)"

    # 5. Match historical crashes
    current_sig = {
        "crash_type": log_result["crash_type"],
        "trigger": log_result["trigger"],
        "query_depth": log_result["query_depth"],
    }
    historical = match_historical(memory_dir, current_sig)

    # 6. Generate recommendations
    recommendations = _generate_recommendations(
        log_result["crash_type"], log_result["trigger"],
        log_result["query_depth"], transcript_result["lost_files"]
    )

    return {
        "crash": {
            "type": log_result["crash_type"],
            "trigger": log_result["trigger"],
            "confidence": _confidence(log_result["crash_type"], log_result["trigger"]),
            "query_depth": log_result["query_depth"],
        },
        "log_lines": log_result["key_lines"],
        "operation_chain": transcript_result["operation_chain"],
        "lost_files": transcript_result["lost_files"],
        "git": {
            "last_commit": last_commit_hash,
            "diff_stat": git_diff,
        },
        "historical_match": historical,
        "recommendations": recommendations,
    }


def _generate_recommendations(crash_type: str, trigger: str,
                               query_depth: int, lost_files: dict) -> list:
    """Generate human-readable recommendations based on crash analysis."""
    recs = []

    if crash_type in ("bun_sigkill", "bun_panic"):
        recs.append("升级 Claude Code: winget upgrade Anthropic.ClaudeCode")
    if query_depth > 20:
        recs.append(f"Agent 嵌套深度达 {query_depth}，建议分批执行任务，避免单 session 过深调用链")
    if trigger == "subprocess_killed":
        recs.append("pytest 子进程被 SIGKILL，建议测试分批运行（每批 ≤30 个），使用 --maxfail=5")
    if lost_files:
        file_list = ", ".join(lost_files.keys())
        recs.append(f"发现 {len(lost_files)} 个未落盘文件: {file_list}，确认后可恢复")
    if crash_type == "unknown":
        recs.append("无法自动诊断崩溃类型，建议人工查看 ~/.claude/debug.log 全文")

    recs.append("恢复后立即 git commit 保存更改")
    return recs


# ---------------------------------------------------------------------------
# Phase 3: 文件恢复 (stub — Task 2 实现)
# ---------------------------------------------------------------------------

def recover_file_write(filepath: str, content: str, dry_run: bool = False) -> dict:
    """Restore a file from a full Write operation's content.

    Returns: {"status": "recovered"|"skipped", "reason": "..."}
    """
    target = Path(filepath)

    # Check if file already matches (idempotent)
    if target.exists() and target.read_text(encoding="utf-8", errors="replace") == content:
        return {"status": "skipped", "reason": "file already matches recovered content"}

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "recovered", "reason": "written from transcript"}
    else:
        return {"status": "recovered", "reason": "[dry-run] would write"}


def recover_file_edits(filepath: str, edits: list, dry_run: bool = False) -> dict:
    """Apply a chain of Edit operations to the current file content.

    Each edit is {"old": str, "new": str}. Applied in order.
    If old_string not found, skip and record conflict.
    Uses replace() with count=1 to match Claude Code Edit semantics.

    Returns: {"status": "recovered"|"partial"|"conflict", "reason": "...",
              "conflict_file": "..."|null}
    """
    target = Path(filepath)
    if not target.exists():
        return {"status": "conflict", "reason": "file does not exist on disk, cannot apply edits",
                "conflict_file": None}

    content = target.read_text(encoding="utf-8", errors="replace")
    original = content
    applied = 0
    conflicts = []

    for i, edit in enumerate(edits):
        old = edit["old"]
        new = edit["new"]
        if old in content:
            content = content.replace(old, new, 1)
            applied += 1
        else:
            conflicts.append({"index": i, "old_preview": old[:80], "new_preview": new[:80]})

    if applied == 0:
        # Nothing applied, save conflict file
        conflict_path = str(target) + ".recover_conflict"
        conflict_content = "\n---\n".join(
            f"# Edit {c['index']}: old_string not found in current file\n"
            f"# OLD:\n{c['old_preview']}\n"
            f"# NEW:\n{c['new_preview']}"
            for c in conflicts
        )
        if not dry_run:
            Path(conflict_path).write_text(conflict_content, encoding="utf-8")
        return {"status": "conflict", "reason": f"{len(conflicts)} edits could not be applied",
                "conflict_file": conflict_path}

    if content == original:
        if conflicts:
            # Edits cancelled each other out but some had conflicts
            return {"status": "partial", "reason": f"content unchanged but {len(conflicts)} edits had conflicts",
                    "conflict_file": str(target) + ".recover_conflict" if not dry_run else None}
        return {"status": "skipped", "reason": "all edits already applied (idempotent)"}

    if not dry_run:
        target.write_text(content, encoding="utf-8")
        status = "recovered" if applied == len(edits) else "partial"
        reason = f"applied {applied}/{len(edits)} edits"
        if conflicts:
            reason += f", {len(conflicts)} conflicts saved to {target}.recover_conflict"
        return {"status": status, "reason": reason,
                "conflict_file": str(target) + ".recover_conflict" if conflicts else None}
    else:
        return {"status": "recovered", "reason": f"[dry-run] would apply {applied}/{len(edits)} edits"}


def recover(transcript_dir: str, project_root: str,
            files: list, dry_run: bool = False) -> dict:
    """Phase 3: 从 transcript 恢复指定文件。

    Args:
        transcript_dir: 含 .jsonl 文件的目录
        project_root: 项目根目录（文件写入的相对基准）
        files: 要恢复的文件路径列表
        dry_run: 仅预览，不实际写入

    Returns: {"recovered": [...], "conflicts": [...], "skipped": [...]}
    """
    # Re-parse transcript to get latest file contents
    transcript_result = parse_transcript(transcript_dir)
    lost_files = transcript_result["lost_files"]

    recovered = []
    conflicts = []
    skipped = []

    for fpath in files:
        abs_path = os.path.join(project_root, fpath) if not os.path.isabs(fpath) else fpath

        # Normalize path for lookup (transcript uses forward-slash relative paths)
        lookup_key = fpath.replace("\\", "/")
        proj = project_root.replace("\\", "/").rstrip("/")
        if lookup_key.startswith(proj + "/"):
            lookup_key = lookup_key[len(proj) + 1:]
        if lookup_key.startswith("./"):
            lookup_key = lookup_key[2:]

        if lookup_key not in lost_files:
            skipped.append({"file": fpath, "reason": "not found in transcript"})
            continue

        file_info = lost_files[lookup_key]

        if file_info["recovery_type"] == "write":
            result = recover_file_write(abs_path, file_info["content"], dry_run)
        else:
            result = recover_file_edits(abs_path, file_info["edits"], dry_run)

        result["file"] = fpath
        if result["status"] == "recovered":
            recovered.append(result)
        elif result["status"] == "conflict":
            conflicts.append(result)
        else:
            skipped.append(result)

    return {"recovered": recovered, "conflicts": conflicts, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Crash recovery helper")
    sub = parser.add_subparsers(dest="command", required=True)

    diag = sub.add_parser("diagnose")
    diag.add_argument("--debug-log", required=True)
    diag.add_argument("--transcript-dir", required=True)
    diag.add_argument("--project-root", required=True)
    diag.add_argument("--memory-dir")
    diag.add_argument("--output", default="json")

    rec = sub.add_parser("recover")
    rec.add_argument("--transcript-dir", required=True)
    rec.add_argument("--project-root", required=True)
    rec.add_argument("--files", required=True)
    rec.add_argument("--dry-run", action="store_true")
    rec.add_argument("--output", default="json")

    args = parser.parse_args()

    if args.command == "diagnose":
        result = diagnose(
            debug_log_path=args.debug_log,
            transcript_dir=args.transcript_dir,
            project_root=args.project_root,
            memory_dir=args.memory_dir,
        )
    elif args.command == "recover":
        result = recover(
            transcript_dir=args.transcript_dir,
            project_root=args.project_root,
            files=args.files.split(","),
            dry_run=args.dry_run,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
