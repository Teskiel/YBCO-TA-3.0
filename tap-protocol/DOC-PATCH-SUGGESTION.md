# 补丁建议：YBCO-TA-3.0/CLAUDE.md 的两处过期断言

> 按你的选择，我**不改**你的仓库，只给补丁。该仓库多机同步，改动会外溢到实验机。
> 依据：`protocol/results/REQ-001.evidence.txt`、`REQ-002.evidence.txt`（本机实测，2026-10-11，Python 3.12.10）。

---

## 补丁 1：第 64 行的测试项数

**现在**

```
python -m pytest Auto_Sweep/tests                  # 645 个测试
python -m pytest sync/tests -q                     # 多机同步守护测试（35 项）
```

**建议改为**

```
python -m pytest Auto_Sweep/tests                  # 645 个测试（本机 12 失败 / 632 通过 / 1 跳过）
python -m pytest sync/tests -q                     # 多机同步守护测试（54 项）
```

理由：`sync/tests` 实测收集 **54** 项（不是 35）；`Auto_Sweep/tests` 收集确为 645，但运行结果是 12 失败。

---

## 补丁 2：第 113-115 行「陷阱 5」

**现在**

```
5. **`Auto_Sweep` 645 个测试里有 11 个必然失败**：本机没装 NI-VISA（缺
   `visa32.dll`），属环境缺失而非代码 bug。另有约 29 个因
   `draw/plot_VNA_powersweep.py` 的 `from _backend import ...` 失败。
```

**建议改为**

```
5. **`Auto_Sweep` 645 个测试里有 12 个必然失败**（实测 2026-10-11，<this-machine> / Py3.12.10）：
   11 个在 `test_auto_reconnect.py`、1 个在 `test_stability_fallback.py`。
   项目文档**曾**把这 11 个归因于本机缺 NI-VISA（缺 `visa32.dll`），但**该归因未经验证**——
   经核对，实测失败集合与"缺 VISA"并不一一对应，需要仪器环境才能判定根因。
   文档里原先提到的「另有约 29 个因 `from _backend import ...` 失败」**本次未复现**，
   若已修好请一并删掉该句。
```

理由：
- 数字 11 → **12**；
- 原句把根因写成了结论（"属环境缺失而非代码 bug"），而它**没有证据**——按协议这只是 `HYPOTHESIS`，
  不该以断言形式写进权威基线；
- 那 29 个 `_backend` 失败本次没有出现，留着会误导下一个人。

---

## 补丁 3（可选）：把"必然失败"变成可判定基线

与其在文档里写一个数字，不如让失败集合成为**可断言的基线**。本次已实现：

```powershell
python <PROTOCOL_ROOT>\validators\wrappers\lab_checks.py `
  --check pytest-baseline --req REQ-001 --project projects/ybco_measurement
```

判据是「失败文件必须落在基线集合 {`test_auto_reconnect.py`, `test_stability_fallback.py`} 内，
且失败数 ≤ 12、通过数 ≥ 632」。**新增失败=回归**，失败减少=改进，都不需要改文档。

建议在 CLAUDE.md 第 63-64 行的代码块后面加一句：

```
> 测试基线的权威判据见 agent-protocol 的 REQ-001（失败集合 + 数量阈值），
> 不要把数字写进本文件——它已经过期过一次。
```
