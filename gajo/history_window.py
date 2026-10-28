"""Ventana Historial de capturas: copiar otra vez, seguir editando, guardar, mostrar o borrar."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDrag, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QVBoxLayout,
)

from . import history
from .icon import make_icon
from .panels.base import QSS, button, hint

ACTIONS = {"copiada": "copiada", "guardada": "guardada", "texto": "texto copiado", "descartada": "cerrada sin copiar"}
THUMB = QSize(220, 140)


class _Grid(QListWidget):
    """Miniaturas que se pueden arrastrar a otra app (WhatsApp, correo, carpeta)."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(THUMB)
        self.setGridSize(QSize(THUMB.width() + 24, THUMB.height() + 46))
        self.setSpacing(6)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setStyleSheet("QListWidget::item { padding: 4px; border-radius: 8px; color: #35291F; }"
                           "QListWidget::item:selected { background: #F6DDC8; }")

    def startDrag(self, _actions) -> None:
        items = self.selectedItems()
        if not items:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(i.data(Qt.ItemDataRole.UserRole)["imagen"])) for i in items])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(items[0].icon().pixmap(QSize(120, 76)))
        drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)


class HistoryWindow(QDialog):
    def __init__(self, on_edit, notify, days: int, max_items: int):
        super().__init__(None)
        self.on_edit, self.notify = on_edit, notify
        self.days, self.max_items = days, max_items
        self.setObjectName("panel")
        self.setStyleSheet(QSS)
        self.setWindowTitle("Historial de capturas · Gajo")
        self.setWindowIcon(make_icon())
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(820, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("Historial de capturas")
        title.setProperty("role", "title")
        self.count = hint("", wrap=False)
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.count)
        head.addStretch()
        head.addWidget(button("Abrir carpeta", self.open_folder))
        lay.addLayout(head)
        self.grid = _Grid()
        self.grid.itemDoubleClicked.connect(lambda _i: self.copy())
        self.grid.itemSelectionChanged.connect(self.refresh_buttons)
        lay.addWidget(self.grid, 1)
        self.empty = hint("Todavía no hay capturas. Usa Ctrl+Impr Pant y aparecerán aquí.", wrap=False)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)
        foot = QHBoxLayout()
        self.btn_copy = button("Copiar", self.copy, icon_name="copy")
        self.btn_edit = button("Seguir editando", self.edit, icon_name="pen")
        self.btn_save = button("Guardar como…", self.save_as, icon_name="save")
        self.btn_show = button("Mostrar archivo", self.reveal)
        self.btn_del = button("Borrar", self.delete, icon_name="trash")
        for b in (self.btn_copy, self.btn_edit, self.btn_save, self.btn_show):
            foot.addWidget(b)
        foot.addStretch()
        foot.addWidget(self.btn_del)
        lay.addLayout(foot)
        lay.addWidget(hint(f"Doble clic copia · arrastra una miniatura a WhatsApp, un correo o una carpeta · "
                           f"se guardan {days} días (máximo {max_items})", wrap=False))
        self._pending: list[dict] = []
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._load_batch)
        self.reload()

    # ------------------------------------------------------------ carga
    def reload(self) -> None:
        history.prune(self.days, self.max_items)
        self.grid.clear()
        self._pending = history.entries()
        n = len(self._pending)
        self.count.setText(f"{n} captura{'s' if n != 1 else ''}")
        self.empty.setVisible(n == 0)
        self.grid.setVisible(n > 0)
        self._timer.start()
        self.refresh_buttons()

    def _load_batch(self) -> None:
        """De a 30 miniaturas, para que la ventana abra al tiro aunque haya cientos."""
        for _ in range(30):
            if not self._pending:
                self._timer.stop()
                if self.grid.count() and not self.grid.selectedItems():
                    self.grid.item(0).setSelected(True)
                return
            entry = self._pending.pop(0)
            mini = QImage(str(entry["mini"])) if Path(entry["mini"]).exists() else QImage()
            if mini.isNull():
                mini = QImage(str(entry["imagen"])).scaled(THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation)
            label = history.when(entry)
            extra = ACTIONS.get(entry.get("accion", ""), "")
            item = QListWidgetItem(QIcon(QPixmap.fromImage(mini)), f"{label}\n{extra}" if extra else label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(f"{entry.get('ancho')} × {entry.get('alto')} px · {len(entry.get('dibujos', []))} dibujos")
            self.grid.addItem(item)

    def selected(self) -> list[dict]:
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.grid.selectedItems()]

    def refresh_buttons(self) -> None:
        n = len(self.selected())
        for b in (self.btn_copy, self.btn_edit, self.btn_save, self.btn_show):
            b.setEnabled(n == 1)
        self.btn_del.setEnabled(n >= 1)

    # ------------------------------------------------------------ acciones
    def copy(self) -> None:
        sel = self.selected()
        if len(sel) == 1:
            QApplication.clipboard().setImage(QImage(str(sel[0]["imagen"])))
            self.notify("Captura copiada", "Pégala con Ctrl+V donde la necesites.")

    def edit(self) -> None:
        sel = self.selected()
        if len(sel) == 1 and Path(sel[0]["original"]).exists():
            self.close()
            self.on_edit(sel[0])

    def save_as(self) -> None:
        sel = self.selected()
        if len(sel) != 1:
            return
        src = Path(sel[0]["imagen"])
        path, _ = QFileDialog.getSaveFileName(self, "Guardar captura", str(Path.home() / f"Captura {sel[0]['id'][:15]}.png"),
                                              "PNG (*.png);;JPG (*.jpg)")
        if path:
            out = Path(path)
            if out.suffix.lower() in (".jpg", ".jpeg"):
                QImage(str(src)).save(str(out), "JPG", 95)
            else:
                shutil.copyfile(src, out)
            self.notify("Captura guardada", out.name)

    def reveal(self) -> None:
        sel = self.selected()
        if len(sel) == 1:
            subprocess.Popen(f'explorer /select,"{sel[0]["imagen"]}"')

    def open_folder(self) -> None:
        history.HIST.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(history.HIST)])

    def delete(self, confirm: bool = True) -> None:
        sel = self.selected()
        if not sel:
            return
        n = len(sel)
        if confirm and QMessageBox.question(
                self, "Gajo", f"¿Borrar {n} captura{'s' if n > 1 else ''} del historial? No se puede deshacer.",
        ) != QMessageBox.StandardButton.Yes:
            return
        for entry in sel:
            history.delete(entry["id"])
        self.reload()
