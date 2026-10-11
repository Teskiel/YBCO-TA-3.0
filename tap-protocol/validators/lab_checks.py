#!/usr/bin/env python3
"""试点 B（ybco_measurement）的验证器：**只用不需要硬件的入口**。

三个检查都对应 YBCO-TA-3.0 自己在 CLAUDE.md 里声明的"离线自检（不需要任何硬件）"：

  pytest-baseline  运行 Auto_Sweep/tests，与**实测基线**比对
                   基线不是文档里的数字，而是本机实测：12 failed / 632 passed / 1 skipped。
                   判据：失败文件必须落在基线文件集合内，且失败数不得超过基线。
  sync-guard       运行 sync/tests（多机同步守护测试，实测 54 项）必须全过
  dryrun           在 Noisesweep 下跑 --dry-run 编排器，必须退出码 0

**不接仪器、不改被测仓库**（dry-run 只往 .gitignore 覆盖的 data/ 下写文件）。

用法：
    python validators/wrappers/lab_checks.py --check pytest-baseline --req REQ-001 --project projects/ybco_measurement
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "validators", "wrappers"))
import cad_checks as cc  # noqa: E402

LAB = os.environ.get("TAP_LAB_ROOT", "<REPO_ROOT>")  # 脱敏
PY = sys.executable

#: 实测基线（2026-10-11 本机测得，非文档数字）
BASELINE_FAILED_FILES = {"test_auto_reconnect.py", "test_stability_fallback.py"}
BASELINE_FAILED_MAX = 12
BASELINE_PASSED_MIN = 632


def run(cmd, cwd, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_pytest_baseline():
    code, out = run([PY, "-m", "pytest", os.path.join(LAB, "Auto_Sweep", "tests"),
                     "-q", "--no-header", "-p", "no:cacheprovider"], cwd=LAB)
    failed = re.findall(r"^FAILED\s+(\S+)", out, re.M)
    files = {os.path.basename(f.split("::")[0]) for f in failed}
    m = re.search(r"(\d+) failed, (\d+) passed(?:, (\d+) skipped)?", out)
    n_fail, n_pass = (int(m.group(1)), int(m.group(2))) if m else (len(failed), -1)
    problems = []
    new_files = files - BASELINE_FAILED_FILES
    if new_files:
        problems.append(f"出现基线之外的失败文件: {sorted(new_files)}")
    if n_fail > BASELINE_FAILED_MAX:
        problems.append(f"失败数 {n_fail} 超过基线 {BASELINE_FAILED_MAX}")
    if n_pass < BASELINE_PASSED_MIN:
        problems.append(f"通过数 {n_pass} 低于基线 {BASELINE_PASSED_MIN}")
    text = ("check=pytest-baseline\n"
            "method=python -m pytest Auto_Sweep/tests（离线，不需要仪器）\n"
            f"baseline: failed<={BASELINE_FAILED_MAX}, passed>={BASELINE_PASSED_MIN}, "
            f"失败文件属于 {sorted(BASELINE_FAILED_FILES)}\n"
            f"measured: failed={n_fail} passed={n_pass} exit={code}\n"
            f"failed_files={sorted(files)}\n"
            f"problems={len(problems)}\n" + "\n".join("  - " + p for p in problems) + "\n"
            )
    # 注意：证据必须**可复现**。早期版本把 pytest 的尾部输出（含耗时）也写进证据，
    # 导致每次重跑摘要都不同、被判"证据过期"。这里只保留稳定字段。
    return (not problems), text, {"failed": n_fail, "passed": n_pass,
                                  "failed_files": sorted(files),
                                  "problems": len(problems)}


def check_sync_guard():
    code, out = run([PY, "-m", "pytest", os.path.join(LAB, "sync", "tests"),
                     "-q", "--no-header", "-p", "no:cacheprovider"], cwd=LAB)
    m = re.search(r"(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) failed)?", out)
    npass = int(m.group(1)) if m else -1
    nfail = int(m.group(3) or 0) if m else -1
    problems = []
    if code != 0:
        problems.append(f"退出码 {code}")
    if nfail not in (0,):
        problems.append(f"有 {nfail} 个失败")
    if npass < 54:
        problems.append(f"通过数 {npass} < 实测基线 54")
    text = ("check=sync-guard\nmethod=python -m pytest sync/tests（多机同步守护测试）\n"
            f"exit={code} passed={npass} failed={nfail}\n"
            f"problems={len(problems)}\n" + "\n".join("  - " + p for p in problems) + "\n")
    return (not problems), text, {"passed": npass, "failed": nfail, "problems": len(problems)}


def check_dryrun():
    cfg = os.path.join(LAB, "Noisesweep", "_dryrun_config.json")
    if not os.path.exists(cfg):
        return False, "check=dryrun\nBLOCKED: 找不到 _dryrun_config.json\n", \
            {"blocked": True}
    code, out = run([PY, "orchestrator_noisesweep.py", "--config", "_dryrun_config.json",
                     "--dry-run", "scan", "4.5"], cwd=os.path.join(LAB, "Noisesweep"))
    problems = [] if code == 0 else [f"退出码 {code}"]
    text = ("check=dryrun\n"
            "method=cd Noisesweep && python orchestrator_noisesweep.py --config "
            "_dryrun_config.json --dry-run scan 4.5（不接仪器）\n"
            f"exit={code}\nproblems={len(problems)}\n"
            + "\n".join("  - " + p for p in problems) + "\n")
    return (not problems), text, {"exit": code, "problems": len(problems)}


CHECKS = dict(**{"pytest-baseline": check_pytest_baseline,
                 "sync-guard": check_sync_guard,
                 "dryrun": check_dryrun})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", required=True, choices=sorted(CHECKS))
    ap.add_argument("--req", required=True)
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    ok, text, meas = CHECKS[a.check]()
    return cc.emit(a.project, a.req, ok, text, meas)


if __name__ == "__main__":
    raise SystemExit(main())
