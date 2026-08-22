# -*- coding: utf-8 -*-
"""
BDD tests for heartbeat.py

Tests cover Heartbeat start/stop/step lifecycle, file format,
disk-full resilience, and post-stop write suppression.
All tests are pure — no hardware or VISA dependencies.
"""

import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest

from heartbeat import Heartbeat


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for heartbeat output, clean up after."""
    d = tempfile.mkdtemp()
    yield d
    # Clean up
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    os.rmdir(d)


@pytest.fixture
def heartbeat_file(temp_dir):
    return os.path.join(temp_dir, "heartbeat.json")


# ======================================================================
# Test 1: Start writes file
# ======================================================================

def test_given_new_heartbeat_when_started_then_writes_file(temp_dir):
    """After start(), the daemon thread should write heartbeat.json to output_dir."""
    hb = Heartbeat(output_dir=temp_dir, interval_s=0.1)
    hb.start()

    # Give the thread time to write at least once
    time.sleep(0.25)

    hb.stop()

    heartbeat_path = os.path.join(temp_dir, "heartbeat.json")
    assert os.path.exists(heartbeat_path), (
        "heartbeat.json should exist after start() + sleep"
    )

    with open(heartbeat_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Check required keys
    for key in ("pid", "step_ts", "step", "temp_idx", "vna_idx",
                "power_idx", "seq", "stop"):
        assert key in data, f"Missing key '{key}' in heartbeat.json"


# ======================================================================
# Test 2: Step increments seq
# ======================================================================

def test_given_heartbeat_when_step_called_then_seq_increments(temp_dir):
    """Each step() call should increment seq by 1."""
    hb = Heartbeat(output_dir=temp_dir, interval_s=0.1)
    hb.start()
    time.sleep(0.15)  # ensure initial write happened

    # Read initial seq
    with open(os.path.join(temp_dir, "heartbeat.json"), "r") as f:
        initial = json.load(f)

    hb.step("ramp", temp_idx=1, vna_idx=0, power_idx=2)
    time.sleep(0.2)  # give thread time to write updated state

    with open(os.path.join(temp_dir, "heartbeat.json"), "r") as f:
        after_step = json.load(f)

    hb.stop()

    assert after_step["seq"] == initial["seq"] + 1, (
        f"seq should increment: {initial['seq']} -> {after_step['seq']}"
    )


# ======================================================================
# Test 3: Stop writes stop=true
# ======================================================================

def test_given_heartbeat_when_stopped_then_file_contains_stop_true(temp_dir):
    """After stop(), the final heartbeat.json must contain stop: true."""
    hb = Heartbeat(output_dir=temp_dir, interval_s=0.1)
    hb.start()
    time.sleep(0.15)

    hb.stop()

    heartbeat_path = os.path.join(temp_dir, "heartbeat.json")
    with open(heartbeat_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["stop"] is True, (
        f"stop should be True after stop(), got {data['stop']}"
    )

    # File should still exist (NOT deleted)
    assert os.path.exists(heartbeat_path), (
        "heartbeat.json should NOT be deleted after stop()"
    )


# ======================================================================
# Test 4: Step updates file with current state
# ======================================================================

def test_given_heartbeat_when_step_updates_then_file_reflects_current_state(temp_dir):
    """Calling step(label, ...) should update the file with the given params."""
    hb = Heartbeat(output_dir=temp_dir, interval_s=0.1)
    hb.start()
    time.sleep(0.15)

    hb.step("sweep", temp_idx=5, vna_idx=2, power_idx=3)
    time.sleep(0.2)

    heartbeat_path = os.path.join(temp_dir, "heartbeat.json")
    with open(heartbeat_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    hb.stop()

    assert data["step"] == "sweep"
    assert data["temp_idx"] == 5
    assert data["vna_idx"] == 2
    assert data["power_idx"] == 3


# ======================================================================
# Test 5: Disk full — no exception
# ======================================================================

def test_given_disk_full_when_writing_then_no_exception_raised(temp_dir):
    """If open/write fails with OSError (disk full), _write must silently ignore."""
    hb = Heartbeat(output_dir=temp_dir, interval_s=0.1)

    # Patch builtins.open to simulate disk-full error
    with patch("builtins.open", side_effect=OSError("No space left on device")):
        # Should not raise
        try:
            hb._write(stop_marker=False)
        except Exception as e:
            pytest.fail(f"_write() raised {type(e).__name__}: {e}")


# ======================================================================
# Test 6: After stop, step does not write
# ======================================================================

def test_given_heartbeat_stopped_when_step_called_then_no_write(temp_dir):
    """After stop(), calling step() should not trigger a new write."""
    hb = Heartbeat(output_dir=temp_dir, interval_s=0.1)
    hb.start()
    time.sleep(0.15)

    hb.stop()

    heartbeat_path = os.path.join(temp_dir, "heartbeat.json")

    # Record file state after stop
    with open(heartbeat_path, "r", encoding="utf-8") as f:
        after_stop = json.load(f)

    stop_mtime = os.path.getmtime(heartbeat_path)

    # Call step after stop — should be ignored
    hb.step("should_not_appear", temp_idx=99, vna_idx=99, power_idx=99)
    time.sleep(0.2)

    # File should be unchanged
    current_mtime = os.path.getmtime(heartbeat_path)
    with open(heartbeat_path, "r", encoding="utf-8") as f:
        current_data = json.load(f)

    # mtime should not have changed (no write happened)
    assert current_mtime == stop_mtime, (
        "File mtime changed after stop — step() triggered an unexpected write"
    )

    # Content should still match post-stop state
    assert current_data == after_stop, (
        "File content changed after stop — step() should not write"
    )
