# -*- coding: utf-8 -*-
"""
实验日志解析与补测计划生成 — 零硬件依赖，纯数据分析

提供：
  - parse_experiment_log()  — 解析文本日志，提取结构化数据
  - scan_s2p_files()        — 扫描目录树中的 .s2p 文件
  - compute_missing()       — 对比期望与实际，找出缺失点
  - generate_fill_plan()    — 生成 fill_plan.json
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


# =========================================================================
# 数据结构
# =========================================================================

@dataclass
class ParsedLog:
    """解析后的日志结构化数据。"""
    temperature_plan: list = field(default_factory=list)
    vna_power_plan: list = field(default_factory=list)
    laser_power_plan: list = field(default_factory=list)
    output_dir: str | None = None
    completed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    total_measurements: int = 0


# =========================================================================
# 日志解析
# =========================================================================

def parse_experiment_log(log_text: str) -> ParsedLog:
    """解析实验文本日志，提取结构化数据。

    返回 ParsedLog，含温度/VNA/激光计划、已完成测量、跳过事件、异常事件。
    所有字段在解析失败时为空列表（不抛异常）。
    """
    result = ParsedLog()
    lines = log_text.split("\n")

    # 当前跟踪状态
    current_target_k: float | None = None

    for line in lines:
        line_stripped = line.strip()

        # --- 头部：温度列表 ---
        m = re.search(r"温度列表:\s*\[([^\]]+)\]", line_stripped)
        if m:
            result.temperature_plan = _parse_float_list(m.group(1))
            continue

        # --- 头部：激光功率列表 ---
        m = re.search(r"激光功率列表:\s*\[([^\]]+)\]\s*mW", line_stripped)
        if m:
            result.laser_power_plan = _parse_float_list(m.group(1))
            continue

        # --- 头部：VNA 功率列表 ---
        m = re.search(r"VNA\s*功率列表:\s*\[([^\]]+)\]\s*dBm", line_stripped)
        if m:
            result.vna_power_plan = _parse_float_list(m.group(1))
            continue

        # --- 头部：输出目录 ---
        m = re.search(r"输出目录:\s*(\S+)", line_stripped)
        if m and result.output_dir is None:
            result.output_dir = m.group(1)
            continue

        # --- 稳定开始：→ Stabilising to X.X K ---
        m = re.search(r"→\s*Stabilis(?:ing|e)\s+to\s+([\d.]+)\s*K", line_stripped)
        if m:
            current_target_k = float(m.group(1))
            continue

        # --- 测量：Measuring X dBm / Y mW @ Z.ZZZ K ---
        # 使用 search（非 match），因为真实日志行有 [timestamp] 前缀
        m = re.search(
            r"Measuring\s+([+-]?\d+)\s*dBm\s*/\s*([\d.]+)\s*mW\s*@\s*([\d.]+)\s*K",
            line_stripped,
        )
        if m:
            vna_dbm = int(m.group(1))
            power_mw = float(m.group(2))
            actual_k = float(m.group(3))
            target_k = current_target_k if current_target_k is not None else actual_k
            result.completed.append({
                "target_k": target_k,
                "vna_dbm": vna_dbm,
                "power_mw": power_mw,
                "actual_k": actual_k,
            })
            continue

        # --- 熔断事件 ---
        m = re.search(r"测量中温度漂移熔断.*?max-min=([\d.]+)K", line_stripped)
        if m:
            max_min = float(m.group(1))
            target_k = current_target_k or 0.0
            result.issues.append({
                "time": "",
                "target_k": target_k,
                "type": "meltdown",
                "detail": f"max-min={max_min:.3f}K",
                "restart_count": 0,
            })
            continue

        # --- 熔断重启计数 ---
        m = re.search(r"测量熔断\s*#(\d+)", line_stripped)
        if m:
            restart_n = int(m.group(1))
            # 更新最近一次熔断事件的 restart_count
            for issue in reversed(result.issues):
                if issue["type"] == "meltdown":
                    issue["restart_count"] = restart_n
                    break
            continue

        # --- 熔断跳过 ---
        m = re.search(r"熔断重启已达上限.*跳过温度点\s*([\d.]+)\s*K", line_stripped)
        if m:
            target_k = float(m.group(1))
            result.skipped.append({
                "target_k": target_k,
                "reason": "meltdown_limit",
                "vna_power_remaining": [],
            })
            continue

        # --- 超时跳过 ---
        m = re.search(r"超时跳过\s*@\s*([\d.]+)\s*K", line_stripped)
        if m:
            target_k = float(m.group(1))
            # 检查下一行是否有硬失败确认
            result.skipped.append({
                "target_k": target_k,
                "reason": "timeout",
                "vna_power_remaining": [],
            })
            continue

        # --- 超时硬失败 ---
        m = re.search(r"跳过温度点\s*([\d.]+)\s*K.*超时硬失败", line_stripped)
        if m:
            target_k = float(m.group(1))
            # 升级最近的 timeout skip 为 hard_fail
            for s in reversed(result.skipped):
                if s["target_k"] == target_k and s["reason"] == "timeout":
                    s["reason"] = "timeout_hard_fail"
                    break
            continue

        # --- 实验完成 ---
        m = re.search(r"Experiment complete.*?(\d+)\s*measurements", line_stripped)
        if m:
            result.total_measurements = int(m.group(1))
            continue

    return result


def _parse_float_list(text: str) -> list:
    """将 "40.0, 50.0, 60.0" 解析为 float 列表。返回 int 若为整数。"""
    items = text.split(",")
    result = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        val = float(item)
        # 整数保持 int 类型（如 mW 中的 0, 1, 3）
        if val == int(val):
            result.append(int(val))
        else:
            result.append(val)
    return result


# =========================================================================
# 文件系统扫描
# =========================================================================

def scan_s2p_files(output_dir: str) -> list[dict]:
    """遍历实验输出目录，解析所有 .s2p 文件名。

    返回 dict 列表: [{target_k, vna_dbm, power_mw, path}, ...]
    文件名格式: YBCO_XdBm_YYmW_target_ZZK_actual_WWWWWK.s2p
    """
    files = []
    if not os.path.isdir(output_dir):
        return files

    for root, _dirs, filenames in os.walk(output_dir):
        for fn in filenames:
            if not fn.endswith(".s2p"):
                continue

            parsed = _parse_s2p_filename(fn)
            if parsed is None:
                continue

            parsed["path"] = os.path.join(root, fn)
            files.append(parsed)

    return files


_S2P_RE = re.compile(
    r"YBCO_([+-]?\d+)dBm_(\d+)mW_target_(\d+)K_actual_([\d.]+)K\.s2p$"
)


def _parse_s2p_filename(filename: str) -> dict | None:
    """从 .s2p 文件名中提取 (vna_dbm, power_mw, target_k, actual_k)。"""
    m = _S2P_RE.search(filename)
    if not m:
        return None
    return {
        "vna_dbm": int(m.group(1)),
        "power_mw": int(m.group(2)),
        "target_k": float(m.group(3)),
    }


# =========================================================================
# 缺失分析
# =========================================================================

def compute_missing(expected: list[tuple], actual: list[tuple]) -> list[tuple]:
    """计算缺失的 (target_k, vna_dbm, power_mw) 三元组。

    expected: [(target_k, vna_dbm, power_mw), ...]
    actual:   [(target_k, vna_dbm, power_mw), ...]
    返回: 在 expected 中但不在 actual 中的三元组列表。
    """
    actual_set = set(actual)
    return [e for e in expected if e not in actual_set]


# =========================================================================
# 补测计划生成
# =========================================================================

def generate_fill_plan(missing: list[tuple],
                       experiment_id: str,
                       output_dir: str = ".",
                       cooldown_offset_k: float = 5.0) -> str | None:
    """根据缺失列表生成 fill_plan.json。

    missing: [(target_k, vna_dbm, power_mw), ...]
    返回 fill_plan.json 的绝对路径，若无缺失则返回 None。
    """
    if not missing:
        return None

    # 按 (target_k, vna_dbm) 分组，收集对应的激光功率
    groups: dict[tuple, list] = {}
    for target_k, vna_dbm, power_mw in missing:
        key = (target_k, vna_dbm)
        groups.setdefault(key, []).append(power_mw)

    # 去重并排序温度
    temperature_plan = sorted(set(target for target, _ in groups.keys()))

    measurements = []
    for (target_k, vna_dbm), powers in sorted(groups.items()):
        measurements.append({
            "target_k": target_k,
            "vna_dbm": vna_dbm,
            "laser_powers_mw": sorted(powers),
        })

    plan = {
        "experiment_id": experiment_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "strategy": "cooldown_then_heat",
        "cooldown_offset_k": cooldown_offset_k,
        "temperature_plan": temperature_plan,
        "measurements": measurements,
    }

    os.makedirs(output_dir, exist_ok=True)
    plan_path = os.path.join(output_dir, "fill_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    return plan_path
