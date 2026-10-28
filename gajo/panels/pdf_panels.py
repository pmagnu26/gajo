"""Paneles de PDF: unir en orden y organizar paginas."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QListView, QListWidget, QListWidgetItem

from .. import editing as ED
from ..tools import GajoError
from .base import Panel, button, hint
from .video_panels import ReorderPanel

THUMB = 150


def _render(page, zoom: float) -> QImage:
    import fitz
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


def _first_page(path: Path) -> QPixmap | None:
    import fitz
    try:
        with fitz.open(path) as doc:
            return QPixmap.fromImage(_render(doc[0], 0.15))
    except Exception:  # noqa: BLE001
        return None


class MergePdfs(ReorderPanel):
    TITLE = "Unir PDF"
    ACTION = "Unir PDF"
    NOTE = "Parte con el orden en que los elegiste. Cambia el orden con las flechas."
    ENGINE = staticmethod(ED.pdf_merge_ordered)

    def thumb(self, path: Path) -> QPixmap | None:
        return _first_page(path)


class OrganizePdf(Panel):
    TITLE = "Organizar páginas"
    ACTION = "Guardar PDF organizado"
    WIDTH = 640

    def build(self) -> None:
        import fitz
        self.src = self.paths[0]
        self.thumbs: list[QImage] = []
        with fitz.open(self.src) as doc:
            for page in doc:
                zoom = THUMB / max(page.rect.width, page.rect.height)
                self.thumbs.append(_render(page, zoom))
        self.count = hint("", wrap=False)
        self.row(self.count, None, button("Reiniciar orden", self.reset))
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.ListMode)
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setSpacing(8)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setMinimumHeight(360)
        self.list.model().rowsMoved.connect(lambda *_: self.renumber())
        self.body.addWidget(self.list, 1)
        self.row(button("Girar", lambda: self.rotate(90), icon_name="rot_right"),
                 button("Girar", lambda: self.rotate(-90), icon_name="rot_left"),
                 button("Duplicar", self.duplicate, icon_name="duplicate"),
                 button("Quitar", self.remove, icon_name="trash"), None,
                 button("Girar todas", self.rotate_all, icon_name="rot_right"))
        self.same = QCheckBox("Dejar todas las páginas del mismo ancho")
        self.body.addWidget(self.same)
        self.body.addWidget(hint("Arrastra las miniaturas para cambiar el orden. Las páginas se escalan "
                                 "al ancho de la más angosta; la altura puede seguir variando."))
        self.reset()

    def _item(self, idx: int, rot: int) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, (idx, rot))
        item.setSizeHint(QSize(THUMB + 16, THUMB + 16))
        return item

    def _icon(self, idx: int, rot: int, number: int) -> QPixmap:
        img = self.thumbs[idx].transformed(QTransform().rotate(rot)) if rot else self.thumbs[idx]
        canvas = QPixmap(THUMB, THUMB)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, y = (THUMB - img.width()) / 2, (THUMB - img.height()) / 2
        p.setPen(QColor("#D8CBBD"))
        p.drawImage(QPointF(x, y), img)
        p.drawRect(QRectF(x, y, img.width() - 1, img.height() - 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E2701F"))
        p.drawEllipse(QPointF(x + 13, y + 13), 11, 11)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", 8, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRectF(x + 2, y + 2, 22, 22), Qt.AlignmentFlag.AlignCenter, str(number))
        p.end()
        return canvas

    def renumber(self) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            idx, rot = item.data(Qt.ItemDataRole.UserRole)
            item.setIcon(self._icon(idx, rot, row + 1))
            item.setToolTip(f"Página original {idx + 1}" + (f", girada {rot}°" if rot else ""))
        n = self.list.count()
        self.count.setText(f"{n} página{'s' if n != 1 else ''}")

    def selected_rows(self) -> list[int]:
        return sorted(self.list.row(i) for i in self.list.selectedItems())

    def rotate(self, deg: int, rows: list[int] | None = None) -> None:
        for row in rows if rows is not None else self.selected_rows():
            item = self.list.item(row)
            idx, rot = item.data(Qt.ItemDataRole.UserRole)
            item.setData(Qt.ItemDataRole.UserRole, (idx, (rot + deg) % 360))
        self.renumber()

    def rotate_all(self) -> None:
        self.rotate(90, list(range(self.list.count())))

    def duplicate(self) -> None:
        for row in reversed(self.selected_rows()):
            idx, rot = self.list.item(row).data(Qt.ItemDataRole.UserRole)
            self.list.insertItem(row + 1, self._item(idx, rot))
        self.renumber()

    def remove(self) -> None:
        for row in reversed(self.selected_rows()):
            self.list.takeItem(row)
        self.renumber()

    def reset(self) -> None:
        self.list.clear()
        for i in range(len(self.thumbs)):
            self.list.addItem(self._item(i, 0))
        self.renumber()

    def pages(self) -> list[tuple[int, int]]:
        return [self.list.item(r).data(Qt.ItemDataRole.UserRole) for r in range(self.list.count())]

    def make_job(self):
        pages = self.pages()
        if not pages:
            raise GajoError("El PDF quedaría sin páginas")
        original = [(i, 0) for i in range(len(self.thumbs))]
        if pages == original and not self.same.isChecked():
            raise GajoError("No cambiaste nada: reordena, gira, duplica o quita páginas")
        src, same = self.src, self.same.isChecked()
        return lambda: ED.pdf_organize(src, pages, same)
