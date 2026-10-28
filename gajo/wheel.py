"""La rueda: aparece bajo el cursor mientras arrastras archivos y tocas una tecla."""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QWidget

from . import catalog

log = logging.getLogger("gajo")

MODES = {
    "convertir": ("Convertir", QColor("#E2701F")),
    "herramientas": ("Herramientas", QColor("#2476AE")),
    "acciones": ("Acciones", QColor("#6E52C9")),
}
KEY_LABEL = {"shift": "Shift", "ctrl": "Ctrl", "alt": "Alt"}
R_OUT, R_IN, MARGIN = 172, 68, 6
BG, SEG, SEG_LINE = QColor("#FBF8F4"), QColor("#FFFFFF"), QColor("#E5DACD")
INK, INK_SOFT, CENTER = QColor("#35291F"), QColor("#76685C"), QColor("#F2EAE0")
GHOST = 0.01  # casi invisible hasta confirmar que lo arrastrado son archivos


def _sector(cx: float, cy: float, r1: float, r2: float, c0: float, c1: float) -> QPainterPath:
    """Gajo entre los radios r1 y r2. Angulos tipo brujula: 0 arriba, horario."""
    outer = QRectF(cx - r2, cy - r2, 2 * r2, 2 * r2)
    inner = QRectF(cx - r1, cy - r1, 2 * r1, 2 * r1)
    start, sweep = 90 - c0, -(c1 - c0)
    path = QPainterPath()
    path.arcMoveTo(outer, start)
    path.arcTo(outer, start, sweep)
    path.arcTo(inner, start + sweep, -sweep)
    path.closeSubpath()
    return path


class Wheel(QWidget):
    chosen = Signal(object, list)  # Op elegida, rutas

    def __init__(self, actions_provider, keys: dict[str, str]):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                 | Qt.WindowType.NoDropShadowWindowHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        side = 2 * (R_OUT + MARGIN)
        self.setFixedSize(side, side)
        # Fuera del circulo la ventana no existe: ahi se puede soltar en cualquier otra parte.
        self.setMask(QRegion(MARGIN - 2, MARGIN - 2, 2 * R_OUT + 4, 2 * R_OUT + 4,
                             QRegion.RegionType.Ellipse))
        self.actions_provider = actions_provider
        self.keys = keys
        self.mode = "convertir"
        self.paths: list[Path] = []
        self.ops: list = []
        self.hover = -1
        self.revealed = False
        self._expire_timer = QTimer(self)
        self._expire_timer.setSingleShot(True)
        self._expire_timer.setInterval(450)
        self._expire_timer.timeout.connect(self._expired)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(110)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------ ciclo de vida

    def summon(self, mode: str) -> None:
        self._expire_timer.stop()
        if self.isVisible():
            self.set_mode(mode)
            return
        self.mode, self.paths, self.ops, self.hover, self.revealed = mode, [], [], -1, False
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(pos.x() - self.width() // 2, geo.left()), geo.right() - self.width())
        y = min(max(pos.y() - self.height() // 2, geo.top()), geo.bottom() - self.height())
        self.move(x, y)
        self.setWindowOpacity(GHOST)
        self.show()
        self.raise_()

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.hover = -1
            self._rebuild()
            self.update()

    def mouse_released(self) -> None:
        """El boton se solto: si no hubo drop sobre la rueda, se cierra sola."""
        if self.isVisible():
            self._expire_timer.start()

    def _expired(self) -> None:
        log.info("Rueda cerrada sin soltar encima (%s, archivos=%d)", self.mode, len(self.paths))
        self.close_wheel()

    def close_wheel(self) -> None:
        self._expire_timer.stop()
        self._fade.stop()
        self.hide()
        self.paths, self.ops, self.hover, self.revealed = [], [], -1, False

    def _reveal(self) -> None:
        if not self.revealed:
            self.revealed = True
            self._fade.stop()
            self._fade.setStartValue(0.15)
            self._fade.setEndValue(1.0)
            self._fade.start()

    def _rebuild(self) -> None:
        self.ops = catalog.options(self.mode, self.paths, self.actions_provider()) if self.paths else []

    # ------------------------------------------------------------ arrastre

    @staticmethod
    def _accept(event) -> None:
        # Siempre "copiar": asi el origen nunca borra el archivo original.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragEnterEvent(self, event) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths or not event.possibleActions() & Qt.DropAction.CopyAction:
            event.ignore()
            return
        if paths != self.paths:
            self.paths = paths
            self._rebuild()
        self._reveal()
        self._track(event.position())
        self._accept(event)

    def dragMoveEvent(self, event) -> None:
        self._track(event.position())
        self._accept(event)

    def dragLeaveEvent(self, event) -> None:
        self.hover = -1
        self.update()

    def dropEvent(self, event) -> None:
        pos = event.position()
        idx = self.segment_at(pos)
        self._accept(event)
        op = self.ops[idx] if 0 <= idx < len(self.ops) else None
        log.info("Soltado en (%.0f, %.0f) de la rueda %s: %s", pos.x() - self.width() / 2,
                 pos.y() - self.height() / 2, self.mode, op.label if op else "centro, cancelado")
        paths = list(self.paths)
        self.close_wheel()
        if op is not None:
            QTimer.singleShot(0, lambda: self.chosen.emit(op, paths))

    def _track(self, pos: QPointF) -> None:
        idx = self.segment_at(pos)
        if idx != self.hover:
            self.hover = idx
            self.update()

    def segment_at(self, pos: QPointF) -> int:
        dx, dy = pos.x() - self.width() / 2, pos.y() - self.height() / 2
        if not self.ops or not R_IN <= math.hypot(dx, dy) <= R_OUT:
            return -1
        width = 360 / len(self.ops)
        theta = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
        return int(((theta + width / 2) % 360) // width)

    # ------------------------------------------------------------ dibujo

    @staticmethod
    def _font(size: float, bold: bool = False) -> QFont:
        font = QFont("Segoe UI", 1)
        font.setPointSizeF(size)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return font

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        name, accent = MODES[self.mode]

        p.setPen(QPen(QColor(0, 0, 0, 45), 1.2))
        p.setBrush(BG)
        p.drawEllipse(QPointF(cx, cy), R_OUT, R_OUT)

        n = len(self.ops)
        if n:
            width = 360 / n
            gap = 1.8 if n > 1 else 0
            size = 11 if n <= 4 else 10 if n <= 6 else 9 if n <= 8 else 8
            rm = (R_IN + R_OUT) / 2 + 3
            box = min(2 * rm * math.sin(math.radians(min(width, 180) / 2)) * 0.9, 104)
            for i, op in enumerate(self.ops):
                hot = i == self.hover
                c0, c1 = i * width - width / 2 + gap / 2, i * width + width / 2 - gap / 2
                path = _sector(cx, cy, R_IN + 4, R_OUT - 5, c0, c1)
                p.setPen(Qt.PenStyle.NoPen if hot else QPen(SEG_LINE, 1))
                p.setBrush(accent if hot else SEG)
                p.drawPath(path)
                theta = math.radians(i * width)
                lx, ly = cx + rm * math.sin(theta), cy - rm * math.cos(theta)
                p.setPen(QColor("#FFFFFF") if hot else INK)
                p.setFont(self._font(size, bold=True))
                p.drawText(QRectF(lx - box / 2, ly - 22, box, 44),
                           Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, op.label)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CENTER)
        p.drawEllipse(QPointF(cx, cy), R_IN - 1, R_IN - 1)

        p.setPen(accent)
        p.setFont(self._font(7.5, bold=True))
        p.drawText(QRectF(cx - 60, cy - 47, 120, 14), Qt.AlignmentFlag.AlignCenter, name.upper())

        p.setPen(INK)
        p.setFont(self._font(12.5, bold=True))
        main = catalog.describe(self.paths) if self.paths else "···"
        p.drawText(QRectF(cx - 60, cy - 33, 120, 24), Qt.AlignmentFlag.AlignCenter, main)

        if 0 <= self.hover < n:
            hint = self.ops[self.hover].hint
        elif n:
            hint = "Suelta sobre una opción"
        elif self.paths:
            hint = "Sin opciones aquí: prueba otra tecla"
        else:
            hint = "Mueve un poco el cursor"
        p.setPen(INK_SOFT)
        p.setFont(self._font(7.5))
        p.drawText(QRectF(cx - 52, cy - 8, 104, 34),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   hint)

        # Las tres teclas, con la activa destacada.
        order = ["convertir", "herramientas", "acciones"]
        for j, mode in enumerate(order):
            active = mode == self.mode
            p.setPen(MODES[mode][1] if active else QColor("#B3A79C"))
            p.setFont(self._font(7.5, bold=active))
            label = KEY_LABEL.get(self.keys.get(mode, ""), "?")
            p.drawText(QRectF(cx - 57 + j * 38, cy + 28, 38, 14), Qt.AlignmentFlag.AlignCenter, label)
        p.end()

    # ------------------------------------------------------------ pruebas

    def snapshot(self, mode: str, paths: list[Path], hover: int, out: Path) -> None:
        """Dibuja la rueda en un PNG sin mostrarla (para revisar el diseno)."""
        self.mode, self.paths, self.hover = mode, paths, hover
        self._rebuild()
        self.grab().save(str(out))
