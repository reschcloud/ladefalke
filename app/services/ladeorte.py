"""Ladeorte: gleiche Adresse → ein Ladeort, mit PLZ und Ort für die Geokodierung (Raumbezug Phase 2).

Die Rechnungen liefern den Standort als Text in unterschiedlicher Reihenfolge, z. B.
  „1010 Wien, Teststraße 1“ (Wien Energie), „Teststraße 1 1010, Teststadt“ (DCS), „Teststraße 60, 4600 Testort“ (EnBW),
  „Teststraße 1, Testdorf“ (LINZ AG, ohne PLZ), „Teststandort 3 (Musterweg 1, 1010 Wien)“ (Wallbox).
Mit dem Land (Phase 1) wird die PLZ nach dem Format des Landes gesucht, der Ort ist der Text dahinter.
Ohne PLZ bleibt der Ort ein Vorschlag (letzter Abschnitt ohne Ziffern); die Geokodierung prüft alle Abschnitte.
Eine manuell eingetragene PLZ hat immer Vorrang und bleibt erhalten.
"""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ladeort

# Postleitzahl-Formate je Land (ohne Ländervorsatz, der vorher entfernt wird)
PLZ_MUSTER = {
    **{land: r"\d{4}" for land in ("AT", "CH", "LI", "HU", "SI", "DK", "NO", "BE", "LU", "BG", "CY", "AL", "MK")},
    **{land: r"\d{5}" for land in ("DE", "IT", "FI", "EE", "HR", "FR", "ES", "ME", "RS", "BA", "LT")},
    "LV": r"\d{4}", "PL": r"\d{2}-\d{3}", "SE": r"\d{3} ?\d{2}", "CZ": r"\d{3} ?\d{2}", "SK": r"\d{3} ?\d{2}",
    "GR": r"\d{3} ?\d{2}", "NL": r"\d{4} ?[A-Z]{2}", "RO": r"\d{6}", "PT": r"\d{4}-\d{3}",
}


@dataclass(frozen=True)
class Adresse:
    schluessel: str  # Land + vereinheitlichter Text – gleiche Adresse, gleicher Ladeort
    text: str  # Anzeige
    plz: str | None
    ort: str | None


def _vereinheitlichen(text: str) -> str:
    text = re.sub(r"\s*,\s*", ", ", " ".join(text.split()))
    return text.strip(" ,")


def analysieren(standort: str | None, land: str | None) -> Adresse | None:
    """Standort-Text → Adresse mit PLZ und Ort (soweit erkennbar); None ohne Standort."""
    if not standort or not standort.strip():
        return None
    text = _vereinheitlichen(standort)
    if m := re.search(r"\(([^()]*\d[^()]*)\)\s*$", text):  # Wallbox: „Bezeichnung (Straße, PLZ Ort)“
        text = _vereinheitlichen(m.group(1))
    plz = ort = None
    muster = PLZ_MUSTER.get(land or "")
    if muster:
        vorsatz = rf"(?:{land}[- ])?" if land else ""
        treffer = list(re.finditer(rf"(?<![\w-]){vorsatz}({muster})(?![\w-])", text))
        if treffer:
            m = treffer[-1]
            plz = m.group(1).replace(" ", "")
            danach = text[m.end():].lstrip(" ,")
            ort = danach.split(",")[0].strip() or None
    if ort is None:  # ohne PLZ (oder PLZ am Ende): letzter Abschnitt ohne Ziffern
        abschnitte = [a.strip() for a in text.split(",")]
        ohne_ziffern = [a for a in abschnitte if a and not re.search(r"\d", a) and len(a) > 1
                        and not re.fullmatch(r"[A-Z]{2}", a)]
        ort = ohne_ziffern[-1] if ohne_ziffern else None
    return Adresse(f"{land or '?'}|{text.lower()}", text, plz, ort)


def ladeort_fuer(db: Session, standort: str | None, land: str | None, cache: dict | None = None) -> Ladeort | None:
    """Ladeort zur Adresse holen oder anlegen. `cache`: Schlüssel → Ladeort, für viele Vorgänge in einem Durchgang."""
    adresse = analysieren(standort, land)
    if adresse is None:
        return None
    cache = {} if cache is None else cache
    if adresse.schluessel in cache:
        return cache[adresse.schluessel]
    ort = db.scalar(select(Ladeort).where(Ladeort.schluessel == adresse.schluessel))
    if ort is None:
        ort = Ladeort(schluessel=adresse.schluessel, land=land, adresse=adresse.text[:300], plz=adresse.plz,
                      ort=(adresse.ort or "")[:120] or None)
        db.add(ort)
        db.flush()
    cache[adresse.schluessel] = ort
    return ort
