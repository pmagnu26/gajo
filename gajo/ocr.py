"""Lectura de texto (OCR) sin internet.

Usa el motor Tesseract que viene dentro de PyMuPDF y el modelo de español de
`modelos/tessdata` (tessdata_best). No necesita instalar Tesseract aparte.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from .editing import MODELS
from .engines import _flatten, open_image
from .tools import GajoError, unique_path

TESSDATA = MODELS / "tessdata"
LANG = "spa"


def _check() -> str:
    if not (TESSDATA / f"{LANG}.traineddata").exists():
        raise GajoError("Falta el modelo de español para leer texto (carpeta modelos/tessdata)")
    return str(TESSDATA)


def _clean(text: str) -> str:
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_from_pil(im) -> str:
    """Texto de una imagen PIL. Las imagenes chicas (capturas) se amplian para leerlas mejor."""
    import fitz
    from PIL import Image
    tessdata = _check()
    im = _flatten(im).convert("RGB")
    if max(im.size) < 2200:
        k = min(3.0, 2200 / max(im.size))
        im = im.resize((round(im.width * k), round(im.height * k)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    pix = fitz.Pixmap(buf.getvalue())
    pdf = pix.pdfocr_tobytes(language=LANG, tessdata=tessdata)
    with fitz.open("pdf", pdf) as doc:
        return _clean(doc[0].get_text())


def image_to_text(src: Path) -> list[Path]:
    text = text_from_pil(open_image(src))
    if not text:
        raise GajoError(f"No encontré texto en {src.name}")
    out = unique_path(src.parent, src.stem, "txt")
    out.write_text(text + "\n", encoding="utf-8")
    return [out]


def needs_ocr(page) -> bool:
    """Pagina escaneada: casi sin texto pero con imagenes."""
    return len(page.get_text().strip()) < 25 and bool(page.get_images())


def _ocr_page(page):
    return page.get_textpage_ocr(language=LANG, dpi=300, full=True, tessdata=_check())


def pdf_to_text(src: Path) -> list[Path]:
    """Texto de un PDF; las paginas escaneadas se leen con OCR."""
    import fitz
    from . import progress
    parts = []
    with fitz.open(src) as doc:
        for i, page in enumerate(doc):
            progress.report(i / len(doc), f"Página {i + 1} de {len(doc)}")
            if needs_ocr(page):
                parts.append(_clean(page.get_text(textpage=_ocr_page(page))))
            else:
                parts.append(page.get_text().strip())
    text = "\n\n".join(p for p in parts if p)
    if not text:
        raise GajoError(f"No encontré texto en {src.name}")
    out = unique_path(src.parent, src.stem, "txt")
    out.write_text(text + "\n", encoding="utf-8")
    return [out]


def add_text_layer(page) -> None:
    """Lee la pagina con OCR y le escribe encima el texto invisible, linea por linea
    (por palabra sueltas, al copiar se pegaban)."""
    import fitz
    lines: dict[tuple, list] = {}
    for x0, y0, x1, y1, word, block, line, wno in page.get_text("words", textpage=_ocr_page(page)):
        if word.strip() and y1 > y0:
            lines.setdefault((block, line), []).append((wno, x0, y0, x1, y1, word))
    for ws in lines.values():
        ws.sort()
        text = " ".join(w[5] for w in ws)
        x0, x1 = min(w[1] for w in ws), max(w[3] for w in ws)
        hs = sorted(w[4] - w[2] for w in ws)
        h = hs[len(hs) // 2]
        base = sorted(w[4] for w in ws)[len(ws) // 2] - h * 0.2
        fs = h * 0.9
        length = fitz.get_text_length(text, fontname="helv", fontsize=fs)
        at = fitz.Point(x0, base) * page.derotation_matrix
        if page.rotation == 0 and length > 0:
            page.insert_text(at, text, fontsize=fs, fontname="helv", render_mode=3,
                             morph=(at, fitz.Matrix((x1 - x0) / length, 1)))
        else:
            page.insert_text(at, text, fontsize=fs, fontname="helv", render_mode=3, rotate=page.rotation)


def pdf_make_searchable(src: Path) -> list[Path]:
    """Agrega una capa de texto invisible a las paginas escaneadas: se puede seleccionar,
    copiar y buscar, y el PDF se ve y pesa casi igual."""
    import fitz
    with fitz.open(src) as doc:
        from . import progress
        done = 0
        for i, page in enumerate(doc):
            progress.report(i / len(doc), f"Leyendo página {i + 1} de {len(doc)}")
            if needs_ocr(page):
                add_text_layer(page)
                done += 1
        progress.report(1.0, "Guardando")
        if not done:
            raise GajoError(f"{src.name} ya tiene texto seleccionable")
        out = unique_path(src.parent, f"{src.stem}-ocr", "pdf")
        doc.save(out, garbage=3, deflate=True)
    return [out]
