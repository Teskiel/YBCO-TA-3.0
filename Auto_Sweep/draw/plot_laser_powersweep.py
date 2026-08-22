# -*- coding: utf-8 -*-
import numpy as np
import matplotlib.pyplot as plt
import os
import glob
import skrf as rf

target_dir = r'D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\20260609_185708\20K\-43dBm'

# 1. 严格获取文件列表
s2p_files = sorted(glob.glob(os.path.join(target_dir, '**', '*.s2p'), recursive=True))

print(f"--- 诊断开始 ---")
print(f"找到的文件总数: {len(s2p_files)}")

if not s2p_files:
    print("未找到任何 .s2p 文件！")
else:
    # 使用大尺寸画布，并使用 jet 色谱（共256种颜色，绝对够用）
    plt.figure(figsize=(12, 8))
    
    # 2. 对每一个文件进行显式处理
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
    plt.title(f"Total {len(s2p_files)} Lines Plotted")
    plt.legend(loc='best', fontsize=10)
    plt.grid(True)
    plt.show()
    print("--- 诊断结束 ---")