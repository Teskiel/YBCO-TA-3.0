#!/usr/bin/env python
# pipeline.py — 总线脚本
"""YBCO 超导谐振器数据处理总线。

一键流程:
  python pipeline.py --data-dir D:\\...\\20260609-0624__6-80K__full

分步:
  python pipeline.py --data-dir ... --phase process        # 仅处理 -> data_med/
  python pipeline.py --data-dir ... --step step3_select     # 跑到指定步骤
  python pipeline.py --data-dir ... --force                # 强制重跑全部

独立运行插件:
  python plugins/step4_fit.py --data-dir ...
  python plugins/plot/plot_f0_vs_T.py --data-dir ...
"""
import sys
import os
import argparse
from pathlib import Path

# 确保 _lib, plugins 可导入
_script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_script_dir))

from _lib.plugin_registry import build_execution_plan, check_incremental
from _lib.io_med import (resolve_data_med_dir, resolve_output_dir,
                         get_temporary_resonance_path, resolve_dataset_id)

# 导入所有插件以触发 @plugin 注册
import plugins.step1_scan       # noqa: F401
import plugins.step2_detect     # noqa: F401
import plugins.step3_select     # noqa: F401
import plugins.step4_fit        # noqa: F401
import plugins.plot.plot_f0_vs_T       # noqa: F401
import plugins.plot.plot_Qi_vs_T       # noqa: F401
import plugins.plot.plot_S21_waterfall  # noqa: F401
import plugins.plot.plot_responsivity   # noqa: F401
import plugins.plot.plot_IQ_fitting     # noqa: F401
import plugins.plot.plot_verification        # noqa: F401
import plugins.plot.plot_power_dependence    # noqa: F401
import plugins.plot.plot_S21_laser_power     # noqa: F401
import plugins.plot.plot_thermal_extrapolation  # noqa: F401


def main():
    parser = argparse.ArgumentParser(
        description="YBCO 超导谐振器数据处理总线"
    )
    parser.add_argument(
        "--data-dir", default=r"D:\code\project\YBCO_TA\data\20260609-0624__6-80K__full",
        help="原始 S2P 数据根目录"
    )
    parser.add_argument(
        "--phase", choices=["process", "plot", "all"], default="all",
        help="执行阶段 (default: all)"
    )
    parser.add_argument(
        "--step", default=None,
        help="执行到指定步骤的 func_name 包含此字符串即停止"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="忽略增量检查, 强制重跑所有步骤"
    )
    parser.add_argument(
        "--backend", choices=["scraps", "dataprocess"], default="scraps",
        help="拟合后端 (default: scraps)"
    )
    args = parser.parse_args()

    source = Path(args.data_dir).resolve()
    if not source.is_dir():
        print(f"ERROR: 数据目录不存在: {source}")
        sys.exit(1)

    dataset_name = resolve_dataset_id(source)
    data_med_dir = resolve_data_med_dir(source)

    # 判断是否首次运行: data_med/{dataset}/ 不存在或为空
    is_first_run = (
        not data_med_dir.exists()
        or not any(data_med_dir.iterdir())
    )
    if is_first_run:
        args.force = True
        print(f"[pipeline] 检测到 data_med 为空 — 首次运行模式 (force=True, interactive=True)")

    data_med_dir.mkdir(parents=True, exist_ok=True)

    print(f"=" * 60)
    print(f"Pipeline: {dataset_name}")
    print(f"  源数据:   {source}")
    print(f"  中间参数: {data_med_dir}")
    print(f"  阶段:     {args.phase}")
    print(f"  后端:     {args.backend}")
    print(f"  模式:     {'首次(弹窗)' if is_first_run else '增量'}")
    print(f"=" * 60)

    # 构建执行计划
    plan = build_execution_plan(phase=args.phase)
    if not plan:
        print("ERROR: 无匹配插件 — 检查 plugins/ 目录")
        sys.exit(1)

    print(f"\n执行计划 ({len(plan)} 步骤):")
    for i, p in enumerate(plan):
        print(f"  {i+1}. [{p.phase}:{p.order}] {p.func_name} — {p.description}")
    print()

    # 配置
    from _pic_std import load_pic_std, apply_pic_std_rcparams
    pic_std = load_pic_std("")
    apply_pic_std_rcparams(pic_std)  # 全局字体/刻度 -> matplotlib rcParams
    config = {
        "pic_std": pic_std,
        "vna_powers_filter": None,
        "peak_kwargs_overrides": None,
        "fit_span_hz": 50e6,
        "interactive": True,
        "force_interactive": is_first_run,
        "temporary_resonance_path": str(get_temporary_resonance_path(source)),
        # 温度模型：拟合/外推/标定。enabled=False 时 step3 行为与改动前完全一致。
        "thermal_model": {
            "enabled": True,
            "chip_json": None,          # None = 自动在 data-dir 及其上两级查找
            "tc_k": None,               # None = 取 chip.json 的 tc_k（实测优先）
            "tc_free": False,           # 默认固定 Tc
            "p_fixed": None,
            "use_prior_model": False,   # 默认不注入旧标定，避免带偏新结果
            "auto_temp_max_k": 50,      # 低于此温度用现有自动选点策略
            "min_anchor_temps": 3,      # 少于该低温锚点数不启用模型，退回原逻辑
            "n_resonators": 5,
            "n_sigma": 3.0,
            "window_min_hz": 15e6,
            "widen_factors": [2, 4],
            "save_calibration": True,
        },
    }

    # 执行
    success_count = 0
    skip_count = 0
    fail_count = 0

    plot_type_map = {
        "plugins.plot.plot_f0_vs_T.main": "f0_vs_T",
        "plugins.plot.plot_Qi_vs_T.main": "Qi_vs_T",
        "plugins.plot.plot_S21_waterfall.main": "S21_waterfall",
        "plugins.plot.plot_responsivity.main": "responsivity",
        "plugins.plot.plot_IQ_fitting.main": "IQ_fitting",
        "plugins.plot.plot_verification.main": "verification",
        "plugins.plot.plot_power_dependence.main": "S21_waterfall",
        "plugins.plot.plot_S21_laser_power.main": "S21_laser_power",
        "plugins.plot.plot_thermal_extrapolation.main": "thermal_extrapolation",
    }

    for i, plugin_info in enumerate(plan):
        step_name = f"[{plugin_info.phase}:{plugin_info.order}] {plugin_info.func_name}"

        # 增量检查
        if not args.force and check_incremental(plugin_info, data_med_dir):
            print(f"  SKIP {step_name} — 中间文件已是最新")
            skip_count += 1
            continue

        # Plot 插件需要 output_dir
        if plugin_info.phase == "plot":
            plot_type = plot_type_map.get(plugin_info.func_name, "unknown")
            output_dir = resolve_output_dir(source, plot_type)
            output_dir.mkdir(parents=True, exist_ok=True)

            print(f"  RUN  {step_name}")
            try:
                plugin_info.func(str(data_med_dir), str(output_dir), str(source), config)
                success_count += 1
            except Exception as e:
                print(f"  FAIL {step_name}: {e}")
                import traceback
                traceback.print_exc()
                fail_count += 1
                if args.step:
                    break
        else:
            print(f"  RUN  {step_name}")
            try:
                plugin_info.func(str(data_med_dir), str(source), config)
                success_count += 1
            except Exception as e:
                print(f"  FAIL {step_name}: {e}")
                import traceback
                traceback.print_exc()
                fail_count += 1
                break  # process 阶段失败立即停止

        # --step 检查
        if args.step and args.step in plugin_info.func_name:
            print(f"\n  到达指定步骤 (匹配 '{args.step}'), 停止。")
            break

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"完成: {success_count} 成功, {skip_count} 跳过, {fail_count} 失败")
    print(f"中间参数: {data_med_dir}")
    print(f"{'=' * 60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
