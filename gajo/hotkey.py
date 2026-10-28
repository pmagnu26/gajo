"""Atajos de teclado globales de Windows (RegisterHotKey), como los de Lightshot."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

log = logging.getLogger("gajo")
_user32 = ctypes.windll.user32
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
KEYS = {"printscreen": 0x2C, "imprpant": 0x2C, "prtsc": 0x2C, "insert": 0x2D, "pause": 0x13,
        "scrolllock": 0x91, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        **{f"f{i}": 0x6F + i for i in range(1, 25)}}
NAMES = {0x2C: "Impr Pant", 0x2D: "Insert", 0x13: "Pausa", 0x91: "Bloq Despl", 0x24: "Inicio", 0x23: "Fin",
         0x21: "Re Pág", 0x22: "Av Pág"}
_VK_TO_KEY = {vk: name for name, vk in KEYS.items() if name not in ("imprpant", "prtsc")}


def parse(combo: str) -> tuple[int, int] | None:
    """'ctrl+printscreen' -> (modificadores, tecla virtual)."""
    mods, vk = 0, None
    for part in combo.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in KEYS:
            vk = KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
    return (mods, vk) if vk else None


def combo_from(mods: int, vk: int) -> str | None:
    """(modificadores, tecla virtual) -> 'ctrl+printscreen'. None si la tecla no sirve para atajo."""
    if vk in _VK_TO_KEY:
        key = _VK_TO_KEY[vk]
    elif 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        key = chr(vk).lower()
    else:
        return None
    words = [n for n, m in (("ctrl", 0x2), ("shift", 0x4), ("alt", 0x1), ("win", 0x8)) if mods & m]
    return "+".join(words + [key])


def label(combo: str) -> str:
    parsed = parse(combo)
    if not parsed:
        return combo
    mods, vk = parsed
    words = [n for n, m in (("Ctrl", 0x2), ("Shift", 0x4), ("Alt", 0x1), ("Win", 0x8)) if mods & m]
    return "+".join(words + [NAMES.get(vk, chr(vk) if 0x30 <= vk <= 0x5A else f"F{vk - 0x6F}")])


class Hotkeys(QWidget):
    """Ventana invisible que recibe los atajos. Reintenta los que otro programa tenga tomados."""
    pressed = Signal(str)
    failed = Signal(str)     # nombre del atajo que no se pudo tomar (aviso una sola vez)
    recovered = Signal(str)  # se libero y ahora lo tiene Gajo

    def __init__(self, bindings: dict[str, str]):
        super().__init__(None, Qt.WindowType.Tool)
        self.hwnd = int(self.winId())
        self.bindings = bindings
        self.ids: dict[int, str] = {}
        self.active: set[str] = set()
        self.warned: set[str] = set()
        self.retry = QTimer(self)
        self.retry.setInterval(20_000)
        self.retry.timeout.connect(self.register_all)
        self.register_all()

    def register_all(self) -> None:
        for i, (name, combo) in enumerate(self.bindings.items(), start=1):
            if name in self.active:
                continue
            parsed = parse(combo)
            if not parsed:
                log.warning("Atajo no valido para %s: %s", name, combo)
                continue
            ok = bool(_user32.RegisterHotKey(self.hwnd, i, parsed[0] | MOD_NOREPEAT, parsed[1]))
            if ok:
                self.ids[i] = name
                self.active.add(name)
                log.info("Atajo %s listo para %s", label(combo), name)
                if name in self.warned:
                    self.recovered.emit(name)
            elif name not in self.warned:
                self.warned.add(name)
                log.warning("Atajo %s ocupado por otro programa (%s)", label(combo), name)
                self.failed.emit(name)
        if len(self.active) < len(self.bindings):
            self.retry.start()
        else:
            self.retry.stop()

    def nativeEvent(self, event_type, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self.ids:
                self.pressed.emit(self.ids[msg.wParam])
                return True, 0
        except (TypeError, ValueError, OSError):
            pass
        return False, 0

    def apply(self, bindings: dict[str, str]) -> list[str]:
        """Cambia los atajos en vivo. Si alguno no se puede tomar, vuelve a los anteriores y lo informa."""
        old = dict(self.bindings)
        self.release()
        self.bindings = dict(bindings)
        failed = []
        for i, (name, combo) in enumerate(self.bindings.items(), start=1):
            parsed = parse(combo)
            if parsed and _user32.RegisterHotKey(self.hwnd, i, parsed[0] | MOD_NOREPEAT, parsed[1]):
                self.ids[i] = name
                self.active.add(name)
            else:
                failed.append(name)
        if failed:
            self.release()
            self.bindings = old
        self.warned = set()
        self.register_all()
        return failed

    def release(self) -> None:
        for i in list(self.ids):
            _user32.UnregisterHotKey(self.hwnd, i)
        self.ids.clear()
        self.active.clear()
