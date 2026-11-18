"""Geokodierung der Ladeorte ohne externe Dienste (Raumbezug Phase 3).

Grundlage: GeoNames-Postleitzahlen (download.geonames.org/export/zip/allCountries.zip, CC-BY 4.0), einmalig per
Befehl in die Tabelle geo_plz eingespielt (nur die Länder aus services/evse.LAENDER):
    docker compose run --rm app python -m app.services.geokodierung [/data/geo/allCountries.txt]

Je Ladeort, der Reihe nach (Genauigkeit in Klammern):
1. Land + PLZ (wirksame PLZ, also auch manuell eingetragene) → Mittel der Einträge, bei mehreren Orten
   bevorzugt der mit passendem Namen („plz“);
2. Land + Ortsname: alle Adressabschnitte ohne Ziffern, dann einzelne Wörter bzw. Wortpaare daraus („ort“);
   erst exakt, dann als Namensanfang („Testkirchen“ → „Testkirchen am See“) – Letzteres nur, wenn alle Treffer
   an einem Ort liegen (mehrere „Testmarkt …“ bleiben offen);
3. Mittelpunkt des Landes („land“) – zählt nicht für die Heatmap, erscheint unter „Aufgaben“.
Ein manuell eingetragener Ortsname ersetzt Schritt 2 (nur dieser Name, exakt bzw. eindeutiger Namensanfang).
Ein Ladeort mit Genauigkeit „manuell“ oder „verzichtet“ (bewusst ohne Verortung) wird nie überschrieben.
"""
import re
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import GeoPlz, Ladeort
from app.services.evse import LAENDER

SECHS = Decimal("0.000001")


def plz_norm(text: str | None, land: str | None = None) -> str:
    """„123 45“ → „12345“; Ländervorsatz („LV-1050“) entfernt; NL nur die vier Ziffern (GeoNames ohne Buchstaben)."""
    text = re.sub(r"\s+", "", (text or "").upper())
    if land and text.startswith(f"{land}-"):
        text = text[len(land) + 1:]
    if land == "NL":
        text = text[:4]
    return text


def name_norm(text: str | None) -> str:
    """Für den Namensvergleich: klein, ohne Akzente, „St.“/„St “ → „sankt“, Bindestriche wie Leerzeichen."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(z for z in text if not unicodedata.combining(z)).replace("ß", "ss")
    text = re.sub(r"\bst\.?\s+", "sankt ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


# ------------------------------------------------------------------ Einspielen

def einlesen(db: Session, pfad: Path, laender: set[str] | None = None) -> int:
    """GeoNames-Datei (Tab-getrennt) einspielen; ersetzt den bisherigen Inhalt. Liefert die Anzahl Einträge."""
    laender = laender or set(LAENDER)
    db.execute(delete(GeoPlz))
    zeilen = []
    with open(pfad, encoding="utf-8") as datei:
        for zeile in datei:
            teile = zeile.rstrip("\n").split("\t")
            if len(teile) < 11 or teile[0] not in laender or not teile[9] or not teile[10]:
                continue
            land, plz, name = teile[0], teile[1], teile[2]
            zeilen.append({"land": land, "plz": plz[:20], "plz_norm": plz_norm(plz, land)[:20], "name": name[:180],
                           "name_norm": name_norm(name)[:180], "lat": Decimal(teile[9]).quantize(SECHS),
                           "lon": Decimal(teile[10]).quantize(SECHS)})
    for i in range(0, len(zeilen), 5000):
        db.execute(GeoPlz.__table__.insert(), zeilen[i:i + 5000])
    db.flush()
    return len(zeilen)


# ------------------------------------------------------------------ Geokodieren

def _mittel(treffer) -> tuple[Decimal, Decimal]:
    lat = sum((t.lat for t in treffer), Decimal(0)) / len(treffer)
    lon = sum((t.lon for t in treffer), Decimal(0)) / len(treffer)
    return lat.quantize(SECHS), lon.quantize(SECHS)


def _an_einem_ort(treffer, grad: Decimal = Decimal("0.15")) -> bool:
    """Alle Treffer liegen nahe beieinander (≈ 15 km) – sonst ist der Name mehrdeutig."""
    lats, lons = [t.lat for t in treffer], [t.lon for t in treffer]
    return max(lats) - min(lats) <= grad and max(lons) - min(lons) <= grad


def _kandidaten(ort: Ladeort) -> list[str]:
    """Mögliche Ortsnamen aus der Adresse: erkannter Ort, Abschnitte ohne Ziffern (von hinten), dann Wortpaare und
    einzelne Wörter daraus (z. B. „Rastanlage Teststadt Nord“ → „teststadt“)."""
    abschnitte = [a.strip() for a in ort.adresse.split(",")]
    texte = [ort.ort or ""] + [a for a in reversed(abschnitte) if a and not re.search(r"\d", a)]
    ergebnis: list[str] = []
    for text in texte:
        ergebnis.append(name_norm(text))
    for text in texte:
        woerter = [w for w in name_norm(text).split() if len(w) >= 4]
        ergebnis += [" ".join(p) for p in zip(woerter, woerter[1:])] + woerter
    return [k for i, k in enumerate(ergebnis) if k and k not in ergebnis[:i]]


def geokodieren(db: Session, ort: Ladeort) -> str | None:
    """Koordinaten setzen; liefert die Genauigkeit („plz“, „ort“, „land“) oder None. „manuell“ und „verzichtet“
    bleiben unverändert."""
    if ort.genauigkeit in ("manuell", "verzichtet"):
        return ort.genauigkeit
    ort.lat = ort.lon = ort.genauigkeit = None
    if not ort.land:
        return None
    if plz := plz_norm(ort.plz_wirksam, ort.land):
        treffer = db.scalars(select(GeoPlz).where(GeoPlz.land == ort.land, GeoPlz.plz_norm == plz)).all()
        if treffer:
            name = ort.ort_manuell or ort.ort
            passend = [t for t in treffer if name and t.name_norm == name_norm(name)]
            ort.lat, ort.lon = _mittel(passend or treffer)
            ort.genauigkeit = "plz"
            return ort.genauigkeit
    kandidaten = [name_norm(ort.ort_manuell)] if ort.ort_manuell else _kandidaten(ort)
    for name in kandidaten:
        treffer = db.scalars(select(GeoPlz).where(GeoPlz.land == ort.land, GeoPlz.name_norm == name)).all()
        if treffer:
            ort.lat, ort.lon = _mittel(treffer)
            ort.genauigkeit = "ort"
            return ort.genauigkeit
    for name in (k for k in kandidaten if len(k) >= 5 or ort.ort_manuell):  # Namensanfang, nur eindeutig
        treffer = db.scalars(select(GeoPlz).where(GeoPlz.land == ort.land,
                                                  GeoPlz.name_norm.startswith(name + " ", autoescape=True))).all()
        if treffer and _an_einem_ort(treffer):
            ort.lat, ort.lon = _mittel(treffer)
            ort.genauigkeit = "ort"
            return ort.genauigkeit
    mitte = db.execute(select(func.avg(GeoPlz.lat), func.avg(GeoPlz.lon)).where(GeoPlz.land == ort.land)).one()
    if mitte[0] is not None:
        ort.lat, ort.lon = Decimal(mitte[0]).quantize(SECHS), Decimal(mitte[1]).quantize(SECHS)
        ort.genauigkeit = "land"
    return ort.genauigkeit


GENAUIGKEIT = {"manuell": "manuell gesetzt", "plz": "über die PLZ", "ort": "über den Ortsnamen",
               "land": "nur Landesmitte", "verzichtet": "bewusst ohne Verortung"}


def verortung_text(ort: Ladeort) -> str:
    if ort.genauigkeit in ("plz", "ort", "manuell"):
        return f"Verortet {GENAUIGKEIT[ort.genauigkeit]}."
    if ort.genauigkeit == "land":
        return "Nur auf das Land verortet – erscheint nicht auf der Karte."
    if ort.genauigkeit == "verzichtet":
        return "Bewusst ohne Verortung – erscheint nicht auf der Karte und ist keine Aufgabe."
    return "Nicht verortet – erscheint nicht auf der Karte."


def plz_setzen(db: Session, ort: Ladeort, plz: str | None, ortsname: str | None = None) -> None:
    """Manuelle PLZ und/oder manuellen Ortsnamen setzen (leer bzw. gleich dem erkannten = keine manuelle Angabe)
    und den Ort neu verorten; hebt „bewusst ohne Verortung“ auf."""
    ort.plz_manuell = plz if plz and plz != ort.plz else None
    ort.ort_manuell = ortsname if ortsname and ortsname != ort.ort else None
    if ort.genauigkeit == "verzichtet":
        ort.genauigkeit = None
    geokodieren(db, ort)


def verzichten(ort: Ladeort) -> None:
    """Bewusst ohne Verortung: keine Aufgabe mehr, nicht auf der Karte; „Alle neu verorten“ lässt ihn so."""
    ort.lat = ort.lon = None
    ort.genauigkeit = "verzichtet"


def wieder_verorten(db: Session, ort: Ladeort) -> str | None:
    """„Bewusst ohne Verortung“ aufheben und neu verorten; liefert die neue Genauigkeit."""
    if ort.genauigkeit == "verzichtet":
        ort.genauigkeit = None
    return geokodieren(db, ort)


def alle_geokodieren(db: Session, nur_offene: bool = False) -> dict[str | None, int]:
    """Alle Ladeorte (bzw. nur die ohne Koordinaten) geokodieren; liefert die Anzahl je Genauigkeit."""
    q = select(Ladeort)
    if nur_offene:
        q = q.where(Ladeort.genauigkeit.is_(None))
    ergebnis: dict[str | None, int] = {}
    for ort in db.scalars(q):
        g = geokodieren(db, ort)
        ergebnis[g] = ergebnis.get(g, 0) + 1
    db.flush()
    return ergebnis


def verzeichnis_vorhanden(db: Session) -> bool:
    return db.scalar(select(GeoPlz.id).limit(1)) is not None


if __name__ == "__main__":  # Einspielen per Befehl (siehe Modul-Docstring)
    from app.config import einstellungen
    from app.db import SessionLocal

    pfad = Path(sys.argv[1] if len(sys.argv) > 1 else Path(einstellungen().geo_dir) / "allCountries.txt")
    with SessionLocal() as sitzung:
        anzahl = einlesen(sitzung, pfad)
        print(f"{anzahl} Postleitzahlen eingespielt ({pfad}).")
        print("Geokodiert:", alle_geokodieren(sitzung))
        sitzung.commit()
