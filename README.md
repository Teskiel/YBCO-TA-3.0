# YBCO-TA 3.0

YBCO 高温超导 KID（动力学电感探测器）微波测量平台 —— **三模块整合版**，取代旧 YBCO-TA。

把三台真正用于实际测量的模块整合到单一仓库，可在其它电脑一键下载运行：

| 模块 | 职责 | 仪器 |
|---|---|---|
| **Auto_Sweep** | 变温 × 激光功率微波扫描测量（GUI） | LakeShore 335 温控、Keysight VNA、N7779C 激光 |
| **Data_process** | 离线数据处理（谐振选点 + 拟合 + 出图） | 无（纯软件） |
| **Noisesweep** | 变温 × 谐振 × 激光功率噪声测量编排（CLI + Dashboard） | E8257D 信号源、PXIe-4480 DAQ + 上述温控/激光 |

## 目录结构

```
YBCO-TA-3.0/
├── Auto_Sweep/      # 测量 GUI（SMlab201 最新运行版）
├── Data_process/    # 离线处理核心（_lib/ + plugins/ + pipeline.py）
├── Noisesweep/      # 噪声测量编排（18 个 .py + config）
├── requirements.txt # 统一依赖
├── install.py       # 安装自检器
└── README.md
```

三模块**代码层相互独立**，可单独读懂/运行；Noisesweep 通过相对路径（兄弟目录）引用 Data_process 的标定库与 Auto_Sweep 的驱动，不依赖任何绝对路径。

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
```bash
# CLI
python Noisesweep/noisesweep.py --config Noisesweep/noisesweep_config.json run
python Noisesweep/noisesweep.py --config Noisesweep/noisesweep_config.json temp read

# Dashboard（浏览器操作面板）
python Noisesweep/noisesweep_dashboard.py --config Noisesweep/noisesweep_config.json --port 8000
# 打开 http://127.0.0.1:8000
```

## 离线验证（不接硬件）

```bash
# Noisesweep 全链路 mock 空跑
python Noisesweep/noisesweep.py --config Noisesweep/_dryrun_config.json --dry-run scan --freqs 4.5

# 55 项离线断言
cd Noisesweep && python _verify_offline.py
```

## 说明

- 测量产生的原始数据（`.s2p` / `.h5` / `.pkl`）与输出产物不入库，见 `.gitignore`。
- `Noisesweep/noisesweep_config.json` 中仪器地址（VISA 地址等）为 `null`，首次真实测量前按实验机实际配置填写，或先用 `--dry-run` 离线空跑验证链路。
