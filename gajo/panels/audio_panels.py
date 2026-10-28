"""Paneles de audio: normalizar, visualizador, cortar, canales, pitidos y unir."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit

from .. import editing as ED
from ..engines import audio_join
from ..tools import GajoError
from .base import (
    Panel, Segmented, SliderRow, Swatches, button, fmt_time, hint, in_background, parse_time, section,
)
from .canvas import ImageCanvas
from .media import AudioPlayer, ClipsLane, Timeline, WaveLabel
from .video_panels import ReorderPanel


class _AudioPanel(Panel):
    WIDTH = 520

    def load(self, stereo: bool = False) -> None:
        self.src = self.paths[0]
        self.info = ED.media_info(self.src)
        if not self.info["audio"]:
            raise GajoError(f"{self.src.name} no tiene sonido")
        self.duration = self.info["duration"]
        self.peaks = ED.waveform(self.src, 900, stereo)
        self.player = AudioPlayer(self, self.on_position)
        self.temps: list[Path] = []

    def on_position(self, t: float) -> None:
        tl = getattr(self, "tl", None)
        if tl is not None:
            tl.set_playhead(t)

    def preview_file(self, **kw) -> Path:
        out = ED.render_preview(self.src, **kw)
        self.temps.append(out.parent)
        return out

    def cleanup(self) -> None:
        if hasattr(self, "player"):
            self.player.stop()
        for d in getattr(self, "temps", []):
            shutil.rmtree(d, ignore_errors=True)


# ================================================================= normalizar

class NormalizeAudio(_AudioPanel):
    TITLE = "Normalizar volumen"
    ACTION = "Normalizar audio"

    def build(self) -> None:
        self.load()
        self.row(hint(self.src.name), None, hint(fmt_time(self.duration)))
        self.tl = Timeline(self.duration, 110)
        self.tl.peaks = self.peaks
        self.tl.wave_color = QColor("#E2701F")
        self.tl.seek.connect(lambda t: self.player.play(self.src, t))
        self.body.addWidget(self.tl)
        self.play_btn = button("Escuchar", self.toggle, icon_name="play")
        self.measure = hint("Midiendo el volumen actual…", wrap=False)
        self.row(self.play_btn, None, self.measure)
        self.target = SliderRow("Volumen objetivo", -30, -5, -16, lambda v: f"{v} LUFS", 120)
        self.lra = SliderRow("Rango dinámico", 1, 20, 11, lambda v: f"{v} LU", 120)
        self.peak = SliderRow("Pico máximo", -90, 0, -15, lambda v: f"{v / 10:.1f} dBTP".replace(".", ","), 120)
        for s in (self.target, self.lra, self.peak):
            self.body.addWidget(s)
        self.body.addWidget(hint("−16 LUFS es un buen punto de partida para voz y música. El pico máximo evita "
                                 "que el sonido se sature al guardar."))
        in_background(lambda: ED.loudness(self.src), self.measured)

    def measured(self, result, error) -> None:
        if error:
            self.measure.setText("No pude medir el volumen actual")
            return
        self.measure.setText(f"Ahora: {float(result['input_i']):.1f} LUFS · pico "
                             f"{float(result['input_tp']):.1f} dBTP".replace(".", ","))

    def toggle(self) -> None:
        from .icons import icon
        if self.player.playing():
            self.player.pause()
            self.play_btn.setText("Escuchar")
            self.play_btn.setIcon(icon("play"))
        else:
            self.player.play(self.src, self.tl.playhead)
            self.play_btn.setText("Pausa")
            self.play_btn.setIcon(icon("pause"))

    def reset(self) -> None:
        for s in (self.target, self.lra, self.peak):
            s.reset()

    def make_job(self):
        src, i, lra, tp = self.src, self.target.value(), self.lra.value(), self.peak.value() / 10
        return lambda: ED.audio_normalize(src, i, lra, tp)


# ================================================================= visualizador

class AudioVisualizer(_AudioPanel):
    TITLE = "Crear visualizador"
    ACTION = "Crear video"

    def build(self) -> None:
        self.load()
        self.image: str | None = None
        self.canvas = ImageCanvas(260)
        self.body.addWidget(self.canvas, 1)
        self.style = Segmented([("onda", "Onda animada"), ("imagen", "Imagen fija")])
        self.style.changed.connect(self.style_changed)
        self.row(QLabel("Estilo"), None, self.style)
        self.shape = Segmented([("horizontal", "Horizontal 16:9"), ("vertical", "Vertical 9:16"),
                                ("cuadrado", "Cuadrado")])
        self.shape.changed.connect(lambda _k: self.render())
        self.row(QLabel("Forma"), None, self.shape)
        self.color = Swatches(["#E2701F", "#FFFFFF", "#4FC3F7", "#81C784", "#F06292", "#FFD54F"], "#E2701F")
        self.color.changed.connect(lambda _c: self.render())
        self.pick = button("Elegir imagen…", self.choose)
        self.row(QLabel("Color"), self.color, None, self.pick)
        self.body.addWidget(hint("El video queda en MP4 con el audio original, listo para compartir."))
        self.style_changed("onda")

    def style_changed(self, k: str) -> None:
        self.pick.setVisible(k == "imagen")
        self.color.setVisible(k == "onda")
        self.render()

    def choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Imagen para el video", str(self.src.parent),
                                              "Imágenes (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.image = path
            self.render()

    def render(self) -> None:
        w, h = ED.SHAPES[self.shape.value()]
        k = 480 / max(w, h)
        img = QImage(int(w * k), int(h * k), QImage.Format.Format_RGB32)
        img.fill(QColor("black"))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.style.value() == "imagen":
            if self.image:
                pic = QImage(self.image)
                target = pic.size().scaled(img.size(), Qt.AspectRatioMode.KeepAspectRatio)
                x, y = (img.width() - target.width()) / 2, (img.height() - target.height()) / 2
                p.drawImage(QRectF(x, y, target.width(), target.height()), pic)
            else:
                p.setPen(QColor("#888888"))
                p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "Elige una imagen")
        else:
            level = self.peaks.max(axis=0)
            n, mid = 120, img.height() / 2
            idx = np.linspace(0, level.size - 1, n).astype(int)
            p.setPen(QPen(QColor(self.color.value()), max(2, img.width() / n * 0.55), Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            for j, i in enumerate(idx):
                x = (j + 0.5) / n * img.width()
                v = max(0.02, float(level[i])) * img.height() * 0.2
                p.drawLine(int(x), int(mid - v), int(x), int(mid + v))
        p.end()
        self.canvas.set_image(img)

    def make_job(self):
        style, shape, color, image, src = (self.style.value(), self.shape.value(), self.color.value(),
                                           self.image, self.src)
        if style == "imagen" and not image:
            raise GajoError("Elige una imagen para el video")
        return lambda: ED.audio_visualizer(src, style, shape, color, image)


# ================================================================= cortar

class TrimAudio(_AudioPanel):
    TITLE = "Cortar audio"
    ACTION = "Aplicar"

    def build(self) -> None:
        self.load()
        self.sel = hint("", wrap=False)
        self.row(hint("Arrastra los extremos para cortar", wrap=False), None, self.sel)
        self.tl = Timeline(self.duration, 110)
        self.tl.peaks = self.peaks
        self.tl.wave_color = QColor("#E2701F")
        self.tl.range = [0.0, self.duration]
        self.tl.range_changed.connect(self.sync)
        self.tl.seek.connect(self.tl.set_playhead)
        self.body.addWidget(self.tl)
        self.start, self.end = QLineEdit(), QLineEdit()
        for e in (self.start, self.end):
            e.setFixedWidth(90)
            e.editingFinished.connect(self.from_fields)
        self.row(QLabel("Inicio"), self.start, None, QLabel("Fin"), self.end)
        self.auto = QCheckBox("Cortar automáticamente los silencios del inicio y del final")
        self.auto.toggled.connect(self.auto_trim)
        self.body.addWidget(self.auto)
        self.play_btn = button("Reproducir selección", self.play, icon_name="play")
        self.row(None, self.play_btn)
        self.sync()

    def sync(self, *_a) -> None:
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
        self.sync()

    def auto_trim(self, on: bool) -> None:
        self.tl.range = list(ED.silent_bounds(self.peaks, self.duration)) if on else [0.0, self.duration]
        self.tl.update()
        self.sync()

    def play(self) -> None:
        a, b = self.tl.range
        self.player.play(self.src, a, b)

    def reset(self) -> None:
        self.auto.setChecked(False)
        self.tl.range = [0.0, self.duration]
        self.tl.update()
        self.sync()

    def make_job(self):
        a, b = self.tl.range
        if a <= 0.001 and b >= self.duration - 0.001:
            raise GajoError("Mueve los extremos para elegir la parte que quieres conservar")
        src = self.src
        return lambda: ED.audio_trim(src, a, b)


# ================================================================= canales

class AudioChannels(_AudioPanel):
    TITLE = "Canales de audio"
    ACTION = "Aplicar"

    def build(self) -> None:
        self.load(stereo=True)
        self.body.addWidget(section("Vista previa por canal"))
        self.waves = []
        for i, name in enumerate(("L", "R")):
            wave = WaveLabel(self.peaks[i], "#E2701F", 46, float(self.peaks.max()))
            self.waves.append(wave)
            badge = QLabel(name)
            badge.setFixedWidth(22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet("background:#F6DDC8;border-radius:11px;color:#E2701F;font-weight:600;")
            self.row(badge, wave, button("", lambda _=False, k=i: self.solo(k), "mini", "Escuchar solo este canal",
                                         "speaker"), stretch_at=1)
        if self.info["channels"] == 1:
            self.body.addWidget(hint("Este audio es mono: los dos canales parten iguales."))
        self.mode = Segmented([("mono", "Mono"), ("estereo", "Estéreo")], "estereo")
        self.mode.changed.connect(lambda _k: self.update_waves())
        self.row(QLabel("Salida"), None, self.mode)
        self.left = SliderRow("Volumen izquierdo", 0, 200, 100, lambda v: f"{v} %", 120)
        self.right = SliderRow("Volumen derecho", 0, 200, 100, lambda v: f"{v} %", 120)
        for s in (self.left, self.right):
            s.changed.connect(self.update_waves)
            self.body.addWidget(s)
        self.row(QLabel("Vista previa"), None, button("Escuchar resultado", self.listen, icon_name="play"))

    def update_waves(self) -> None:
        lv, rv = self.left.value() / 100, self.right.value() / 100
        if self.mode.value() == "mono":
            lv = rv = (lv + rv) / 2
        self.waves[0].gain, self.waves[1].gain = lv, rv
        for w in self.waves:
            w.update()

    def _filter(self, mode: str, lv: float, rv: float) -> str:
        pre = "aformat=channel_layouts=stereo," if self.info["channels"] == 1 else ""
        return pre + ED.channels_filter(mode, lv, rv)

    def solo(self, k: int) -> None:
        lv, rv = (1.0, 0.0) if k == 0 else (0.0, 1.0)
        self.player.play(self.preview_file(af=self._filter("estereo", lv, rv)))

    def listen(self) -> None:
        f = self._filter(self.mode.value(), self.left.value() / 100, self.right.value() / 100)
        self.player.play(self.preview_file(af=f))

    def reset(self) -> None:
        self.mode.set_value("estereo")
        self.left.reset()
        self.right.reset()

    def make_job(self):
        mode, lv, rv, src = self.mode.value(), self.left.value() / 100, self.right.value() / 100, self.src
        if mode == "estereo" and lv == 1 and rv == 1 and self.info["channels"] == 2:
            raise GajoError("Cambia la salida o el volumen de algún canal")
        return lambda: ED.audio_channels(src, mode, lv, rv)


# ================================================================= pitidos

class BleepAudio(_AudioPanel):
    TITLE = "Pitido sobre audio"
    ACTION = "Guardar copia con pitidos"

    def build(self) -> None:
        self.load()
        self.count = QLabel()
        self.row(section("Pitidos"), self.count, None)
        self.lane = ClipsLane(self.duration, "Pitido", height=34)
        self.lane.changed.connect(self.refresh)
        self.body.addWidget(self.lane)
        self.tl = Timeline(self.duration, 90)
        self.tl.peaks = self.peaks
        self.tl.wave_color = QColor("#E2701F")
        self.tl.seek.connect(self.tl.set_playhead)
        self.body.addWidget(self.tl)
        self.row(hint("0:00.00"), None, hint(fmt_time(self.duration)))
        self.body.addWidget(hint("Arrastra sobre la pista de pitidos para agregar un tramo; arrastra el tramo o sus "
                                 "bordes para ajustarlo."))
        self.row(QLabel("Vista previa"), None, button("Original", self.original, icon_name="play"),
                 button("Con pitidos", self.bleeped, icon_name="play"),
                 button("Borrar pitido", self.lane.delete_selected, icon_name="trash"))
        self.refresh()

    def refresh(self) -> None:
        self.count.setText(str(len(self.lane.clips)))

    def original(self) -> None:
        self.player.play(self.src, self.tl.playhead)

    def bleeped(self) -> None:
        if not self.lane.clips:
            self.original()
            return
        rate = self.info["rate"] or 44100
        out = self.preview_file(fc=ED.bleep_filter([tuple(c) for c in self.lane.clips], rate), seconds=self.duration + 1)
        self.player.play(out)

    def reset(self) -> None:
        self.lane.clips.clear()
        self.lane.update()
        self.refresh()

    def make_job(self):
        ranges, src = [tuple(c) for c in self.lane.clips], self.src
        if not ranges:
            raise GajoError("Agrega al menos un pitido")
        return lambda: ED.audio_bleep(src, ranges)


class JoinAudio(ReorderPanel):
    TITLE = "Unir audios"
    ACTION = "Unir audios"
    NOTE = "El resultado queda en el formato del primer archivo."
    ENGINE = staticmethod(audio_join)
