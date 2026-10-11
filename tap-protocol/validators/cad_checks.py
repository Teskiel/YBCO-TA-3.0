#!/usr/bin/env python3
"""cad_checks.py -- YBCO-KID CAD 试点的验证器包装。

设计原则（core/verification_policy.md §2）：**换一条计算路径**。
  - `cavity`  : 本包装自己聚类器件并做路径级点内测试，**不复用** legacy 验收器；
  - `nested`  : 本包装自己数"路径数 > 1 的 HATCH"；
  - `legacy`  : 调用既有 `_jid/verify_v2.py` 并解析其输出——它已知有盲区（BS-001），
                因此它的 FAIL 会被争用降级，绝不允许变成 PASS；
  - `fill`    : 调用既有 `_jid/ref_diff_map.py`（参考件 vs 实际填充的逐点差集）。

每次运行都会写：证据文件 + `results/<req_id>.json` 运行记录（含全部摘要）。
用法：
    python validators/wrappers/cad_checks.py --check fill   --req REQ-002 --project projects/ybco_kid_cad
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAD = r"<CAD_ROOT>"
JID = os.path.join(CAD, "_jid")
PLACED = os.path.join(JID, "out", "HTS_KID_placed.dxf")
REF = os.path.join(JID, "ref")
sys.path.insert(0, os.path.join(REPO, "validators"))
from truth_lint import sha256_file, sha256_text  # noqa: E402

GRID_X0, GRID_Y0, CELL = 17886.9231, 9221.84950121253, 10000.0
FEED_LO, FEED_HI = 2789.0, 6889.0


def load_ref_extract():
    spec = importlib.util.spec_from_file_location(
        "ref_extract", os.path.join(JID, "ref_extract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(cmd, cwd=None, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- checks
def rects_and_hatches(re):
    """返回 (附加闭合矩形, 新增 HATCH, 全部 HATCH)。

    新增 HATCH 以 handle >= 0x20000 判定——与生成器发号范围一致。
    母版自带的 HATCH 本来就是多路径的（25 个方格各含 10000 / 9950 / 地面带 /
    馈线等环），它们不受 HATCH-001 约束；HATCH-001 只约束**新增**填充。
    """
    rects, new_hatches, all_hatches = [], [], []
    for cont, et, items in re.scan(re.read_pairs(PLACED)):
        h = next((v.strip() for c, v in items if c == 5), None)
        try:
            handle = int(h, 16) if h is not None else -1
        except ValueError:
            handle = -1
        if et == "LWPOLYLINE":
            if handle < 0x20000:
                continue
            lp = re.lwpolyline(items)
            if not lp["closed"] or len(lp["pts"]) != 4:
                continue
            xs = sorted({round(p[0], 4) for p in lp["pts"]})
            ys = sorted({round(p[1], 4) for p in lp["pts"]})
            if len(xs) == 2 and len(ys) == 2:
                rects.append((xs[0], ys[0], xs[1], ys[1]))
        elif et == "HATCH":
            paths = [re.path_points(p) for p in (re.hatch_paths(items) or [])]
            paths = [p for p in paths if len(p) >= 3]
            if not paths:
                continue
            all_hatches.append(paths)
            if handle >= 0x20000:
                new_hatches.append(paths)
    return rects, new_hatches, all_hatches


def check_nested():
    re = load_ref_extract()
    _rects, new_hatches, all_hatches = rects_and_hatches(re)
    multi = [i for i, h in enumerate(new_hatches) if len(h) > 1]
    text = (f"check=nested\nmethod=count HATCHes with >1 boundary path, NEW ones only "
            f"(handle >= 0x20000)\n"
            f"new_hatches={len(new_hatches)}\nall_hatches={len(all_hatches)}\n"
            f"nested_new={len(multi)}\nfirst={multi[:5]}\n")
    return (len(multi) == 0), text, {"nested_new": len(multi),
                                     "new_hatches": len(new_hatches),
                                     "all_hatches": len(all_hatches)}


def _side_key(r):
    cx, cy = (r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0
    j = int((cy - GRID_Y0) // CELL)
    feeds = (GRID_Y0 + j * CELL + FEED_LO, GRID_Y0 + j * CELL + FEED_HI)
    feed = min(feeds, key=lambda f: abs(f - cy))
    return (round(feed, 3), "above" if cy >= feed else "below")


def devices_from_bars(rects):
    """由框柱配对确定器件足迹——只用几何本身，不用生成器的任何内部数据。

    为什么不用距离聚类：同侧相邻器件的框柱只相隔约 200-1200 单位，并查集会把
    整侧器件串成一条链（曾把 812 个矩形并成一个跨到 x=65552 的团）。
    器件由其**两根框柱**唯一确定：同侧、等高、间距 = 器件宽 − 柱宽
    （C 组 1300−50=1250；A/B 组 800−120=680）。
    """
    bars = sorted({(round(r[0], 3), round(r[1], 3), round(r[2], 3), round(r[3], 3))
                   for r in rects
                   if (r[3] - r[1]) > 3 * (r[2] - r[0]) and (r[3] - r[1]) > 500})
    byside = collections.defaultdict(list)
    for b in bars:
        byside[_side_key(b)].append(b)
    out = []
    for _k, bs in byside.items():
        for width, sep in ((1300.0, 1250.0), (800.0, 680.0)):
            for L in bs:
                for R in bs:
                    if L is R or abs(L[3] - L[1] - (R[3] - R[1])) > 2 \
                            or abs((R[0] - L[0]) - sep) > 3 or abs(L[1] - R[1]) > 2:
                        continue
                    x_lo, x_hi = L[0], R[2]
                    y_lo, y_hi = min(L[1], R[1]), max(L[3], R[3])
                    grp = [r for r in rects
                           if x_lo - 0.5 <= (r[0] + r[2]) / 2.0 <= x_hi + 0.5
                           and r[1] >= y_lo - 0.5 and r[3] <= y_hi + 0.5]
                    out.append((x_lo, y_lo, x_hi, y_hi, width, grp))
    return out


def check_cavity():
    re = load_ref_extract()
    rects, _new, hatches = rects_and_hatches(re)
    devices = devices_from_bars(rects)
    by_width = collections.Counter(int(d[4]) for d in devices)

    def filled(x, y):
        for polys in hatches:
            xs = [p[0] for q in polys for p in q]
            ys = [p[1] for q in polys for p in q]
            if not (min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys)):
                continue
            odd = 0
            for q in polys:
                n = len(q)
                for i in range(n):
                    ax, ay = q[i]
                    bx, by = q[(i + 1) % n]
                    if (ay > y) != (by > y):
                        xi = ax + (y - ay) * (bx - ax) / (by - ay)
                        if xi > x:
                            odd += 1
            if odd % 2:
                return True
        return False

    pts = bad = 0
    samples = []
    bands = collections.Counter()
    per_dev = []
    N = 24
    for x_lo, y_lo, x_hi, y_hi, _w, grp in devices:
        below = _side_key(grp[0])[1] == "below"
        dev_bad = 0
        for i in range(1, N):
            for j in range(1, N):
                x = x_lo + (x_hi - x_lo) * i / float(N)
                y = y_lo + (y_hi - y_lo) * j / float(N)
                if any(a <= x <= c and b <= y <= d for a, b, c, d in grp):
                    continue
                pts += 1
                if filled(x, y):
                    bad += 1
                    dev_bad += 1
                    ly = (y_hi - y) if below else (y - y_lo)
                    for lim, name in ((160, "0-160 feed band"), (400, "160-400"),
                                      (800, "400-800"), (1400, "800-1400"),
                                      (10 ** 9, "1400+")):
                        if ly < lim:
                            bands[name] += 1
                            break
                    if len(samples) < 5:
                        samples.append((round(x), round(y)))
        if dev_bad:
            per_dev.append((round(x_lo), round(y_lo), dev_bad))
    text = (f"check=cavity\nmethod=device boxes from frame-bar pairing "
            f"(geometry only) + path-level point test\n"
            f"devices={len(devices)}\nby_width={dict(by_width)}\n"
            f"cavity_points={pts}\nfilled_wrong={bad}\n"
            f"devices_affected={len(per_dev)}\n"
            f"bad_by_local_y={dict(bands)}\n"
            f"worst_first5={per_dev[:5]}\nsamples={samples}\n")
    return (bad == 0), text, {"devices": len(devices), "by_width": dict(by_width),
                              "cavity_points": pts, "filled_wrong": bad,
                              "devices_affected": len(per_dev),
                              "bands": dict(bands)}


def check_fill():
    code, out = run([sys.executable, os.path.join(JID, "ref_diff_map.py"), PLACED],
                    cwd=CAD)
    extra = missed = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("extra paint not in the reference"):
            extra = float(s.split(":")[-1].strip().rstrip("%"))
        elif s.startswith("reference coverage missed by the fill"):
            missed = float(s.split(":")[-1].strip().rstrip("%"))
    text = (f"check=fill\nmethod=ref_diff_map.py (points vs reference metal)\n"
            f"extra_percent={extra}\nmissed_percent={missed}\nexit={code}\n"
            f"--- stdout tail ---\n" + "\n".join(out.splitlines()[-12:]) + "\n")
    ok = (extra is not None and extra == 0.0)
    return ok, text, {"extra_percent": extra, "missed_percent": missed}


def check_legacy():
    code, out = run([sys.executable, os.path.join(JID, "verify_v2.py")], cwd=JID)
    cavity = nested = None
    for line in out.splitlines():
        s = line.strip()
        if "cavity sample points -> filled (WRONG):" in s:
            cavity = int(s.split(":")[-1].strip().split()[0])
        elif s.startswith("nested loops: emitted"):
            nested = int(s.split("emitted")[1].strip().split()[0])
    text = (f"check=legacy\nmethod=_jid/verify_v2.py (known blind spot BS-001)\n"
            f"cavity_bad={cavity}\nnested_new={nested}\nexit={code}\n"
            f"--- stdout tail ---\n" + "\n".join(out.splitlines()[-14:]) + "\n")
    ok = (cavity == 0)
    return ok, text, {"cavity_bad": cavity, "nested_new": nested}


CHECKS = dict(nested=check_nested, cavity=check_cavity, fill=check_fill,
              legacy=check_legacy)


# ---------------------------------------------------------------- emit
def emit(project, req_id, ok, text, measurements, counter=None):
    proto = os.path.join(project, "protocol")
    res = os.path.join(proto, "results")
    os.makedirs(res, exist_ok=True)
    ev_path = os.path.join(res, f"{req_id}.evidence.txt")
    with open(ev_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    spec_path = os.path.join(proto, "truth_spec.json")
    spec = json.load(open(spec_path, encoding="utf-8"))
    req = next(q for q in spec["requirements"] if q["req_id"] == req_id)

    def resolve(ref):
        raw = ref[len("FILE:"):]
        return raw if os.path.isabs(raw) else os.path.normpath(os.path.join(REPO, raw))

    digests = {}
    for ref in req.get("input_manifest") or []:
        if ref.startswith("EXTERNAL"):
            continue
        p = resolve(ref)
        if os.path.exists(p):
            digests[ref] = sha256_file(p)
    script = resolve(req["verifier"]["script"])
    rec = {"req_id": req_id,
           "outcome": "PASS" if ok else "FAIL",
           "exit_code": 0 if ok else 1,
           "ran_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
           "validator": req["verifier"]["script"],
           "validator_sha256": sha256_file(script),
           "spec_version": spec["spec_version"],
           "spec_digest": sha256_file(spec_path),
           "input_digests": digests,
           "evidence": [{"path": f"FILE:{os.path.relpath(ev_path, REPO).replace(os.sep, '/')}",
                         "sha256": sha256_file(ev_path)}],
           "measurements": measurements,
           "problems": [] if ok else [text.strip().splitlines()[-1]]}
    # 反证：登记**规格里声明的那条路径**的摘要，而不是另存一份副本，
    # 这样 evaluate 的三条件校验（存在 + 摘要匹配）才成立且可追溯。
    declared = (req.get("contest") or {}).get("counter_evidence") or []
    ce = []
    for ref in declared:
        p = resolve(ref)
        if os.path.exists(p):
            ce.append({"path": ref, "sha256": sha256_file(p)})
    if ce:
        rec["counter_evidence"] = ce
    with open(os.path.join(res, f"{req_id}.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    print(f"{req_id}: outcome={'PASS' if ok else 'FAIL'}  {measurements}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", required=True, choices=sorted(CHECKS))
    ap.add_argument("--req", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--counter-from", help="把另一个 req 的证据作为反证登记")
    a = ap.parse_args()
    ok, text, meas = CHECKS[a.check]()
    counter = None
    if a.counter_from:
        p = os.path.join(a.project, "protocol", "results", f"{a.counter_from}.evidence.txt")
        if os.path.exists(p):
            counter = open(p, encoding="utf-8").read()
    return emit(a.project, a.req, ok, text, meas, counter)


if __name__ == "__main__":
    raise SystemExit(main())
