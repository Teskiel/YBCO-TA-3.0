# Data_process — 离线数据处理（v2.0 总线 + 插件）

YBCO 谐振器 S2P 数据的离线分析：**扫描 → 寻峰 → 选点 → IQ 拟合 → 出图**。纯软件，无硬件依赖。

```
S2P → [process: 4 plugins] → data_med/{dataset}/ → [plot: 9 plugins] → output/{dataset}/{plot_type}/
```

## 命令

```bash
python Data_process/pipeline.py --data-dir <原始S2P目录>            # 全流程
python Data_process/pipeline.py --data-dir <目录> --phase process   # 只到中间参数
python Data_process/pipeline.py --data-dir <目录> --phase plot      # 只画图
python Data_process/pipeline.py --data-dir <目录> --step step3_select   # 跑到某步停
python Data_process/pipeline.py --data-dir <目录> --force           # 强制重跑
python Data_process/pipeline.py --data-dir <目录> --backend scraps|dataprocess
python Data_process/plugins/plot/plot_f0_vs_T.py --data-dir <目录>  # 单插件独立跑
python Data_process/rescalibration.py --data-dir <目录>             # 人工校准（Tk，非插件）
```

## 文件地图

| 文件 | 作用 |
|---|---|
| `pipeline.py` | 总线。顶部 13 个 `import plugins.*` **只为触发 `@plugin` 注册**，不是没用 |
| `_lib/plugin_registry.py` | `@plugin` 装饰器；注册键是**模块限定名**（`plugins.step4_fit.main`），避免键冲突 |
| `_lib/scanning.py` | 目录扫描；判 `hierarchical`（有 `{T}K/actual_*K/`）还是 `flat` |
| `_lib/resonance.py`(1013 行) | 寻峰核心。`find_true_resonances`（幅度+相位双判据）、`find_dip_peaks_in_windows`（高温专用）、`ResonancePickerSession`（供人工校准） |
| `_lib/fitting.py` | 双后端拟合 + `finite_diff_extrapolate` + `compute_normalized_iq` |
| `_lib/thermal_model.py`(645 行) | ⭐ f(T,P) 物理模型，纯计算无 IO。全项目技术含量最高处 |
| `_lib/io_s2p.py` | `load_s_param`（skrf）+ `parse_s2p_filename` → 提取 `actual_temp_k` |
| `_lib/io_med.py` | `data_med/` JSON+npz 读写；`resolve_dataset_id` |
| `_lib/io_thermal.py` | `chip.json` / `thermal_models/` / `resposition/` 存取 |
| `plugins/step1..4_*.py` | 四步 process 插件 |
| `plugins/plot/*.py` | 9 个绘图插件 |
| `rescalibration.py` | 人工校准 + 反馈闭环（**不注册 plugin**） |

## 四步 process

| 步 | 输入 → 输出 | 要点 |
|---|---|---|
| step1_scan | → `scan_result.json` + `file_manifest.json` | 逐 (T,Pv,Pl) 记录 `actual_temp_k` 与温度偏差 |
| step2_detect | → `detected_peaks.json` | 参考 trace = (T, −25 dBm, 0 mW)，缺失时回退 −30/−35/−45/−55 |
| step3_select | → `initial_guess.json` | 选点核心，见下 |
| step4_fit | → `fit_results.json` + `traces.npz` + `selected_resonances.json` | IQ 拟合，末尾 `_archive_resposition()` 追加激光维度 |

**step3 是重点：低温自动 + 高温模型驱动 walk-forward。**
- 低温（`target < auto_temp_max_k`=50 K）：取最深的 5 个谷，按频率升序编号 R1–R5
- 高温：`thermal_model.prediction_window()` 给出预测窗口 → 窗口内 `find_dip_peaks_in_windows()` 验证下凹 → `assign_peaks()`（匈牙利算法全局分配）→ 窗口按 `[2,4]` 倍逐级展宽重扫
- 状态 `ok` / `merged` / `lost`；lost 保留编号占位，后续温度继续尝试找回
- 全部失败才退回 `find_true_resonances(hint_freqs=...)` 或 Tk 交互选点

## 双后端拟合（`_lib/fitting.py`）

| backend | 实现 | 返回 |
|---|---|---|
| `scraps`（默认） | `scraps.Resonator` + `fitsS21.cmplxIQ_fit` 复平面拟合 | f0, qi, qc, ql |
| `dataprocess`（自动回退） | `find_true_resonances` 定 f0 + −3 dB 带宽 | f0, dip_db, bw_hz, ql |

**坑**：scraps 的拟合值**不在 `res` 属性上**，必须 `dict(zip(res.lmfit_labels, res.lmfit_vals))`。代码里有专门注释。另外本实现的 `phi/a/alpha/tau` **硬编码返回 `None`**（文档声称会返回，不一致）。

## ⭐ 温度模型 `_lib/thermal_model.py`

物理动机：逼近 Tc 时谐振变浅变宽（70 K 实测 prominence 仅 0.001–0.01 dB），双判据寻峰**物理性失效**——这正是当初必须人工点选的原因。解法是用模型把搜索缩到预测中心 ± 窗口，窗口内凹陷仍清晰（1.8–2.8 dB）。

```
f_n(T,P) = f_n(0)·[ (1−α_n) + α_n/ρ_s ]^(−1/2),   ρ_s = 1 − (T/Tc)^p − κ_n·P
```
与 Roitman 2023（SUST 36 015002）式(3) **数学等价**（`p ↔ γ`）。`P=0` 时退化为纯温度模型，旧调用不受影响。

**docstring 里记了 4 条实测硬经验，改这个模块前必读：**
1. 温度**必须**用 S2P 文件名里的 `actual` 实测值，绝不用文件夹整数 target —— 实测这是 2.6× 的误差源（walk-forward 12.7 → 32.5 MHz；全量 rms 447 → 974 ppm）
2. 跨段一次性外推不可靠（误差同号累积，最坏 53 MHz），**必须单步 walk-forward**
3. 给 (λ/λ₀)² 加自由指数会过拟合（误差 12.7 → 22.8 MHz）
4. delta-method 的 σ **严重低估**（3σ 覆盖率仅 30%），窗口必须用 `prediction_window()` 经验自标定

参数：`DEFAULT_P_INIT=1.85`、`P_BOUNDS=(0.5,6.0)`、`ALPHA_BOUNDS=(1e-4,0.95)`、`KAPPA_BOUNDS=(0,0.05)`、`DEFAULT_N_SIGMA=3.0`、`DEFAULT_WINDOW_MIN_HZ=15e6`

## 芯片级标定库

**两级目录布局**：`data/{chip}/chip.json` + `data/{chip}/{run}/{T}K/...`（chip.json 在数据集**父目录**即两级，在数据集目录内即扁平）。

**`dataset_id` 与 `run_id` 是两个不同的键**——前者是 `data_med/`+`output/` 的路径键（两级时 `{chip_id}__{run}`），后者是 `thermal_models/{chip_id}__{run_id}.json` 的归档键。

**`resposition/`**（芯片级标定资产，txt 内是 JSON）：
```
res__run1.txt             # 0mW 变温谐振记录（基准）
res__run1&01mW.txt        # 每个激光功率一份
res__run1&03mW__v2.txt    # 人工校准产生的新版本
fit__{chip_id}.txt        # f(T,P) 拟合函数，单文件覆盖更新
```
记录写的是**拟合后 f0**（非 step3 初值），每点同时留 `f_initial_hz` 与 `f0_hz`。

**`rescalibration.py`**：Tk 主窗选温度/激光 → 三面板算法预填 N 个槽位 → 人工只改错的那一两个 → 任一模式 |Δ| 超窗口半宽则写新版本 + 全量重拟合 + 覆盖 `fit__{chip_id}.txt`。

## data_med 产物

| 文件 | 内容 |
|---|---|
| `scan_result.json` | 温度/功率列表、目录结构类型 |
| `file_manifest.json` | 逐 (T,Pv,Pl) 的 actual_temp 与偏差统计（~441 KB） |
| `detected_peaks.json` | 每温度的峰列表（频率、SNR、深度） |
| `initial_guess.json` | schema v2：`by_temperature`/`by_resonator`/`actual_temps_k`/`status`/`prediction`/`thermal_fit`/`by_laser` |
| `fit_results.json` | 每 (R,T) 的 f0/qi/qc/ql/r_squared/responsivity |
| `traces.npz` | 拟合窗口内的 `freq_hz`/`s21_db`/`s21_complex`/`iq_norm`/`iq_fit_norm` |

**关键区分**：`initial_guess.json` 的频率是**选定初值**（仅供启动拟合），`fit_results.json` 的 `f0_hz` 才是**拟合后真实 f0**。两者不相等。

## 陷阱

1. **本仓库（3.0）没有 `tests/`** —— 旧库 `YBCO_TA/Data_process/tests/` 有 103 个测试但没搬过来。改代码后无法靠 pytest 验证，只能实跑 `--phase process`。**如果要做实质修改，建议先从旧库把 tests 拷过来。**
2. `matplotlib.use("TkAgg")` **必须在 `import _lib.resonance` 之前**（该模块顶部就 import pyplot）。
3. `_archive_resposition()` 里 VNA 功率**硬编码 −25 dBm**，换读出功率档时需改这里。
4. `compute_normalized_iq()` 依赖 `from scraps.fitsS21.hanger_resonator import cmplx_hanger` —— **该模块不存在**（正确名字是 `harder_resonator`），此函数会静默失败（打印警告后 `return None`）。
5. 文档漂移（以代码为准）：`CLAUDE.md` 若说 `_lib` 8 个模块，实际 **10** 个；说 8 个绘图插件，实际 **9** 个。

## 不要读（省 token）

- `output/`（数 GB 图片）、`data_med/`（中间 JSON/npz）
- `plugins/plot/` 的具体实现 —— 除非要改那张图，否则只读 `pic_std.json` / `_pic_std.py` 就够
- `_lib/resonance.py` 全文（1013 行）—— 先看函数名列表，按需读单个函数
