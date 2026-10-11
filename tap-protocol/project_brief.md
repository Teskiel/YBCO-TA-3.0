# 项目简报（G0）：YBCO-TA-3.0 测量自动化

| 字段 | 内容 |
|---|---|
| 项目 | `<REPO_ROOT>`（YBCO 高温超导 KID 微波测量平台，三模块整合版） |
| 本轮目标 | **只做 G0/G1**：建立任务范围、资产清单与真值规格；跑通**不需要硬件**的验收通道 |
| 交付物 | 本目录下的 brief / inventory / truth_spec / evidence / gate_report |
| 明确排除 | ① 任何连接仪器的动作；② 对 YBCO-TA-3.0 仓库的任何修改；③ 测量数据有效性判定 |
| 权威输入 | 项目自身的 `CLAUDE.md` 链、`machines/machines.json`、`VERSION`、`requirements.txt` |
| 参考性输入 | `docs/multi-machine.md`（18 KB，规范全文）、`machines/history.json` |
| 关键歧义 | **仪器六项**（前置/执行/完成/失败/恢复/数据保护）全部未确认 → 见 `truth_spec.json` 的 `INSTR-001..006` |
| 授权边界 | 本轮 A0–A1（只读 + 协议仓内写入）；A2/A3 一律不动 |
| 多机注意 | 该项目**多台电脑 + GitHub 同步**，改动会外溢到实验机；本轮因此**一个字节都没改它** |
