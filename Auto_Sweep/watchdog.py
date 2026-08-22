# -*- coding: utf-8 -*-
"""
Watchdog — 独立看门狗进程，监控子进程心跳并自动恢复挂死的实验。

纯 stdlib 实现，零外部依赖。通过 heartbeat.json 检测子进程状态：
  - 文件缺失 → 子进程崩溃 → 杀掉残留并重启
  - stop: true  → 正常退出 → 看门狗退出
  - seq 连续 3 次不递增 → 子进程挂死 → 杀掉并重启
  - step_ts 超时 → 子进程挂死 → 杀掉并重启

Usage::

    # 由 app.py 或 workers.py 启动
    from watchdog import start_watchdog_subprocess
    start_watchdog_subprocess(resume_path="/path/to/experiment")

    # 或直接运行
    python watchdog.py /path/to/experiment <child_pid>
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WATCHDOG_POLL_INTERVAL_S = 60       # 轮询心跳间隔（秒）
SEQ_STALL_THRESHOLD = 3              # seq 连续不递增次数阈值
HEARTBEAT_FILENAME = "heartbeat.json"
WATCHDOG_PID_FILENAME = "watchdog.pid"

# 看门狗自身进程的 PID 文件存活宽限：超过此时间未更新视为陈旧
_WATCHDOG_PID_STALE_S = 180  # 3 分钟


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HeartbeatStatus:
    """心跳状态（与 heartbeat.py 的 JSON 格式一一对应）。"""
    pid: int
    step_ts: float
    step: str
    temp_idx: int
    vna_idx: int
    power_idx: int
    seq: int
    stop: bool = False


# ---------------------------------------------------------------------------
# 读取心跳文件
# ---------------------------------------------------------------------------

def read_heartbeat(output_dir):
    """读取 heartbeat.json 并返回 HeartbeatStatus，失败返回 None。

    Args:
        output_dir: heartbeat.json 所在目录的路径。

    Returns:
        HeartbeatStatus | None
    """
    filepath = os.path.join(output_dir, HEARTBEAT_FILENAME)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    # 字段缺失时使用默认值（防御性解析）
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


# ---------------------------------------------------------------------------
# 超时判断
# ---------------------------------------------------------------------------

def is_timed_out(status, timeout_s, now=None):
    """判断心跳是否超时。

    Args:
        status: HeartbeatStatus 实例。
        timeout_s: 超时阈值（秒）。
        now: 当前时间戳，None 则使用 time.time()。

    Returns:
        bool: True 表示已超时（且未停止），False 表示正常。
    """
    if now is None:
        now = time.time()
    if status.stop:
        return False
    return (now - status.step_ts) > timeout_s


# ---------------------------------------------------------------------------
# 锁文件管理
# ---------------------------------------------------------------------------

def _read_watchdog_pid(resume_path):
    """读取 watchdog.pid 文件，返回 PID（int）或 None。"""
    pid_path = os.path.join(resume_path, WATCHDOG_PID_FILENAME)
    try:
        with open(pid_path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _write_watchdog_pid(resume_path):
    """将当前进程 PID 写入 watchdog.pid。"""
    pid_path = os.path.join(resume_path, WATCHDOG_PID_FILENAME)
    try:
        os.makedirs(resume_path, exist_ok=True)
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass  # 磁盘满等情况静默忽略


def _remove_watchdog_pid(resume_path):
    """删除 watchdog.pid 锁文件。"""
    pid_path = os.path.join(resume_path, WATCHDOG_PID_FILENAME)
    try:
        os.remove(pid_path)
    except (FileNotFoundError, OSError):
        pass


def _is_process_alive(pid):
    """检查给定 PID 的进程是否存活（Windows 兼容）。"""
    try:
        # os.kill(pid, 0) 在 Windows 上不适用（无 SIG 0）
        # 使用 tasklist 检查
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return str(pid) in result.stdout
    except OSError:
        return False


def _check_existing_watchdog(resume_path):
    """检查是否已有有效的看门狗在运行。

    Returns:
        True: 已有有效看门狗运行（当前进程应退出）。
        False: 无有效看门狗（可继续启动）。
    """
    existing_pid = _read_watchdog_pid(resume_path)
    if existing_pid is None:
        return False
    if existing_pid == os.getpid():
        return False  # 自己的 PID（不太可能但防御一下）
    if _is_process_alive(existing_pid):
        return True
    # PID 文件存在但进程已死 → 视为陈旧，覆盖
    return False


# ---------------------------------------------------------------------------
# 杀进程 & 重启
# ---------------------------------------------------------------------------

def _kill_and_restart(child_pid, resume_path, timeout_s):
    """杀掉子进程并重新启动实验。

    Args:
        child_pid: 要杀掉的子进程 PID（int）。
        resume_path: 实验数据目录路径。
        timeout_s: 超时阈值（传递给重启后的新看门狗）。
    """
    # 1. 强制杀子进程
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.kill(child_pid, 9)  # SIGKILL, fallback
    except OSError:
        pass  # 进程可能已退出

    # 2. 等待 3 秒确保端口/文件释放
    time.sleep(3)

    # 3. 重启实验（启动新的 app.py 实例，传递 --resume）
    app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [sys.executable, app_py, "--resume", resume_path,
             "--watchdog-timeout", str(timeout_s)],
            **kwargs,
        )
    except OSError:
        pass  # 无法重启则静默退出


# ---------------------------------------------------------------------------
# 看门狗主循环
# ---------------------------------------------------------------------------

def run(child_pid, resume_path, timeout_s=300):
    """看门狗主循环——监控子进程心跳，检测挂死后自动恢复。

    运行直到以下任一条件满足：
      - heartbeat.json 中 stop == True（子进程正常退出）
      - 收到 KeyboardInterrupt / SIGTERM
      - 无法重启（_kill_and_restart 失败）

    Args:
        child_pid: 被监控的子进程 PID（int）。
        resume_path: 实验数据目录路径（heartbeat.json 所在目录）。
        timeout_s: 心跳超时阈值（秒），默认 300。
    """
    # 锁文件：防止同时运行多个看门狗
    if _check_existing_watchdog(resume_path):
        print(f"[watchdog] 已有有效看门狗在监控 {resume_path}，退出。")
        return

    _write_watchdog_pid(resume_path)
    print(f"[watchdog] 启动，监控 PID={child_pid}，目录={resume_path}，"
          f"超时={timeout_s}s")

    last_seq = -1
    stall_count = 0

    try:
        while True:
            time.sleep(WATCHDOG_POLL_INTERVAL_S)

            # 检查子进程是否存活
            if not _is_process_alive(child_pid):
                # 子进程已死 → 读取最后一次心跳判断是否是正常退出
                status = read_heartbeat(resume_path)
                if status is not None and status.stop:
                    print("[watchdog] 子进程正常退出 (stop=true)，看门狗退出。")
                    break
                else:
                    print(f"[watchdog] 子进程 PID={child_pid} 已消失，重启实验。")
                    _kill_and_restart(child_pid, resume_path, timeout_s)
                    break

            # 读取心跳
            status = read_heartbeat(resume_path)

            if status is None:
                # 心跳文件缺失 → 子进程可能已经崩溃
                print("[watchdog] 心跳文件缺失，子进程可能已崩溃，重启实验。")
                _kill_and_restart(child_pid, resume_path, timeout_s)
                break

            if status.stop:
                print("[watchdog] 检测到 stop=true，子进程正常退出。")
                break

            # 检查 seq 停滞
            if status.seq == last_seq:
                stall_count += 1
                if stall_count >= SEQ_STALL_THRESHOLD:
                    print(f"[watchdog] seq={status.seq} 连续 {stall_count} 次"
                          f"未递增，子进程挂死，重启实验。")
                    _kill_and_restart(child_pid, resume_path, timeout_s)
                    break
            else:
                stall_count = 0
                last_seq = status.seq

            # 检查 step_ts 超时
            if is_timed_out(status, timeout_s):
                elapsed = time.time() - status.step_ts
                print(f"[watchdog] 心跳超时（距上次 step {elapsed:.0f}s > "
                      f"{timeout_s}s），子进程挂死，重启实验。")
                _kill_and_restart(child_pid, resume_path, timeout_s)
                break

    except KeyboardInterrupt:
        print("[watchdog] 收到中断信号，退出。")
    finally:
        _remove_watchdog_pid(resume_path)
        print("[watchdog] 锁文件已清理，退出。")


# ---------------------------------------------------------------------------
# 启动看门狗子进程
# ---------------------------------------------------------------------------

def start_watchdog_subprocess(resume_path, child_pid=None, timeout_s=300):
    """以独立子进程启动看门狗（零异常保证）。

    看门狗进程与父进程解耦，即使父进程退出也能继续监控。

    Args:
        resume_path: 实验数据目录路径。
        child_pid: 被监控的子进程 PID，None 则使用当前进程 PID。
        timeout_s: 心跳超时阈值（秒），默认 300。

    Returns:
        subprocess.Popen | None: 成功返回 Popen 对象，失败返回 None。
    """
    if child_pid is None:
        child_pid = os.getpid()

    watchdog_py = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "watchdog.py"
    )

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(
            [sys.executable, watchdog_py,
             str(child_pid), resume_path, str(timeout_s)],
            **kwargs,
        )
        return proc
    except OSError:
        return None


# ---------------------------------------------------------------------------
# CLI 入口（用于独立运行 watchdog.py）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: python watchdog.py <child_pid> <resume_path> [timeout_s]")
        sys.exit(1)

    _child_pid = int(sys.argv[1])
    _resume_path = sys.argv[2]
    _timeout_s = int(sys.argv[3]) if len(sys.argv) >= 4 else 300

    run(_child_pid, _resume_path, _timeout_s)
