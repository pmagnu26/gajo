"""Dibujos de la captura como objetos independientes (se mueven, estiran y borran por separado).

Cada objeto es un dict: kind ('pen', 'marker', 'line', 'arrow', 'rect', 'text'), color,
width, pts (lista de (x, y)) y, en texto, text/size/w/h. Coordenadas en pixeles logicos.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

Pt = tuple[float, float]


def text_font(size: float) -> QFont:
    f = QFont("Segoe UI", 1, QFont.Weight.DemiBold)
    f.setPixelSize(max(8, int(size)))
    return f


def measure_text(s: dict) -> None:
    fm = QFontMetricsF(text_font(s["size"]))
    lines = s["text"].split("\n") or [""]
    s["w"] = max(fm.horizontalAdvance(ln) for ln in lines) + 2
    s["h"] = fm.height() * len(lines)


def arrow_head(width: float) -> float:
    return max(11.0, width * 4.2)


# ---------------------------------------------------------------- dibujo

def _smooth_path(pts: list[Pt]) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    if len(pts) == 1:
        path.lineTo(QPointF(pts[0][0] + 0.01, pts[0][1]))
        return path
    for i in range(1, len(pts) - 1):
        mid = ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
        path.quadTo(QPointF(*pts[i]), QPointF(*mid))
    path.lineTo(QPointF(*pts[-1]))
    return path


def blur_path(s: dict) -> QPainterPath:
    """Zona que cubre un trazo de difuminar (pincel ancho de puntas redondas)."""
    from PySide6.QtGui import QPainterPathStroker
    st = QPainterPathStroker()
    st.setWidth(blur_width(s["width"]))
    st.setCapStyle(Qt.PenCapStyle.RoundCap)
    st.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return st.createStroke(_smooth_path(s["pts"]))


def blur_width(w: float) -> float:
    return max(14.0, w * 6)


def draw_shape(p: QPainter, s: dict, blur=None) -> None:
    """blur: (imagen pixelada de toda la pantalla, rectangulo donde va) para los trazos de difuminar."""
    color = QColor(s["color"])
    w = s["width"]
    k = s["kind"]
    pts = s["pts"]
    if k == "blur":
        if blur is not None:
            img, rect = blur
            p.save()
            p.setClipPath(blur_path(s))
            p.drawImage(rect, img)
            p.restore()
        return
    if k in ("pen", "marker"):
        if k == "marker":
            color.setAlphaF(0.4)
            w = w * 4
        p.setPen(QPen(color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(_smooth_path(pts))
    elif k == "line":
        p.setPen(QPen(color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(*pts[0]), QPointF(*pts[1]))
    elif k == "arrow":
        a, b = QPointF(*pts[0]), QPointF(*pts[1])
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        head = arrow_head(w)
        length = math.hypot(b.x() - a.x(), b.y() - a.y())
        if length > head * 0.6:
            base = QPointF(b.x() - math.cos(ang) * head * 0.75, b.y() - math.sin(ang) * head * 0.75)
            p.setPen(QPen(color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, base)
        tip = QPainterPath(b)
        for sgn in (-1, 1):
            tip.lineTo(QPointF(b.x() - math.cos(ang + sgn * 0.42) * head, b.y() - math.sin(ang + sgn * 0.42) * head))
        tip.closeSubpath()
        p.setPen(QPen(color, 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(color)
        p.drawPath(tip)
    elif k == "rect":
        p.setPen(QPen(color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(QPointF(*pts[0]), QPointF(*pts[1])).normalized())
    elif k == "text":
        p.setFont(text_font(s["size"]))
        fm = QFontMetricsF(p.font())
        x, y = pts[0]
        if s.get("bg"):  # fondo del color elegido y letra que contraste
            pad = s["size"] * 0.28
            if "w" not in s:
                measure_text(s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x - pad, y - pad * 0.5, s["w"] + 2 * pad, s["h"] + pad), pad * 0.8, pad * 0.8)
            color = QColor("#111111") if color.lightness() > 150 else QColor("#FFFFFF")
        p.setPen(color)
        for i, ln in enumerate(s["text"].split("\n")):
            p.drawText(QPointF(x, y + fm.ascent() + i * fm.height()), ln)


def handles(s: dict) -> list[Pt]:
    """Puntos que se pueden arrastrar para estirar el objeto."""
    if s["kind"] in ("line", "arrow"):
        return list(s["pts"])
    if s["kind"] == "rect":
        (x0, y0), (x1, y1) = s["pts"]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return []


def set_handle(s: dict, i: int, q: Pt) -> None:
    if s["kind"] in ("line", "arrow"):
        s["pts"][i] = q
    elif s["kind"] == "rect":
        (x0, y0), (x1, y1) = s["pts"]
        if i == 0:
            x0, y0 = q
        elif i == 1:
            x1, y0 = q
        elif i == 2:
            x1, y1 = q
        else:
            x0, y1 = q
        s["pts"] = [(x0, y0), (x1, y1)]


def bbox(s: dict) -> QRectF:
    if s["kind"] == "text":
        x, y = s["pts"][0]
        return QRectF(x, y, s.get("w", 10), s.get("h", 10))
    xs = [q[0] for q in s["pts"]]
    ys = [q[1] for q in s["pts"]]
    m = s["width"] * (2 if s["kind"] == "marker" else 0.5) + (arrow_head(s["width"]) if s["kind"] == "arrow" else 0)
    return QRectF(min(xs) - m, min(ys) - m, max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m)


def move(s: dict, dx: float, dy: float) -> None:
    s["pts"] = [(x + dx, y + dy) for x, y in s["pts"]]


# ---------------------------------------------------------------- geometria

def dist_seg(p: Pt, a: Pt, b: Pt) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _rect_edges(s: dict) -> list[tuple[Pt, Pt]]:
    c = handles(s)
    return [(c[i], c[(i + 1) % 4]) for i in range(4)]


def hit(s: dict, p: Pt, tol: float = 6.0) -> bool:
    k = s["kind"]
    reach = s["width"] * (2 if k == "marker" else 0.5) + tol
    if k == "text":
        return bbox(s).adjusted(-4, -4, 4, 4).contains(QPointF(*p))
    if k == "blur":
        reach = blur_width(s["width"]) / 2 + tol
        k = "pen"
    if k in ("pen", "marker"):
        pts = s["pts"]
        if len(pts) == 1:
            return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= reach
        return any(dist_seg(p, pts[i], pts[i + 1]) <= reach for i in range(len(pts) - 1))
    if k in ("line", "arrow"):
        extra = arrow_head(s["width"]) * 0.5 if k == "arrow" else 0
        return dist_seg(p, *s["pts"]) <= reach + extra
    if k == "rect":
        return any(dist_seg(p, a, b) <= reach for a, b in _rect_edges(s))
    return False


def top_hit(shapes: list[dict], p: Pt) -> int:
    for i in range(len(shapes) - 1, -1, -1):
        if hit(shapes[i], p):
            return i
    return -1


def densify(pts: list[Pt], step: float = 2.5) -> list[Pt]:
    """Agrega puntos intermedios para que la goma corte con precision."""
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = int(d // step)
        for k in range(1, n + 1):
            t = k / (n + 1)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


def ortho(a: Pt, b: Pt) -> Pt:
    """Con Shift: solo horizontal o vertical desde a."""
    return (b[0], a[1]) if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else (a[0], b[1])


def snap(a: Pt, b: Pt, square: bool = False) -> Pt:
    """Con Shift: lineas a 0/45/90 grados, rectangulos cuadrados."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if square:
        m = max(abs(dx), abs(dy))
        return (a[0] + math.copysign(m, dx or 1), a[1] + math.copysign(m, dy or 1))
    ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    L = math.hypot(dx, dy)
    return (a[0] + L * math.cos(ang), a[1] + L * math.sin(ang))


def _circle_interval(a: Pt, b: Pt, c: Pt, r: float) -> tuple[float, float] | None:
    """Tramo [t0, t1] del segmento a-b que queda dentro del circulo (c, r)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    fx, fy = a[0] - c[0], a[1] - c[1]
    A = dx * dx + dy * dy
    if A == 0:
        return (0.0, 1.0) if math.hypot(fx, fy) <= r else None
    B = 2 * (fx * dx + fy * dy)
    C = fx * fx + fy * fy - r * r
    disc = B * B - 4 * A * C
    if disc <= 0:
        return None
    sq = math.sqrt(disc)
    t0, t1 = (-B - sq) / (2 * A), (-B + sq) / (2 * A)
    t0, t1 = max(t0, 0.0), min(t1, 1.0)
    return (t0, t1) if t0 < t1 else None


def _erase_segment(s: dict, c: Pt, r: float) -> list[dict] | None:
    a, b = s["pts"]
    span = _circle_interval(a, b, c, r + s["width"] / 2)
    if span is None:
        return None
    t0, t1 = span
    at = lambda t: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)  # noqa: E731
    pieces = []
    if math.hypot(*(x - y for x, y in zip(at(t0), a))) > 3:
        pieces.append({**s, "kind": "line", "pts": [a, at(t0)]})
    if math.hypot(*(x - y for x, y in zip(b, at(t1)))) > 3:
        pieces.append({**s, "pts": [at(t1), b]})  # conserva la punta si era flecha
    return pieces


def erase(shapes: list[dict], c: Pt, r: float) -> tuple[list[dict], bool]:
    """Borra solo lo que toca la goma: corta trazos y lineas, quita textos tocados."""
    out: list[dict] = []
    changed = False
    for s in shapes:
        k = s["kind"]
        if k in ("pen", "marker", "blur"):
            reach = r + (blur_width(s["width"]) / 2 if k == "blur" else s["width"] * (2 if k == "marker" else 0.5))
            runs, run, cut = [], [], False
            for q in s["pts"]:
                if math.hypot(q[0] - c[0], q[1] - c[1]) <= reach:
                    cut = True
                    if run:
                        runs.append(run)
                        run = []
                else:
                    run.append(q)
            if run:
                runs.append(run)
            if not cut:
                out.append(s)
                continue
            changed = True
            out += [{**s, "pts": rn} for rn in runs if len(rn) >= 2]
        elif k in ("line", "arrow"):
            pieces = _erase_segment(s, c, r)
            if pieces is None:
                out.append(s)
            else:
                changed = True
                out += pieces
        elif k == "rect":
            if any(dist_seg(c, a, b) <= r + s["width"] / 2 for a, b in _rect_edges(s)):
                changed = True
                sides = [{**s, "kind": "line", "pts": [a, b]} for a, b in _rect_edges(s)]
                rest, _ = erase(sides, c, r)
                out += rest
            else:
                out.append(s)
        elif k == "text":
            if bbox(s).adjusted(-r, -r, r, r).contains(QPointF(*c)):
                changed = True
            else:
                out.append(s)
        else:
            out.append(s)
    return out, changed
