"""Fristen für die Aufgaben: Leasing, Führerscheinkontrolle, Autobahn-Vignette und Streckenmaut FLEX (Österreich).

- Leasing: aktive Fahrzeuge, deren Leasing-Ende innerhalb der Vorwarnzeit liegt oder schon vorbei ist.
- Führerscheinkontrolle (nur mit Feature): aktive Fahrer:innen; nächste Kontrolle = letzte + Intervall (Monate).
  Gemeldet, sobald sie innerhalb der Vorwarnzeit liegt oder überfällig ist; ohne eingetragene Kontrolle als fällig.
- Vignette und Streckenmaut FLEX (nur mit Feature, je Fahrzeug nur, wenn dort „nötig“): gemeldet, wenn ein Datum
  fehlt, heute nicht im Gültigkeitszeitraum liegt (noch nicht gültig oder abgelaufen) oder das Ende in die
  Vorwarnzeit fällt.
Schalter, Vorwarnzeiten, Intervall und die Leasingdauer zum Vorbelegen des Endes stehen in den Einstellungen.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import heute
from app.models import Fahrer, Fahrzeug
from app.services.app_einstellungen import AppEinstellungen


def plus_monate(tag: date, monate: int) -> date:
    """Datum + Monate; fehlt der Tag im Zielmonat (31.1. + 1 Monat), dessen letzter Tag."""
    index = tag.year * 12 + tag.month - 1 + monate
    jahr, monat = divmod(index, 12)
    return date(jahr, monat + 1, min(tag.day, monthrange(jahr, monat + 1)[1]))


def leasing_ende_vorschlag(beginn: date, monate: int) -> date:
    """Leasing-Ende aus Beginn und Dauer: letzter Tag vor dem Monatsjahrestag (1.1.2026 + 36 → 31.12.2028)."""
    return plus_monate(beginn, monate) - timedelta(days=1)


@dataclass(frozen=True)
class Frist:
    art: str  # "leasing" | "fuehrerschein" | "vignette" | "streckenmaut"
    name: str  # Kennzeichen bzw. Name
    link: str
    faellig: date | None  # Ende (Leasing, Vignette, Maut) bzw. nächste Kontrolle; None = kein Datum eingetragen
    tag: date  # Bezugstag (heute)
    ab: date | None = None  # Beginn der Gültigkeit (Vignette, Maut)
    ab_fehlt: bool = False  # Gültigkeitsbeginn nicht eingetragen

    @property
    def zustand(self) -> str:
        """„fehlt“, „noch_nicht“ (noch nicht gültig), „abgelaufen“ oder „bald“."""
        if self.faellig is None or self.ab_fehlt:
            return "fehlt"
        if self.ab is not None and self.ab > self.tag:
            return "noch_nicht"
        return "abgelaufen" if self.faellig < self.tag else "bald"

    @property
    def ueberfaellig(self) -> bool:
        return self.zustand != "bald"

    @property
    def tage(self) -> int | None:
        """Tage bis zur Frist (negativ = überfällig)."""
        return None if self.faellig is None else (self.faellig - self.tag).days


def ermitteln(db: Session, e: AppEinstellungen, tag: date | None = None) -> list[Frist]:
    """Alle gemeldeten Fristen, überfällige zuerst, dann nach Datum."""
    tag = tag or heute()
    fristen = []
    grenze_leasing = tag + timedelta(days=e.leasing_vorwarnung_tage)
    for fz in db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv, Fahrzeug.leasing_ende.is_not(None),
                                                Fahrzeug.leasing_ende <= grenze_leasing)):
        fristen.append(Frist("leasing", fz.kennzeichen, f"/stammdaten/fahrzeuge/{fz.id}", fz.leasing_ende, tag))
    if e.feature_fuehrerschein:
        grenze_fs = tag + timedelta(days=e.fuehrerschein_vorwarnung_tage)
        for f in db.scalars(select(Fahrer).where(Fahrer.aktiv)):
            naechste = naechste_kontrolle(f, e)
            if naechste is None or naechste <= grenze_fs:
                fristen.append(Frist("fuehrerschein", f.name, f"/stammdaten/fahrer/{f.id}", naechste, tag))
    for art in MAUT:
        if not maut_aktiv(e, art):
            continue
        for fz in db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv)):
            if (frist := maut_frist(fz, art, e, tag)) is not None:
                fristen.append(frist)
    return sorted(fristen, key=lambda x: (x.faellig or date.min, x.art, x.name))


# Österreich: Vignette und Streckenmaut FLEX (Felder am Fahrzeug: <art>_noetig, <art>_ab, <art>_bis)
MAUT = {"vignette": "Vignette", "streckenmaut": "Streckenmaut FLEX"}


def maut_aktiv(e: AppEinstellungen, art: str) -> bool:
    return e.feature_vignette if art == "vignette" else e.feature_streckenmaut


def maut_frist(fz: Fahrzeug, art: str, e: AppEinstellungen, tag: date | None = None) -> Frist | None:
    """Frist, wenn beim aktiven Fahrzeug etwas zu tun ist (Datum fehlt, nicht gültig, läuft bald ab); sonst None."""
    tag = tag or heute()
    if not getattr(fz, f"{art}_noetig"):
        return None
    ab, bis = getattr(fz, f"{art}_ab"), getattr(fz, f"{art}_bis")
    vorwarnung = e.vignette_vorwarnung_tage if art == "vignette" else e.streckenmaut_vorwarnung_tage
    frist = Frist(art, fz.kennzeichen, f"/stammdaten/fahrzeuge/{fz.id}", bis, tag, ab, ab_fehlt=ab is None)
    if frist.zustand != "bald" or bis <= tag + timedelta(days=vorwarnung):
        return frist
    return None


def jahresvignette(jahr: int) -> tuple[date, date]:
    """Gültigkeit der österreichischen Jahresvignette „jahr“: 1.12. des Vorjahres bis 31.1. des Folgejahres."""
    return date(jahr - 1, 12, 1), date(jahr + 1, 1, 31)


def einstufen(faellig: date | None, vorwarnung_tage: int, tag: date | None = None) -> str:
    """Für Listen: „ueberfaellig“, „bald“ (innerhalb der Hinweisfrist), „fehlt“ (kein Datum) oder „“."""
    tag = tag or heute()
    if faellig is None:
        return "fehlt"
    if faellig < tag:
        return "ueberfaellig"
    return "bald" if faellig <= tag + timedelta(days=vorwarnung_tage) else ""


def naechste_kontrolle(f: Fahrer, e: AppEinstellungen) -> date | None:
    """Nächste Führerscheinkontrolle (letzte + Intervall); None ohne eingetragene Kontrolle."""
    return plus_monate(f.fuehrerschein_kontrolle, e.fuehrerschein_intervall_monate) if f.fuehrerschein_kontrolle else None
