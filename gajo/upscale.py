"""Ampliar imagenes con IA (Real-ESRGAN, realesrgan-ncnn-vulkan).

El programa no viaja en el instalador: la primera vez que se usa se baja del lanzamiento oficial
(github.com/xinntao/Real-ESRGAN, 45 MB, sin cuenta), se verifica su huella y queda en
%LOCALAPPDATA%\\Gajo\\realesrgan. Usa la tarjeta de video (Vulkan); si no hay, falla con un mensaje claro.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import progress
from .tools import APPDATA, CREATE_NO_WINDOW, GajoError, unique_path

URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
       "realesrgan-ncnn-vulkan-20220424-windows.zip")
SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
EXE = "realesrgan-ncnn-vulkan.exe"
HOME = APPDATA / "realesrgan"
KEEP = {EXE, "vcomp140.dll", "realesrgan-x4plus.bin", "realesrgan-x4plus.param",
        "realesr-animevideov3-x2.bin", "realesr-animevideov3-x2.param",
        "realesr-animevideov3-x4.bin", "realesr-animevideov3-x4.param"}

# fotos: el modelo general; capturas y dibujos (PNG, GIF, BMP): el de lineas limpias
PHOTO, DRAWING = "realesrgan-x4plus", "realesr-animevideov3-x4"


def find_exe() -> Path | None:
    for d in (HOME, Path(os.path.expandvars(r"%USERPROFILE%\Herramientas\realesrgan"))):
        if (d / EXE).is_file() and (d / "models").is_dir():
            return d / EXE
    return None


def ensure() -> Path:
    """Devuelve el programa; si falta, lo baja (una sola vez) y verifica la huella."""
    exe = find_exe()
    if exe:
        return exe
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = HOME / "descarga.zip"
    req = urllib.request.Request(URL, headers={"User-Agent": "Gajo"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(256 * 1024):
                progress.check()
                f.write(chunk)
                done += len(chunk)
                if total:
                    progress.report(0.5 * done / total,
                                    f"Primera vez: bajando la IA ({done // 1048576} de {total // 1048576} MB)")
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise GajoError("No pude bajar la IA de ampliación (¿hay internet?)") from exc
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != SHA256:
        tmp.unlink(missing_ok=True)
        raise GajoError("La descarga de la IA llegó dañada; intenta de nuevo")
    with zipfile.ZipFile(tmp) as z:
        for name in z.namelist():
            base = Path(name).name
            if base in KEEP:
                dest = HOME / ("models" if "models/" in name else "") / base
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(name))
    tmp.unlink(missing_ok=True)
    exe = find_exe()
    if not exe:
        raise GajoError("El paquete de la IA no traía lo esperado")
    return exe


def run(src: Path, dest: Path, model: str, scale: int, base: float = 0.0, span: float = 1.0) -> None:
    """Corre Real-ESRGAN informando el avance (base..base+span de la barra)."""
    exe = ensure()
    cmd = [str(exe), "-i", str(src), "-o", str(dest), "-n", model, "-s", str(scale), "-f", "png"]
    proc = subprocess.Popen(cmd, cwd=exe.parent, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    tail: list[str] = []
    assert proc.stderr is not None
    for raw in proc.stderr:
        line = raw.decode("utf-8", "replace").strip()
        tail = (tail + [line])[-4:]
        m = re.match(r"([\d.]+)%", line)
        if m:
            progress.report(base + span * float(m.group(1)) / 100, "La IA está trabajando…")
        try:
            progress.check()
        except progress.Cancelled:
            proc.kill()
            raise
    if proc.wait() != 0 or not dest.exists():
        msg = " | ".join(tail)
        if "vkCreateInstance" in msg or "invalid gpu" in msg.lower():
            raise GajoError("Este PC no tiene una tarjeta de video compatible (Vulkan) para la IA")
        raise GajoError(f"La IA falló: {msg}")


def enlarge(src: Path, scale: int = 4) -> list[Path]:
    """Rueda: amplia una imagen con IA. Fotos con el modelo general; capturas con el de lineas."""
    from PIL import Image
    ext = src.suffix.lower()
    model = DRAWING if ext in (".png", ".gif", ".bmp", ".webp") and _looks_like_screen(src) else PHOTO
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "entrada.png"
        with Image.open(src) as im:
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            im.save(inp)
        out = Path(td) / "salida.png"
        first = find_exe() is None  # la primera vez, media barra es la descarga
        run(inp, out, model, 4, base=0.5 if first else 0.0, span=0.45 if first else 0.95)
        with Image.open(out) as big:
            big.load()
            if scale != 4:
                w, h = Image.open(inp).size
                big = big.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
            target = unique_path(src.parent, f"{src.stem}-IA-x{scale}", "png" if ext == ".png" else "jpg")
            if target.suffix == ".jpg":
                big.convert("RGB").save(target, "JPEG", quality=93)
            else:
                big.save(target, "PNG", optimize=False)
    progress.report(1.0, "Listo")
    return [target]


def enlarge2(src: Path) -> list[Path]:
    return enlarge(src, 2)


def _looks_like_screen(src: Path) -> bool:
    """Capturas y graficos tienen pocos colores distintos; las fotos, muchisimos."""
    from PIL import Image
    with Image.open(src) as im:
        small = im.convert("RGB")
        small.thumbnail((400, 400))
        colors = small.getcolors(maxcolors=20000)
    return colors is not None


def upscale_qimage(img, model: str = "realesr-animevideov3-x2", scale: int = 2):
    """Para la captura 'al doble': devuelve la QImage ampliada con IA, o None si no se puede."""
    from PySide6.QtGui import QImage
    exe = find_exe()
    if exe is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "a.png", Path(td) / "b.png"
        img.save(str(a), "PNG")
        try:
            subprocess.run([str(exe), "-i", str(a), "-o", str(b), "-n", model, "-s", str(scale)],
                           cwd=exe.parent, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = QImage(str(b))
        return None if out.isNull() else out.copy()
