"""Historial de capturas: cada captura queda guardada sola, con sus dibujos editables.

Por cada captura se guardan tres archivos en %LOCALAPPDATA%\\Gajo\\historial:
  <id>.png           la imagen final, con los dibujos (lo que se copio o guardo)
  <id>-original.png  la zona tal como estaba en pantalla, sin dibujos
  <id>.json          los dibujos como objetos (para seguir editando), fechas y accion
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt

from .tools import APPDATA

log = logging.getLogger("gajo")
HIST = APPDATA / "historial"
_lock = threading.Lock()


def new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def paths(entry_id: str) -> tuple[Path, Path, Path]:
    return HIST / f"{entry_id}.png", HIST / f"{entry_id}-original.png", HIST / f"{entry_id}.json"


def add(rendered, region, shapes: list, dpr: float, action: str, entry_id: str | None = None) -> str:
    """Guarda (o actualiza, si viene entry_id) una captura. Se puede llamar desde otro hilo."""
    entry_id = entry_id or new_id()
    final, original, meta = paths(entry_id)
    with _lock:
        HIST.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat(timespec="milliseconds")  # con milesimas: el orden nunca empata
        created = now
        if meta.exists():
            try:
                created = json.loads(meta.read_text(encoding="utf-8")).get("creada", now)
            except (OSError, ValueError):
                pass
        rendered.save(str(final), "PNG")
        mini = rendered.scaled(320, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        mini.save(str(HIST / f"{entry_id}-mini.jpg"), "JPG", 85)
        if not original.exists():  # la zona limpia no cambia al volver a editar
            region.save(str(original), "PNG")
        meta.write_text(json.dumps({
            "creada": created, "editada": now, "accion": action, "dpr": dpr,
            "ancho": rendered.width(), "alto": rendered.height(), "dibujos": shapes,
        }, ensure_ascii=False), encoding="utf-8")
    return entry_id


def entries() -> list[dict]:
    """Capturas del historial, la mas reciente primero."""
    out = []
    for meta in HIST.glob("*.json") if HIST.exists() else []:
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entry_id = meta.stem
        final, original, _ = paths(entry_id)
        if not final.exists():
            continue
        data.update(id=entry_id, imagen=final, original=original, mini=HIST / f"{entry_id}-mini.jpg")
        out.append(data)
    out.sort(key=lambda d: (d.get("editada", ""), d["id"]), reverse=True)
    return out


def latest() -> dict | None:
    items = entries()
    return items[0] if items else None


def delete(entry_id: str) -> None:
    with _lock:
        for p in (*paths(entry_id), HIST / f"{entry_id}-mini.jpg"):
            p.unlink(missing_ok=True)


def prune(days: int = 60, max_items: int = 500) -> int:
    """Borra lo que tenga mas de `days` dias y, si sobran, las mas antiguas. Devuelve cuantas borro."""
    items = entries()
    limit = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    doomed = [d["id"] for i, d in enumerate(items) if d.get("editada", "") < limit or i >= max_items]
    for entry_id in doomed:
        delete(entry_id)
    if doomed:
        log.info("Historial: %d capturas antiguas borradas", len(doomed))
    return len(doomed)


def when(entry: dict) -> str:
    """'Hoy 15:42', 'Ayer 10:03' o '17-10 09:12'."""
    try:
        t = datetime.fromisoformat(entry.get("editada") or entry.get("creada"))
    except (TypeError, ValueError):
        return ""
    today = datetime.now().date()
    if t.date() == today:
        day = "Hoy"
    elif t.date() == today - timedelta(days=1):
        day = "Ayer"
    else:
        day = t.strftime("%d-%m")
    return f"{day} {t:%H:%M}"


def save_async(*args, **kwargs) -> threading.Thread:
    """Guardar sin congelar la pantalla (codificar un PNG grande toma un momento)."""
    def work():
        try:
            add(*args, **kwargs)
        except Exception:  # noqa: BLE001 - el historial nunca debe botar la captura
            log.exception("No pude guardar la captura en el historial")
    th = threading.Thread(target=work, daemon=True)
    th.start()
    return th

