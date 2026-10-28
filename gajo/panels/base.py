"""Base comun de los paneles: ventana, estilo y controles reutilizables."""
from __future__ import annotations

import ctypes
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QImage, QIntValidator, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFileIconProvider, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QVBoxLayout, QWidget,
)

from ..icon import make_icon
from ..tools import GajoError

ACCENT = "#E2701F"
QSS = """
QDialog#panel { background: #FBF8F4; }
QWidget { color: #35291F; font-family: 'Segoe UI'; font-size: 9pt; }
QLabel[role="title"] { font-size: 11pt; font-weight: 600; }
QLabel[role="section"] { color: #9A8B7E; font-size: 8pt; font-weight: 600; letter-spacing: 0.5px; }
QLabel[role="hint"] { color: #8A7C70; font-size: 8pt; }
QLabel[role="value"] { color: #76685C; font-size: 8.5pt; }
QPushButton { background: #EFE7DD; border: 1px solid #E2D6C8; border-radius: 7px; padding: 5px 12px; }
QPushButton:hover { background: #E8DCCE; }
QPushButton:disabled { color: #B9ADA2; background: #F3EEE8; }
QPushButton[role="primary"] { background: #E2701F; border: none; color: white; font-weight: 600; padding: 7px 18px; }
QPushButton[role="primary"]:hover { background: #C9611A; }
QPushButton[role="primary"]:disabled { background: #EDC3A3; }
QPushButton[role="seg"] { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 3px 10px; }
QPushButton[role="seg"]:checked { background: white; border: 1px solid #DCCFC1; font-weight: 600; }
QPushButton[role="tool"] { min-width: 34px; padding: 5px 4px; font-size: 10pt; }
QPushButton[role="tool"]:checked { background: #E2701F; color: white; border: none; }
QPushButton[role="mini"] { background: transparent; border: none; color: #A3958A; padding: 0 4px; font-size: 10pt; }
QPushButton[role="mini"]:hover { color: #E2701F; }
QFrame[role="segbox"] { background: #EFE7DD; border-radius: 8px; }
QFrame[role="line"] { background: #E8DED3; max-height: 1px; min-height: 1px; border: none; }
QFrame[role="card"] { background: #F3ECE4; border-radius: 9px; }
QSlider::groove:horizontal { height: 4px; background: #E3D8CC; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #CDBFB1; border-radius: 2px; }
QSlider::handle:horizontal { background: white; border: 1px solid #C4B5A7; width: 15px; height: 15px; margin: -6px 0; border-radius: 8px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit { background: white; border: 1px solid #DCCFC1; border-radius: 6px; padding: 3px 6px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 8px; }
QListWidget::item:selected { background: #F6DDC8; }
QCheckBox { spacing: 6px; }
"""


def bring_to_front(w: QWidget) -> None:
    """Muestra la ventana adelante aunque Windows proteja el foco de otra app."""
    w.show()
    w.raise_()
    w.activateWindow()
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        fg = user32.GetForegroundWindow()
        other = user32.GetWindowThreadProcessId(fg, None)
        mine = kernel32.GetCurrentThreadId()
        if other and other != mine:
            user32.AttachThreadInput(other, mine, True)
            user32.BringWindowToTop(int(w.winId()))
            user32.SetForegroundWindow(int(w.winId()))
            user32.AttachThreadInput(other, mine, False)
    except Exception:  # noqa: BLE001 - si falla, igual queda visible
        pass


@contextmanager
def busy():
    QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


class _Relay(QObject):
    done = Signal(object, object)


_alive: list[_Relay] = []


def in_background(fn: Callable, callback: Callable) -> None:
    """Corre fn en otro hilo y llama callback(resultado, error) en la ventana principal."""
    relay = _Relay()
    _alive.append(relay)

    def finish(result, error) -> None:
        _alive.remove(relay)
        callback(result, error)

    relay.done.connect(finish, Qt.ConnectionType.QueuedConnection)

    def work() -> None:
        try:
            relay.done.emit(fn(), None)
        except Exception as exc:  # noqa: BLE001 - se entrega al callback
            relay.done.emit(None, exc)

    threading.Thread(target=work, daemon=True).start()


def pil_to_qimage(im) -> QImage:
    rgba = im.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def qimage_to_pil(img: QImage):
    from PIL import Image
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(img.constBits())[: img.width() * img.height() * 4]
    return Image.frombytes("RGBA", (img.width(), img.height()), data)


def section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setProperty("role", "section")
    return lab


def hint(text: str, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "hint")
    lab.setWordWrap(wrap)
    return lab


def hline() -> QFrame:
    f = QFrame()
    f.setProperty("role", "line")
    return f


def button(text: str, slot: Callable | None = None, role: str | None = None, tip: str = "",
           icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        from .icons import icon
        b.setIcon(icon(icon_name))
        b.setIconSize(QSize(18, 18))
    if role:
        b.setProperty("role", role)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class IntField(QLineEdit):
    """Casilla que solo acepta numeros enteros."""

    def __init__(self, lo: int = 1, hi: int = 30000, width: int = 80):
        super().__init__()
        self.setValidator(QIntValidator(lo, hi, self))
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def value(self) -> int:
        try:
            return int(self.text())
        except ValueError:
            return 0

    def setValue(self, v: int) -> None:
        self.setText(str(int(v)))


class SliderRow(QWidget):
    """Etiqueta + deslizador + valor + boton para volver a cero."""
    changed = Signal()

    def __init__(self, label: str, lo: int, hi: int, default: int = 0,
                 fmt: Callable[[int], str] = lambda v: f"{v:+d}", label_w: int = 92):
        super().__init__()
        self.default, self.fmt = default, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        name = QLabel(label)
        name.setFixedWidth(label_w)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(default)
        self.value_lab = QLabel(fmt(default))
        self.value_lab.setProperty("role", "value")
        self.value_lab.setFixedWidth(62)
        self.value_lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        undo = button("↺", self.reset, "mini", "Volver al valor inicial")
        lay.addWidget(name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_lab)
        lay.addWidget(undo)
        self.slider.valueChanged.connect(self._moved)

    def _moved(self, v: int) -> None:
        self.value_lab.setText(self.fmt(v))
        self.changed.emit()

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, v: int) -> None:
        self.slider.setValue(int(v))

    def reset(self) -> None:
        self.slider.setValue(self.default)


class Segmented(QFrame):
    """Botones unidos donde solo uno queda marcado (como un selector de iOS)."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], default: str | None = None):
        super().__init__()
        self.setProperty("role", "segbox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = button(text, role="seg")
            b.setCheckable(True)
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
        self.set_value(default or options[0][0])

    def value(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return ""

    def set_value(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class Swatches(QWidget):
    """Muestras de color para elegir con un clic."""
    changed = Signal(str)

    def __init__(self, colors: list[str], default: str | None = None, size: int = 22):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.group = QButtonGroup(self)
        self.colors = colors
        for c in colors:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(size, size)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            border = "#CBBEB1" if QColor(c).lightness() > 230 else c
            b.setStyleSheet(
                f"QPushButton {{ background: {c}; border: 1px solid {border}; border-radius: {size // 2}px; padding: 0; }}"
                f"QPushButton:checked {{ border: 3px solid {ACCENT}; }}")
            self.group.addButton(b)
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, col=c: self.changed.emit(col))
        lay.addStretch()
        self.set_value(default or colors[0])

    def value(self) -> str:
        for b, c in zip(self.group.buttons(), self.colors):
            if b.isChecked():
                return c
        return self.colors[0]

    def set_value(self, color: str) -> None:
        for b, c in zip(self.group.buttons(), self.colors):
            b.setChecked(c.lower() == color.lower())


class ReorderList(QWidget):
    """Lista de archivos con flechas para cambiar el orden."""
    changed = Signal()

    def __init__(self, paths: list[Path], thumbs: Callable[[Path], QPixmap | None] | None = None):
        super().__init__()
        self.paths = list(paths)
        self.thumbs = thumbs
        self._icons = QFileIconProvider()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self._cache: dict[Path, QPixmap | None] = {}
        self._rebuild()

    def _thumb(self, p: Path) -> QPixmap:
        if p not in self._cache:
            pix = self.thumbs(p) if self.thumbs else None
            if pix is None:
                from PySide6.QtCore import QFileInfo
                pix = self._icons.icon(QFileInfo(str(p))).pixmap(32, 32)
            self._cache[p] = pix
        return self._cache[p]

    def _rebuild(self) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, p in enumerate(self.paths):
            row = QFrame()
            row.setProperty("role", "card")
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 6, 8, 6)
            num = QLabel(str(i + 1))
            num.setFixedWidth(18)
            pic = QLabel()
            pic.setPixmap(self._thumb(p).scaled(QSize(64, 40), Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
            pic.setFixedWidth(68)
            name = QLabel(p.name)
            name.setToolTip(str(p))
            up = button("", lambda _=False, k=i: self.move(k, -1), "mini", "Subir", "up")
            down = button("", lambda _=False, k=i: self.move(k, 1), "mini", "Bajar", "down")
            up.setEnabled(i > 0)
            down.setEnabled(i < len(self.paths) - 1)
            for w in (num, pic, name):
                h.addWidget(w)
            h.addStretch()
            h.addWidget(up)
            h.addWidget(down)
            self.lay.addWidget(row)
        self.lay.addStretch()

    def move(self, i: int, d: int) -> None:
        j = i + d
        if 0 <= j < len(self.paths):
            self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
            self._rebuild()
            self.changed.emit()


class Panel(QDialog):
    """Ventana de una herramienta. Las subclases llenan build() y make_job()."""
    TITLE = "Panel"
    ACTION = "Guardar copia"
    WIDTH = 500
    RESET = True
    TALL = 0.0  # fraccion de la altura de pantalla para ventanas con vista previa grande (0 = la justa)

    def __init__(self, paths: list[Path]):
        super().__init__(None)
        self.setObjectName("panel")
        self.setWindowTitle(f"{self.TITLE} · Gajo")
        self.setWindowIcon(make_icon())
        self.setStyleSheet(QSS)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.paths = list(paths)
        self.job: Callable[[], list[Path]] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(16, 14, 16, 12)
        self.body.setSpacing(10)
        root.addWidget(holder, 1)
        root.addWidget(hline())
        foot = QHBoxLayout()
        foot.setContentsMargins(16, 10, 16, 12)
        if self.RESET:
            foot.addWidget(button("Reiniciar", self.reset))
        foot.addStretch()
        self.status = hint("", wrap=False)
        foot.addWidget(self.status)
        self.primary = button(self.ACTION, self._go, "primary")
        foot.addWidget(self.primary)
        root.addLayout(foot)
        self.setMinimumWidth(self.WIDTH)
        with busy():
            self.build()
        self.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        limit = int(geo.height() * 0.94)
        # Sin esto, al fijar el ancho minimo Qt deja achicar la ventana bajo lo que necesita su contenido
        # y los controles quedan encimados sobre la vista previa.
        self.setMinimumHeight(min(root.minimumSize().height(), limit))
        height = int(geo.height() * self.TALL) if self.TALL else self.height()
        self.resize(max(self.width(), self.WIDTH), max(self.minimumHeight(), min(height, limit)))
        self.move(geo.center().x() - self.width() // 2, geo.top() + (geo.height() - self.height()) // 2)

    # para las subclases ---------------------------------------------------
    def build(self) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def make_job(self) -> Callable[[], list[Path]] | None:
        raise NotImplementedError

    def cleanup(self) -> None:
        """Detener reproductores u otros recursos al cerrar."""

    # utilidades -----------------------------------------------------------
    def scroll_area(self, max_h: int | None = None) -> QVBoxLayout:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(6)
        area.setWidget(inner)
        if max_h:
            area.setMaximumHeight(max_h)
        area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body.addWidget(area, 1)
        return lay

    def row(self, *widgets, stretch_at: int | None = None) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(8)
        for i, w in enumerate(widgets):
            if w is None:
                h.addStretch()
            elif isinstance(w, str):
                h.addWidget(QLabel(w))
            else:
                h.addWidget(w, 1 if i == stretch_at else 0)
        self.body.addLayout(h)
        return h

    def warn(self, text: str) -> None:
        QMessageBox.warning(self, "Gajo", text)

    def _go(self) -> None:
        try:
            job = self.make_job()
        except GajoError as exc:
            self.warn(str(exc))
            return
        if job is not None:
            self.job = job
            self.accept()

    def done(self, result: int) -> None:
        self.cleanup()
        super().done(result)

    def closeEvent(self, e) -> None:
        self.cleanup()
        super().closeEvent(e)


def fmt_time(t: float, decimals: int = 2) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:0{3 + decimals}.{decimals}f}"


def parse_time(text: str) -> float:
    """Acepta '1:02.5', '62.5' o '1:02,5'."""
    text = text.strip().replace(",", ".")
    parts = text.split(":")
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return total
    except ValueError as exc:
        raise GajoError(f"Tiempo no válido: {text}") from exc


def swatch_icon(color: str, size: int = 14) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.end()
    return QIcon(pix)
