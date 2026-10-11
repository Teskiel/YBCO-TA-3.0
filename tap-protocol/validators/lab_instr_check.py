#!/usr/bin/env python3
"""REQ-004 的验证器：核验"仪器六项确认记录完整"。

**能力边界**：它只证明"六条规则都已由用户确认并落在规格里"，
**不证明任何设备行为**（设备执行正确性与数据有效性不在 G0/G1 范围内）。
"""
from __future__ import annotations
import argparse, json, os, sys
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "validators", "wrappers"))
import cad_checks as cc  # noqa: E402
NEED = ["INSTR-001", "INSTR-002", "INSTR-003", "INSTR-004", "INSTR-005", "INSTR-006"]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--req", required=True); ap.add_argument("--project", required=True)
    a = ap.parse_args()
    spec = json.load(open(os.path.join(REPO, a.project, "protocol", "truth_spec.json"),
                          encoding="utf-8"))
    rules = {r["rule_id"]: r for r in spec["rules"]}
    log = os.path.join(REPO, a.project, "evidence", "instrument_confirmation.md")
    problems = []
    for rid in NEED:
        r = rules.get(rid)
        if r is None:
            problems.append(f"{rid} 不存在"); continue
        if r["class"] != "CONFIRMED":
            problems.append(f"{rid} class={r['class']} != CONFIRMED")
        if r["resolution_status"] != "RESOLVED":
            problems.append(f"{rid} resolution_status={r['resolution_status']} != RESOLVED")
        if "user-confirmation" not in (r.get("source") or ""):
            problems.append(f"{rid} source 未指向用户确认")
    if not os.path.exists(log):
        problems.append("缺少确认记录 evidence/instrument_confirmation.md")
    text = ("check=instr-confirmed\n"
            "method=核验六条 INSTR 规则已 CONFIRMED/RESOLVED 且 source 指向用户确认，"
            "并存在确认记录\n"
            "BOUNDARY=本检查不证明任何设备行为\n"
            f"rules_checked={len(NEED)}\nconfirmation_log={os.path.exists(log)}\n"
            f"problems={len(problems)}\n" + "\n".join("  - " + p for p in problems) + "\n")
    ok = not problems
    return cc.emit(a.project, a.req, ok, text,
                   {"rules_checked": len(NEED), "problems": len(problems)})

if __name__ == "__main__":
    raise SystemExit(main())
