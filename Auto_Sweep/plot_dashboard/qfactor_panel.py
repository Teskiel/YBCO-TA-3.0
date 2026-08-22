# -*- coding: utf-8 -*-
"""Q 值提取面板 — 占位。

未来将在此模块中实现：
  - 谐振谷自动寻谷算法
  - f₀ 提取（谷底频率）
  - Δf₃dB 提取（3 dB 带宽）
  - Q = f₀ / Δf₃dB 计算
  - Qi / Qc 圆拟合分离

当前仅为架构占位，确保 S21Trace 数据模型已携带所需原始数据。
"""

from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class QFactorPanel(QWidget):
    """Q 值提取面板（待实现）。

    将集成到主窗口的左侧筛选面板下方，
    提供谐振谷检测和品质因数分析功能。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        label = QLabel("Q-Factor Extraction\n\nComing soon ...")
        label.setStyleSheet(
            "color: #8B949E; font-size: 12px; padding: 12px;"
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()
