"""Paneles de imagen: editar, anotar, fondo, recortar, ocultar, collage, comprimir y metadatos."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget,
)

from .. import editing as ED
from ..engines import open_image
from ..tools import GajoError
from .base import (
    IntField, Panel, ReorderList, Segmented, SliderRow, Swatches, busy, button, hint, pil_to_qimage, section,
)
from .canvas import BoxesTool, CropTool, ImageCanvas


def _preview(path: Path, side: int):
    im = open_image(path)
    full = im.size
    small = im.copy()
    small.thumbnail((side, side))
    return small, full


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}".replace(",", ".") if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} GB"


# ================================================================= editar foto

EDIT_SLIDERS = [
    ("Luz", [("exposure", "Exposición", -300, 300, lambda v: f"{v / 100:+.2f} EV"),
             ("brightness", "Brillo", -100, 100, None), ("contrast", "Contraste", -100, 100, None),
             ("highlights", "Luces altas", -100, 100, None), ("shadows", "Sombras", -100, 100, None),
             ("whites", "Blancos", -100, 100, None), ("blacks", "Negros", -100, 100, None),
             ("gamma", "Gamma", -100, 100, None)]),
    ("Color", [("temperature", "Temperatura", -100, 100, None), ("tint", "Matiz verde/magenta", -100, 100, None),
               ("saturation", "Saturación", -100, 100, None), ("vibrance", "Intensidad", -100, 100, None),
               ("hue", "Tono", -180, 180, lambda v: f"{v:+d}°")]),
    ("Detalle", [("clarity", "Claridad", -100, 100, None), ("dehaze", "Quitar neblina", -100, 100, None),
                 ("sharpness", "Nitidez", 0, 100, lambda v: f"{v}"), ("noise", "Reducir ruido", 0, 100, lambda v: f"{v}"),
                 ("grain", "Grano", 0, 100, lambda v: f"{v}")]),
]


class EditPhoto(Panel):
    TITLE = "Editar foto"
    ACTION = "Guardar copia editada"

    def build(self) -> None:
        small, _ = _preview(self.paths[0], 1000)
        self.rgb, self.alpha = ED.to_array(small)
        self.original = pil_to_qimage(small)
        self.canvas = ImageCanvas(300)
        self.canvas.set_image(self.original)
        self.body.addWidget(self.canvas, 2)
        before = button("Mantén apretado para ver el original")
        before.pressed.connect(lambda: self.canvas.set_image(self.original))
        before.released.connect(self.render)
        self.body.addWidget(before)
        lay = self.scroll_area(300)
        self.sliders: dict[str, SliderRow] = {}
        for title, rows in EDIT_SLIDERS:
            lay.addWidget(section(title))
            for key, label, lo, hi, fmt in rows:
                s = SliderRow(label, lo, hi, 0, fmt or (lambda v: f"{v:+d}"), 118)
                s.changed.connect(self.schedule)
                self.sliders[key] = s
                lay.addWidget(s)
            if title == "Color":
                self.bw = QCheckBox("Blanco y negro")
                self.bw.toggled.connect(self.schedule)
                lay.addWidget(self.bw)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.render)

    def params(self) -> dict:
        p = {k: s.value() for k, s in self.sliders.items()}
        p["bw"] = 1 if self.bw.isChecked() else 0
        return p

    def schedule(self) -> None:
        self.timer.start()

    def render(self) -> None:
        out = ED.apply_edits(self.rgb, self.params())
        self.canvas.set_image(pil_to_qimage(ED.from_array(out, self.alpha)))

    def reset(self) -> None:
        for s in self.sliders.values():
            s.blockSignals(True)
            s.reset()
            s.blockSignals(False)
        self.bw.setChecked(False)
        self.render()

    def make_job(self):
        p = self.params()
        if not any(p.values()):
            raise GajoError("Mueve al menos un ajuste")
        src = self.paths[0]
        return lambda: ED.edit_photo(src, p)


# ================================================================= anotar

ANN_TOOLS = [
    ("arrow", "↗", "Flecha", "Arrastra desde la cola hacia la punta. Arrastra una anotación para moverla."),
    ("pen", "✎", "Lápiz", "Dibuja a mano alzada."),
    ("text", "Aa", "Texto", "Haz clic donde quieres el texto."),
    ("rect", "▭", "Rectángulo", "Arrastra para dibujar un rectángulo."),
    ("ellipse", "◯", "Elipse", "Arrastra para dibujar una elipse."),
    ("highlight", "▬", "Resaltador", "Pinta encima para destacar."),
    ("number", "①", "Número", "Haz clic para poner números correlativos."),
    ("magnifier", "⊕", "Lupa", "Arrastra desde el centro hacia afuera para ampliar una zona."),
    ("image", "▣", "Imagen", "Elige una imagen y arrástrala para ubicarla."),
]
ANN_COLORS = ["#E53935", "#FB8C00", "#FDD835", "#43A047", "#1E88E5", "#111111", "#FFFFFF"]


class AnnotateTool:
    def __init__(self, panel: "AnnotatePhoto"):
        self.panel = panel
        self.anns: list[dict] = []
        self.history: list[list[dict]] = []
        self.kind = "arrow"
        self.color = ANN_COLORS[0]
        self.selected = -1
        self.base: QImage | None = None
        self._drag = None

    def _snapshot(self) -> None:
        self.history.append(copy.deepcopy(self.anns))
        self.history = self.history[-50:]

    def undo(self) -> None:
        if self.history:
            self.anns = self.history.pop()
            self.selected = -1

    def delete_selected(self) -> None:
        if 0 <= self.selected < len(self.anns):
            self._snapshot()
            self.anns.pop(self.selected)
            self.selected = -1

    def paint(self, p: QPainter, r: QRectF) -> None:
        diag = math.hypot(r.width(), r.height())
        p.save()
        p.setClipRect(r)
        for a in self.anns:
            ED.draw_annotation(p, a, r.width(), r.height(), diag, self.base, r.left(), r.top())
        if 0 <= self.selected < len(self.anns):
            x0, y0, x1, y1 = self._bbox(self.anns[self.selected])
            pen = QPen(QColor("#E2701F"), 1.2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(r.left() + x0 * r.width(), r.top() + y0 * r.height(),
                              (x1 - x0) * r.width(), (y1 - y0) * r.height()))
        p.restore()

    def _bbox(self, a: dict) -> tuple:
        pts = a.get("pts", [])
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        if a["kind"] == "text":
            ratio = self.base.height() / max(self.base.width(), 1) if self.base else 1
            w = len(a.get("text", "")) * a.get("size", 0.045) * 0.6 * ratio
            return xs[0], ys[0] - a.get("size", 0.045), xs[0] + w, ys[0] + 0.01
        if a["kind"] == "number":
            return xs[0] - 0.03, ys[0] - 0.03, xs[0] + 0.03, ys[0] + 0.03
        if a["kind"] == "magnifier":
            rad = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
            return xs[0] - rad, ys[0] - rad, xs[0] + rad, ys[0] + rad
        m = 0.015
        return min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m

    def _hit(self, n: tuple) -> int:
        for i in range(len(self.anns) - 1, -1, -1):
            x0, y0, x1, y1 = self._bbox(self.anns[i])
            if x0 <= n[0] <= x1 and y0 <= n[1] <= y1:
                return i
        return -1

    def press(self, canvas, pos: QPointF) -> None:
        n = canvas.to_norm(pos)
        if self.kind not in ("pen", "highlight"):
            i = self._hit(n)
            if i >= 0:
                self._snapshot()
                self.selected = i
                self._drag = ("move", n, copy.deepcopy(self.anns[i]["pts"]))
                return
        self.selected = -1
        if self.kind == "text":
            text, ok = QInputDialog.getText(self.panel, "Texto", "Escribe el texto:")
            if ok and text.strip():
                self._snapshot()
                self.anns.append({"kind": "text", "pts": [n], "text": text.strip(), "color": self.color, "size": 0.045})
            self.panel.refresh_buttons()
            return
        if self.kind == "number":
            self._snapshot()
            count = sum(1 for a in self.anns if a["kind"] == "number")
            self.anns.append({"kind": "number", "pts": [n], "n": count + 1, "color": self.color})
            self.panel.refresh_buttons()
            return
        ann = {"kind": self.kind, "color": self.color, "pts": [n] if self.kind in ("pen", "highlight") else [n, n]}
        if self.kind == "image":
            path, _ = QFileDialog.getOpenFileName(self.panel, "Elegir imagen", str(self.panel.paths[0].parent),
                                                  "Imágenes (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
            if not path:
                return
            ann["path"] = path
        self._snapshot()
        self.anns.append(ann)
        self._drag = ("new", n, None)

    def move(self, canvas, pos: QPointF, pressed: bool) -> None:
        if not pressed or not self._drag:
            canvas.setCursor(Qt.CursorShape.CrossCursor)
            return
        n = canvas.to_norm(pos)
        mode, start, orig = self._drag
        if mode == "move":
            dx, dy = n[0] - start[0], n[1] - start[1]
            self.anns[self.selected]["pts"] = [(x + dx, y + dy) for x, y in orig]
        else:
            a = self.anns[-1]
            if a["kind"] in ("pen", "highlight"):
                a["pts"].append(n)
            else:
                a["pts"][1] = n

    def release(self, canvas, pos: QPointF) -> None:
        if self._drag and self._drag[0] == "new":
            a = self.anns[-1]
            (x0, y0), (x1, y1) = a["pts"][0], a["pts"][-1]
            small = math.hypot(x1 - x0, y1 - y0) < 0.01
            if a["kind"] == "image" and small:
                pic = QImage(a["path"])
                ratio = (pic.height() / max(pic.width(), 1)) * (self.base.width() / max(self.base.height(), 1))
                a["pts"] = [(x0, y0), (min(x0 + 0.3, 1.0), y0 + 0.3 * ratio)]
            elif a["kind"] == "magnifier" and small:
                a["pts"] = [(x0, y0), (x0 + 0.08, y0)]
            elif small and a["kind"] not in ("pen", "highlight"):
                self.anns.pop()
                self.history.pop()
        self._drag = None
        self.panel.refresh_buttons()


class AnnotatePhoto(Panel):
    TITLE = "Anotar foto"
    ACTION = "Guardar copia anotada"

    def build(self) -> None:
        small, _ = _preview(self.paths[0], 1600)
        self.canvas = ImageCanvas(380)
        self.tool = AnnotateTool(self)
        self.tool.base = pil_to_qimage(small)
        self.canvas.set_image(self.tool.base)
        self.canvas.tool = self.tool
        self.body.addWidget(self.canvas, 1)
        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.group = QButtonGroup(self)
        for key, _glyph, name, _ in ANN_TOOLS:
            b = button("", role="tool", tip=name, icon_name=key)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            self.group.addButton(b)
            tools.addWidget(b)
            if key == "arrow":
                b.setChecked(True)
        self.body.addLayout(tools)
        self.swatches = Swatches(ANN_COLORS, ANN_COLORS[0])
        self.swatches.changed.connect(self.set_color)
        self.undo_btn = button("Deshacer", self.undo, icon_name="undo")
        self.del_btn = button("Borrar", self.delete, icon_name="trash")
        self.row(self.swatches, None, self.undo_btn, self.del_btn, stretch_at=0)
        self.tip = hint(ANN_TOOLS[0][3])
        self.body.addWidget(self.tip)
        self.refresh_buttons()

    def set_tool(self, key: str) -> None:
        self.tool.kind = key
        self.tool.selected = -1
        self.tip.setText(next(t[3] for t in ANN_TOOLS if t[0] == key))
        self.canvas.update()

    def set_color(self, color: str) -> None:
        self.tool.color = color
        if 0 <= self.tool.selected < len(self.tool.anns):
            self.tool.anns[self.tool.selected]["color"] = color
        self.canvas.update()

    def undo(self) -> None:
        self.tool.undo()
        self.canvas.update()
        self.refresh_buttons()

    def delete(self) -> None:
        self.tool.delete_selected()
        self.canvas.update()
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        self.undo_btn.setEnabled(bool(self.tool.history))
        self.del_btn.setEnabled(self.tool.selected >= 0)

    def reset(self) -> None:
        if self.tool.anns:
            self.tool._snapshot()
        self.tool.anns, self.tool.selected = [], -1
        self.canvas.update()
        self.refresh_buttons()

    def make_job(self):
        if not self.tool.anns:
            raise GajoError("Agrega al menos una anotación")
        anns, src = copy.deepcopy(self.tool.anns), self.paths[0]
        return lambda: ED.annotate_image(src, anns)


# ================================================================= fondo

class BgPicker(QWidget):
    """Degradados, colores solidos y una imagen propia como fondo."""

    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        self.value = {"type": "gradient", "colors": ED.GRADIENTS[0][1]}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(5)
        self.group = QButtonGroup(self)
        for i, (name, cols) in enumerate(ED.GRADIENTS):
            stops = ", ".join(f"stop:{k / max(len(cols) - 1, 1):.2f} {c}" for k, c in enumerate(cols))
            self._add(grid, 0, i, f"qlineargradient(x1:0, y1:0, x2:1, y2:1, {stops})", name,
                      {"type": "gradient", "colors": cols}, checked=i == 0)
        for i, c in enumerate(ED.SOLIDS):
            self._add(grid, 1, i, c, c, {"type": "solid", "color": c})
        pick = button("Imagen…", self.pick_image, tip="Usar otra imagen como fondo")
        grid.addWidget(pick, 0, len(ED.GRADIENTS), 2, 1)

    def _add(self, grid, r, c, css, tip, value, checked=False) -> None:
        b = QPushButton()
        b.setCheckable(True)
        b.setFixedSize(28, 28)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"QPushButton {{ background: {css}; border: 1px solid #D8CBBD; border-radius: 7px; padding: 0; }}"
                        "QPushButton:checked { border: 3px solid #E2701F; }")
        b.setChecked(checked)
        b.clicked.connect(lambda _=False, v=value: self._set(v))
        self.group.addButton(b)
        grid.addWidget(b, r, c)

    def _set(self, v: dict) -> None:
        self.value = v
        self.on_change()

    def pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Imagen de fondo", "", "Imágenes (*.png *.jpg *.jpeg *.webp *.heic *.bmp)")
        if path:
            checked = self.group.checkedButton()
            if checked:
                self.group.setExclusive(False)
                checked.setChecked(False)
                self.group.setExclusive(True)
            self._set({"type": "image", "path": path})


class AddBackground(Panel):
    TITLE = "Agregar fondo"
    ACTION = "Guardar con fondo"

    def build(self) -> None:
        self.small, full = _preview(self.paths[0], 700)
        self.full_side = max(full)
        self.canvas = ImageCanvas(300)
        self.body.addWidget(self.canvas, 1)
        self.body.addWidget(section("Fondo"))
        self.bg = BgPicker(self.schedule)
        self.body.addWidget(self.bg)
        px = lambda v: f"{round(v / 100 * self.full_side)} px"  # noqa: E731
        self.pad = SliderRow("Margen", 0, 40, 10, px)
        self.corner = SliderRow("Esquinas", 0, 20, 3, px)
        self.shadow = SliderRow("Sombra", 0, 10, 3, px)
        for s in (self.pad, self.corner, self.shadow):
            s.changed.connect(self.schedule)
            self.body.addWidget(s)
        self.ratio = Segmented([("auto", "Auto"), ("1:1", "1:1"), ("16:9", "16:9"), ("4:5", "4:5"), ("9:16", "9:16")])
        self.ratio.changed.connect(lambda _k: self.schedule())
        self.row(QLabel("Proporción"), None, self.ratio)
        if len(self.paths) > 1:
            self.body.addWidget(hint(f"Se aplica igual a las {len(self.paths)} imágenes."))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.render)
        self.render()

    def schedule(self) -> None:
        self.timer.start()

    def opts(self) -> tuple:
        return (self.bg.value, self.pad.value() / 100, self.corner.value() / 100, self.shadow.value() / 100,
                self.ratio.value())

    def render(self) -> None:
        bg, pk, ck, sk, ratio = self.opts()
        side = max(self.small.size)
        try:
            res = ED.compose_background(self.small, bg, round(pk * side), round(ck * side), round(sk * side), ratio)
        except Exception as exc:  # noqa: BLE001 - imagen de fondo ilegible
            self.status.setText(f"No se pudo usar el fondo: {exc}")
            return
        self.status.setText("")
        self.canvas.set_image(pil_to_qimage(res))

    def reset(self) -> None:
        for s in (self.pad, self.corner, self.shadow):
            s.reset()
        self.ratio.set_value("auto")
        self.render()

    def make_job(self):
        bg, pk, ck, sk, ratio = self.opts()
        paths = list(self.paths)
        return lambda: ED.add_background(paths, bg, pk, ck, sk, ratio)


# ================================================================= recortar

class CropImage(Panel):
    TITLE = "Recortar imagen"
    ACTION = "Aplicar"

    def build(self) -> None:
        self.small, self.full = _preview(self.paths[0], 1400)
        self.turns, self.flip = 0, False
        self.canvas = ImageCanvas(340)
        self.tool = CropTool(self.sync_spins)
        self.canvas.tool = self.tool
        self.body.addWidget(self.canvas, 1)
        rot = QHBoxLayout()
        rot.addWidget(button("90°", lambda: self.rotate(-1), tip="Girar a la izquierda", icon_name="rot_left"))
        rot.addWidget(button("90°", lambda: self.rotate(1), tip="Girar a la derecha", icon_name="rot_right"))
        rot.addWidget(button("Voltear", self.mirror, tip="Espejo horizontal", icon_name="flip"))
        rot.addStretch()
        self.body.addLayout(rot)
        self.aspect = Segmented([("libre", "Libre"), ("1:1", "1:1"), ("16:9", "16:9"), ("9:16", "9:16"),
                                 ("4:3", "4:3"), ("3:4", "3:4")])
        self.aspect.changed.connect(self.set_aspect)
        self.row(QLabel("Proporción"), None, self.aspect)
        self.w_spin, self.h_spin = IntField(1, 30000), IntField(1, 30000)
        for s in (self.w_spin, self.h_spin):
            s.editingFinished.connect(self.exact_size)
        self.exact = None
        self.row(QLabel("Ancho"), self.w_spin, QLabel("Alto"), self.h_spin, QLabel("px"), None)
        if len(self.paths) > 1:
            self.body.addWidget(hint(f"El mismo recorte (en proporción) se aplica a las {len(self.paths)} imágenes."))
        self.refresh_image()

    def real_size(self) -> tuple[int, int]:
        w, h = self.full
        return (h, w) if self.turns % 2 else (w, h)

    def refresh_image(self) -> None:
        img = ED._transform(self.small, self.turns, self.flip)
        self.canvas.set_image(pil_to_qimage(img))
        self.tool.img_ratio = img.width / img.height
        self.set_aspect(self.aspect.value())

    def rotate(self, d: int) -> None:
        self.turns = (self.turns + d) % 4
        self.refresh_image()

    def mirror(self) -> None:
        self.flip = not self.flip
        self.refresh_image()

    def set_aspect(self, key: str) -> None:
        self.exact = None
        if key == "libre":
            self.tool.aspect = None
            self.tool.rect = [0.0, 0.0, 1.0, 1.0]
            self.sync_spins()
        else:
            a, b = (int(v) for v in key.split(":"))
            self.tool.set_aspect(a / b)
        self.canvas.update()

    def sync_spins(self) -> None:
        rw, rh = self.real_size()
        _, _, w, h = self.tool.rect
        for spin, v in ((self.w_spin, w * rw), (self.h_spin, h * rh)):
            if not spin.hasFocus():
                spin.blockSignals(True)
                spin.setValue(max(1, round(v)))
                spin.blockSignals(False)

    def exact_size(self) -> None:
        w, h = self.w_spin.value(), self.h_spin.value()
        if w < 1 or h < 1:
            return
        self.tool.set_aspect(w / h)
        self.exact = (w, h)
        self.canvas.update()

    def reset(self) -> None:
        self.turns, self.flip = 0, False
        self.aspect.set_value("libre")
        self.refresh_image()

    def make_job(self):
        rect, size = tuple(self.tool.rect), self.exact
        paths, turns, flip = list(self.paths), self.turns, self.flip
        return lambda: ED.crop_image(paths, rect, size, turns, flip)


# ================================================================= ocultar

class RedactPhoto(Panel):
    TITLE = "Ocultar en foto"
    ACTION = "Guardar copia oculta"

    def build(self) -> None:
        self.small, _ = _preview(self.paths[0], 1400)
        self.canvas = ImageCanvas(360)
        self.tool = BoxesTool(self.canvas.update)
        self.canvas.tool = self.tool
        self.canvas.set_image(pil_to_qimage(self.small))
        self.body.addWidget(self.canvas, 1)
        self.style = Segmented([("solid", "Sólido"), ("blur", "Desenfoque")])
        self.style.changed.connect(self.set_style)
        self.row(QLabel("Estilo"), None, self.style)
        self.colors = Swatches(["#000000", "#FFFFFF", "#808080", "#E2701F"], "#000000")
        self.colors.changed.connect(self.set_color)
        self.row(QLabel("Color"), None, self.colors)
        self.row(QLabel("Detectar"), None, button("Caras", self.faces), button("Texto", self.text),
                 button("Borrar selección", self.delete))
        self.body.addWidget(hint("Arrastra sobre la foto para tapar una zona. Arrastra una caja para moverla "
                                 "o desde sus esquinas para ajustarla. La detección funciona en este PC."))

    def set_style(self, k: str) -> None:
        self.tool.style = k
        self.canvas.update()

    def set_color(self, c: str) -> None:
        self.tool.color = c
        self.canvas.update()

    def _add(self, boxes: list, what: str) -> None:
        self.tool.boxes += [list(b) for b in boxes]
        self.status.setText(f"{len(boxes)} {what} encontrad{'as' if what == 'caras' else 'os'}")
        self.canvas.update()

    def faces(self) -> None:
        with busy():
            try:
                self._add(ED.detect_faces(open_image(self.paths[0])), "caras")
            except GajoError as exc:
                self.warn(str(exc))

    def text(self) -> None:
        with busy():
            self._add(ED.detect_text(open_image(self.paths[0])), "textos")

    def delete(self) -> None:
        self.tool.delete_selected()

    def reset(self) -> None:
        self.tool.boxes, self.tool.selected = [], -1
        self.status.setText("")
        self.canvas.update()

    def make_job(self):
        if not self.tool.boxes:
            raise GajoError("Marca al menos una zona para ocultar")
        boxes = [tuple(b[:4]) for b in self.tool.boxes]
        src, style, color = self.paths[0], self.tool.style, self.tool.color
        return lambda: ED.redact_image(src, boxes, style, color)


# ================================================================= collage

class CollagePanel(Panel):
    TITLE = "Crear collage"
    ACTION = "Crear collage"

    def build(self) -> None:
        self.smalls = {}
        for p in self.paths:
            im = open_image(p)
            im.thumbnail((500, 500))
            self.smalls[p] = im
        self.canvas = ImageCanvas(260)
        self.body.addWidget(self.canvas, 1)
        lay = self.scroll_area(330)
        self.style = QComboBox()
        for key, text in (("grilla", "Cuadrícula"), ("fila", "Fila"), ("columna", "Columna"), ("destacada", "Destacada")):
            self.style.addItem(text, key)
        self.cols = QSpinBox()
        self.cols.setRange(1, 10)
        self.cols.setValue(max(1, math.ceil(math.sqrt(len(self.paths)))))
        self.ratio = Segmented([("auto", "Auto"), ("1:1", "1:1"), ("4:5", "4:5"), ("16:9", "16:9")], "1:1")
        self.width_box = QComboBox()
        for w in (1080, 2048, 3000, 4096):
            self.width_box.addItem(f"{w} px", w)
        self.width_box.setCurrentIndex(1)
        self.fit = Segmented([("llenar", "Llenar (recorta)"), ("completa", "Imagen completa")])
        self.spacing = SliderRow("Espaciado", 0, 80, 16, lambda v: f"{v} px")
        self.corners = SliderRow("Esquinas", 0, 80, 0, lambda v: f"{v} px")
        self.bg = Swatches(["#FFFFFF", "#F3ECE4", "#111111", "#E2701F", "#2476AE", "#4FA372"], "#FFFFFF")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        for r, (label, w) in enumerate((("Estilo", self.style), ("Columnas", self.cols), ("Lienzo", self.ratio),
                                         ("Ancho", self.width_box), ("Ajuste", self.fit))):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(w, r, 1, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(grid)
        lay.addWidget(section("Espaciado y fondo"))
        lay.addWidget(self.spacing)
        lay.addWidget(self.corners)
        lay.addWidget(self.bg)
        lay.addWidget(section("Orden de las imágenes"))
        self.order = ReorderList(self.paths, lambda p: QPixmap.fromImage(pil_to_qimage(self.smalls[p])))
        lay.addWidget(self.order)
        for sig in (self.style.currentIndexChanged, self.cols.valueChanged, self.ratio.changed,
                    self.width_box.currentIndexChanged, self.fit.changed, self.spacing.changed,
                    self.corners.changed, self.bg.changed, self.order.changed):
            sig.connect(lambda *_: self.timer.start())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.render)
        self.render()

    def opts(self, width: int | None = None) -> dict:
        real_w = self.width_box.currentData()
        w = width or real_w
        k = w / 2048  # espaciado y esquinas estan pensados para 2048 px
        return {"style": self.style.currentData(), "cols": self.cols.value(), "ratio": self.ratio.value(),
                "width": w, "fit": self.fit.value(), "spacing": round(self.spacing.value() * k),
                "corners": round(self.corners.value() * k), "bg": self.bg.value()}

    def render(self) -> None:
        self.cols.setEnabled(self.style.currentData() == "grilla")
        sheet = ED.render_collage([self.smalls[p] for p in self.order.paths], **self.opts(700))
        self.canvas.set_image(pil_to_qimage(sheet))

    def make_job(self):
        order, opts = list(self.order.paths), self.opts()
        return lambda: ED.collage(order, **opts)


# ================================================================= comprimir

class CompressImages(Panel):
    TITLE = "Comprimir imágenes"
    ACTION = "Comprimir"
    WIDTH = 460

    def build(self) -> None:
        total = sum(p.stat().st_size for p in self.paths)
        n = len(self.paths)
        self.body.addWidget(QLabel(f"{n} {'imágenes' if n > 1 else 'imagen'} · {_human(total)} en total"))
        self.mode = Segmented([("calidad", "Calidad"), ("peso", "Peso objetivo"), ("porcentaje", "Reducir %")])
        self.mode.changed.connect(self.show_mode)
        self.body.addWidget(self.mode)
        self.preset = Segmented([("85", "Alta"), ("72", "Equilibrada"), ("55", "Liviana")], "72")
        self.target = QDoubleSpinBox()
        self.target.setRange(0.01, 500)
        self.target.setDecimals(2)
        self.target.setValue(0.5)
        self.unit = QComboBox()
        self.unit.addItems(["MB", "KB"])
        self.target_row = QWidget()
        h = QHBoxLayout(self.target_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Cada imagen de máximo"))
        h.addWidget(self.target)
        h.addWidget(self.unit)
        h.addStretch()
        self.percent = SliderRow("Reducir", 10, 90, 50, lambda v: f"{v} %")
        for w in (self.preset, self.target_row, self.percent):
            self.body.addWidget(w)
        self.size = QComboBox()
        for label, v in (("Mismo tamaño", 0), ("3840 px", 3840), ("2560 px", 2560), ("1920 px", 1920),
                         ("1280 px", 1280), ("800 px", 800)):
            self.size.addItem(label, v)
        self.row(QLabel("Lado mayor"), self.size, None)
        self.estimate = hint("", wrap=False)
        self.body.addWidget(self.estimate)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.update_estimate)
        for sig in (self.preset.changed, self.target.valueChanged, self.unit.currentIndexChanged,
                    self.percent.changed, self.size.currentIndexChanged):
            sig.connect(lambda *_: self.timer.start())
        self.show_mode("calidad")

    def show_mode(self, key: str) -> None:
        self.preset.setVisible(key == "calidad")
        self.target_row.setVisible(key == "peso")
        self.percent.setVisible(key == "porcentaje")
        self.timer.start()

    def params(self) -> tuple:
        mode = self.mode.value()
        if mode == "calidad":
            value = float(self.preset.value())
        elif mode == "peso":
            value = self.target.value() * (1024 * 1024 if self.unit.currentText() == "MB" else 1024)
        else:
            value = float(self.percent.value())
        return mode, value, self.size.currentData()

    def update_estimate(self) -> None:
        src = self.paths[0]
        mode, value, side = self.params()
        try:
            with busy():
                from PIL import Image
                from ..tools import norm_ext
                im = open_image(src)
                if side:
                    im.thumbnail((side, side), Image.Resampling.LANCZOS)
                ext = norm_ext(src)
                fmt = ext if ext in ("jpg", "webp", "heic", "avif", "png") else "jpg"
                data, _ = ED.compress_to(im, fmt, mode, value, src.stat().st_size)
            self.estimate.setText(f"{src.name}: {_human(src.stat().st_size)} → aprox. {_human(len(data))}")
        except Exception as exc:  # noqa: BLE001
            self.estimate.setText(f"No pude estimar: {exc}")

    def make_job(self):
        mode, value, side = self.params()
        paths = list(self.paths)
        return lambda: ED.compress_images(paths, mode, value, side)


# ================================================================= metadatos

class MetadataPanel(Panel):
    TITLE = "Metadatos"
    ACTION = "Guardar copia"
    WIDTH = 460

    def build(self) -> None:
        current = ED.read_metadata(self.paths[0])
        many = len(self.paths) > 1
        self.fields: dict[str, QLineEdit] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        labels = [("fecha", "Fecha", "AAAA:MM:DD HH:MM:SS"), ("marca", "Marca de cámara", ""),
                  ("modelo", "Modelo", ""), ("autor", "Autor", ""), ("descripcion", "Descripción", ""),
                  ("lat", "Latitud", "-33.9123"), ("lon", "Longitud", "-71.8456")]
        for r, (key, label, ph) in enumerate(labels):
            edit = QLineEdit()
            if many:
                edit.setPlaceholderText(current.get(key) or ph)
            else:
                edit.setText(current.get(key, ""))
                edit.setPlaceholderText(ph)
            self.fields[key] = edit
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(edit, r, 1)
        self.body.addLayout(grid)
        self.no_gps = QCheckBox("Quitar la ubicación")
        self.strip = QCheckBox("Quitar todos los metadatos (cámara, fecha, ubicación…)")
        self.strip.toggled.connect(lambda on: [f.setEnabled(not on) for f in self.fields.values()])
        self.body.addWidget(self.no_gps)
        self.body.addWidget(self.strip)
        self.body.addWidget(hint("En JPG se cambian los datos sin recomprimir la foto." +
                                 (f" Lo que llenes se aplica a las {len(self.paths)} fotos; lo vacío no se toca."
                                  if many else "")))
        self.original = current

    def make_job(self):
        fields = {k: f.text().strip() for k, f in self.fields.items()}
        if len(self.paths) == 1:  # solo lo que cambio
            fields = {k: v for k, v in fields.items() if v != self.original.get(k, "")}
        for key in ("lat", "lon"):
            if fields.get(key):
                try:
                    float(fields[key].replace(",", "."))
                except ValueError as exc:
                    raise GajoError(f"{key.capitalize()} debe ser un número, por ejemplo -33.91") from exc
        if bool(fields.get("lat")) != bool(fields.get("lon")):
            if len(self.paths) == 1:
                fields.setdefault("lat", self.original.get("lat"))
                fields.setdefault("lon", self.original.get("lon"))
        fields["quitar_ubicacion"] = self.no_gps.isChecked()
        strip = self.strip.isChecked()
        if not strip and not any(v for k, v in fields.items()):
            raise GajoError("No cambiaste ningún dato")
        paths = list(self.paths)
        return lambda: ED.write_metadata(paths, fields, strip)
