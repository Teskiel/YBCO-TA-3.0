# -*- coding: utf-8 -*-
"""
人工 f₀ 修正覆盖模块。

谐振子追踪诊断后, 用户肉眼判定修正的 f₀ 值通过 JSON 文件注入,
替换 cache["collected"][T]["identified"] 中的自动识别结果。
后续 collect_dff_for_resonator() 会在修正后的 f₀ 附近搜索, 自然修复追踪。

数据格式 (f0_overrides.json):
    {
      "70": {"R1": 3.5731, "R2": 3.7189, "R5": 4.6644},
      "77": {"R1": 3.3982, "R5": 4.6565}
    }

用法:
    from _f0_overrides import load_overrides, apply_overrides

    overrides = load_overrides("path/to/f0_overrides.json")
    n = apply_overrides(cache, overrides)
    print(f"Applied {n} manual f0 corrections")
"""

import json
import os
from pathlib import Path


def load_overrides(path: str) -> dict:
    """加载 f₀ 覆盖 JSON 文件。

    Args:
        path: JSON 文件路径

    Returns:
        dict: {T_k: {rname: f0_ghz}}, 其中 T_k 为 int, rname 为 str
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"覆盖文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 确保温度键为 int
    overrides = {}
    for key, val in raw.items():
        T_k = int(key)
        overrides[T_k] = {}
        for rname, f0 in val.items():
            if f0 is not None:
                overrides[T_k][rname] = float(f0)

    return overrides


def apply_overrides(cache: dict, overrides: dict) -> int:
    """将 f₀ 覆盖值写入 cache 的 collected 结构中。

    只修改 cache["collected"][T]["identified"][rname]["f0_ghz"],
    不动 identification 阶段的结果 (保留原始记录用于对比)。

    Args:
        cache: 缓存 dict (会被原地修改)
        overrides: {T_k: {rname: f0_ghz}}

    Returns:
        int: 成功覆盖的条目数
    """
    n_applied = 0
    collected = cache.get("collected", {})

    for T_k, r_overrides in overrides.items():
        if T_k not in collected:
            print(f"  [OVERRIDE] T={T_k}K: 不在缓存中, 跳过")
            continue

        c = collected[T_k]
        if c is None:
            print(f"  [OVERRIDE] T={T_k}K: collected 为 None, 跳过")
            continue

        identified = c.get("identified", {})
        for rname, f0_new in r_overrides.items():
            if rname not in identified:
                print(f"  [OVERRIDE] T={T_k}K {rname}: 不在 identified 中, 跳过")
                continue

            f0_old = identified[rname]["f0_ghz"]
            identified[rname]["f0_ghz"] = f0_new
            n_applied += 1
            print(f"  [OVERRIDE] T={T_k}K {rname}: "
                  f"{f0_old:.4f} → {f0_new:.4f} GHz "
                  f"(Δ={f0_new - f0_old:+.1f} MHz)")

    return n_applied


def patch_identification(identification: dict, overrides: dict) -> int:
    """在 collect_and_cache() 流程中应用 f0 覆盖。

    修改 identification[T_K]["resonators"] 列表中每个谐振子的 f0_ghz 字段。
    此函数用于数据收集阶段 (cache 尚未构建 collected 结构时)。

    Args:
        identification: cache["identification"] 或等效的 {T_k: info} dict
        overrides: {T_k: {rname: f0_ghz}}

    Returns:
        int: 成功覆盖的条目数
    """
    n_applied = 0

    for T_k, r_overrides in overrides.items():
        if T_k not in identification:
            print(f"  [OVERRIDE] T={T_k}K: 不在 identification 中, 跳过")
            continue

        info = identification[T_k]
        if info is None or "resonators" not in info:
            print(f"  [OVERRIDE] T={T_k}K: 无谐振子数据, 跳过")
            continue

        # 构建 name -> resonator dict 索引
        r_index = {r["name"]: i for i, r in enumerate(info["resonators"])}

        for rname, f0_new in r_overrides.items():
            if rname not in r_index:
                print(f"  [OVERRIDE] T={T_k}K {rname}: 不在谐振子列表中, 跳过")
                continue

            idx = r_index[rname]
            f0_old = info["resonators"][idx]["f0_ghz"]
            info["resonators"][idx]["f0_ghz"] = f0_new
            n_applied += 1
            print(f"  [OVERRIDE] T={T_k}K {rname}: "
                  f"{f0_old:.4f} -> {f0_new:.4f} GHz "
                  f"(Delta={f0_new - f0_old:+.1f} MHz)")

    return n_applied


def create_template(path: str, cache: dict = None):
    """生成空白的覆盖文件模板, 方便用户填写。

    Args:
        path: 输出 JSON 文件路径
        cache: 可选, 用于预填当前识别的 f₀ 值
    """
    template = {}
    if cache is not None:
        for T_k, c in cache.get("collected", {}).items():
            if c is None:
                continue
            template[str(T_k)] = {}
            for rname, ident in c.get("identified", {}).items():
                template[str(T_k)][rname] = round(ident["f0_ghz"], 4)
    else:
        # 空模板示例
        template = {
            "70": {
                "R1": None,
                "R2": None,
                "R3": None,
                "R4": None,
                "R5": None,
            }
        }

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)
    print(f"覆盖模板已生成: {path}")
    print(f"  将需要修正的 f0 值替换对应的 null, 不需要修正的保持 null 或删除该行")


# ═══════════════════════════════════════════════════════
# 自检
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    import pickle

    print("=== _f0_overrides 自检 ===\n")

    # 测试 load_overrides
    import tempfile
    test_json = os.path.join(tempfile.gettempdir(), "_test_f0_overrides.json")
    test_data = {"70": {"R1": 3.5731, "R2": 3.7189, "R5": 4.6644}}
    with open(test_json, "w", encoding="utf-8") as f:
        json.dump(test_data, f)

    overrides = load_overrides(test_json)
    print(f"1. load_overrides: {overrides}")
    assert overrides[70]["R1"] == 3.5731
    assert overrides[70]["R5"] == 4.6644
    print("   [OK] load_overrides 测试通过")

    # 测试 apply_overrides (需要真实 cache)
    cache_path = Path(
        "D:/YBCO/VNAMeas/Auto_Sweep/experiment_data/~merged/output/_cache/"
        "_cache_20260609-0624__6-80K__full.pkl")
    if cache_path.exists():
        with open(cache_path, "rb") as f:
            cache = pickle.load(f)

        # 保存原始值
        orig_r1 = cache["collected"][70]["identified"]["R1"]["f0_ghz"]
        print(f"\n2. 原始 R1@70K f0: {orig_r1:.4f} GHz")

        n = apply_overrides(cache, overrides)
        new_r1 = cache["collected"][70]["identified"]["R1"]["f0_ghz"]
        print(f"   修正后 R1@70K f0: {new_r1:.4f} GHz")
        print(f"   覆盖条目数: {n}")
        assert new_r1 == 3.5731
        assert n == 3

        # 恢复原始值 (仅测试用, 不保存)
        cache["collected"][70]["identified"]["R1"]["f0_ghz"] = orig_r1
        print("   [OK] apply_overrides 测试通过 (已恢复原始值)")
    else:
        print(f"\n2. 缓存不存在, 跳过 apply_overrides 集成测试")
        print(f"   路径: {cache_path}")

    # 清理
    os.unlink(test_json)

    # 测试 create_template
    template_path = os.path.join(tempfile.gettempdir(), "_test_template.json")
    create_template(template_path)
    with open(template_path, "r") as f:
        t = json.load(f)
    assert "70" in t
    print(f"\n3. create_template: {template_path}")
    print(f"   [OK] 模板包含 T=70K, R1-R5")
    os.unlink(template_path)

    print("\n[OK] _f0_overrides 自检全部通过")
