# -*- coding: utf-8 -*-
"""pic_std 配置加载器 — 读取 pic_std.json，提供结构化访问接口。

用法:
    from _pic_std import PicStd, load_pic_std, _cfg

    pic_std = load_pic_std()                    # 默认路径
    pic_std = load_pic_std("path/to/custom.json")  # 自定义路径
    pic_std = load_pic_std(None)                # 返回 None (传统模式)

    # 便捷访问
    color = pic_std.get_temp_color(6)           # → "#1565C0"
    fp = pic_std.figure_params("approach_A")    # → {"figsize": [10,6], "dpi": 150, ...}
    lw = pic_std.cfg("defaults", "lines", "fit", default=2.5)

    # _cfg() 也可独立使用 — pic_std=None 时自动回退到 default
    lw = _cfg(pic_std, "defaults", "lines", "fit", default=2.5)
"""

import json
import os
from typing import Any, Dict, List, Optional


# =========================================================================
# _cfg — 通用配置读取辅助函数
# =========================================================================

def _cfg(pic_std: Optional["PicStd"], *path: str, default: Any = None) -> Any:
    """从 pic_std 中按路径读取值，pic_std=None 时返回 default。

    用法:
        lw = _cfg(pic_std, "defaults", "lines", "fit", default=2.5)

    如果 pic_std 为 None 或路径中任何键缺失，返回 default。
    """
    if pic_std is None:
        return default
    d = pic_std._raw
    for key in path:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
        if d is None:
            return default
    return d


# =========================================================================
# PicStd 类
# =========================================================================

class PicStd:
    """pic_std.json 的结构化访问器。"""

    def __init__(self, json_path: str):
        """加载 pic_std JSON 文件。

        Args:
            json_path: pic_std.json 的绝对路径

        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON 格式错误
        """
        if not os.path.exists(json_path):
            raise FileNotFoundError(
                f"pic_std 配置文件不存在: {json_path}\n"
                f"请确认文件路径，或创建 pic_std.json 配置文件。"
            )
        with open(json_path, "r", encoding="utf-8") as f:
            self._raw: Dict[str, Any] = json.load(f)

    # ---- 便捷访问器 ----

    def get_temp_color(self, temp_k: int) -> str:
        """根据温度 (K) 返回对应颜色。

        Args:
            temp_k: 温度 (开尔文)

        Returns:
            十六进制颜色字符串，未知温度返回 fallback
        """
        tc = self._raw.get("temperature_colors", {})
        return tc.get(str(temp_k), tc.get("fallback", "#333333"))

    def get_vna_colors(self, n: int) -> List[str]:
        """为 n 条 VNA 功率曲线生成均匀分布的颜色。

        Args:
            n: VNA 功率曲线数量

        Returns:
            长度为 n 的十六进制颜色列表
        """
        ramp = self._raw.get("vna_color_ramp", [])
        if not ramp:
            return ["#000000"] * n

        if n <= len(ramp):
            import numpy as np
            indices = np.linspace(0, len(ramp) - 1, n, dtype=int)
            return [ramp[i] for i in indices]

        # 超出预设数量: 使用 jet colormap 兜底
        import matplotlib
        try:
            import matplotlib.pyplot as plt
            cmap = plt.cm.jet
        except Exception:
            cmap = matplotlib.cm.jet
        return [
            matplotlib.colors.to_hex(cmap(i / max(n - 1, 1)))
            for i in range(n)
        ]

    def get_special_color(self, name: str) -> str:
        """获取特殊颜色。

        Args:
            name: 颜色名称 (zero_line, low_snr_text, f0_marker, ...)

        Returns:
            十六进制颜色字符串
        """
        return self._raw.get("special_colors", {}).get(name, "#333333")

    def figure_params(self, section: str, key: str = "individual") -> Dict[str, Any]:
        """获取指定图类型的 figure 参数。

        Args:
            section: 图类型名称 (approach_A, approach_B, approach_S21, verification)
            key: 子键 (individual, grid)

        Returns:
            {"figsize": [w, h], "dpi": int, "save_dpi": int,
             "bbox_inches": str, "save_format": str}
        """
        d = self._raw.get("defaults", {}).get("figure", {})
        section_cfg = self._raw.get(section, {})

        # 确定子配置: key 指定 → 取 section[key]; 否则优先 section 顶层 figsize
        if key != "individual" and key in section_cfg:
            sub_cfg = section_cfg[key]
        elif key in section_cfg and isinstance(section_cfg[key], dict) and "figsize" in section_cfg[key]:
            sub_cfg = section_cfg[key]
        elif "figsize" in section_cfg:
            # section 顶层直接有 figsize (如 approach_S21, verification)
            sub_cfg = section_cfg
        else:
            sub_cfg = section_cfg.get(key, {}) if isinstance(section_cfg, dict) else {}

        figsize = sub_cfg.get("figsize", [10, 6])

        return {
            "figsize": tuple(figsize),
            "dpi": sub_cfg.get("dpi", d.get("dpi", 150)),
            "save_dpi": sub_cfg.get("save_dpi", d.get("save_dpi", 150)),
            "bbox_inches": sub_cfg.get("bbox_inches", d.get("bbox_inches", "tight")),
            "save_format": sub_cfg.get("save_format", d.get("save_format", "png")),
        }

    def cfg(self, *path: str, default: Any = None) -> Any:
        """实例方法版的 _cfg — 从 self._raw 按路径读取。

        用法:
            pic_std.cfg("defaults", "lines", "fit", default=2.5)
        """
        return _cfg(self, *path, default=default)


# =========================================================================
# 工厂函数
# =========================================================================

def load_pic_std(path: Optional[str] = None) -> Optional[PicStd]:
    """加载 pic_std 配置。

    Args:
        path: pic_std.json 路径。
              None → 返回 None (传统模式)
              省略或 "" → 使用默认路径 (draw/pic_std.json)

    Returns:
        PicStd 实例，或 None
    """
    if path is None:
        return None

    if path == "":
        path = os.path.join(os.path.dirname(__file__), "pic_std.json")

    return PicStd(path)


# =========================================================================
# 默认路径
# =========================================================================

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "pic_std.json")


# =========================================================================
# legend_kwargs — 从 pic_std 读取图例样式，返回 ax.legend(**kwargs) 参数字典
# =========================================================================

def legend_kwargs(pic_std: Optional["PicStd"], n_entries: int = 1,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从 pic_std 构造 ax.legend() 的 kwargs。

    图例放置规则: ≤4 条 → 嵌图内 (defaults.legend.loc); >4 条 → 图外右侧。

    Parameters
    ----------
    pic_std : PicStd or None
    n_entries : int
        图例条目数，用于决定放置策略
    extra : dict or None
        额外/覆盖的 kwargs (如 title)

    Returns
    -------
    dict
    """
    loc = _cfg(pic_std, "defaults", "legend", "loc", default="upper right")
    framealpha = _cfg(pic_std, "defaults", "legend", "framealpha", default=0.8)
    facecolor = _cfg(pic_std, "defaults", "legend", "facecolor", default="white")
    edgecolor = _cfg(pic_std, "defaults", "legend", "edgecolor", default="#999999")
    fontsize = _cfg(pic_std, "defaults", "font", "legend", default=12)

    kwargs: Dict[str, Any] = {
        "loc": loc,
        "framealpha": framealpha,
        "facecolor": facecolor,
        "edgecolor": edgecolor,
        "fontsize": fontsize,
    }

    # >4 条 → 图外
    if n_entries > 4:
        kwargs["loc"] = "upper left"
        kwargs["bbox_to_anchor"] = (1.02, 1)

    if extra:
        kwargs.update(extra)

    return kwargs


# =========================================================================
# apply_pic_std_rcparams — 应用 pic_std 字体/刻度设置到 matplotlib rcParams
# 替代旧的 ut.SetDefaultPlotParam()
# =========================================================================

def apply_pic_std_rcparams(pic_std: "PicStd") -> None:
    """Apply pic_std font settings as matplotlib rcParams.

    Replaces the legacy ``ut.SetDefaultPlotParam()`` call when pic_std mode
    is active.  All values come from the JSON config so that changing
    ``pic_std.json`` is sufficient to update every figure — no code edits
    needed.

    Parameters
    ----------
    pic_std : PicStd
        A loaded PicStd instance.  Must not be None.
    """
    import matplotlib as mpl
    import matplotlib.pyplot as _plt

    font_family = _cfg(pic_std, "defaults", "font", "family",
                       default="Times New Roman")
    font_base  = _cfg(pic_std, "defaults", "font", "axis_label", default=14)
    tick_size  = _cfg(pic_std, "defaults", "font", "tick_label", default=11)
    title_size = _cfg(pic_std, "defaults", "font", "title", default=18)

    mpl.rcParams['font.family'] = font_family
    mpl.rcParams['mathtext.fontset'] = 'custom'
    mpl.rcParams['mathtext.rm'] = font_family
    mpl.rcParams['mathtext.it'] = f'{font_family}:italic'
    mpl.rcParams['mathtext.bf'] = f'{font_family}:bold'
    mpl.rcParams.update({'font.size': font_base})
    _plt.rcParams['xtick.labelsize'] = tick_size
    _plt.rcParams['ytick.labelsize'] = tick_size
    mpl.rcParams['axes.titlesize'] = title_size

    # ---- spines / axes edge ----
    mpl.rcParams['axes.edgecolor'] = _cfg(pic_std, "defaults", "spines", "edgecolor",
                                           default="#222222")
    mpl.rcParams['axes.labelcolor'] = _cfg(pic_std, "defaults", "spines", "labelcolor",
                                            default="#111111")
    mpl.rcParams['text.color'] = _cfg(pic_std, "defaults", "spines", "text_color",
                                       default="#111111")
    mpl.rcParams['xtick.color'] = _cfg(pic_std, "defaults", "spines", "tick_color",
                                        default="#222222")
    mpl.rcParams['ytick.color'] = _cfg(pic_std, "defaults", "spines", "tick_color",
                                        default="#222222")
    mpl.rcParams['axes.linewidth'] = _cfg(pic_std, "defaults", "spines", "axes_linewidth",
                                           default=1.2)
    mpl.rcParams['xtick.major.width'] = _cfg(pic_std, "defaults", "spines", "tick_width",
                                               default=1.0)
    mpl.rcParams['ytick.major.width'] = _cfg(pic_std, "defaults", "spines", "tick_width",
                                               default=1.0)

    # ---- figure / axes background ----
    mpl.rcParams['figure.facecolor'] = _cfg(pic_std, "defaults", "spines", "figure_facecolor",
                                              default="white")
    mpl.rcParams['axes.facecolor'] = _cfg(pic_std, "defaults", "spines", "axes_facecolor",
                                            default="white")

    # ---- grid ----
    mpl.rcParams['grid.color'] = _cfg(pic_std, "defaults", "grid", "color",
                                       default="#CCCCCC")

    # ---- tick formatting: 纯数字, 不用科学计数法/offset/mathtext ----
    mpl.rcParams['axes.formatter.limits'] = (-3, 4)
    mpl.rcParams['axes.formatter.use_mathtext'] = False
    mpl.rcParams['axes.formatter.useoffset'] = False


def apply_axis_ticks(ax, pic_std=None, axis='both'):
    """对 ax 应用保守刻度格式: MaxNLocator + prune='both'.

    MaxNLocator 使用 1/2/5*10^n 步长 + prune='both' 裁剪越界刻度,
    确保刻度全部落在数据范围内, 且为漂亮的约整数.

    tick 格式 (plain / useOffset=False) 由 apply_pic_std_rcparams() 全局控制,
    此函数仅负责 tick 位置 (locator).

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    pic_std : PicStd or None
        为 None 时使用硬编码默认值
    axis : str
        'y', 'x', or 'both' (default: 'both')
    """
    from matplotlib.ticker import MaxNLocator
    if axis in ('y', 'both'):
        y_nbins = _cfg(pic_std, "defaults", "ticks", "y_nbins", default=4)
        y_min   = _cfg(pic_std, "defaults", "ticks", "y_min_ticks", default=3)
        y_prune = _cfg(pic_std, "defaults", "ticks", "prune", default="both")
        ax.yaxis.set_major_locator(MaxNLocator(
            nbins=y_nbins, min_n_ticks=y_min, prune=y_prune))
    if axis in ('x', 'both'):
        x_nbins = _cfg(pic_std, "defaults", "ticks", "x_nbins", default=5)
        x_min   = _cfg(pic_std, "defaults", "ticks", "x_min_ticks", default=3)
        x_prune = _cfg(pic_std, "defaults", "ticks", "prune", default="both")
        ax.xaxis.set_major_locator(MaxNLocator(
            nbins=x_nbins, min_n_ticks=x_min, prune=x_prune))
