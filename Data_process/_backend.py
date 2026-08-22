# -*- coding: utf-8 -*-
"""matplotlib 后端初始化 — 有 Qt → Qt5Agg，否则 TkAgg，最后 Agg。

所有画图脚本统一 import 此模块，替代直接调用 matplotlib.use()。

兜底顺序：PyQt5 → PySide2 → TkAgg → Agg。
TkAgg 依赖 tkinter，Windows 下 matplotlib 自带，是纯 Python 环境里唯一可交互的后端。
无任何 GUI 工具包时才退回 Agg（非交互，plt.show(block=True) 直接返回）。
"""

import matplotlib

_backend = "Agg"
try:
    import PyQt5  # noqa: F401
    _backend = "Qt5Agg"
except ImportError:
    try:
        import PySide2  # noqa: F401
        _backend = "Qt5Agg"
    except ImportError:
        try:
            import tkinter  # noqa: F401
            _backend = "TkAgg"
        except ImportError:
            _backend = "Agg"

matplotlib.use(_backend)
