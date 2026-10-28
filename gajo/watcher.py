"""Vigila el teclado y el mouse de todo Windows para saber cuando abrir la rueda.

No instala ganchos globales: solo consulta cada 15 ms el estado de las teclas
(GetAsyncKeyState), igual que un juego. Si el boton izquierdo esta apretado y
el cursor ya se movio (un arrastre) y se toca Shift, Ctrl o Alt, avisa.
"""
from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QCursor

_user32 = ctypes.windll.user32
_user32.GetAsyncKeyState.restype = ctypes.c_short
VK = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12}
VK_ESCAPE = 0x1B
SM_SWAPBUTTON = 23


def _down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


class KeyWatcher(QObject):
    summon = Signal(str)   # modo a abrir
    released = Signal()    # se solto el boton del mouse
    cancel = Signal()      # Esc

    def __init__(self, keys: dict[str, str]):
        super().__init__()
        self.keys = {mode: VK[key] for mode, key in keys.items() if key in VK}
        self.prev = {vk: False for vk in [*self.keys.values(), VK_ESCAPE]}
        self.button_down = False
        self.dragging = False
        self.origin = None
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def poll(self) -> None:
        left = 0x02 if _user32.GetSystemMetrics(SM_SWAPBUTTON) else 0x01
        if _down(left):
            pos = QCursor.pos()
            if not self.button_down:
                self.button_down, self.dragging, self.origin = True, False, pos
            elif not self.dragging and (pos - self.origin).manhattanLength() > 8:
                self.dragging = True
        elif self.button_down:
            self.button_down = self.dragging = False
            self.released.emit()

        for mode, vk in self.keys.items():
            now = _down(vk)
            if now and not self.prev[vk] and self.dragging:
                self.summon.emit(mode)
            self.prev[vk] = now

        esc = _down(VK_ESCAPE)
        if esc and not self.prev[VK_ESCAPE]:
            self.cancel.emit()
        self.prev[VK_ESCAPE] = esc
