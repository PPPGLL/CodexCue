"""Signal artwork shared by the window, system tray and Windows executable."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


STATUS_COLORS = {
    "ready": "#D6FF36",
    "loading": "#87BCE0",
    "paused": "#ADB8AF",
    "error": "#FF9C78",
}


def render_mark(size: int, status: str | None = None) -> QPixmap:
    """Render the terminal-prompt mark at native icon sizes."""
    if size < 1:
        raise ValueError("Icon size must be positive")
    if status is not None and status not in STATUS_COLORS:
        raise ValueError(f"Unknown tray status: {status}")

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 64, size / 64)
    lime = QColor("#D6FF36")
    painter.setBrush(QColor("#111913"))
    painter.setPen(QPen(lime, 3))
    painter.drawRoundedRect(QRectF(3.5, 3.5, 57, 57), 8, 8)
    painter.setPen(QPen(lime, 5, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
    painter.drawLine(QPointF(17, 20), QPointF(31, 32))
    painter.drawLine(QPointF(31, 32), QPointF(17, 44))
    painter.drawLine(QPointF(34, 44), QPointF(48, 44))

    if status is not None:
        painter.setBrush(QColor(STATUS_COLORS[status]))
        painter.setPen(QPen(QColor("#111913"), 2.5))
        painter.drawEllipse(QPointF(53, 53), 7, 7)

    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_mark(size))
    return icon


def tray_icon(status: str) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        icon.addPixmap(render_mark(size, status))
    return icon
