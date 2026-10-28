"""Actualizaciones automaticas desde los lanzamientos publicos de GitHub (sin cuenta ni login).

Gajo consulta la ultima version publicada; si es mas nueva, la descarga, revisa su huella
SHA-256, reemplaza los archivos del programa (la configuracion y el historial viven aparte, en
%LOCALAPPDATA%\\Gajo) e instala librerias nuevas solo si cambio requirements.txt.
La copia de desarrollo (con el archivo .desarrollo) nunca se auto-actualiza.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import __version__, progress
from .tools import CREATE_NO_WINDOW, GajoError

log = logging.getLogger("gajo")
REPO = "pmagnu26/gajo"
APP_DIR = Path(__file__).resolve().parents[1]
API = "https://api.github.com/repos/{repo}/releases/latest"


def vtuple(v: str) -> tuple:
    out = []
    for part in v.lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


def is_dev_copy(app_dir: Path = APP_DIR) -> bool:
    return (app_dir / ".desarrollo").exists()


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"Gajo/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest(repo: str = REPO, api: str = API) -> dict | None:
    """{'version', 'zip_url', 'sha_url', 'notes'} de la ultima version publicada, o None."""
    data = json.loads(_get(api.format(repo=repo)))
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    zip_name = next((n for n in assets if n.endswith(".zip")), None)
    if not zip_name:
        return None
    return {"version": data.get("tag_name", "").lstrip("vV"), "zip_url": assets[zip_name],
            "sha_url": assets.get(zip_name + ".sha256"), "notes": data.get("body") or ""}


def newer_available(repo: str = REPO, api: str = API) -> dict | None:
    info = latest(repo, api)
    if info and vtuple(info["version"]) > vtuple(__version__):
        return info
    return None


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"Gajo/{__version__}"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress.report(0.8 * done / total, f"Descargando {done // 1048576} de {max(1, total // 1048576)} MB")


def install(info: dict, app_dir: Path = APP_DIR, python: str | None = None) -> str:
    """Descarga, verifica y reemplaza los archivos. Devuelve la version instalada."""
    if is_dev_copy(app_dir):
        raise GajoError("Esta es la copia de desarrollo: no se actualiza sola")
    tmp = Path(tempfile.mkdtemp(prefix="gajo-act-"))
    try:
        z = tmp / "gajo.zip"
        _download(info["zip_url"], z)
        if info.get("sha_url"):
            expected = _get(info["sha_url"]).decode("ascii", "ignore").split()[0].lower()
            actual = hashlib.sha256(z.read_bytes()).hexdigest()
            if expected != actual:
                raise GajoError("La descarga llegó dañada (no coincide su huella); no se instaló nada")
        progress.report(0.85, "Descomprimiendo")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp / "x")
        new_app = next((p for p in (tmp / "x").rglob("gajo.pyw") if p.parent.name == "app"), None)
        if new_app is None:
            raise GajoError("El paquete descargado no trae la app")
        new_app = new_app.parent
        old_req = (app_dir / "requirements.txt").read_text(encoding="utf-8") if (app_dir / "requirements.txt").exists() else ""
        new_req = (new_app / "requirements.txt").read_text(encoding="utf-8")
        progress.report(0.9, "Reemplazando archivos")
        for item in new_app.iterdir():
            target = app_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        if new_req.strip() != old_req.strip():
            progress.report(0.95, "Instalando librerías nuevas")
            py = python or str(Path(sys.executable).with_name("python.exe"))
            subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                            str(app_dir / "requirements.txt")], creationflags=CREATE_NO_WINDOW, timeout=1800)
        progress.report(1.0, "Listo")
        log.info("Gajo actualizado a %s", info["version"])
        return info["version"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def restart(app_dir: Path = APP_DIR) -> None:
    """Abre la version nueva; esta espera a que la actual se cierre antes de tomar los atajos."""
    import os
    pyw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pyw), str(app_dir / "gajo.pyw"), "--esperar", str(os.getpid())], cwd=str(app_dir))
