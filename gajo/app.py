"""Arranque de Gajo: icono junto al reloj, rueda, vigilante de teclas y trabajos en segundo plano."""
from __future__ import annotations

import ctypes
import importlib
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__, catalog
from .capture import CaptureSession
from .catalog import Op
from .engines import make_zip
from .hotkey import Hotkeys, label as key_label
from .icon import make_icon
from .shelf import Shelf
from .tools import APPDATA, GajoError, unique_path
from .watcher import KeyWatcher
from .wheel import Wheel

log = logging.getLogger("gajo")
CONFIG = APPDATA / "config.json"
LOG_FILE = APPDATA / "gajo.log"
DEFAULT_KEYS = {"convertir": "shift", "herramientas": "ctrl", "acciones": "alt"}
DEFAULT_CAPTURE = {"zona": "ctrl+printscreen", "pantalla_completa": "shift+printscreen"}
CREATE_NEW_CONSOLE = 0x00000010

HELP = """<b>Gajo {v}</b> &mdash; conversor de archivos sin conexión.<br><br>
1. Empieza a arrastrar uno o varios archivos (desde el Explorador, el escritorio o el correo).<br>
2. Sin soltar, toca una tecla:<br>
&nbsp;&nbsp;&nbsp;<b>Shift</b> &rarr; convertir formato<br>
&nbsp;&nbsp;&nbsp;<b>Ctrl</b> &rarr; herramientas (comprimir, girar, unir, dividir…)<br>
&nbsp;&nbsp;&nbsp;<b>Alt</b> &rarr; acciones (repisa, copiar, mover, ruta, terminal)<br>
3. Suelta sobre la opción. Para cancelar, suelta en el centro.<br><br>
<b>{zona}</b> &rarr; captura una zona y dibuja encima (Ctrl+C copia, Ctrl+S guarda).<br>
<b>{completa}</b> &rarr; guarda la pantalla completa.<br>
En la captura, cada dibujo se mueve con clic derecho, se borra con Supr y la goma corta solo lo que toca.<br><br>
El resultado queda al lado del original, que nunca se toca. Todo ocurre en este PC, sin internet.<br>
{n} conversiones disponibles."""


def _lightshot_folder() -> str:
    """Carpeta donde Lightshot guardaba por ultima vez, para partir igual."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Skillbrains\Lightshot") as k:
            folder = winreg.QueryValueEx(k, "LastSavedDir")[0]
        return folder if Path(folder).is_dir() else ""
    except OSError:
        return ""


def load_config() -> dict:
    data = {"teclas": dict(DEFAULT_KEYS)}
    try:
        data.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        log.warning("config.json ilegible, uso teclas por defecto: %s", exc)
    changed = False
    if "captura" not in data:
        data["captura"] = dict(DEFAULT_CAPTURE)
        changed = True
    if "captura_carpeta" not in data:
        data["captura_carpeta"] = _lightshot_folder() or str(Path.home() / "Pictures")
        changed = True
    if changed or not CONFIG.exists():
        save_config(data)
    return data


def save_config(data: dict) -> None:
    try:
        CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("No pude guardar config.json: %s", exc)


class _Bridge(QObject):
    finished = Signal(str, object, object)  # descripcion, salidas, errores
    update = Signal(object, bool, str)      # info de version nueva, fue manual, error


class GajoApp(QObject):
    def __init__(self, qt: QApplication):
        super().__init__()
        self.qt = qt
        self.config = load_config()
        keys = {**DEFAULT_KEYS, **self.config.get("teclas", {})}
        self.shelf = Shelf()
        self.wheel = Wheel(self.action_ops, keys)
        self.wheel.winId()  # crea la ventana ya, para que Windows la registre como destino
        self.wheel.chosen.connect(self.execute)
        self.watcher = KeyWatcher(keys)
        self.watcher.summon.connect(self.wheel.summon)
        self.watcher.released.connect(self.wheel.mouse_released)
        self.watcher.cancel.connect(self.wheel.close_wheel)
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.bridge = _Bridge()
        self.bridge.finished.connect(self._finished)
        self.bridge.update.connect(self._update_checked)
        self.pending_update = None
        self._click = None
        self.running = 0
        self.panels: list = []
        self.last_output: Path | None = None

        self.capture: CaptureSession | None = None
        shortcuts = {**DEFAULT_CAPTURE, **self.config.get("captura", {})}
        self.shortcuts = shortcuts

        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        self.act_zona = menu.addAction("", self.start_capture)
        self.act_full = menu.addAction("", self.save_fullscreen)
        menu.addAction("Historial de capturas…", self.open_history)
        menu.addAction("Copiar última captura", self.copy_last)
        menu.addAction("Cambiar atajos de captura…", self.change_shortcuts)
        self._label_capture_actions()
        menu.addSeparator()
        menu.addAction("Cómo se usa", self.show_help)
        menu.addAction("Abrir la repisa", self.shelf.dock)
        menu.addSeparator()
        menu.addAction("Cambiar teclas (config.json)", lambda: os.startfile(CONFIG))
        menu.addAction("Ver registro", lambda: os.startfile(LOG_FILE))
        menu.addSeparator()
        menu.addAction("Buscar actualizaciones", lambda: self.check_updates(manual=True))
        self.act_auto = menu.addAction("Actualizar automáticamente")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(bool(self.config.get("actualizaciones", True)))
        self.act_auto.toggled.connect(self._toggle_auto_update)
        menu.addSeparator()
        menu.addAction("Salir", self.qt.quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_click)
        self.tray.messageClicked.connect(self._message_clicked)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(24 * 3600 * 1000)
        self.update_timer.timeout.connect(lambda: self.check_updates(manual=False))
        if self.config.get("actualizaciones", True):
            QTimer.singleShot(60_000, lambda: self.check_updates(manual=False))
            self.update_timer.start()
        self._tooltip()
        self.tray.show()

        self.hotkeys = Hotkeys(shortcuts)
        self.hotkeys.pressed.connect(self.on_hotkey)
        self.hotkeys.failed.connect(self._hotkey_busy)
        self.hotkeys.recovered.connect(
            lambda n: self.notify("Atajo listo", f"{key_label(self.shortcuts[n])} ahora abre la captura de Gajo."))
        self.qt.aboutToQuit.connect(self.hotkeys.release)
        from . import history
        import threading
        threading.Thread(target=history.prune, args=self.history_limits(), daemon=True).start()

    # ------------------------------------------------------------ captura de pantalla

    def on_hotkey(self, name: str) -> None:
        if name == "zona":
            self.start_capture()
        elif name == "pantalla_completa":
            self.save_fullscreen()

    def _label_capture_actions(self) -> None:
        self.act_zona.setText(f"Capturar zona ({key_label(self.shortcuts['zona'])})")
        self.act_full.setText(f"Guardar pantalla completa ({key_label(self.shortcuts['pantalla_completa'])})")

    def change_shortcuts(self) -> None:
        from .atajos import open_dialog
        if getattr(self, "_shortcuts_dlg", None) and self._shortcuts_dlg.isVisible():
            self._shortcuts_dlg.raise_()
            return
        self._shortcuts_dlg = open_dialog(self.hotkeys, self.shortcuts, self._shortcuts_saved)

    def _shortcuts_saved(self, new: dict) -> None:
        self.shortcuts = dict(new)
        self.config["captura"] = dict(new)
        save_config(self.config)
        self._label_capture_actions()
        self.notify("Atajos guardados", f"Capturar zona: {key_label(new['zona'])} · "
                    f"Pantalla completa: {key_label(new['pantalla_completa'])}")

    def _hotkey_busy(self, name: str) -> None:
        combo = key_label(self.shortcuts[name])
        self.notify("Atajo ocupado", f"{combo} lo tiene otro programa (¿Lightshot abierto?). "
                    "Gajo lo tomará apenas se libere; mientras, usa el menú del ícono.",
                    QSystemTrayIcon.MessageIcon.Warning)

    def start_capture(self, restore: dict | None = None) -> None:
        if self.capture is not None:
            return
        self.wheel.close_wheel()
        self.watcher.timer.stop()  # en la captura Shift y Ctrl sirven para dibujar, no para la rueda
        self.capture = CaptureSession(self.config, lambda: save_config(self.config), restore=restore)
        self.capture.done.connect(self._capture_done)
        self.capture.show()

    # ------------------------------------------------------------ historial de capturas

    def history_limits(self) -> tuple[int, int]:
        return int(self.config.get("historial_dias", 60)), int(self.config.get("historial_max", 500))

    def open_history(self) -> None:
        from .history_window import HistoryWindow
        from .panels.base import bring_to_front
        win = getattr(self, "_history_win", None)
        if win is not None and win.isVisible():
            win.reload()
            win.raise_()
            return
        self._history_win = HistoryWindow(lambda entry: self.start_capture(restore=entry), self.notify,
                                          *self.history_limits())
        bring_to_front(self._history_win)

    def copy_last(self) -> None:
        from PySide6.QtGui import QImage
        from . import history
        entry = history.latest()
        if not entry:
            self.notify("Historial vacío", "Todavía no hay capturas guardadas.")
            return
        QApplication.clipboard().setImage(QImage(str(entry["imagen"])))
        self.notify("Última captura copiada", f"{history.when(entry)} · pégala con Ctrl+V")

    def _capture_done(self, result: str, path) -> None:
        self.capture = None
        self.watcher.timer.start()
        if result == "copiada":
            self.notify("Captura copiada", "Pégala con Ctrl+V. También queda en el historial de capturas.")
        elif result == "texto":
            self.notify("Texto copiado", f"{path} palabras leídas. Pégalas con Ctrl+V.")
        elif result == "historial":
            QTimer.singleShot(0, self.open_history)
        elif result == "guardada" and path:
            self.last_output = Path(path)
            self.notify("Captura guardada", f"{Path(path).name}  (clic para verla)")

    def save_fullscreen(self) -> None:
        from datetime import datetime
        from PySide6.QtGui import QCursor, QGuiApplication
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        shot = screen.grabWindow(0)
        folder = Path(self.config.get("captura_carpeta") or Path.home())
        if not folder.is_dir():
            folder = Path.home()
        name = datetime.now().strftime("Pantalla %Y-%m-%d %H.%M.%S.png")
        path, _ = QFileDialog.getSaveFileName(None, "Guardar pantalla completa", str(folder / name),
                                              "PNG (*.png);;JPG (*.jpg)")
        if not path:
            return
        out = Path(path)
        shot.save(str(out), "JPG" if out.suffix.lower() in (".jpg", ".jpeg") else "PNG", 95)
        self.config["captura_carpeta"] = str(out.parent)
        save_config(self.config)
        self.last_output = out
        self.notify("Pantalla guardada", f"{out.name}  (clic para verla)")

    # ------------------------------------------------------------ acciones (Alt)

    def action_ops(self) -> list[Op]:
        return [
            Op("FLOTAR", "Dejar en una repisa flotante", self.shelf.add_paths, multi=True, ui=True),
            Op("COPIAR", "Copiar al portapapeles para pegar en una carpeta", self.copy_files,
               multi=True, ui=True),
            Op("MOVER", "Mover a otra carpeta", self.move_files, multi=True, ui=True),
            Op("COPIAR RUTA", "Copiar la ruta completa como texto", self.copy_paths, multi=True, ui=True),
            Op("TERMINAL", "Abrir una terminal en esa carpeta", self.open_terminal, multi=True, ui=True),
            Op("MOSTRAR", "Mostrar en el Explorador", self.reveal, multi=True, ui=True),
            Op("ZIP", "Comprimir en ZIP", make_zip, multi=True),
        ]

    def copy_files(self, paths: list[Path]) -> list[Path]:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        mime.setData('application/x-qt-windows-mime;value="Preferred DropEffect"', struct.pack("<I", 1))
        QApplication.clipboard().setMimeData(mime)
        self.notify("Copiado", f"{len(paths)} elemento(s) listos para pegar con Ctrl+V")
        return []

    def copy_paths(self, paths: list[Path]) -> list[Path]:
        QApplication.clipboard().setText("\n".join(str(p) for p in paths))
        self.notify("Ruta copiada", str(paths[0]) if len(paths) == 1 else f"{len(paths)} rutas")
        return []

    def move_files(self, paths: list[Path]) -> list[Path]:
        dest = QFileDialog.getExistingDirectory(None, "Mover a…", str(paths[0].parent))
        if not dest:
            return []
        moved = []
        for p in paths:
            target = Path(dest) / p.name
            if target.exists():
                target = unique_path(Path(dest), p.stem, p.suffix) if p.is_file() else Path(dest) / f"{p.name} (2)"
            shutil.move(str(p), str(target))
            moved.append(target)
        self.last_output = moved[0] if moved else None
        self.notify("Movido", f"{len(moved)} elemento(s) a {Path(dest).name}")
        return moved

    def open_terminal(self, paths: list[Path]) -> list[Path]:
        folder = paths[0] if paths[0].is_dir() else paths[0].parent
        wt = shutil.which("wt")
        if wt:
            subprocess.Popen([wt, "-d", str(folder)])
        else:
            subprocess.Popen(["powershell.exe", "-NoExit"], cwd=folder, creationflags=CREATE_NEW_CONSOLE)
        return []

    def reveal(self, paths: list[Path]) -> list[Path]:
        subprocess.Popen(f'explorer /select,"{paths[0]}"')
        return []

    # ------------------------------------------------------------ trabajos

    def execute(self, op: Op, paths: list[Path]) -> None:
        log.info("%s -> %s", op.hint, [str(p) for p in paths])
        if op.panel:
            self.open_panel(op, paths)
            return
        if op.ui:
            try:
                op.apply(paths)
            except Exception as exc:  # noqa: BLE001 - se informa al usuario
                log.exception("Accion fallida")
                self.notify("No se pudo", str(exc))
            return
        groups = [paths] if op.multi else [[p] for p in paths]
        tasks = [(g[0].name, lambda g=g: op.apply(g)) for g in groups]
        names = paths[0].name if len(paths) == 1 else f"{len(paths)} archivos"
        self.start_job(op.hint, tasks, names)

    def open_panel(self, op: Op, paths: list[Path]) -> None:
        from .panels.base import bring_to_front
        module, cls = op.panel.split(":")
        try:
            panel_cls = getattr(importlib.import_module(f".panels.{module}", __package__), cls)
            panel = panel_cls(paths)
        except GajoError as exc:
            self.notify("No se pudo abrir", str(exc), QSystemTrayIcon.MessageIcon.Warning)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Panel %s fallo al abrir", op.panel)
            self.notify("No se pudo abrir", str(exc), QSystemTrayIcon.MessageIcon.Warning)
            return
        self.panels.append(panel)
        panel.finished.connect(lambda _r, pnl=panel: self._panel_closed(pnl, op, paths))
        bring_to_front(panel)

    def _panel_closed(self, panel, op: Op, paths: list[Path]) -> None:
        if panel in self.panels:
            self.panels.remove(panel)
        job = panel.job
        panel.deleteLater()
        if job is not None:
            names = paths[0].name if len(paths) == 1 else f"{len(paths)} archivos"
            self.start_job(op.hint, [(names, job)], names)

    def start_job(self, hint: str, tasks: list, names: str) -> None:
        from .progress import Job
        from .progress_window import ProgressWindow
        self.running += 1
        self._tooltip()
        job = Job(f"{hint} · {names}", len(tasks))
        if getattr(self, "progress_win", None) is None:
            self.progress_win = ProgressWindow()
        self.progress_win.add(job)
        self.pool.submit(self._work, hint, tasks, job)

    def _work(self, hint: str, tasks: list, job=None) -> None:
        from . import progress
        outputs: list[Path] = []
        errors: list[str] = []
        progress.bind(job)
        try:
            for i, (name, task) in enumerate(tasks):
                if job is not None:
                    job.index, job.fraction, job.known, job.detail = i, 0.0, False, name if len(tasks) > 1 else ""
                try:
                    outputs += task()
                except progress.Cancelled:
                    errors.append("Cancelado")
                    break
                except GajoError as exc:
                    errors.append(str(exc))
                except Exception as exc:  # noqa: BLE001
                    log.exception("Fallo %s con %s", hint, name)
                    errors.append(f"{name}: {exc}")
        finally:
            progress.bind(None)
            if job is not None:
                job.ok, job.done = not errors, True
        self.bridge.finished.emit(hint, outputs, errors)

    def _finished(self, hint: str, outputs: list[Path], errors: list[str]) -> None:
        if getattr(self, "_updating", False) and hint == "Actualizar Gajo":
            self._updating = False
            self.running -= 1
            if errors:
                self.notify("No se pudo actualizar", errors[0], QSystemTrayIcon.MessageIcon.Warning)
                return
            from . import updater
            self.notify("Actualizado", "Gajo se está reiniciando con la versión nueva.")
            self.hotkeys.release()
            updater.restart()
            QTimer.singleShot(800, self.qt.quit)
            return
        self.running -= 1
        self._tooltip()
        if outputs:
            self.last_output = outputs[0]
        if errors == ["Cancelado"]:
            self.notify("Cancelado", hint + (f" ({len(outputs)} alcanzaron a quedar listos)" if outputs else ""))
            return
        if errors:
            for err in errors:
                log.warning("%s: %s", hint, err)
            ok = f"{len(outputs)} listo(s). " if outputs else ""
            self.notify("Hubo un problema", ok + errors[0], QSystemTrayIcon.MessageIcon.Warning)
        elif outputs:
            first = outputs[0].name
            more = f" y {len(outputs) - 1} más" if len(outputs) > 1 else ""
            self.notify("Listo", f"{first}{more}  (clic para verlo)")

    # ------------------------------------------------------------ bandeja

    def notify(self, title: str, text: str,
               icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
               click: str | None = None) -> None:
        self._click = click
        self.tray.showMessage(f"Gajo · {title}", text, icon, 8000 if click else 4000)

    def _message_clicked(self) -> None:
        if self._click == "update" and self.pending_update:
            self.install_update()
        else:
            self._reveal_last()

    # ------------------------------------------------------------ actualizaciones
    def _toggle_auto_update(self, on: bool) -> None:
        self.config["actualizaciones"] = on
        save_config(self.config)
        (self.update_timer.start if on else self.update_timer.stop)()

    def check_updates(self, manual: bool = False) -> None:
        import threading
        from . import updater
        if not manual and not self.config.get("actualizaciones", True):
            return

        def work():
            try:
                self.bridge.update.emit(updater.newer_available(), manual, "")
            except Exception as exc:  # noqa: BLE001 - sin internet, GitHub caido, etc.
                self.bridge.update.emit(None, manual, str(exc))
        threading.Thread(target=work, daemon=True).start()

    def _update_checked(self, info, manual: bool, error: str) -> None:
        from . import updater
        if error:
            log.info("No pude revisar actualizaciones: %s", error)
            if manual:
                self.notify("Sin conexión", "No pude revisar si hay una versión nueva. Intenta más tarde.")
            return
        if not info:
            if manual:
                self.notify("Al día", f"Tienes la última versión ({__version__}).")
            return
        self.pending_update = info
        if updater.is_dev_copy():
            self.notify("Versión nueva publicada", f"{info['version']} (esta es la copia de desarrollo: no se actualiza sola)")
            return
        self.notify("Hay una versión nueva", f"Gajo {info['version']} está disponible. Haz clic aquí para actualizar.",
                    click="update")

    def install_update(self) -> None:
        from . import updater
        info = self.pending_update
        if not info or updater.is_dev_copy():
            return
        msg = (f"¿Actualizar Gajo de la versión {__version__} a la {info['version']}?" + chr(10) * 2 +
               "Tus ajustes y el historial de capturas se conservan. Gajo se reiniciará solo.")
        if QMessageBox.question(None, "Gajo", msg) != QMessageBox.StandardButton.Yes:
            return
        self._updating = True
        self.start_job("Actualizar Gajo", [(f"versión {info['version']}", lambda: (updater.install(info), [])[1])],
                       f"versión {info['version']}")


    def _tooltip(self) -> None:
        self.tray.setToolTip(f"Gajo: trabajando ({self.running})" if self.running
                             else "Gajo: arrastra un archivo y toca Shift, Ctrl o Alt")

    def _tray_click(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_help()

    def _reveal_last(self) -> None:
        if self.last_output and self.last_output.exists():
            self.reveal([self.last_output])

    def show_help(self) -> None:
        box = QMessageBox()
        box.setWindowTitle("Gajo")
        box.setWindowIcon(make_icon())
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(HELP.format(v=__version__, n=catalog.count_conversions(),
                                zona=key_label(self.shortcuts["zona"]),
                                completa=key_label(self.shortcuts["pantalla_completa"])))
        box.exec()


def _setup_logging() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    sys.excepthook = lambda *exc: log.error("Error no controlado", exc_info=exc)


def _single_instance() -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "Local\\GajoConversor")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main(argv: list[str]) -> int:
    _setup_logging()
    qt = QApplication(argv)
    qt.setApplicationName("Gajo")
    qt.setQuitOnLastWindowClosed(False)

    if "--foto" in argv:  # dibuja la rueda en PNG para revisar el diseno
        i = argv.index("--foto")
        out, mode, hover = Path(argv[i + 1]), argv[i + 2], int(argv[i + 3])
        files = [Path(a) for a in argv[i + 4:]]
        wheel = Wheel(lambda: GajoApp.action_ops(_FakeApp()), DEFAULT_KEYS)
        wheel.snapshot(mode, files, hover, out)
        return 0

    if "--esperar" in argv:  # viene de una actualizacion: esperar que la version anterior se cierre
        try:
            pid = int(argv[argv.index("--esperar") + 1])
            h = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
            if h:
                ctypes.windll.kernel32.WaitForSingleObject(h, 20000)
                ctypes.windll.kernel32.CloseHandle(h)
        except (ValueError, IndexError):
            pass
    if not _single_instance():
        return 0
    app = GajoApp(qt)
    app.notify("Activo", "Arrastra un archivo y, sin soltar, toca Shift, Ctrl o Alt.")
    log.info("Gajo %s iniciado", __version__)
    return qt.exec()


class _FakeApp:
    """Solo para --foto: entrega las acciones sin crear bandeja ni repisa."""
    shelf = type("S", (), {"add_paths": staticmethod(lambda p: [])})()
    copy_files = move_files = copy_paths = open_terminal = reveal = staticmethod(lambda p: [])
