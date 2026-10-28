"""Arma dist/Gajo-<version>.zip para instalar Gajo en otros PC.

Uso:  python crear_paquete.py
El .zip trae "Instalar Gajo.bat" (doble clic) y la carpeta app con el programa.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_ITEMS = ["gajo.pyw", "gajo", "modelos", "gajo.ico", "gajo.png", "LÉEME.md", "requirements.txt"]
BOM = b"\xef\xbb\xbf"  # PowerShell 5.1 necesita BOM para leer bien las tildes de los .ps1


def version() -> str:
    text = (ROOT / "gajo" / "__init__.py").read_text(encoding="utf-8-sig")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def main() -> Path:
    name = f"Gajo-{version()}"
    out = ROOT / "dist" / f"{name}.zip"
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for item in APP_ITEMS:
            src = ROOT / item
            files = [src] if src.is_file() else sorted(f for f in src.rglob("*") if f.is_file())
            for f in files:
                if "__pycache__" in f.parts or f.suffix == ".pyc":
                    continue
                z.write(f, f"{name}/app/{f.relative_to(ROOT).as_posix()}")
                count += 1
        for f in sorted((ROOT / "instalador").iterdir()):
            data = f.read_bytes()
            if f.suffix in (".ps1", ".txt") and not data.startswith(BOM):
                data = BOM + data
            z.writestr(f"{name}/{f.name}", data)
            count += 1
    print(f"{out}  ({count} archivos, {out.stat().st_size / 1024:.0f} KB)")
    return out


if __name__ == "__main__":
    main()
