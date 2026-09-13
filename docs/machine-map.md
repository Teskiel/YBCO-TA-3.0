# 机器活动总表

> 由 `python sync/map_history.py` 生成——**不要手工编辑**，改动会在下次生成时被覆盖。
> 机器清单见 [`machines/machines.json`](../machines/machines.json)；历史归属登记见 [`machines/history.json`](../machines/history.json)。

## 已登记机器

| 代号 | 显示名 | 角色 | 主机名 | 提交数 |
|---|---|---|---|---|
| `pc-teski` | 个人开发机（本机） | dev | DESKTOP-FJA4R3U | 2 |
| `lab-smlab` | 实验测量机（smlab） | lab | — | 2 |
| `pc-merge` | 第二台个人机（待确认） | dev | — | 0 |
| `unknown` | 归属未登记 | — | — | 1 |

## 各机器的提交

### pc-teski（个人开发机（本机））

共 2 个提交。

| 提交 | 日期 | 作者 | 摘要 | 归属依据 |
|---|---|---|---|---|
| `032d1a0` | 2026-08-24 | Teskiel | 芯片标定资产: thermal_models 归档刷新为 YBCO#1145 + README 指向 YBCO-TA-data 数据仓库 | 提交 trailer |
| `c797aa4` | 2026-08-23 | Teskiel | feat: 一键安装脚本；scraps 改为 GitHub Release wheel 直链（scarp 已转公开） | 人工登记（证据中等） |

### lab-smlab（实验测量机（smlab））

共 2 个提交。

| 提交 | 日期 | 作者 | 摘要 | 归属依据 |
|---|---|---|---|---|
| `666c658` | 2026-08-26 | Teskiel | Merge remote-tracking branch 'origin/master' | 人工登记（证据薄弱） |
| `45a6a3c` | 2026-08-26 | Teskiel | YBCO-TA 3.1: 集成个人测量 GUI（6-Tab + 自动化/仪器 Tab），orchestrator_noisesweep 替代 noisesweep.py，移除 dashboard；无硬件后端与配置填充；新增 YBCO#1145 热模型 | 人工登记（证据中等） |

### unknown（归属未登记）

共 1 个提交。

| 提交 | 日期 | 作者 | 摘要 | 归属依据 |
|---|---|---|---|---|
| `8443181` | 2026-08-23 | Teskiel | YBCO-TA 3.0: 整合 Auto_Sweep + Data_process + Noisesweep 三测量模块 | — |

## 归属置信度汇总

| 置信度 | 含义 | 提交数 |
|---|---|---|
| high | 提交自带 Machine: trailer，直接可信 | 1 |
| medium | 人工登记，有间接证据（路径、配置文件、reflog） | 2 |
| low | 人工登记，证据薄弱 | 1 |
| unknown | 无任何证据——需要人工补充 machines/history.json | 1 |

## 怎么补 unknown 的归属

1. 判断该提交是哪台机器产出的（看它引入的文件路径、配置里的主机名、当时的 reflog）
2. 在 `machines/history.json` 的 `commits` 数组里加一条：

```json
{
  "sha": "<完整 40 位 SHA>",
  "machine": "<machines.json 里的代号>",
  "confidence": "high|medium|low",
  "evidence": "判定依据：看到了什么，而不是觉得像什么"
}
```

3. 重跑 `python sync/map_history.py`
