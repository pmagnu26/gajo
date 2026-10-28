"""Ventana Escanear documento: esquinas ajustables con lupa, vista del resultado y varias fotos."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel

from .. import scanner
from ..engines import open_image
from .base import Panel, Segmented, busy, button, hint, pil_to_qimage, section
from .canvas import ImageCanvas

ORANGE = QColor("#E2701F")


class QuadTool:
    """Cuatro esquinas que se arrastran; mientras se arrastra, una lupa muestra el borde de cerca."""

    def __init__(self, on_change):
        self.pts = list(scanner.FULL)
        self.on_change = on_change
        self.drag = -1
        self.canvas: ImageCanvas | None = None

    def _screen(self, r: QRectF) -> list[QPointF]:
        return [QPointF(r.left() + x * r.width(), r.top() + y * r.height()) for x, y in self.pts]

    def paint(self, p: QPainter, r: QRectF) -> None:
        pts = self._screen(r)
        poly = QPolygonF(pts)
        shade = QPainterPath()
        shade.addRect(r)
        hole = QPainterPath()
        hole.addPolygon(poly)
        hole.closeSubpath()
        p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 115))
        p.setPen(QPen(ORANGE, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(poly)
        for i, q in enumerate(pts):
            p.setPen(QPen(ORANGE, 2.5))
            p.setBrush(QColor(255, 255, 255, 230 if i != self.drag else 120))
            p.drawEllipse(q, 9, 9)
        if 0 <= self.drag < 4 and self.canvas and self.canvas.image is not None:
            self._loupe(p, r, pts[self.drag])

    def _loupe(self, p: QPainter, r: QRectF, c: QPointF) -> None:
        img = self.canvas.image
        R, zoom = 58.0, 3.0
        scale = r.width() / max(img.width(), 1)
        src_r = R / (scale * zoom)
        x, y = self.pts[self.drag]
        sx, sy = x * img.width(), y * img.height()
        dx = -95 if c.x() > r.left() + 140 else 95
        dy = -95 if c.y() > r.top() + 140 else 95
        center = QPointF(c.x() + dx, c.y() + dy)
        clip = QPainterPath()
        clip.addEllipse(center, R, R)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(center.x() - R, center.y() - R, 2 * R, 2 * R), QColor("#1B1917"))
        p.drawImage(QRectF(center.x() - R, center.y() - R, 2 * R, 2 * R), img,
                    QRectF(sx - src_r, sy - src_r, 2 * src_r, 2 * src_r))
        p.setPen(QPen(ORANGE, 1.5))
        p.drawLine(QPointF(center.x() - 10, center.y()), QPointF(center.x() + 10, center.y()))
        p.drawLine(QPointF(center.x(), center.y() - 10), QPointF(center.x(), center.y() + 10))
        p.restore()
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, R, R)

    def _nearest(self, canvas, pos: QPointF) -> int:
        pts = self._screen(canvas.image_rect())
        best = min(range(4), key=lambda i: (pts[i].x() - pos.x()) ** 2 + (pts[i].y() - pos.y()) ** 2)
        d2 = (pts[best].x() - pos.x()) ** 2 + (pts[best].y() - pos.y()) ** 2
        return best if d2 <= 26 ** 2 else -1

    def press(self, canvas, pos: QPointF) -> None:
        self.canvas = canvas
        self.drag = self._nearest(canvas, pos)

    def move(self, canvas, pos: QPointF, pressed: bool) -> None:
        self.canvas = canvas
        if pressed and self.drag >= 0:
            self.pts[self.drag] = canvas.to_norm(pos)
        else:
            canvas.setCursor(Qt.CursorShape.SizeAllCursor if self._nearest(canvas, pos) >= 0
                             else Qt.CursorShape.ArrowCursor)

    def release(self, canvas, pos: QPointF) -> None:
        if self.drag >= 0:
            self.drag = -1
            self.on_change()


class ScanDocument(Panel):
    TITLE = "Escanear documento"
    ACTION = "Crear PDF"
    WIDTH = 640
    TALL = 0.9

    def build(self) -> None:
        self.pages: list[dict] = []
        for path in self.paths:
            im = open_image(path)
            small = im.copy()
            small.thumbnail((1600, 1600))
            quad = scanner.detect_quad(small)
            self.pages.append({"path": path, "small": small, "qimg": pil_to_qimage(small),
                               "quad": quad or list(scanner.FULL), "found": quad is not None, "turns": 0})
        self.idx = 0
        start = "resultado" if all(pg["found"] for pg in self.pages) else "bordes"
        self.view = Segmented([("bordes", "Ajustar bordes"), ("resultado", "Ver resultado")], start)
        self.view.changed.connect(lambda _k: self.show_page())
        self.compare = button("Ver original", tip="Mantén apretado para ver la foto tal como llegó",
                              icon_name="eye")
        self.compare.pressed.connect(self.show_original)
        self.compare.released.connect(self.show_page)
        self.prev_btn = button("", lambda: self.go(-1), "mini", "Foto anterior", "back")
        self.next_btn = button("", lambda: self.go(1), "mini", "Foto siguiente", "fwd")
        self.page_lab = QLabel()
        nav = QHBoxLayout()
        nav.addWidget(self.view)
        nav.addWidget(self.compare)
        nav.addStretch()
        for w in (self.prev_btn, self.page_lab, self.next_btn):
            nav.addWidget(w)
            w.setVisible(len(self.pages) > 1)
        self.body.addLayout(nav)
        self.canvas = ImageCanvas(340)
        self.tool = QuadTool(self.quad_changed)
        self.body.addWidget(self.canvas, 1)
        self.body.addSpacing(4)  # los botones van afuera, bajo la vista previa
        self.found_lab = hint("", wrap=False)
        self.row(button("Detectar bordes", self.redetect), button("Toda la foto", self.full_photo),
                 button("90°", lambda: self.rotate(-1), tip="Girar a la izquierda", icon_name="rot_left"),
                 button("90°", lambda: self.rotate(1), tip="Girar a la derecha", icon_name="rot_right"),
                 None, self.found_lab)
        self.body.addWidget(section("Resultado"))
        self.mode = Segmented([("color", "Color"), ("grises", "Grises"), ("byn", "Blanco y negro"),
                               ("original", "Original")])
        self.paper = Segmented([("auto", "Automático"), ("carta", "Carta"), ("oficio", "Oficio"), ("a4", "A4"),
                                ("cedula", "Cédula")])
        self.fmt = Segmented([("pdf", "PDF"), ("jpg", "Imagen")])
        self.ocr = QCheckBox("Texto buscable (OCR)")
        self.ocr.setChecked(True)
        self.row(QLabel("Limpieza"), None, self.mode)
        self.row(QLabel("Tamaño"), None, self.paper)
        self.row(QLabel("Guardar como"), self.fmt, None, self.ocr)
        n = len(self.pages)
        self.body.addWidget(hint("Arrastra las esquinas naranjas hasta los bordes de la hoja; la lupa ayuda a "
                                 "dejarlas justas. Color quita sombras y conserva timbres y firmas."
                                 + (f" Las {n} fotos quedan en un solo PDF, una por página." if n > 1 else "")))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.show_page)
        for seg in (self.mode, self.paper):
            seg.changed.connect(self.setting_changed)
        self.fmt.changed.connect(self.fmt_changed)
        self.show_page()

    def setting_changed(self, _key: str) -> None:
        """Limpieza y tamaño se ven al instante: si se estaban ajustando bordes, pasa a ver el resultado."""
        if self.view.value() != "resultado":
            self.view.set_value("resultado")
        self.timer.start()

    def show_original(self) -> None:
        self.canvas.tool = None
        self.canvas.set_image(self.page()["qimg"])

    # ------------------------------------------------------------ paginas
    def page(self) -> dict:
        return self.pages[self.idx]

    def go(self, d: int) -> None:
        self.idx = (self.idx + d) % len(self.pages)
        self.show_page()

    def show_page(self) -> None:
        pg = self.page()
        self.compare.setEnabled(self.view.value() == "resultado")
        self.page_lab.setText(f"Foto {self.idx + 1} de {len(self.pages)}")
        self.found_lab.setText("Bordes detectados" if pg["found"] else "No encontré los bordes: ajústalos a mano")
        if self.view.value() == "bordes":
            self.tool.pts = list(pg["quad"])
            self.canvas.tool = self.tool
            self.canvas.set_image(pg["qimg"])
            return
        self.canvas.tool = None
        with busy():
            img, _ = scanner.warp(pg["small"], pg["quad"], self.paper.value(), pg["turns"], max_side=1400)
            self.canvas.set_image(pil_to_qimage(scanner.enhance(img, self.mode.value())))

    def quad_changed(self) -> None:
        self.page()["quad"] = list(self.tool.pts)
        self.page()["found"] = True

    def redetect(self) -> None:
        pg = self.page()
        with busy():
            quad = scanner.detect_quad(pg["small"])
        pg["quad"], pg["found"] = (quad or list(scanner.FULL)), quad is not None
        self.show_page()

    def full_photo(self) -> None:
        self.page()["quad"] = list(scanner.FULL)
        self.show_page()

    def rotate(self, d: int) -> None:
        self.page()["turns"] = (self.page()["turns"] + d) % 4
        if self.view.value() != "resultado":
            self.view.set_value("resultado")
        self.show_page()

    def fmt_changed(self, k: str) -> None:
        self.ocr.setEnabled(k == "pdf")
        self.primary.setText("Crear PDF" if k == "pdf" else "Guardar imagen" + ("es" if len(self.pages) > 1 else ""))

    def reset(self) -> None:
        for pg in self.pages:
            quad = scanner.detect_quad(pg["small"])
            pg.update(quad=quad or list(scanner.FULL), found=quad is not None, turns=0)
        self.mode.set_value("color")
        self.paper.set_value("auto")
        self.view.set_value("bordes")
        self.show_page()

    def make_job(self):
        data = [{"path": Path(pg["path"]), "quad": list(pg["quad"]), "turns": pg["turns"]} for pg in self.pages]
        mode, paper, fmt = self.mode.value(), self.paper.value(), self.fmt.value()
        ocr = self.ocr.isChecked() and fmt == "pdf"
        return lambda: scanner.scan_documents(data, mode, paper, fmt, ocr)
