"""Gajo: conversor de archivos sin conexion para Windows. Doble clic para iniciar."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gajo.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv))
