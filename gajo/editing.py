"""Motores con parametros para los paneles de edicion (imagen, video, audio y PDF).

Todo recibe coordenadas normalizadas (0 a 1) para que la vista previa, que usa
una copia chica, y el archivo final, a resolucion completa, den lo mismo.
Ningun motor toca el original: el resultado queda al lado con nombre nuevo.
"""
from __future__ import annotations

import io
import json
import math
import re
import shutil
from pathlib import Path

import numpy as np

from .engines import AUDIO_ARGS, _flatten, _has_alpha, _same_fmt, open_image, save_image
from .tools import (
    FFMPEG, GajoError, ffmpeg, need, norm_ext, probe, run, unique_dir, unique_path, work_dir,
)

MODELS = Path(__file__).resolve().parents[1] / "modelos"

# ================================================================= utilidades

def media_info(path: Path) -> dict:
    """Ancho, alto, duracion, fps y si tiene audio/video."""
    from .tools import FFPROBE
    proc = run([need(FFPROBE, "ffprobe"), "-v", "error", "-show_streams", "-show_format",
                "-of", "json", path], 120)
    data = json.loads(proc.stdout or b"{}")
    info = {"w": 0, "h": 0, "duration": 0.0, "fps": 30.0, "audio": False, "video": False,
            "channels": 0, "rate": 0}
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info["video"] and st.get("codec_name") not in ("mjpeg", "png"):
            info["video"] = True
            info["w"], info["h"] = int(st.get("width", 0)), int(st.get("height", 0))
            num, _, den = (st.get("avg_frame_rate") or "30/1").partition("/")
            try:
                fps = float(num) / float(den or 1)
                info["fps"] = fps if 1 <= fps <= 240 else 30.0
            except (ValueError, ZeroDivisionError):
                pass
        elif st.get("codec_type") == "audio" and not info["audio"]:
            info["audio"] = True
            info["channels"] = int(st.get("channels", 2))
            info["rate"] = int(st.get("sample_rate", 44100))
    try:
        info["duration"] = float(data.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        pass
    return info


def waveform(path: Path, buckets: int = 800, stereo: bool = False) -> np.ndarray:
    """Picos de amplitud (0 a 1) por tramo. Forma (canales, buckets)."""
    ch = 2 if stereo else 1
    proc = run([need(FFMPEG, "ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", str(ch),
                "-ar", "8000", "-f", "s16le", "-"], 600)
    data = np.frombuffer(proc.stdout, dtype=np.int16)
    if data.size == 0:
        return np.zeros((ch, buckets), dtype=np.float32)
    data = data[: data.size - data.size % ch].reshape(-1, ch).T.astype(np.float32) / 32768
    n = data.shape[1]
    edges = np.linspace(0, n, buckets + 1).astype(int)
    out = np.zeros((ch, buckets), dtype=np.float32)
    for i in range(buckets):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        out[:, i] = np.abs(data[:, a:b]).max(axis=1) if a < n else 0
    return out


def thumb_strip(path: Path, duration: float, count: int = 12, height: int = 56) -> bytes:
    """Tira de miniaturas del video en PNG (una sola imagen horizontal)."""
    fps = max(count / max(duration, 0.1), 0.01)
    proc = run([need(FFMPEG, "ffmpeg"), "-v", "error", "-i", path, "-vf",
                f"fps={fps:.5f},scale=-2:{height},tile={count}x1", "-frames:v", "1",
                "-f", "image2pipe", "-vcodec", "png", "-"], 300)
    return proc.stdout


def frame_png(path: Path, t: float, max_side: int = 0) -> bytes:
    vf = ["-vf", f"scale='min({max_side},iw)':-2"] if max_side else []
    proc = run([need(FFMPEG, "ffmpeg"), "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", path,
                "-frames:v", "1", *vf, "-f", "image2pipe", "-vcodec", "png", "-"], 120)
    return proc.stdout


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _out_video_ext(src: Path) -> str:
    ext = norm_ext(src)
    return ext if ext in ("mp4", "mov", "mkv") else "mp4"


_X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]


# ================================================================= imagenes

def to_array(im) -> tuple[np.ndarray, np.ndarray | None]:
    """PIL -> (rgb float32 0..1, alfa o None)."""
    alpha = None
    if _has_alpha(im):
        rgba = im.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"))
        rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32) / 255
    else:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255
    return rgb, alpha


def from_array(rgb: np.ndarray, alpha: np.ndarray | None):
    from PIL import Image
    im = Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    if alpha is not None:
        im.putalpha(Image.fromarray(alpha))
    return im


def _lum(x: np.ndarray) -> np.ndarray:
    return x[..., 0] * 0.2126 + x[..., 1] * 0.7152 + x[..., 2] * 0.0722


def _smooth(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def _blur(x: np.ndarray, sigma: float) -> np.ndarray:
    import cv2
    return cv2.GaussianBlur(x, (0, 0), max(sigma, 0.3))


EDIT_DEFAULTS = {
    "exposure": 0, "brightness": 0, "contrast": 0, "highlights": 0, "shadows": 0, "whites": 0,
    "blacks": 0, "gamma": 0, "temperature": 0, "tint": 0, "saturation": 0, "vibrance": 0,
    "hue": 0, "clarity": 0, "dehaze": 0, "sharpness": 0, "noise": 0, "grain": 0, "bw": 0,
}


def apply_edits(rgb: np.ndarray, p: dict) -> np.ndarray:
    """Ajustes tipo revelado. Valores de -100 a 100 (exposicion en centesimas de EV, tono en grados)."""
    x = rgb.copy()
    scale = min(x.shape[:2]) / 1000  # los radios crecen con la resolucion
    v = lambda k: p.get(k, 0) / 100  # noqa: E731
    if p.get("exposure"):
        x *= 2 ** (p["exposure"] / 100)
    if v("brightness"):
        x += v("brightness") * 0.25
    if v("contrast"):
        x = (x - 0.5) * (1 + v("contrast")) + 0.5
    if v("highlights") or v("shadows"):
        L = np.clip(_lum(x), 0, 1)
        delta = v("highlights") * 0.35 * _smooth(0.5, 1.0, L) + v("shadows") * 0.35 * (1 - _smooth(0.0, 0.5, L))
        x += delta[..., None]
    if v("whites") or v("blacks"):
        black, white = -v("blacks") * 0.15, 1 - v("whites") * 0.15
        x = (x - black) / max(white - black, 0.05)
    if v("gamma"):
        x = np.clip(x, 0, None) ** (2 ** -v("gamma"))
    if v("temperature") or v("tint"):
        t, ti = v("temperature"), v("tint")
        x *= np.array([1 + 0.2 * t + 0.08 * ti, 1 - 0.2 * ti, 1 - 0.2 * t + 0.08 * ti], dtype=np.float32)
    if v("saturation") or v("vibrance") or p.get("bw"):
        L = _lum(x)[..., None]
        factor = 1 + v("saturation")
        if v("vibrance"):
            sat_now = (x.max(axis=-1) - x.min(axis=-1))[..., None]
            factor = factor * (1 + v("vibrance") * (1 - np.clip(sat_now, 0, 1)))
        if p.get("bw"):
            factor = 0
        x = L + (x - L) * factor
    if p.get("hue"):
        a = math.radians(p["hue"])
        to_yiq = np.array([[0.299, 0.587, 0.114], [0.596, -0.274, -0.322], [0.211, -0.523, 0.312]], np.float32)
        rot = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]], np.float32)
        m = np.linalg.inv(to_yiq) @ rot @ to_yiq
        x = x @ m.T.astype(np.float32)
    if v("dehaze"):
        d = v("dehaze")
        if d > 0:
            haze = _blur(x.min(axis=-1), 15 * scale)[..., None] * d * 0.6
            x = (x - haze) / np.maximum(1 - haze, 0.2)
        else:
            x = x * (1 + d * 0.5) - d * 0.5 * 0.85
    if v("clarity"):
        L = _lum(x)
        x += (v("clarity") * 0.7 * (L - _blur(L, 25 * scale)))[..., None]
    if v("noise") > 0:
        import cv2
        n = v("noise")
        smooth = cv2.bilateralFilter(np.clip(x, 0, 1).astype(np.float32), 0, 0.05 + 0.15 * n, 2 + 5 * n * max(scale, 0.3))
        x = x * (1 - n) + smooth * n
    if v("sharpness"):
        x += v("sharpness") * 1.2 * (x - _blur(x, 1.4 * max(scale, 0.5)))
    if v("grain") > 0:
        rng = np.random.default_rng(7)
        g = rng.normal(0, 0.07 * v("grain"), x.shape[:2]).astype(np.float32)
        x += _blur(g, 0.6 * max(scale, 0.5))[..., None]
    return np.clip(x, 0, 1)


def edit_photo(src: Path, params: dict) -> list[Path]:
    im = open_image(src)
    rgb, alpha = to_array(im)
    out_im = from_array(apply_edits(rgb, params), alpha)
    out_im.info = dict(im.info)
    fmt = _same_fmt(src)
    out = unique_path(src.parent, f"{src.stem}-editada", fmt)
    save_image(out_im, out, fmt, quality=94)
    return [out]


# ---------------------------------------------------------------- recortar

def _transform(im, quarter_turns: int, flip: bool):
    from PIL import Image
    if quarter_turns % 4:
        im = im.rotate(-90 * (quarter_turns % 4), expand=True)
    if flip:
        im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return im


def crop_image(paths: list[Path], rect: tuple, size: tuple | None, turns: int = 0,
               flip: bool = False) -> list[Path]:
    from PIL import Image
    outs = []
    for src in paths:
        im = _transform(open_image(src), turns, flip)
        x, y, w, h = rect
        box = (round(x * im.width), round(y * im.height),
               round((x + w) * im.width), round((y + h) * im.height))
        part = im.crop(box)
        if size and all(size):
            part = part.resize((int(size[0]), int(size[1])), Image.Resampling.LANCZOS)
        part.info = dict(im.info)
        fmt = _same_fmt(src)
        out = unique_path(src.parent, f"{src.stem}-recorte", fmt)
        save_image(part, out, fmt, quality=94)
        outs.append(out)
    return outs


# ---------------------------------------------------------------- ocultar

def detect_faces(im) -> list[tuple]:
    """Caras con YuNet (modelo local de OpenCV). Devuelve cajas normalizadas."""
    import cv2
    model = MODELS / "face_detection_yunet_2023mar.onnx"
    if not model.exists():
        raise GajoError("Falta el modelo de caras (carpeta modelos)")
    rgb = np.asarray(_flatten(im).convert("RGB"))
    h, w = rgb.shape[:2]
    k = min(1.0, 1280 / max(w, h))
    small = cv2.resize(rgb, (max(1, int(w * k)), max(1, int(h * k)))) if k < 1 else rgb
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    det = cv2.FaceDetectorYN.create(str(model), "", (bgr.shape[1], bgr.shape[0]), 0.7, 0.3, 5000)
    _, faces = det.detect(bgr)
    boxes = []
    for f in faces if faces is not None else []:
        x, y, fw, fh = (float(v) for v in f[:4])
        pad = 0.15
        x, y, fw, fh = x - fw * pad, y - fh * pad, fw * (1 + 2 * pad), fh * (1 + 2 * pad)
        sw, sh = bgr.shape[1], bgr.shape[0]
        boxes.append((max(0, x / sw), max(0, y / sh), min(fw / sw, 1), min(fh / sh, 1)))
    return boxes


def detect_text(im) -> list[tuple]:
    """Zonas con texto por morfologia (bordes densos y alargados)."""
    import cv2
    gray = np.asarray(_flatten(im).convert("L"))
    h, w = gray.shape
    k = min(1.0, 1600 / max(w, h))
    if k < 1:
        gray = cv2.resize(gray, (int(w * k), int(h * k)))
    sh, sw = gray.shape
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    kw = max(9, sw // 90)
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 3)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if ch < 8 or cw < 2.0 * ch or ch > sh * 0.2 or cw * ch < sw * sh * 0.0004:
            continue
        fill = cv2.countNonZero(bw[y:y + ch, x:x + cw]) / float(cw * ch)
        if fill < 0.2:
            continue
        pad = ch * 0.25
        boxes.append((max(0, (x - pad) / sw), max(0, (y - pad) / sh),
                      min((cw + 2 * pad) / sw, 1), min((ch + 2 * pad) / sh, 1)))
    return boxes


def _obscure(im, box: tuple, style: str, color: str):
    from PIL import Image, ImageDraw, ImageFilter
    x, y, w, h = box
    b = (int(x * im.width), int(y * im.height), int((x + w) * im.width), int((y + h) * im.height))
    if b[2] - b[0] < 2 or b[3] - b[1] < 2:
        return im
    if style == "blur":  # pixelado + desenfoque: no se puede revertir
        region = im.crop(b)
        cell = max(2, min(region.size) // 10)
        tiny = region.resize((max(1, region.width // cell), max(1, region.height // cell)), Image.Resampling.BOX)
        region = tiny.resize(region.size, Image.Resampling.NEAREST).filter(ImageFilter.GaussianBlur(cell * 0.6))
        im.paste(region, b[:2])
    else:
        ImageDraw.Draw(im).rectangle(b, fill=color)
    return im


def redact_image(src: Path, boxes: list[tuple], style: str, color: str) -> list[Path]:
    im = open_image(src)
    info = dict(im.info)
    im = im.convert("RGBA" if _has_alpha(im) else "RGB")
    for box in boxes:
        im = _obscure(im, box, style, color)
    im.info = {k: v for k, v in info.items() if k == "icc_profile"}  # sin metadatos
    fmt = _same_fmt(src)
    out = unique_path(src.parent, f"{src.stem}-oculto", fmt)
    save_image(im, out, fmt, quality=94, keep_meta=False)
    return [out]


# ---------------------------------------------------------------- fondo

GRADIENTS = [
    ("Atardecer", ["#F6705B", "#F9A86B", "#F7D774"]),
    ("Brisa", ["#7AD7F0", "#C7B8F5"]),
    ("Arcoiris", ["#FF9A8B", "#FFD36E", "#8EE3A1", "#78C1F5"]),
    ("Niebla", ["#F4E9F6", "#E3E8F7"]),
    ("Lavanda", ["#E3A3E8", "#9E8CF0"]),
    ("Bosque", ["#2E6B4F", "#A8D58B"]),
    ("Cielo", ["#4F8DF5", "#8FD3FE"]),
    ("Turquesa", ["#3CB5B9", "#B6F0E2"]),
    ("Durazno", ["#FBD3C0", "#F6A6B2"]),
]
SOLIDS = ["#FFFFFF", "#F2C94C", "#A6CE5C", "#4FA372", "#3E9C94", "#6CC5DE", "#4467D6", "#22324A", "#7A4FD0"]


def _gradient(size: tuple, colors: list[str], angle: float = 135):
    from PIL import Image, ImageColor
    w, h = size
    a = math.radians(angle)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (xx * math.cos(a) + yy * math.sin(a))
    t = (t - t.min()) / max(t.max() - t.min(), 1)
    cols = np.array([ImageColor.getrgb(c) for c in colors], np.float32)
    stops = np.linspace(0, 1, len(cols))
    out = np.stack([np.interp(t, stops, cols[:, k]) for k in range(3)], axis=-1)
    return Image.fromarray(out.astype(np.uint8), "RGB")


def _cover(im, size: tuple):
    from PIL import Image
    k = max(size[0] / im.width, size[1] / im.height)
    im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.Resampling.LANCZOS)
    left, top = (im.width - size[0]) // 2, (im.height - size[1]) // 2
    return im.crop((left, top, left + size[0], top + size[1]))


RATIOS = {"auto": None, "1:1": 1.0, "16:9": 16 / 9, "4:5": 4 / 5, "9:16": 9 / 16}


def compose_background(im, bg: dict, padding: int, corners: int, shadow: int, ratio: str):
    """Pone la imagen sobre un lienzo nuevo (degradado, color o imagen)."""
    from PIL import Image, ImageDraw, ImageFilter
    im = im.convert("RGBA")
    w, h = im.size
    cw, ch = w + 2 * padding, h + 2 * padding
    r = RATIOS.get(ratio)
    if r:
        if cw / ch < r:
            cw = round(ch * r)
        else:
            ch = round(cw / r)
    if bg["type"] == "gradient":
        canvas = _gradient((cw, ch), bg["colors"])
    elif bg["type"] == "image":
        canvas = _cover(_flatten(open_image(Path(bg["path"]))), (cw, ch)).filter(
            ImageFilter.GaussianBlur(max(8, min(cw, ch) // 40)))
    else:
        canvas = Image.new("RGB", (cw, ch), bg["color"])
    canvas = canvas.convert("RGBA")
    x, y = (cw - w) // 2, (ch - h) // 2
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=corners, fill=255)
    if im.getchannel("A").getextrema()[0] < 255:  # respeta la transparencia propia
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, im.getchannel("A"))
    if shadow > 0:
        sh = Image.new("L", (cw, ch), 0)
        sh.paste(mask, (x, y + shadow // 3))
        sh = sh.filter(ImageFilter.GaussianBlur(shadow))
        dark = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        dark.putalpha(sh.point(lambda v: int(v * 0.38)))
        canvas = Image.alpha_composite(canvas, dark)
    canvas.paste(im, (x, y), mask)
    return canvas


def add_background(paths: list[Path], bg: dict, padding_k: float, corners_k: float,
                   shadow_k: float, ratio: str) -> list[Path]:
    """Los valores *_k son fracciones del lado mayor, asi sirven para cualquier tamano."""
    outs = []
    for src in paths:
        im = open_image(src)
        side = max(im.size)
        res = compose_background(im, bg, round(padding_k * side), round(corners_k * side),
                                 round(shadow_k * side), ratio)
        out = unique_path(src.parent, f"{src.stem}-fondo", "png")
        res.save(out, "PNG", optimize=True)
        outs.append(out)
    return outs


# ---------------------------------------------------------------- collage

def render_collage(images: list, style: str, cols: int, ratio: str, width: int, fit: str,
                   spacing: int, corners: int, bg: str):
    from PIL import Image, ImageDraw
    n = len(images)
    if style == "fila":
        cols, rows = n, 1
    elif style == "columna":
        cols, rows = 1, n
    elif style == "destacada":
        cols, rows = max(1, n - 1), 2
    else:
        cols = max(1, min(cols, n))
        rows = math.ceil(n / cols)
    r = RATIOS.get(ratio)
    inner_w = width - spacing * (cols + 1)
    cell_w = inner_w / cols
    if r:
        height = round(width / r)
    elif style == "destacada":
        height = round(cell_w * 1 + width * 0.6 + spacing * 3)
    else:
        height = round(cell_w * rows + spacing * (rows + 1))
    sheet = Image.new("RGB", (width, height), bg)
    slots = []
    if style == "destacada" and n > 1:
        top_h = (height - spacing * 3) * 0.62
        slots.append((spacing, spacing, width - 2 * spacing, top_h))
        bw = (width - spacing * (n)) / (n - 1)
        for i in range(n - 1):
            slots.append((spacing + i * (bw + spacing), spacing * 2 + top_h, bw, height - top_h - spacing * 3))
    else:
        ch = (height - spacing * (rows + 1)) / rows
        for i in range(n):
            rr, cc = divmod(i, cols)
            slots.append((spacing + cc * (cell_w + spacing), spacing + rr * (ch + spacing), cell_w, ch))
    for im, (sx, sy, sw, sh) in zip(images, slots):
        sw, sh = max(1, round(sw)), max(1, round(sh))
        src = _flatten(im)
        if fit == "llenar":
            tile = _cover(src, (sw, sh))
            ox, oy = 0, 0
        else:
            tile = src.copy()
            tile.thumbnail((sw, sh), Image.Resampling.LANCZOS)
            ox, oy = (sw - tile.width) // 2, (sh - tile.height) // 2
        mask = Image.new("L", tile.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile.width - 1, tile.height - 1), radius=corners, fill=255)
        sheet.paste(tile, (round(sx) + ox, round(sy) + oy), mask)
    return sheet


def collage(paths: list[Path], **opts) -> list[Path]:
    images = [open_image(p) for p in paths]
    sheet = render_collage(images, **opts)
    out = unique_path(paths[0].parent, "Collage", "jpg")
    sheet.save(out, "JPEG", quality=92, optimize=True)
    return [out]


# ---------------------------------------------------------------- comprimir con objetivo

def _encode(im, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "png":
        from PIL import Image
        colors = max(16, min(256, quality * 3))
        im.convert("RGBA").quantize(colors=colors, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
    else:
        save_image(im, buf, fmt, quality=quality)
    return buf.getvalue()


def compress_to(im, fmt: str, mode: str, value: float, original: int) -> tuple[bytes, int]:
    """mode: 'calidad' (value=1..100), 'peso' (value=bytes), 'porcentaje' (value=0..95)."""
    if mode == "calidad":
        return _encode(im, fmt, int(value)), int(value)
    target = value if mode == "peso" else original * (1 - value / 100)
    lo, hi, best = 5, 95, None
    while lo <= hi:  # busqueda binaria de la calidad mas alta que cabe en el objetivo
        q = (lo + hi) // 2
        data = _encode(im, fmt, q)
        if len(data) <= target:
            best, lo = (data, q), q + 1
        else:
            hi = q - 1
    return best or (_encode(im, fmt, 5), 5)


def compress_images(paths: list[Path], mode: str, value: float, max_side: int) -> list[Path]:
    from PIL import Image
    outs = []
    for src in paths:
        im = open_image(src)
        if max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        ext = norm_ext(src)
        fmt = ext if ext in ("jpg", "webp", "heic", "avif", "png") else ("png" if _has_alpha(im) else "jpg")
        data, _ = compress_to(im, fmt, mode, value, src.stat().st_size)
        out = unique_path(src.parent, f"{src.stem}-comprimido", fmt)
        out.write_bytes(data)
        outs.append(out)
    return outs


# ---------------------------------------------------------------- metadatos

def _to_rational(v: float) -> tuple:
    d = int(v)
    m = int((v - d) * 60)
    s = round(((v - d) * 60 - m) * 60 * 100)
    return ((d, 1), (m, 1), (s, 100))


def _from_rational(t) -> float:
    try:
        d, m, s = (a / b for a, b in t)
        return d + m / 60 + s / 3600
    except (TypeError, ZeroDivisionError, ValueError):
        return 0.0


def read_metadata(src: Path) -> dict:
    import piexif
    im = open_image(src)
    raw = im.info.get("exif")
    out = {"fecha": "", "marca": "", "modelo": "", "autor": "", "descripcion": "", "lat": "", "lon": ""}
    if not raw:
        return out
    try:
        ex = piexif.load(raw)
    except Exception:  # noqa: BLE001 - exif danado: se muestra vacio
        return out
    dec = lambda b: b.decode("utf-8", "replace").strip("\x00 ") if isinstance(b, bytes) else str(b or "")  # noqa: E731
    out["fecha"] = dec(ex["Exif"].get(piexif.ExifIFD.DateTimeOriginal) or ex["0th"].get(piexif.ImageIFD.DateTime, b""))
    out["marca"] = dec(ex["0th"].get(piexif.ImageIFD.Make, b""))
    out["modelo"] = dec(ex["0th"].get(piexif.ImageIFD.Model, b""))
    out["autor"] = dec(ex["0th"].get(piexif.ImageIFD.Artist, b""))
    out["descripcion"] = dec(ex["0th"].get(piexif.ImageIFD.ImageDescription, b""))
    gps = ex.get("GPS", {})
    if piexif.GPSIFD.GPSLatitude in gps:
        lat = _from_rational(gps[piexif.GPSIFD.GPSLatitude])
        lon = _from_rational(gps.get(piexif.GPSIFD.GPSLongitude))
        if gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N") in (b"S", "S"):
            lat = -lat
        if gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E") in (b"W", "W"):
            lon = -lon
        out["lat"], out["lon"] = f"{lat:.6f}", f"{lon:.6f}"
    return out


def write_metadata(paths: list[Path], fields: dict, strip_all: bool = False) -> list[Path]:
    """Escribe solo los campos con valor. Si strip_all, deja la foto sin metadatos."""
    import piexif
    from .engines import img_strip
    outs = []
    for src in paths:
        if strip_all:
            outs += img_strip(src)
            continue
        im = open_image(src)
        try:
            ex = piexif.load(im.info["exif"]) if im.info.get("exif") else {}
        except Exception:  # noqa: BLE001
            ex = {}
        ex = {k: ex.get(k, {}) for k in ("0th", "Exif", "GPS", "1st")} | {"thumbnail": None}
        ex["0th"].pop(piexif.ImageIFD.Orientation, None)  # la imagen ya viene enderezada
        enc = lambda s: s.encode("utf-8")  # noqa: E731
        if fields.get("fecha"):
            ex["Exif"][piexif.ExifIFD.DateTimeOriginal] = enc(fields["fecha"])
            ex["0th"][piexif.ImageIFD.DateTime] = enc(fields["fecha"])
        for key, tag in (("marca", piexif.ImageIFD.Make), ("modelo", piexif.ImageIFD.Model),
                         ("autor", piexif.ImageIFD.Artist), ("descripcion", piexif.ImageIFD.ImageDescription)):
            if fields.get(key):
                ex["0th"][tag] = enc(fields[key])
        if fields.get("quitar_ubicacion"):
            ex["GPS"] = {}
        elif fields.get("lat") and fields.get("lon"):
            lat, lon = float(str(fields["lat"]).replace(",", ".")), float(str(fields["lon"]).replace(",", "."))
            ex["GPS"] = {
                piexif.GPSIFD.GPSLatitudeRef: b"S" if lat < 0 else b"N",
                piexif.GPSIFD.GPSLatitude: _to_rational(abs(lat)),
                piexif.GPSIFD.GPSLongitudeRef: b"W" if lon < 0 else b"E",
                piexif.GPSIFD.GPSLongitude: _to_rational(abs(lon)),
            }
        raw = piexif.dump(ex)
        ext = norm_ext(src)
        if ext == "jpg":  # sin recomprimir: se copia el archivo y se cambia solo el bloque EXIF
            out = unique_path(src.parent, f"{src.stem}-metadatos", "jpg")
            shutil.copyfile(src, out)
            piexif.insert(raw, str(out))
            if open_image(src).size != open_image(out).size:  # tenia orientacion: guardar enderezada
                save_image(im, out, "jpg", quality=95)
                piexif.insert(raw, str(out))
        else:
            fmt = _same_fmt(src)
            out = unique_path(src.parent, f"{src.stem}-metadatos", fmt)
            im.info["exif"] = raw
            save_image(im, out, fmt, quality=95)
        outs.append(out)
    return outs


# ---------------------------------------------------------------- anotaciones

def render_annotations(qimg, anns: list[dict]):
    """Dibuja las anotaciones sobre una copia del QImage (coordenadas normalizadas)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen
    img = qimg.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    base = img.copy()
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = img.width(), img.height()
    diag = math.hypot(w, h)
    for a in anns:
        draw_annotation(p, a, w, h, diag, base)
    p.end()
    return img


def draw_annotation(p, a: dict, w: float, h: float, diag: float, base=None, ox: float = 0, oy: float = 0) -> None:
    """Dibuja una anotacion en un rectangulo de w x h que parte en (ox, oy)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainterPath, QPen
    color = QColor(a.get("color", "#E53935"))
    pts = [QPointF(ox + x * w, oy + y * h) for x, y in a.get("pts", [])]
    stroke = max(2.0, diag * 0.004)
    kind = a["kind"]
    if kind in ("pen", "highlight") and len(pts) > 1:
        c = QColor(color)
        width = stroke * (6 if kind == "highlight" else 1.2)
        if kind == "highlight":
            c.setAlphaF(0.38)
        pen = QPen(c, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        p.drawPath(path)
    elif kind == "arrow" and len(pts) == 2:
        a0, a1 = pts
        p.setPen(QPen(color, stroke * 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        ang = math.atan2(a1.y() - a0.y(), a1.x() - a0.x())
        head = stroke * 5.5
        tip_base = QPointF(a1.x() - math.cos(ang) * head * 0.8, a1.y() - math.sin(ang) * head * 0.8)
        p.drawLine(a0, tip_base)
        poly = QPainterPath(a1)
        for s in (-1, 1):
            poly.lineTo(QPointF(a1.x() - math.cos(ang + s * 0.45) * head, a1.y() - math.sin(ang + s * 0.45) * head))
        poly.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(poly)
    elif kind in ("rect", "ellipse") and len(pts) == 2:
        r = QRectF(pts[0], pts[1]).normalized()
        p.setPen(QPen(color, stroke * 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        (p.drawRect if kind == "rect" else p.drawEllipse)(r)
    elif kind == "text" and pts:
        font = QFont("Segoe UI", 1, QFont.Weight.Bold)
        font.setPixelSize(max(10, int(h * a.get("size", 0.045))))
        p.setFont(font)
        path = QPainterPath()
        path.addText(pts[0], font, a.get("text", ""))
        outline = QColor("#FFFFFF") if color.lightness() < 140 else QColor("#000000")
        p.setPen(QPen(outline, max(1.5, font.pixelSize() / 9)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)
    elif kind == "number" and pts:
        rad = max(10.0, diag * 0.018)
        p.setPen(QPen(QColor("#FFFFFF"), max(1.5, rad * 0.14)))
        p.setBrush(color)
        p.drawEllipse(pts[0], rad, rad)
        font = QFont("Segoe UI", 1, QFont.Weight.Bold)
        font.setPixelSize(int(rad * 1.15))
        p.setFont(font)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(pts[0].x() - rad, pts[0].y() - rad, rad * 2, rad * 2),
                   Qt.AlignmentFlag.AlignCenter, str(a.get("n", 1)))
    elif kind == "magnifier" and len(pts) == 2 and base is not None:
        c, edge = pts
        rad = max(8.0, math.hypot(edge.x() - c.x(), edge.y() - c.y()))
        zoom = 2.0
        # la zona de la imagen original (sin anotaciones) bajo el centro, ampliada
        sx = (c.x() - ox) / w * base.width()
        sy = (c.y() - oy) / h * base.height()
        sr = rad / zoom / w * base.width()
        src = QRectF(sx - sr, sy - sr, sr * 2, sr * 2)
        clip = QPainterPath()
        clip.addEllipse(c, rad, rad)
        p.save()
        p.setClipPath(clip)
        p.drawImage(QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), base, src)
        p.restore()
        p.setPen(QPen(QColor("#FFFFFF"), stroke * 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, rad, rad)
        p.setPen(QPen(color, stroke * 0.7))
        p.drawEllipse(c, rad + stroke * 0.7, rad + stroke * 0.7)
    elif kind == "image" and len(pts) == 2 and a.get("path"):
        r = QRectF(pts[0], pts[1]).normalized()
        pic = QImage(a["path"])
        if not pic.isNull():
            p.drawImage(r, pic)


def annotate_image(src: Path, anns: list[dict]) -> list[Path]:
    from PySide6.QtGui import QImage
    im = open_image(src)
    flat = im.convert("RGBA")
    data = flat.tobytes("raw", "RGBA")
    qimg = QImage(data, flat.width, flat.height, flat.width * 4, QImage.Format.Format_RGBA8888).copy()
    out_q = render_annotations(qimg, anns).convertToFormat(QImage.Format.Format_RGBA8888)
    from PIL import Image
    buf = bytes(out_q.constBits())[: out_q.width() * out_q.height() * 4]
    res = Image.frombytes("RGBA", (out_q.width(), out_q.height()), buf)
    if not _has_alpha(im):
        res = res.convert("RGB")
    res.info = {k: v for k, v in im.info.items() if k in ("icc_profile", "exif")}
    fmt = _same_fmt(src)
    out = unique_path(src.parent, f"{src.stem}-anotada", fmt)
    save_image(res, out, fmt, quality=94)
    return [out]


# ================================================================= video

def _video_out(src: Path, suffix: str) -> Path:
    return unique_path(src.parent, f"{src.stem}-{suffix}", _out_video_ext(src))


def _run_ff(args: list, out: Path) -> list[Path]:
    try:
        ffmpeg(args)
    except GajoError:
        if out.is_file():
            out.unlink(missing_ok=True)
        raise
    return [out]


def video_trim(src: Path, start: float, end: float) -> list[Path]:
    if end - start < 0.05:
        raise GajoError("La parte elegida es demasiado corta")
    out = _video_out(src, "corte")
    return _run_ff(["-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}", "-map", "0:v:0",
                    "-map", "0:a?", *_X264, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], out)


def _turn_filter(turns: int) -> list[str]:
    turns %= 4
    return {0: [], 1: ["transpose=1"], 2: ["transpose=1", "transpose=1"], 3: ["transpose=2"]}[turns]


def video_crop(src: Path, rect: tuple, size: tuple | None, turns: int = 0) -> list[Path]:
    info = media_info(src)
    w, h = (info["h"], info["w"]) if turns % 2 else (info["w"], info["h"])
    x, y, rw, rh = rect
    cw, ch = _even(rw * w), _even(rh * h)
    cx, cy = min(int(x * w), w - cw), min(int(y * h), h - ch)
    vf = _turn_filter(turns) + [f"crop={cw}:{ch}:{max(cx, 0)}:{max(cy, 0)}"]
    if size and all(size):
        vf.append(f"scale={_even(size[0])}:{_even(size[1])}")
    out = _video_out(src, "encuadre")
    return _run_ff(["-i", src, "-vf", ",".join(vf), "-map", "0:v:0", "-map", "0:a?", *_X264,
                    "-c:a", "copy", "-movflags", "+faststart", out], out)


def _atempo(rate: float) -> str:
    parts = []
    while rate > 2.0:
        parts.append("atempo=2.0")
        rate /= 2
    while rate < 0.5:
        parts.append("atempo=0.5")
        rate /= 0.5
    parts.append(f"atempo={rate:.4f}")
    return ",".join(parts)


def video_speed(src: Path, rate: float, keep_pitch: bool = True) -> list[Path]:
    info = media_info(src)
    label = f"{rate:g}x".replace(".", ",")
    out = _video_out(src, f"velocidad-{label}")
    args = ["-i", src, "-filter:v", f"setpts=PTS/{rate:.4f}"]
    if info["audio"]:
        af = _atempo(rate) if keep_pitch else f"asetrate={info['rate']}*{rate:.4f},aresample={info['rate']}"
        args += ["-filter:a", af, "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-an"]
    return _run_ff([*args, *_X264, "-movflags", "+faststart", out], out)


def video_join(paths: list[Path]) -> list[Path]:
    infos = [media_info(p) for p in paths]
    W, H, F = _even(infos[0]["w"]), _even(infos[0]["h"]), round(infos[0]["fps"], 3)
    args: list = []
    for p in paths:
        args += ["-i", p]
    chains, labels = [], ""
    for i, inf in enumerate(infos):
        chains.append(f"[{i}:v:0]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                      f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={F},format=yuv420p[v{i}]")
        if inf["audio"]:
            chains.append(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            chains.append(f"anullsrc=r=48000:cl=stereo:d={inf['duration']:.3f}[a{i}]")
        labels += f"[v{i}][a{i}]"
    chains.append(f"{labels}concat=n={len(paths)}:v=1:a=1[v][a]")
    out = unique_path(paths[0].parent, f"{paths[0].stem}-unido", "mp4")
    return _run_ff([*args, "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
                    *_X264, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], out)


def video_snapshots(src: Path, times: list[float]) -> list[Path]:
    if not times:
        raise GajoError("No elegiste ningún fotograma")
    if len(times) == 1:
        out = unique_path(src.parent, f"{src.stem}-{_clock(times[0], True)}", "png")
        ffmpeg(["-ss", f"{times[0]:.3f}", "-i", src, "-frames:v", "1", out])
        return [out]
    folder = unique_dir(src.parent, f"{src.stem} - fotogramas")
    for t in sorted(times):
        ffmpeg(["-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", folder / f"{src.stem}-{_clock(t, True)}.png"])
    return [folder]


def _clock(t: float, safe: bool = False) -> str:
    m, s = divmod(max(t, 0), 60)
    text = f"{int(m)}:{s:05.2f}"
    return text.replace(":", "m").replace(".", "s") if safe else text


def video_split(src: Path, points: list[float]) -> list[Path]:
    info = media_info(src)
    cuts = sorted(p for p in set(round(x, 3) for x in points) if 0.05 < p < info["duration"] - 0.05)
    if not cuts:
        raise GajoError("Agrega al menos un punto de corte")
    bounds = [0.0, *cuts, info["duration"]]
    folder = unique_dir(src.parent, f"{src.stem} - partes")
    ext = _out_video_ext(src)
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        ffmpeg(["-ss", f"{a:.3f}", "-i", src, "-t", f"{b - a:.3f}", "-map", "0:v:0", "-map", "0:a?",
                *_X264, "-c:a", "aac", "-b:a", "192k", folder / f"{src.stem}-parte{i + 1:02d}.{ext}"])
    return [folder]


def video_redact(src: Path, boxes: list[dict], style: str, color: str) -> list[Path]:
    """boxes: [{'rect': (x,y,w,h), 'start': s, 'end': e}] en coordenadas normalizadas."""
    if not boxes:
        raise GajoError("Agrega al menos una zona para ocultar")
    info = media_info(src)
    W, H = info["w"], info["h"]
    chains, cur = [], "[0:v]"
    for i, b in enumerate(boxes):
        x, y, w, h = b["rect"]
        px, py, pw, ph = int(x * W), int(y * H), max(2, _even(w * W)), max(2, _even(h * H))
        px, py = min(px, W - pw), min(py, H - ph)
        when = f"between(t\\,{b['start']:.3f}\\,{b['end']:.3f})"
        if style == "blur":
            cell = max(2, min(pw, ph) // 10)
            chains.append(f"{cur}split=2[base{i}][cut{i}]")
            chains.append(f"[cut{i}]crop={pw}:{ph}:{px}:{py},scale={max(1, pw // cell)}:{max(1, ph // cell)},"
                          f"scale={pw}:{ph}:flags=neighbor,boxblur={max(1, cell // 2)}[blk{i}]")
            chains.append(f"[base{i}][blk{i}]overlay={px}:{py}:enable='{when}'[s{i}]")
        else:
            hexcol = color.lstrip("#")
            chains.append(f"{cur}drawbox=x={px}:y={py}:w={pw}:h={ph}:color=0x{hexcol}@1:t=fill:enable='{when}'[s{i}]")
        cur = f"[s{i}]"
    out = _video_out(src, "oculto")
    return _run_ff(["-i", src, "-filter_complex", ";".join(chains), "-map", cur, "-map", "0:a?", *_X264,
                    "-c:a", "copy", "-movflags", "+faststart", out], out)


# ================================================================= audio

def _audio_ext(src: Path) -> str:
    ext = norm_ext(src)
    return ext if ext in AUDIO_ARGS else "mp3"


def loudness(src: Path) -> dict:
    """Mide el volumen percibido (LUFS) con el filtro loudnorm de ffmpeg."""
    proc = run([need(FFMPEG, "ffmpeg"), "-hide_banner", "-nostats", "-i", src, "-vn", "-af",
                "loudnorm=print_format=json", "-f", "null", "-"], 1200)
    text = proc.stderr.decode("utf-8", "replace")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not match:
        raise GajoError("No pude medir el volumen")
    return json.loads(match.group(0))


def audio_normalize(src: Path, target: float, lra: float, peak: float) -> list[Path]:
    m = loudness(src)
    af = (f"loudnorm=I={target}:LRA={lra}:TP={peak}:measured_I={m['input_i']}:"
          f"measured_LRA={m['input_lra']}:measured_TP={m['input_tp']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true")
    ext = _audio_ext(src)
    out = unique_path(src.parent, f"{src.stem}-normalizado", ext)
    rate = media_info(src)["rate"] or 48000
    return _run_ff(["-i", src, "-vn", "-af", af, "-ar", str(rate), *AUDIO_ARGS[ext], out], out)


SHAPES = {"horizontal": (1920, 1080), "vertical": (1080, 1920), "cuadrado": (1080, 1080)}


def audio_visualizer(src: Path, style: str, shape: str, color: str, image: str | None = None) -> list[Path]:
    W, H = SHAPES[shape]
    out = unique_path(src.parent, f"{src.stem}-visual", "mp4")
    if style == "imagen" and image:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
              "format=yuv420p")
        return _run_ff(["-loop", "1", "-framerate", "2", "-i", image, "-i", src, "-vf", vf, "-map", "0:v",
                        "-map", "1:a", "-c:v", "libx264", "-tune", "stillimage", "-preset", "medium", "-crf", "20",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], out)
    wave_h = int(H * 0.42)
    fc = (f"[0:a]showwaves=s={W}x{wave_h}:mode=cline:colors={color}:rate=30:scale=sqrt,format=rgba[w];"
          f"color=c=black:s={W}x{H}:r=30[bg];[bg][w]overlay=0:(H-h)/2:shortest=1,format=yuv420p[v]")
    return _run_ff(["-i", src, "-filter_complex", fc, "-map", "[v]", "-map", "0:a", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", out], out)


def audio_trim(src: Path, start: float, end: float) -> list[Path]:
    if end - start < 0.05:
        raise GajoError("La parte elegida es demasiado corta")
    ext = _audio_ext(src)
    out = unique_path(src.parent, f"{src.stem}-corte", ext)
    return _run_ff(["-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}", "-vn",
                    "-map_metadata", "0", *AUDIO_ARGS[ext], out], out)


def silent_bounds(peaks: np.ndarray, duration: float, threshold_db: float = -45) -> tuple[float, float]:
    """Inicio y fin del sonido segun la forma de onda (para cortar silencios de los extremos)."""
    level = peaks.max(axis=0) if peaks.ndim == 2 else peaks
    thr = 10 ** (threshold_db / 20)
    loud = np.nonzero(level > thr)[0]
    if loud.size == 0:
        return 0.0, duration
    n = level.size
    return loud[0] / n * duration, min(duration, (loud[-1] + 1) / n * duration)


def channels_filter(mode: str, left: float, right: float) -> str:
    if mode == "mono":
        return f"pan=mono|c0={0.5 * left:.3f}*c0+{0.5 * right:.3f}*c1"
    return f"pan=stereo|c0={left:.3f}*c0|c1={right:.3f}*c1"


def audio_channels(src: Path, mode: str, left: float, right: float) -> list[Path]:
    info = media_info(src)
    pre = "aformat=channel_layouts=stereo," if info["channels"] == 1 else ""
    ext = _audio_ext(src)
    out = unique_path(src.parent, f"{src.stem}-{mode}", ext)
    return _run_ff(["-i", src, "-vn", "-af", pre + channels_filter(mode, left, right),
                    *AUDIO_ARGS[ext], out], out)


def bleep_filter(ranges: list[tuple], rate: int) -> str:
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)
    return (f"[0:a]volume=enable='{cond}':volume=0[m];"
            f"sine=f=1000:sample_rate={rate},volume=0.35,volume=enable='not({cond})':volume=0[s];"
            f"[m][s]amix=inputs=2:duration=first:normalize=0[a]")


def audio_bleep(src: Path, ranges: list[tuple]) -> list[Path]:
    if not ranges:
        raise GajoError("Agrega al menos un pitido")
    rate = media_info(src)["rate"] or 44100
    ext = _audio_ext(src)
    out = unique_path(src.parent, f"{src.stem}-pitido", ext)
    return _run_ff(["-i", src, "-filter_complex", bleep_filter(ranges, rate), "-map", "[a]",
                    "-ar", str(rate), *AUDIO_ARGS[ext], out], out)


def render_preview(src: Path, af: str | None = None, fc: str | None = None, seconds: float = 60) -> Path:
    """Wav temporal para escuchar el resultado antes de guardar."""
    out = work_dir() / "vista.wav"
    args = ["-t", f"{seconds:.1f}", "-i", src, "-vn"]
    if fc:
        args += ["-filter_complex", fc, "-map", "[a]"]
    elif af:
        args += ["-af", af]
    ffmpeg([*args, "-c:a", "pcm_s16le", out])
    return out


# ================================================================= PDF

def pdf_merge_ordered(paths: list[Path]) -> list[Path]:
    from .engines import pdf_merge
    return pdf_merge(paths)


def pdf_organize(src: Path, pages: list[tuple[int, int]], same_width: bool) -> list[Path]:
    """pages: [(indice_original, giro_extra_en_grados)] en el orden final."""
    import fitz
    if not pages:
        raise GajoError("El PDF quedaría sin páginas")
    out = unique_path(src.parent, f"{src.stem}-organizado", "pdf")
    with fitz.open(src) as doc, fitz.open() as new:
        if same_width:
            widths = []
            for idx, extra in pages:
                r = doc[idx].rect
                rot = (doc[idx].rotation + extra) % 180
                widths.append(r.height if rot == 90 else r.width)
            target = min(widths)
            for idx, extra in pages:
                pg = doc[idx]
                r = pg.rect
                rot = (pg.rotation + extra) % 360
                pw, ph = (r.height, r.width) if rot % 180 == 90 else (r.width, r.height)
                k = target / pw
                page = new.new_page(width=target, height=ph * k)
                page.show_pdf_page(page.rect, doc, idx, rotate=extra)
        else:
            for idx, extra in pages:
                new.insert_pdf(doc, from_page=idx, to_page=idx)
                page = new[-1]
                page.set_rotation((page.rotation + extra) % 360)
        new.save(out, garbage=3, deflate=True)
    return [out]
