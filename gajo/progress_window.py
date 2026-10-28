"""Ventanita flotante abajo a la derecha con el avance de los trabajos largos."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from .progress import Job

SHOW_AFTER = 0.6   # segundos: los trabajos rapidos no alcanzan a mostrar nada
LINGER = 1.2       # segundos que queda "Listo" antes de desaparecer
QSS = """
QFrame#card { background: #FBF8F4; border: 1px solid #D9CCBE; border-radius: 10px; }
QLabel { color: #35291F; font: 9pt 'Segoe UI'; }
QLabel#detail { color: #8A7C70; font: 8pt 'Segoe UI'; }
QProgressBar { background: #EFE7DD; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #E2701F; border-radius: 4px; }
QPushButton { background: transparent; border: none; color: #A3958A; font: 10pt 'Segoe UI'; padding: 0 4px; }
QPushButton:hover { color: #C0392B; }
"""


class _Row(QFrame):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self.done_at: float | None = None
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 10)
        lay.setSpacing(5)
        head = QHBoxLayout()
        self.title = QLabel()
        self.cancel = QPushButton("✕")
        self.cancel.setToolTip("Cancelar")
        self.cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel.clicked.connect(self._cancel)
        head.addWidget(self.title, 1)
        head.addWidget(self.cancel)
        lay.addLayout(head)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        lay.addWidget(self.bar)
        self.detail = QLabel()
        self.detail.setObjectName("detail")
        lay.addWidget(self.detail)
        self.setFixedWidth(340)

    def _cancel(self) -> None:
        self.job.cancelled = True
        self.cancel.setEnabled(False)
        self.detail.setText("Cancelando…")

    def refresh(self) -> None:
        j = self.job
        fm = self.title.fontMetrics()
        self.title.setText(fm.elidedText(j.title, Qt.TextElideMode.ElideMiddle, 280))
        if j.done:
            self.bar.setRange(0, 1000)
            self.bar.setValue(1000 if j.ok else self.bar.value())
            self.detail.setText("Listo" if j.ok else ("Cancelado" if j.cancelled else "Terminó con problemas"))
            self.cancel.hide()
            return
        if j.known or j.total > 1:
            self.bar.setRange(0, 1000)
            self.bar.setValue(round(j.overall() * 1000))
            pct = f"{j.overall():.0%}"
        else:
            self.bar.setRange(0, 0)  # sin porcentaje: barra en movimiento
            pct = "trabajando…"
        parts = [f"{j.index + 1} de {j.total}"] if j.total > 1 else []
        parts.append(pct)
        if j.detail:
            parts.append(j.detail)
        if not j.cancelled:
            self.detail.setText(" · ".join(parts))


class ProgressWindow(QWidget):
    def __init__(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(QSS)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.rows: list[_Row] = []
        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.tick)

    def add(self, job: Job) -> None:
        row = _Row(job)
        row.hide()
        self.rows.append(row)
        self.lay.addWidget(row)
        self.timer.start()

    def tick(self) -> None:
        now = time.monotonic()
        for row in list(self.rows):
            job = row.job
            if job.done and row.done_at is None:
                row.done_at = now
            if row.done_at is not None and (now - row.done_at > LINGER or row.isHidden()):
                self.rows.remove(row)
                row.deleteLater()
                continue
            if row.isHidden() and now - job.started >= SHOW_AFTER:
                row.show()
            if not row.isHidden():
                row.refresh()
        visible = [r for r in self.rows if not r.isHidden()]
        if not visible:
            self.hide()
            if not self.rows:
                self.timer.stop()
            return
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)
        if self.isHidden():
            self.show()
