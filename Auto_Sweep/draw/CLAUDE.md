# draw/CLAUDE.md — 独立绘图脚本

独立的 matplotlib 绘图脚本，用于实验后 S2P 数据可视化。主文档见 [../CLAUDE.md](../CLAUDE.md)。

依赖: `numpy`, `scikit-rf`, `matplotlib`。

## 新工作流 (推荐) — 数据缓存 + 统一画图

**两步流程，避免重复扫描和数据收集：**

### Step 1: 收集 + 缓存 + 验证

```bash
python draw/_data_cache.py --data-dir "D:/.../experiment_data/T6-77K_VNA-55~-25dBm_step2dB"
```

这一步会:
1. 扫描所有 (T, Pv, Pl) 组合
2. 在每个温度下识别 5 个谐振子
3. **生成验证图** → 用户检查谐振位置是否正确
4. 追踪全部 (T, R, Pv, Pl) 的 f0 → δf/f₀
5. 保存为 `output/_cache/_cache_{dataset}.pkl` (~1-5 MB)

验证图输出到 `output/_cache/verification_{dataset}/`，每张标注识别的 f0、谷底最小值、P90 基线。

### Step 2: 画图

```bash
# 全部方案，全部 VNA 功率，两种风格
python draw/plot_all.py --cache "D:/.../output/_cache/_cache_XXX.pkl"

# 仅方案 B，指定 VNA 功率，仅拟合线
python draw/plot_all.py --cache "..." --approaches B \
    --vna-powers -55,-53,-51,-49,-47,-45,-43,-41,-39 --style fit_only

# 方案 A + B，6 条等间距 VNA 线 (6dB 间隔)
python draw/plot_all.py --cache "..." --approaches A,B \
    --vna-powers -55,-49,-43,-37,-31,-25 --style both
```

### 方案说明

| 方案 | 内容 | X 轴 | 曲线 | 输出 |
|------|------|------|------|------|
| **A** | 响应率 vs VNA 功率 | VNA Power (dBm) | 温度 (5 条) | 5 张图 (R1-R5) |
| **B** | df/f vs Laser Power | Laser Power (mW) | VNA Power (N 条) | 5×5 网格 + 25 张单独图 |
| **S21** | S21 全谱叠加 | Frequency (GHz) | VNA Power (N 条) | N 张图 (T×Pl) |

### 风格选项

| `--style` | 效果 |
|-----------|------|
| `full` | 散点 + 原始连线 + 拟合虚线 |
| `fit_only` | 仅线性拟合实线 |
| `both` | 两套都生成 (默认) |

### 编程用法

```python
from draw._data_cache import collect_and_cache, load_cache, print_cache_summary

# 收集
cache = collect_and_cache(data_dir, force_refresh=False)
print_cache_summary(cache)

# 后续直接加载
cache = load_cache("path/to/_cache_xxx.pkl")

# 访问数据
meta = cache["metadata"]
for T_K in meta["temperatures_k"]:
    c = cache["collected"][T_K]
    for rname in meta["resonator_names"]:
        d = c["data"][rname]
        # d["delta_f_over_f"]  → (n_pv, n_pl) ndarray
        # d["flags"]           → (n_pv, n_pl) str array
```

### 缓存数据结构

```python
cache = {
    "metadata": {
        "data_dir": "...",           # 原始数据路径
        "dataset_name": "...",       # 数据集名 (文件夹名)
        "collected_at": "2026-...",  # ISO 时间戳
        "temperatures_k": [6,10,20,40,77],
        "laser_powers_mw": [0,1,3,5,7,9],
        "resonator_names": ["R1","R2","R3","R4","R5"],
        "total_tracked_points": 1234,
        "total_grid_points": 2400,
    },
    "identification": {              # 谐振子识别结果 (供验证)
        6: {
            "vna_powers_dbm": [-55,-53,...,-25],
            "reference_file": "D:/.../...s2p",
            "reference_pv_dbm": -55,
            "reference_pl_mw": 0,
            "resonators": [
                {"name":"R1","f0_ghz":3.8451,"dip_depth_db":-15.5},
                ...
            ],
        },
        ...
    },
    "collected": {                   # δf/f₀ 数据 (供画图)
        6: {
            "vna_powers_dbm": [...],
            "reference": {...},
            "identified": {"R1": {"f0_ghz":..., "dip_depth_db":...}, ...},
            "data": {
                "R1": {
                    "delta_f_over_f": ndarray (n_pv, n_pl),
                    "flags": ndarray (n_pv, n_pl),
                    "f0_refs": {pv: f0, ...},
                },
                ...
            },
        },
        ...
    },
}
```

---

## Prompt 模板 — 省口舌的画图请求

以后画图只需一句话，Claude 自动匹配参数：

### 模板 1: 新数据集首次画图

> 用 `{数据文件夹名}` 画图，先收集缓存 + 验证，确认谐振位置后再画全部方案。

Claude 会自动:
1. 运行 `_data_cache.py` 收集缓存
2. 等待你检查验证图
3. 运行 `plot_all.py --approaches all --style both` 画全部方案

### 模板 2: 仅更新画图参数

> 基于 `{数据集}` 的缓存，用 `{VNA功率列表}` 画方案 `{A/B/S21}`，风格 `{full/fit_only/both}`。

例: "基于 T6-77K_step2dB 缓存，用 -55 到 -39 的 9 条 VNA 线画方案 B，仅拟合线。"

### 模板 3: 加新方案

> 基于已有缓存，追加画 `{新方案}`。

例: "基于已有缓存，追加画 S21 overlay。"

### 模板 4: 更换数据集

> 用 `{新数据文件夹}` 代替 `{旧数据集}`，其余不变。

Claude 会重新收集缓存，然后复用相同的画图参数。

---

## 已废弃的旧脚本

以下脚本为 `_data_cache.py` + `plot_all.py` 重构前的单功能脚本，**不再维护**。功能已被覆盖，保留仅供代码参考：

| 旧脚本 | 替代方案 |
|--------|----------|
| `plot_AB_final.py` | `plot_all.py --approaches A,B` |
| `plot_approach_A_full.py` | `plot_all.py --approaches A --style both` |
| `plot_B_2dBstep.py` | `plot_all.py --approaches B --vna-powers -55,-53,...,-39 --style fit_only` |
| `plot_approach_B.py` | `plot_all.py --approaches B` |
| `plot_verification.py` | `_data_cache.py` (自动生成验证图) |
| `plot_three_approaches.py` | 概念已整合进 `plot_all.py` |
| `diagnose_nofit.py` | 调试用，不再需要 |
| `diagnose_77K.py` | 调试用，不再需要 |
| `diagnose_77K_detail.py` | 调试用，不再需要 |

**仍在使用的独立脚本:**

| 脚本 | 用途 |
|------|------|
| `plot_laser_powersweep.py` | S21 overlay: 固定 (T, Pv)，变化 Pl — 独立于缓存体系 |
| `plot_VNA_powersweep.py` | S21 overlay: 固定 (T, Pl)，变化 Pv — 独立于缓存体系 |
| `plot_s21_overlay_batch.py` | 批量 S21 overlay (被 `plot_all.py --approaches S21` 覆盖) |
| `plot_deltaf_vs_laser.py` | δf vs laser (旧版, 被方案 B 覆盖) |
| `plot_deltaf_vs_laser_v2.py` | δf vs laser v2 (旧版, 被方案 B 覆盖) |

---

## 核心模块

| 文件 | 用途 |
|------|------|
| `_tracking_utils.py` | 谐振子追踪算法: FD 预测 + P90 dip 检测 +相位交叉验证 |
| `_data_cache.py` | 数据收集 + pickle 缓存 + 验证图 |
| `plot_all.py` | 统一画图入口: 方案 A/B/S21 |

---

## 画图规范（强制）

| 项目 | 规范 | 来源 |
|------|------|------|
| S2P 读取 | `skrf.Network(fp)` | `plot_laser_powersweep.py` |
| S21 索引 | `ntwk.s[:, 1, 0]`（端口 1→0） | skrf 惯例 |
| dB 转换 | `20 * np.log10(np.abs(s21))` | — |
| 画布 | `figsize=(10, 6)` (单独), `figsize=(28, 22)` (网格) | — |
| 色谱 (VNA 功率轴) | 自动生成: 深蓝→青→绿→橙→红 均匀分布 | `get_vna_colors()` |
| 色谱 (激光功率 mW 轴) | `plt.cm.jet` 蓝→青→绿→黄→橙→红 冷→暖渐变 | `plot_laser_powersweep.py` |
| 色谱 (温度轴) | 蓝(6K) 青(10K) 绿(20K) 橙(40K) 红(77K) | 固定配色 |
| 线宽 | `linewidth=2.5` (拟合线), `linewidth=2` (数据线) | — |
| 网格 | `ax.grid(True, alpha=0.25~0.3)` | — |
| DPI | `dpi=150` | — |
| print 输出 | **禁用 emoji**（GBK 报错 `UnicodeEncodeError`） | Spyder 兼容 |

---

## 频率追踪（关键坑，反复踩过）

YBCO 谐振子 f0 随温度单调红移（动能电感效应）。**不可**用 6K 参考频率固定 zoom，必须逐温度追踪。

**最终方案：f0(T) 曲线驱动 + 双验证 + 自适应 zoom**

```
1. f0(T) 骨架: scraps 数据 [6, 22, 38, 54, 70] K 精确 f0
2. 有限差分外推: 同 process_data_single_pixel.py，仅用历史中 T < target 的点
3. 验证 a) 预测位置 ±50 MHz → 局部基线 (P90) 矫正 dip 检测
4. 验证 b) 若 a 失败 → find_true_resonances() 幅度+相位交叉验证寻峰
5. Zoom 自适应: T≤20K → ±10 MHz, T≥77K → ±30 MHz, 中间线性渐变
6. 局部基线: P90, dip_depth = dip_abs − baseline < −1 dB 判定存活
```

**坑 1 — 固定参考频率：** 初始直接用 6K f0 做 zoom 中心，±25 MHz 在 20K 时仍可覆盖，但 40K 时偏移 25~60+ MHz，全部出窗。

**坑 2 — 绝对 dB 阈值：** 高温下 S21 基线抬升到 +8~9 dB，R1-R5 dip 绝对值全部 > 0 dB，但相对局部基线下降 5-7 dB。**必须用 P90 局部基线矫正。**

**坑 3 — 有限差分方向（最关键）：** `predict_f0_fd()` 必须只取 T < target 的历史点做外推。若包含 T > target 的点，6K 时预测值完全错误（外推方向反转）。已知温度（±0.5K）直接返回精确 scraps 值。

**坑 4 — Zoom 窗口固定：** 76K 时 ±15 MHz 无法覆盖基线偏移后的谐振特征。按 T≤20K=10, T≥77K=30 MHz 线性渐变。

## 温度节点与谐振子存活表

| 温度 | Zoom | R1 | R2 | R3 | R4 | R5 | 存活 |
|------|------|-----|-----|-----|-----|-----|------|
| 6K | ±10 | −15.5 | −11.9 | −14.8 | −22.5 | −11.8 | 5/5 |
| 10K | ±10 | −15.4 | −11.9 | −14.6 | −22.0 | −11.5 | 5/5 |
| 20K | ±10 | −14.6 | −11.0 | −13.0 | −19.5 | −11.3 | 5/5 |
| 40K | ±17 | −12.6 | −9.8 | −9.9 | −14.3 | −12.1 | 5/5 |
| 76K | ±30 | −2.7 | −4.3 | −2.3 | −4.9 | −5.0 | 5/5 |

76K 时 R4/R5 频率合并（4.469 GHz），但两者均被独立追踪。

## 编码注意事项

- print 输出**禁用 emoji**（`✅` `⚠` → GBK 报错 `UnicodeEncodeError`），用 ASCII 或跳过
- Spyder 兼容：`matplotlib.use("Qt5Agg")` 优先，回退 `Agg`

## 输出组织

```
output/_cache/
├── _cache_{dataset}.pkl              # 缓存文件
├── verification_{dataset}/           # 验证图
│   ├── T6K_R1_verify.png
│   └── ...
└── plot_output/                      # 画图输出
    ├── approach_A/                   # 方案 A: 散点+线+拟合
    │   └── responsivity_vs_VNA_R1.png ... R5.png
    ├── approach_A_fit_only/          # 方案 A: 仅趋势线
    ├── approach_B/                   # 方案 B: 散点+线+拟合
    │   ├── grid_overview.png         # 5×5 网格总览
    │   └── individual/               # 25 张单独大图
    ├── approach_B_fit_only/          # 方案 B: 仅拟合线
    └── S21_overlay/                  # S21 全谱叠加
```

## 数据依赖

输入示例：`D:\YBCO\VNAMeas\Auto_Sweep\experiment_data\T6-77K_VNA-55~-25dBm_step2dB\`
结构：`{T}K/{-Pv}dBm/{mw:02d}mW/*.s2p`

依赖：`numpy`, `scikit-rf`, `matplotlib`, `scipy`（dataprocess 已内联至仓库 `lib/`，无外部 monorepo 依赖）。
