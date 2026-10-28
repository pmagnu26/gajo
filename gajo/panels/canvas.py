"""Lienzo de vista previa con herramientas que se dibujan encima (recorte, cajas)."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

ORANGE = QColor("#E2701F")


class ImageCanvas(QWidget):
    """Muestra una imagen ajustada sobre fondo oscuro y reenvia el mouse a una herramienta."""

    def __init__(self, min_h: int = 300):
        super().__init__()
        self.setMinimumHeight(min_h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.image: QImage | None = None
        self.tool = None
        self.layers: list[Callable] = []  # funciones (painter, rect_imagen) que dibujan encima

    def set_image(self, img: QImage | None) -> None:
        self.image = img
        self.update()

    def image_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        if not self.image or self.image.isNull():
            return area
        k = min(area.width() / self.image.width(), area.height() / self.image.height())
        w, h = self.image.width() * k, self.image.height() * k
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def to_norm(self, pos: QPointF, clamp: bool = True) -> tuple[float, float]:
        r = self.image_rect()
        x = (pos.x() - r.left()) / max(r.width(), 1)
        y = (pos.y() - r.top()) / max(r.height(), 1)
        if clamp:
            x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        return x, y

    def from_norm(self, x: float, y: float) -> QPointF:
        r = self.image_rect()
        return QPointF(r.left() + x * r.width(), r.top() + y * r.height())

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1B1917"))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        if self.image and not self.image.isNull():
            r = self.image_rect()
            p.drawImage(r, self.image)
            for layer in self.layers:
                layer(p, r)
            if self.tool:
                self.tool.paint(p, r)
        p.end()

    def mousePressEvent(self, e) -> None:
        if self.tool and e.button() == Qt.MouseButton.LeftButton:
            self.tool.press(self, e.position())
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self.tool:
            self.tool.move(self, e.position(), bool(e.buttons() & Qt.MouseButton.LeftButton))
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.tool and e.button() == Qt.MouseButton.LeftButton:
            self.tool.release(self, e.position())
            self.update()


def _handle_rects(r: QRectF, size: float = 9) -> dict[str, QRectF]:
    pts = {
        "tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight(),
        "t": QPointF(r.center().x(), r.top()), "b": QPointF(r.center().x(), r.bottom()),
        "l": QPointF(r.left(), r.center().y()), "r": QPointF(r.right(), r.center().y()),
    }
    return {k: QRectF(v.x() - size / 2, v.y() - size / 2, size, size) for k, v in pts.items()}


class CropTool:
    """Marco de recorte con manillas. rect y aspect en coordenadas normalizadas."""

    def __init__(self, on_change: Callable | None = None):
        self.rect = [0.0, 0.0, 1.0, 1.0]
        self.aspect: float | None = None  # ancho/alto en pixeles reales
        self.img_ratio = 1.0              # ancho/alto de la imagen
        self.on_change = on_change
        self._drag: str | None = None
        self._start = None

    def set_aspect(self, aspect: float | None) -> None:
        self.aspect = aspect
        if aspect:  # el marco mas grande con esa proporcion, centrado
            norm = aspect / self.img_ratio  # w/h en coordenadas normalizadas
            w, h = (1.0, 1.0 / norm) if norm >= 1 else (norm, 1.0)
            if h > 1:
                w, h = w / h, 1.0
            if w > 1:
                w, h = 1.0, h / w
            self.rect = [(1 - w) / 2, (1 - h) / 2, w, h]
        self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def screen_rect(self, canvas) -> QRectF:
        x, y, w, h = self.rect
        return QRectF(canvas.from_norm(x, y), canvas.from_norm(x + w, y + h))

    def paint(self, p: QPainter, img_r: QRectF) -> None:
        x, y, w, h = self.rect
        r = QRectF(img_r.left() + x * img_r.width(), img_r.top() + y * img_r.height(),
                   w * img_r.width(), h * img_r.height())
        shade = QPainterPath()
        shade.addRect(img_r)
        hole = QPainterPath()
        hole.addRect(r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawPath(shade.subtracted(hole))
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for k in (1, 2):  # tercios
            p.drawLine(QPointF(r.left() + r.width() * k / 3, r.top()), QPointF(r.left() + r.width() * k / 3, r.bottom()))
            p.drawLine(QPointF(r.left(), r.top() + r.height() * k / 3), QPointF(r.right(), r.top() + r.height() * k / 3))
        p.setPen(QPen(ORANGE, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ORANGE)
        for hr in _handle_rects(r).values():
            p.drawRect(hr)

    def press(self, canvas, pos: QPointF) -> None:
        r = self.screen_rect(canvas)
        for k, hr in _handle_rects(r, 16).items():
            if hr.contains(pos):
                self._drag = k
                break
        else:
            self._drag = "move" if r.contains(pos) else None
        self._start = (canvas.to_norm(pos, clamp=False), list(self.rect))

    def move(self, canvas, pos: QPointF, pressed: bool) -> None:
        if not pressed:
            r = self.screen_rect(canvas)
            over = next((k for k, hr in _handle_rects(r, 16).items() if hr.contains(pos)), None)
            cursors = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                       "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                       "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                       "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor}
            canvas.setCursor(cursors.get(over, Qt.CursorShape.SizeAllCursor if r.contains(pos)
                                         else Qt.CursorShape.ArrowCursor))
            return
        if not self._drag or not self._start:
            return
        (sx, sy), (x, y, w, h) = self._start
        nx, ny = canvas.to_norm(pos, clamp=False)
        dx, dy = nx - sx, ny - sy
        if self._drag == "move":
            x = min(max(x + dx, 0.0), 1 - w)
            y = min(max(y + dy, 0.0), 1 - h)
        else:
            left, top, right, bottom = x, y, x + w, y + h
            if "l" in self._drag:
                left = min(max(left + dx, 0.0), right - 0.02)
            if "r" in self._drag:
                right = max(min(right + dx, 1.0), left + 0.02)
            if "t" in self._drag:
                top = min(max(top + dy, 0.0), bottom - 0.02)
            if "b" in self._drag:
                bottom = max(min(bottom + dy, 1.0), top + 0.02)
            x, y, w, h = left, top, right - left, bottom - top
            if self.aspect:  # mantener la proporcion ajustando el lado secundario
                norm = self.aspect / self.img_ratio
                if self._drag in ("t", "b"):
                    w = h * norm
                else:
                    h = w / norm
                if x + w > 1:
                    w = 1 - x
                    h = w / norm
                if y + h > 1:
                    h = 1 - y
                    w = h * norm
                if "t" in self._drag:
                    y = bottom - h
                if "l" in self._drag:
                    x = right - w
        self.rect = [x, y, w, h]
        self._changed()

    def release(self, canvas, pos: QPointF) -> None:
        self._drag = None


class BoxesTool:
    """Varias cajas que se crean arrastrando, se mueven y se ajustan desde las esquinas."""

    def __init__(self, on_change: Callable | None = None):
        self.boxes: list[list[float]] = []
        self.selected = -1
        self.style = "solid"
        self.color = "#000000"
        self.visible: Callable[[int], bool] = lambda i: True
        self.extra: Callable[[], list] = lambda: []  # datos extra por caja (ej. tiempos en video)
        self.on_change = on_change
        self._mode = None
        self._start = None

    def _rect(self, canvas, b) -> QRectF:
        return QRectF(canvas.from_norm(b[0], b[1]), canvas.from_norm(b[0] + b[2], b[1] + b[3]))

    def paint(self, p: QPainter, img_r: QRectF) -> None:
        for i, b in enumerate(self.boxes):
            if not self.visible(i) and i != self.selected:
                continue
            r = QRectF(img_r.left() + b[0] * img_r.width(), img_r.top() + b[1] * img_r.height(),
                       b[2] * img_r.width(), b[3] * img_r.height())
            dim = not self.visible(i)
            if self.style == "blur":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(255, 255, 255, 60 if dim else 150), Qt.BrushStyle.Dense4Pattern))
            else:
                c = QColor(self.color)
                c.setAlpha(70 if dim else 255)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
            p.drawRect(r)
            if i == self.selected:
                p.setPen(QPen(ORANGE, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ORANGE)
                for k in ("tl", "tr", "bl", "br"):
                    p.drawRect(_handle_rects(r)[k])

    def _hit(self, canvas, pos: QPointF):
        order = [self.selected] + [i for i in range(len(self.boxes) - 1, -1, -1) if i != self.selected]
        for i in order:
            if i < 0 or not (self.visible(i) or i == self.selected):
                continue
            r = self._rect(canvas, self.boxes[i])
            for k, hr in _handle_rects(r, 16).items():
                if k in ("tl", "tr", "bl", "br") and hr.contains(pos):
                    return i, k
            if r.contains(pos):
                return i, "move"
        return -1, None

    def press(self, canvas, pos: QPointF) -> None:
        i, mode = self._hit(canvas, pos)
        n = canvas.to_norm(pos)
        if i < 0:
            self.boxes.append([n[0], n[1], 0.0, 0.0, *self.extra()])
            i, mode = len(self.boxes) - 1, "new"
        self.selected, self._mode = i, mode
        self._start = (n, list(self.boxes[i]))
        self._changed()

    def move(self, canvas, pos: QPointF, pressed: bool) -> None:
        if not pressed:
            _, mode = self._hit(canvas, pos)
            canvas.setCursor({"move": Qt.CursorShape.SizeAllCursor}.get(
                mode, Qt.CursorShape.SizeFDiagCursor if mode else Qt.CursorShape.CrossCursor))
            return
        if self._mode is None or self.selected < 0:
            return
        (sx, sy), (x, y, w, h, *_) = self._start
        nx, ny = canvas.to_norm(pos)
        box = self.boxes[self.selected]
        if self._mode == "new":
            box[:4] = [min(sx, nx), min(sy, ny), abs(nx - sx), abs(ny - sy)]
        elif self._mode == "move":
            box[:4] = [min(max(x + nx - sx, 0), 1 - w), min(max(y + ny - sy, 0), 1 - h), w, h]
        else:
            left, top, right, bottom = x, y, x + w, y + h
            if "l" in self._mode:
                left = nx
            if "r" in self._mode:
                right = nx
            if "t" in self._mode:
                top = ny
            if "b" in self._mode:
                bottom = ny
            box[:4] = [min(left, right), min(top, bottom), abs(right - left), abs(bottom - top)]
        self._changed()

    def release(self, canvas, pos: QPointF) -> None:
        if self._mode == "new" and self.selected >= 0:
            b = self.boxes[self.selected]
            if b[2] < 0.01 or b[3] < 0.01:  # un clic sin arrastrar no crea caja
                self.boxes.pop(self.selected)
                self.selected = -1
        self._mode = None
        self._changed()

    def delete_selected(self) -> None:
        if 0 <= self.selected < len(self.boxes):
            self.boxes.pop(self.selected)
            self.selected = -1
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()
