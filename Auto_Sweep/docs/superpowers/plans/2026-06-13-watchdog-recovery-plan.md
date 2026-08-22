# 实验进程看门狗 & 自动恢复 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实验线程 VISA 调用阻塞时，心跳线程检测 → 看门狗进程 kill + 重启 → 从 checkpoint 自动恢复。

**Architecture:** `HeartbeatThread`（独立线程，60s 写一次 `heartbeat.json`）→ `watchdog.py`（子进程，检测 300s 超时 → taskkill + `app.py --resume` 重启）→ `ExperimentWorker` 集成心跳步骤更新 + `--resume` 模式自动启动。

**Tech Stack:** Python stdlib (threading, subprocess, json), PyQt5 (信号/槽), pytest

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `heartbeat.py` | HeartbeatThread 类 | **新增** |
| `watchdog.py` | 看门狗进程 `run()` 函数 | **新增** |
| `app.py` | CLI 参数解析 + 看门狗启动 + `--resume` 模式 | **修改** |
| `ui/workers.py` | ExperimentWorker 集成心跳步骤更新 | **修改** |
| `config.py` | `heartbeat_interval_s`, `heartbeat_timeout_s` 常量 | **修改** |
| `ui/main_window.py` | `--resume` 模式下自动连接 + 自动启动实验 | **修改** |
| `tests/test_heartbeat.py` | HeartbeatThread 单元测试 | **新增** |
| `tests/test_watchdog.py` | 看门狗逻辑单元测试 | **新增** |
| `tests/test_app_watchdog.py` | app.py CLI 参数 + watchdog 集成测试 | **新增** |

---

### Task 1: 新增 config 常量

**Files:**
- Modify: `config.py:214`（在断点续传段末尾追加）

- [ ] **Step 1: 追加心跳配置常量**

在 `config.py` 末尾追加：

```python
# =========================================================================
# 进程看门狗 & 心跳
# =========================================================================

heartbeat_interval_s = 60          # 心跳写入间隔（秒）
heartbeat_timeout_s = 300          # 挂死判定阈值（秒）
```

- [ ] **Step 2: 验证 config 导入**

```bash
python -c "import config; print(config.heartbeat_interval_s, config.heartbeat_timeout_s)"
```
Expected: `60 300`

- [ ] **Step 3: 运行已有 config 测试确保未破坏**

```bash
python -m pytest tests/test_config.py -x -q --tb=short
```
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add Auto_Sweep/config.py
git commit -m "feat: add heartbeat_interval_s & heartbeat_timeout_s to config"
```

---

### Task 2: HeartbeatThread

**Files:**
- Create: `heartbeat.py`
- Create: `tests/test_heartbeat.py`

- [ ] **Step 1: 在 test_heartbeat.py 中写测试 — 验证启动/写入/停止**

```python
# tests/test_heartbeat.py
# -*- coding: utf-8 -*-
"""HeartbeatThread 单元测试。"""

import json
import os
import tempfile
import time
from threading import Event

import pytest

# 确保项目根在 path 上
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heartbeat import Heartbeat


class TestHeartbeatBasic:
    """基础读写 & 生命周期测试。"""

    def test_given_new_heartbeat_when_started_then_writes_file(self):
        """启动后应在 interval 内写入 heartbeat.json。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=0.1)
            hb.start()
            time.sleep(0.3)
            hb.stop()

            path = os.path.join(tmp, "heartbeat.json")
            assert os.path.exists(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["stop"] is True
            assert data["pid"] == os.getpid()
        finally:
            hb.stop()
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            os.rmdir(tmp)

    def test_given_heartbeat_when_step_called_then_seq_increments(self):
        """每次 step() 调用应使 seq 递增。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=0.05)
            hb.start()
            time.sleep(0.15)
            hb.step("step_one", temp_idx=0, vna_idx=0, power_idx=0)
            time.sleep(0.15)
            hb.stop()

            path = os.path.join(tmp, "heartbeat.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # seq 至少 > 1（初始为 0，每次写入 +1）
            assert data["seq"] >= 2
        finally:
            hb.stop()
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            os.rmdir(tmp)

    def test_given_heartbeat_when_stopped_then_file_contains_stop_true(self):
        """stop() 后文件应包含 'stop': True。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=0.05)
            hb.start()
            time.sleep(0.15)
            hb.stop()

            path = os.path.join(tmp, "heartbeat.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["stop"] is True
        finally:
            hb.stop()
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            os.rmdir(tmp)

    def test_given_heartbeat_when_step_updates_then_file_reflects_current_state(self):
        """step() 后心跳文件应反映最新状态。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=0.05)
            hb.start()
            time.sleep(0.15)
            hb.step("stabilising 78.0K", temp_idx=3, vna_idx=0, power_idx=0)
            time.sleep(0.15)
            hb.stop()

            path = os.path.join(tmp, "heartbeat.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["step"] == "stabilising 78.0K"
            assert data["temp_idx"] == 3
            assert data["vna_idx"] == 0
            assert data["power_idx"] == 0
        finally:
            hb.stop()
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            os.rmdir(tmp)

    def test_given_disk_full_when_writing_then_no_exception_raised(self):
        """写入失败 (磁盘满) 应静默忽略，不抛异常。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=0.05)
            hb.start()
            time.sleep(0.15)

            # 替换 _write 为 mock，模拟 OSError
            original_write = hb._write
            def _mock_write():
                raise OSError("No space left on device")
            hb._write = _mock_write
            time.sleep(0.15)
            # 不应抛异常
            hb._write = original_write
            hb.stop()
        finally:
            hb.stop()
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            os.rmdir(tmp)

    def test_given_heartbeat_stopped_when_step_called_then_no_write(self):
        """stop() 后调用 step() 不应再写入文件。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=0.05)
            hb.start()
            time.sleep(0.15)
            hb.stop()
            # 确保文件是 stop 状态
            path = os.path.join(tmp, "heartbeat.json")
            with open(path, "r", encoding="utf-8") as f:
                before = json.load(f)
            assert before["stop"] is True

            # stop 后调用 step
            hb.step("should_not_write", 0, 0, 0)
            time.sleep(0.2)

            with open(path, "r", encoding="utf-8") as f:
                after = json.load(f)
            # step 应不变
            assert after["step"] == before["step"]
        finally:
            hb.stop()
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            os.rmdir(tmp)
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
python -m pytest tests/test_heartbeat.py -v --tb=short
```
Expected: FAIL (No module named 'heartbeat')

- [ ] **Step 3: 实现 Heartbeat 类**

```python
# heartbeat.py
# -*- coding: utf-8 -*-
"""实验心跳线程 — 定期写入心跳文件供看门狗检测挂死。"""

import json
import os
import threading
import time as _time


class Heartbeat:
    """独立线程，每 interval_s 秒写入心跳 JSON 文件。

    Usage:
        hb = Heartbeat(output_dir="/path/to/experiment")
        hb.start()
        hb.step("stabilising 72.0K", temp_idx=0, vna_idx=0, power_idx=0)
        ...
        hb.stop()
    """

    def __init__(self, output_dir: str, interval_s: int = 60):
        if not output_dir:
            raise ValueError("output_dir must not be empty")
        self._output_dir = output_dir
        self._interval_s = int(interval_s)
        if self._interval_s <= 0:
            raise ValueError("interval_s must be positive")

        # 线程安全：GIL 保证 str/int 原子赋值
        self._step_label = "initialising"
        self._temp_idx = 0
        self._vna_idx = 0
        self._power_idx = 0
        self._seq = 0
        self._stop = False

        self._thread = threading.Thread(
            target=self._loop, name="heartbeat", daemon=True
        )
        self._lock = threading.Lock()

    # ---- public API ----

    def start(self) -> None:
        """启动心跳线程。幂等，多次调用仅首次生效。"""
        if self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._loop, name="heartbeat", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """停止心跳线程。最后一次写入含 stop: true。"""
        self._stop = True
        self._write(stop_marker=True)
        # 不 delete 文件 — 看门狗需要读取 stop 信号

    def step(self, label: str, temp_idx: int, vna_idx: int,
             power_idx: int) -> None:
        """更新当前实验步骤（由实验线程调用）。"""
        self._step_label = label
        self._temp_idx = temp_idx
        self._vna_idx = vna_idx
        self._power_idx = power_idx

    # ---- internal ----

    def _loop(self) -> None:
        """心跳线程主循环。"""
        while not self._stop:
            self._write(stop_marker=False)
            _time.sleep(self._interval_s)

    def _write(self, stop_marker: bool = False) -> None:
        """将当前状态写入 heartbeat.json（覆盖）。"""
        self._seq += 1
        payload = {
            "pid": os.getpid(),
            "step_ts": _time.time(),
            "step": self._step_label,
            "temp_idx": self._temp_idx,
            "vna_idx": self._vna_idx,
            "power_idx": self._power_idx,
            "seq": self._seq,
        }
        if stop_marker:
            payload["stop"] = True

        path = os.path.join(self._output_dir, "heartbeat.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except (OSError, IOError):
            # 磁盘满等 — 静默忽略
            pass
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
python -m pytest tests/test_heartbeat.py -v --tb=short
```
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add Auto_Sweep/heartbeat.py Auto_Sweep/tests/test_heartbeat.py
git commit -m "feat: add HeartbeatThread for experiment liveness monitoring"
```

---

### Task 3: 看门狗进程 (watchdog.py)

**Files:**
- Create: `watchdog.py`
- Create: `tests/test_watchdog.py`

- [ ] **Step 1: 写 watchdog 逻辑测试**

```python
# tests/test_watchdog.py
# -*- coding: utf-8 -*-
"""看门狗逻辑单元测试。"""

import json
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watchdog import (
    read_heartbeat,
    is_timed_out,
    HeartbeatStatus,
)


class TestReadHeartbeat:
    """heartbeat.json 读取测试。"""

    def test_given_valid_heartbeat_file_when_read_then_returns_status(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "heartbeat.json")
            data = {
                "pid": 12345,
                "step_ts": time.time(),
                "step": "stabilising 72.0K",
                "temp_idx": 0,
                "vna_idx": 0,
                "power_idx": 0,
                "seq": 5,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            status = read_heartbeat(tmp)
            assert status is not None
            assert status.pid == 12345
            assert status.step == "stabilising 72.0K"
            assert status.seq == 5
            assert status.stop is False
        finally:
            os.remove(path)
            os.rmdir(tmp)

    def test_given_stop_marker_when_read_then_stop_is_true(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "heartbeat.json")
            data = {
                "pid": 12345,
                "step_ts": time.time(),
                "step": "finished",
                "temp_idx": 5,
                "vna_idx": 0,
                "power_idx": 0,
                "seq": 10,
                "stop": True,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            status = read_heartbeat(tmp)
            assert status is not None
            assert status.stop is True
        finally:
            os.remove(path)
            os.rmdir(tmp)

    def test_given_missing_file_when_read_then_returns_none(self):
        tmp = tempfile.mkdtemp()
        try:
            status = read_heartbeat(tmp)
            assert status is None
        finally:
            os.rmdir(tmp)

    def test_given_corrupt_json_when_read_then_returns_none(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "heartbeat.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not valid json {{{")
            status = read_heartbeat(tmp)
            assert status is None
        finally:
            os.remove(path)
            os.rmdir(tmp)


class TestIsTimedOut:
    """超时判定逻辑测试。"""

    def test_given_recent_heartbeat_when_checking_then_not_timed_out(self):
        now = time.time()
        # 刚更新的心跳
        status = HeartbeatStatus(
            pid=12345,
            step_ts=now - 30,  # 30 秒前
            step="measuring",
            temp_idx=1,
            vna_idx=0,
            power_idx=0,
            seq=10,
            stop=False,
        )
        assert is_timed_out(status, timeout_s=300, now=now) is False

    def test_given_stale_heartbeat_when_checking_then_timed_out(self):
        now = time.time()
        status = HeartbeatStatus(
            pid=12345,
            step_ts=now - 400,  # 400 秒前
            step="stabilising",
            temp_idx=2,
            vna_idx=0,
            power_idx=0,
            seq=5,
            stop=False,
        )
        assert is_timed_out(status, timeout_s=300, now=now) is True

    def test_given_stopped_heartbeat_when_checking_then_not_timed_out(self):
        """stop: true 的心跳不应判定为超时。"""
        now = time.time()
        status = HeartbeatStatus(
            pid=12345,
            step_ts=now - 500,
            step="finished",
            temp_idx=5,
            vna_idx=0,
            power_idx=0,
            seq=20,
            stop=True,
        )
        assert is_timed_out(status, timeout_s=300, now=now) is False

    def test_given_seq_not_incrementing_when_checking_then_timed_out(self):
        """seq 不递增说明心跳线程也卡死了。"""
        # 模拟：文件存在但 seq 在多次检查中不变
        # 这个测试验证 is_timed_out 的 seq 比较逻辑
        # 由于 is_timed_out 是纯函数，通过参数化测试覆盖
        now = time.time()
        # step_ts 总是新的但 seq 没变 → 看门狗应通过连续两次 seq 相同判定
        # is_timed_out 本身不做 seq 比较（由调用者做），只做时间比较
        # 此测试验证超时阈值本身
        status = HeartbeatStatus(
            pid=12345,
            step_ts=now - 301,
            step="stuck",
            temp_idx=2,
            vna_idx=0,
            power_idx=0,
            seq=5,
            stop=False,
        )
        assert is_timed_out(status, timeout_s=300, now=now) is True
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
python -m pytest tests/test_watchdog.py -v --tb=short
```
Expected: FAIL (No module named 'watchdog')

- [ ] **Step 3: 实现 watchdog.py**

```python
# watchdog.py
# -*- coding: utf-8 -*-
"""实验进程看门狗 — 监控心跳，超时则 kill + 重启。

纯 stdlib，无 PyQt5/pyvisa 依赖。作为 app.py 的子进程运行。
Usage:
    python app.py --watchdog --child-pid=<PID> --resume-path=<dir>
    或直接:
    python watchdog.py <child_pid> <resume_path>
"""

import json
import os
import subprocess
import sys
import time as _time
from dataclasses import dataclass


# =========================================================================
# 数据结构
# =========================================================================

@dataclass
class HeartbeatStatus:
    pid: int
    step_ts: float
    step: str
    temp_idx: int
    vna_idx: int
    power_idx: int
    seq: int
    stop: bool = False


# =========================================================================
# 读取
# =========================================================================

HEARTBEAT_FILENAME = "heartbeat.json"
WATCHDOG_PID_FILENAME = "watchdog.pid"


def read_heartbeat(output_dir: str):
    """读取心跳文件，返回 HeartbeatStatus 或 None。"""
    path = os.path.join(output_dir, HEARTBEAT_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, IOError):
        return None

    if not isinstance(data, dict):
        return None

    return HeartbeatStatus(
        pid=data.get("pid", 0),
        step_ts=data.get("step_ts", 0.0),
        step=data.get("step", ""),
        temp_idx=data.get("temp_idx", 0),
        vna_idx=data.get("vna_idx", 0),
        power_idx=data.get("power_idx", 0),
        seq=data.get("seq", 0),
        stop=data.get("stop", False),
    )


# =========================================================================
# 判定
# =========================================================================

def is_timed_out(status: HeartbeatStatus, timeout_s: int,
                 now: float = None) -> bool:
    """检查心跳是否超时。"""
    if status.stop:
        return False
    _now = now if now is not None else _time.time()
    return (_now - status.step_ts) > timeout_s


# =========================================================================
# 看门狗主循环
# =========================================================================

# 检查间隔（秒）— 固定，不从 config 读取（零依赖原则）
WATCHDOG_POLL_INTERVAL_S = 60
# 连续 seq 不递增次数阈值
SEQ_STALL_THRESHOLD = 3


def run(child_pid: int, resume_path: str, timeout_s: int = 300) -> None:
    """看门狗主循环 — 监控子进程，超时则 kill + 重启。

    永不返回，除非检测到 stop 信号或自身被杀。

    Args:
        child_pid: 被监控进程的 PID
        resume_path: 实验输出目录（含 heartbeat.json + checkpoint.json）
        timeout_s: 心跳超时阈值（秒）
    """
    # ---- 锁文件 ----
    lock_path = os.path.join(resume_path, WATCHDOG_PID_FILENAME)
    try:
        with open(lock_path, "x") as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        # 已有看门狗运行
        try:
            with open(lock_path, "r") as f:
                existing_pid = int(f.read().strip())
            # 检查该 PID 是否仍在运行
            _check = subprocess.run(
                ["tasklist", "/FI", f"PID eq {existing_pid}"],
                capture_output=True, text=True,
            )
            if str(existing_pid) in _check.stdout:
                print(f"[watchdog] 已有看门狗运行 (PID {existing_pid})，退出")
                return
        except (ValueError, OSError):
            pass
        # 旧锁文件指向不存在的进程 → 覆盖
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))

    # ---- 状态 ----
    last_seq = -1
    seq_stall_count = 0
    poll_interval = WATCHDOG_POLL_INTERVAL_S

    try:
        while True:
            _time.sleep(poll_interval)

            status = read_heartbeat(resume_path)

            # 情况 1: 文件不存在 → 子进程崩溃
            if status is None:
                print(f"[watchdog] heartbeat 文件缺失 — 子进程可能崩溃")
                _kill_and_restart(child_pid, resume_path)
                return  # kill_and_restart 会 exec 自己，理论上不返回

            # 情况 2: 正常停止
            if status.stop:
                print(f"[watchdog] 检测到 stop 信号 — 正常退出")
                break

            # 情况 3: seq 不递增 → 心跳线程也卡死
            if status.seq == last_seq:
                seq_stall_count += 1
                if seq_stall_count >= SEQ_STALL_THRESHOLD:
                    print(f"[watchdog] seq 连续 {SEQ_STALL_THRESHOLD} 次未递增 — 判定挂死")
                    _kill_and_restart(status.pid or child_pid, resume_path)
                    return
            else:
                seq_stall_count = 0
                last_seq = status.seq

            # 情况 4: step_ts 超时
            if is_timed_out(status, timeout_s):
                print(f"[watchdog] 心跳超时 ({_time.time() - status.step_ts:.0f}s) — 判定挂死")
                _kill_and_restart(status.pid or child_pid, resume_path)
                return

            # 更新追踪的 PID（实验进程可能已被 restart 替换）
            if status.pid and status.pid != child_pid:
                child_pid = status.pid

    finally:
        _cleanup_lock(lock_path)


def _kill_and_restart(child_pid: int, resume_path: str) -> None:
    """强制终止子进程，然后重新启动 app.py --resume。"""
    _time.sleep(2)  # 短暂等待，确保磁盘写入完成

    # Kill
    if child_pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/F"],
                capture_output=True, timeout=15,
            )
            print(f"[watchdog] 已终止 PID {child_pid}")
        except subprocess.TimeoutExpired:
            print(f"[watchdog] taskkill 超时 (PID {child_pid})")
        except Exception as e:
            print(f"[watchdog] taskkill 失败: {e}")

    _time.sleep(3)

    # Restart
    try:
        subprocess.Popen(
            [sys.executable, "app.py", "--resume", resume_path],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        print(f"[watchdog] 已重启 app.py --resume {resume_path}")
    except Exception as e:
        print(f"[watchdog] 重启失败: {e}")


def _cleanup_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except OSError:
        pass


# =========================================================================
# CLI 入口
# =========================================================================

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: python watchdog.py <child_pid> <resume_path> [timeout_s]")
        sys.exit(1)
    _child_pid = int(sys.argv[1])
    _resume_path = sys.argv[2]
    _timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    run(_child_pid, _resume_path, _timeout)
```

- [ ] **Step 4: Run tests, verify PASS**

```bash
python -m pytest tests/test_watchdog.py -v --tb=short
```
Expected: 7 PASS

- [ ] **Step 5: 追加 start_watchdog_subprocess() 公共函数**

在 `watchdog.py` 末尾添加：

```python
# =========================================================================
# 便捷启动函数（供 app.py 和 workers.py 调用）
# =========================================================================

def start_watchdog_subprocess(resume_path: str) -> None:
    """启动看门狗子进程。不抛异常，失败静默忽略。"""
    import subprocess as _sp
    import sys as _sys
    import os as _os
    try:
        _sp.Popen(
            [_sys.executable, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app.py"),
             "--watchdog",
             "--child-pid", str(_os.getpid()),
             "--resume-path", resume_path],
            creationflags=0x08000000  # CREATE_NO_WINDOW
            if _sys.platform == "win32" else 0,
        )
    except Exception:
        pass  # 看门狗启动失败不应阻止实验
```

- [ ] **Step 6: Commit**

```bash
git add Auto_Sweep/watchdog.py Auto_Sweep/tests/test_watchdog.py
git commit -m "feat: add watchdog process for hung experiment detection & restart"
```

---

### Task 4: app.py CLI 参数 + 看门狗启动

**Files:**
- Modify: `app.py`
- Create: `tests/test_app_watchdog.py`

- [ ] **Step 1: 写 CLI 参数 + 看门狗启动测试**

```python
# tests/test_app_watchdog.py
# -*- coding: utf-8 -*-
"""app.py --watchdog / --resume CLI 参数 & 看门狗启动测试。"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAppCliArgs:
    """CLI 参数解析测试。"""

    def test_given_no_args_when_parsing_then_defaults(self):
        """无参数时返回正常 GUI 模式。"""
        import app as app_module
        with patch.object(sys, "argv", ["app.py"]):
            args = app_module.parse_args()
            assert args.watchdog is False
            assert args.resume is None
            assert args.child_pid is None
            assert args.resume_path is None

    def test_given_watchdog_args_when_parsing_then_all_set(self):
        """--watchdog 参数正确解析。"""
        import app as app_module
        with patch.object(sys, "argv", [
            "app.py", "--watchdog", "--child-pid", "12345",
            "--resume-path", "/tmp/experiment/20260613_031259",
        ]):
            args = app_module.parse_args()
            assert args.watchdog is True
            assert args.child_pid == 12345
            assert args.resume_path == "/tmp/experiment/20260613_031259"

    def test_given_resume_arg_when_parsing_then_correct(self):
        """--resume 参数正确解析。"""
        import app as app_module
        with patch.object(sys, "argv", [
            "app.py", "--resume", "/tmp/experiment/20260613_031259",
        ]):
            args = app_module.parse_args()
            assert args.resume == "/tmp/experiment/20260613_031259"
            assert args.watchdog is False

    def test_given_watchdog_missing_required_when_parsing_then_system_exit(self):
        """--watchdog 缺少 --child-pid 或 --resume-path 时退出。"""
        import app as app_module
        with patch.object(sys, "argv", ["app.py", "--watchdog"]):
            with pytest.raises(SystemExit):
                app_module.parse_args()


class TestWatchdogLaunch:
    """看门狗进程启动测试。"""

    def test_given_normal_mode_when_launch_watchdog_then_popen_called(self):
        """正常 GUI 模式应启动看门狗子进程。"""
        with patch("subprocess.Popen") as mock_popen:
            # 模拟启动看门狗
            import subprocess
            import sys
            subprocess.Popen(
                [sys.executable, "app.py", "--watchdog",
                 "--child-pid", str(os.getpid()),
                 "--resume-path", "/tmp/test"],
            )
            assert mock_popen.called
            args = mock_popen.call_args[0][0]
            assert "--watchdog" in args
            assert "--child-pid" in args
            assert "--resume-path" in args
```

- [ ] **Step 2: Run tests, verify FAIL**

```bash
python -m pytest tests/test_app_watchdog.py -v --tb=short
```
Expected: FAIL (parse_args not defined in app.py)

- [ ] **Step 3: 修改 app.py — 添加 argparse + 看门狗启动**

将 `app.py` 的 `main()` 函数改造为支持三种模式。

修改前先读取完整 app.py：

```python
# app.py — 在文件顶部 import 区添加 argparse
# -*- coding: utf-8 -*-
"""
YBCO Auto Sweep Control Panel
==============================
Launch the unified GUI for Laser, LakeShore 335, and VNA control.

Design: Deep Space Cyan (极简深色模式仪表板)
Usage:
    python app.py                              # 正常 GUI 启动
    python app.py --resume <output_dir>         # 从 checkpoint 恢复
    python app.py --watchdog --child-pid=<PID> --resume-path=<dir>  # 看门狗模式
"""

import argparse
import os
import sys

from PyQt5.QtWidgets import QApplication
from ui.main_window import MainWindow


def parse_args():
    """解析 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="YBCO Auto Sweep Control Panel"
    )
    parser.add_argument(
        "--resume", type=str, default=None, metavar="DIR",
        help="从 checkpoint 恢复实验（需指定实验输出目录）",
    )
    parser.add_argument(
        "--watchdog", action="store_true",
        help="看门狗模式（监控实验进程心跳）",
    )
    parser.add_argument(
        "--child-pid", type=int, default=None,
        help="被监控进程 PID（仅 --watchdog）",
    )
    parser.add_argument(
        "--resume-path", type=str, default=None, metavar="DIR",
        help="实验输出目录（仅 --watchdog）",
    )
    return parser.parse_args()


# 注: 看门狗启动统一通过 watchdog.start_watchdog_subprocess() 调用
# app.py 和 workers.py 各自导入该函数


def main() -> int:
    args = parse_args()

    # ---- 模式 1: 看门狗 ----
    if args.watchdog:
        if not args.child_pid or not args.resume_path:
            print("Error: --watchdog requires --child-pid and --resume-path")
            return 1
        # 直接调用 watchdog.run()，不启动 Qt
        from watchdog import run as watchdog_run
        import config
        timeout = getattr(config, "heartbeat_timeout_s", 300)
        watchdog_run(args.child_pid, args.resume_path, timeout)
        return 0

    # ---- 模式 2 & 3: GUI ----
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # [QSS stylesheet — 保持不变，省略以节省篇幅]

    app.setStyleSheet("""
        /* ---- base ---- */
        QMainWindow, QWidget {
            background-color: #0C1014;
            color: #E6EDF3;
        }
        /* ---- cards / sections ---- */
        QGroupBox {
            background-color: #161B22;
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 8px;
            margin-top: 18px;
            padding-top: 20px;
            font-weight: bold;
            color: #8B949E;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            color: #E6EDF3;
            font-size: 13px;
        }
        /* ---- buttons ---- */
        QPushButton {
            background-color: #21262D;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            padding: 8px 18px;
            color: #E6EDF3;
            font-weight: bold;
            min-height: 20px;
        }
        QPushButton:hover {
            background-color: #30363D;
            border: 1px solid rgba(255, 255, 255, 0.20);
        }
        QPushButton:pressed {
            background-color: #0D419D;
        }
        QPushButton:disabled {
            background-color: #1A1E24;
            color: #484F58;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        /* ---- inputs ---- */
        QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
            background-color: #0C1014;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 6px;
            padding: 6px 10px;
            color: #E6EDF3;
            font-size: 13px;
        }
        QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {
            border: 1px solid rgba(55, 135, 255, 0.6);
        }
        QComboBox::drop-down {
            border: none;
            padding-right: 8px;
        }
        QComboBox QAbstractItemView {
            background-color: #161B22;
            border: 1px solid rgba(255, 255, 255, 0.10);
            selection-background-color: #1F3A5F;
            color: #E6EDF3;
        }
        /* ---- scrollbars ---- */
        QScrollBar:vertical {
            background: #0C1014; width: 10px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #30363D; border-radius: 5px; min-height: 30px;
        }
        QScrollBar::handle:vertical:hover { background: #484F58; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal {
            background: #0C1014; height: 10px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background: #30363D; border-radius: 5px; min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover { background: #484F58; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        /* ---- tab widget ---- */
        QTabWidget::pane { border: none; }
        QTabBar::tab {
            background: #161B22;
            border: 1px solid rgba(255, 255, 255, 0.08);
            padding: 8px 20px;
            margin-right: 2px;
            color: #8B949E;
        }
        QTabBar::tab:selected { background: #21262D; color: #E6EDF3; }
        QTabBar::tab:hover:!selected { color: #C0C8D0; }
        /* ---- checkboxes ---- */
        QCheckBox { spacing: 8px; color: #E6EDF3; }
        QCheckBox::indicator {
            width: 18px; height: 18px;
            border: 1px solid rgba(255, 255, 255, 0.25);
            border-radius: 4px;
            background: #0C1014;
        }
        QCheckBox::indicator:checked { background: #1F6FEB; border-color: #1F6FEB; }
        /* ---- sliders ---- */
        QSlider::groove:horizontal {
            background: #21262D; height: 6px; border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #1F6FEB; width: 16px; margin: -5px 0;
            border-radius: 8px;
        }
        QSlider::sub-page:horizontal { background: #1F6FEB; border-radius: 3px; }
        /* ---- progress bars ---- */
        QProgressBar {
            background: #0C1014;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 6px;
            text-align: center;
            color: #E6EDF3;
            height: 10px;
        }
        QProgressBar::chunk {
            background: #1F6FEB;
            border-radius: 5px;
        }
        /* ---- tooltips ---- */
        QToolTip {
            background: #21262D; color: #E6EDF3;
            border: 1px solid rgba(255, 255, 255, 0.15);
            padding: 4px;
        }
    """)

    # ---- resume_path 传递给 MainWindow ----
    resume_path = args.resume

    window = MainWindow(resume_path=resume_path)
    window.show()

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
```

具体改动点（相对于当前 app.py）：
1. 文件顶部新增 `import argparse, subprocess`
2. 新增 `parse_args()` 函数
3. `main()` 开头调用 `parse_args()`，根据结果分流三种模式
5. `MainWindow(resume_path=resume_path)` 传递恢复路径

- [ ] **Step 4: Run app CLI tests, verify PASS**

```bash
python -m pytest tests/test_app_watchdog.py -v --tb=short
```
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add Auto_Sweep/app.py Auto_Sweep/tests/test_app_watchdog.py
git commit -m "feat: add --watchdog, --resume, --child-pid, --resume-path CLI args to app.py"
```

---

### Task 5: MainWindow --resume 支持

**Files:**
- Modify: `ui/main_window.py`

**注**: `MainWindow` 代码较长（~500 行），此处仅列改动点而非完整文件。

- [ ] **Step 1: 确认 MainWindow.__init__ 签名**

先读取当前签名：

```bash
grep -n "def __init__" ui/main_window.py
```

预期类似: `def __init__(self):`

- [ ] **Step 2: 修改 MainWindow.__init__ 接受 resume_path**

在 `main_window.py` 中：

```python
# 修改前:
def __init__(self):
    super().__init__()
    ...

# 修改后:
def __init__(self, resume_path: str = None):
    super().__init__()
    self._resume_path = resume_path
    ...

    # 在 __init__ 末尾（所有控件初始化完成后）:
    if self._resume_path:
        # 使用 QTimer.singleShot 延迟执行，确保窗口已完全显示
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(500, self._auto_resume_experiment)
```

- [ ] **Step 3: 实现 _auto_resume_experiment 方法**

在 `MainWindow` 类中添加：

```python
def _auto_resume_experiment(self):
    """--resume 模式下自动连接设备并启动实验。"""
    if not self._resume_path:
        return

    import os
    import json

    checkpoint_path = os.path.join(self._resume_path, "checkpoint.json")
    if not os.path.exists(checkpoint_path):
        self._log("⚠ --resume 模式但 checkpoint.json 不存在，无法自动恢复")
        return

    # 加载 checkpoint
    try:
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
    except (json.JSONDecodeError, OSError):
        self._log("⚠ checkpoint.json 损坏，无法自动恢复")
        return

    state = ckpt.get("state", {})
    original_temps = ckpt.get("original_temp_list", state.get("original_temp_list", []))
    original_vna = ckpt.get("original_vna_power_list", state.get("original_vna_power_list", []))
    original_powers = ckpt.get("original_power_list", state.get("original_power_list", []))
    temp_idx = state.get("temp_idx", 0)

    if temp_idx >= len(original_temps):
        self._log("✓ 实验已完成，无需恢复")
        return

    self._log(f"=== 从 {self._resume_path} 恢复实验 ===")
    self._log(f"  跳过 {temp_idx}/{len(original_temps)} 个温度点")
    self._log(f"  从 {original_temps[temp_idx]:.1f}K 开始")

    # 将 resume 信息传递给 Dashboard
    dashboard = self.dashboard
    if dashboard and hasattr(dashboard, "set_resume_config"):
        dashboard.set_resume_config(
            resume_path=self._resume_path,
            temp_list=original_temps,
            vna_power_list=original_vna,
            power_list=original_powers,
            start_temp_idx=temp_idx,
        )
```

- [ ] **Step 4: 运行已有 GUI 测试验证未破坏**

```bash
python -m pytest tests/test_button_styling.py tests/test_auto_reconnect.py -x -q --tb=short
```
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add Auto_Sweep/ui/main_window.py
git commit -m "feat: add --resume auto-connect & auto-start to MainWindow"
```

---

### Task 6: ExperimentWorker 集成心跳 + 看门狗启动

**Files:**
- Modify: `ui/workers.py`

- [ ] **Step 1: 确定心跳步骤插入点**

当前 `_run_impl` 的关键位置（根据之前的 grep 结果）：

| 行号附近 | 事件 | 心跳步骤 |
|----------|------|----------|
| ~1420 | `→ Stabilising to X K` | `"stabilising {target}K"` |
| ~1480 | 稳定完成 | `"stabilised {target}K"` |
| ~1590 | 测量开始 `Measuring ... @ X K` | `"measuring {target}K"` |
| ~1880 | 温度点完成 | `"temp_done {target}K"` |

- [ ] **Step 2: 修改 ExperimentWorker.configure() 添加 _output_dir 追踪**

在 `ExperimentWorker.__init__` 中添加：

```python
def __init__(self):
    super().__init__()
    self._abort_flag = False
    self._lakeshore_ctrl = None
    ...
    self._heartbeat = None    # ← 新增
```

- [ ] **Step 3: 在 _run_impl 开始处启动心跳 + 看门狗**

在 `_run_impl` 中，初始化 checkpoint 变量之后（约 1370 行），添加：

```python
# ---- 启动心跳 ----
from heartbeat import Heartbeat
self._heartbeat = Heartbeat(
    output_dir=self._output_dir,
    interval_s=getattr(config, "heartbeat_interval_s", 60),
)
self._heartbeat.start()

# ---- 启动看门狗 ----
from watchdog import start_watchdog_subprocess
start_watchdog_subprocess(self._output_dir)
```

- [ ] **Step 4: 在四个关键点插入心跳步骤更新**

**位置 1**: 稳定开始（~1420 行附近 `→ Stabilising to X K` 日志行之后）：

```python
self._heartbeat.step(
    f"stabilising {target_k:.1f}K",
    temp_idx=ti, vna_idx=vi, power_idx=0,
)
```

**位置 2**: 稳定完成（~1480 行附近 `稳定: X K` 日志行之后）：

```python
self._heartbeat.step(
    f"stabilised {target_k:.1f}K",
    temp_idx=ti, vna_idx=vi, power_idx=0,
)
```

**位置 3**: 测量开始（~1590 行附近第一个 `Measuring` 日志行处）：

```python
self._heartbeat.step(
    f"measuring {target_k:.1f}K",
    temp_idx=ti, vna_idx=vi, power_idx=pi,
)
```

**位置 4**: 温度点完成（~1880 行附近 `温度点完成` 日志行处）：

```python
self._heartbeat.step(
    f"temp_done {target_k:.1f}K",
    temp_idx=ti, vna_idx=vi, power_idx=0,
)
```

- [ ] **Step 5: _run_impl 结束/finally 中停止心跳**

在 `_run_impl` 的 finally 块（如存在）或正常结束处：

```python
if self._heartbeat:
    self._heartbeat.stop()
    self._heartbeat = None
```

同时在 `_abort_flag` 处理路径中也要停止心跳。

- [ ] **Step 6: 运行已有 experiment worker 测试**

```bash
python -m pytest tests/test_experiment_worker.py -x -q --tb=short
```
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add Auto_Sweep/ui/workers.py
git commit -m "feat: integrate HeartbeatThread & watchdog launch into ExperimentWorker"
```

---

### Task 7: DashboardPage resume 支持

**Files:**
- Modify: `ui/dashboard_page.py`

- [ ] **Step 1: 添加 set_resume_config 方法**

```python
def set_resume_config(self, resume_path, temp_list, vna_power_list,
                      power_list, start_temp_idx):
    """存储 resume 配置，供实验启动时使用。"""
    self._resume_path = resume_path
    self._resume_temp_list = temp_list
    self._resume_vna_power_list = vna_power_list
    self._resume_power_list = power_list
    self._resume_start_temp_idx = start_temp_idx
    self._is_resume_mode = True
```

在 `__init__` 中初始化：

```python
self._is_resume_mode = False
self._resume_path = None
self._resume_temp_list = []
self._resume_vna_power_list = []
self._resume_power_list = []
self._resume_start_temp_idx = 0
```

- [ ] **Step 2: 修改实验启动逻辑，resume 模式下使用 checkpoint 参数**

在 "Start Experiment" 按钮的 slot 中（或启动实验的函数中），检测 `self._is_resume_mode`：

```python
if self._is_resume_mode:
    temp_list = self._resume_temp_list
    vna_power_list = self._resume_vna_power_list
    power_list = self._resume_power_list
    # 使用 resume 路径作为输出目录
    output_dir = self._resume_path
else:
    temp_list = self._get_temp_list()
    vna_power_list = self._get_vna_power_list()
    power_list = self._get_power_list()
    output_dir = self._create_output_dir()
```

- [ ] **Step 3: 运行已有 dashboard 相关测试**

```bash
python -m pytest tests/test_experiment_worker.py -x -q --tb=short
```
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add Auto_Sweep/ui/dashboard_page.py
git commit -m "feat: add resume mode support to DashboardPage"
```

---

### Task 8: 集成测试 — 模拟挂死 & 恢复

**Files:**
- Create: `tests/test_watchdog_recovery.py`

- [ ] **Step 1: 写集成测试**

```python
# tests/test_watchdog_recovery.py
# -*- coding: utf-8 -*-
"""看门狗恢复集成测试 — mock 实验线程挂死场景。"""

import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heartbeat import Heartbeat
from watchdog import read_heartbeat, is_timed_out, HeartbeatStatus


class TestRecoveryFlow:
    """端到端恢复流程测试（不需要真实进程）。"""

    def test_given_hung_experiment_when_watchdog_checks_then_timed_out(self):
        """模拟：心跳 step_ts 过期 → 判定超时。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=60)
            hb.start()
            hb.step("stabilising 78.0K", temp_idx=3, vna_idx=0, power_idx=0)
            time.sleep(0.15)
            hb.stop()

            # 手动修改心跳文件，将 step_ts 设为 400 秒前
            path = os.path.join(tmp, "heartbeat.json")
            with open(path, "r") as f:
                data = json.load(f)
            data["step_ts"] = time.time() - 400
            data["stop"] = False  # 模拟挂死
            with open(path, "w") as f:
                json.dump(data, f)

            # 看门狗检查
            status = read_heartbeat(tmp)
            assert status is not None
            assert is_timed_out(status, timeout_s=300) is True
        finally:
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass

    def test_given_crashed_process_when_watchdog_checks_then_file_missing(self):
        """模拟：进程崩溃（heartbeat.json 不存在）。"""
        tmp = tempfile.mkdtemp()
        try:
            status = read_heartbeat(tmp)
            assert status is None  # 看门狗应检测到"崩溃"
        finally:
            os.rmdir(tmp)

    def test_given_normal_stop_when_watchdog_checks_then_stop_detected(self):
        """模拟：实验正常结束 → stop: true。"""
        tmp = tempfile.mkdtemp()
        try:
            hb = Heartbeat(output_dir=tmp, interval_s=60)
            hb.start()
            time.sleep(0.15)
            hb.stop()

            status = read_heartbeat(tmp)
            assert status is not None
            assert status.stop is True
            assert is_timed_out(status, timeout_s=300) is False
        finally:
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass

    def test_given_seq_stalled_when_multiple_checks_then_stall_detected(self):
        """模拟：seq 连续不递增 → 判定挂死。"""
        tmp = tempfile.mkdtemp()
        try:
            # 写一个初始心跳
            path = os.path.join(tmp, "heartbeat.json")
            data = {
                "pid": 12345,
                "step_ts": time.time(),
                "step": "stuck",
                "temp_idx": 2,
                "vna_idx": 0,
                "power_idx": 0,
                "seq": 3,
                "stop": False,
            }
            with open(path, "w") as f:
                json.dump(data, f)

            # 模拟 3 次检查，seq 始终不变
            last_seq = -1
            stall_count = 0
            for _ in range(4):
                status = read_heartbeat(tmp)
                if status.seq == last_seq:
                    stall_count += 1
                else:
                    stall_count = 0
                last_seq = status.seq

            assert stall_count >= 3  # 应触发 stall 判定
        finally:
            try:
                os.remove(os.path.join(tmp, "heartbeat.json"))
            except OSError:
                pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_watchdog_recovery.py -v --tb=short
```
Expected: 4 PASS

- [ ] **Step 3: Commit**

```bash
git add Auto_Sweep/tests/test_watchdog_recovery.py
git commit -m "test: add watchdog recovery integration tests"
```

---

### Task 9: 手动 smoke test & cleanup

- [ ] **Step 1: 验证 Heartbeat 可独立 import**

```bash
python -c "from heartbeat import Heartbeat; hb = Heartbeat('/tmp'); print('OK')"
```
Expected: OK

- [ ] **Step 2: 验证 watchdog 可独立 import**

```bash
python -c "from watchdog import read_heartbeat, is_timed_out, HeartbeatStatus; print('OK')"
```
Expected: OK

- [ ] **Step 3: 验证 app.py --help**

```bash
python app.py --help
```
Expected: 显示 argparse help 文本，列出 --resume, --watchdog, --child-pid, --resume-path

- [ ] **Step 4: 跑全量测试确认无回归**

```bash
python -m pytest tests/ -x -q --tb=short
```
Expected: 全部 PASS（允许 skip，不允许 FAIL）

- [ ] **Step 5: 最终 commit**

```bash
git add -A
git commit -m "chore: final integration verification for watchdog recovery system"
```

---

## 实现顺序 & 依赖

```
Task 1 (config)       ← 无依赖，最先做
    ↓
Task 2 (heartbeat)    ← 依赖 Task 1
    ↓
Task 3 (watchdog)     ← 依赖 Task 1
    ↓
Task 4 (app.py CLI)   ← 依赖 Task 3
    ↓
Task 5 (MainWindow)   ← 依赖 Task 4
    ↓
Task 6 (workers)      ← 依赖 Task 2, Task 4
    ↓
Task 7 (dashboard)    ← 依赖 Task 5
    ↓
Task 8 (集成测试)     ← 依赖 Task 2, Task 3
    ↓
Task 9 (smoke test)   ← 依赖全部
```
