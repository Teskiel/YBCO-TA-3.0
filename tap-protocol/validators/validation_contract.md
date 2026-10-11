# 校验器契约（`truth_lint.py`）

> 本文件规定校验器**做什么、不做什么、怎么判定**。它是实现与测试的唯一依据。

---

## 1. 能力边界（最重要的一节）

**能做**：协议/规格/运行记录的自洽检查、基线差异比较、摘要与路径核验、验证器存在性与版本核验、**重跑验证器并检查退出码**、计算 `OVERALL`、生成 `gate_report.md`。

**不能做**：证明业务正确性。它可以证明"某验证器存在、版本匹配、有证据、退出码为 0"，但**不能据此断言电容确实靠近馈线**。几何/实验/业务正确性必须由独立验证器提供**可复查证据**（见 `profiles/`）。

因此 `gate_report.md` 必须原样输出这句声明：

> 本报告由 `truth_lint.py evaluate` 产出。它判断协议、规格与运行记录是否自洽，**不证明业务正确性**。

---

## 2. 子命令

```
python validators/truth_lint.py lint     --project <dir> [--baseline]
python validators/truth_lint.py evaluate --project <dir> [--run|--no-run] [--report <path>]
python validators/truth_lint.py freeze   --project <dir> --cr <cr_id>
python validators/truth_lint.py self-test [--tests <dir>]
python validators/truth_lint.py digest   --project <dir> <path>...
```

- `<dir>`：项目目录（其中含 `protocol/`）。
- 退出码：`PASS`→0；`FAIL`/`INCONCLUSIVE`/`NOT_RUN`/`BLOCKED`→2/3/4/5；工具自身错误→1。
  （便于脚本区分"结论非通过"与"校验器崩了"。）

---

## 3. `lint` 检查项

**结构**：必填字段齐全、类型正确、`rule_id`/`req_id` 唯一、`schema` 版本正确。

**语义**：

| 编号 | 规则 |
|---|---|
| `LINT-001` | `WAIVED` 必须有 `waiver_evidence`，且指向 `state = APPROVED` 的 CR |
| `LINT-002` | `mandatory` 验收项的 `derives_from` **不得**引用 `class ∈ {HYPOTHESIS, UNKNOWN}` 的规则作为**已批准设计依据**；但允许引用以**声明阻断关系**（引用时必须同时存在 `UNKNOWN+BLOCKING+OPEN` 的该规则，否则报错） |
| `LINT-003` | `derives_from` 必须解析到真实存在的 `rule_id` |
| `LINT-004` | `FILE:` 路径必须存在且解析后落在 `roots.allow` 之内（越界即失败） |
| `LINT-005` | `EXTERNAL:` 证据不得出现在任何 `PASS` 依据中（该 requirement 最高 `INCONCLUSIVE`） |
| `LINT-006` | 规格文件任何位置**不得**出现 `status` 字段（`resolution_status` 除外） |
| `LINT-007` | **`manual` 允许声明为强制项，但其状态封顶 `INCONCLUSIVE`，永不可能带来 `PASS`**（因此不是 lint 错误；`evaluate` 负责封顶）。这一条很关键：若把 manual 判为 lint 错误，就无法表达"必须人工验证"的强制项（例如交付回读）。 |
| `LINT-008` | `applicability` 只声明条件；`NOT_APPLICABLE` 的结论只能出现在结果侧 |
| `LINT-009` | `contest` 三要素齐全，且 `blind_spot_id` / `blocking_rule_id` 可解析 |
| `LINT-010` | `req_id` 在 `results/` 中不得有孤立的未知项（有结果无声明） |

**基线差异**（存在基线时必查）：

| 编号 | 规则 |
|---|---|
| `BYPASS-001` | 出现 `core/change_control.md` §2 表中的任一差异，且无 CR 的 `authorizes` token 覆盖 → `FAIL`，并列出**全部**未授权差异 |

---

## 4. `evaluate` 判定

1. 先跑 `lint`；`lint` 失败 → 直接按 `FAIL` 出报告（不进入状态计算）。
2. `--run`（默认）：对每个 `verifier.kind = exec` 的 requirement，在项目根为 cwd 下执行 `cmd`，捕获退出码与 stdout/stderr，重算 `validator_sha256`、`input_digests`、`evidence[].sha256`。
3. 逐项比对运行记录与本次重跑结果；不一致 → 该项 `INCONCLUSIVE`。
4. 由原始 `outcome` + `contest` 三条件 + 外部证据规则，得出每项的**最终状态**。
5. 按 `core/gate_and_status.md` §3 的优先级计算 `OVERALL`；**保留全部问题**。
6. 写 `gate_report.md`（字段见 `gate_and_status.md` §4），并返回对应退出码。

**`--no-run`**：不执行验证器，所有 `exec` 项一律 `INCONCLUSIVE`（理由：未重跑），仅用于离线审阅。

---

## 5. `freeze`

生成/更新 `baseline/approved.json`。拒绝条件：

- CR 不存在、`state != APPROVED`；
- CR 的 `authorizes` 未覆盖当前规格与旧基线的**全部**差异；
- `spec_state != APPROVED`。

冻结时记录旧基线摘要到 `supersedes`，并写 `cr_id`。

---

## 6. 运行记录格式

由验证器（`validators/wrappers/*`）写 `protocol/results/<req_id>.json`，字段见 `core/truth_spec_schema.md` §4。
**规格文件本身不得写 `results`**；`results/` 中出现无对应 `req_id` 的文件 → `LINT-010`。

---

## 7. `self-test`

跑 `tests/` 下全部用例，每例断言：期望 `OVERALL`、期望命中的规则编号、以及**报告未掩盖被高优先级遮挡的问题**。任一不符即失败。
