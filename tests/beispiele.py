"""Echte Beispieldateien aus samples/ (nicht im Git, echte Personendaten) für Parser-Tests.

Die Tests nennen keine Dateinamen (die enthalten Rechnungs- und Kundennummern), sondern den Anfang der
SHA-256-Prüfsumme der Datei. Fehlt die Datei, wird der Test übersprungen.
"""
import hashlib
from functools import lru_cache
from pathlib import Path

import pytest

SAMPLES = Path(__file__).parent.parent / "samples"


@lru_cache
def _pruefsummen() -> dict[str, Path]:
    return {hashlib.sha256(p.read_bytes()).hexdigest(): p
            for p in sorted(SAMPLES.iterdir()) if p.is_file()} if SAMPLES.is_dir() else {}


def finden(praefix: str) -> Path | None:
    """Datei, deren SHA-256 mit `praefix` beginnt, sonst None."""
    return next((p for h, p in _pruefsummen().items() if h.startswith(praefix)), None)


def beispiel(praefix: str) -> Path:
    """Wie finden(), überspringt den Test aber, wenn die Datei fehlt."""
    pfad = finden(praefix)
    if pfad is None:
        pytest.skip("Beispieldatei nicht vorhanden (samples/ ist nicht im Git)")
    return pfad
