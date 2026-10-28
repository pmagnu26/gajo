"""Paneles de video: cortar, encuadrar, velocidad, unir, fotogramas, dividir y ocultar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from .. import editing as ED
from ..tools import GajoError
from .base import (
    IntField, Panel, ReorderList, Segmented, Swatches, button, fmt_time, hint, parse_time, section,
)
from .canvas import BoxesTool, CropTool
from .media import ClipsLane, Timeline, VideoView, strip_for


def _thumb(path: Path) -> QPixmap | None:
    try:
        info = ED.media_info(path)
        img = QImage()
        img.loadFromData(ED.frame_png(path, min(1.0, info["duration"] / 3), 160))
        return QPixmap.fromImage(img)
    except Exception:  # noqa: BLE001 - sin miniatura, se usa el icono del archivo
        return None


class _VideoPanel(Panel):
    """Base: carga la info, crea la vista previa y detiene el reproductor al cerrar."""
    WIDTH = 560

    def setup_view(self, min_h: int = 280) -> None:
        self.src = self.paths[0]
        self.info = ED.media_info(self.src)
        if not self.info["video"]:
            raise GajoError(f"{self.src.name} no tiene imagen de video")
        self.duration = self.info["duration"]
        self.view = VideoView(self.src, self.info, min_h)
        self.body.addWidget(self.view, 1)

    def add_timeline(self, with_range: bool = False) -> Timeline:
        tl = Timeline(self.duration, 60)
        tl.strip = strip_for(self.src, self.duration)
        if with_range:
            tl.range = [0.0, self.duration]
        tl.seek.connect(self.view.seek)
        self.view.position_changed.connect(tl.set_playhead)
        self.body.addWidget(tl)
        return tl

    def cleanup(self) -> None:
        if hasattr(self, "view"):
            self.view.stop()


# ================================================================= cortar

class TrimVideo(_VideoPanel):
    TITLE = "Cortar video"
    ACTION = "Aplicar"

    def build(self) -> None:
        self.setup_view()
        self.sel = hint("", wrap=False)
        self.row(hint("Arrastra los extremos para cortar", wrap=False), None, self.sel)
        self.tl = self.add_timeline(with_range=True)
        self.tl.range_changed.connect(self.sync_fields)
        self.start, self.end = QLineEdit(), QLineEdit()
        for e in (self.start, self.end):
            e.setFixedWidth(90)
            e.editingFinished.connect(self.from_fields)
        self.row(QLabel("Inicio"), self.start, None, QLabel("Fin"), self.end)
        self.view.position_changed.connect(self.keep_in_range)
        self.sync_fields()

    def keep_in_range(self, t: float) -> None:
        if self.view.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and t >= self.tl.range[1]:
            self.view.player.pause()

    def sync_fields(self, *_a) -> None:
        a, b = self.tl.range
        self.start.setText(fmt_time(a))
        self.end.setText(fmt_time(b))
        self.sel.setText(f"{fmt_time(b - a)} elegidos")

    def from_fields(self) -> None:
        try:
            a, b = parse_time(self.start.text()), parse_time(self.end.text())
        except GajoError as exc:
            self.warn(str(exc))
            return
        a, b = max(0.0, min(a, self.duration)), max(0.0, min(b, self.duration))
        if b > a:
            self.tl.range = [a, b]
            self.tl.update()
        self.sync_fields()

    def reset(self) -> None:
        self.tl.range = [0.0, self.duration]
        self.tl.update()
        self.sync_fields()

    def make_job(self):
        a, b = self.tl.range
        if a <= 0.001 and b >= self.duration - 0.001:
            raise GajoError("Mueve los extremos para elegir la parte que quieres conservar")
        src = self.src
        return lambda: ED.video_trim(src, a, b)


# ================================================================= encuadrar

class CropVideo(_VideoPanel):
    TITLE = "Encuadrar video"
    ACTION = "Aplicar"

    def build(self) -> None:
        self.setup_view(320)
        self.turns = 0
        self.tool = CropTool(self.sync_spins)
        self.view.canvas.tool = self.tool
        self.view.transform_frame = lambda img: img.transformed(QTransform().rotate(90 * self.turns)) if self.turns else img
        rot = QHBoxLayout()
        rot.addWidget(button("90°", lambda: self.rotate(-1), tip="Girar a la izquierda", icon_name="rot_left"))
        rot.addWidget(button("90°", lambda: self.rotate(1), tip="Girar a la derecha", icon_name="rot_right"))
        rot.addStretch()
        self.body.addLayout(rot)
        self.aspect = Segmented([("libre", "Libre"), ("1:1", "1:1"), ("16:9", "16:9"), ("9:16", "9:16"),
                                 ("4:3", "4:3"), ("3:4", "3:4")])
        self.aspect.changed.connect(self.set_aspect)
        self.row(QLabel("Proporción"), None, self.aspect)
        self.w_spin, self.h_spin = IntField(2, 8000), IntField(2, 8000)
        for s in (self.w_spin, self.h_spin):
            s.editingFinished.connect(self.exact_size)
        self.exact = None
        self.row(QLabel("Ancho"), self.w_spin, QLabel("Alto"), self.h_spin, QLabel("px"), None)
        self.body.addWidget(hint("El audio original se mantiene."))
        self.refresh_ratio()

    def real_size(self) -> tuple[int, int]:
        w, h = self.info["w"], self.info["h"]
        return (h, w) if self.turns % 2 else (w, h)

    def refresh_ratio(self) -> None:
        w, h = self.real_size()
        self.tool.img_ratio = w / max(h, 1)
        self.set_aspect(self.aspect.value())
        self.view.refresh()

    def rotate(self, d: int) -> None:
        self.turns = (self.turns + d) % 4
        self.refresh_ratio()

    def set_aspect(self, key: str) -> None:
        self.exact = None
        if key == "libre":
            self.tool.aspect = None
            self.tool.rect = [0.0, 0.0, 1.0, 1.0]
            self.sync_spins()
        else:
            a, b = (int(v) for v in key.split(":"))
            self.tool.set_aspect(a / b)
        self.view.canvas.update()

    def sync_spins(self) -> None:
        rw, rh = self.real_size()
        _, _, w, h = self.tool.rect
        for spin, v in ((self.w_spin, w * rw), (self.h_spin, h * rh)):
            if not spin.hasFocus():
                spin.blockSignals(True)
                spin.setValue(max(2, round(v)))
                spin.blockSignals(False)

    def exact_size(self) -> None:
        w, h = self.w_spin.value(), self.h_spin.value()
        if w < 2 or h < 2:
            return
        self.tool.set_aspect(w / h)
        self.exact = (w, h)
        self.view.canvas.update()

    def reset(self) -> None:
        self.turns = 0
        self.aspect.set_value("libre")
        self.refresh_ratio()

    def make_job(self):
        rect, size, turns, src = tuple(self.tool.rect), self.exact, self.turns, self.src
        if rect == (0.0, 0.0, 1.0, 1.0) and not size and not turns:
            raise GajoError("Ajusta el marco o gira el video antes de aplicar")
        return lambda: ED.video_crop(src, rect, size, turns)


# ================================================================= velocidad

SPEEDS = [0.25, 0.5, 1.0, 1.5, 2.0, 4.0]


class SpeedVideo(_VideoPanel):
    TITLE = "Cambiar velocidad"
    ACTION = "Aplicar"

    def build(self) -> None:
        self.setup_view(300)
        self.speed = Segmented([(str(s), f"{s:g}×".replace(".", ",")) for s in SPEEDS], "1.0")
        self.speed.changed.connect(lambda _k: self.changed())
        self.row(QLabel("Velocidad"), None, self.speed)
        self.pitch = QCheckBox("Mantener el tono del audio")
        self.pitch.setChecked(True)
        self.pitch.setEnabled(self.info["audio"])
        self.body.addWidget(self.pitch)
        if not self.info["audio"]:
            self.body.addWidget(hint("Este video no tiene sonido; igual se puede acelerar o ralentizar."))

    def rate(self) -> float:
        return float(self.speed.value())

    def changed(self) -> None:
        self.view.player.setPlaybackRate(self.rate())

    def reset(self) -> None:
        self.speed.set_value("1.0")
        self.changed()

    def make_job(self):
        r, keep, src = self.rate(), self.pitch.isChecked(), self.src
        if r == 1.0:
            raise GajoError("Elige una velocidad distinta de 1×")
        return lambda: ED.video_speed(src, r, keep)


# ================================================================= unir (video, audio y PDF)

class ReorderPanel(Panel):
    """Lista para ordenar archivos antes de unirlos."""
    WIDTH = 480
    RESET = False
    NOTE = ""
    ENGINE = None

    def build(self) -> None:
        self.body.addWidget(section("Orden"))
        self.order = ReorderList(self.paths, self.thumb)
        self.body.addWidget(self.order, 1)
        if self.NOTE:
            self.body.addWidget(hint(self.NOTE))

    def thumb(self, path: Path) -> QPixmap | None:
        return None

    def make_job(self):
        order, fn = list(self.order.paths), type(self).ENGINE
        return lambda: fn(order)


class JoinVideos(ReorderPanel):
    TITLE = "Unir videos"
    ACTION = "Unir videos"
    NOTE = "Se pueden mezclar MP4, MOV, MKV y otros. El primer clip define el tamaño y los cuadros por segundo."
    ENGINE = staticmethod(ED.video_join)

    def thumb(self, path: Path) -> QPixmap | None:
        return _thumb(path)


# ================================================================= fotogramas

class VideoSnapshots(_VideoPanel):
    TITLE = "Guardar fotogramas"
    ACTION = "Guardar fotogramas"

    def build(self) -> None:
        self.setup_view(300)
        self.count = hint("Ninguno elegido", wrap=False)
        self.row(self.count, None, button("Agregar fotograma", self.add), button("Quitar", self.remove))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setIconSize(QSize(96, 54))
        self.list.setFixedHeight(96)
        self.body.addWidget(self.list)
        self.body.addWidget(hint("Avanza cuadro a cuadro con ◁| y |▷. Se guardan en PNG a resolución completa."))
        self.times: list[float] = []
        self.refresh()

    def add(self) -> None:
        t = round(self.view.position(), 3)
        if t in self.times:
            return
        self.times.append(t)
        img = self.view.canvas.image
        item = QListWidgetItem(fmt_time(t))
        if img is not None:
            item.setIcon(QPixmap.fromImage(img.scaled(QSize(192, 108), Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation)))
        item.setData(Qt.ItemDataRole.UserRole, t)
        self.list.addItem(item)
        self.refresh()

    def remove(self) -> None:
        for item in self.list.selectedItems():
            self.times.remove(item.data(Qt.ItemDataRole.UserRole))
            self.list.takeItem(self.list.row(item))
        self.refresh()

    def refresh(self) -> None:
        n = len(self.times)
        self.count.setText(f"{n} elegido{'s' if n != 1 else ''}" if n else "Ninguno elegido")
        self.primary.setText(f"Guardar {n} fotograma{'s' if n != 1 else ''}" if n else "Guardar fotogramas")

    def reset(self) -> None:
        self.times.clear()
        self.list.clear()
        self.refresh()

    def make_job(self):
        if not self.times:
            raise GajoError("Agrega al menos un fotograma")
        times, src = list(self.times), self.src
        return lambda: ED.video_snapshots(src, times)


# ================================================================= dividir

class SplitVideo(_VideoPanel):
    TITLE = "Dividir video"
    ACTION = "Dividir video"

    def build(self) -> None:
        self.setup_view(280)
        self.points: list[float] = []
        self.tl = self.add_timeline()
        self.tl.markers = self.points  # misma lista: los cortes se ven al instante
        self.count = hint("", wrap=False)
        self.row(self.count, None, button("Agregar corte aquí", self.add, icon_name="scissors"),
                 button("Quitar cortes", self.reset))
        self.parts = QSpinBox()
        self.parts.setRange(2, 50)
        self.row(QLabel("Partes iguales"), self.parts, button("Fijar", self.equal), None)
        self.body.addWidget(hint("Todas las partes quedan juntas en una carpeta nueva al lado del video."))
        self.refresh()

    def add(self) -> None:
        t = round(self.view.position(), 3)
        if 0.05 < t < self.duration - 0.05 and t not in self.points:
            self.points.append(t)
            self.points.sort()
        self.refresh()

    def equal(self) -> None:
        n = self.parts.value()
        self.points[:] = [round(self.duration * k / n, 3) for k in range(1, n)]
        self.refresh()

    def refresh(self) -> None:
        n = len(self.points)
        self.count.setText(f"{n} punto{'s' if n != 1 else ''} de corte → {n + 1} partes" if n else "Sin cortes")
        self.tl.update()

    def reset(self) -> None:
        self.points.clear()
        self.refresh()

    def make_job(self):
        if not self.points:
            raise GajoError("Agrega al menos un corte")
        points, src = list(self.points), self.src
        return lambda: ED.video_split(src, points)


# ================================================================= ocultar

class RedactVideo(_VideoPanel):
    TITLE = "Ocultar en video"
    ACTION = "Guardar copia oculta"

    def build(self) -> None:
        self.setup_view(300)
        self.tool = BoxesTool(self.boxes_changed)
        self.tool.extra = lambda: [self.view.position(), self.duration]
        self.tool.visible = lambda i: self.tool.boxes[i][4] <= self.view.position() <= self.tool.boxes[i][5]
        self.view.canvas.tool = self.tool
        self.view.position_changed.connect(lambda _t: self.view.canvas.update())
        self.style = Segmented([("solid", "Sólido"), ("blur", "Desenfoque")])
        self.style.changed.connect(self.set_style)
        self.colors = Swatches(["#000000", "#FFFFFF", "#808080", "#E2701F"], "#000000")
        self.colors.changed.connect(self.set_color)
        self.row(QLabel("Estilo"), self.style, None, QLabel("Color"), self.colors)
        self.body.addWidget(section("Zonas y su tiempo"))
        self.lanes_box = QVBoxLayout()
        self.lanes_box.setSpacing(4)
        self.body.addLayout(self.lanes_box)
        self.add_timeline()
        self.row(hint("Arrastra sobre el video para tapar una zona; ajusta en qué tramo aparece en su pista."),
                 button("Borrar zona", self.delete, icon_name="trash"), stretch_at=0)
        self.lanes: list[ClipsLane] = []

    def set_style(self, k: str) -> None:
        self.tool.style = k
        self.view.canvas.update()

    def set_color(self, c: str) -> None:
        self.tool.color = c
        self.view.canvas.update()

    def boxes_changed(self) -> None:
        if len(self.lanes) != len(self.tool.boxes):
            self.rebuild_lanes()
        for i, lane in enumerate(self.lanes):
            lane.selected = 0 if i == self.tool.selected else -1
            lane.update()
        self.view.canvas.update()

    def rebuild_lanes(self) -> None:
        while self.lanes_box.count():
            w = self.lanes_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.lanes = []
        for i, box in enumerate(self.tool.boxes):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(f"Zona {i + 1}")
            lab.setFixedWidth(52)
            lane = ClipsLane(self.duration, f"Zona {i + 1}", single=True, height=26)
            lane.clips = [[box[4], box[5]]]
            lane.changed.connect(lambda i=i, lane=lane: self.lane_changed(i, lane))
            lane.selected_changed.connect(lambda _k, i=i: self.select(i))
            h.addWidget(lab)
            h.addWidget(lane, 1)
            self.lanes_box.addWidget(row)
            self.lanes.append(lane)

    def lane_changed(self, i: int, lane: ClipsLane) -> None:
        if lane.clips and i < len(self.tool.boxes):
            self.tool.boxes[i][4], self.tool.boxes[i][5] = lane.clips[0]
            self.view.canvas.update()

    def select(self, i: int) -> None:
        self.tool.selected = i
        self.boxes_changed()

    def delete(self) -> None:
        self.tool.delete_selected()
        self.rebuild_lanes()

    def reset(self) -> None:
        self.tool.boxes, self.tool.selected = [], -1
        self.rebuild_lanes()
        self.view.canvas.update()

    def make_job(self):
        if not self.tool.boxes:
            raise GajoError("Marca al menos una zona para ocultar")
        boxes = [{"rect": tuple(b[:4]), "start": b[4], "end": b[5]} for b in self.tool.boxes]
        src, style, color = self.src, self.tool.style, self.tool.color
        return lambda: ED.video_redact(src, boxes, style, color)
