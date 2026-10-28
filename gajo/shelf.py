"""Repisa flotante (accion "Flotar"): deja archivos a mano para arrastrarlos a otra app."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QSize, Qt, QUrl
from PySide6.QtGui import QDrag, QGuiApplication
from PySide6.QtWidgets import (
    QFileIconProvider, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

_STYLE = """
#panel { background: #FBF8F4; border: 1px solid #DCCFC1; border-radius: 12px; }
#title { color: #E2701F; font: 600 9pt 'Segoe UI'; }
QListWidget { background: transparent; border: none; font: 9pt 'Segoe UI'; color: #35291F; }
QListWidget::item { padding: 4px; border-radius: 6px; }
QListWidget::item:selected { background: #F6DDC8; color: #35291F; }
QPushButton { border: none; color: #76685C; font: 600 9pt 'Segoe UI'; padding: 2px 6px; }
QPushButton:hover { color: #E2701F; }
"""


class _ShelfList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(False)  # lo que llega desde afuera lo recibe la repisa
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(28, 28))

    def startDrag(self, _actions) -> None:
        items = self.selectedItems()
        if not items:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(i.data(Qt.ItemDataRole.UserRole)) for i in items])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(items[0].icon().pixmap(32, 32))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.LinkAction, Qt.DropAction.CopyAction)


class Shelf(QWidget):
    def __init__(self):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setStyleSheet(_STYLE)
        self._icons = QFileIconProvider()
        self._grab: QPoint | None = None

        panel = QFrame(objectName="panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 8, 10, 10)
        head = QHBoxLayout()
        self.title = QLabel("REPISA", objectName="title")
        clear = QPushButton("Vaciar")
        clear.clicked.connect(self.clear)
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(clear)
        lay.addLayout(head)
        self.list = _ShelfList()
        lay.addWidget(self.list)
        self.setFixedWidth(250)

    def add_paths(self, paths: list[Path]) -> list[Path]:
        known = {self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())}
        for p in paths:
            if str(p) in known:
                continue
            item = QListWidgetItem(self._icons.icon(QFileInfo(str(p))), p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip(str(p))
            self.list.addItem(item)
        self.dock()
        return []

    def clear(self) -> None:
        self.list.clear()
        self.hide()

    def dock(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        rows = max(1, self.list.count())
        self.setFixedHeight(min(60 + rows * 38, int(geo.height() * 0.7)))
        if not self.isVisible():
            self.move(geo.right() - self.width() - 12, geo.top() + (geo.height() - self.height()) // 2)
        self.show()
        self.raise_()

    # mover la repisa arrastrando su borde
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._grab = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._grab is not None:
            self.move(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, _e) -> None:
        self._grab = None

    # recibir mas archivos
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()

    def dragMoveEvent(self, e) -> None:
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()

    def dropEvent(self, e) -> None:
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()
        self.add_paths([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])
