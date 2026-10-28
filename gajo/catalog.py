"""Que opciones muestra cada anillo de la rueda segun los archivos arrastrados."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable

from . import engines as E
from . import ocr
from .tools import norm_ext

KINDS = {
    "imagen": {"jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "gif", "svg"},
    "audio": {"mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma", "aac"},
    "video": {"mp4", "mov", "mkv", "webm", "avi", "wmv"},
    "pdf": {"pdf"},
    "documento": {"docx", "doc", "odt", "rtf", "txt", "md", "html", "epub"},
    "presentacion": {"pptx", "ppt", "odp"},
    "planilla": {"xlsx", "xls", "ods", "csv", "tsv"},
    "subtitulo": {"srt", "vtt"},
    "comprimido": {"zip", "tar", "tgz", "gz", "rar", "7z"},
}

# Formatos de salida de cada tipo, en el orden en que aparecen en la rueda.
TARGETS = {
    "imagen": ["jpg", "png", "webp", "heic", "tiff", "avif", "bmp", "pdf"],
    "audio": ["mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff", "wma"],
    "video": ["mp4", "mov", "mkv", "webm", "avi", "wmv", "gif", "mp3"],
    "pdf": ["docx", "pptx", "xlsx", "jpg", "png", "epub", "txt", "md"],
    "documento": ["pdf", "docx", "odt", "rtf", "txt", "md", "html", "epub"],
    "presentacion": ["pdf", "pptx", "jpg", "png"],
    "planilla": ["xlsx", "ods", "csv", "tsv", "pdf"],
    "subtitulo": ["srt", "vtt", "txt"],
    "comprimido": ["zip", "tar", "tgz"],
}

_CONVERTERS: dict[str, Callable] = {
    "imagen": E.img_convert,
    "audio": E.audio_convert,
    "video": E.video_convert,
    "documento": E.doc_convert,
    "presentacion": E.slides_convert,
    "planilla": E.sheet_convert,
    "subtitulo": E.subs_convert,
    "comprimido": E.archive_convert,
}
_PDF_CONVERTERS = {
    "docx": E.pdf_to_docx, "pptx": E.pdf_to_pptx, "xlsx": E.pdf_to_xlsx,
    "jpg": partial(E.pdf_to_images, fmt="jpg"), "png": partial(E.pdf_to_images, fmt="png"),
    "epub": E.pdf_to_epub, "txt": ocr.pdf_to_text, "md": E.pdf_to_md,
}
_LABEL = {"tgz": "TAR.GZ", "jpg": "JPG", "md": "MD"}


@dataclass
class Op:
    label: str                 # texto corto dentro del gajo
    hint: str                  # descripcion al pasar por encima
    run: Callable | None       # recibe una ruta (por archivo) o la lista completa (multi)
    multi: bool = False        # True: recibe todas las rutas juntas
    ui: bool = False           # True: se ejecuta en la ventana principal (acciones)
    skip_ext: str | None = None  # no aplicar a archivos que ya estan en este formato
    panel: str | None = None   # "modulo:Clase" en gajo/panels: abre una ventana con opciones

    def apply(self, paths: list[Path]) -> list[Path]:
        if self.multi:
            return self.run(paths)
        out: list[Path] = []
        for p in paths:
            if self.skip_ext and norm_ext(p) == self.skip_ext:
                continue
            out += self.run(p)
        return out


def kind_of(path: Path) -> str:
    if path.is_dir():
        return "carpeta"
    ext = norm_ext(path)
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind
    return "otro"


def common_kind(paths: list[Path]) -> str | None:
    kinds = {kind_of(p) for p in paths}
    return kinds.pop() if len(kinds) == 1 else None


def convert_ops(paths: list[Path]) -> list[Op]:
    kind = common_kind(paths)
    if kind not in TARGETS:
        return []
    exts = {norm_ext(p) for p in paths}
    ops = []
    for fmt in TARGETS[kind]:
        if exts == {fmt}:
            continue  # convertir a su mismo formato no tiene sentido
        fn = _PDF_CONVERTERS[fmt] if kind == "pdf" else partial(_CONVERTERS[kind], fmt=fmt)
        label = _LABEL.get(fmt, fmt.upper())
        ops.append(Op(label, f"Convertir a {label}", fn, skip_ext=fmt))
    if kind == "imagen":
        ops.append(Op("TEXTO", "Leer el texto de la imagen (OCR) y guardarlo en .txt", ocr.image_to_text))
    if kind == "imagen" and exts == {"gif"}:
        ops.append(Op("MP4", "Convertir el GIF en video MP4", partial(E.img_convert, fmt="mp4")))
    return ops


def _panel(label: str, hint: str, panel: str) -> Op:
    return Op(label, hint, None, multi=True, panel=panel)


def tool_ops(paths: list[Path]) -> list[Op]:
    kind = common_kind(paths)
    many = len(paths) > 1
    ops: list[Op] = []
    if kind == "imagen":
        ops += [
            _panel("ESCANEAR", "Enderezar y limpiar la foto de un documento y pasarla a PDF", "scan_panel:ScanDocument"),
            _panel("COMPRIMIR", "Comprimir con calidad, peso objetivo o porcentaje", "image_panels:CompressImages"),
            _panel("METADATOS", "Ver, editar o quitar fecha, cámara y ubicación", "image_panels:MetadataPanel"),
        ]
        if not many:
            ops += [
                _panel("EDITAR", "Ajustar luz, color y detalle", "image_panels:EditPhoto"),
                _panel("ANOTAR", "Flechas, texto, formas, números y lupas", "image_panels:AnnotatePhoto"),
            ]
        ops += [
            _panel("FONDO", "Poner la imagen sobre un fondo", "image_panels:AddBackground"),
            _panel("RECORTAR", "Recortar, girar o voltear", "image_panels:CropImage"),
        ]
        if not many:
            ops.append(_panel("OCULTAR", "Tapar caras, textos o zonas privadas", "image_panels:RedactPhoto"))
        ops.append(Op("CREAR PDF", "Juntar en un PDF, una página por imagen", E.imgs_to_pdf, multi=True))
        if many:
            ops.append(_panel("COLLAGE", "Armar un collage", "image_panels:CollagePanel"))
    elif kind == "video":
        ops.append(Op("COMPRIMIR", "Comprimir video (MP4 liviano)", E.video_compress))
        if not many:
            ops += [
                _panel("CORTAR", "Quedarse con una parte del video", "video_panels:TrimVideo"),
                _panel("ENCUADRAR", "Recortar el cuadro o girarlo", "video_panels:CropVideo"),
                _panel("VELOCIDAD", "Hacerlo más lento o más rápido", "video_panels:SpeedVideo"),
                _panel("FOTOGRAMAS", "Guardar cuadros exactos como imagen", "video_panels:VideoSnapshots"),
                _panel("DIVIDIR", "Separar en partes", "video_panels:SplitVideo"),
                _panel("OCULTAR", "Tapar zonas durante un tramo", "video_panels:RedactVideo"),
            ]
        ops.append(Op("SIN AUDIO", "Quitar el sonido", E.video_mute))
        if many:
            ops.append(_panel("UNIR", "Unir en un solo video", "video_panels:JoinVideos"))
    elif kind == "audio":
        if not many:
            ops += [
                _panel("NORMALIZAR", "Emparejar el volumen", "audio_panels:NormalizeAudio"),
                _panel("CORTAR", "Quedarse con una parte", "audio_panels:TrimAudio"),
                _panel("CANALES", "Mono, estéreo y volumen por lado", "audio_panels:AudioChannels"),
                _panel("PITIDO", "Tapar partes con un pitido", "audio_panels:BleepAudio"),
                _panel("VISUALIZAR", "Crear un video con la onda o una imagen", "audio_panels:AudioVisualizer"),
            ]
        ops.append(Op("COMPRIMIR", "Comprimir a MP3 128 kbps", E.audio_compress))
        if many:
            ops.append(_panel("UNIR", "Unir en un solo audio", "audio_panels:JoinAudio"))
    elif kind == "pdf":
        ops.append(Op("COMPRIMIR", "Comprimir PDF (imágenes a 150 dpi)", E.pdf_compress))
        if not many:
            ops.append(_panel("ORGANIZAR", "Ordenar, girar, duplicar o quitar páginas", "pdf_panels:OrganizePdf"))
        ops += [
            Op("OCR", "Hacer seleccionable y buscable el texto de un PDF escaneado", ocr.pdf_make_searchable),
            Op("DIVIDIR", "Separar en un PDF por página", E.pdf_split),
            Op("IMÁGENES", "Extraer las imágenes", E.pdf_extract_images),
        ]
        if many:
            ops.append(_panel("UNIR", "Unir en un solo PDF", "pdf_panels:MergePdfs"))
    elif kind == "comprimido":
        ops.append(Op("EXTRAER", "Descomprimir en una carpeta", E.archive_extract))
    if not ops or (kind == "comprimido" and many):
        ops.append(Op("ZIP", "Comprimir en ZIP", E.make_zip, multi=True))
    return ops


def options(mode: str, paths: list[Path], actions: list[Op]) -> list[Op]:
    if mode == "convertir":
        return convert_ops(paths)
    if mode == "herramientas":
        return tool_ops(paths)
    return actions


def describe(paths: list[Path]) -> str:
    """Texto corto para el centro de la rueda."""
    if len(paths) == 1:
        p = paths[0]
        return "CARPETA" if p.is_dir() else (norm_ext(p).upper() or "ARCHIVO")
    return f"{len(paths)} ARCHIVOS"


def count_conversions() -> int:
    total = 0
    for kind, outs in TARGETS.items():
        for ext in KINDS[kind]:
            total += sum(1 for o in outs if o != ext)
    return total + 1  # GIF a MP4
