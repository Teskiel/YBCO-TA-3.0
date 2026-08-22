# -*- coding: utf-8 -*-
"""Watchdog 模块单元测试。

测试 read_heartbeat() 和 is_timed_out() 两个纯函数。
不测试 run() 和 _kill_and_restart()（需要真实进程）。
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# read_heartbeat 测试
# ---------------------------------------------------------------------------

def test_given_valid_heartbeat_file_when_read_then_returns_status():
    """给定合法的 heartbeat.json，读取后返回正确的 HeartbeatStatus。"""
    from watchdog import read_heartbeat

    tmpdir = tempfile.mkdtemp()
    try:
        hb_path = os.path.join(tmpdir, "heartbeat.json")
        now = time.time()
        payload = {
            "pid": 12345,
            "step_ts": now,
            "step": "sweep",
            "temp_idx": 5,
            "vna_idx": 2,
            "power_idx": 3,
            "seq": 42,
            "stop": False,
        }
        with open(hb_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        result = read_heartbeat(tmpdir)

        assert result is not None
        assert result.pid == 12345
        assert result.step_ts == now
        assert result.step == "sweep"
        assert result.temp_idx == 5
        assert result.vna_idx == 2
        assert result.power_idx == 3
        assert result.seq == 42
        assert result.stop is False
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_given_stop_marker_when_read_then_stop_is_true():
    """给定带 stop: true 的 heartbeat.json，读取后 stop 字段为 True。"""
    from watchdog import read_heartbeat

    tmpdir = tempfile.mkdtemp()
    try:
        hb_path = os.path.join(tmpdir, "heartbeat.json")
        payload = {
            "pid": 12345,
            "step_ts": time.time(),
            "step": "done",
            "temp_idx": 0,
            "vna_idx": 0,
            "power_idx": 0,
            "seq": 99,
            "stop": True,
        }
        with open(hb_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        result = read_heartbeat(tmpdir)

        assert result is not None
        assert result.stop is True
        assert result.seq == 99
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_given_missing_file_when_read_then_returns_none():
    """给定不存在的目录（无 heartbeat.json），read_heartbeat 返回 None。"""
    from watchdog import read_heartbeat

    tmpdir = tempfile.mkdtemp()
    import shutil
    shutil.rmtree(tmpdir)  # 删除目录，确保文件不存在

    result = read_heartbeat(tmpdir)

    assert result is None


def test_given_corrupt_json_when_read_then_returns_none():
    """给定内容损坏的 heartbeat.json，read_heartbeat 返回 None。"""
    from watchdog import read_heartbeat

    tmpdir = tempfile.mkdtemp()
    try:
        hb_path = os.path.join(tmpdir, "heartbeat.json")
        with open(hb_path, "w", encoding="utf-8") as f:
            f.write("this is not valid json {")

        result = read_heartbeat(tmpdir)

        assert result is None
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# is_timed_out 测试
# ---------------------------------------------------------------------------

def test_given_recent_heartbeat_when_checking_then_not_timed_out():
    """step_ts 距今 30s，不应判定为超时。"""
    from watchdog import HeartbeatStatus, is_timed_out

    now = time.time()
    status = HeartbeatStatus(
        pid=12345,
        step_ts=now - 30,  # 30 秒前的心跳
        step="sweep",
        temp_idx=1,
        vna_idx=0,
        power_idx=0,
        seq=10,
        stop=False,
    )

    assert is_timed_out(status, timeout_s=300, now=now) is False


def test_given_stale_heartbeat_when_checking_then_timed_out():
    """step_ts 距今 400s，超出超时阈值 300s，应判定为超时。"""
    from watchdog import HeartbeatStatus, is_timed_out

    now = time.time()
    status = HeartbeatStatus(
        pid=12345,
        step_ts=now - 400,  # 400 秒前的心跳
        step="sweep",
        temp_idx=1,
        vna_idx=0,
        power_idx=0,
        seq=10,
        stop=False,
    )

    assert is_timed_out(status, timeout_s=300, now=now) is True


def test_given_stopped_heartbeat_when_checking_then_not_timed_out():
    """即使 step_ts 已过期，若 stop=True 也不应判定为超时（正常退出）。"""
    from watchdog import HeartbeatStatus, is_timed_out

    now = time.time()
    status = HeartbeatStatus(
        pid=12345,
        step_ts=now - 400,  # 400 秒前的心跳
        step="done",
        temp_idx=0,
        vna_idx=0,
        power_idx=0,
        seq=99,
        stop=True,
    )

    assert is_timed_out(status, timeout_s=300, now=now) is False
