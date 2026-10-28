"""Captura de pantalla con dibujo encima (reemplaza a Lightshot).

Ctrl+Impr Pant congela la pantalla. Se arrastra para elegir la zona y aparecen las
herramientas. Cada trazo es un objeto: se mueve con clic derecho (o con la flecha de
mover), se estira desde sus puntas, se borra con Supr o se corta con la goma.
Ctrl+C copia y cierra; Ctrl+S guarda.
"""
from __future__ import annotations

import copy
import math
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QColorDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from . import history
from . import shapes as S
from .panels.icons import icon

ORANGE = QColor("#E2701F")
COLORS = ["#E53935", "#FB8C00", "#FDD835", "#43A047", "#1E88E5", "#8E24AA", "#111111", "#FFFFFF"]
WIDTHS = [2, 4, 7, 12]
TOOLS = [  # (clave, icono, nombre, tecla)
    ("move", "pointer", "Mover y estirar (V) · también con clic derecho", "V"),
    ("pen", "pen", "Lápiz (P)", "P"),
    ("line", "line", "Línea (L) · Shift: recta a 45°", "L"),
    ("arrow", "arrow", "Flecha (A) · Shift: recta a 45°", "A"),
    ("rect", "rect", "Rectángulo (R) · Shift: cuadrado", "R"),
    ("marker", "highlight", "Marcador (M)", "M"),
    ("text", "text", "Texto (T)", "T"),
    ("blur", "blur", "Difuminar (D): pasa el pincel sobre lo que no quieres mostrar", "D"),
    ("eraser", "eraser", "Goma (E) · la rueda del mouse cambia su tamaño", "E"),
]
BAR_QSS = """
QFrame#bar { background: #FBF8F4; border: 1px solid #D9CCBE; border-radius: 9px; }
QPushButton { background: transparent; border: none; border-radius: 6px; padding: 5px; }
QPushButton:hover { background: #EFE5D9; }
QPushButton:checked { background: #E2701F; }
QPushButton:disabled { opacity: 0.4; }
"""


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _pt(q: QPointF) -> tuple[float, float]:
    return (q.x(), q.y())


class _Bar(QFrame):
    def __init__(self, parent: QWidget, vertical: bool):
        super().__init__(parent)
        self.setObjectName("bar")
        self.setStyleSheet(BAR_QSS)
        self.lay = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        self.lay.setContentsMargins(4, 4, 4, 4)
        self.lay.setSpacing(2)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def add(self, name: str, tip: str, slot=None, checkable: bool = False) -> QPushButton:
        b = QPushButton()
        b.setIcon(icon(name))
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if slot:
            b.clicked.connect(slot)
        self.lay.addWidget(b)
        return b

    def sep(self) -> None:
        line = QFrame()
        line.setFixedSize(QSize(26, 1) if isinstance(self.lay, QVBoxLayout) else QSize(1, 26))
        line.setStyleSheet("background:#E3D8CC;")
        self.lay.addWidget(line)


class _Swatch(QPushButton):
    """Boton que muestra el color o el grosor actual."""

    def __init__(self, overlay: "Overlay", what: str):
        super().__init__()
        self.overlay, self.what = overlay, what
        self.setFixedSize(30, 30)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Color" if what == "color" else "Grosor (o rueda del mouse)")

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.what == "color":
            p.setPen(QPen(QColor("#B8A99B"), 1))
            p.setBrush(QColor(self.overlay.color))
            p.drawEllipse(c, 8, 8)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#35291F"))
            r = min(9.0, 1.5 + self.overlay.width * 0.6)
            p.drawEllipse(c, r, r)
        p.end()


class _Palette(QFrame):
    def __init__(self, overlay: "Overlay"):
        super().__init__(overlay)
        self.setObjectName("bar")
        self.setStyleSheet(BAR_QSS)
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for i, col in enumerate(COLORS):
            b = QPushButton()
            b.setFixedSize(24, 24)
            border = "#B8A99B" if col in ("#FFFFFF", "#FDD835") else col
            b.setStyleSheet(f"QPushButton {{ background:{col}; border:1px solid {border}; border-radius:12px; }}"
                            "QPushButton:hover { border:2px solid #E2701F; }")
            b.clicked.connect(lambda _=False, c=col: overlay.set_color(c))
            grid.addWidget(b, i // 4, i % 4)
        more = QPushButton("Otro…")
        more.setStyleSheet("QPushButton { font: 8pt 'Segoe UI'; color:#35291F; }")
        more.clicked.connect(overlay.pick_color)
        grid.addWidget(more, 2, 0, 1, 4)
        self.hide()


class Overlay(QWidget):
    """Capa a pantalla completa sobre un monitor, con la imagen congelada."""

    def __init__(self, session: "CaptureSession", screen, shot):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        super().__init__(None, flags)
        self.session, self.screen_ref, self.shot = session, screen, shot
        self.setGeometry(screen.geometry())
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.sel: QRectF | None = None
        self.shapes: list[dict] = []
        self.undo_stack: list[list[dict]] = []
        self.redo_stack: list[list[dict]] = []
        self.tool: str | None = None
        self.color = session.color
        self.width = session.width
        self._ortho = None
        self._last_erase = (0.0, 0.0)
        self._blur = None
        self.eraser_r = 12.0
        self.selected = -1
        self.drag: tuple | None = None
        self.mouse = QPointF(-100, -100)
        self.shift_down = False
        self.flash: tuple[str, QPointF, float] | None = None
        self._wheel_last: tuple[int, float] = (-1, 0.0)
        self.editor: QPlainTextEdit | None = None
        self.edit_index = -1
        self._build_bars()

    # ------------------------------------------------------------ barras
    def _build_bars(self) -> None:
        self.tools_bar = _Bar(self, vertical=True)
        self.tool_btns: dict[str, QPushButton] = {}
        for key, ic, tip, _k in TOOLS:
            self.tool_btns[key] = self.tools_bar.add(ic, tip, lambda _=False, k=key: self.set_tool(k), True)
        self.textbg_btn = self.tools_bar.add("textbg", "Texto con fondo (el fondo usa el color elegido)",
                                             self.toggle_text_bg, True)
        self.textbg_btn.setChecked(self.session.text_bg)
        self.tools_bar.sep()
        self.color_btn = _Swatch(self, "color")
        self.color_btn.clicked.connect(self.toggle_palette)
        self.width_btn = _Swatch(self, "width")
        self.width_btn.clicked.connect(self.cycle_width)
        self.tools_bar.lay.addWidget(self.color_btn)
        self.tools_bar.lay.addWidget(self.width_btn)
        self.tools_bar.sep()
        self.undo_btn = self.tools_bar.add("undo", "Deshacer (Ctrl+Z)", self.undo)
        self.redo_btn = self.tools_bar.add("redo", "Rehacer (Ctrl+Y)", self.redo)
        self.del_btn = self.tools_bar.add("trash", "Borrar el objeto elegido (Supr)", self.delete_selected)
        self.actions_bar = _Bar(self, vertical=False)
        self.actions_bar.add("ocr", "Copiar el texto de la zona (Ctrl+T)", self.copy_text)
        self.actions_bar.add("copy", "Copiar y cerrar (Ctrl+C)", self.copy)
        self.actions_bar.add("save", "Guardar (Ctrl+S)", self.save)
        hist = self.actions_bar.add("history", "Historial de capturas (Ctrl+H)", self.go_history)
        hist.setText(" Historial")
        hist.setStyleSheet("QPushButton { color: #35291F; font: 9pt 'Segoe UI'; padding: 5px 8px; }")
        self.actions_bar.sep()
        self.actions_bar.add("close", "Cerrar (Esc)", lambda: self.session.finish("cancelada"))
        self.palette = _Palette(self)
        for w in (self.tools_bar, self.actions_bar):
            w.adjustSize()
            w.hide()

    def place_bars(self) -> None:
        if not self.sel or self.sel.width() < 4 or self.sel.height() < 4:
            self.tools_bar.hide()
            self.actions_bar.hide()
            self.palette.hide()
            return
        r = self.sel.normalized()
        W, H = self.width_px(), self.height_px()
        tb, ab = self.tools_bar.sizeHint(), self.actions_bar.sizeHint()
        x = r.right() + 8 if r.right() + 8 + tb.width() < W else r.left() - 8 - tb.width()
        if x < 0:
            x = r.right() - tb.width() - 8
        y = min(max(r.top(), 4), H - tb.height() - 4)
        self.tools_bar.move(int(x), int(y))
        ay = r.bottom() + 8 if r.bottom() + 8 + ab.height() < H else r.top() - 8 - ab.height()
        if ay < 0:
            ay = r.bottom() - ab.height() - 8
        ax = min(max(r.right() - ab.width(), 4), W - ab.width() - 4)
        self.actions_bar.move(int(ax), int(ay))
        self.tools_bar.show()
        self.actions_bar.show()
        if self.palette.isVisible():
            self._place_palette()
        self.refresh_buttons()

    def width_px(self) -> int:
        return self.geometry().width()

    def height_px(self) -> int:
        return self.geometry().height()

    def _place_palette(self) -> None:
        self.palette.adjustSize()
        g = self.tools_bar.geometry()
        cb = self.color_btn.geometry().translated(g.topLeft())
        x = g.left() - self.palette.width() - 6 if g.left() - self.palette.width() - 6 > 0 else g.right() + 6
        self.palette.move(int(x), int(min(cb.top(), self.height_px() - self.palette.height() - 4)))

    def refresh_buttons(self) -> None:
        for k, b in self.tool_btns.items():
            b.setChecked(k == self.tool)
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.redo_btn.setEnabled(bool(self.redo_stack))
        self.del_btn.setEnabled(0 <= self.selected < len(self.shapes))
        self.color_btn.update()
        self.width_btn.update()

    # ------------------------------------------------------------ ajustes
    def set_tool(self, key: str | None) -> None:
        self.commit_text()
        self.tool = None if key == self.tool else key
        if self.tool not in ("move", None):
            self.selected = -1
        self.palette.hide()
        self.refresh_buttons()
        self._update_cursor(self.mouse)
        self.update()

    def set_color(self, c: str) -> None:
        self.color = self.session.color = c
        if 0 <= self.selected < len(self.shapes):
            self.snapshot()
            self.shapes[self.selected]["color"] = c
        if self.editor:
            self._style_editor()
        self.palette.hide()
        self.refresh_buttons()
        self.update()

    def pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, "Color")
        if c.isValid():
            self.set_color(c.name())

    def toggle_palette(self) -> None:
        if self.palette.isVisible():
            self.palette.hide()
        else:
            self._place_palette()
            self.palette.show()
            self.palette.raise_()

    def set_width(self, w: float) -> None:
        self.width = self.session.width = max(1, min(30, round(w)))
        if 0 <= self.selected < len(self.shapes) and self.shapes[self.selected]["kind"] != "text":
            self.shapes[self.selected]["width"] = self.width
        if self.editor:
            self._style_editor()
        self.refresh_buttons()
        self.update()

    def cycle_width(self) -> None:
        nxt = next((w for w in WIDTHS if w > self.width), WIDTHS[0])
        self.set_width(nxt)

    # ------------------------------------------------------------ historial
    def snapshot(self) -> None:
        self.undo_stack.append(copy.deepcopy(self.shapes))
        self.undo_stack = self.undo_stack[-200:]
        self.redo_stack.clear()

    def undo(self) -> None:
        self.commit_text()
        if self.undo_stack:
            self.redo_stack.append(copy.deepcopy(self.shapes))
            self.shapes = self.undo_stack.pop()
            self.selected = -1
            self.refresh_buttons()
            self.update()

    def redo(self) -> None:
        if self.redo_stack:
            self.undo_stack.append(copy.deepcopy(self.shapes))
            self.shapes = self.redo_stack.pop()
            self.selected = -1
            self.refresh_buttons()
            self.update()

    def delete_selected(self) -> None:
        if 0 <= self.selected < len(self.shapes):
            self.snapshot()
            self.shapes.pop(self.selected)
            self.selected = -1
            self.refresh_buttons()
            self.update()

    # ------------------------------------------------------------ texto
    def _style_editor(self) -> None:
        size = 14 + self.width * 2.2
        self.editor.setFont(S.text_font(size))
        self.editor.setStyleSheet(f"QPlainTextEdit {{ background: rgba(255,255,255,30); color: {self.color};"
                                  f" border: 1px dashed {ORANGE.name()}; padding: 0; }}")
        self._fit_editor()

    def _fit_editor(self) -> None:
        if not self.editor:
            return
        fm = self.editor.fontMetrics()
        lines = self.editor.toPlainText().split("\n")
        w = max(fm.horizontalAdvance(ln) for ln in lines) + fm.horizontalAdvance("MM")
        self.editor.resize(max(60, w), fm.height() * len(lines) + 12)

    def open_editor(self, pos: QPointF, index: int = -1) -> None:
        self.commit_text()
        self.editor = QPlainTextEdit(self)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.edit_index = index
        if index >= 0:
            s = self.shapes[index]
            self.color, self.width = s["color"], round((s["size"] - 14) / 2.2)
            self.editor.setPlainText(s["text"])
            pos = QPointF(*s["pts"][0])
        self._style_editor()
        self.editor.move(int(pos.x()) - 4, int(pos.y()) - 4)
        self.editor.textChanged.connect(self._fit_editor)
        self.editor.installEventFilter(self)
        self.editor.show()
        self.editor.setFocus()
        self.update()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.editor and ev.type() == ev.Type.KeyPress:
            if ev.key() == Qt.Key.Key_Escape:
                self.commit_text(cancel=True)
                return True
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.commit_text()
                return True
        return super().eventFilter(obj, ev)

    def commit_text(self, cancel: bool = False) -> None:
        if not self.editor:
            return
        ed, self.editor = self.editor, None
        text = ed.toPlainText().rstrip()
        pos = (ed.x() + 4, ed.y() + 4)
        size = 14 + self.width * 2.2
        ed.hide()
        ed.deleteLater()
        if cancel:
            self.update()
            return
        if self.edit_index >= 0:
            self.snapshot()
            if text:
                s = self.shapes[self.edit_index]
                s.update({"text": text, "color": self.color, "size": size})
                S.measure_text(s)
            else:
                self.shapes.pop(self.edit_index)
        elif text:
            self.snapshot()
            s = {"kind": "text", "color": self.color, "width": self.width, "pts": [pos], "text": text, "size": size,
                 "bg": self.session.text_bg}
            S.measure_text(s)
            self.shapes.append(s)
        self.edit_index = -1
        self.setFocus()
        self.refresh_buttons()
        self.update()

    # ------------------------------------------------------------ seleccion de zona
    def sel_handles(self) -> dict[str, QPointF]:
        r = self.sel.normalized()
        c = r.center()
        return {"tl": r.topLeft(), "t": QPointF(c.x(), r.top()), "tr": r.topRight(), "r": QPointF(r.right(), c.y()),
                "br": r.bottomRight(), "b": QPointF(c.x(), r.bottom()), "bl": r.bottomLeft(), "l": QPointF(r.left(), c.y())}

    def sel_handle_at(self, pos: QPointF) -> str | None:
        if not self.sel or self.tool not in (None, "move"):
            return None
        for k, q in self.sel_handles().items():
            if _dist(q, pos) <= 8:
                return k
        return None

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, e) -> None:
        pos = e.position()
        self.palette.hide()
        if self.editor and not self.editor.geometry().contains(pos.toPoint()):
            self.commit_text()
            if self.tool == "text":
                return
        self.session.activate(self)
        if e.button() == Qt.MouseButton.RightButton:
            i = S.top_hit(self.shapes, _pt(pos))
            if i >= 0:
                self.snapshot()
                self.selected = i
                self.drag = ("move_shape", pos)
                self.refresh_buttons()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if not self.sel:
            self.drag = ("new_sel", pos)
            self.sel = QRectF(pos, pos)
            return
        handle = self.sel_handle_at(pos)
        if self.tool == "move" or ctrl:
            if 0 <= self.selected < len(self.shapes):
                for hi, q in enumerate(S.handles(self.shapes[self.selected])):
                    if _dist(QPointF(*q), pos) <= 9:
                        self.snapshot()
                        self.drag = ("shape_handle", hi)
                        return
            i = S.top_hit(self.shapes, _pt(pos))
            if i >= 0:
                self.snapshot()
                self.selected = i
                self.drag = ("move_shape", pos)
                self.refresh_buttons()
                self.update()
                return
            self.selected = -1
            self.refresh_buttons()
        if handle:
            self.drag = ("sel_handle", handle)
            return
        if self.tool in (None, "move"):
            if self.sel.normalized().contains(pos) and self.tool is None:
                self.drag = ("move_sel", pos, QRectF(self.sel))
            elif not self.sel.normalized().contains(pos):
                self.drag = ("new_sel", pos)
                self.sel = QRectF(pos, pos)
                self.place_bars()
            self.update()
            return
        if self.tool == "text":
            i = S.top_hit(self.shapes, _pt(pos))
            if i >= 0 and self.shapes[i]["kind"] == "text":
                self.open_editor(pos, i)
            else:
                self.open_editor(pos)
            return
        if self.tool == "eraser":
            self.snapshot()
            self.drag = ("erase",)
            self._ortho = None
            self._erase(pos)
            return
        self.snapshot()
        kind = self.tool
        pts = [_pt(pos)] if kind in ("pen", "marker", "blur") else [_pt(pos), _pt(pos)]
        self.shapes.append({"kind": kind, "color": self.color, "width": self.width, "pts": pts})
        self._ortho = None
        self.drag = ("draw",)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        self.mouse = pos
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier) or self.shift_down
        d = self.drag
        if d is None:
            self._update_cursor(pos)
            if self.tool == "eraser":
                self.update()
            return
        kind = d[0]
        if kind == "new_sel":
            self.sel = QRectF(d[1], pos)
        elif kind == "move_sel":
            start, orig = d[1], d[2]
            r = orig.translated(pos - start)
            r.moveLeft(min(max(r.left(), 0), self.width_px() - r.width()))
            r.moveTop(min(max(r.top(), 0), self.height_px() - r.height()))
            self.sel = r
            self.place_bars()
        elif kind == "sel_handle":
            r = self.sel.normalized()
            h = d[1]
            if "l" in h:
                r.setLeft(pos.x())
            if "r" in h:
                r.setRight(pos.x())
            if "t" in h:
                r.setTop(pos.y())
            if "b" in h:
                r.setBottom(pos.y())
            self.sel = r
            self.place_bars()
        elif kind == "move_shape":
            delta = pos - d[1]
            S.move(self.shapes[self.selected], delta.x(), delta.y())
            self.drag = ("move_shape", pos)
        elif kind == "shape_handle":
            s = self.shapes[self.selected]
            q = _pt(pos)
            if shift and s["kind"] in ("line", "arrow"):
                q = S.ortho(s["pts"][1 - d[1]], q)
            S.set_handle(s, d[1], q)
        elif kind == "erase":
            self._erase(QPointF(*self._constrain(_pt(pos), shift, self._last_erase)))
        elif kind == "draw":
            s = self.shapes[-1]
            if s["kind"] in ("pen", "marker", "blur"):
                s["pts"].append(self._constrain(_pt(pos), shift, s["pts"][-1]))
            else:
                q = _pt(pos)
                if shift:
                    q = S.snap(s["pts"][0], q, square=True) if s["kind"] == "rect" else S.ortho(s["pts"][0], q)
                s["pts"][1] = q
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        d, self.drag = self.drag, None
        if d is None:
            return
        if d[0] in ("new_sel", "sel_handle"):
            self.sel = self.sel.normalized()
            if self.sel.width() < 4 or self.sel.height() < 4:
                self.sel = None
            self.place_bars()
        elif d[0] == "draw":
            s = self.shapes[-1]
            if s["kind"] in ("pen", "marker", "blur"):
                s["pts"] = S.densify(s["pts"])
            else:
                (x0, y0), (x1, y1) = s["pts"]
                if math.hypot(x1 - x0, y1 - y0) < 3:  # un clic suelto no deja figura
                    self.shapes.pop()
                    self.undo_stack.pop()
        elif d[0] == "erase" and self.undo_stack and self.undo_stack[-1] == self.shapes:
            self.undo_stack.pop()  # la goma no toco nada
        elif d[0] in ("move_shape", "shape_handle") and self.undo_stack and self.undo_stack[-1] == self.shapes:
            self.undo_stack.pop()
        self.refresh_buttons()
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        i = S.top_hit(self.shapes, _pt(e.position()))
        if i >= 0 and self.shapes[i]["kind"] == "text":
            self.open_editor(e.position(), i)
        else:
            super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e) -> None:
        """Rueda: grosor. Mientras se dibuja o mueve, cambia ese dibujo; con Ctrl, el que esta bajo el cursor."""
        step = 1 if e.angleDelta().y() > 0 else -1
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        kind = self.drag[0] if self.drag else None
        target = -1
        if kind == "draw":
            target = len(self.shapes) - 1
        elif kind in ("move_shape", "shape_handle"):
            target = self.selected
        elif ctrl:
            target = S.top_hit(self.shapes, _pt(e.position()))
            if target < 0:
                target = self.selected
        elif 0 <= self.selected < len(self.shapes):
            target = self.selected
        if 0 <= target < len(self.shapes):
            self._resize_shape(target, step, new=kind == "draw", in_drag=kind is not None, at=e.position())
        elif self.tool == "eraser":
            self.eraser_r = max(4.0, min(80.0, self.eraser_r + step * 3))
        else:
            self.set_width(self.width + step)
            self._flash(f"Grosor {self.width}", e.position())
        self.update()

    def _resize_shape(self, i: int, step: int, new: bool, in_drag: bool, at: QPointF) -> None:
        s = self.shapes[i]
        now = time.monotonic()
        burst = self._wheel_last[0] == i and now - self._wheel_last[1] < 0.9
        if not in_drag and not burst:  # una vuelta de rueda seguida = un solo Ctrl+Z
            self.snapshot()
        self._wheel_last = (i, now)
        if s["kind"] == "text":
            s["size"] = max(10.0, min(160.0, s["size"] + step * 2))
            S.measure_text(s)
            self._flash(f"Texto {round(s['size'])} px", at)
        else:
            s["width"] = max(1, min(30, s["width"] + step))
            if new:  # lo que se dibuje despues sigue con este grosor
                self.width = self.session.width = s["width"]
            self._flash(f"Grosor {s['width']}", at)
        self.refresh_buttons()

    def _flash(self, text: str, at: QPointF) -> None:
        self.flash = (text, QPointF(at), time.monotonic() + 0.9)
        QTimer.singleShot(950, self.update)

    def _constrain(self, q, shift: bool, start):
        """Con Shift, el trazo sigue solo en horizontal o vertical desde donde se apreto."""
        if not shift:
            self._ortho = None
            return q
        if self._ortho is None:
            self._ortho = [start, None]
        anchor, axis = self._ortho
        dx, dy = q[0] - anchor[0], q[1] - anchor[1]
        if axis is None and max(abs(dx), abs(dy)) > 4:
            axis = self._ortho[1] = "h" if abs(dx) >= abs(dy) else "v"
        if axis == "h":
            return (q[0], anchor[1])
        if axis == "v":
            return (anchor[0], q[1])
        return anchor

    def toggle_text_bg(self) -> None:
        self.session.text_bg = self.textbg_btn.isChecked()
        if 0 <= self.selected < len(self.shapes) and self.shapes[self.selected]["kind"] == "text":
            self.snapshot()
            self.shapes[self.selected]["bg"] = self.session.text_bg
        self.update()

    def blur_ctx(self):
        """Pantalla pixelada (una vez) para los trazos de difuminar."""
        if getattr(self, "_blur", None) is None:
            img = self.shot.toImage()
            k = max(6, round(9 * self.shot.devicePixelRatio()))
            small = img.scaled(max(1, img.width() // k), max(1, img.height() // k),
                               Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._blur = small.scaled(img.width(), img.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                      Qt.TransformationMode.FastTransformation)
        return self._blur, QRectF(self.rect())

    def _erase(self, pos: QPointF) -> None:
        self._last_erase = _pt(pos)
        self.shapes, changed = S.erase(self.shapes, _pt(pos), self.eraser_r)
        if changed:
            self.selected = -1

    def _update_cursor(self, pos: QPointF) -> None:
        h = self.sel_handle_at(pos)
        if h:
            cur = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                   "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                   "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor}.get(h, Qt.CursorShape.SizeHorCursor)
        elif self.tool == "move" and S.top_hit(self.shapes, _pt(pos)) >= 0:
            cur = Qt.CursorShape.SizeAllCursor
        elif self.tool is None and self.sel and self.sel.contains(pos):
            cur = Qt.CursorShape.SizeAllCursor
        elif self.tool == "eraser":
            cur = Qt.CursorShape.BlankCursor
        elif self.tool == "text":
            cur = Qt.CursorShape.IBeamCursor
        else:
            cur = Qt.CursorShape.CrossCursor
        self.setCursor(cur)

    # ------------------------------------------------------------ teclado
    def keyReleaseEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Shift:
            self.shift_down = False
        super().keyReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Shift:
            self.shift_down = True
            return
        if e.matches(QKeySequence.StandardKey.Copy):
            self.copy()
        elif e.matches(QKeySequence.StandardKey.Save):
            self.save()
        elif e.key() == Qt.Key.Key_T and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_text()
        elif e.key() == Qt.Key.Key_H and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.go_history()
        elif e.matches(QKeySequence.StandardKey.Undo):
            self.undo()
        elif e.matches(QKeySequence.StandardKey.Redo) or (
                e.key() == Qt.Key.Key_Y and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.redo()
        elif e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif e.key() == Qt.Key.Key_Escape:
            if self.palette.isVisible():
                self.palette.hide()
            elif self.selected >= 0:
                self.selected = -1
                self.refresh_buttons()
                self.update()
            else:
                self.session.finish("cancelada")
        elif not e.modifiers() and self.sel:
            letter = e.text().upper()
            for key, _ic, _tip, k in TOOLS:
                if letter == k:
                    self.set_tool(key)
                    break
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------ dibujo
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        p.drawPixmap(self.rect(), self.shot)
        dim = QPainterPath()
        dim.addRect(full)
        if self.sel:
            hole = QPainterPath()
            hole.addRect(self.sel.normalized())
            dim = dim.subtracted(hole)
        p.fillPath(dim, QColor(0, 0, 0, 105))
        for i, s in enumerate(self.shapes):
            if i == self.edit_index and self.editor:
                continue
            S.draw_shape(p, s, self.blur_ctx())
        if 0 <= self.selected < len(self.shapes):
            s = self.shapes[self.selected]
            p.setPen(QPen(ORANGE, 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(S.bbox(s).adjusted(-3, -3, 3, 3))
            p.setPen(QPen(QColor("white"), 1.5))
            p.setBrush(ORANGE)
            for q in S.handles(s):
                p.drawEllipse(QPointF(*q), 5, 5)
        if self.sel:
            r = self.sel.normalized()
            p.setPen(QPen(ORANGE, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            if self.tool in (None, "move"):
                p.setPen(QPen(QColor("white"), 1))
                p.setBrush(ORANGE)
                for q in self.sel_handles().values():
                    p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
            dpr = self.shot.devicePixelRatio()
            label = f"{round(r.width() * dpr)} × {round(r.height() * dpr)}"
            self._pill(p, QPointF(r.left(), r.top() - 8), label, anchor_bottom=True)
        tip = ("Arrastra para elegir la zona   ·   Esc para salir" if not self.sel else
               "Clic derecho y arrastra un dibujo para moverlo   ·   Ctrl+rueda sobre él cambia su grosor   ·   "
               "Supr lo borra   ·   Ctrl+Z deshacer   ·   Ctrl+C copiar   ·   Ctrl+S guardar")
        self._pill(p, QPointF(full.center().x(), 14), tip, centered=True)
        if self.flash and time.monotonic() < self.flash[2]:
            self._pill(p, self.flash[1] + QPointF(16, 14), self.flash[0])
        if self.tool == "eraser" and not self.drag or (self.drag and self.drag[0] == "erase"):
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor(0, 0, 0, 40))
            p.drawEllipse(self.mouse, self.eraser_r, self.eraser_r)
            p.setPen(QPen(QColor("black"), 1))
            p.drawEllipse(self.mouse, self.eraser_r + 1.5, self.eraser_r + 1.5)
        p.end()

    def _pill(self, p: QPainter, at: QPointF, text: str, centered: bool = False, anchor_bottom: bool = False) -> None:
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 18
        h = 22
        x = at.x() - w / 2 if centered else at.x()
        y = at.y() - h if anchor_bottom else at.y()
        if y < 2:
            y = at.y() + 12 if anchor_bottom else 2
        box = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(30, 26, 22, 200))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------ resultado
    def render(self) -> QImage:
        """La zona elegida con los dibujos, a resolucion real de la pantalla."""
        self.commit_text()
        dpr = self.shot.devicePixelRatio()
        r = self.sel.normalized()
        src = QRect(round(r.x() * dpr), round(r.y() * dpr), round(r.width() * dpr), round(r.height() * dpr))
        img = self.shot.toImage().copy(src).convertToFormat(QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(1)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.scale(dpr, dpr)
        p.translate(-r.topLeft())
        for s in self.shapes:
            S.draw_shape(p, s, self.blur_ctx())
        p.end()
        return img

    def region_image(self) -> QImage:
        """La zona elegida tal como estaba en pantalla, sin los dibujos."""
        dpr = self.shot.devicePixelRatio()
        r = self.sel.normalized()
        src = QRect(round(r.x() * dpr), round(r.y() * dpr), round(r.width() * dpr), round(r.height() * dpr))
        return self.shot.toImage().copy(src)

    def copy_text(self) -> None:
        """Lee el texto de la zona (OCR) y lo deja en el portapapeles."""
        if not self.sel:
            return
        from .ocr import text_from_pil
        from .panels.base import qimage_to_pil
        self.commit_text()
        self.flash = ("Leyendo texto…", self.sel.normalized().center(), time.monotonic() + 30)
        self.repaint()
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            text = text_from_pil(qimage_to_pil(self.region_image()))
        except Exception as exc:  # noqa: BLE001 - se informa y la captura sigue abierta
            QApplication.restoreOverrideCursor()
            self._flash(f"No pude leer: {exc}", self.sel.normalized().center())
            self.update()
            return
        QApplication.restoreOverrideCursor()
        if not text:
            self._flash("No encontré texto en la zona", self.sel.normalized().center())
            self.update()
            return
        QApplication.clipboard().setText(text)
        self.session.record(self, "texto")
        self.session.finish("texto", len(text.split()))

    def go_history(self) -> None:
        """Cierra la captura y abre el historial (si habia dibujos, quedan guardados igual)."""
        self.session.finish("historial")

    def copy(self) -> None:
        if not self.sel:
            return
        img = self.render()
        QApplication.clipboard().setImage(img)
        self.session.record(self, "copiada", img)
        self.session.finish("copiada")

    def save(self) -> None:
        if not self.sel:
            return
        img = self.render()
        folder = self.session.save_dir()
        name = datetime.now().strftime("Captura %Y-%m-%d %H.%M.%S.png")
        path, _ = QFileDialog.getSaveFileName(self, "Guardar captura", str(folder / name),
                                              "PNG (*.png);;JPG (*.jpg)")
        if not path:
            self.setFocus()
            return
        out = Path(path)
        img.save(str(out), "JPG" if out.suffix.lower() in (".jpg", ".jpeg") else "PNG", 95)
        self.session.record(self, "guardada", img)
        self.session.remember_dir(out.parent)
        self.session.finish("guardada", out)


class CaptureSession(QObject):
    """Congela todos los monitores y abre una capa en cada uno."""
    done = Signal(str, object)  # "copiada" | "guardada" | "cancelada", ruta

    def __init__(self, settings: dict, save_settings, shots: list | None = None, restore: dict | None = None):
        super().__init__()
        self.settings, self.save_settings = settings, save_settings
        self.color = settings.get("captura_color", COLORS[0])
        self.width = settings.get("captura_grosor", 4)
        self.text_bg = bool(settings.get("captura_texto_fondo", False))
        self.active: Overlay | None = None
        self.save_thread = None
        self.restore_id: str | None = None
        self.restore_k = 1.0
        self.restore_dpr: float | None = None
        if restore:
            shots, place = self._restore_shot(restore)
        screens = QGuiApplication.screens()
        shots = shots or [(sc, sc.grabWindow(0)) for sc in screens]  # se congela antes de mostrar nada
        self.overlays = [Overlay(self, sc, pix) for sc, pix in shots]
        self.closed = False
        if restore:
            self._restore_drawings(restore, *place)

    # ------------------------------------------------------------ volver a editar desde el historial
    def _restore_shot(self, entry: dict):
        """Arma una 'pantalla' con la zona guardada al centro, para seguir editandola."""
        from PySide6.QtGui import QPixmap
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio()
        geo = screen.geometry()
        region = QImage(str(entry["original"]))
        rdpr = float(entry.get("dpr") or 1.0)
        lw, lh = region.width() / rdpr, region.height() / rdpr
        k = min(1.0, 0.9 * geo.width() / lw, 0.85 * geo.height() / lh)
        dw, dh = lw * k, lh * k
        x0, y0 = (geo.width() - dw) / 2, (geo.height() - dh) / 2
        comp = QPixmap(round(geo.width() * dpr), round(geo.height() * dpr))
        comp.fill(QColor(48, 45, 42))
        p = QPainter(comp)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(x0 * dpr, y0 * dpr, dw * dpr, dh * dpr), region)
        p.end()
        comp.setDevicePixelRatio(dpr)
        self.restore_id, self.restore_k, self.restore_dpr = entry["id"], k, rdpr
        return [(screen, comp)], (x0, y0, dw, dh, k)

    def _restore_drawings(self, entry: dict, x0: float, y0: float, dw: float, dh: float, k: float) -> None:
        o = self.overlays[0]
        o.sel = QRectF(x0, y0, dw, dh)
        o.shapes = [self._scale_shape(s, k, x0, y0) for s in entry.get("dibujos", [])]
        self.active = o
        o.place_bars()

    @staticmethod
    def _scale_shape(s: dict, k: float, dx: float, dy: float) -> dict:
        s = copy.deepcopy(s)
        s["pts"] = [(x * k + dx, y * k + dy) for x, y in s["pts"]]
        s["width"] = max(1, round(s["width"] * k)) if k < 1 else s["width"]
        if s["kind"] == "text":
            s["size"] = s["size"] * k
            S.measure_text(s)
        return s

    def record(self, o: Overlay, action: str, rendered: QImage | None = None) -> None:
        """Guarda la captura en el historial (en otro hilo, para no demorar el cierre)."""
        if not o.sel:
            return
        o.commit_text()
        r = o.sel.normalized()
        k = self.restore_k
        shapes = [self._scale_shape(s, 1 / k, 0, 0) for s in
                  (self._scale_shape(s, 1.0, -r.x(), -r.y()) for s in o.shapes)]
        rendered = (rendered or o.render()).copy()
        dpr = self.restore_dpr or o.shot.devicePixelRatio()
        self.save_thread = history.save_async(rendered, o.region_image().copy(), shapes, dpr, action,
                                              entry_id=self.restore_id)

    def show(self) -> None:
        from .panels.base import bring_to_front
        for o in self.overlays:
            o.show()
        here = QGuiApplication.screenAt(QCursor.pos())
        main = next((o for o in self.overlays if o.screen_ref is here), self.overlays[0])
        bring_to_front(main)
        main.setFocus()

    def activate(self, overlay: Overlay) -> None:
        """Al empezar en un monitor, se limpia la seleccion de los otros."""
        self.active = overlay
        for o in self.overlays:
            if o is not overlay and o.sel is not None:
                o.sel = None
                o.place_bars()
                o.update()

    def save_dir(self) -> Path:
        d = Path(self.settings.get("captura_carpeta") or Path.home() / "Pictures")
        return d if d.exists() else Path.home()

    def remember_dir(self, d: Path) -> None:
        self.settings["captura_carpeta"] = str(d)

    def finish(self, result: str, path: Path | None = None) -> None:
        if self.closed:
            return
        o = self.active
        if result in ("cancelada", "historial") and o is not None and o.sel and o.shapes:
            self.record(o, "descartada")  # se cerro con dibujos: se guardan igual, por si acaso
            if result == "historial" and self.save_thread:
                self.save_thread.join(5)  # que ya aparezca en el historial que se va a abrir
        self.closed = True
        self.settings["captura_color"], self.settings["captura_grosor"] = self.color, self.width
        self.settings["captura_texto_fondo"] = self.text_bg
        self.save_settings()
        for o in self.overlays:
            o.hide()
            o.deleteLater()
        self.done.emit(result, path)
