# _lib/caption.py — 图注工具
"""在 matplotlib figure 底部留白处写图注 (SCI 规范: "Fig. X. 说明")。

依赖 tight_layout(rect=[0, 0.06, 1, 1]) 预留底部空间，从 OLD compare_resonators.py 提取。
"""


def add_caption(fig, text: str, fontsize: int = 9,
                color: str = "#555555", y: float = -0.02):
    """在 figure 底部添加斜体图注。

    Parameters
    ----------
    fig : matplotlib.figure.Figure
    text : str
        图注文字，如 "Fig. 1. Full-spectrum S21 ..."
    fontsize : int
        字号 (默认 9，来自 pic_std defaults.layout.caption_fontsize)
    color : str
        文字颜色 (默认 #555555)
    y : float
        垂直位置 (figure 坐标，负值 = 底部留白处)
    """
    fig.text(0.5, y, text, ha="center", va="top", fontsize=fontsize,
             fontstyle="italic", color=color)
