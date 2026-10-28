"""Publica la version actual de Gajo en GitHub para que los demas PC se actualicen solos.

Uso:  python publicar.py "que cambio en esta version"
Arma dist/Gajo-<version>.zip, calcula su huella SHA-256 y crea el lanzamiento v<version>
en github.com/pmagnu26/gajo (repositorio publico: los usuarios no necesitan cuenta).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import crear_paquete

REPO = "pmagnu26/gajo"
ROOT = Path(__file__).resolve().parent


def main() -> int:
    notes = sys.argv[1] if len(sys.argv) > 1 else "Mejoras y correcciones."
    version = crear_paquete.version()
    z = crear_paquete.main()
    sha = z.with_name(z.name + ".sha256")
    sha.write_text(f"{hashlib.sha256(z.read_bytes()).hexdigest()}  {z.name}\n", encoding="ascii")
    tag = f"v{version}"
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", REPO], capture_output=True)
    if existing.returncode == 0:
        print(f"Ya existe {tag} en GitHub: sube el número de versión en gajo/__init__.py antes de publicar.")
        return 1
    subprocess.run(["git", "-C", str(ROOT), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ROOT), "commit", "-q", "-m", f"Gajo {version}: {notes}"], check=False)
    subprocess.run(["git", "-C", str(ROOT), "push", "-q", "origin", "HEAD"], check=True)
    subprocess.run(["gh", "release", "create", tag, str(z), str(sha), "--repo", REPO,
                    "--title", f"Gajo {version}", "--notes", notes, "--target", "main"], check=True)
    print(f"Publicado {tag}: los Gajo instalados lo ofrecerán dentro de un día (o al buscar actualizaciones).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
