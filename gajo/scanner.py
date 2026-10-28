"""Escaner de documentos desde fotos: detecta la hoja, la endereza, quita sombras y arma un PDF.

Pensado para las fotos de cedulas, escrituras y certificados que llegan por WhatsApp:
chuecas, con sombra y con la mesa de fondo.
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np

from .engines import _flatten, open_image
from .tools import GajoError, unique_dir, unique_path

PT_PER_MM = 72 / 25.4
# Papeles conocidos: (nombre, ancho mm, alto mm) en vertical.
PAPERS = {
    "carta": (215.9, 279.4),
    "oficio": (216.0, 330.0),   # oficio chileno
    "a4": (210.0, 297.0),
    "cedula": (85.6, 54.0),     # tarjeta ID-1 (cedula, licencia), horizontal
}


# ---------------------------------------------------------------- deteccion de la hoja

def _order(pts: np.ndarray) -> np.ndarray:
    """Esquinas en orden: arriba-izq, arriba-der, abajo-der, abajo-izq."""
    s = pts.sum(axis=1)
    d = pts[:, 1] - pts[:, 0]
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _quad_from_contours(contours, area_min: float):
    import cv2
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        if cv2.contourArea(c) < area_min:
            break
        peri = cv2.arcLength(c, True)
        for eps in (0.015, 0.02, 0.03, 0.045, 0.06):
            approx = cv2.approxPolyDP(c, eps * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx) and cv2.contourArea(approx) >= area_min:
                return approx.reshape(4, 2).astype(np.float32)
    return None


def detect_quad(im) -> list[tuple[float, float]] | None:
    """Busca las 4 esquinas de la hoja. Devuelve coordenadas normalizadas o None."""
    import cv2
    rgb = np.asarray(_flatten(im).convert("RGB"))
    h, w = rgb.shape[:2]
    k = min(1.0, 1000 / max(h, w))
    small = cv2.resize(rgb, (max(1, int(w * k)), max(1, int(h * k)))) if k < 1 else rgb
    sh, sw = small.shape[:2]
    area_min = sh * sw * 0.15
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    # cerrar borra el texto: la hoja queda como una mancha clara de bordes netos
    blob = cv2.morphologyEx(cv2.GaussianBlur(gray, (5, 5), 0), cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)), iterations=3)
    quad = None
    for lo, hi in ((30, 110), (15, 60), (50, 160)):
        edges = cv2.dilate(cv2.Canny(blob, lo, hi), None, iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        quad = _quad_from_contours(contours, area_min)
        if quad is not None:
            break
    if quad is None:  # plan B: la hoja es lo mas claro de la foto
        _, mask = cv2.threshold(blob, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        quad = _quad_from_contours(contours, area_min)
        if quad is None and contours:
            big = max(contours, key=cv2.contourArea)
            if cv2.contourArea(big) >= area_min:
                quad = cv2.boxPoints(cv2.minAreaRect(big)).astype(np.float32)
    if quad is None:
        return None
    quad = _order(quad)
    return [(float(min(max(x / sw, 0), 1)), float(min(max(y / sh, 0), 1))) for x, y in quad]


FULL = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


# ---------------------------------------------------------------- enderezar

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def true_ratio(pts: np.ndarray, w: int, h: int) -> float:
    """Proporcion real (alto/ancho) de la hoja, corrigiendo la perspectiva con el modelo de
    camara de Zhang y He (2007), con el centro optico al medio de la foto."""
    tl, tr, br, bl = pts
    side_w = (_dist(tl, tr) + _dist(bl, br)) / 2
    side_h = (_dist(tl, bl) + _dist(tr, br)) / 2
    simple = side_h / max(side_w, 1)
    u0, v0 = w / 2, h / 2
    m1, m2, m3, m4 = (np.array([p[0], p[1], 1.0]) for p in (tl, tr, bl, br))
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
        n2, n3 = k2 * m2 - m1, k3 * m3 - m1
        n21, n22, n23 = n2
        n31, n32, n33 = n3
        if abs(n23 * n33) < 1e-12:  # foto tomada de frente: no hay perspectiva que corregir
            return simple
        f2 = -((n21 * n31 - (n21 * n33 + n23 * n31) * u0 + n23 * n33 * u0 * u0)
               + (n22 * n32 - (n22 * n33 + n23 * n32) * v0 + n23 * n33 * v0 * v0)) / (n23 * n33)
        if not 0 < f2 < (20 * max(w, h)) ** 2 or f2 < (0.3 * max(w, h)) ** 2:
            return simple
        f = math.sqrt(f2)
        A_inv = np.linalg.inv(np.array([[f, 0, u0], [0, f, v0], [0, 0, 1.0]]))
        B = A_inv.T @ A_inv
        wh = math.sqrt((n2 @ B @ n2) / (n3 @ B @ n3))
        ratio = 1 / wh
        return ratio if 0.2 < ratio < 5 else simple
    except (ZeroDivisionError, ValueError, np.linalg.LinAlgError, FloatingPointError):
        return simple


def guess_paper(w: float, h: float) -> str | None:
    """Papel conocido cuya proporcion se parece a la de la hoja (tolerancia 7%)."""
    ratio = max(w, h) / max(min(w, h), 1)
    best, err = None, 0.07
    for name, (pw, ph) in PAPERS.items():
        r = max(pw, ph) / min(pw, ph)
        e = abs(ratio - r) / r
        if e < err:
            best, err = name, e
    return best


def warp(im, quad: list[tuple[float, float]], paper: str = "auto", turns: int = 0, max_side: int = 3000):
    """Endereza la hoja. Devuelve (imagen PIL, nombre de papel o None)."""
    import cv2
    from PIL import Image
    rgb = np.asarray(_flatten(im).convert("RGB"))
    h, w = rgb.shape[:2]
    pts = np.float32([(x * w, y * h) for x, y in quad])
    tl, tr, br, bl = pts
    width = max(_dist(tl, tr), _dist(bl, br))  # se conserva la resolucion del lado mas nitido
    height = width * true_ratio(pts, w, h)
    name = guess_paper(width, height) if paper == "auto" else paper
    if name in PAPERS:  # la perspectiva deforma un poco: se ajusta a la medida real del papel
        pw, ph = PAPERS[name]
        long_side, short_side = max(pw, ph), min(pw, ph)
        if width >= height:
            height = width * short_side / long_side
        else:
            height = width * long_side / short_side
    k = min(1.15, max_side / max(width, height))  # nunca mas de 3000 px ni ampliar de mas
    W, H = max(8, round(width * k)), max(8, round(height * k))
    M = cv2.getPerspectiveTransform(pts, np.float32([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]]))
    out = cv2.warpPerspective(rgb, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    m = max(2, round(0.005 * min(W, H)))  # un hilo de mesa en el borde se veria como linea punteada
    img = Image.fromarray(np.ascontiguousarray(out[m:H - m, m:W - m]))
    if turns % 4:
        img = img.rotate(-90 * (turns % 4), expand=True)
    return img, name


# ---------------------------------------------------------------- limpiar

def _background(ch: np.ndarray) -> np.ndarray:
    """Luz de fondo (papel + sombras), calculada en chico para que sea rapido."""
    import cv2
    h, w = ch.shape
    k = 4
    small = cv2.resize(ch, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    ksize = max(15, (min(small.shape) // 12) | 1)
    bg = cv2.medianBlur(cv2.dilate(small, np.ones((5, 5), np.uint8)), min(ksize, 255) | 1)
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)


def _flatten_light(ch: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.divide(ch, np.maximum(_background(ch), 1), scale=255)


def enhance(img, mode: str):
    """mode: 'byn' (blanco y negro), 'grises', 'color' (sin sombras) u 'original'."""
    import cv2
    from PIL import Image
    if mode == "original":
        return img.convert("RGB")
    rgb = np.asarray(img.convert("RGB"))
    if mode == "color":
        chans = [_flatten_light(rgb[..., c]) for c in range(3)]
        out = np.stack(chans, axis=-1).astype(np.float32)
        out = np.clip((out - 12) * (255 / 235), 0, 255)  # papel blanco, tinta mas firme
        return Image.fromarray(out.astype(np.uint8), "RGB")
    gray = _flatten_light(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
    if mode == "grises":
        lo, hi = np.percentile(gray, (1, 99))
        out = np.clip((gray.astype(np.float32) - lo) * (255 / max(hi - lo, 1)), 0, 255)
        return Image.fromarray(out.astype(np.uint8), "L")
    block = max(15, (min(gray.shape) // 45) | 1)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 13)
    bw = np.where(gray > 235, 255, bw).astype(np.uint8)  # lo que ya es papel queda blanco
    n, labels, stats, _ = cv2.connectedComponentsWithStats(255 - bw, connectivity=8)
    specks = np.isin(labels, np.nonzero(stats[:, cv2.CC_STAT_AREA] < 4)[0][1:])  # motas sueltas
    bw[specks] = 255
    return Image.fromarray(bw, "L").convert("1")


# ---------------------------------------------------------------- PDF / imagenes

def _page_size(img, paper: str | None) -> tuple[float, float]:
    landscape = img.width > img.height
    if paper in PAPERS:
        pw, ph = (v * PT_PER_MM for v in PAPERS[paper])
        long_side, short_side = max(pw, ph), min(pw, ph)
        return (long_side, short_side) if landscape else (short_side, long_side)
    w = 612.0  # ancho carta como referencia
    if landscape:
        return w * img.width / img.height if img.height else w, w
    return w, w * img.height / img.width


def scan_documents(pages: list[dict], mode: str, paper: str, fmt: str, ocr: bool) -> list[Path]:
    """pages: [{'path', 'quad', 'turns'}]. Arma un PDF (o una imagen por pagina) al lado de la primera foto."""
    if not pages:
        raise GajoError("No hay fotos para escanear")
    from . import progress
    first = Path(pages[0]["path"])
    steps = len(pages) * (2 if ocr and fmt == "pdf" else 1)
    results = []
    for n, pg in enumerate(pages):
        progress.report(n / steps, f"Enderezando foto {n + 1} de {len(pages)}")
        img, name = warp(open_image(Path(pg["path"])), pg.get("quad") or FULL, paper, pg.get("turns", 0))
        results.append((enhance(img, mode), name if paper == "auto" else paper))
    stem = f"{first.stem}-escaneado"
    if fmt == "jpg":
        outs = []
        folder = first.parent if len(results) == 1 else unique_dir(first.parent, stem)
        for i, (img, _) in enumerate(results, 1):
            ext = "png" if mode == "byn" else "jpg"
            target = unique_path(folder, stem if len(results) == 1 else f"{first.stem}-{i:02d}", ext)
            if ext == "png":
                img.save(target, "PNG", optimize=True)
            else:
                img.convert("RGB" if mode != "grises" else "L").save(target, "JPEG", quality=88, optimize=True)
            outs.append(target)
        return [folder] if len(results) > 1 else outs
    import fitz
    out = unique_path(first.parent, stem, "pdf")
    with fitz.open() as doc:
        for img, name in results:
            pw, ph = _page_size(img, name)
            page = doc.new_page(width=pw, height=ph)
            buf = io.BytesIO()
            if mode == "byn":
                img.save(buf, "PNG", optimize=True)
            else:
                img.save(buf, "JPEG", quality=82, optimize=True)
            page.insert_image(page.rect, stream=buf.getvalue())
        if ocr:
            from .ocr import add_text_layer
            for n, page in enumerate(doc):
                progress.report((len(pages) + n) / steps, f"Leyendo texto, página {n + 1} de {len(doc)}")
                add_text_layer(page)
        doc.save(out, garbage=3, deflate=True)
    return [out]
