"""Iconos dibujados en codigo (nitidos a cualquier escala, sin archivos de imagen)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

INK = "#35291F"
WHITE = "#FFFFFF"
_cache: dict[str, QIcon] = {}


def _arrow_head(p: QPainter, tip: QPointF, ang: float, size: float) -> None:
    path = QPainterPath(tip)
    for s in (-1, 1):
        path.lineTo(QPointF(tip.x() - math.cos(ang + s * 0.5) * size, tip.y() - math.sin(ang + s * 0.5) * size))
    path.closeSubpath()
    p.save()
    p.setBrush(p.pen().color())
    p.drawPath(path)
    p.restore()


def _arc_arrow(p: QPainter, clockwise: bool) -> None:
    r = QRectF(4, 4, 12, 12)
    p.drawArc(r, (100 if clockwise else 80) * 16, (-250 if clockwise else 250) * 16)
    end = math.radians(-150 if clockwise else -30)
    tip = QPointF(10 + 6 * math.cos(end), 10 - 6 * math.sin(end))
    _arrow_head(p, tip, end + (math.pi / 2 if clockwise else -math.pi / 2) + math.pi, 4.5)


def _draw(name: str, p: QPainter) -> None:
    c = p.pen().color()
    if name == "arrow":
        p.drawLine(QPointF(4, 16), QPointF(14, 6))
        _arrow_head(p, QPointF(16, 4), math.atan2(-12, 12), 6)
    elif name == "pen":
        path = QPainterPath(QPointF(4, 16))
        path.lineTo(QPointF(5, 12.5))
        path.lineTo(QPointF(13.5, 4))
        path.lineTo(QPointF(16, 6.5))
        path.lineTo(QPointF(7.5, 15))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "text":
        f = QFont("Segoe UI", 1, QFont.Weight.DemiBold)
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 20, 20), Qt.AlignmentFlag.AlignCenter, "Aa")
    elif name == "rect":
        p.drawRect(QRectF(3.5, 5.5, 13, 9))
    elif name == "ellipse":
        p.drawEllipse(QRectF(3, 5, 14, 10))
    elif name == "highlight":
        col = QColor(c)
        col.setAlphaF(0.45)
        p.save()
        p.setPen(QPen(col, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4, 14), QPointF(16, 14))
        p.restore()
        p.drawLine(QPointF(8, 11), QPointF(14, 4))
    elif name == "number":
        p.drawEllipse(QRectF(3, 3, 14, 14))
        f = QFont("Segoe UI", 1, QFont.Weight.Bold)
        f.setPixelSize(10)
        p.setFont(f)
        p.drawText(QRectF(3, 3, 14, 14), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "magnifier":
        p.drawEllipse(QRectF(3, 3, 10, 10))
        p.drawLine(QPointF(11.5, 11.5), QPointF(16.5, 16.5))
        p.drawLine(QPointF(8, 5.5), QPointF(8, 10.5))
        p.drawLine(QPointF(5.5, 8), QPointF(10.5, 8))
    elif name == "image":
        p.drawRoundedRect(QRectF(3, 4, 14, 12), 2, 2)
        path = QPainterPath(QPointF(4, 15))
        path.lineTo(QPointF(8.5, 9.5))
        path.lineTo(QPointF(11.5, 12.5))
        path.lineTo(QPointF(13, 11))
        path.lineTo(QPointF(16, 15))
        p.drawPath(path)
        p.drawEllipse(QPointF(13, 7.5), 1.2, 1.2)
    elif name in ("play", "pause", "back", "fwd"):
        p.save()
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        if name == "play":
            path = QPainterPath(QPointF(6, 4))
            path.lineTo(QPointF(15.5, 10))
            path.lineTo(QPointF(6, 16))
            path.closeSubpath()
            p.drawPath(path)
        elif name == "pause":
            p.drawRoundedRect(QRectF(5.5, 4.5, 3, 11), 1, 1)
            p.drawRoundedRect(QRectF(11.5, 4.5, 3, 11), 1, 1)
        else:
            flip = name == "fwd"
            x = lambda v: 20 - v if flip else v  # noqa: E731
            p.drawRect(QRectF(min(x(4.5), x(6.5)), 5, 2, 10))
            path = QPainterPath(QPointF(x(7.5), 10))
            path.lineTo(QPointF(x(15), 5))
            path.lineTo(QPointF(x(15), 15))
            path.closeSubpath()
            p.drawPath(path)
        p.restore()
    elif name in ("rot_left", "rot_right"):
        _arc_arrow(p, name == "rot_right")
    elif name == "flip":
        p.drawLine(QPointF(10, 3), QPointF(10, 17))
        for s in (-1, 1):
            path = QPainterPath(QPointF(10 + s * 2, 6))
            path.lineTo(QPointF(10 + s * 7, 14))
            path.lineTo(QPointF(10 + s * 2, 14))
            path.closeSubpath()
            p.drawPath(path)
    elif name == "scissors":
        p.drawEllipse(QPointF(6, 14), 2.6, 2.6)
        p.drawEllipse(QPointF(14, 14), 2.6, 2.6)
        p.drawLine(QPointF(7.8, 12.2), QPointF(14.5, 3.5))
        p.drawLine(QPointF(12.2, 12.2), QPointF(5.5, 3.5))
    elif name == "undo":
        p.drawArc(QRectF(5, 5, 11, 10), 90 * 16, -200 * 16)
        p.drawLine(QPointF(10.5, 5), QPointF(5, 5))
        _arrow_head(p, QPointF(3.5, 5), math.pi, 4.5)
    elif name == "trash":
        p.drawLine(QPointF(4, 6), QPointF(16, 6))
        p.drawLine(QPointF(8, 6), QPointF(8.5, 3.5))
        p.drawLine(QPointF(8.5, 3.5), QPointF(11.5, 3.5))
        p.drawLine(QPointF(11.5, 3.5), QPointF(12, 6))
        path = QPainterPath(QPointF(5.5, 6))
        path.lineTo(QPointF(6.5, 17))
        path.lineTo(QPointF(13.5, 17))
        path.lineTo(QPointF(14.5, 6))
        p.drawPath(path)
    elif name in ("up", "down"):
        path = QPainterPath()
        if name == "up":
            path.moveTo(5, 12.5)
            path.lineTo(10, 7.5)
            path.lineTo(15, 12.5)
        else:
            path.moveTo(5, 7.5)
            path.lineTo(10, 12.5)
            path.lineTo(15, 7.5)
        p.drawPath(path)
    elif name == "duplicate":
        p.drawRoundedRect(QRectF(3, 6, 10, 11), 1.5, 1.5)
        p.drawRoundedRect(QRectF(7, 3, 10, 11), 1.5, 1.5)
    elif name == "pointer":
        path = QPainterPath(QPointF(5, 3))
        for pt in ((5, 15.5), (8.5, 12.5), (11, 17.5), (13, 16.5), (10.5, 11.5), (15, 11)):
            path.lineTo(QPointF(*pt))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "line":
        p.drawLine(QPointF(4, 16), QPointF(16, 4))
    elif name == "eraser":
        path = QPainterPath(QPointF(9, 4))
        for pt in ((17, 12), (12, 17), (7, 17), (3, 13)):
            path.lineTo(QPointF(*pt))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(6, 7), QPointF(14, 15))
        p.drawLine(QPointF(12, 17), QPointF(17, 17))
    elif name == "copy":
        p.drawRoundedRect(QRectF(7, 7, 10, 10), 2, 2)
        path = QPainterPath(QPointF(13, 4.5))
        path.lineTo(QPointF(13, 3.5))
        path.lineTo(QPointF(3.5, 3.5))
        path.lineTo(QPointF(3.5, 13))
        path.lineTo(QPointF(4.5, 13))
        p.drawPath(path)
    elif name == "save":
        path = QPainterPath(QPointF(4, 4))
        for pt in ((13.5, 4), (16, 6.5), (16, 16), (4, 16)):
            path.lineTo(QPointF(*pt))
        path.closeSubpath()
        p.drawPath(path)
        p.drawRect(QRectF(7, 4, 6, 4))
        p.drawRect(QRectF(6.5, 11, 7, 5))
    elif name == "close":
        p.drawLine(QPointF(5, 5), QPointF(15, 15))
        p.drawLine(QPointF(15, 5), QPointF(5, 15))
    elif name == "redo":
        p.drawArc(QRectF(4, 5, 11, 10), 90 * 16, 200 * 16)
        p.drawLine(QPointF(9.5, 5), QPointF(15, 5))
        _arrow_head(p, QPointF(16.5, 5), 0, 4.5)
    elif name == "ocr":
        for (x, y), (dx, dy) in (((3, 3), (1, 1)), ((17, 3), (-1, 1)), ((3, 17), (1, -1)), ((17, 17), (-1, -1))):
            p.drawLine(QPointF(x, y), QPointF(x + 4 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 4 * dy))
        for y in (7.5, 10, 12.5):
            p.drawLine(QPointF(6.5, y), QPointF(13.5 if y != 12.5 else 11, y))
    elif name == "eye":
        path = QPainterPath(QPointF(2.5, 10))
        path.quadTo(QPointF(10, 2), QPointF(17.5, 10))
        path.quadTo(QPointF(10, 18), QPointF(2.5, 10))
        p.drawPath(path)
        p.drawEllipse(QPointF(10, 10), 3, 3)
    elif name == "history":
        # reloj con flecha que gira hacia atras (abierto a la izquierda, punta hacia abajo)
        r = 6.5
        p.drawArc(QRectF(10 - r, 10 - r, 2 * r, 2 * r), 160 * 16, -290 * 16)
        a = math.radians(160)
        end = QPointF(10 + r * math.cos(a), 10 - r * math.sin(a))
        tx, ty = -math.sin(a), -math.cos(a)  # sentido antihorario en pantalla
        p.save()
        p.setPen(QPen(c, 1.2))
        _arrow_head(p, QPointF(end.x() + tx * 2.2, end.y() + ty * 2.2), math.atan2(ty, tx), 3.6)
        p.restore()
        p.drawLine(QPointF(10, 10), QPointF(10, 6.2))
        p.drawLine(QPointF(10, 10), QPointF(12.6, 11.5))
    elif name == "blur":
        for i in range(3):
            for j in range(3):
                if (i + j) % 2 == 0:
                    p.fillRect(QRectF(4 + i * 4, 4 + j * 4, 4, 4), c)
        p.drawRect(QRectF(4, 4, 12, 12))
    elif name == "textbg":
        p.save()
        p.setBrush(c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(2.5, 4.5, 15, 11), 2.5, 2.5)
        p.restore()
        f = QFont("Segoe UI", 1, QFont.Weight.Bold)
        f.setPixelSize(9)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF") if QColor(c).lightness() < 128 else QColor("#35291F"))
        p.drawText(QRectF(2.5, 4.5, 15, 11), Qt.AlignmentFlag.AlignCenter, "Aa")
    elif name == "speaker":
        p.save()
        p.setBrush(c)
        path = QPainterPath(QPointF(3, 8))
        for pt in ((6, 8), (10, 4.5), (10, 15.5), (6, 12), (3, 12)):
            path.lineTo(QPointF(*pt))
        path.closeSubpath()
        p.drawPath(path)
        p.restore()
        p.drawArc(QRectF(9, 6, 6, 8), -60 * 16, 120 * 16)
        p.drawArc(QRectF(9, 3.5, 9, 13), -60 * 16, 120 * 16)


def icon(name: str, color: str = INK, checked_color: str = WHITE) -> QIcon:
    key = f"{name}:{color}:{checked_color}"
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for state, col in ((QIcon.State.Off, color), (QIcon.State.On, checked_color)):
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Active):
            pix = QPixmap(40, 40)
            pix.setDevicePixelRatio(2)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            p.setPen(QPen(QColor(col), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            _draw(name, p)
            p.end()
            ic.addPixmap(pix, mode, state)
    _cache[key] = ic
    return ic
