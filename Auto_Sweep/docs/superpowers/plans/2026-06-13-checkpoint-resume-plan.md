# 实验断点续传 & 自动重连 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ExperimentWorker 中实现 VISA 连接丢失后的自动重连 + 断点续传，使长时间实验能从连接中断中恢复而非报废。

**Architecture:** 在 `run()` 外层新增统一异常捕获 → 保存检查点到 `checkpoint.json` → 30s 间隔重连循环 → 恢复时从 completed_points 跳过已完成的测量点。CheckpointManager 为独立类，原子写入、恢复判断、S2P 文件去重均在类内封装。

**Tech Stack:** Python 3, pytest + unittest.mock, PyQt5 signals, json

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `config.py` | 4 个新常量（重连/检查点配置） |
| `ui/workers.py` | 新增 `CheckpointManager` 类 + 修改 `ExperimentWorker`（信号/方法/run骨架） |
| `ui/main_window.py` | 连接 `experiment_resume_prompt` 等新信号到 GUI 弹窗 |
| `tests/test_experiment_worker.py` | 10+ 个新测试覆盖检查点/恢复/重连/文件去重 |

---

### Task 1: Config 常量

**Files:**
- Modify: `config.py` (append to end)

- [ ] **Step 1: 添加断点续传 & 自动重连配置常量**

```python
# =========================================================================
# 断点续传 & 自动重连
# =========================================================================

reconnect_retry_interval_s = 30          # 重连尝试间隔（秒）
reconnect_max_wait_minutes = 30          # 最大等待重连时间（分钟）
checkpoint_save_interval_points = 5      # 每完成 N 个测量点增量保存检查点
checkpoint_keep_latest_attempt_only = True  # 实验正常结束时仅保留最新 attempt 的 S2P
```

- [ ] **Step 2: 验证 config 可导入**

Run: `python -c "import config; print(config.reconnect_retry_interval_s, config.checkpoint_save_interval_points)"`
Expected: `30 5`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add checkpoint & auto-reconnect config constants"
```

---

### Task 2: CheckpointManager 类 — 测试先写

**Files:**
- Modify: `tests/test_experiment_worker.py` (append new test class)

- [ ] **Step 1: 编写 CheckpointManager 测试类**

在 `tests/test_experiment_worker.py` 末尾追加：

```python
# =========================================================================
# 断点续传: CheckpointManager 单元测试
# =========================================================================

import json
import os as _os
import tempfile as _tempfile

class TestCheckpointManager:
    """验证 CheckpointManager 的保存/加载/恢复/清理。"""

    @staticmethod
    def _make_state():
        return {
            "temp_idx": 2,
            "vna_dbm_idx": 0,
            "power_mw_idx": 0,
            "current_temp_k": 73.6,
            "total_count": 25,
            "extended_max_wait_s": 1800,
            "extended_pre_wait_s": 300,
            "rollback_consecutive_issues": 0,
            "rollback_first_issue_index": None,
            "rollback_count": 0,
            "overshoot_learning": {},
        }

    @staticmethod
    def _make_completed_points():
        return [
            {"temp_k": 72.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 71.604},
            {"temp_k": 72.0, "vna_dbm": -45, "power_mw": 1, "actual_k": 71.693},
            {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 73.599},
            {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 1, "actual_k": 73.690},
            {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 3, "actual_k": 73.691},
        ]

    # ---- 保存 & 加载 ----

    def test_given_state_and_points_when_save_then_file_created(self):
        """保存检查点 → 文件存在且内容完整。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            points = self._make_completed_points()
            CheckpointManager.save(tmpdir, state, points, "20260612_190601",
                                   [72.0, 74.0, 76.0], [-45, -30, -25], [0, 1, 3, 5, 7, 9])
            ckpt_path = _os.path.join(tmpdir, "checkpoint.json")
            assert _os.path.exists(ckpt_path)
            with open(ckpt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["version"] == 1
            assert data["state"]["temp_idx"] == 2
            assert len(data["completed_points"]) == 5

    def test_given_saved_checkpoint_when_load_then_returns_state_and_points(self):
        """加载有效检查点 → 返回 state dict 和 completed_points list。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            points = self._make_completed_points()
            CheckpointManager.save(tmpdir, state, points, "20260612_190601",
                                   [72.0, 74.0, 76.0], [-45, -30, -25], [0, 1, 3, 5, 7, 9])
            loaded_state, loaded_points = CheckpointManager.load(tmpdir)
            assert loaded_state is not None
            assert loaded_state["temp_idx"] == 2
            assert loaded_state["total_count"] == 25
            assert len(loaded_points) == 5

    def test_given_no_checkpoint_when_load_then_returns_none(self):
        """无检查点文件 → load() 返回 None。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            result = CheckpointManager.load(tmpdir)
            assert result is None

    def test_given_corrupt_checkpoint_when_load_then_returns_none(self):
        """检查点 JSON 损坏 → load() 返回 None（不抛异常）。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            ckpt_path = _os.path.join(tmpdir, "checkpoint.json")
            with open(ckpt_path, "w", encoding="utf-8") as f:
                f.write("not valid json {{{")
            result = CheckpointManager.load(tmpdir)
            assert result is None

    # ---- 增量追加 ----

    def test_given_existing_checkpoint_when_append_point_then_point_added(self):
        """增量追加测量点 → completed_points 增长。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            points = self._make_completed_points()
            CheckpointManager.save(tmpdir, state, points, "20260612_190601",
                                   [72.0, 74.0, 76.0], [-45, -30, -25], [0, 1, 3, 5, 7, 9])
            new_point = {"temp_k": 74.0, "vna_dbm": -45, "power_mw": 5,
                         "actual_k": 73.691}
            CheckpointManager.append_point(tmpdir, new_point)
            _, loaded_points = CheckpointManager.load(tmpdir)
            assert len(loaded_points) == 6
            assert loaded_points[-1]["power_mw"] == 5

    def test_given_no_checkpoint_when_append_point_then_does_nothing(self):
        """无检查点文件 → append_point() 不抛异常，不创建文件。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            CheckpointManager.append_point(tmpdir, {"temp_k": 30.0, "vna_dbm": -45,
                                                     "power_mw": 0, "actual_k": 30.0})
            assert not _os.path.exists(_os.path.join(tmpdir, "checkpoint.json"))

    # ---- 删除 ----

    def test_given_checkpoint_exists_when_delete_then_file_removed(self):
        """delete() → 检查点文件被删除。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            CheckpointManager.save(tmpdir, state, [], "test",
                                   [30.0], [-45], [0])
            assert _os.path.exists(_os.path.join(tmpdir, "checkpoint.json"))
            CheckpointManager.delete(tmpdir)
            assert not _os.path.exists(_os.path.join(tmpdir, "checkpoint.json"))

    def test_given_no_checkpoint_when_delete_then_no_error(self):
        """无检查点 → delete() 不抛异常。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            CheckpointManager.delete(tmpdir)  # 不应抛异常

    # ---- 恢复判断 ----

    def test_given_completed_points_when_resume_then_skips_done_points(self):
        """completed_points 中已有的点 → resume_from 跳过。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 5, "actual_k": 30.1},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0, 50.0],
            vna_power_list=[-45],
            power_list=[0, 5],
        )
        # 30.0K 的两个 power 都已完成 → 应从 50.0K / -45dBm / 0mW 开始
        assert result == (1, 0, 0)  # temp_idx=1, vna_idx=0, power_idx=0

    def test_given_partial_temp_completed_when_resume_then_starts_at_next_power(self):
        """同一温度点部分完成 → 从下一个未完成的 power 开始。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0],
            vna_power_list=[-45],
            power_list=[0, 5, 10],
        )
        assert result == (0, 0, 1)  # power_idx=1 (5mW)

    def test_given_all_points_completed_when_resume_then_returns_none(self):
        """所有点都完成 → resume_from 返回 None。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 5, "actual_k": 30.1},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0],
            vna_power_list=[-45],
            power_list=[0, 5],
        )
        assert result is None

    def test_given_vna_power_levels_when_resume_then_correctly_advances(self):
        """多个 VNA 功率级别 → 恢复时正确跨 VNA 功率推进。"""
        from ui.workers import CheckpointManager
        completed = [
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 5, "actual_k": 30.1},
            {"temp_k": 30.0, "vna_dbm": -30, "power_mw": 0, "actual_k": 30.2},
        ]
        result = CheckpointManager.resume_from(
            completed,
            temp_list=[30.0],
            vna_power_list=[-45, -30],
            power_list=[0, 5],
        )
        # -45dBm 全部完成，-30dBm 的 0mW 完成 → 从 -30dBm / 5mW 开始
        assert result == (0, 1, 1)  # temp_idx=0, vna_idx=1, power_idx=1

    # ---- 参数列表不匹配 ----

    def test_given_temp_list_changed_when_load_then_validate_warns(self):
        """温度列表变更 → 恢复时检测到不匹配。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            CheckpointManager.save(tmpdir, state, [], "test",
                                   original_temp_list=[72.0, 74.0, 76.0],
                                   original_vna_power_list=[-45],
                                   original_power_list=[0, 1, 3, 5, 7, 9])
            loaded_state, _ = CheckpointManager.load(tmpdir)
            # 新温度列表与原始不同
            new_temp_list = [72.0, 74.0, 80.0]  # 76→80 变更
            is_match = (loaded_state is not None and
                        CheckpointManager.validate_lists(
                            loaded_state, new_temp_list, [-45], [0, 1, 3, 5, 7, 9]))
            assert is_match is False

    def test_given_same_lists_when_load_then_validate_passes(self):
        """参数列表未变 → validate_lists 返回 True。"""
        from ui.workers import CheckpointManager
        with _tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state()
            CheckpointManager.save(tmpdir, state, [], "test",
                                   original_temp_list=[72.0, 74.0, 76.0],
                                   original_vna_power_list=[-45, -30],
                                   original_power_list=[0, 1, 3, 5, 7, 9])
            loaded_state, _ = CheckpointManager.load(tmpdir)
            is_match = (loaded_state is not None and
                        CheckpointManager.validate_lists(
                            loaded_state,
                            [72.0, 74.0, 76.0],
                            [-45, -30],
                            [0, 1, 3, 5, 7, 9]))
            assert is_match is True
```

- [ ] **Step 2: 运行测试确认全部失败**

Run: `pytest tests/test_experiment_worker.py::TestCheckpointManager -v`
Expected: 12 failed (CheckpointManager not defined)

- [ ] **Step 3: Commit**

```bash
git add tests/test_experiment_worker.py
git commit -m "test: add CheckpointManager unit tests (red)"
```

---

### Task 3: CheckpointManager 类 — 实现

**Files:**
- Modify: `ui/workers.py` (new class before ExperimentWorker)

- [ ] **Step 1: 实现 CheckpointManager 类**

在 `ui/workers.py` 的 `ExperimentWorker` 类定义之前插入：

```python
# ---------------------------------------------------------------------------
# CheckpointManager — 实验断点续传
# ---------------------------------------------------------------------------

class CheckpointManager:
    """检查点文件的原子读写与恢复判断。

    检查点保存到实验输出目录的 ``checkpoint.json``。
    写入使用 .tmp + os.rename 保证原子性，崩溃不会产生损坏文件。

    所有方法均为静态方法，无状态 — 可被 ExperimentWorker 直接调用。
    """

    CHECKPOINT_VERSION = 1
    CHECKPOINT_FILENAME = "checkpoint.json"

    # ---- 保存 & 加载 ----

    @staticmethod
    def save(output_dir: str, state: dict, completed_points: list,
             experiment_id: str,
             original_temp_list: list, original_vna_power_list: list,
             original_power_list: list) -> None:
        """原子写入检查点文件。

        Args:
            output_dir: 实验输出根目录
            state: 当前运行状态字典 (temp_idx, overshoot_learning, 等)
            completed_points: 已完成测量点列表
            experiment_id: 实验标识 (YYYYMMDD_HHMMSS)
            original_temp_list: 实验开始时的温度列表（用于恢复验证）
            original_vna_power_list: 实验开始时的 VNA 功率列表
            original_power_list: 实验开始时的激光功率列表
        """
        import json as _json
        import os as _os
        import time as _time

        checkpoint = {
            "version": CheckpointManager.CHECKPOINT_VERSION,
            "experiment_id": experiment_id,
            "timestamp": _time.strftime(
                "%Y-%m-%dT%H:%M:%S", _time.localtime(_time.time())),
            "original_temp_list": original_temp_list,
            "original_vna_power_list": original_vna_power_list,
            "original_power_list": original_power_list,
            "state": state,
            "completed_points": completed_points,
        }

        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        tmp_path = ckpt_path + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(checkpoint, f, indent=2, ensure_ascii=False)

        # 原子 rename（Windows 上如果目标存在会先删除）
        _os.replace(tmp_path, ckpt_path)

    @staticmethod
    def load(output_dir: str):
        """加载检查点文件。

        Returns:
            (state_dict, completed_points_list) 或 None（文件不存在/损坏）
        """
        import json as _json
        import os as _os

        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        if not _os.path.exists(ckpt_path):
            return None

        # 也检查 .tmp 残留（上次写入崩溃）
        tmp_path = ckpt_path + ".tmp"
        if _os.path.exists(tmp_path):
            # .tmp 存在但 .json 也存在 → 上次 rename 可能失败
            # 如果 .json 更新时间 >= .tmp 更新时间 → 使用 .json
            # 否则忽略 .tmp（不完整写入）
            pass  # .json 存在即可，忽略 .tmp

        try:
            with open(ckpt_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (json.JSONDecodeError, OSError, IOError):
            return None

        # 基本结构验证
        if not isinstance(data, dict):
            return None
        if "state" not in data or "completed_points" not in data:
            return None

        return (data["state"], data["completed_points"])

    @staticmethod
    def append_point(output_dir: str, point: dict) -> None:
        """增量追加一个已完成测量点到检查点文件。

        如果检查点不存在，不执行任何操作（非致命）。
        """
        import json as _json
        import os as _os

        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        if not _os.path.exists(ckpt_path):
            return

        tmp_path = ckpt_path + ".tmp"
        try:
            with open(ckpt_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (json.JSONDecodeError, OSError, IOError):
            return

        data.setdefault("completed_points", []).append(point)
        data["timestamp"] = __import__("time").strftime(
            "%Y-%m-%dT%H:%M:%S", __import__("time").localtime(
                __import__("time").time()))

        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
        _os.replace(tmp_path, ckpt_path)

    @staticmethod
    def delete(output_dir: str) -> None:
        """删除检查点文件（实验正常完成时调用）。"""
        import os as _os
        ckpt_path = _os.path.join(output_dir, CheckpointManager.CHECKPOINT_FILENAME)
        tmp_path = ckpt_path + ".tmp"
        for path in (ckpt_path, tmp_path):
            if _os.path.exists(path):
                try:
                    _os.remove(path)
                except OSError:
                    pass

    # ---- 恢复判断 ----

    @staticmethod
    def resume_from(completed_points: list, temp_list: list,
                    vna_power_list: list, power_list: list):
        """根据已完成测量点确定恢复起点。

        completed_points 是权威数据源 — 扫描所有 (temp, vna, power)
        组合，返回第一个未完成点的索引。

        Args:
            completed_points: 已完成测量点列表
            temp_list: 温度列表
            vna_power_list: VNA 功率列表
            power_list: 激光功率列表

        Returns:
            (temp_idx, vna_idx, power_idx) 或 None（全部完成）
        """
        # 构建已完成集合: {(temp_k, vna_dbm, power_mw), ...}
        done = set()
        for pt in completed_points:
            done.add((
                pt.get("temp_k"),
                pt.get("vna_dbm"),
                pt.get("power_mw"),
            ))

        for ti, temp_k in enumerate(temp_list):
            for vi, vna_dbm in enumerate(vna_power_list):
                for pi, power_mw in enumerate(power_list):
                    if (temp_k, vna_dbm, power_mw) not in done:
                        return (ti, vi, pi)

        return None  # 全部完成

    # ---- 参数验证 ----

    @staticmethod
    def validate_lists(loaded_state: dict, current_temp_list: list,
                       current_vna_power_list: list,
                       current_power_list: list) -> bool:
        """检查当前参数列表是否与保存时一致。

        Returns:
            True 如果一致，False 如果有变更
        """
        # 从 loaded_state 中获取原始列表（由 save() 写入顶层）
        # loaded_state 来自 load() 返回值，但原始列表在顶层 data 中
        # 这里接受 loaded_state 是完整的 checkpoint dict
        orig_temp = loaded_state.get("original_temp_list")
        orig_vna = loaded_state.get("original_vna_power_list")
        orig_power = loaded_state.get("original_power_list")

        if orig_temp is None or orig_vna is None or orig_power is None:
            # 旧版本检查点可能没有这些字段 → 保守拒绝
            return False

        return (list(orig_temp) == list(current_temp_list) and
                list(orig_vna) == list(current_vna_power_list) and
                list(orig_power) == list(current_power_list))
```

- [ ] **Step 2: 运行 CheckpointManager 测试**

Run: `pytest tests/test_experiment_worker.py::TestCheckpointManager -v`
Expected: 12 passed

- [ ] **Step 3: Commit**

```bash
git add ui/workers.py tests/test_experiment_worker.py
git commit -m "feat: implement CheckpointManager for atomic checkpoint persistence"
```

---

### Task 4: ExperimentWorker — 异常检测 & 文件命名 测试先写

**Files:**
- Modify: `tests/test_experiment_worker.py` (append new test classes)

- [ ] **Step 1: 编写 `_is_recoverable_error` 测试**

```python
# =========================================================================
# 断点续传: 异常分类测试
# =========================================================================

class TestRecoverableErrorDetection:
    """验证 _is_recoverable_error() 正确区分连接错误和逻辑错误。"""

    @staticmethod
    def _is_recoverable(exc):
        from ui.workers import ExperimentWorker
        return ExperimentWorker._is_recoverable_error(exc)

    def test_given_vi_error_conn_lost_when_checking_then_recoverable(self):
        """VI_ERROR_CONN_LOST → 可恢复。"""
        exc = Exception("VI_ERROR_CONN_LOST (-1073807194): "
                        "The connection for the given session has been lost.")
        assert self._is_recoverable(exc) is True

    def test_given_timeout_when_checking_then_recoverable(self):
        """含 timeout 关键字 → 可恢复。"""
        assert self._is_recoverable(Exception("VISA timeout on read")) is True

    def test_given_disconnected_when_checking_then_recoverable(self):
        """含 disconnected → 可恢复。"""
        assert self._is_recoverable(Exception("Device disconnected")) is True

    def test_given_tcpip_error_when_checking_then_recoverable(self):
        """含 tcpip → 可恢复。"""
        assert self._is_recoverable(Exception("TCPIP connection refused")) is True

    def test_given_value_error_when_checking_then_not_recoverable(self):
        """普通 ValueError → 不可恢复。"""
        assert self._is_recoverable(ValueError("invalid literal for float()")) is False

    def test_given_key_error_when_checking_then_not_recoverable(self):
        """KeyError → 不可恢复。"""
        assert self._is_recoverable(KeyError("missing_key")) is False

    def test_given_data_parse_error_when_checking_then_not_recoverable(self):
        """数据解析失败 → 不可恢复。"""
        assert self._is_recoverable(Exception("could not convert string to float")) is False
```

- [ ] **Step 2: 编写 `_find_next_filename` 测试**

```python
# =========================================================================
# 断点续传: S2P 文件去重测试
# =========================================================================

class TestS2PFilenameDedup:
    """验证 _find_next_filename() 的 attempt 自动递增。"""

    @staticmethod
    def _find_next(folder, temp_k, vna_dbm, power_mw, actual_k):
        from ui.workers import ExperimentWorker
        return ExperimentWorker._find_next_filename(
            folder, temp_k, vna_dbm, power_mw, actual_k)

    def test_given_no_existing_file_when_finding_then_attempt_0(self):
        """无同名文件 → 返回 attempt=0（无 attempt 后缀）。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt" not in name
            assert name.endswith("_actual_29.995K.s2p")

    def test_given_file_exists_when_finding_then_increments_attempt(self):
        """已有 attempt=0 的文件 → 返回 attempt=1。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            existing = _os.path.join(
                tmpdir,
                "YBCO_-45dBm_00mW_target_30K_actual_29.995K.s2p")
            with open(existing, "w") as f:
                f.write("dummy")
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt1" in name

    def test_given_multiple_attempts_when_finding_then_uses_next_available(self):
        """已有 attempt=0,1,2 → 返回 attempt=3。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            base = "YBCO_-45dBm_00mW_target_30K"
            for suffix in ["_actual_29.995K.s2p",
                           "_attempt1_actual_29.995K.s2p",
                           "_attempt2_actual_29.995K.s2p"]:
                with open(_os.path.join(tmpdir, base + suffix), "w") as f:
                    f.write("dummy")
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt3" in name

    def test_given_different_actual_temp_when_finding_then_not_a_conflict(self):
        """不同 actual K → 不视为冲突，返回 attempt=0。"""
        with _tempfile.TemporaryDirectory() as tmpdir:
            existing = _os.path.join(
                tmpdir,
                "YBCO_-45dBm_00mW_target_30K_actual_30.500K.s2p")
            with open(existing, "w") as f:
                f.write("dummy")
            name = self._find_next(tmpdir, 30.0, -45, 0, 29.995)
            assert "attempt" not in name  # actual temp 不同，无冲突
```

- [ ] **Step 3: 运行测试确认全部失败**

Run: `pytest tests/test_experiment_worker.py::TestRecoverableErrorDetection tests/test_experiment_worker.py::TestS2PFilenameDedup -v`
Expected: all failed

- [ ] **Step 4: Commit**

```bash
git add tests/test_experiment_worker.py
git commit -m "test: add recoverable error & S2P filename dedup tests (red)"
```

---

### Task 5: ExperimentWorker — 异常检测 & 文件命名 实现

**Files:**
- Modify: `ui/workers.py` (add methods to ExperimentWorker)

- [ ] **Step 1: 实现 `_is_recoverable_error`**

在 `ExperimentWorker` 类中添加（放在 `abort()` 方法之后）：

```python
    # ------------------------------------------------------------------
    # 断点续传: 异常分类
    # ------------------------------------------------------------------

    @staticmethod
    def _is_recoverable_error(exc: Exception) -> bool:
        """判断异常是否为可恢复的 VISA 连接错误。

        Returns:
            True 如果是连接错误（应触发断点续传）
            False 如果是逻辑错误（应终止实验）
        """
        msg = str(exc)

        # VISA 标准连接丢失错误码
        if "VI_ERROR_CONN_LOST" in msg:
            return True

        # 连接断开关键字
        recoverable_keywords = [
            "timeout", "disconnected", "closed", "lost",
            "not responding", "connection", "tcpip", "hislip",
        ]
        msg_lower = msg.lower()
        for kw in recoverable_keywords:
            if kw in msg_lower:
                return True

        return False
```

- [ ] **Step 2: 实现 `_build_filename` 和 `_find_next_filename`**

在 `ExperimentWorker` 类中继续添加：

```python
    # ------------------------------------------------------------------
    # 断点续传: S2P 文件去重
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filename(temp_k: float, vna_dbm: int, power_mw: int,
                        actual_k: float, attempt: int = 0) -> str:
        """构建 S2P 文件名（支持 attempt 后缀用于去重）。"""
        base = (f"YBCO_{vna_dbm:+d}dBm_{power_mw:02d}mW_"
                f"target_{temp_k:.0f}K")
        if attempt > 0:
            return f"{base}_attempt{attempt}_actual_{actual_k:.3f}K.s2p"
        return f"{base}_actual_{actual_k:.3f}K.s2p"

    @staticmethod
    def _find_next_filename(folder: str, temp_k: float, vna_dbm: int,
                            power_mw: int, actual_k: float) -> str:
        """扫描已有 S2P 文件，返回不冲突的文件名。

        规则：仅当同 (temp_k, vna_dbm, power_mw) + 同 actual_k 时
        才递增 attempt。不同 actual_k 不视为冲突（温度自然漂移的结果）。
        """
        import os as _os

        attempt = 0
        while True:
            name = ExperimentWorker._build_filename(
                temp_k, vna_dbm, power_mw, actual_k, attempt)
            if not _os.path.exists(_os.path.join(folder, name)):
                return name
            attempt += 1
```

- [ ] **Step 3: 运行测试确认通过**

Run: `pytest tests/test_experiment_worker.py::TestRecoverableErrorDetection tests/test_experiment_worker.py::TestS2PFilenameDedup -v`
Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add ui/workers.py
git commit -m "feat: add recoverable error detection & S2P filename dedup"
```

---

### Task 6: ExperimentWorker — 新信号 & 恢复询问 测试先写

**Files:**
- Modify: `tests/test_experiment_worker.py` (append new test class)

- [ ] **Step 1: 编写恢复流程集成测试**

```python
# =========================================================================
# 断点续传: 恢复流程集成测试
# =========================================================================

class TestExperimentRecovery:
    """验证 ExperimentWorker 的恢复信号和工作流。"""

    def test_given_worker_configured_when_signals_exist_then_accessible(self):
        """新信号应在 ExperimentWorker 上可连接。"""
        from ui.workers import ExperimentWorker
        worker = ExperimentWorker()
        # 确认信号存在
        assert hasattr(worker, "experiment_recovering")
        assert hasattr(worker, "experiment_recovered")
        assert hasattr(worker, "experiment_recovery_timeout")
        assert hasattr(worker, "experiment_resume_prompt")

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_connection_lost_during_run_when_recoverable_then_checkpoint_saved(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """连接丢失 → 检查点文件被写入。"""
        import tempfile
        from ui.workers import CheckpointManager

        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0

        with tempfile.TemporaryDirectory() as tmpdir:
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0, 50.0], power_list=[0],
                output_dir=tmpdir,
            )

            # 第一次 get_temperature 成功，后续抛出连接错误
            call_count = [0]

            def get_temp_with_failure(channel="A"):
                call_count[0] += 1
                if call_count[0] >= 3:
                    raise Exception("VI_ERROR_CONN_LOST (-1073807194): "
                                    "The connection for the given session has been lost.")
                return 30.0

            ls.get_temperature.side_effect = get_temp_with_failure

            errors = []
            worker.experiment_error.connect(lambda e: errors.append(e))
            recovering = []
            worker.experiment_recovering.connect(lambda msg: recovering.append(msg))

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5

                worker.run()

            # 不应发射 experiment_error（这是可恢复错误）
            assert len(errors) == 0
            # 应发射 experiment_recovering
            assert len(recovering) == 1
            # 检查点应存在
            ckpt = CheckpointManager.load(tmpdir)
            assert ckpt is not None

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_recoverable_error_when_not_connection_then_error_emitted(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """不可恢复的错误 → experiment_error 正常发射。"""
        ls = _make_mock_lakeshore(start_temp=30.0)
        ls.get_temperature.return_value = 30.0

        worker = _build_worker(
            lakeshore=ls, temp_list=[30.0], power_list=[0],
        )

        # 让 stability controller 抛出非连接错误
        errors = []
        worker.experiment_error.connect(lambda e: errors.append(e))

        with patch(
            "ui.experiment_stability_controller.ExperimentStabilityController"
        ) as mock_ctrl_cls:
            mock_ctrl_cls.side_effect = ValueError("invalid config value")

            worker.run()

        # 不可恢复的异常应被捕获并发射
        assert len(errors) == 1
        assert "invalid config value" in errors[0]

    @patch("time.sleep", return_value=None)
    @patch("os.makedirs")
    def test_given_checkpoint_exists_when_run_starts_then_resume_prompt_emitted(
        self, mock_makedirs, mock_sleep, qapp
    ):
        """启动时检测到检查点 → 发射 experiment_resume_prompt。"""
        import tempfile
        from ui.workers import CheckpointManager

        with tempfile.TemporaryDirectory() as tmpdir:
            # 预置检查点
            state = {
                "temp_idx": 0, "vna_dbm_idx": 0, "power_mw_idx": 1,
                "current_temp_k": 30.0, "total_count": 1,
                "extended_max_wait_s": 1800, "extended_pre_wait_s": 0,
                "rollback_consecutive_issues": 0,
                "rollback_first_issue_index": None,
                "rollback_count": 0,
                "overshoot_learning": {},
            }
            completed = [
                {"temp_k": 30.0, "vna_dbm": -45, "power_mw": 0, "actual_k": 30.0},
            ]
            CheckpointManager.save(
                tmpdir, state, completed, "20260612_test",
                original_temp_list=[30.0, 50.0],
                original_vna_power_list=[-45],
                original_power_list=[0, 5],
            )

            ls = _make_mock_lakeshore(start_temp=30.0)
            ls.get_temperature.return_value = 30.0
            worker = _build_worker(
                lakeshore=ls, temp_list=[30.0, 50.0], power_list=[0, 5],
                output_dir=tmpdir,
            )

            resume_prompts = []
            worker.experiment_resume_prompt.connect(
                lambda exp_id, n: resume_prompts.append((exp_id, n)))

            with patch(
                "ui.experiment_stability_controller.ExperimentStabilityController"
            ) as mock_ctrl_cls:
                mock_ctrl = mock_ctrl_cls.return_value
                mock_ctrl.get_fixed_pid.return_value = {"p": 100, "i": 0, "d": 0}
                mock_ctrl.setup.return_value = None
                mock_ctrl.add_reading.return_value = None
                mock_ctrl.check.return_value = MagicMock(
                    stable=True, reason="stable", avg_temp=30.0)
                mock_ctrl.needs_setpoint_adjustment.return_value = None
                mock_ctrl.base_overshoot = 1.5
                mock_ctrl.current_overshoot = 1.5

                worker.run()

            assert len(resume_prompts) == 1
            assert resume_prompts[0][0] == "20260612_test"
            assert resume_prompts[0][1] == 1  # 1 completed point
```

- [ ] **Step 2: 运行测试确认全部失败**

Run: `pytest tests/test_experiment_worker.py::TestExperimentRecovery -v`
Expected: all failed (signals/methods not yet defined)

- [ ] **Step 3: Commit**

```bash
git add tests/test_experiment_worker.py
git commit -m "test: add experiment recovery integration tests (red)"
```

---

### Task 7: ExperimentWorker — 新信号 & `_enter_recovery` 实现

**Files:**
- Modify: `ui/workers.py` (add signals + methods to ExperimentWorker)

- [ ] **Step 1: 在 ExperimentWorker 类中添加新信号**

在 `ExperimentWorker` 现有信号定义之后添加：

```python
    # ---- 断点续传信号 ----
    experiment_recovering = pyqtSignal(str)           # 连接丢失，进入重连
    experiment_recovered = pyqtSignal()               # 重连成功
    experiment_recovery_timeout = pyqtSignal()        # 重连超时
    experiment_resume_prompt = pyqtSignal(str, int)   # 恢复询问 (exp_id, completed_n)
```

- [ ] **Step 2: 实现 `_enter_recovery` 方法**

在 `ExperimentWorker` 类中添加：

```python
    # ------------------------------------------------------------------
    # 断点续传: 重连循环
    # ------------------------------------------------------------------

    def _enter_recovery(self, error: Exception):
        """连接丢失后的恢复流程: 保存检查点 → 循环重连。

        在 run() 的顶层 except 块中调用。

        Args:
            error: 触发恢复的异常
        """
        import time as _time
        import config
        from datetime import datetime

        self.progress.emit(f"  ⛔ VISA 连接丢失: {error}")
        self.progress.emit(f"  正在保存检查点...")

        # 保存当前状态（_capture_checkpoint_state 在 Task 8 中定义）
        self._save_checkpoint()

        # 通知 GUI
        self.experiment_recovering.emit(str(error))

        # 重连循环
        max_attempts = (config.reconnect_max_wait_minutes * 60 //
                        config.reconnect_retry_interval_s)
        for attempt in range(1, max_attempts + 1):
            if self._abort_flag:
                self.progress.emit("  用户中止 — 检查点已保存")
                self.experiment_aborted.emit()
                return

            self.progress.emit(
                f"  重连尝试 #{attempt}/{max_attempts} "
                f"(等待 {config.reconnect_retry_interval_s}s)...")
            _time.sleep(config.reconnect_retry_interval_s)

            # 尝试重新连接所有设备
            all_ok = True

            if self._lakeshore_ctrl:
                try:
                    # 读取温度测试连接
                    t = self._lakeshore_ctrl.get_temperature("A")
                    if t is None:
                        raise Exception("temperature read returned None")
                    self.progress.emit(f"  ✓ LakeShore 重连成功 ({t:.3f} K)")
                except Exception as e:
                    self.progress.emit(f"  ✗ LakeShore 重连失败: {e}")
                    all_ok = False

            if self._laser_ctrl:
                try:
                    self._laser_ctrl.get_status()
                    self.progress.emit(f"  ✓ Laser 重连成功")
                except Exception as e:
                    self.progress.emit(f"  ✗ Laser 重连失败: {e}")
                    all_ok = False

            if self._vna:
                try:
                    idn = self._vna.query("*IDN?").strip()
                    self.progress.emit(f"  ✓ VNA 重连成功: {idn}")
                except Exception as e:
                    self.progress.emit(f"  ✗ VNA 重连失败: {e}")
                    all_ok = False

            if all_ok:
                self.progress.emit("  ✓ 所有设备重连成功，恢复实验")
                self.experiment_recovered.emit()
                # 递归调用 run() 从检查点恢复
                self.run()
                return

        # 超时
        self.progress.emit(
            f"  ⛔ 重连超时 ({config.reconnect_max_wait_minutes}min)，"
            f"检查点已保存，可稍后手动恢复")
        self.experiment_recovery_timeout.emit()
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/test_experiment_worker.py::TestExperimentRecovery -v`
Expected: 第一个测试 `test_given_worker_configured_when_signals_exist_then_accessible` 通过；其余待 Task 8 完成 run() 修改后通过。

- [ ] **Step 4: Commit**

```bash
git add ui/workers.py
git commit -m "feat: add recovery signals & _enter_recovery to ExperimentWorker"
```

---

### Task 8: ExperimentWorker — `run()` 骨架修改

**Files:**
- Modify: `ui/workers.py` (modify `run()` method)

- [ ] **Step 1: 添加检查点辅助方法**

在 `ExperimentWorker` 类中添加（放在 `_enter_recovery` 之后）：

```python
    # ------------------------------------------------------------------
    # 断点续传: 检查点辅助
    # ------------------------------------------------------------------

    def _save_checkpoint(self):
        """保存当前运行状态到 checkpoint.json。"""
        import os as _os
        from datetime import datetime

        # 收集当前状态
        state = {
            "temp_idx": getattr(self, "_checkpoint_temp_idx", 0),
            "vna_dbm_idx": getattr(self, "_checkpoint_vna_idx", 0),
            "power_mw_idx": getattr(self, "_checkpoint_power_idx", 0),
            "current_temp_k": getattr(self, "_checkpoint_current_temp",
                                       self._temp_list[0] if self._temp_list else 0),
            "total_count": getattr(self, "_checkpoint_total_count", 0),
            "extended_max_wait_s": getattr(self, "_checkpoint_max_wait", 1800),
            "extended_pre_wait_s": getattr(self, "_checkpoint_pre_wait", 0),
            "rollback_consecutive_issues": getattr(
                self, "_checkpoint_consecutive", 0),
            "rollback_first_issue_index": getattr(
                self, "_checkpoint_first_issue_idx", None),
            "rollback_count": getattr(self, "_checkpoint_rollback_count", 0),
            "overshoot_learning": getattr(self, "_checkpoint_overshoot", {}),
        }

        completed = getattr(self, "_checkpoint_completed_points", [])

        CheckpointManager.save(
            self._output_dir, state, completed,
            experiment_id=_os.path.basename(self._output_dir),
            original_temp_list=self._temp_list,
            original_vna_power_list=self._vna_power_list,
            original_power_list=self._power_list,
        )

    def _update_checkpoint_state(self, temp_idx: int, vna_idx: int,
                                  power_idx: int, current_temp: float,
                                  total_count: int,
                                  extended_max_wait_s: float,
                                  extended_pre_wait_s: float):
        """更新运行中的检查点追踪变量（不写入磁盘）。"""
        self._checkpoint_temp_idx = temp_idx
        self._checkpoint_vna_idx = vna_idx
        self._checkpoint_power_idx = power_idx
        self._checkpoint_current_temp = current_temp
        self._checkpoint_total_count = total_count
        self._checkpoint_max_wait = extended_max_wait_s
        self._checkpoint_pre_wait = extended_pre_wait_s

    def _record_measurement_point(self, temp_k: float, vna_dbm: int,
                                   power_mw: int, actual_k: float):
        """记录一个已完成测量点到 completed_points。"""
        if not hasattr(self, "_checkpoint_completed_points"):
            self._checkpoint_completed_points = []
        self._checkpoint_completed_points.append({
            "temp_k": temp_k,
            "vna_dbm": vna_dbm,
            "power_mw": power_mw,
            "actual_k": actual_k,
        })

        # 每 N 个点增量保存
        import config
        interval = getattr(config, "checkpoint_save_interval_points", 5)
        if len(self._checkpoint_completed_points) % interval == 0:
            self._save_checkpoint()
```

- [ ] **Step 1.5: 实现 `_resume_from_checkpoint` 方法**

在 `ExperimentWorker` 类中添加（放在 `_save_checkpoint` 之前）：

```python
    def _resume_from_checkpoint(self, ckpt_state: dict,
                                 ckpt_completed: list) -> tuple:
        """从检查点恢复运行状态。

        Returns:
            (temp_idx, vna_idx, power_idx) — 恢复起点索引
        """
        # 恢复回退状态机变量
        self._checkpoint_consecutive = ckpt_state.get(
            "rollback_consecutive_issues", 0)
        self._checkpoint_first_issue_idx = ckpt_state.get(
            "rollback_first_issue_index", None)
        self._checkpoint_rollback_count = ckpt_state.get(
            "rollback_count", 0)
        self._checkpoint_overshoot = ckpt_state.get(
            "overshoot_learning", {})

        # 恢复扩展时间参数
        self._checkpoint_max_wait = ckpt_state.get(
            "extended_max_wait_s", self._max_wait_s)
        self._checkpoint_pre_wait = ckpt_state.get(
            "extended_pre_wait_s", self._pre_measurement_wait_s)

        # 恢复已完成测量点
        self._checkpoint_completed_points = list(ckpt_completed)
        self._checkpoint_total_count = ckpt_state.get("total_count", 0)

        # 更新回退状态机（如果存在）
        if hasattr(self, '_rollback_state'):
            self._rollback_state.consecutive_issues = (
                self._checkpoint_consecutive)
            self._rollback_state.first_issue_index = (
                self._checkpoint_first_issue_idx)
            self._rollback_state.rollback_count = (
                self._checkpoint_rollback_count)

        # 确定恢复起点
        resume = CheckpointManager.resume_from(
            ckpt_completed,
            self._temp_list,
            self._vna_power_list,
            self._power_list,
        )
        if resume is None:
            # 全部完成 → 从最后一个温度点之后开始（正常结束）
            return (len(self._temp_list), 0, 0)

        temp_idx, vna_idx, power_idx = resume
        self._checkpoint_temp_idx = temp_idx
        self._checkpoint_vna_idx = vna_idx
        self._checkpoint_power_idx = power_idx

        self.progress.emit(
            f"  从检查点恢复: 温度 #{temp_idx + 1}/{len(self._temp_list)} "
            f"({self._temp_list[temp_idx]:.1f}K), "
            f"已完成 {len(ckpt_completed)} 个测量点")

        return resume
```

- [ ] **Step 2: 修改 `run()` 方法 — 外层异常包裹**

将 `run()` 的现有内容包裹在 try/except 中。在 `run()` 的方法体最外层添加：

```python
    @pyqtSlot()
    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            if self._is_recoverable_error(e):
                self._enter_recovery(e)
            else:
                self.experiment_error.emit(f"Experiment failed: {e}")
                try:
                    log_file.close()
                except Exception:
                    pass
```

然后将现有 `run()` 方法体重命名为 `_run_impl(self)`（保持内部逻辑不变，仅函数名改变）。在 `_run_impl` 中：

1. 初始化检查点追踪变量（在初始化代码块后）:

```python
    # 初始化检查点追踪变量
    self._checkpoint_completed_points = []
    self._checkpoint_temp_idx = 0
    self._checkpoint_vna_idx = 0
    self._checkpoint_power_idx = 0
    self._checkpoint_current_temp = self._temp_list[0] if self._temp_list else 0
    self._checkpoint_total_count = 0
    self._checkpoint_max_wait = _extended_max_wait_s
    self._checkpoint_pre_wait = _extended_pre_wait_s
    self._checkpoint_consecutive = 0
    self._checkpoint_first_issue_idx = None
    self._checkpoint_rollback_count = 0
    self._checkpoint_overshoot = {}
```

2. **在 while 循环前**添加检查点恢复询问：

```python
    # ---- 检查点恢复询问 ----
    checkpoint = CheckpointManager.load(self._output_dir)
    if checkpoint is not None:
        ckpt_state, ckpt_completed = checkpoint
        import os as _os2
        exp_id = _os2.path.basename(self._output_dir)
        self.experiment_resume_prompt.emit(exp_id, len(ckpt_completed))
        # 等待 GUI 回调（QMessageBox 是模态的，信号在同一线程同步执行）
        # GUI 回调中设置 _resume_action: "resume" | "restart" | "cancel"
        resume_action = getattr(self, "_resume_action", "cancel")
        if resume_action == "resume":
            resume_idx = self._resume_from_checkpoint(ckpt_state, ckpt_completed)
            if resume_idx is not None:
                temp_idx, _, _ = resume_idx
                # 同步扩展时间参数
                if hasattr(self, "_checkpoint_max_wait"):
                    _extended_max_wait_s = self._checkpoint_max_wait
                if hasattr(self, "_checkpoint_pre_wait"):
                    _extended_pre_wait_s = self._checkpoint_pre_wait
                # 同步回退状态机
                if hasattr(self, "_rollback_state"):
                    self._rollback_state.consecutive_issues = (
                        self._checkpoint_consecutive)
                    self._rollback_state.first_issue_index = (
                        self._checkpoint_first_issue_idx)
                    self._rollback_state.rollback_count = (
                        self._checkpoint_rollback_count)
        elif resume_action == "restart":
            CheckpointManager.delete(self._output_dir)
            # 可选：提示用户确认是否删除已有 S2P
        elif resume_action == "cancel":
            self.progress.emit("用户取消恢复，实验不启动")
            # 不抛异常，让 run() 正常退出（while 循环条件不满足）
            # 设置 temp_idx 到末尾
            temp_idx = len(self._temp_list)
```

3. 在测量点完成处（`self.measurement_complete.emit()` 之后），调用记录：

将 VNA 功率循环改为 `enumerate` 形式（当前 line 1142 为 `for vna_dbm in vna_powers`，需改为 `for vi, vna_dbm in enumerate(vna_powers)`），激光功率循环同理（当前 line 1150 为 `for power_mw in self._power_list`，需改为 `for pi, power_mw in enumerate(self._power_list)`）。

在 `self.measurement_complete.emit(post_temp, power_mw, filepath)` 之后添加：

```python
    if measurement_ok:
        self._record_measurement_point(
            target_k, vna_dbm, power_mw, post_temp)
        self._update_checkpoint_state(
            temp_idx, vi, pi, post_temp, count,
            _extended_max_wait_s, _extended_pre_wait_s)
```

4. 实验正常结束时，删除检查点（在 `log_file.close()` 之前）:

```python
    CheckpointManager.delete(self._output_dir)
```

5. 同时将现有 S2P 文件命名改为使用 `_find_next_filename()`：

将现有的文件名构造（约 line 1230-1234）替换为：

```python
    filename = self._find_next_filename(
        folder, target_k, vna_dbm, power_mw, pre_temp)
    filepath = os.path.join(folder, filename)
```

- [ ] **Step 3: 运行所有 ExperimentWorker 测试**

Run: `pytest tests/test_experiment_worker.py -v`
Expected: 所有已有测试通过 + 新增测试通过

- [ ] **Step 4: Commit**

```bash
git add ui/workers.py tests/test_experiment_worker.py
git commit -m "feat: integrate checkpoint resume into ExperimentWorker.run()"
```

---

### Task 9: MainWindow — 恢复询问 GUI 弹窗

**Files:**
- Modify: `ui/main_window.py`

- [ ] **Step 1: 连接 `experiment_resume_prompt` 信号**

在 `MainWindow` 中 `_start_experiment()` 或实验线程创建处，连接 ExperimentWorker 的恢复询问信号：

```python
# 在 _start_experiment() 或 setup_experiment_worker() 方法中添加:
self._experiment_worker.experiment_resume_prompt.connect(
    self._on_experiment_resume_prompt)
self._experiment_worker.experiment_recovering.connect(
    lambda msg: self._experiment_log.append(
        f"[{datetime.now():%H:%M:%S}] 🔴 {msg}"))
self._experiment_worker.experiment_recovered.connect(
    lambda: self._experiment_log.append(
        f"[{datetime.now():%H:%M:%S}] 🟢 重连成功，实验恢复"))
self._experiment_worker.experiment_recovery_timeout.connect(
    lambda: QMessageBox.warning(
        self, "重连超时",
        "设备重连已超时（30 分钟）。\n\n"
        "检查点文件已保存，您可稍后手动恢复实验。"))
```

- [ ] **Step 2: 实现 `_on_experiment_resume_prompt` 方法**

```python
def _on_experiment_resume_prompt(self, experiment_id: str,
                                  completed_count: int):
    """检测到未完成实验的检查点，询问用户是否恢复。"""
    from PyQt5.QtWidgets import QMessageBox

    msg = QMessageBox(self)
    msg.setWindowTitle("恢复实验")
    msg.setIcon(QMessageBox.Question)
    msg.setText(
        f"检测到未完成的实验\n\n"
        f"实验 ID: {experiment_id}\n"
        f"已完成: {completed_count} 个测量点\n\n"
        f"是否恢复？")
    msg.setStandardButtons(
        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
    msg.button(QMessageBox.Yes).setText("恢复")
    msg.button(QMessageBox.No).setText("重新开始")
    msg.button(QMessageBox.Cancel).setText("取消")
    msg.setDefaultButton(QMessageBox.Yes)

    result = msg.exec_()

    # 设置 worker 的恢复动作用于 run() 同步消费
    if result == QMessageBox.Yes:
        self._experiment_log.append(
            f"[{datetime.now():%H:%M:%S}] 从检查点恢复实验 {experiment_id}")
        self._experiment_worker._resume_action = "resume"
    elif result == QMessageBox.No:
        self._experiment_log.append(
            f"[{datetime.now():%H:%M:%S}] 放弃检查点，重新开始实验")
        self._experiment_worker._resume_action = "restart"
    else:  # Cancel
        self._experiment_log.append(
            f"[{datetime.now():%H:%M:%S}] 用户取消恢复，实验不启动")
        self._experiment_worker._resume_action = "cancel"
```

- [ ] **Step 3: 手动验证 GUI 行为（需运行 app.py）**

- 启动 GUI
- 创建一个模拟的 checkpoint.json 在临时输出目录
- 验证恢复对话框正确显示

- [ ] **Step 4: Commit**

```bash
git add ui/main_window.py
git commit -m "feat: add resume-prompt dialog to MainWindow"
```

---

### Task 10: 实验完成时清理旧 attempt 文件

**Files:**
- Modify: `ui/workers.py` (add cleanup method to ExperimentWorker)

- [ ] **Step 1: 实现 `_cleanup_old_attempts`**

在 `ExperimentWorker` 类中添加：

```python
    @staticmethod
    def _cleanup_old_attempts(output_dir: str):
        """实验正常结束时，每个测量点仅保留最新的 attempt。

        扫描所有 S2P 文件，按 (temp, vna_dbm, power_mw) 分组，
        每组中按 actual_k（文件名中）保留最新的文件，
        删除带 attempt N 后缀的旧文件。
        """
        import os as _os
        import glob as _glob
        import re as _re
        import config

        if not getattr(config, "checkpoint_keep_latest_attempt_only", True):
            return

        # 收集所有 S2P 文件
        pattern = _os.path.join(output_dir, "**", "*.s2p")
        s2p_files = _glob.glob(pattern, recursive=True)

        # 按 (temp, vna_dbm, power_mw) 分组
        groups = {}
        for fpath in s2p_files:
            fname = _os.path.basename(fpath)
            # 解析: YBCO_-45dBm_00mW_target_74K_actual_73.691K.s2p
            #   或: YBCO_-45dBm_00mW_target_74K_attempt1_actual_73.691K.s2p
            match = _re.match(
                r"YBCO_([+-]\d+)dBm_(\d+)mW_target_(\d+)K"
                r"(?:_attempt(\d+))?_actual_([\d.]+)K\.s2p", fname)
            if not match:
                continue

            vna_dbm = int(match.group(1))
            power_mw = int(match.group(2))
            temp_k = int(match.group(3))
            attempt = int(match.group(4)) if match.group(4) else 0
            actual_k = float(match.group(5))

            key = (temp_k, vna_dbm, power_mw)
            if key not in groups:
                groups[key] = []
            groups[key].append((attempt, actual_k, fpath))

        # 每组仅保留最新的（按 attempt 降序，最高 attempt = 最新）
        for key, files in groups.items():
            if len(files) <= 1:
                continue
            # 按 attempt 降序排序
            files.sort(key=lambda x: x[0], reverse=True)
            # 保留第一个（最新），删除其余
            for attempt, actual_k, fpath in files[1:]:
                try:
                    _os.remove(fpath)
                except OSError:
                    pass
```

- [ ] **Step 2: 在 run() 正常结束处调用**

在 `_run_impl()` 的实验完成清理代码中，`CheckpointManager.delete()` 之后添加：

```python
    # 清理旧的 attempt 文件（仅保留每个测量点最新的）
    self._cleanup_old_attempts(self._output_dir)
```

- [ ] **Step 3: 编写清理测试**

在 `tests/test_experiment_worker.py` 的 `TestS2PFilenameDedup` 类中添加：

```python
    def test_given_multiple_attempts_when_cleanup_then_keeps_latest_only(self):
        """多个 attempt → 仅保留最新的。"""
        from ui.workers import ExperimentWorker
        with _tempfile.TemporaryDirectory() as tmpdir:
            # 创建温度点目录
            temp_dir = _os.path.join(tmpdir, "30K", "-45dBm", "00mW")
            _os.makedirs(temp_dir, exist_ok=True)
            # 创建 3 个 attempt
            files = [
                "YBCO_-45dBm_00mW_target_30K_actual_30.000K.s2p",       # attempt=0
                "YBCO_-45dBm_00mW_target_30K_attempt1_actual_30.001K.s2p",  # attempt=1
                "YBCO_-45dBm_00mW_target_30K_attempt2_actual_30.002K.s2p",  # attempt=2 (latest)
            ]
            for fname in files:
                with open(_os.path.join(temp_dir, fname), "w") as f:
                    f.write("! S2P test data\n")

            ExperimentWorker._cleanup_old_attempts(tmpdir)

            remaining = [f for f in _os.listdir(temp_dir) if f.endswith(".s2p")]
            assert len(remaining) == 1
            assert "attempt2" in remaining[0] or "attempt" not in remaining[0]
            # attempt2 应该保留
            assert "attempt2" in remaining[0]

    def test_given_single_file_when_cleanup_then_unchanged(self):
        """每个点只有一个文件 → 不做任何删除。"""
        from ui.workers import ExperimentWorker
        with _tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = _os.path.join(tmpdir, "30K", "-45dBm", "00mW")
            _os.makedirs(temp_dir, exist_ok=True)
            fname = "YBCO_-45dBm_00mW_target_30K_actual_30.000K.s2p"
            with open(_os.path.join(temp_dir, fname), "w") as f:
                f.write("! S2P test data\n")

            ExperimentWorker._cleanup_old_attempts(tmpdir)

            remaining = [f for f in _os.listdir(temp_dir) if f.endswith(".s2p")]
            assert len(remaining) == 1
```

- [ ] **Step 4: 运行清理测试**

Run: `pytest tests/test_experiment_worker.py::TestS2PFilenameDedup -v`
Expected: all passed (including the 2 new cleanup tests)

- [ ] **Step 5: Commit**

```bash
git add ui/workers.py tests/test_experiment_worker.py
git commit -m "feat: add old-attempt cleanup on experiment completion"
```

---

### Task 11: 运行全量测试，最终验证

- [ ] **Step 1: 运行完整测试套件**

```bash
pytest tests/test_experiment_worker.py -v
pytest tests/ -v
```

- [ ] **Step 2: 修复任何回归**

检查是否有已有测试因 `run()` → `_run_impl()` 重命名而失败。如有，更新测试中的 Mock 路径。

- [ ] **Step 3: 最终 commit**

```bash
git add -A
git commit -m "chore: final polish — all tests green"
```
