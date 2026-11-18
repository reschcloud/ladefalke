"""Vollständigkeit der Daten je Monat (Dashboard).

Fehlt (Lücke):
- Jeder MSP mit aktiven Ladekarten erwartet ab seinem ersten Eintrag mindestens einen Eintrag pro Monat
  (Ladevorgang, Gebühr oder Import mit passendem Abrechnungszeitraum).
- MSP mit „Abrechnung je Ladekarte“ (Msp.abrechnung_je_karte) erwarten das je aktiver Karte, ab deren erstem Eintrag.
Hinweis (kein sicherer Fehler):
- Fahrzeuge mit Zuordnung im Monat, aber ohne Ladevorgang;
- Fahrer:innen mit Fahrzeug oder Karte im Monat, aber ohne Ladung (Zurechnung wie in der Auswertung);
- Ladevorgänge ohne Fahrzeug (ohne Gastkarten).
Der laufende Monat gilt als „noch offen“ (Rechnungen kommen erst im Folgemonat), künftige Monate fehlen.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import heute as heute_
from app.formatierung import monat as monat_text
from app.models import (Fahrer, Fahrzeug, Gebuehr, Import, ImportStatus, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte)
from app.services.app_einstellungen import AppEinstellungen
from app.services.aufloesung import fahrer_beim_laden, stichtag
from app.services.auswertung import Zeitraum, gebuehr_stichtag



def _erster(tag: date) -> date:
    return tag.replace(day=1)


def _naechster(monat: date) -> date:
    return (monat + timedelta(days=32)).replace(day=1)


def _monate(von: date, bis: date) -> list[date]:
    """Monatserste aller Monate, die den Zeitraum [von, bis] berühren."""
    ergebnis, m = [], _erster(von)
    while m <= bis:
        ergebnis.append(m)
        m = _naechster(m)
    return ergebnis


def _im_monat(ab: date, bis: date | None, monat: date) -> bool:
    """Zuordnungszeitraum (bis inklusive, None = offen) überschneidet den Monat."""
    return ab < _naechster(monat) and (bis is None or bis >= monat)


@dataclass
class MspLuecke:
    msp: Msp
    karten: list[Ladekarte] = field(default_factory=list)  # bei Abrechnung je Karte: Karten ohne Eintrag
    karten_erwartet: int = 0
    anzeigen: dict = field(default_factory=dict)  # Karten-ID → kartenanzeige.Anzeige im Monat


@dataclass
class MonatsStatus:
    monat: date
    offen: bool = False  # laufender Monat
    luecken: list[MspLuecke] = field(default_factory=list)
    fahrzeuge_ohne: list[Fahrzeug] = field(default_factory=list)
    fahrer_ohne: list[Fahrer] = field(default_factory=list)
    ohne_fahrzeug: int = 0  # Ladevorgänge ohne Fahrzeug

    @property
    def label(self) -> str:
        return monat_text(self.monat)

    @property
    def hinweise(self) -> int:
        return len(self.fahrzeuge_ohne) + len(self.fahrer_ohne) + (1 if self.ohne_fahrzeug else 0)

    @property
    def status(self) -> str:
        """"offen" | "fehlt" | "ok" """
        return "offen" if self.offen else "fehlt" if self.luecken else "ok"


@dataclass
class Vollstaendigkeit:
    monate: list[MonatsStatus]

    @property
    def unvollstaendig(self) -> list[MonatsStatus]:
        return [m for m in self.monate if m.status == "fehlt"]

    @property
    def hinweise(self) -> int:
        return sum(m.hinweise for m in self.monate)

    @property
    def status(self) -> str:
        if self.unvollstaendig:
            return "fehlt"
        return "offen" if all(m.offen for m in self.monate) else "ok"


def pruefen(db: Session, z: Zeitraum, einst: AppEinstellungen, heute: date | None = None) -> Vollstaendigkeit:
    heute = heute or heute_()
    laufend = _erster(heute)
    monate = [m for m in _monate(z.von, z.bis) if m <= laufend]
    if not monate:
        return Vollstaendigkeit([])
    bis = _naechster(monate[-1])

    # ---------------------------------------------------------------- Eintragsmonate (gesamter Datenbestand)
    msp_monate: dict[int, set[date]] = defaultdict(set)
    karten_monate: dict[int, set[date]] = defaultdict(set)
    spalte = Ladevorgang.start if einst.stichtag == "beginn" else Ladevorgang.ende
    for msp_id, karte_id, zeit in db.execute(select(Ladevorgang.msp_id, Ladevorgang.ladekarte_id, spalte)):
        m = _erster(zeit.date())
        msp_monate[msp_id].add(m)
        if karte_id is not None:
            karten_monate[karte_id].add(m)
    for g in db.scalars(select(Gebuehr).options(selectinload(Gebuehr.import_))):
        if (tag := gebuehr_stichtag(g)) is None:
            continue
        if g.import_.msp_id is not None:
            msp_monate[g.import_.msp_id].add(_erster(tag))
        if g.ladekarte_id is not None:
            karten_monate[g.ladekarte_id].add(_erster(tag))
    for imp in db.scalars(select(Import).where(Import.status == ImportStatus.abgeschlossen,
                                               Import.msp_id.is_not(None))):
        von = imp.abrechnungszeitraum_von or imp.rechnungsdatum
        if von is not None:
            msp_monate[imp.msp_id].update(_monate(von, imp.abrechnungszeitraum_bis or von))

    karten_je_msp: dict[int, list[Ladekarte]] = defaultdict(list)
    for k in db.scalars(select(Ladekarte).where(Ladekarte.aktiv, Ladekarte.ist_gastkarte.is_(False))
                        .order_by(Ladekarte.kennung)):
        karten_je_msp[k.msp_id].append(k)
    msps = [m for m in db.scalars(select(Msp).order_by(Msp.name)) if karten_je_msp.get(m.id)]

    # ---------------------------------------------------------------- Ladevorgänge im Zeitraum (für Hinweise)
    vorgaenge = db.scalars(select(Ladevorgang).where(spalte >= datetime.combine(monate[0], datetime.min.time()),
                                                     spalte < datetime.combine(bis, datetime.min.time()))
                           .options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.fahrzeug))).all()
    fz_geladen: dict[date, set[int]] = defaultdict(set)
    fahrer_geladen: dict[date, set[int]] = defaultdict(set)
    ohne_fahrzeug: dict[date, int] = defaultdict(int)
    personen_cache: dict[tuple, list[Fahrer]] = {}
    for v in vorgaenge:
        m = _erster((v.start if einst.stichtag == "beginn" else v.ende).date())
        if v.fahrzeug_id is not None:
            fz_geladen[m].add(v.fahrzeug_id)
        elif not (v.ladekarte and v.ladekarte.ist_gastkarte):
            ohne_fahrzeug[m] += 1
        tag = stichtag(v.start, v.ende, einst.stichtag)
        schluessel = (v.ladekarte_id, v.fahrzeug_id, tag)
        if schluessel not in personen_cache:
            personen_cache[schluessel] = fahrer_beim_laden(db, v.ladekarte, v.fahrzeug, tag)
        fahrer_geladen[m].update(f.id for f in personen_cache[schluessel])

    # Zuordnungen, die den Zeitraum berühren
    erster_tag = monate[0]
    zff = db.scalars(select(ZuordnungFahrerFahrzeug).where(
        ZuordnungFahrerFahrzeug.gueltig_ab < bis,
        or_(ZuordnungFahrerFahrzeug.gueltig_bis.is_(None), ZuordnungFahrerFahrzeug.gueltig_bis >= erster_tag))
        .options(selectinload(ZuordnungFahrerFahrzeug.fahrer), selectinload(ZuordnungFahrerFahrzeug.fahrzeug))).all()
    zk = db.scalars(select(ZuordnungKarte).where(
        ZuordnungKarte.gueltig_ab < bis,
        or_(ZuordnungKarte.gueltig_bis.is_(None), ZuordnungKarte.gueltig_bis >= erster_tag))
        .options(selectinload(ZuordnungKarte.fahrer), selectinload(ZuordnungKarte.fahrzeug))).all()

    # ---------------------------------------------------------------- je Monat
    ergebnis = []
    for m in monate:
        st = MonatsStatus(m, offen=m == laufend)
        if st.offen:
            ergebnis.append(st)
            continue
        for msp in msps:
            if msp.abrechnung_je_karte:
                erwartet = [k for k in karten_je_msp[msp.id]
                            if karten_monate.get(k.id) and min(karten_monate[k.id]) <= m]
                fehlend = [k for k in erwartet if m not in karten_monate[k.id]]
                if fehlend:
                    from calendar import monthrange
                    from app.services.kartenanzeige import fuer_zeitraum
                    ende = m.replace(day=monthrange(m.year, m.month)[1])
                    st.luecken.append(MspLuecke(msp, fehlend, len(erwartet),
                                                fuer_zeitraum(db, fehlend, m, ende, einst.stichtag)))
            elif msp_monate.get(msp.id) and min(msp_monate[msp.id]) <= m and m not in msp_monate[msp.id]:
                st.luecken.append(MspLuecke(msp))

        fahrzeuge = {z.fahrzeug.id: z.fahrzeug for z in zff if _im_monat(z.gueltig_ab, z.gueltig_bis, m)}
        fahrzeuge |= {z.fahrzeug.id: z.fahrzeug for z in zk
                      if z.fahrzeug is not None and _im_monat(z.gueltig_ab, z.gueltig_bis, m)}
        st.fahrzeuge_ohne = sorted((fz for fz in fahrzeuge.values() if fz.aktiv and fz.id not in fz_geladen[m]),
                                   key=lambda fz: fz.kennzeichen)
        personen = {z.fahrer.id: z.fahrer for z in zff if _im_monat(z.gueltig_ab, z.gueltig_bis, m)}
        personen |= {z.fahrer.id: z.fahrer for z in zk
                     if z.fahrer is not None and _im_monat(z.gueltig_ab, z.gueltig_bis, m)}
        st.fahrer_ohne = sorted((f for f in personen.values() if f.aktiv and f.id not in fahrer_geladen[m]),
                                key=lambda f: f.name)
        st.ohne_fahrzeug = ohne_fahrzeug[m]
        ergebnis.append(st)
    return Vollstaendigkeit(ergebnis)
