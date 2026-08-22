# -*- coding: utf-8 -*-
"""
Claude Code 侧实验监控脚本

用法:
  python claude_monitor.py --dir DIR --check      # 状态摘要
  python claude_monitor.py --dir DIR --report     # Markdown 进度表
  python claude_monitor.py --dir DIR --intervene   # 分析并写入 commands.json
  python claude_monitor.py --dir DIR --fill-plan   # 生成 fill_plan.json

由 Claude Code 通过 Bash 工具调用，输出直接显示在终端。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import List, Set

import config

# Windows GBK 控制台兼容：强制 stdout/stderr 使用 utf-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 确保能找到同目录模块
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from experiment_status import ExperimentStatusReader
from fill_planner import scan_s2p_files, compute_missing, generate_fill_plan


# =========================================================================
# 纯逻辑：决策规则
# =========================================================================

def analyze_for_intervention(issues: list, skipped_list: list,
                             existing_actions: set) -> list:
    """分析异常列表，返回应执行的干预命令。

    Args:
        issues: status.json 中的 issues 列表
        skipped_list: status.json 中的 skipped 列表
        existing_actions: commands.json 中已存在的 pending/applied action 名集合

    Returns:
        [{"action": ..., "params": {...}, "reason": "..."}, ...]
    """
    commands = []

    # 规则1：连续2次超时 → 延长 max_wait
    timeout_temps = [
        i["target_k"] for i in issues
        if i["type"] in ("timeout", "timeout_hard_fail")
    ]
    # 按温度点去重计数
    unique_timeout_temps = len(set(timeout_temps))
    if unique_timeout_temps >= 2 and "extend_max_wait" not in existing_actions:
        commands.append({
            "action": "extend_max_wait",
            "params": {"add_minutes": 30},
            "reason": f"连续 {unique_timeout_temps} 个温度点超时，增加等待时间",
        })

    # 规则2：同一温度点熔断 >=2 次 → 放宽熔断阈值
    meltdown_temps = [
        i["target_k"] for i in issues
        if i["type"] == "meltdown"
    ]
    meltdown_counts = Counter(meltdown_temps)
    for temp_k, count in meltdown_counts.items():
        if count >= 2 and "relax_meltdown" not in existing_actions:
            commands.append({
                "action": "relax_meltdown",
                "params": {"new_threshold_k": 0.45, "target_k": temp_k},
                "reason": f"{temp_k}K 已熔断 {count} 次，临时放宽阈值至 0.45K",
            })
            break  # 一条命令即可

    return commands


# =========================================================================
# CLI 子命令
# =========================================================================

def _read_status(output_dir: str) -> dict | None:
    """读取 status.json，失败时打印错误到 stderr。"""
    reader = ExperimentStatusReader(output_dir)
    status = reader.read()
    if status is None:
        print(f"[错误] {output_dir} 中未找到 status.json", file=sys.stderr)
        return None
    return status


def _load_existing_actions(output_dir: str) -> Set[str]:
    """读取 commands.json 中已存在的 action 名。"""
    cmd_path = os.path.join(output_dir, "commands.json")
    if not os.path.exists(cmd_path):
        return set()
    try:
        with open(cmd_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {c["action"] for c in data.get("commands", [])}
    except (json.JSONDecodeError, OSError):
        return set()


def _write_commands(output_dir: str, new_commands: list):
    """将新命令写入 commands.json（追加到已有命令列表）。"""
    cmd_path = os.path.join(output_dir, "commands.json")
    existing = {"commands": [], "last_command_id": ""}

    if os.path.exists(cmd_path):
        try:
            with open(cmd_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    next_id = len(existing["commands"]) + 1
    for cmd in new_commands:
        cmd["id"] = f"cmd_{next_id:03d}"
        cmd["time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        cmd["status"] = "pending"
        existing["commands"].append(cmd)
        next_id += 1

    if existing["commands"]:
        existing["last_command_id"] = existing["commands"][-1]["id"]

    os.makedirs(output_dir, exist_ok=True)
    with open(cmd_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


# =========================================================================
# 自动发现活跃实验
# =========================================================================

_EXP_DIR_RE = re.compile(r"^\d{8}_\d{6}$")

# 默认过期阈值：30 分钟（覆盖测量阶段长时间无 phase 切换的情况）
_DEFAULT_STALE_TIMEOUT_S = 1800


def _discover_active_experiments(base_dir: str,
                                  stale_timeout_s: int = _DEFAULT_STALE_TIMEOUT_S) -> list[str]:
    """扫描 base_dir 下所有实验目录，返回活跃实验的绝对路径列表。

    双重信号判定：
      1. 主信号 status.json → "status": "running" + last_update 未超时
      2. 备用信号 heartbeat.json → stop != true + step_ts 未超时

    超时实验输出 stderr 警告，但不作为活跃实验返回。
    返回按最后更新时间倒序排列的目录路径列表。
    """
    if not os.path.isdir(base_dir):
        return []

    active = []
    stale = []
    now_ts = datetime.now(timezone.utc).timestamp()

    for entry_name in os.listdir(base_dir):
        if not _EXP_DIR_RE.match(entry_name):
            continue
        exp_dir = os.path.join(base_dir, entry_name)
        if not os.path.isdir(exp_dir):
            continue

        # 主信号: status.json
        status_path = os.path.join(exp_dir, "status.json")
        status_active = False
        last_update_ts = 0.0
        if os.path.exists(status_path):
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    status_data = json.load(f)
                if status_data.get("status") == "running":
                    status_active = True
                    lu = status_data.get("last_update", "")
                    if lu:
                        try:
                            dt = datetime.fromisoformat(lu)
                            # 兼容不带时区标记的旧格式（视为 UTC）
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            last_update_ts = dt.timestamp()
                        except (ValueError, OSError):
                            pass
            except (json.JSONDecodeError, OSError):
                pass

        # 备用信号: heartbeat.json
        heartbeat_path = os.path.join(exp_dir, "heartbeat.json")
        heartbeat_active = False
        if os.path.exists(heartbeat_path):
            try:
                with open(heartbeat_path, "r", encoding="utf-8") as f:
                    hb_data = json.load(f)
                if not hb_data.get("stop", False):
                    step_ts = hb_data.get("step_ts", 0)
                    if now_ts - step_ts < stale_timeout_s:
                        heartbeat_active = True
                        if step_ts > last_update_ts:
                            last_update_ts = step_ts
            except (json.JSONDecodeError, OSError):
                pass

        if status_active or heartbeat_active:
            age_s = now_ts - last_update_ts if last_update_ts else 0
            if age_s < stale_timeout_s:
                active.append((exp_dir, last_update_ts))
            else:
                stale.append((exp_dir, age_s))

    # 报告疑似挂死的实验（不阻塞）
    for exp_dir, age_s in stale:
        print(
            f"[警告] {os.path.basename(exp_dir)} 可能已挂死 "
            f"({int(age_s // 60)} 分钟未更新)",
            file=sys.stderr,
        )

    # 按最新活跃优先排序
    active.sort(key=lambda x: x[1], reverse=True)
    return [d for d, _ in active]


def _cmd_check(output_dir: str):
    """--check: 打印状态摘要。"""
    status = _read_status(output_dir)
    if status is None:
        sys.exit(1)

    curr = status.get("current", {})
    phase_labels = {
        "stabilizing_sparse": "稳定中 (sparse)",
        "stabilizing_fine": "稳定中 (fine)",
        "pre_measuring": "预测量等待",
        "measuring": "测量中",
        "meltdown_recovery": "熔断恢复",
    }
    phase_cn = phase_labels.get(curr.get("phase", ""), curr.get("phase", "?"))

    print(f"📡 实验 {status['experiment_id']}")
    print(f"   状态: {status['status']}")
    print(f"   当前: {curr.get('target_k', '?')}K | "
          f"{curr.get('vna_dbm', '?')}dBm | {curr.get('laser_mw', '?')}mW")
    print(f"   阶段: {phase_cn}")
    print(f"   已完成: {len(status.get('completed', []))} 组测量")
    print(f"   异常: {len(status.get('issues', []))} 条")
    print(f"   跳过: {len(status.get('skipped', []))} 条")

    # 若有跳过，列出详情
    skipped = status.get("skipped", [])
    if skipped:
        print(f"   跳过详情:")
        for s in skipped:
            print(f"     - {s['target_k']}K: {s['reason']}")


def _cmd_report(output_dir: str):
    """--report: 打印 Markdown 格式进度表。"""
    status = _read_status(output_dir)
    if status is None:
        sys.exit(1)

    temps = status.get("temperature_plan", [])
    vnas = status.get("vna_power_plan", [])
    completed = status.get("completed", [])
    skipped = status.get("skipped", [])

    # 构建完成映射: {(target_k, vna_dbm): set(power_mw)}
    done_map = {}
    for c in completed:
        key = (c["target_k"], c["vna_dbm"])
        done_map.setdefault(key, set()).add(c["power_mw"])

    # 被完全跳过的温度点
    skipped_temps = {s["target_k"] for s in skipped}

    print(f"## 📊 实验 {status['experiment_id']} 进度报告")
    print()
    print(f"| 温度点 | VNA 功率 | 状态 | 已完成功率数 | 备注 |")
    print(f"|--------|---------|------|-------------|------|")

    for t in temps:
        if t in skipped_temps:
            skip_info = next((s for s in skipped if s["target_k"] == t), {})
            print(f"| **{t}K** | — | ❌ 跳过 | — | {skip_info.get('reason', '')} |")
            continue

        for v in vnas:
            key = (t, v)
            powers_done = done_map.get(key, set())
            expected_count = len(status.get("laser_power_plan", []))

            if len(powers_done) == expected_count and expected_count > 0:
                emoji, label = "✅", "完成"
                note = ""
            elif len(powers_done) > 0:
                emoji, label = "⚠️", "部分"
                note = f"已测 {len(powers_done)}/{expected_count}"
            else:
                emoji, label = "❌", "缺失"
                note = "未开始"

            print(f"| {t}K | {v:+d} dBm | {emoji} {label} | "
                  f"{len(powers_done)}/{expected_count} | {note} |")

    total_expected = len(temps) * len(vnas) * len(status.get("laser_power_plan", []))
    total_done = len(completed)
    print()
    print(f"**总进度: {total_done}/{total_expected} ({total_done * 100 // max(total_expected, 1)}%)**")


def _cmd_intervene(output_dir: str):
    """--intervene: 分析状态，写入 commands.json。"""
    status = _read_status(output_dir)
    if status is None:
        sys.exit(1)

    issues = status.get("issues", [])
    skipped_list = status.get("skipped", [])
    existing_actions = _load_existing_actions(output_dir)

    commands = analyze_for_intervention(issues, skipped_list, existing_actions)

    if not commands:
        print("✅ 无需干预，实验状态正常。")
        return

    _write_commands(output_dir, commands)
    for cmd in commands:
        print(f"⚠️ 已写入命令: {cmd['action']} — {cmd['reason']}")


def _cmd_fill_plan(output_dir: str):
    """--fill-plan: 分析缺失数据，生成补测计划。"""
    status = _read_status(output_dir)
    if status is None:
        sys.exit(1)

    # 从 status 获取期望列表
    temp_plan = status.get("temperature_plan", [])
    vna_plan = status.get("vna_power_plan", [])
    laser_plan = status.get("laser_power_plan", [])

    # 从 status 的 completed 中提取已完成
    completed = status.get("completed", [])
    actual_set = set()
    for c in completed:
        for pw in c.get("powers_mw", []):
            actual_set.add((c["target_k"], c["vna_dbm"], pw))

    # 也扫描文件系统（优先 status，补充文件系统）
    scanned = scan_s2p_files(output_dir)
    for s in scanned:
        actual_set.add((s["target_k"], s["vna_dbm"], s["power_mw"]))

    expected = []
    for t in temp_plan:
        for v in vna_plan:
            for p in laser_plan:
                expected.append((t, v, p))

    missing = compute_missing(expected, list(actual_set))

    if not missing:
        print("✅ 数据完整，无需补测。")
        # 写入 fill_complete.json 避免重复扫描
        fc_path = os.path.join(output_dir, "fill_complete.json")
        try:
            with open(fc_path, "w", encoding="utf-8") as f:
                json.dump({"status": "complete", "missing": 0}, f)
        except OSError:
            pass
        return

    print(f"🔍 缺失 {len(missing)} 个测量点:")

    # 按温度+VNA分组展示
    groups: dict[tuple, list] = {}
    for t, v, p in missing:
        groups.setdefault((t, v), []).append(p)

    for (t, v), powers in sorted(groups.items()):
        print(f"   {t}K  {v:+d} dBm  缺 {len(powers)} 个激光功率: {sorted(powers)}")

    # 生成补测计划
    plan_path = generate_fill_plan(
        missing=missing,
        experiment_id=status["experiment_id"],
        output_dir=output_dir,
        cooldown_offset_k=config.fill_cooldown_offset_k,
    )

    if plan_path:
        print(f"\n📋 补测计划已写入: {plan_path}")
        print(f"   预计耗时: ~{len(missing) * 2} 分钟")
        # 打印补测计划概要
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        print(f"   温度点: {plan['temperature_plan']}")
        print(f"   策略: {plan['strategy']} (冷却至 target-{plan['cooldown_offset_k']}K)")


# =========================================================================
# --auto 模式入口
# =========================================================================

def _discover_completed_unchecked(base_dir: str,
                                   max_age_hours: int = 24) -> list[str]:
    """扫描 base_dir 下已完成但尚未做完整性检查的实验。

    条件：status.json 中 status="completed" 且该目录下无 fill_plan.json。
    仅返回最近 max_age_hours 内完成的实验，避免反复处理历史数据。
    """
    if not os.path.isdir(base_dir):
        return []

    unchecked = []
    now_ts = datetime.now(timezone.utc).timestamp()

    for entry_name in os.listdir(base_dir):
        if not _EXP_DIR_RE.match(entry_name):
            continue
        exp_dir = os.path.join(base_dir, entry_name)
        if not os.path.isdir(exp_dir):
            continue

        # 已有 fill_plan.json 或 fill_complete.json → 跳过
        if os.path.exists(os.path.join(exp_dir, "fill_plan.json")):
            continue
        if os.path.exists(os.path.join(exp_dir, "fill_complete.json")):
            continue

        # 检查 status.json
        status_path = os.path.join(exp_dir, "status.json")
        if not os.path.exists(status_path):
            continue
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if status_data.get("status") != "completed":
            continue

        # 检查完成时间是否在 max_age_hours 内
        lu = status_data.get("last_update", "")
        if lu:
            try:
                dt = datetime.fromisoformat(lu)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_hours = (now_ts - dt.timestamp()) / 3600
                if age_hours > max_age_hours:
                    continue
            except (ValueError, OSError):
                pass

        unchecked.append((exp_dir, lu))

    unchecked.sort(key=lambda x: x[1], reverse=True)
    return [d for d, _ in unchecked]


def _cmd_auto(base_dir: str, action: str = "check",
               stale_timeout_s: int = _DEFAULT_STALE_TIMEOUT_S):
    """扫描 base_dir 寻找活跃实验并执行 action。

    每轮同时检查：
      - 活跃实验（running）→ 状态摘要 + 干预
      - 已完成实验（completed）且无 fill_plan → 自动生成补测计划
    """
    exp_dirs = _discover_active_experiments(base_dir, stale_timeout_s)
    completed_unchecked = _discover_completed_unchecked(base_dir)

    if not exp_dirs and not completed_unchecked:
        # 静默退出：无活跃实验时不输出任何内容，避免干扰正常开发
        return

    # 活跃实验 → 状态 + 干预
    if exp_dirs:
        print(f"-- 发现 {len(exp_dirs)} 个活跃实验 --")
        for exp_dir in exp_dirs:
            exp_name = os.path.basename(exp_dir)
            print(f"\n  [{exp_name}]")
            _cmd_check(exp_dir)
            if action in ("intervene",):
                _cmd_intervene(exp_dir)

    # 已完成但未做完整性检查 → 自动补测分析
    if completed_unchecked:
        print(f"\n-- 发现 {len(completed_unchecked)} 个已完成但未做完整性检查的实验 --")
        for exp_dir in completed_unchecked:
            exp_name = os.path.basename(exp_dir)
            print(f"\n  [{exp_name}] 自动补测分析:")
            _cmd_fill_plan(exp_dir)


# =========================================================================
# CLI 入口
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Code 实验监控脚本",
    )
    parser.add_argument("--dir", type=str, required=False, default=None,
                        help="实验输出目录路径（--auto 模式下可不提供）")
    parser.add_argument("--auto", action="store_true",
                        help="自动发现活跃实验（扫描 experiment_data/）")
    parser.add_argument("--base", type=str, default=None,
                        help="实验数据根目录（默认: config.experiment_data_base_dir）")
    parser.add_argument("--stale-timeout", type=int, default=_DEFAULT_STALE_TIMEOUT_S,
                        help=f"实验视为挂死的超时秒数（默认 {_DEFAULT_STALE_TIMEOUT_S}）")
    parser.add_argument("--check", action="store_true",
                        help="状态摘要")
    parser.add_argument("--report", action="store_true",
                        help="Markdown 进度表")
    parser.add_argument("--intervene", action="store_true",
                        help="分析并写入干预命令")
    parser.add_argument("--fill-plan", action="store_true",
                        dest="fill_plan",
                        help="分析缺失并生成补测计划")

    args = parser.parse_args()

    # --auto 模式：自动发现 + 执行
    if args.auto:
        base = args.base or config.experiment_data_base_dir
        # 确定 action 优先级: intervene > fill_plan > report > check (默认)
        if args.intervene:
            action = "intervene"
        elif args.fill_plan:
            action = "fill_plan"
        elif args.report:
            action = "report"
        else:
            action = "intervene"  # 默认：检查 + 干预 + 完成时补测
        _cmd_auto(base, action, args.stale_timeout)
        return

    # 传统 --dir 模式（向后兼容）
    if not args.dir:
        print("[错误] 需要 --dir 或 --auto 参数", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(args.dir):
        print(f"[错误] 目录不存在: {args.dir}", file=sys.stderr)
        sys.exit(1)

    if args.intervene:
        _cmd_intervene(args.dir)
    elif args.fill_plan:
        _cmd_fill_plan(args.dir)
    elif args.report:
        _cmd_report(args.dir)
    else:
        # 默认行为：等同于 --check
        _cmd_check(args.dir)


if __name__ == "__main__":
    main()
