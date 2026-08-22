---
name: recover
description: 闪退诊断与代码恢复 — 从 Claude Code transcript 分析崩溃原因并恢复丢失的改动
disable-model-invocation: true
---

# /recover — Crash Diagnosis & Code Recovery

分析 Claude Code 闪退原因，并从 transcript JSONL 恢复未落盘的代码改动。

## 流程概览

```
/recover 触发
  → Phase 1: python scripts/crash_recovery.py diagnose → JSON
  → Phase 2: 格式化 5 区块诊断报告（终端输出）
  → 等待用户确认
  → Phase 3: python scripts/crash_recovery.py recover → 写入文件
  → Phase 4: 保存崩溃记录到 memory/crash-*.md
```

## Phase 1: 取证扫描

执行以下命令收集诊断数据：

```bash
python scripts/crash_recovery.py diagnose \
  --debug-log "$HOME/.claude/debug.log" \
  --transcript-dir "$HOME/.claude/projects/D--YBCO-VNAMeas-Auto_Sweep/" \
  --project-root "." \
  --memory-dir "memory/" \
  --output json
```

将 JSON 输出解析为 `DIAG` 变量供后续使用。

## Phase 2: 诊断报告

用以下模板格式化 `DIAG` 输出到终端：

```
## 💥 崩溃诊断
**类型**: {DIAG.crash.type}   **触发**: {DIAG.crash.trigger}
**置信度**: {DIAG.crash.confidence}   **Agent 深度**: {DIAG.crash.query_depth}

## 📋 关键日志
{DIAG.log_lines 逐行输出，前加 > }

## 🕐 崩溃前操作时间线
| 时间 | 操作 | 文件 | 状态 |
|------|------|------|------|
{DIAG.operation_chain 逐行格式化}

## 📋 丢失文件
{DIAG.lost_files 逐文件列出，标注 recovery_type (write/edit_chain)}

## 📊 历史对比
**本次签名**: {DIAG.crash.type}-{DIAG.crash.trigger}-depth{DIAG.crash.query_depth}
{如果 DIAG.historical_match.id: **匹配历史**: {id} 相似度 {similarity}}
{如果无匹配: **新模式** — 之前未见过此类崩溃}

## 🔧 建议
{DIAG.recommendations 逐条列出}
```

### 操作链状态图例

- `committed` — 已 git commit
- `on_disk` — 文件已落盘（工作区有改动但未 commit）
- `transcript_only` — 仅存在于 transcript，文件未落盘（丢失风险）

### 丢失文件判断

通过 git diff HEAD 对比 transcript 中的操作：
1. 如果文件在 `git diff HEAD` 中有改动 → 已落盘，无需恢复
2. 如果文件仅在 transcript 中出现 → 丢失，需要恢复
3. 如果最近一次 transcript 操作是 Write → 完整恢复（直接写入）
4. 如果只有 Edit → 补丁恢复（在当前文件上 apply）

## Phase 3: 恢复执行

输出报告后，**询问用户**：

> "恢复以上文件？输入 `y` 恢复全部，或指定文件: file1.py,file2.py"

用户确认后，执行（自动替换 `<files>` 为用户指定的文件列表）：

```bash
python scripts/crash_recovery.py recover \
  --transcript-dir "$HOME/.claude/projects/D--YBCO-VNAMeas-Auto_Sweep/" \
  --project-root "." \
  --files "<files>"
```

如果用户想先预览，加 `--dry-run` 参数：

```bash
python scripts/crash_recovery.py recover \
  --transcript-dir "$HOME/.claude/projects/D--YBCO-VNAMeas-Auto_Sweep/" \
  --project-root "." \
  --files "<files>" \
  --dry-run
```

恢复后输出摘要：

```
## ✅ 恢复完成
- 已恢复: file1.py, file2.py
- 冲突: file3.py → 查看 file3.py.recover_conflict
- 跳过: (无)

建议立即执行: git add -A && git commit -m "recover: 从 transcript 恢复崩溃前改动"
```

## Phase 4: 存档

将本次崩溃签名保存到 memory 目录。

创建 `memory/crash-{YYYYMMDD-HHMMSS}.md`：

```markdown
---
name: crash-{YYYYMMDD-HHMMSS}
description: Claude Code 闪退记录 — {DIAG.crash.type}/{DIAG.crash.trigger}
metadata:
  type: project
---

# 崩溃记录 {timestamp}

- **类型**: {DIAG.crash.type}
- **触发**: {DIAG.crash.trigger}
- **Agent 深度**: {DIAG.crash.query_depth}
- **置信度**: {DIAG.crash.confidence}
- **丢失文件**: {DIAG.lost_files 列表}
- **恢复状态**: {recovered|partial|skipped}
```

然后更新 `memory/MEMORY.md` 索引，在末尾添加一行：

```markdown
- [崩溃 {YYYYMMDD-HHMMSS}](crash-{YYYYMMDD-HHMMSS}.md) — {DIAG.crash.type}/{DIAG.crash.trigger}
```

## 边界情况

- **无 debug.log**: 跳过日志扫描，仅基于 transcript 分析（diagnose 命令会返回 crash_type=unknown）
- **无 transcript JSONL**: 报告"无数据可恢复"（diagnose 返回空 operation_chain 和 lost_files）
- **无丢失文件**: 报告"所有改动已落盘，无需恢复"
- **diagnose 返回 crash_type=unknown**: 标注低置信度，建议人工查看日志全文
- **edit old_string 不匹配**: `recover` 命令自动处理，生成 .recover_conflict 文件
- **重复执行 /recover**: 幂等 — 已恢复的文件在 diagnose 阶段就会被检测到
- **transcript 跨 session**: 扫描最新 2 个 JSONL，覆盖跨 session 情况
