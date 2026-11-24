"""Warnungen beim Fahrzeug: Fahrer:innen, Vignette oder Streckenmaut FLEX außerhalb des Leasingzeitraums.

Nur für Fahrzeuge mit Leasingdaten; Ladekarten prüft services/kartenpruefung.py (dort zusätzlich als Aufgabe).
- Fahrer:in → Fahrzeug: Beginn vor dem Leasingbeginn bzw. eingetragenes Ende nach dem Leasing-Ende. Eine offene
  Zuordnung (ohne Ende) gilt erst als außerhalb, wenn das Leasing-Ende vorbei ist – während des Leasings ist
  „bis auf Weiteres“ normal.
- Vignette / Streckenmaut FLEX (nur mit Feature und wenn beim Fahrzeug nötig): Gültigkeit liegt komplett außerhalb
  des Leasings (vermutlich ein falsches Jahr); dass eine Jahresvignette über das Leasing-Ende hinaus gilt, ist normal.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import heute
from app.formatierung import zeitraum
from app.models import Fahrzeug, ZuordnungFahrerFahrzeug
from app.services.app_einstellungen import AppEinstellungen
from app.services.fristen import MAUT, maut_aktiv
from app.services.zuordnung import ueberlappt

TAG = timedelta(days=1)


@dataclass(frozen=True)
class Warnung:
    fahrzeug: Fahrzeug
    art: str  # „fahrer“ | „vignette“ | „streckenmaut“
    von: date
    bis: date | None
    text: str


def _fahrer(fz: Fahrzeug, z: ZuordnungFahrerFahrzeug, tag: date) -> list[Warnung]:
    lb, le = fz.leasing_beginn, fz.leasing_ende
    teile = []
    if lb and z.gueltig_ab < lb:
        teile.append((z.gueltig_ab, min(z.gueltig_bis or lb - TAG, lb - TAG)))
    if le and ((z.gueltig_bis and z.gueltig_bis > le) or (z.gueltig_bis is None and le < tag)):
        teile.append((max(z.gueltig_ab, le + TAG), z.gueltig_bis))
    return [Warnung(fz, "fahrer", a, b, f"{z.fahrer.name} außerhalb des Leasings: {zeitraum(a, b)}") for a, b in teile]


def ermitteln(db: Session, e: AppEinstellungen, fahrzeuge: list[Fahrzeug], tag: date | None = None
              ) -> dict[int, list[Warnung]]:
    """Warnungen je Fahrzeug-ID (nur Fahrzeuge mit Leasingbeginn oder -ende)."""
    tag = tag or heute()
    mit_leasing = [fz for fz in fahrzeuge if (fz.leasing_beginn or fz.leasing_ende) and not fz.ersatzfahrzeug]
    zuordnungen: dict[int, list[ZuordnungFahrerFahrzeug]] = {}
    for z in db.scalars(select(ZuordnungFahrerFahrzeug)
                        .where(ZuordnungFahrerFahrzeug.fahrzeug_id.in_([fz.id for fz in mit_leasing]))
                        .options(selectinload(ZuordnungFahrerFahrzeug.fahrer))
                        .order_by(ZuordnungFahrerFahrzeug.gueltig_ab)):
        zuordnungen.setdefault(z.fahrzeug_id, []).append(z)
    ergebnis: dict[int, list[Warnung]] = {}
    for fz in mit_leasing:
        w = [x for z in zuordnungen.get(fz.id, []) for x in _fahrer(fz, z, tag)]
        for art, name in MAUT.items():
            ab, bis = getattr(fz, f"{art}_ab"), getattr(fz, f"{art}_bis")
            if (maut_aktiv(e, art) and getattr(fz, f"{art}_noetig") and ab and bis
                    and not ueberlappt(ab, bis, fz.leasing_beginn or date.min, fz.leasing_ende)):
                w.append(Warnung(fz, art, ab, bis, f"{name} komplett außerhalb des Leasings: {zeitraum(ab, bis)}"))
        if w:
            ergebnis[fz.id] = w
    return ergebnis
