# YBCO-TA-3.0 — ⭐ 权威开发基线

YBCO 高温超导 KID 微波测量平台的**三模块整合版**，取代旧 `YBCO_TA` monorepo。

## 多机协作（先读这段）

本项目在**多台电脑**上开发与测量，代码靠 GitHub 同步。
**开工第一件事**是跑体检，不要凭记忆判断"我是不是最新的"：

```bash
python sync/check.py        # 本机身份 / 落后于 origin 多少 / 漏交 / 漏推 / 风险
```

- 规范全文：[`docs/multi-machine.md`](docs/multi-machine.md)（机器代号、提交 trailer、
  发布流程、配置分层、冲突铁律、数据备份）
- 机器清单：[`machines/machines.json`](machines/machines.json)
- 哪个版本从哪台机器上传：[`sync/releases/INDEX.md`](sync/releases/INDEX.md)
- 按机器分组的提交总表：[`docs/machine-map.md`](docs/machine-map.md)
- 每台机器**首次**需跑一次：`python sync/setup_machine.py --id <机器代号>`

> ⚠️ **不要在本文件里写死 HEAD 与提交数**。历史上这里曾写
> 「HEAD `032d1a0`，工作区干净、已全部 push」，而当时远端其实已经领先 2 个提交——
> 一句过期的断言比没有断言更危险。**提交状态永远以 `git log` / `sync/check.py`
> 为准**，本文件只描述结构与约定。

## 仓库形态

- 远端：`git@github.com:Teskiel/YBCO-TA-3.0.git` @ `master`（PUBLIC）
- 数据仓：`git@github.com:Teskiel/YBCO-TA-data.git`（芯片标定资产，见下）
- **不是** `YBCO_TA` 的分支或子提交——是全新根提交（`8443181`，211 文件 /
  70,799 行一次性导入）。两者**无共同历史**，别指望 `git log` 能连起来。
- 三模块**代码层相互独立**，可单独读懂/运行。

## 三模块

| 模块 | 职责 | 仪器 | 文档 |
|---|---|---|---|
| `Auto_Sweep/` | 变温 × 激光功率 × VNA 功率扫描（PyQt5 GUI + CLI） | LakeShore 335、Keysight PXI VNA、N7779C 激光 | `Auto_Sweep/CLAUDE.md`（分层：`ui/` `draw/` `tests/` 各一份） |
| `Data_process/` | 离线数据处理：谐振选点 → IQ 拟合 → 出图 | 无（纯软件） | `Data_process/CLAUDE.md` |
| `Noisesweep/` | 变温 × 谐振 × 激光功率**噪声**测量编排（6-Tab GUI + CLI） | E8257D 信号源、PXIe-4480 DAQ + 上述温控/激光 | `Noisesweep/CLAUDE.md` |

## 入口命令

```bash
# Auto_Sweep — 测量 GUI（cwd 需为 Auto_Sweep/，它用包内绝对导入）
python Auto_Sweep/app.py

# Data_process — 离线分析（全流程 / 只处理 / 只画图）
python Data_process/pipeline.py --data-dir <原始S2P数据目录>
python Data_process/pipeline.py --data-dir <目录> --phase process|plot
python Data_process/rescalibration.py --data-dir <数据目录>   # 人工校准选点（Tk 三面板）

# Noisesweep — 6-Tab 个人测量 GUI（cwd 需为 Noisesweep/）
python Noisesweep/kid_measurement_gui_personal.py

# Noisesweep — CLI 编排器（子命令式，注意频率是位置参数）
python Noisesweep/orchestrator_noisesweep.py --config Noisesweep/noisesweep_config.json run
python Noisesweep/orchestrator_noisesweep.py --config Noisesweep/noisesweep_config.json temp read

# 离线自检（不需要任何硬件）
cd Noisesweep && python orchestrator_noisesweep.py --config _dryrun_config.json --dry-run scan 4.5
cd Noisesweep && python _verify_worker_dryrun.py   # 另有 3 个 _verify_gui_*.py
python -m pytest Auto_Sweep/tests                  # 645 个测试
python -m pytest sync/tests -q                     # 多机同步守护测试（35 项）
```

> **3.1 起已移除**：`Noisesweep/noisesweep.py`、`noisesweep_dashboard.py`、
> `kid_measurement_gui_v3.py`。分别由 `orchestrator_noisesweep.py` 与
> `kid_measurement_gui_personal.py` 取代。旧命令不要再出现在文档或脚本里。

## 芯片与标定数据

- 本仓库自带一份归档：`Data_process/thermal_models/YBCO#1145__20260609-0624__6-80K__full.json`
- 完整芯片标定资产在**独立数据仓**（代码与数据分离是有意设计：修改频率与原因都不同）：
  ```bash
  git clone https://github.com/Teskiel/YBCO-TA-data.git
  cd YBCO-TA-data && python scripts/deploy_chip_library.py --data-root <本仓库路径>
  ```
- 必记数值（芯片 YBCO#1145，t50nm PLD）：**Tc = 88.6 K**；α ≈ 0.070（每模式）；
  p ≈ 1.61；κ ≈ 0.0005–0.0006 /mW；f(T,P) 联合拟合 rms ≈ 350 ppm（300 点）

## 配置分层（"什么该入库"）

| 内容 | 存哪 |
|---|---|
| 逻辑 / 算法 / 文档 / 测试 | 入库 |
| 机器路径、仪器地址 | `*.machine.json`（**不入库**，`.gitignore` 已排除） |
| 原始数据与产物（`.s2p`/`.h5`/`npz`/`output/`） | 不入库 |
| 私钥 / 口令 / token | 永不入库 |

约定：`foo.json` 是入库基准（对任意机器都成立），`foo.machine.json` 是本机真实值，
`foo.machine.example.json` 是模板。加载顺序
**环境变量 → `*.machine.json` → `foo.json` → 代码内默认值**，
任一层缺失**都不报错**（离线自检必须在全新 clone 上跑通）。

已分层的接缝：`Noisesweep/gui_config.json`、`Auto_Sweep/app_settings.json`、
`Noisesweep/noisesweep_config.json`。

## 陷阱

1. **机器专属绝对路径不许回库。** 历史上 `Noisesweep/gui_config.{json,py}`、
   `启动.bat`、`Auto_Sweep/config.py` 都写死过某台机器的路径（其中含他人用户名
   与一个需要管理员权限的部署位置，而仓库是 PUBLIC）。现在一律走
   `*.machine.json` 或环境变量（`YBCO_EXPERIMENT_DATA_DIR`、
   `YBCO_DRAW_CACHE_ROOT`、`YBCO_S21_HDF5`、`YBCO_PYTHON`）。守护测试会拦住回潮。
2. **`D:\YBCO\VNAMeas\...` 遗留**：`Auto_Sweep/draw/` 下**已废弃**的旧绘图/诊断
   脚本里仍有硬编码，属冻结清单（`sync/tests` 里只许缩短不许增长）。
   活跃绘图脚本走 `Auto_Sweep/draw/_paths.py`。
3. **`Data_process` 没有 `tests/`** —— 旧库有 103 个测试但没搬过来。改 Data_process
   时**不能靠跑测试验证**，要靠 `--phase process` 实跑一份数据。
4. **`Noisesweep` 没有包结构**：所有 `.py` 必须平铺、直接 `import` 模块名，
   **运行时 cwd 必须是该目录**。加 `__init__.py` 或改相对导入会直接坏掉。
5. **`Auto_Sweep` 645 个测试里有 11 个必然失败**：本机没装 NI-VISA（缺
   `visa32.dll`），属环境缺失而非代码 bug。另有约 29 个因
   `draw/plot_VNA_powersweep.py` 的 `from _backend import ...` 失败。
6. **`scraps` 从 wheel 直链安装**（`requirements.txt`）。**不要**改成从本地
   `scarp` 目录装：那份 `pyproject.toml` 的 `build-backend` 是被 setuptools≥80
   移除的旧写法。
7. 三处部署件要保持一致：`requirements.txt` / `install.py` / `setup.bat`；
   版本号唯一来源是根目录 `VERSION` 文件。

## 不要读（省 token）

| 路径 | 原因 |
|---|---|
| `Auto_Sweep/draw/figures/` | 9 个图模块共约 2900 行。要改某张图时**只读那一个文件** |
| `Noisesweep/*.py`（25+ 个） | 除非确实要改噪声测量；`Noisesweep/CLAUDE.md` 已含全部架构要点 |
| `Data_process/output/`、`data_med/` | 数 GB 中间产物与图片 |
| `Auto_Sweep/plot_dashboard/` | 旧的交互式可视化面板，非主线 |

## 旧库参照

需要考古，或要找只存在于旧库的孤本资产（原始 .s2p / Noise 数据 / 论文分析 /
PPT 成品）时，见 `../YBCO_TA/CLAUDE.md`。
