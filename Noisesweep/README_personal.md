# KID 个人测量 GUI（基于 KID_measurement_v3_package 23）

为替代 `C:\Windows\System32\YBCO-TA-3.0\Noisesweep` 里难用的浏览器 dashboard 而做的
**独立桌面 GUI**。界面/功能与 Desktop 的 KID v3 包**保持一致**（4 个原 Tab 原样保留），
新增功能全部放在**两个新选项页**里，与原 V3 包、原 Noisesweep **互不修改、并存**。

## 快速开始

```bat
启动.bat                      （或）  python kid_measurement_gui_personal.py
```

需要 Python 3.14 + PyQt5 / matplotlib / h5py / numpy / scipy / pyvisa / nidaqmx /
lmfit / scraps（与 KID v3 包运行环境一致）。

## 界面说明（6 个 Tab）

| Tab | 内容 |
|---|---|
| S21+噪声一键测量 | 原 V3 功能，零改动 |
| S21扫描测量 | 原 V3 功能，零改动 |
| 噪声采集 | 原 V3 功能，零改动 |
| IQ校准 | 原 V3 功能，零改动 |
| **自动化测量流程** | 新增：自动扫描（单温度）+ 温度/激光/谐振选择/扫描策略/保存 |
| **仪器与连接** | 新增：5 台仪器（E8257D / P5002A / PXIe-4480 / LakeShore335 / 激光）统一状态、连接、手动控制、验证连接 |

顶部「仪器控制」菜单仍保留 V3 的三个对话框（E8257D / P5002A / PXIe-4480）。

## 自动化测量流程（一期 = 自动扫描）

1. **自动化模块**：目前为「自动扫描」；「变温扫描」二期预留（置灰）。
2. **温度区**：
   - 「手动输入目标温度」：输入单值（如 10K）。未连接 LakeShore 时，该值同时作为
     假设温度，用于芯片预测与文件命名。
   - 「读取 LakeShore 实际温度」（需连接）：以实际温度作为测量温度。
   - 「驱动温控到目标温度并等稳定」（需连接，默认关）：勾选后调用 LakeShore
     set_temperature 并等待稳定（容差 0.05 K / 保持 60 s / 最长 1800 s，可在
     gui_config.json 调整）。
   - 连接 LakeShore 时，**实际温度优先**用于①芯片预测谐振位置②`<实际T>K` 文件夹命名。
3. **激光区**：
   - 「激光 sweep」需连接激光才可勾选；功率文本框连接时可填多个（如 `0,1,3` 逗号分隔），
     未连接时仅可填 1 个数（该值仍用于路径命名）。
4. **谐振选择（芯片标定库）**：
   - 标定资产仓库目录默认 `C:\Windows\System32\YBCO-TA-3.0\Data_process`；
   - 下拉选择标定存档（如 `YBCO#1145__20260609-0624__6-80K__full`），显示谐振数；
   - 「预测谐振频率」在指定温度下预览各 res 的 f0 与窗口半宽；
   - 谐振选择：全部 / 自定义（纯数字，逗号分隔，如 `1,3,5`，只测这些 res）。
5. **扫描策略**：宽扫带宽模式（固定/随温度插值/公式）与固定值；精扫带宽表（每行 `T, MHz`）。
   S21/噪声参数（频点数、采样数、稳定时间、功率、IQ 校准文件、I/Q 通道、噪声时长、
   Welch 等）**实时取自 S21/噪声 Tab 的当前控件值**。
6. **保存区**：
   - 保存根目录默认原 V3 包的 `data/S21`；实验名留空则自动生成（如 `77K&0mW`）。
   - 目录结构：
     `<保存根>/<实验名>/<实测T>K/<res>/<功率>mW/{coarse_s21.h5, fine_s21.h5, pic/}`
     （未连接 LakeShore 时实测即手动输入值；连接时按实测温度命名，|目标-实测|>1K 中止）
   - 「保存 V3 面板图」：把自动化运行过程实时画进 V3 的 S21/噪声面板，并保存
     `pic/coarse_s21.png`、`pic/fine_s21.png`、`pic/noise.png`。
   - 「跳过噪声」「强制重跑（忽略 checkpoint）」「空跑（mock，不接触硬件）」。
   - 「保存当前设置为默认」：把本页值写入 gui_config.json，作为下次启动默认。

**执行顺序**（用户约定）：温度(最外) → 激光功率 → 谐振(最内)，即
`4K: res1@0mW → res2@0mW → res1@9mW → res2@9mW …`。

运行中有 checkpoint（`<实验名>/checkpoint.json`），中断后重跑自动跳过已完成点。

## 仪器与连接

- 状态行实时显示 5 台仪器连接状态（绿/红）；E8257D/P5002A/PXIe 用「打开控制窗口/对话框」
  沿用 V3 原面板。
- LakeShore：VISA 地址（留空 = 无硬件，用固定温度后端）、连接/断开、读温度、setpoint、
  加热器 %、「验证连接」。
- 激光：VISA 地址（留空 = 无硬件，操作跳过）、连接/断开、功率/波长/输出 ON-OFF、
  「验证连接」。

## 配置（gui_config.json）

由 `noisesweep_config.json` seed 并修正为绝对路径。关键项：
`save_root`（= 原 V3 `data/S21`）、`data_directory`（= 原 V3 `data`，本程序不复制数据）、
`data_process_dir`（= `YBCO-TA-3.0/Data_process`，芯片标定库+追踪表）、`autosweep_dir`
（= `Auto_Sweep`，温控/激光驱动来源）、`chip_id` / `chip_run_id`（= YBCO#1145）、
`lakeshore_visa_address` / `laser_visa_address`（默认 null，连接时自动保存）、
`wide_bandwidth_mhz` / `fine_bandwidth_mhz_by_temperature` 等。

## 验证 / 验收清单

**A. 无硬件 dry-run**（GUI 勾「空跑」或 `backend:"mock"`）：
- 预期产物 `.../<实验名>/77K/res1/{0,3}mW/{coarse_s21.h5, fine_s21.h5, pic/*.png}`
  （单层温度目录，无实际温度子文件夹）+ `checkpoint.json` 标记 completed；
  日志出现芯片预测 f0、宽/精扫、SCRAPS 拟合；
  V3 的 S21/噪声面板实时出图。
- 回归：同一配置跑原 `noisesweep.py --dry-run run`，对比 HDF5 schema 与 checkpoint 结构一致。

**B. 真实硬件冒烟**：
- 「仪器与连接」5 台状态全绿；LakeShore/激光读值/设点回读正常。
- 4 个原 Tab 行为与 V3 原件逐项对比（一键 S21+噪声、S21、噪声、IQ 校准）。
- 自动化 1 点（77K / 0mW / res1，宽 150 / 精 25 MHz）跑通，V3 面板实时更新、pic 落盘、
  checkpoint 写 fine_center_hz。
- 并发：自动化运行中点 V3「开始S21扫描」应被拒；停止后恢复。

**验收标准**：dry-run 全绿 + B 项通过 + 原 4 Tab 与 V3 完全一致 + 所有新功能都在新 Tab 内。

## 测试脚本（保留）

- `_verify_worker_dryrun.py`：worker 无 GUI 全链路 mock 测试。
- `_verify_gui_smoke.py`：主窗口构造 / 6 Tab / 预测预览 / 连接态联动。
- `_verify_gui_dryrun.py`：通过 GUI 事件循环跑完整自动扫描（含 V3 面板存图）。

## 已知限制

- 一期自动化仅单温度；变温扫描（多温度列表 + 每温度稳定等待）二期。
- SCRAPS 拟合依赖 `scraps` + `lmfit`，缺失时数据照存但无 f0（与原 V3 行为一致）。
- 自动化与 V3 手动测量互斥：一方运行时会禁用另一方启动按钮。
