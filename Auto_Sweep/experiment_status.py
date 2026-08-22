# -*- coding: utf-8 -*-
"""
实验状态文件读写 — 结构化 JSON 状态，供 Claude Code 监控

零硬件依赖，仅使用 json + os + config 常量。
"""

import json
import os
from datetime import datetime, timezone


class ExperimentStatusWriter:
    """写入 experiment_data/{timestamp}/status.json。

    所有写方法使用原子写入（.tmp → os.replace），写入失败时静默降级（不抛异常）。
    当 config.status_write_enabled=False 时，所有写操作跳过。
    """

    def __init__(self, output_dir: str):
        self._output_dir = output_dir
        self._path = os.path.join(output_dir, "status.json")

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        """读取当前 status.json，若不存在返回 None。"""
        if not os.path.exists(self._path):
            return None
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: dict) -> bool:
        """原子写入：先写 .tmp 再 os.replace。失败返回 False。"""
        import config
        if not config.status_write_enabled:
            return False
        try:
            os.makedirs(self._output_dir, exist_ok=True)
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def write_initial(self, experiment_id: str,
                      temperature_plan: list,
                      vna_power_plan: list,
                      laser_power_plan: list,
                      runtime_params: dict) -> bool:
        """实验开始时调用，写入完整初始化状态。"""
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "experiment_id": experiment_id,
            "status": "running",
            "start_time": now,
            "last_update": now,
            "temperature_plan": temperature_plan,
            "vna_power_plan": vna_power_plan,
            "laser_power_plan": laser_power_plan,
            "current": {
                "temp_idx": 0,
                "target_k": temperature_plan[0] if temperature_plan else None,
                "actual_k": None,
                "vna_dbm": vna_power_plan[0] if vna_power_plan else None,
                "laser_mw": laser_power_plan[0] if laser_power_plan else None,
                "phase": "starting",
            },
            "completed": [],
            "issues": [],
            "skipped": [],
            "runtime_params": runtime_params,
        }
        return self._save(data)

    def update_current(self, temp_idx: int = None, target_k: float = None,
                       actual_k: float = None, vna_dbm: float = None,
                       laser_mw: float = None, phase: str = None) -> bool:
        """更新 current 字段（温度点切换、phase 变更时调用）。传入 None 的字段保持不变。"""
        data = self._load()
        if data is None:
            return False

        current = data.get("current", {})
        if temp_idx is not None:
            current["temp_idx"] = temp_idx
        if target_k is not None:
            current["target_k"] = target_k
        if actual_k is not None:
            current["actual_k"] = actual_k
        if vna_dbm is not None:
            current["vna_dbm"] = vna_dbm
        if laser_mw is not None:
            current["laser_mw"] = laser_mw
        if phase is not None:
            current["phase"] = phase

        data["current"] = current
        data["last_update"] = datetime.now(timezone.utc).isoformat()
        return self._save(data)

    def add_completed(self, target_k: float, vna_dbm: float,
                      powers_mw: list, status: str = "done") -> bool:
        """追加一条已完成记录。"""
        data = self._load()
        if data is None:
            return False

        data.setdefault("completed", []).append({
            "target_k": target_k,
            "vna_dbm": vna_dbm,
            "powers_mw": powers_mw,
            "status": status,
        })
        data["last_update"] = datetime.now(timezone.utc).isoformat()
        return self._save(data)

    def add_issue(self, target_k: float, issue_type: str,
                  detail: str, restart_count: int = 0) -> bool:
        """追加一条异常事件记录。"""
        data = self._load()
        if data is None:
            return False

        data.setdefault("issues", []).append({
            "time": datetime.now(timezone.utc).isoformat(),
            "target_k": target_k,
            "type": issue_type,
            "detail": detail,
            "restart_count": restart_count,
        })
        data["last_update"] = datetime.now(timezone.utc).isoformat()
        return self._save(data)

    def add_skipped(self, target_k: float, reason: str,
                    vna_power_remaining: list) -> bool:
        """追加一条跳过记录。"""
        data = self._load()
        if data is None:
            return False

        data.setdefault("skipped", []).append({
            "target_k": target_k,
            "reason": reason,
            "vna_power_remaining": vna_power_remaining,
        })
        data["last_update"] = datetime.now(timezone.utc).isoformat()
        return self._save(data)

    def set_status(self, new_status: str) -> bool:
        """修改顶层 status 字段。"""
        data = self._load()
        if data is None:
            return False

        data["status"] = new_status
        data["last_update"] = datetime.now(timezone.utc).isoformat()
        return self._save(data)


class ExperimentStatusReader:
    """读取 status.json。"""

    def __init__(self, output_dir: str):
        self._path = os.path.join(output_dir, "status.json")

    def read(self) -> dict | None:
        """读取并返回 status.json 内容；文件不存在返回 None。"""
        if not os.path.exists(self._path):
            return None
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)
