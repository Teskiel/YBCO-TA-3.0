# YBCO-TA 3.1

YBCO 高温超导 KID（动力学电感探测器）微波测量平台 —— **三模块整合版**，取代旧 YBCO-TA。

把三台真正用于实际测量的模块整合到单一仓库，可在其它电脑一键下载运行：

### 3.1.1 变更（相对 3.1）— 多机同步与版本溯源

本项目在多台电脑上开发与测量，靠 GitHub 同步。3.1 之前出现过**已实证**的问题：
本机落后远端 2 个提交而不自知、`CLAUDE.md` 两台机器各演化一份却从未入库、
实验机的绝对路径被提交进库（而仓库是 PUBLIC）、`v3.1` 是轻量标签因而完全看不出
是哪台机器发布的。本版把这些各自变成机制：

- **机器代号**：`machines/machines.json` 注册表 + `python sync/setup_machine.py --id <代号>` 一次性认机。
- **提交溯源**：git 钩子自动补齐 `Machine: <代号>` trailer；缺失时 `commit-msg` 拒绝提交。
- **推送门禁**：`pre-push` 阻断「落后于远端 / 有未提交改动 / 有 stash / 缺机器标识」四种情况。
- **体检工具**：`python sync/check.py` 一次回答本机身份、与 origin 的差距、漏交、漏推、风险。
- **发布溯源**：`python sync/release.py` 打**附注标签**（message 含机器归属）并自动追加发布索引
  [`sync/releases/INDEX.md`](sync/releases/INDEX.md)。
- **历史归属**：`machines/history.json` 事后登记 3.0/3.1 的机器归属（带置信度与证据），
  `python sync/map_history.py` 生成 [`docs/machine-map.md`](docs/machine-map.md)。
  **不重写历史**——改写 SHA 会让既有标签失效、其它机器分叉。
- **配置分层**：机器专属路径与仪器地址迁出所有入库文件，改为
  `*.machine.json`（不入库）+ `*.machine.example.json`（模板），
  加载顺序为 环境变量 → 本机覆盖 → 基准 → 代码默认，任一层缺失都不报错。
- **守护测试**：`python -m pytest sync/tests -q`（35 项）阻止绝对路径回潮、
  机器文件误入库、轻量标签、文档漂移。

### 3.1 变更（相对 3.0）

- **个人测量 GUI**：Noisesweep 新增 6-Tab 桌面界面（`kid_measurement_gui_personal.py`），取代旧浏览器 Dashboard 与 PyQt 4-Tab（二者已移除）。
- **CLI 编排器重构**：`noisesweep.py` 重构为 `orchestrator_noisesweep.py`（变温 × 谐振 × 激光三层扫描、固定温度单点、温度失配中止）。
- **无硬件后端**：`backends.py` 新增 `NullLaser` / `FixedTemperature`，温控/激光地址为空时自动降级，离线即可跑通链路。
- **配置填充**：`noisesweep_config.json` 填实实验机参数（E8257D/PXIe 地址、`chip_id=YBCO#1145`、标定库 run_id）。
  > 注：3.1.1 起这些**机器专属**值已迁到 `noisesweep_config.machine.json`（不入库）。
- **上游修复**：HDF5 数据集显式 `dtype="f8"`（schema 稳定）、mock 噪声改偏置点小簇模型、S21 噪声校准均衡偏置频率。

| 模块 | 职责 | 仪器 |
|---|---|---|
| **Auto_Sweep** | 变温 × 激光功率微波扫描测量（GUI） | LakeShore 335 温控、Keysight VNA、N7779C 激光 |
| **Data_process** | 离线数据处理（谐振选点 + 拟合 + 出图） | 无（纯软件） |
| **Noisesweep** | 变温 × 谐振 × 激光功率噪声测量编排（6-Tab 个人 GUI + CLI） | E8257D 信号源、PXIe-4480 DAQ + 上述温控/激光 |

## 目录结构

```
YBCO-TA-3.0/
├── Auto_Sweep/      # 测量 GUI（SMlab201 最新运行版）
├── Data_process/    # 离线处理核心（_lib/ + plugins/ + pipeline.py）
├── Noisesweep/      # 噪声测量编排（6-Tab 个人 GUI + CLI + 无硬件后端）
├── sync/            # 多机同步工具（体检 / 钩子 / 发布 / 历史映射 / 守护测试）
├── machines/        # 机器代号注册表与历史归属登记
├── docs/            # 多机协同规范 + 机器活动总表
├── VERSION          # 版本号唯一来源
├── requirements.txt # 统一依赖
├── install.py       # 安装自检器
└── README.md
```

三模块**代码层相互独立**，可单独读懂/运行；Noisesweep 通过相对路径（兄弟目录）引用 Data_process 的标定库与 Auto_Sweep 的驱动。

**机器专属路径（各台电脑的目录、仪器地址）不进库**，统一走 `*.machine.json`
微分层或环境变量。怎么加一台新电脑、怎么判断自己是不是最新、发布流程是什么——
见 [`docs/multi-machine.md`](docs/multi-machine.md)。

## 多机协作（本项目跑在多台电脑上）

先跑体检，别凭记忆判断：

```bash
python sync/setup_machine.py --id <机器代号>   # 每台机器只需一次：认机 + 装 git 钩子
python sync/check.py                          # 开工/收工体检
```

装好钩子后，提交会自动带 `Machine: <机器代号>` 标识；忘记同步 / 忘记提交 /
忘记标识时，`git push` 会被挡下来并说明原因。

| 想知道 | 看这里 |
|---|---|
| 规范全文（代号、提交、发布、配置分层、冲突铁律、数据备份） | [`docs/multi-machine.md`](docs/multi-machine.md) |
| 有哪些机器 | [`machines/machines.json`](machines/machines.json) |
| 哪个版本从哪台电脑上传的 | [`sync/releases/INDEX.md`](sync/releases/INDEX.md) |
| 哪台电脑改了什么（历史提交总表） | [`docs/machine-map.md`](docs/machine-map.md) |

## 芯片标定数据

芯片级标定资产（chip.json、resposition 谐振记录、f(T,P) 拟合函数）存放在**独立数据仓库** [YBCO-TA-data](https://github.com/Teskiel/YBCO-TA-data)（与代码仓库分离：两者职能、修改频率、修改原因都不同）。下载并放置到本地对应路径后，本仓库的 pipeline / rescalibration.py / Noisesweep 即可直接读取——放置路径与一键部署方法见该库 README：

```bash
git clone https://github.com/Teskiel/YBCO-TA-data.git
cd YBCO-TA-data
python scripts/deploy_chip_library.py --data-root <本仓库或数据根路径>
```

本仓库 `Data_process/thermal_models/` 自带一份已标定芯片（YBCO#1145）的 f(T) 归档，作为开箱即用的基准；原始测量数据（.s2p 等）不入库。

## 一键安装

**推荐（新电脑，全自动）**：登录 GitHub 网页下载本仓库 zip（私有仓库需登录）→ 解压 → 双击根目录 `setup.bat`。脚本自动完成：Git/Python 检测与安装、SSH 密钥生成与 443 端口配置、公钥打印（需手动添加到 GitHub）、依赖安装（scraps 直接下载 wheel）、`install.py` 自检。

手动方式：

```bash
# 1. 克隆
git clone https://github.com/Teskiel/YBCO-TA-3.0.git
cd YBCO-TA-3.0

# 2. 安装依赖
pip install -r requirements.txt

# 3. 自检
python install.py
```

## scraps 依赖

三模块共用的超导谐振拟合库 `scraps` 由公开仓库 `Teskiel/scarp` 提供，`requirements.txt` 通过 wheel 直链安装（GitHub Actions 自动构建发布，无需任何认证）。升级方式见 README 顶部版本说明。

## 硬件前置条件（真实测量才需要）

- **pyvisa**：需本机安装 NI-VISA 或 Keysight IO Libraries（提供 `visa32.dll`，pip 本身不包含该 DLL）。
- **nidaqmx**：需本机安装 NI-DAQmx 运行时驱动（PXIe-4480 采集卡）。

纯软件 / 离线验证不需要上述硬件驱动。

## 启动

### Auto_Sweep（测量 GUI）
```bash
python Auto_Sweep/app.py
```

### Data_process（离线处理）
```bash
python Data_process/pipeline.py --data-dir <原始S2P数据目录>
python Data_process/rescalibration.py --data-dir <数据目录>    # 人工校准选点
```

### Noisesweep（变温噪声测量）

**个人测量 GUI（6 Tab：测量 / S21 / 噪声 / IQ 校准 / 自动化测量流程 / 仪器与连接）**
```bash
python Noisesweep/kid_measurement_gui_personal.py
```

**CLI 编排器（原 noisesweep.py 重构版）**
```bash
python Noisesweep/orchestrator_noisesweep.py --config Noisesweep/noisesweep_config.json run
python Noisesweep/orchestrator_noisesweep.py --config Noisesweep/noisesweep_config.json temp read
```

> 3.1 起旧浏览器 Dashboard（`noisesweep_dashboard.py`）、旧 PyQt 4-Tab（`kid_measurement_gui_v3.py`）与旧 CLI（`noisesweep.py`）均已移除，由个人 GUI / orchestrator 取代。

## 离线验证（不接硬件）

```bash
# Noisesweep 全链路 mock 空跑
python Noisesweep/orchestrator_noisesweep.py --config Noisesweep/_dryrun_config.json --dry-run scan --freqs 4.5

# 新 GUI + 自动化引擎 mock 验证（无硬件，均在 Noisesweep/ 目录下运行）
cd Noisesweep
python _verify_worker_dryrun.py   # worker 无 GUI 全链路（含噪声质量断言）
python _verify_gui_smoke.py       # GUI 冒烟
python _verify_gui_dryrun.py      # GUI 事件循环全链路（产物校验）
python _verify_gui_persistence.py # 设置持久化
```

## 说明

- 测量产生的原始数据（`.s2p` / `.h5` / `.pkl`）与输出产物不入库，见 `.gitignore`。
- `Noisesweep/noisesweep_config.json` 已填入实验机实际参数（`backend=autosweep`、E8257D/PXIe 地址、`chip_id=YBCO#1145` 标定）；温控/激光地址为 `null` 时由 `backends.py` 返回 `FixedTemperature`/`NullLaser` 无硬件后端，离线即可跑通链路。个人 GUI 的种子配置见 `Noisesweep/gui_config.json`（`backend=standalone`，数据路径指向本机 V3 包 data，部署到其它机器需按需修改）。
