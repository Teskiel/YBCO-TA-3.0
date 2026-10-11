# Gate 报告（`truth_lint.py evaluate`）

> 本报告由 `truth_lint.py evaluate` 产出。它判断协议、规格与运行记录是否自洽，**不证明业务正确性**。

- 项目：`ybco_measurement`
- 规格版本：`V1` / `sha256:9d69e55d64fae6a1653caab1c2e0479456b8a0cbb6b179af707ff12c1958ed7d`
- 生成时间：2026-10-11T13:15:52+0800

## OVERALL: **PASS**（命中优先级 6）

| req_id | 来源规则 | 强制 | 原始 outcome | 最终状态 | 理由 |
|---|---|---|---|---|---|
| `REQ-001` | BASE-001 | 是 | PASS | **PASS** | 重跑退出码 0，与记录一致 |
| `REQ-002` | BASE-002 | 是 | PASS | **PASS** | 重跑退出码 0，与记录一致 |
| `REQ-003` | BASE-003 | 是 | PASS | **PASS** | 重跑退出码 0，与记录一致 |
| `REQ-004` | INSTR-001, INSTR-002, INSTR-003, INSTR-004, INSTR-005, INSTR-006 | 是 | PASS | **PASS** | 重跑退出码 0，与记录一致 |

## 本次范围（与明确排除项）

**范围内**：G0 任务理解与规格冻结；G1 真值提取与来源核验；不需要硬件的离线自检（pytest / 同步守护 / dry-run 编排器）

**明确排除（本次没有验证）**：
- 任何需要连接仪器的动作（LakeShore 335 / PXI VNA / N7779C 激光 / E8257D / PXIe-4480）
- 对 YBCO-TA-3.0 仓库的任何修改（本试点只读它、只往协议仓写）
- 测量数据的有效性判定（无硬件则无从谈起）

## 已接受但未消除的残余风险（`resolution_status = OPEN`）

| rule_id | 类别 | statement |
|---|---|---|
| `DOC-001` | FACT/NON_BLOCKING | 项目 CLAUDE.md 里有两处与实测不符的过期断言：①『645 个测试里有 11 个必然失败…另有约 29 个因 from _backend import 失败』（实测 12 failed，且未复现那 29 个）；②『sync/test |
| `RISK-001` | FACT/NON_BLOCKING | 第 1 项选定的就绪判据只能证明『VISA 资源可打开』，**不证明量程/工作模式/温度已处于正确状态**；第 3 项的 completed_points 由程序自行维护，与真实采集进度是否一致未经验证。两者都是已接受但未消除的残余风险。 |

## 全部问题清单（含被高优先级掩盖的）

（无）

## 复现命令

```
python validators/truth_lint.py lint     --project projects\ybco_measurement
python validators/truth_lint.py evaluate --project projects\ybco_measurement --run
```
