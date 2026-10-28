"""Piezas para paneles de video y audio: reproductor, linea de tiempo y pistas de tramos."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .base import button, fmt_time
from .canvas import ImageCanvas

ORANGE = QColor("#E2701F")
INK = QColor("#35291F")


class VideoView(QWidget):
    """Vista previa de video con reproducir, avanzar cuadro a cuadro y tiempo."""
    position_changed = Signal(float)

    def __init__(self, path: Path, info: dict, min_h: int = 280):
        super().__init__()
        self.info = info
        self.duration = max(info["duration"], 0.01)
        self.fps = info["fps"] or 30
        self.canvas = ImageCanvas(min_h)
        self.player = QMediaPlayer(self)
        self.sink = QVideoSink(self)
        self.audio = QAudioOutput(self)
        self.player.setVideoSink(self.sink)
        self.player.setAudioOutput(self.audio)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.positionChanged.connect(lambda ms: self.position_changed.emit(ms / 1000))
        self.player.playbackStateChanged.connect(self._state)
        self._first = True
        self.transform_frame = None  # funcion opcional QImage -> QImage (ej. girar la vista)
        self.audio.setVolume(0)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()  # se pausa en el primer cuadro (ver _frame)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.canvas, 1)
        ctrl = QHBoxLayout()
        self.back = button("", lambda: self.step(-1), tip="Cuadro anterior", icon_name="back")
        self.play_btn = button("", self.toggle, tip="Reproducir / pausar", icon_name="play")
        self.fwd = button("", lambda: self.step(1), tip="Cuadro siguiente", icon_name="fwd")
        self.time_lab = QLabel()
        self.time_lab.setProperty("role", "value")
        for w in (self.back, self.play_btn, self.fwd):
            ctrl.addWidget(w)
        ctrl.addStretch()
        ctrl.addWidget(self.time_lab)
        lay.addLayout(ctrl)
        self.position_changed.connect(self._update_time)
        self._update_time(0)

    def _frame(self, frame) -> None:
        if not frame.isValid():
            return
        img = frame.toImage()
        self.last_frame = img
        self.canvas.set_image(self.transform_frame(img) if self.transform_frame else img)
        if self._first:
            self._first = False
            self.player.pause()
            self.player.setPosition(0)
            self.audio.setVolume(1)

    def _state(self, state) -> None:
        from .icons import icon
        playing = state == QMediaPlayer.PlaybackState.PlayingState and not self._first
        self.play_btn.setIcon(icon("pause" if playing else "play"))

    def _update_time(self, t: float) -> None:
        self.time_lab.setText(f"{fmt_time(t)} / {fmt_time(self.duration)}")

    def refresh(self) -> None:
        """Vuelve a dibujar el ultimo cuadro (por ejemplo tras girar la vista)."""
        img = getattr(self, "last_frame", None)
        if img is not None:
            self.canvas.set_image(self.transform_frame(img) if self.transform_frame else img)

    def position(self) -> float:
        return self.player.position() / 1000

    def seek(self, t: float) -> None:
        self.player.setPosition(int(min(max(t, 0), self.duration) * 1000))

    def step(self, n: int) -> None:
        self.player.pause()
        self.seek(self.position() + n / self.fps)

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.position() >= self.duration - 0.05:
                self.seek(0)
            self.player.play()

    def stop(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())


class AudioPlayer:
    """Reproduce un archivo o un tramo, avisando la posicion para dibujar el cursor."""

    def __init__(self, parent: QWidget, on_position=None):
        self.player = QMediaPlayer(parent)
        self.out = QAudioOutput(parent)
        self.player.setAudioOutput(self.out)
        self.end: float | None = None
        self.on_position = on_position
        self.player.positionChanged.connect(self._pos)
        self.current: Path | None = None

    def _pos(self, ms: int) -> None:
        t = ms / 1000
        if self.end is not None and t >= self.end:
            self.player.pause()
            self.end = None
        if self.on_position:
            self.on_position(t)

    def play(self, path: Path, start: float = 0.0, end: float | None = None) -> None:
        if self.current != path:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.current = path
        self.end = end
        self.player.setPosition(int(start * 1000))
        self.player.play()

    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def pause(self) -> None:
        self.player.pause()

    def stop(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.current = None


class Timeline(QWidget):
    """Tira de miniaturas o forma de onda, con cursor y (opcional) rango o marcas."""
    seek = Signal(float)
    range_changed = Signal(float, float)

    def __init__(self, duration: float, height: int = 64):
        super().__init__()
        self.duration = max(duration, 0.01)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.strip: QImage | None = None
        self.strip_count = 14
        self.peaks: np.ndarray | None = None
        self.wave_color = QColor("#C9B8A8")
        self.range: list[float] | None = None
        self.markers: list[float] | None = None
        self.playhead = 0.0
        self._drag = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def area(self) -> QRectF:
        pad = 12 if self.range is not None else 2
        return QRectF(self.rect()).adjusted(pad, 2, -pad, -2)

    def t_to_x(self, t: float) -> float:
        a = self.area()
        return a.left() + t / self.duration * a.width()

    def x_to_t(self, x: float) -> float:
        a = self.area()
        return min(max((x - a.left()) / max(a.width(), 1) * self.duration, 0.0), self.duration)

    def set_playhead(self, t: float) -> None:
        self.playhead = t
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = self.area()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EFE7DD"))
        p.drawRoundedRect(a, 6, 6)
        if self.strip is not None and not self.strip.isNull():
            # cada cuadro con su proporcion real; se eligen los necesarios para llenar el ancho
            n = max(1, self.strip_count)
            tile_w = self.strip.width() / n
            aspect = tile_w / max(self.strip.height(), 1)
            th = a.height()
            tw = max(th * aspect, 1)
            k = max(1, int(a.width() // tw) + 1)
            p.save()
            p.setClipRect(a)
            for i in range(k):
                src_i = round(i * (n - 1) / max(k - 1, 1))
                p.drawImage(QRectF(a.left() + i * tw, a.top(), tw, th), self.strip,
                            QRectF(src_i * tile_w, 0, tile_w, self.strip.height()))
            p.restore()
        if self.peaks is not None:
            level = self.peaks.max(axis=0) if self.peaks.ndim == 2 else self.peaks
            level = level / max(float(level.max()), 1e-3) * 0.95  # escala para que se vea
            n = level.size
            mid, half = a.center().y(), a.height() / 2 - 4
            step = max(1, int(n / max(a.width() / 3, 1)))
            p.setPen(QPen(self.wave_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(0, n, step):
                v = float(level[i:i + step].max())
                x = a.left() + (i + step / 2) / n * a.width()
                hh = max(1.0, v * half)
                p.drawLine(QPointF(x, mid - hh), QPointF(x, mid + hh))
        if self.range is not None:
            x0, x1 = self.t_to_x(self.range[0]), self.t_to_x(self.range[1])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(251, 248, 244, 190))
            p.drawRect(QRectF(a.left(), a.top(), x0 - a.left(), a.height()))
            p.drawRect(QRectF(x1, a.top(), a.right() - x1, a.height()))
            p.setPen(QPen(ORANGE, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x0, a.top() + 1, x1 - x0, a.height() - 2), 4, 4)
            for x in (x0, x1):
                grip = QRectF(x - 6, a.top(), 12, a.height())
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ORANGE)
                p.drawRoundedRect(grip, 4, 4)
                p.setPen(QPen(QColor("white"), 2))
                p.drawLine(QPointF(x, a.center().y() - 8), QPointF(x, a.center().y() + 8))
        if self.markers:
            for t in self.markers:  # corte: linea gruesa con borde blanco y rombo arriba
                x = self.t_to_x(t)
                p.setPen(QPen(QColor("white"), 6))
                p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
                p.setPen(QPen(ORANGE, 3))
                p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
                p.setPen(QPen(QColor("white"), 1.5))
                p.setBrush(ORANGE)
                p.drawRect(QRectF(x - 5, a.top() - 1, 10, 10))
        x = self.t_to_x(self.playhead)
        p.setPen(QPen(INK, 2))
        p.drawLine(QPointF(x, a.top() - 1), QPointF(x, a.bottom() + 1))
        p.end()

    def mousePressEvent(self, e) -> None:
        x = e.position().x()
        self._drag = "seek"
        if self.range is not None:
            x0, x1 = self.t_to_x(self.range[0]), self.t_to_x(self.range[1])
            if abs(x - x0) <= 10:
                self._drag = "a"
            elif abs(x - x1) <= 10:
                self._drag = "b"
        self._apply(x)

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.MouseButton.LeftButton and self._drag:
            self._apply(e.position().x())

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None

    def _apply(self, x: float) -> None:
        t = self.x_to_t(x)
        min_len = self.duration * 0.01
        if self._drag == "a":
            self.range[0] = min(t, self.range[1] - min_len)
            self.range_changed.emit(*self.range)
            self.seek.emit(self.range[0])
        elif self._drag == "b":
            self.range[1] = max(t, self.range[0] + min_len)
            self.range_changed.emit(*self.range)
            self.seek.emit(self.range[1])
        else:
            self.seek.emit(t)
        self.update()


class ClipsLane(QWidget):
    """Pista donde se marcan tramos de tiempo arrastrando (pitidos, zonas por tiempo)."""
    changed = Signal()
    selected_changed = Signal(int)

    def __init__(self, duration: float, label: str = "", single: bool = False, height: int = 30):
        super().__init__()
        self.duration = max(duration, 0.01)
        self.label = label
        self.single = single
        self.clips: list[list[float]] = []
        self.selected = -1
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._drag = None

    def area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(2, 2, -2, -2)

    def t_to_x(self, t: float) -> float:
        a = self.area()
        return a.left() + t / self.duration * a.width()

    def x_to_t(self, x: float) -> float:
        a = self.area()
        return min(max((x - a.left()) / max(a.width(), 1) * self.duration, 0.0), self.duration)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = self.area()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EFE7DD"))
        p.drawRoundedRect(a, 6, 6)
        if not self.clips:
            p.setPen(QColor("#A3958A"))
            p.drawText(a, Qt.AlignmentFlag.AlignCenter, "Arrastra aquí para marcar un tramo")
        for i, (s, e) in enumerate(self.clips):
            r = QRectF(self.t_to_x(s), a.top() + 2, max(self.t_to_x(e) - self.t_to_x(s), 6), a.height() - 4)
            c = QColor(ORANGE)
            c.setAlpha(255 if i == self.selected else 150)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QPen(QColor("white"), 2))
            for x in (r.left() + 5, r.right() - 5):
                p.drawLine(QPointF(x, r.top() + 6), QPointF(x, r.bottom() - 6))
            if self.label and r.width() > 60:
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.label)
        p.end()

    def _hit(self, x: float):
        for i in range(len(self.clips) - 1, -1, -1):
            x0, x1 = self.t_to_x(self.clips[i][0]), self.t_to_x(self.clips[i][1])
            if abs(x - x0) <= 7:
                return i, "a"
            if abs(x - x1) <= 7:
                return i, "b"
            if x0 < x < x1:
                return i, "move"
        return -1, None

    def mousePressEvent(self, e) -> None:
        x = e.position().x()
        i, mode = self._hit(x)
        t = self.x_to_t(x)
        if i < 0:
            if self.single:
                self.clips = []
            self.clips.append([t, t])
            i, mode = len(self.clips) - 1, "new"
        self.selected = i
        self._drag = (mode, t, list(self.clips[i]))
        self.selected_changed.emit(i)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        x = e.position().x()
        if not (e.buttons() & Qt.MouseButton.LeftButton) or not self._drag:
            _, mode = self._hit(x)
            self.setCursor(Qt.CursorShape.SizeHorCursor if mode in ("a", "b") else
                           Qt.CursorShape.OpenHandCursor if mode == "move" else Qt.CursorShape.IBeamCursor)
            return
        mode, t0, (s, e0) = self._drag
        t = self.x_to_t(x)
        clip = self.clips[self.selected]
        if mode == "new":
            clip[:] = [min(t0, t), max(t0, t)]
        elif mode == "move":
            d = min(max(t - t0, -s), self.duration - e0)
            clip[:] = [s + d, e0 + d]
        elif mode == "a":
            clip[0] = min(t, clip[1] - 0.05)
        else:
            clip[1] = max(t, clip[0] + 0.05)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag and self._drag[0] == "new" and self.selected >= 0:
            s, e = self.clips[self.selected]
            if e - s < self.duration * 0.005:  # clic suelto: tramo minimo de medio segundo
                self.clips[self.selected] = [s, min(s + max(0.5, self.duration * 0.05), self.duration)]
        self._drag = None
        self.changed.emit()
        self.update()

    def delete_selected(self) -> None:
        if 0 <= self.selected < len(self.clips):
            self.clips.pop(self.selected)
            self.selected = -1
            self.changed.emit()
            self.update()


def peaks_for(path: Path, buckets: int = 800, stereo: bool = False) -> np.ndarray:
    from ..editing import waveform
    return waveform(path, buckets, stereo)


def strip_for(path: Path, duration: float, count: int = 14, height: int = 64) -> QImage:
    from ..editing import thumb_strip
    img = QImage()
    img.loadFromData(thumb_strip(path, duration, count, height))
    return img


class WaveLabel(QWidget):
    """Forma de onda chica (sin interaccion) para un canal."""

    def __init__(self, peaks: np.ndarray, color: str = "#C9B8A8", height: int = 46, norm: float | None = None):
        super().__init__()
        self.peaks = peaks / max(norm if norm is not None else float(peaks.max()), 1e-3) * 0.9
        self.color = QColor(color)
        self.gain = 1.0
        self.playhead: float | None = None
        self.setFixedHeight(height)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EFE7DD"))
        p.drawRoundedRect(a, 6, 6)
        n = self.peaks.size
        mid, half = a.center().y(), a.height() / 2 - 3
        step = max(1, int(n / max(a.width() / 3, 1)))
        p.setPen(QPen(self.color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(0, n, step):
            v = min(1.0, float(self.peaks[i:i + step].max()) * self.gain)
            x = a.left() + (i + step / 2) / n * a.width()
            p.drawLine(QPointF(x, mid - max(1.0, v * half)), QPointF(x, mid + max(1.0, v * half)))
        if self.playhead is not None:
            x = a.left() + self.playhead * a.width()
            p.setPen(QPen(ORANGE, 2))
            p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
        p.end()
