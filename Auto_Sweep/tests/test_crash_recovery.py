"""Tests for crash_recovery.py 崩溃诊断与文件恢复."""

import json
import os
import tempfile
import pytest
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import crash_recovery as cr


@pytest.fixture
def temp_workspace():
    """创建临时工作空间，包含 mock 文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_debug_log(temp_workspace):
    """创建 mock debug.log，模拟 Bun SIGKILL 崩溃。"""
    log_path = temp_workspace / "debug.log"
    log_path.write_text("\n".join([
        "[2026-06-13T23:14:50] queryDepth=27, agent=Task 8",
        "[2026-06-13T23:14:52] Running pytest tests/test_config.py",
        "[2026-06-13T23:14:55] ShellError: pytest process killed (exit 137)",
        "[2026-06-13T23:14:55] ERROR: is_running_with_bun: true",
        "[2026-06-13T23:14:55] PANIC: Bun runtime error in error recovery path",
        "[2026-06-13T23:14:56] SIGKILL received, shutting down",
        "[2026-06-13T23:14:56] Some normal log line after crash",
    ]))
    return log_path


@pytest.fixture
def sample_transcript(temp_workspace):
    """创建 mock transcript JSONL，包含 Write 和 Edit 操作。"""
    transcript_path = temp_workspace / "transcript.jsonl"
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-06-13T23:10:00Z",
            "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {
                    "file_path": "test_file.py",
                    "content": "def hello():\n    return 'world'\n"
                }}
            ]}
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-06-13T23:12:00Z",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {
                    "file_path": "existing.py",
                    "old_string": "x = 1",
                    "new_string": "x = 2"
                }}
            ]}
        }),
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "do something"}]}
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-06-13T23:14:00Z",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {
                    "file_path": "existing.py",
                    "old_string": "y = 3",
                    "new_string": "y = 4"
                }}
            ]}
        }),
        "",
        "not valid json at all",
    ]
    transcript_path.write_text("\n".join(lines))
    return transcript_path


class TestScanDebugLog:
    def test_detects_bun_sigkill(self, sample_debug_log):
        """检测 Bun SIGKILL 崩溃类型。"""
        result = cr.scan_debug_log(str(sample_debug_log))
        assert result["crash_type"] == "bun_sigkill"
        assert result["trigger"] == "subprocess_killed"

    def test_extracts_query_depth(self, sample_debug_log):
        """提取 queryDepth 值。"""
        result = cr.scan_debug_log(str(sample_debug_log))
        assert result["query_depth"] == 27

    def test_extracts_key_lines(self, sample_debug_log):
        """提取关键日志行。"""
        result = cr.scan_debug_log(str(sample_debug_log))
        assert len(result["key_lines"]) > 0
        assert any("SIGKILL" in line for line in result["key_lines"])

    def test_handles_missing_log(self, temp_workspace):
        """日志文件不存在时返回 unknown。"""
        result = cr.scan_debug_log(str(temp_workspace / "nonexistent.log"))
        assert result["crash_type"] == "unknown"
        assert result["trigger"] == "no_log_found"

    def test_detects_oom_kill(self, temp_workspace):
        """检测 OOM 崩溃类型。"""
        log = temp_workspace / "oom.log"
        log.write_text("[error] out of memory: process killed\nMemoryError\n")
        result = cr.scan_debug_log(str(log))
        assert result["crash_type"] == "oom_kill"

    def test_first_match_wins(self, temp_workspace):
        """SIGKILL 在 OOM 前匹配 → 第一个匹配获胜。"""
        log = temp_workspace / "mixed.log"
        log.write_text("SIGKILL received\nout of memory\n")
        result = cr.scan_debug_log(str(log))
        assert result["crash_type"] == "bun_sigkill"


class TestParseTranscript:
    def test_extracts_write_operations(self, sample_transcript):
        """提取 Write 操作并归类为 write 恢复类型。"""
        result = cr.parse_transcript(str(sample_transcript.parent))
        assert "test_file.py" in result["lost_files"]
        assert result["lost_files"]["test_file.py"]["recovery_type"] == "write"
        assert "hello" in result["lost_files"]["test_file.py"]["content"]

    def test_extracts_edit_operations(self, sample_transcript):
        """提取 Edit 操作并归类为 edit_chain 恢复类型。"""
        result = cr.parse_transcript(str(sample_transcript.parent))
        assert "existing.py" in result["lost_files"]
        assert result["lost_files"]["existing.py"]["recovery_type"] == "edit_chain"
        assert len(result["lost_files"]["existing.py"]["edits"]) == 2

    def test_handles_malformed_lines(self, sample_transcript):
        """格式错误的行不应导致崩溃。"""
        result = cr.parse_transcript(str(sample_transcript.parent))
        assert result is not None
        assert "operation_chain" in result

    def test_handles_user_messages(self, sample_transcript):
        """用户消息应被过滤掉。"""
        result = cr.parse_transcript(str(sample_transcript.parent))
        ops = [op for op in result["operation_chain"] if op["tool"] == "user"]
        assert len(ops) == 0

    def test_operation_chain_truncated_to_10(self, temp_workspace):
        """操作链最多保留 10 条。"""
        t = temp_workspace / "many.jsonl"
        lines = []
        for i in range(20):
            lines.append(json.dumps({
                "type": "assistant",
                "timestamp": f"2026-06-13T23:{i:02d}:00Z",
                "message": {"content": [
                    {"type": "tool_use", "name": "Write", "input": {
                        "file_path": f"file{i}.py",
                        "content": f"# file {i}"
                    }}
                ]}
            }))
        t.write_text("\n".join(lines))
        result = cr.parse_transcript(str(temp_workspace))
        assert len(result["operation_chain"]) <= 10

    def test_empty_dir(self, temp_workspace):
        """空目录返回空结果。"""
        result = cr.parse_transcript(str(temp_workspace))
        assert result["operation_chain"] == []
        assert result["lost_files"] == {}

    def test_filters_by_last_commit_time(self, temp_workspace):
        """last_commit_time 之后的操作被排除。"""
        t = temp_workspace / "time.jsonl"
        t.write_text(json.dumps({
            "type": "assistant",
            "timestamp": "2026-06-13T23:10:00Z",
            "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {
                    "file_path": "old_file.py",
                    "content": "old"
                }}
            ]}
        }) + "\n")
        # last_commit_time 在未来 → 所有操作被排除
        future_ts = 9999999999.0
        result = cr.parse_transcript(str(temp_workspace), last_commit_time=future_ts)
        assert result["lost_files"] == {}
        assert result["operation_chain"] == []


class TestRecoverFileWrite:
    def test_writes_new_file(self, temp_workspace):
        """写入新文件。"""
        path = str(temp_workspace / "new.py")
        result = cr.recover_file_write(path, "content here")
        assert result["status"] == "recovered"
        assert Path(path).read_text() == "content here"

    def test_idempotent(self, temp_workspace):
        """已有相同内容 → 跳过。"""
        path = str(temp_workspace / "exists.py")
        Path(path).write_text("same content")
        result = cr.recover_file_write(path, "same content")
        assert result["status"] == "skipped"

    def test_dry_run_does_not_write(self, temp_workspace):
        """dry_run 模式不写盘。"""
        path = str(temp_workspace / "dry.py")
        result = cr.recover_file_write(path, "test", dry_run=True)
        assert result["status"] == "recovered"
        assert not Path(path).exists()

    def test_overwrites_different_content(self, temp_workspace):
        """不同内容 → 覆盖写入。"""
        path = str(temp_workspace / "overwrite.py")
        Path(path).write_text("old content")
        result = cr.recover_file_write(path, "new content")
        assert result["status"] == "recovered"
        assert Path(path).read_text() == "new content"


class TestRecoverFileEdits:
    def test_applies_single_edit(self, temp_workspace):
        """应用单个编辑。"""
        path = str(temp_workspace / "edit.py")
        Path(path).write_text("old value")
        result = cr.recover_file_edits(path, [{"old": "old value", "new": "new value"}])
        assert result["status"] == "recovered"
        assert Path(path).read_text() == "new value"

    def test_applies_edit_chain_in_order(self, temp_workspace):
        """按顺序应用编辑链。"""
        path = str(temp_workspace / "chain.py")
        Path(path).write_text("aaa bbb ccc")
        edits = [
            {"old": "aaa", "new": "111"},
            {"old": "bbb", "new": "222"},
        ]
        result = cr.recover_file_edits(path, edits)
        assert result["status"] == "recovered"
        assert Path(path).read_text() == "111 222 ccc"

    def test_conflict_saves_conflict_file(self, temp_workspace):
        """old_string 不匹配 → 保存冲突文件。"""
        path = str(temp_workspace / "conflict.py")
        Path(path).write_text("current content")
        result = cr.recover_file_edits(path, [{"old": "not found", "new": "replacement"}])
        assert result["status"] == "conflict"
        assert Path(path + ".recover_conflict").exists()

    def test_idempotent_when_already_applied(self, temp_workspace):
        """编辑已生效 → 跳过。"""
        path = str(temp_workspace / "already.py")
        Path(path).write_text("already applied")
        result = cr.recover_file_edits(path, [{"old": "already", "new": "already"}])
        assert result["status"] == "skipped"

    def test_file_not_exist(self, temp_workspace):
        """文件不存在 → 冲突。"""
        result = cr.recover_file_edits(str(temp_workspace / "nope.py"), [{"old": "a", "new": "b"}])
        assert result["status"] == "conflict"

    def test_partial_edit_chain(self, temp_workspace):
        """部分编辑成功，部分冲突 → 状态应为 partial。"""
        path = str(temp_workspace / "partial.py")
        Path(path).write_text("hello world")
        edits = [
            {"old": "hello", "new": "hi"},
            {"old": "not found", "new": "nope"},
        ]
        result = cr.recover_file_edits(path, edits)
        assert result["status"] == "partial"
        assert Path(path).read_text() == "hi world"

    def test_cancelling_edits_with_conflict(self, temp_workspace):
        """编辑相互抵消但有冲突 → 状态应为 partial。"""
        path = str(temp_workspace / "cancel.py")
        Path(path).write_text("unchanged")
        edits = [
            {"old": "unchanged", "new": "temp"},
            {"old": "temp", "new": "unchanged"},  # 抵消
            {"old": "missing", "new": "x"},        # 冲突
        ]
        result = cr.recover_file_edits(path, edits)
        assert result["status"] == "partial"

    def test_dry_run_no_write(self, temp_workspace):
        """dry_run 模式不写盘。"""
        path = str(temp_workspace / "dry_edit.py")
        original = "original text"
        Path(path).write_text(original)
        result = cr.recover_file_edits(path, [{"old": "original", "new": "changed"}], dry_run=True)
        assert result["status"] == "recovered"
        assert Path(path).read_text() == original


class TestMatchHistorical:
    def test_no_match_when_dir_empty(self, temp_workspace):
        """空记忆目录无匹配。"""
        mem_dir = str(temp_workspace / "memory")
        os.makedirs(mem_dir)
        result = cr.match_historical(mem_dir, {"crash_type": "bun_sigkill", "trigger": "x"})
        assert result["similarity"] == 0
        assert result["id"] is None

    def test_no_match_when_none_dir(self):
        """None 目录无匹配。"""
        result = cr.match_historical(None, {"crash_type": "bun_sigkill"})
        assert result["similarity"] == 0

    def test_exact_match(self, temp_workspace):
        """type 和 trigger 完全匹配 → 相似度 1.0。"""
        mem_dir = temp_workspace / "memory"
        mem_dir.mkdir()
        crash_file = mem_dir / "crash-20260613-001400.md"
        crash_file.write_text("""---
name: crash-test
description: test crash
metadata:
  type: project
type: bun_sigkill
trigger: subprocess_killed
---
# Details
""")
        result = cr.match_historical(str(mem_dir), {"crash_type": "bun_sigkill", "trigger": "subprocess_killed"})
        assert result["similarity"] == 1.0

    def test_partial_match_type_only(self, temp_workspace):
        """仅 type 匹配 → 相似度 0.5。"""
        mem_dir = temp_workspace / "memory"
        mem_dir.mkdir()
        crash_file = mem_dir / "crash-old.md"
        crash_file.write_text("""---
type: bun_sigkill
trigger: deep_agent_chain
---
""")
        result = cr.match_historical(str(mem_dir), {"crash_type": "bun_sigkill", "trigger": "subprocess_killed"})
        assert result["similarity"] == 0.5


class TestDiagnose:
    def test_returns_expected_structure(self, temp_workspace, sample_debug_log, sample_transcript):
        """diagnose() 返回所有顶层键。"""
        result = cr.diagnose(
            debug_log_path=str(sample_debug_log),
            transcript_dir=str(temp_workspace),
            project_root=str(temp_workspace),
        )
        for key in ("crash", "log_lines", "operation_chain", "lost_files", "git", "historical_match", "recommendations"):
            assert key in result, f"缺少键: {key}"
        assert result["crash"]["type"] == "bun_sigkill"

    def test_unknown_crash_gives_low_confidence(self, temp_workspace):
        """无 debug.log → crash_type=unknown → confidence=low。"""
        result = cr.diagnose(
            debug_log_path=str(temp_workspace / "nonexistent.log"),
            transcript_dir=str(temp_workspace),
            project_root=str(temp_workspace),
        )
        assert result["crash"]["confidence"] == "low"
        assert result["crash"]["type"] == "unknown"


class TestRecover:
    def test_returns_expected_structure(self, temp_workspace):
        """recover() 返回预期结构。"""
        result = cr.recover(str(temp_workspace), str(temp_workspace), [])
        assert "recovered" in result
        assert "conflicts" in result
        assert "skipped" in result

    def test_file_not_in_transcript(self, temp_workspace):
        """文件不在 transcript 中 → 跳过。"""
        result = cr.recover(str(temp_workspace), str(temp_workspace), ["nonexistent.py"])
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "not found in transcript"

    def test_path_normalization(self, temp_workspace, sample_transcript):
        """反斜杠路径应匹配正斜杠 transcript 键。"""
        result = cr.recover(
            str(temp_workspace),  # transcript_dir
            str(temp_workspace),  # project_root
            ["test_file.py"],
            dry_run=True,
        )
        assert len(result["recovered"]) == 1
