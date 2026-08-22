#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YBCO-TA 3.0 安装自检器 —— 零第三方依赖，仅标准库。

不真正安装任何东西，只做三件事 + 打印启动说明：
  1. 三模块目录/关键文件齐备性检查。
  2. 第三方依赖可导入性检查（importlib.util.find_spec，不触发 import 副作用）。
  3. Noisesweep 相对路径（兄弟目录 Data_process / Auto_Sweep）能否解析。

运行：python install.py
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (模块目录, [哨兵文件…])
MODULES = [
    ("Auto_Sweep",   ["lakeshore_control.py", "laser_driver.py", "app.py"]),
    ("Data_process", ["_lib/thermal_model.py", "pipeline.py", "thermal_models"]),
    ("Noisesweep",   ["noisesweep.py", "noisesweep_config.json", "backends.py"]),
]

# (import 名, 用途)
DEPS = [
    ("numpy",      "数值计算（三模块）"),
    ("scipy",      "科学计算/拟合（三模块）"),
    ("matplotlib", "绘图（三模块）"),
    ("skrf",       "S2P 读取/微波网络（Auto_Sweep + Data_process）"),
    ("PyQt5",      "GUI（Auto_Sweep + Data_process + Noisesweep）"),
    ("pyvisa",     "VISA 仪器驱动（Auto_Sweep + Noisesweep）"),
    ("h5py",       "HDF5 数据存储（Noisesweep）"),
    ("nidaqmx",    "NI PXIe-4480 DAQ（Noisesweep，需 NI-DAQmx 运行时）"),
    ("scraps",     "超导谐振拟合库（三模块；私有仓库 Teskiel/scarp）"),
]

# Noisesweep 相对路径（对应 noisesweep.py DEFAULTS / chip_library.py 的解析结果）
NOISESWEEP_PATHS = [
    ("Data_process/_lib",              "chip_library.DEFAULT_DATA_PROCESS_DIR"),
    ("Data_process/thermal_models",    "芯片标定库数据"),
    ("Data_process/resonance_table.txt", "追踪表 tracking_file"),
    ("Auto_Sweep/lakeshore_control.py",  "autosweep_dir（温控驱动）"),
    ("Auto_Sweep/laser_driver.py",       "autosweep_dir（激光驱动）"),
]


def _dep_installed(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def main() -> int:
    ok = True

    print("=" * 68)
    print("YBCO-TA 3.0 安装自检")
    print("根目录: {}".format(ROOT))
    print("Python : {}".format(sys.version.split()[0]))
    print("=" * 68)

    # 1) 三模块目录/文件
    print("\n[1/3] 三模块目录")
    for mod, sentinels in MODULES:
        missing = [s for s in sentinels if not (ROOT / mod / s).exists()]
        if missing:
            ok = False
            print("  [X] {}  缺少: {}".format(mod, ", ".join(missing)))
        else:
            print("  [OK] {}  ({})".format(mod, ", ".join(sentinels)))

    # 2) 依赖
    print("\n[2/3] 第三方依赖")
    missing_deps = []
    for name, use in DEPS:
        if _dep_installed(name):
            print("  [OK] {}  — {}".format(name, use))
        else:
            ok = False
            missing_deps.append(name)
            print("  [X] {}  — {}  (未安装)".format(name, use))

    # 3) Noisesweep 相对路径
    print("\n[3/3] Noisesweep 相对路径（兄弟目录 Data_process / Auto_Sweep）")
    for rel, desc in NOISESWEEP_PATHS:
        p = ROOT / rel
        if p.exists():
            print("  [OK] {}  — {}".format(rel, desc))
        else:
            ok = False
            print("  [X] {}  — {}  不存在: {}".format(rel, desc, p))

    # 汇总
    print("\n" + "=" * 68)
    if missing_deps:
        print("依赖缺失，请先安装：")
        print("    pip install -r requirements.txt")
        print("（scraps 为私有仓库，若 pip 拉取失败见 README「私有依赖」节）")
    if not ok:
        print("\n存在未通过项，请按上述 [X] 逐项处理。")
    else:
        print("全部检查通过 ✓")
    print("=" * 68)

    # 启动说明
    print("\n启动命令：")
    print("  [Auto_Sweep 测量 GUI]")
    print("      python Auto_Sweep/app.py")
    print("  [Data_process 离线处理]")
    print("      python Data_process/pipeline.py --data-dir <原始S2P目录>")
    print("  [Noisesweep 变温噪声测量]")
    print("      python Noisesweep/noisesweep.py --config Noisesweep/noisesweep_config.json run")
    print("      python Noisesweep/noisesweep_dashboard.py --config Noisesweep/noisesweep_config.json --port 8000")
    print("\n离线自检（不接硬件）：")
    print("      python Noisesweep/noisesweep.py --config Noisesweep/_dryrun_config.json --dry-run scan --freqs 4.5")
    print("\n硬件前置条件（真实测量才需要）：")
    print("  - pyvisa 需本机装 NI-VISA 或 Keysight IO（提供 visa32.dll）")
    print("  - nidaqmx 需本机装 NI-DAQmx 运行时驱动")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
