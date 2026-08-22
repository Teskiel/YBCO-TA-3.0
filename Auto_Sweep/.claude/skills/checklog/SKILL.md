---
name: checklog
description: 实验测量日志快速检索与分析 — 按时间范围筛选日志并诊断问题。当用户输入 /checklog 时触发。
disable-model-invocation: true
---

# /checklog — Experiment Log Inspector

检索并分析 YBCO 超导测量实验日志，快速定位问题。

## 触发后行为

收到 `/checklog` 后，**首先输出以下选项菜单**（英文），等待用户选择：

```
/checklog — Select log scope:
  1. Most recent log
  2. Last 2 hours
  3. Up to today 05:00 AM
  4. Up to yesterday 05:00 AM
  5. All (list summaries, then choose)
  6. Chat about it (no log reading, discuss what to look for)
```

用户选择后执行对应检索逻辑。

## 日志存储路径

两个目录都需扫描：
- `D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\{YYYYMMDD_HHMMSS}/logs/experiment_log_{YYYYMMDD_HHMMSS}.txt`
- `D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\accomplish\{YYYYMMDD_HHMMSS}/logs/experiment_log_{YYYYMMDD_HHMMSS}.txt`

部分实验目录有 `readme.txt`（实验元数据摘要，含设备参数、时长、测量点数）。

## 各选项检索逻辑

### 1. Most recent log
- 用 `ls -lt` 列出两个日志目录下所有 `experiment_log_*.txt`，按修改时间降序
- 取最新的 **1 个**日志文件
- **完整阅读**（Read 整个文件）
- 进入分析阶段

### 2. Last 2 hours
- 用 `find` + `-mmin -120` 找最近 120 分钟内修改的 `experiment_log_*.txt`
- 如有多个，全部阅读；如无结果，提示用户并列出最近的 3 个日志让用户选
- 进入分析阶段

### 3. Up to today 05:00 AM
- 计算今天 05:00 的 Unix 时间戳
- 用 `find -newer` 或比较目录名（`YYYYMMDD_HHMMSS` ≥ `{today}050000`）
- 阅读所有匹配日志
- 进入分析阶段

### 4. Up to yesterday 05:00 AM
- 计算昨天 05:00 到今天 05:00 的时间窗口
- 用目录名过滤（`{yesterday}050000` ≤ dir < `{today}050000`）
- 阅读所有匹配日志
- 进入分析阶段

### 5. All
- 扫描两个目录下所有 `experiment_log_*.txt`
- 按时间倒序排列
- **先输出摘要列表**：每次实验的目录名、起止时间、温度范围、测量点数（从日志前几行和 readme.txt 快速提取）
- 让用户选择具体要分析的实验（可多选或选 "all"）
- 阅读选中的日志，进入分析阶段

### 6. Chat about it
- 不读任何日志文件
- 与用户讨论他们关心什么问题、想查什么类型的异常
- 根据讨论结果建议用选项 1-5 查看哪些日志

## 分析阶段（选项 1-5 共用）

阅读日志后，输出结构化分析报告：

### A. 实验概览

| 项目 | 值 |
|------|-----|
| 实验时间 | 开始 → 结束（如日志中有结束标记） |
| 温度范围 | 最低 → 最高 K |
| 激光功率 | mW 列表 |
| VNA 功率 | dBm 列表 |
| 总测量点数 | N |
| 总耗时 | （如有结束时间） |

### B. 时间线摘要
提取关键事件时间线（最多 15 条）：
- 实验开始、每个温度点开始稳定、稳定完成、测量开始/结束、实验结束
- 异常事件（如有）

### C. 异常检测 — 按以下 checklist 逐项检查

逐项扫描日志，报告发现的问题：

| # | 检查项 | 扫描模式 | 风险 |
|---|--------|----------|------|
| 1 | 温度稳定超时 | 单温度点 `Stabilising` → `稳定` 间隔 > 30 min | ⚠️ 高 |
| 2 | Overshoot 反复调整 | `跳过过冲调整` 对同一目标温度出现 ≥ 5 次 | ⚠️ 中 |
| 3 | 温度长期不平稳 | `trending_stable=False` 持续 > 20 min | 🔴 高 |
| 4 | 内存下降趋势 | `available` 数值持续下降，或触发告警阈值 | ⚠️ 中 |
| 5 | 激光加热效应大 | `Δ=` 绝对值 > 0.05 K（说明激光对温度影响显著） | ℹ️ 信息 |
| 6 | 实验异常中断 | 有开始时间、无结束标记 | 🔴 高 |
| 7 | Phase 2 从未进入 | 有 `Phase 1` 行为但无 `Phase 2` 日志 | ⚠️ 中 |
| 8 | 温度 overshoot 过大 | `actual` 温度超过 `target` 2K 以上且持续 | ⚠️ 中 |
| 9 | 频繁重连 | 出现 `reconnect` / `连接` 相关错误日志 | 🔴 高 |
| 10 | 数据缺失 | 某温度点下测量点数 < 预期（激光功率数 × VNA 功率数） | ⚠️ 中 |

### D. 统计汇总

```
温度点数: N   完成: N   异常/超时: N
总测量点: N   内存峰值使用: X%   最低可用: X MB
平均每温度点耗时: X min   最长温度点: X min (XX K)
```

### E. 建议
基于发现的异常给出 2-5 条具体建议（中文）。

## 边界情况处理

- **无日志匹配**：告知用户，列出可用的最近 5 个实验目录让用户选
- **日志文件为空或极短**（< 5 行）：标注为异常，可能实验启动失败
- **跨天检索**（选项 4）：正确处理 `accomplish` 和主目录两处
- **日志文件巨大**（> 500 行）：先读首尾各 50 行 + 搜索关键模式，不全量读取
- **只有 readme.txt 无 log**：使用 readme.txt 提供基本信息，标注"日志缺失"

## 分析语言

- 菜单和选项提示：英文
- 分析报告和诊断：中文（匹配项目注释语言）
