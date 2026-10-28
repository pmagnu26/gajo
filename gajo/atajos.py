"""Ventana para cambiar los atajos de la captura de pantalla."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .hotkey import Hotkeys, combo_from, label
from .icon import make_icon
from .panels.base import QSS, button, hint

DEFAULTS = {"zona": "ctrl+printscreen", "pantalla_completa": "shift+printscreen"}
NAMES = {"zona": "Capturar zona y dibujar", "pantalla_completa": "Guardar pantalla completa"}

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
VK_ESCAPE = 0x1B


class _KBD(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_user32.GetAsyncKeyState.restype = ctypes.c_short


def _down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _mods() -> int:
    m = 0
    if _down(0x11):
        m |= 0x2
    if _down(0x10):
        m |= 0x4
    if _down(0x12):
        m |= 0x1
    if _down(0x5B) or _down(0x5C):
        m |= 0x8
    return m


class KeyRecorder(QPushButton):
    """Boton que, al hacerle clic, graba la siguiente combinacion de teclas (incluida Impr Pant)."""
    changed = Signal(str)

    def __init__(self, combo: str):
        super().__init__()
        self.combo = combo
        self._hook = None
        self._proc = _HOOKPROC(self._on_key)  # se guarda: si Python lo libera, Windows llamaria a la nada
        self.setMinimumWidth(200)
        self.setMinimumHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self.start)
        self._show()

    def _show(self) -> None:
        self.setText(label(self.combo) if self.combo else "Sin atajo")
        self.setStyleSheet("")

    def start(self) -> None:
        if self._hook:
            return
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        self.setText("Presiona la combinación…")
        self.setStyleSheet("QPushButton { border: 2px solid #E2701F; background: #FFF4EA; }")

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            vk = _KBD.from_address(lparam).vkCode
            if vk not in MODIFIER_VKS:
                mods = _mods()
                QTimer.singleShot(0, lambda: self._finish(None if vk == VK_ESCAPE and not mods else
                                                          combo_from(mods, vk), vk == VK_ESCAPE and not mods))
                return 1  # la tecla no llega a otros programas mientras se graba
        return _user32.CallNextHookEx(None, code, wparam, lparam)

    def stop(self) -> None:
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _finish(self, combo: str | None, cancelled: bool) -> None:
        if not self._hook:
            return
        self.stop()
        if cancelled:
            self._show()
            return
        if combo is None:
            self._show()
            self.window().findChild(QLabel, "aviso").setText(
                "Esa tecla no sirve para atajo. Usa Impr Pant, F1–F12, Insert, Pausa, Inicio, Fin, "
                "Re Pág, Av Pág, una letra o un número, solos o con Ctrl, Shift o Alt.")
            return
        self.combo = combo
        self._show()
        self.changed.emit(combo)


class ShortcutsDialog(QDialog):
    def __init__(self, hotkeys: Hotkeys, current: dict[str, str], on_saved: Callable[[dict], None]):
        super().__init__(None)
        self.setObjectName("panel")
        self.setStyleSheet(QSS)
        self.setWindowTitle("Atajos de captura · Gajo")
        self.setWindowIcon(make_icon())
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(460)
        self.hotkeys, self.on_saved = hotkeys, on_saved
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        title = QLabel("Atajos de la captura de pantalla")
        title.setProperty("role", "title")
        lay.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        self.recorders: dict[str, KeyRecorder] = {}
        for r, key in enumerate(DEFAULTS):
            grid.addWidget(QLabel(NAMES[key]), r, 0)
            rec = KeyRecorder(current.get(key, DEFAULTS[key]))
            rec.changed.connect(lambda _c: self.aviso.setText(""))
            self.recorders[key] = rec
            grid.addWidget(rec, r, 1)
        lay.addLayout(grid)
        lay.addWidget(hint("Haz clic en un atajo y presiona la nueva combinación, por ejemplo Ctrl+Impr Pant o "
                           "Ctrl+Shift+S. Esc cancela la grabación."))
        self.aviso = hint("")
        self.aviso.setObjectName("aviso")
        self.aviso.setStyleSheet("color: #B3471B;")
        lay.addWidget(self.aviso)
        foot = QHBoxLayout()
        foot.addWidget(button("Restablecer", self.defaults))
        foot.addStretch()
        foot.addWidget(button("Cancelar", self.reject))
        foot.addWidget(button("Guardar", self.save, "primary"))
        lay.addLayout(foot)

    def defaults(self) -> None:
        for key, rec in self.recorders.items():
            rec.stop()
            rec.combo = DEFAULTS[key]
            rec._show()
        self.aviso.setText("")

    def save(self) -> None:
        new = {k: r.combo for k, r in self.recorders.items()}
        if new["zona"] == new["pantalla_completa"]:
            self.aviso.setText("Los dos atajos no pueden ser iguales.")
            return
        failed = self.hotkeys.apply(new)
        if failed:
            names = ", ".join(f"{label(new[n])} ({NAMES[n].lower()})" for n in failed)
            self.aviso.setText(f"Otro programa ya usa {names}. Se mantienen los atajos anteriores.")
            return
        self.on_saved(new)
        self.accept()

    def done(self, result: int) -> None:
        for rec in self.recorders.values():
            rec.stop()
        super().done(result)


def open_dialog(hotkeys: Hotkeys, current: dict, on_saved) -> ShortcutsDialog:
    from .panels.base import bring_to_front
    dlg = ShortcutsDialog(hotkeys, current, on_saved)
    bring_to_front(dlg)
    return dlg

