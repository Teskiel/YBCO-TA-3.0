# -*- coding: utf-8 -*-
"""
plot_laser_powersweep_0-9mW.py
基于 plot_laser_powersweep.py，仅作图 0-9 mW 范围的激光功率数据。
超出范围的数据即使检测到也会被舍弃。
"""
import numpy as np
import matplotlib.pyplot as plt
import os
import glob
import re
import skrf as rf

target_dir = r'D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\20260609_185708\20K\-43dBm'


def extract_laser_power_mw(file_path):
    """从文件路径中提取激光功率 (mW)。

    路径格式: .../{mw}mW/*.s2p，例如 .../5mW/YBCO_xxx.s2p
    返回 int 类型的 mW 值；若路径中无匹配的 mW 目录则返回 None。
    """
    parts = file_path.replace('\\', '/').split('/')
    for part in parts:
        m = re.match(r'^(\d+)mW$', part)
        if m:
            return int(m.group(1))
    return None


# 1. 获取所有 s2p 文件
all_s2p_files = sorted(glob.glob(os.path.join(target_dir, '**', '*.s2p'), recursive=True))

# 2. 过滤：仅保留 0-9 mW 范围内的文件，其余舍弃
s2p_files = []
skipped_files = []

for fp in all_s2p_files:
    power_mw = extract_laser_power_mw(fp)
    if power_mw is not None and 0 <= power_mw <= 9:
        s2p_files.append(fp)
    else:
        skipped_files.append((fp, power_mw))

print(f"--- 诊断开始 (仅 0-9 mW) ---")
print(f"找到的文件总数: {len(all_s2p_files)}")
print(f"0-9 mW 范围内: {len(s2p_files)}")
print(f"已舍弃: {len(skipped_files)}")

if skipped_files:
    skipped_powers = set(pw for _, pw in skipped_files if pw is not None)
    if skipped_powers:
        print(f"舍弃的激光功率值: {sorted(skipped_powers)} mW")

if not s2p_files:
    print("未找到 0-9 mW 范围内的 .s2p 文件！")
else:
    # 使用大尺寸画布，并使用 jet 色谱（共256种颜色，绝对够用）
    plt.figure(figsize=(12, 8))

    # 3. 对每一个过滤后的文件进行显式处理
    for i, file_path in enumerate(s2p_files):
        try:
            ntwk = rf.Network(file_path)
            freq = ntwk.f / 1e9
            # s21 = ntwk.s[:, 0, 1]
            s21 = ntwk.s[:, 1, 0]

            # 使用 jet 色谱，根据索引分配颜色，确保不重叠
            color = plt.cm.jet(i / len(s2p_files))

            # 增加线宽，确保看得清
            plt.plot(freq, 20 * np.log10(np.abs(s21)),
                     color=color,
                     linewidth=3,
                     alpha=0.8,
                     label=f'File_{i}: {os.path.basename(file_path)}')

            print(f"成功绘制第 {i+1} 条线: {os.path.basename(file_path)}")

        except Exception as e:
            print(f"读取失败 {file_path}: {e}")

    plt.xlabel("Frequency (GHz)")
    plt.ylabel("S21 Magnitude (dB)")
    plt.title(f"Total {len(s2p_files)} Lines Plotted (0-9 mW only)")
    plt.legend(loc='best', fontsize=10)
    plt.grid(True)
    plt.show()
    print("--- 诊断结束 ---")
