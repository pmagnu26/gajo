"""Ubicacion de programas externos (ffmpeg, LibreOffice, Pandoc) y utilidades comunes."""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

APPDATA = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Gajo"
APPDATA.mkdir(parents=True, exist_ok=True)


class GajoError(Exception):
    """Error con un mensaje legible para mostrarle al usuario."""


def _find(name: str, candidates: list[str]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for pattern in candidates:
        for hit in sorted(glob.glob(os.path.expandvars(pattern)), reverse=True):
            if os.path.isfile(hit):
                return hit
    return None


FFMPEG = _find("ffmpeg", [
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
])
FFPROBE = _find("ffprobe", [
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffprobe.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffprobe.exe",
    r"C:\ffmpeg\bin\ffprobe.exe",
])
SOFFICE = _find("soffice", [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
])
PANDOC = _find("pandoc", [
    r"%LOCALAPPDATA%\Pandoc\pandoc.exe",
    r"C:\Program Files\Pandoc\pandoc.exe",
])
BSDTAR = _find("tar", [r"C:\Windows\System32\tar.exe"])


def run(cmd: list, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Corre un programa sin abrir ventana de consola y falla con un mensaje claro."""
    cmd = [str(c) for c in cmd]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise GajoError(f"{Path(cmd[0]).stem} tardó demasiado y se detuvo") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-4:]
        raise GajoError(f"{Path(cmd[0]).stem} falló: " + " | ".join(tail))
    return proc


def need(tool: str | None, name: str) -> str:
    if not tool:
        raise GajoError(f"Falta {name} en este PC")
    return tool


def ffmpeg(args: list, timeout: int = 7200) -> None:
    from . import progress
    if progress.current() is None:
        run([need(FFMPEG, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args], timeout)
        return
    _ffmpeg_with_progress(args, timeout)


def _media_seconds(args: list) -> float:
    """Duracion a procesar: la del primer archivo de entrada, o lo pedido con -t."""
    args = [str(a) for a in args]
    if "-t" in args:
        try:
            return float(args[args.index("-t") + 1])
        except (ValueError, IndexError):
            pass
    for i, a in enumerate(args[:-1]):
        if a == "-i" and Path(args[i + 1]).is_file():
            try:
                return probe(Path(args[i + 1]))["duration"]
            except GajoError:
                return 0.0
    return 0.0


def _ffmpeg_with_progress(args: list, timeout: int) -> None:
    """ffmpeg informando por donde va (-progress) y deteniendose si el usuario cancela."""
    import time as _time
    from . import progress
    total = _media_seconds(args)
    cmd = [str(c) for c in [need(FFMPEG, "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                            "-progress", "pipe:1", "-nostats", *args]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace")
    start = _time.monotonic()
    try:
        for line in proc.stdout:
            if line.startswith("out_time_us=") and total > 0:
                try:
                    progress.report(int(line.split("=", 1)[1]) / 1e6 / total)
                except ValueError:
                    pass
            else:
                progress.check()
            if _time.monotonic() - start > timeout:
                raise GajoError("ffmpeg tardó demasiado y se detuvo")
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if proc.returncode != 0:
        tail = proc.stderr.read().strip().splitlines()[-4:]
        raise GajoError("ffmpeg falló: " + " | ".join(tail))


def probe(path: Path) -> dict:
    """Codecs y duracion de un archivo de audio o video."""
    proc = run([need(FFPROBE, "ffprobe"), "-v", "error", "-show_entries",
                "stream=codec_type,codec_name:format=duration", "-of", "json", path], 120)
    data = json.loads(proc.stdout or b"{}")
    info = {"v": None, "a": None, "duration": 0.0}
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and info["v"] is None and stream.get("codec_name") not in ("mjpeg", "png"):
            info["v"] = stream.get("codec_name")
        elif kind == "audio" and info["a"] is None:
            info["a"] = stream.get("codec_name")
    try:
        info["duration"] = float(data.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        pass
    return info


_lo_lock = threading.Lock()
_LO_PROFILE = APPDATA / "lo-perfil"


def soffice_convert(src: Path, fmt: str, outdir: Path, infilter: str | None = None) -> Path:
    """Convierte con LibreOffice sin interfaz. Devuelve el archivo creado en outdir."""
    exe = need(SOFFICE, "LibreOffice")
    cmd = [exe, f"-env:UserInstallation={_LO_PROFILE.as_uri()}", "--headless", "--norestore",
           "--nolockcheck", "--nodefault", "--convert-to", fmt, "--outdir", outdir]
    if infilter:
        cmd.append(f"--infilter={infilter}")
    cmd.append(src)
    from . import progress
    progress.check()
    with _lo_lock:  # LibreOffice no tolera dos conversiones a la vez con el mismo perfil
        run(cmd, timeout=900)
    progress.check()
    ext = fmt.split(":", 1)[0]
    out = outdir / f"{src.stem}.{ext}"
    if not out.exists():
        raise GajoError(f"LibreOffice no pudo convertir {src.name}")
    return out


def pandoc(args: list, timeout: int = 600) -> None:
    run([need(PANDOC, "Pandoc"), *args], timeout)


def work_dir() -> Path:
    base = APPDATA / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    """Ruta libre: 'foto.png', o 'foto (2).png' si ya existe. Nunca pisa un archivo."""
    ext = ext.lstrip(".")
    cand = folder / f"{stem}.{ext}"
    n = 2
    while cand.exists():
        cand = folder / f"{stem} ({n}).{ext}"
        n += 1
    return cand


def unique_dir(folder: Path, name: str) -> Path:
    cand = folder / name
    n = 2
    while cand.exists():
        cand = folder / f"{name} ({n})"
        n += 1
    cand.mkdir(parents=True)
    return cand


def place(tmp_file: Path, folder: Path, stem: str, ext: str) -> Path:
    """Mueve un resultado temporal a su destino final con nombre libre."""
    final = unique_path(folder, stem, ext)
    shutil.move(str(tmp_file), str(final))
    return final


def base_stem(path: Path) -> str:
    name = path.name
    for double in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.lower().endswith(double):
            return name[: -len(double)]
    return path.stem


def norm_ext(path: Path) -> str:
    name = path.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        return "tgz"
    ext = path.suffix.lower().lstrip(".")
    return {"jpeg": "jpg", "tif": "tiff", "heif": "heic", "aif": "aiff", "htm": "html",
            "markdown": "md", "m4v": "mp4"}.get(ext, ext)
