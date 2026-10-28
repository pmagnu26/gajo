"""Icono de Gajo dibujado en codigo (una rueda naranja de seis gajos)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

ORANGE = QColor("#E2701F")


def make_icon(size: int = 64) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = size / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ORANGE)
    p.drawEllipse(QPointF(c, c), c - 2, c - 2)
    p.setPen(QPen(QColor("#FFF4E8"), size / 22))
    for k in range(6):
        p.save()
        p.translate(c, c)
        p.rotate(k * 60)
        p.drawLine(QPointF(0, -size * 0.14), QPointF(0, -(c - 5)))
        p.restore()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFF4E8"))
    p.drawEllipse(QRectF(c - size * 0.11, c - size * 0.11, size * 0.22, size * 0.22))
    p.end()
    return QIcon(pix)
