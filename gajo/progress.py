"""Avance de los trabajos largos: los motores avisan por donde van y la ventanita lo muestra.

Los motores llaman report(fraccion) desde su hilo; si el usuario cancelo, report lanza Cancelled.
La ventana lee el estado cada poco tiempo (solo lectura de numeros: seguro entre hilos).
"""
from __future__ import annotations

import threading
import time

from .tools import GajoError


class Cancelled(GajoError):
    pass


class Job:
    def __init__(self, title: str, total: int):
        self.title = title
        self.total = max(1, total)
        self.index = 0             # archivo actual (0..total-1)
        self.fraction = 0.0        # avance dentro del archivo actual
        self.known = False         # True si el motor informa porcentaje real
        self.detail = ""
        self.cancelled = False
        self.done = False
        self.ok = False
        self.started = time.monotonic()

    def overall(self) -> float:
        return min(1.0, (self.index + (self.fraction if self.known else 0.0)) / self.total)


_local = threading.local()


def bind(job: Job | None) -> None:
    _local.job = job


def current() -> Job | None:
    return getattr(_local, "job", None)


def report(fraction: float | None = None, detail: str | None = None) -> None:
    """Avance del archivo actual (0..1). Si el usuario cancelo, corta el trabajo."""
    job = current()
    if job is None:
        return
    if job.cancelled:
        raise Cancelled("Cancelado")
    if fraction is not None:
        job.fraction = max(0.0, min(1.0, fraction))
        job.known = True
    if detail is not None:
        job.detail = detail


def check() -> None:
    job = current()
    if job is not None and job.cancelled:
        raise Cancelled("Cancelado")
