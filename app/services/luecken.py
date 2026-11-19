"""Lücken in der Fahrer:innen-Zuordnung je Fahrzeug (Hinweis unter „Aufgaben“).

Geprüft wird je aktivem Fahrzeug der Zeitraum ab Leasingbeginn (ohne Leasingbeginn: ab der ersten Zuordnung) bis
zum Leasing-Ende – auch in der Zukunft (z. B. eine Zuordnung endet vor dem Leasing) –, ohne Leasing-Ende bis heute.
Jeder Tag, an dem keine Fahrer:in zugeordnet ist, gehört zu einer Lücke – auch am Ende („seit … ohne Fahrer:in“). Überlappende Zuordnungen (Poolfahrzeuge) zählen gemeinsam. Ladevorgänge in einer
Lücke bekommen keine Person (Auswertung pro Fahrer:in), daher wird ihre Anzahl mit angegeben. Zeiträume „ohne
Fahrer:in“ (FahrzeugOhneFahrer) gelten als abgedeckt – das Fahrzeug war bewusst niemandem zugeordnet.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import heute
from app.formatierung import datum
from app.models import Fahrzeug, FahrzeugOhneFahrer, Ladevorgang, ZuordnungFahrerFahrzeug
from app.services.aufloesung import stichtag_art, stichtag_spalte


@dataclass(frozen=True)
class Luecke:
    fahrzeug: Fahrzeug
    von: date
    bis: date  # inklusive
    offen: bool  # umfasst heute (aktuell ohne Fahrer:in)
    ladevorgaenge: int = 0
    endlos: bool = False  # ohne Leasing-Ende: reicht bis heute, ein Ende ist nicht bekannt
    kuenftig: bool = False  # beginnt erst nach heute

    @property
    def text(self) -> str:
        """z. B. „seit 01.06.2026 ohne Fahrer:in (bis 31.12.2026)“ bzw. „01.01.2027 – 31.03.2027 ohne Fahrer:in“."""
        if self.offen:
            return f"seit {datum(self.von)} ohne Fahrer:in" + ("" if self.endlos else f" (bis {datum(self.bis)})")
        return f"{datum(self.von)} – {datum(self.bis)} ohne Fahrer:in"

    @property
    def tage(self) -> int:
        return (self.bis - self.von).days + 1


def _zeitraeume(von: date, bis: date, zuordnungen: list[tuple[date, date | None]]) -> list[tuple[date, date]]:
    """Nicht abgedeckte Zeiträume in [von, bis] (inklusive)."""
    luecken, cursor = [], von
    for ab, ende in sorted(zuordnungen):
        ende = ende or bis
        if ab > cursor:
            luecken.append((cursor, min(ab - timedelta(days=1), bis)))
        cursor = max(cursor, ende + timedelta(days=1))
        if cursor > bis:
            break
    if cursor <= bis:
        luecken.append((cursor, bis))
    return [(a, b) for a, b in luecken if a <= b]


def ermitteln(db: Session, tag: date | None = None) -> list[Luecke]:
    tag = tag or heute()
    spalte = stichtag_spalte(stichtag_art(db))
    zuordnungen: dict[int, list[tuple[date, date | None]]] = {}
    for z in [*db.scalars(select(ZuordnungFahrerFahrzeug)), *db.scalars(select(FahrzeugOhneFahrer))]:
        zuordnungen.setdefault(z.fahrzeug_id, []).append((z.gueltig_ab, z.gueltig_bis))
    ergebnis = []
    for fz in db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv).order_by(Fahrzeug.kennzeichen)):
        eigene = zuordnungen.get(fz.id, [])
        von = fz.leasing_beginn or min((ab for ab, _ in eigene), default=None)
        if von is None:  # weder Leasingbeginn noch Zuordnung: kein Bezugszeitraum
            continue
        bis = fz.leasing_ende or tag  # mit Leasing-Ende der ganze Leasingzeitraum, auch die Zukunft
        for a, b in _zeitraeume(von, bis, eigene):
            anzahl = db.scalar(select(func.count()).select_from(Ladevorgang).where(
                Ladevorgang.fahrzeug_id == fz.id, spalte >= datetime.combine(a, time.min),
                spalte < datetime.combine(b + timedelta(days=1), time.min))) or 0
            ergebnis.append(Luecke(fz, a, b, offen=a <= tag <= b, ladevorgaenge=anzahl,
                                   endlos=not fz.leasing_ende and b == tag, kuenftig=a > tag))
    return ergebnis
