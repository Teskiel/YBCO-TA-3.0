# -*- coding: utf-8 -*-
"""
Shared UI widgets — Deep Space Cyan theme.

  - StatusLight:   coloured circle indicating connection state
  - DeviceCard:    clickable card showing device name, model, and status
"""

from typing import Optional

from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QBrush
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# StatusLight — Deep Space Cyan palette
# ---------------------------------------------------------------------------

class StatusLight(QWidget):
    """Coloured circle: cyan (standby), green (connected), yellow (busy), red (error)."""

    COLORS = {
        "green":  QColor("#4ADE80"),
        "yellow": QColor("#FBBF24"),
        "red":    QColor("#EF4444"),
        "grey":   QColor("#30363D"),
    }

    def __init__(self, size: int = 14, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._color = self.COLORS["grey"]
        self.setFixedSize(QSize(size + 6, size + 6))
        self._radius = size // 2

    def set_color(self, name: str) -> None:
        if name in self.COLORS:
            self._color = self.COLORS[name]
            self.update()

    def set_green(self) -> None:   self.set_color("green")
    def set_yellow(self) -> None:  self.set_color("yellow")
    def set_red(self) -> None:     self.set_color("red")
    def set_grey(self) -> None:    self.set_color("grey")

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # outer glow ring
        glow = QColor(self._color)
        glow.setAlpha(50)
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.NoPen)
        cx, cy = self.width() // 2, self.height() // 2
        painter.drawEllipse(cx - self._radius - 2, cy - self._radius - 2,
                            (self._radius + 2) * 2, (self._radius + 2) * 2)
        # solid core
        painter.setBrush(QBrush(self._color))
        painter.drawEllipse(cx - self._radius, cy - self._radius,
                            self._radius * 2, self._radius * 2)


# ---------------------------------------------------------------------------
# DeviceCard
# ---------------------------------------------------------------------------

class DeviceCard(QFrame):
    """Clickable instrument card with status light, icon, name, model.

    Deep Space Cyan styling: #161B22 background, 1px rgba border,
    highlighted with a left cyan accent bar when connected.
    """

    clicked = pyqtSignal()

    def __init__(self, icon: str, name: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._connected = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(200, 120)
        self.setMaximumWidth(240)
        self.setStyleSheet(
            "DeviceCard {"
            "  background-color: #161B22;"
            "  border: 1px solid rgba(255,255,255,0.10);"
            "  border-radius: 8px;"
            "}")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(4)

        # status light row
        light_row = QHBoxLayout()
        light_row.addStretch()
        self.status_light = StatusLight(12)
        light_row.addWidget(self.status_light)
        layout.addLayout(light_row)

        # icon
        self.icon_label = QLabel(icon)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 30px; background: transparent;")
        layout.addWidget(self.icon_label)

        # name
        self.name_label = QLabel(name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet(
            "font-weight: bold; font-size: 14px; background: transparent;")
        layout.addWidget(self.name_label)

        # model
        self.model_label = QLabel("—")
        self.model_label.setAlignment(Qt.AlignCenter)
        self.model_label.setStyleSheet(
            "color: #8B949E; font-size: 11px; background: transparent;")
        layout.addWidget(self.model_label)

        # hint
        hint = QLabel("Click to configure")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            "color: #484F58; font-size: 10px; background: transparent;")
        layout.addWidget(hint)

    def set_model(self, text: str) -> None:
        self.model_label.setText(text)

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self.status_light.set_green()
            self.setStyleSheet(
                "DeviceCard {"
                "  background-color: #161B22;"
                "  border: 1px solid rgba(255,255,255,0.10);"
                "  border-left: 3px solid #22D3EE;"
                "  border-radius: 8px;"
                "}")
        else:
            self.status_light.set_red()
            self.setStyleSheet(
                "DeviceCard {"
                "  background-color: #161B22;"
                "  border: 1px solid rgba(255,255,255,0.10);"
                "  border-radius: 8px;"
                "}")

    def set_connecting(self) -> None:
        self.status_light.set_yellow()

    def mousePressEvent(self, event):  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)
