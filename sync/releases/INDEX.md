# 发布索引（机器 × 版本）

> 由 `python sync/release.py` 自动追加。**不要手工删行**——
> 这张表就是「哪个版本从哪台电脑上传的」的唯一权威记录。
> 机读版本见 [`log.jsonl`](log.jsonl)；按机器分组的提交总表见
> [`../../docs/machine-map.md`](../../docs/machine-map.md)。
>
> **归属置信度**：`high` = 提交自带 `Machine:` trailer，直接可信；
> `中` / `低` = 事后人工登记，依据见 [`../../machines/history.json`](../../machines/history.json)。
> 推定要标成推定，不要把猜测写成事实。

| 版本 | 日期 | 机器 | Tag 指向 | 关键内容 |
|---|---|---|---|---|
| v3.0 | 2026-08-23 | 归属未登记（置信度：无） | `8443181` | 三模块整合版首次导入（Auto_Sweep + Data_process + Noisesweep，211 文件 / 70,799 行）。**追溯项**：无路径或配置证据可指向具体机器。 |
| v3.1 | 2026-08-26 | lab-smlab（实验测量机，置信度：中） | `666c658` | 6-Tab 个人测量 GUI、`orchestrator_noisesweep` 替代 `noisesweep.py`、移除浏览器 Dashboard、无硬件后端、新增 YBCO#1145 热模型。**追溯项**：依据是该提交引入的配置写死 `C:/Users/smlab/...` 与 `C:/Windows/System32/YBCO-TA-3.0/...`。 |
