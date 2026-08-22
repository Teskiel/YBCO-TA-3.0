# -*- coding: utf-8 -*-
"""
Experiment configuration constants.

All sweep parameters, instrument addresses, PID values, and stability
criteria live here so they can be changed in one place without touching
any instrument or algorithm code.

Dead code removed from the original power_sweep_auto.py:
  - TRACE_NAME         (declared but never referenced)
  - auto_adjust_pid    (never read by any code path)
  - diagnose_before_measure (never read by any code path)
"""

# =========================================================================
# Instrument resource addresses
# =========================================================================

resource_vna = "TCPIP0::localhost::hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR"
laser_resource = "TCPIP0::K-N7779C-00108::inst0::INSTR"
resource_lakeshore = "ASRL4::INSTR"

# =========================================================================
# Output paths
# =========================================================================

date = "20260529"
base_folder = rf"D:\YBCO\VNAMeas\data\{date}\-45dBm_temperature_sweep"

# =========================================================================
# Sweep ranges
# =========================================================================

power_levels_mw = [0, 1, 3, 5, 7, 9]

# Start from 26 K, step 2 K up to 100 K
temperature_levels_k = list(range(26, 101, 2))

# =========================================================================
# Temperature stability parameters
# =========================================================================

stable_hold_seconds = 60             # must stay stable this long before measuring
max_wait_seconds = 30 * 60           # 测量阶段默认最大等待 (30 min, Dashboard 可 override)
max_wait_max_minutes = 180           # Dashboard max_wait 控件上限 (升至 3h)
temperature_poll_seconds = 10        # polling interval (~6 readings/min)

# ---- 各温度点的最大等待时间覆盖（秒） ----
# 升温速率 ~0.2 K/min，50K→60K (10K) 需 ~50min，60K→70K 需 ~50min
# 因此高温段设置更长超时，键为温度 (K)，值为 max_wait 秒数
# None = 使用默认 max_wait_seconds
max_wait_per_temperature_k = {
    60.0: 60 * 60,    # 50→60K 升温: 60 min
    70.0: 60 * 60,    # 60→70K 升温: 60 min
    80.0: 60 * 60,    # 70→80K 升温: 60 min
}

custom_stability_settings = {
    "avg_window_seconds": 60,        # 1-minute rolling window
    "avg_tolerance_k": 1.0,          # average within ±1.0 K of setpoint
    "delta_tolerance_k": 0.1,        # max drift over 2-minute span — 趋于平稳阈值
    "final_stable_band_k": 0.5,      # ±0.5 K 进入目标温度区间
    "min_readings_required": 8,      # 匹配 180s 回溯窗口（SPARSE 20s: 180/20=9, FINE 5s: 180/5=36, 8 兼顾两者）
}

# ---- 稳态判定（与目标区间判定并行，无先后关系） ----
steady_state_max_min_k = 0.1         # 3 分钟窗口 max-min ≤ 0.1K = 进入稳态
steady_state_window_s = 180          # 稳态判定窗口（3 分钟）

# ---- 双阶段轮询间隔 ----
sparse_poll_seconds = 20             # Phase 1 低频读取（趋于平稳前）
fine_poll_seconds = 5                # Phase 2 高频读取（趋于平稳后，密集判稳态）

# =========================================================================
# Setpoint adjustment (overshoot / undershoot compensation)
# =========================================================================

setpoint_adjust_settings = {
    "low_temp_threshold": 20.0,      # below 20 K: no overshoot, setpoint = target
    "medium_temp_threshold": 40.0,   # 20–40 K: apply limited overshoot
    "max_overshoot_k": 1.0,          # cap on overshoot amount
    "overshoot_factor": 0.5,         # multiplier on temperature error
}

# =========================================================================
# PID parameters (temperature-zone-dependent)
# =========================================================================

PID_PARAMS = {
    "low_temp":    {"p": 100.0, "i": 5.0, "d": 0.0, "max_temp": 20.0},
    "medium_temp": {"p": 100.0, "i": 0.0, "d": 0.0, "min_temp": 20.0, "max_temp": 40.0},
    "high_temp":   {"p": 150.0, "i": 0.0, "d": 0.0, "min_temp": 40.0},
}

# =========================================================================
# 自动重连设置
# =========================================================================

max_reconnect_attempts = 3          # 最大重连尝试次数
reconnect_delay_seconds = 2         # 重连前等待秒数

# =========================================================================
# 实验稳定性回退设置（简化版：固定PID + 仅调整设定点过冲）
# =========================================================================

# 各温区固定 PID 参数（永不调整）
FIXED_PID_ZONES = {
    "low":    {"max_temp": 20.0, "p": 100, "i": 5, "d": 0, "base_overshoot_k": 0.0},
    "medium": {"max_temp": 40.0, "p": 100, "i": 0, "d": 0, "base_overshoot_k": 2.0},
    "high":   {"max_temp": 70.0, "p": 150, "i": 0, "d": 0, "base_overshoot_k": 2.0},
    "very_high": {"max_temp": float("inf"), "p": 150, "i": 0, "d": 0, "base_overshoot_k": 2.5},
}

stability_fallback_settings = {
    "max_setpoint_adjustments": 2,   # 已废弃 — overshoot 调整不再有计数上限，改用 overshoot_target_band_k
    "good_enough_band_k": 0.5,       # ±0.5K "足够好"回退阈值
    "diagnostic_interval_s": 60,     # 状态推进间隔（秒）
    "max_overshoot_k": 10.0,         # 设定点过冲上限
    "overshoot_target_band_k": 0.7,  # overshoot 调整目标区间: |avg−target| ≤ 此值 → 停止调整
}

# =========================================================================
# 测量逻辑版本
# =========================================================================

MEASUREMENT_LOGIC_VERSION = "2026-06-08"  # 每次修改测量流程后更新，写入数据头部

# =========================================================================
# VNA 功率区间扫描默认值（与按钮网格并行使用）
# =========================================================================

vna_power_range_default_start_dbm = -55  # 功率区间起始 dBm
vna_power_range_default_stop_dbm = -45   # 功率区间终止 dBm
vna_power_range_default_step_db = 2      # 功率区间步长 dB

# =========================================================================
# 内存监控设置（防止实验期间 OOM 级联崩溃）
# =========================================================================

memory_monitor_enabled = True            # 是否启用内存监控
memory_warning_threshold_mb = 8192       # 可用内存低于 8 GB 时警告
memory_critical_threshold_mb = 4096      # 可用内存低于 4 GB 时严重告警
memory_check_interval_s = 60             # 长时间等待期间每隔 60 秒检查一次内存
memory_auto_pause_threshold_mb = 3072    # 可用内存低于 3 GB 时自动暂停实验，等待恢复
memory_early_warning_percent = 80         # 内存使用率超过此百分比时触发早期预警（自动保存 checkpoint）
memory_graceful_exit_threshold_mb = 2048  # 可用内存低于 2 GB 时优雅退出（保存 checkpoint 后终止）
memory_process_diag_enabled = True       # 实验启动时输出系统 Top-5 内存消耗进程
long_experiment_warning_hours = 2.0      # 实验超过此时长后，完成时提示建议重启 GUI
post_experiment_zombie_cleanup = True     # 实验结束后自动清理僵尸 Python 进程
post_experiment_restart_recommend = True  # 实验结束后强制建议重启 GUI（内存 > 60% 时）
log_max_blocks = 5000                    # GUI 日志控件最大保留行数（防止 QTextEdit OOM）

# =========================================================================
# 稳定性阶段独立超时
# =========================================================================

sparse_max_wait_seconds = 90 * 60      # SPARSE 阶段最长等待 (90 min)

# =========================================================================
# 测量中温度漂移熔断
# =========================================================================

inter_measurement_max_delta_k = 0.25   # 同温度下任意两次测量 pre_temp 差 > 0.25K → 熔断
max_meltdown_restarts = 3                 # 同温度点熔断重启上限，超限跳过此温度点

# 测量中漂移熔断自适应
meltdown_settling_multipliers = [8, 15]   # 第1次×8, 第2次×15
meltdown_relaxed_threshold_k = 0.45       # 第2次放宽后的阈值
retry_mode_max_meltdowns = 2              # 复测模式额外熔断次数

laser_on_temp_tolerance_k = 1.0          # 激光加热时放宽的测量后容差 (K)

# =========================================================================
# 测量前预等待（仅首次测量循环使用）
# =========================================================================

pre_measurement_wait_minutes = 0       # 默认 0 min (关闭)
pre_measurement_wait_max_minutes = 120 # Dashboard 控件上限
pre_measurement_wait_temp_tolerance_k = 0.5   # 预等待后 |ΔT| > 此值 → 重入稳定

# =========================================================================
# 激光功率切换沉降时间
# =========================================================================

laser_settle_time_s = 20                  # 功率切换沉降 (激光已上电)
laser_first_on_settle_time_s = 60         # 首次上电沉降 (0→非零)

# =========================================================================
# 实验数据完整性检查与迁移设置
# =========================================================================

# 实验数据根目录（与 ui/workers.py ExperimentWorker 的输出路径一致）
experiment_data_base_dir = r"D:\YBCO\VNAMeas\Auto_Sweep\experiment_data"

# 已完成实验迁移目标子文件夹名称（在 experiment_data_base_dir 下创建）
accomplish_subfolder_name = "accomplish"

# 完整性判定阈值
min_temp_levels_for_complete = 3          # 至少完成 N 个温度点才算完整
expected_laser_powers_mw = [0, 1, 3, 5, 7, 9]  # 每个 (温度, VNA功率) 组合期望的激光功率
min_laser_powers_for_complete = 3         # 如果达不到 expected，至少要有此数量的激光功率

# 日志完整性标记
log_complete_marker = "Experiment complete"  # 日志最后 N 字符中查找此标记
log_tail_chars = 200                       # 检查日志末尾的字符数

# S2P 文件最小大小（字节），用于判定非空
min_s2p_file_size_bytes = 1000             # 低于此值视为空文件

# 迁移时跳过的文件名模式（Zip 归档等）
migrate_skip_patterns = ["*.zip"]

# =========================================================================
# 超时软化与连续回退设置
# =========================================================================

# 超时软化（需求 A）：超时后 |avg−target| ≤ 此值 → 软通过，照常测量
timeout_soft_pass_band_k = 2.0

# 连续问题回退（需求 B）
consecutive_issue_threshold = 2         # 连续 N 个温度点出现问题 → 触发回退
rollback_max_wait_increase_min = 30     # 回退时 max_wait 增加分钟数
rollback_pre_wait_increase_min = 10     # 回退时 pre_measurement_wait 增加分钟数

# 4K 特殊处理（需求 C）
skip_validation_temp_k = 4.0            # 此温度跳过温度范围检定，仅做稳定判定

# =========================================================================
# 断点续传 & 自动重连
# =========================================================================

reconnect_retry_interval_s = 30          # 重连尝试间隔（秒）
reconnect_max_wait_minutes = 30          # 最大等待重连时间（分钟）
checkpoint_save_interval_points = 5      # 每完成 N 个测量点增量保存检查点
checkpoint_keep_latest_attempt_only = True  # 实验正常结束时仅保留最新 attempt 的 S2P

# =========================================================================
# 进程看门狗 & 心跳
# =========================================================================

heartbeat_interval_s = 60          # 心跳写入间隔（秒）
heartbeat_timeout_s = 300          # 挂死判定阈值（秒）

# =========================================================================
# Claude 主动监控 & 自动补测
# =========================================================================

# 冷却策略（补测模式）
fill_cooldown_offset_k = 5.0          # 冷却时 setpoint = target - offset（而非 0K）
fill_cooldown_poll_seconds = 10       # 冷却阶段轮询间隔（秒）
fill_cooldown_max_wait_minutes = 60   # 冷却最大等待时间（分钟）
fill_min_safe_temp_k = 4.0            # 补测冷却最低安全温度

# 状态文件
status_write_enabled = True           # 是否写入 status.json 供 Claude 监控
