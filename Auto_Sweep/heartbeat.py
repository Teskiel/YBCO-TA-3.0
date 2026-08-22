# -*- coding: utf-8 -*-
"""
Heartbeat — 独立心跳线程，定期写入 heartbeat.json 供外部 watchdog 检测挂死。

纯 stdlib 实现，零外部依赖。线程安全（step() 和 _write() 使用锁保护共享状态）。
"""

import json
import os
import threading
import time


class Heartbeat:
    """独立心跳线程。

    每隔 interval_s 秒将当前实验进度写入 heartbeat.json。
    外部 watchdog 进程通过检查文件修改时间判断实验是否挂死。

    Usage::

        hb = Heartbeat(output_dir="/path/to/experiment")
        hb.start()
        # ... experiment loop ...
        hb.step("ramp", temp_idx=0, vna_idx=0, power_idx=0)
        # ...
        hb.step("sweep", temp_idx=5, vna_idx=2, power_idx=3)
        # ...
        hb.stop()   # writes stop: true, file persists
    """

    def __init__(self, output_dir, interval_s=60):
        """初始化心跳线程。

        Args:
            output_dir: heartbeat.json 输出目录（字符串，必须非空）。
            interval_s: 心跳写入间隔（秒），必须 > 0。

        Raises:
            ValueError: output_dir 为空或 interval_s <= 0。
        """
        if not output_dir:
            raise ValueError("output_dir must be a non-empty string")
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")

        self._output_dir = output_dir
        self._interval_s = interval_s

        # 共享状态，由 _lock 保护
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._step_ts = time.time()
        self._step_label = ""
        self._temp_idx = 0
        self._vna_idx = 0
        self._power_idx = 0
        self._seq = 0

        # 线程控制
        self._stop_event = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def start(self):
        """启动心跳守护线程（幂等：重复调用不创建多个线程）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="heartbeat-writer")
        self._thread.start()

    def stop(self):
        """停止心跳线程，写入最终 stop: true 记录。

        文件不会被删除——watchdog 可通过 stop 字段区分正常结束与挂死。
        """
        self._stop_event.set()
        self._write(stop_marker=True)
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 5)

    def step(self, label, temp_idx=0, vna_idx=0, power_idx=0):
        """更新当前实验步骤（原子操作，线程安全）。

        seq 自动递增，step_ts 更新为当前时间。

        Args:
            label: 步骤标签，如 "ramp", "sweep", "idle"。
            temp_idx: 温度索引。
            vna_idx: VNA 功率索引。
            power_idx: 激光功率索引。
        """
        if self._stop_event.is_set():
            return  # 已停止，忽略后续 step 调用
        with self._lock:
            self._step_label = label
            self._temp_idx = temp_idx
            self._vna_idx = vna_idx
            self._power_idx = power_idx
            self._seq += 1
            self._step_ts = time.time()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _loop(self):
        """后台线程主循环：周期写入心跳直到收到停止信号。"""
        while not self._stop_event.is_set():
            self._write(stop_marker=False)
            # 分段 sleep 以便快速响应 stop 信号
            self._stop_event.wait(self._interval_s)

    def _write(self, stop_marker=False):
        """将当前状态写入 heartbeat.json（覆盖写，约 200 字节）。

        Args:
            stop_marker: True 时 stop 字段为 true（最终记录）。

        写入失败（磁盘满等）静默忽略，绝不抛出异常。
        """
        with self._lock:
            payload = {
                "pid": self._pid,
                "step_ts": self._step_ts,
                "step": self._step_label,
                "temp_idx": self._temp_idx,
                "vna_idx": self._vna_idx,
                "power_idx": self._power_idx,
                "seq": self._seq,
                "stop": stop_marker or self._stop_event.is_set(),
            }

        filepath = os.path.join(self._output_dir, "heartbeat.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except OSError:
            pass  # 磁盘满等 I/O 错误静默忽略
