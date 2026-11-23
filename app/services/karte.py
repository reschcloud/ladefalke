"""Kartendaten (Raumbezug Phase 4): Ladevorgänge je Ladeort.

- Dashboard: reine Heatmap der öffentlichen Ladevorgänge im Zeitraum (`berechnen`); zeitliche Zuordnung wie in den
  Auswertungen (Stichtag Ladeende bzw. Ladebeginn laut Einstellungen).
- Ladevorgänge: interaktive Karte mit Symbolen je Ort und Stromart (`ladungen`: jede Ladung mit den Angaben für
  Symbol und Tooltip); die Liste darunter folgt dem Kartenausschnitt (`im_bereich`).
Nur Ladeorte mit Koordinaten aus PLZ, Ortsname oder manueller Angabe; Vorgänge ohne Ladeort oder nur mit Landesmitte
zählen als „ohne genauen Ort“.
"""
from dataclasses import dataclass, field

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Ladeart, Ladeort, Ladevorgang
from app.services.app_einstellungen import AppEinstellungen
from app.services.aufloesung import personen_beim_laden, stichtag, stichtag_art, stichtag_spalte
from app.services.auswertung import Zeitraum, zeitbedingung
from app.services.evse import landname

GENAU = ("plz", "ort", "manuell")


@dataclass
class Punkt:
    ladeort_id: int
    lat: float  # nur für die Darstellung
    lon: float
    anzahl: int
    adresse: str
    land: str | None


@dataclass
class Karte:
    punkte: list[Punkt] = field(default_factory=list)
    ohne_ort: int = 0  # Vorgänge ohne genauen Ort

    @property
    def anzahl(self) -> int:
        return sum(p.anzahl for p in self.punkte)

    def als_json(self) -> list[list]:
        return [[p.lat, p.lon, p.anzahl] for p in self.punkte]


def _person(personen) -> str:
    """Anzeige je Vorgang: Name, „Pool“, „Gäste“ oder „ohne Person“."""
    if personen is None:
        return "Gäste"
    if len(personen) > 1:
        return "Pool"
    return personen[0].name if personen else "ohne Person"


def _ort_text(o: Ladeort) -> str:
    return o.ort or (landname(o.land) if o.land else "unbekannter Ort")


def _verortet():
    return and_(Ladeort.genauigkeit.in_(GENAU), Ladeort.lat.is_not(None))


def ohne_ort_anzahl(db: Session, bedingungen: list) -> int:
    return db.scalar(select(func.count()).select_from(Ladevorgang).where(
        *bedingungen, not_(Ladevorgang.ladeort.has(_verortet())))) or 0


def im_bereich(bereich: str):
    """„süd,west,nord,ost“ → Bedingung: Ladeort im Ausschnitt oder ohne genauen Ort (die stehen immer in der Liste).
    None bei ungültiger Angabe."""
    try:
        sued, west, nord, ost = (float(x) for x in bereich.split(","))
    except ValueError:
        return None
    return or_(Ladevorgang.ladeort.has(and_(_verortet(), Ladeort.lat.between(sued, nord), Ladeort.lon.between(west, ost))),
               not_(Ladevorgang.ladeort.has(_verortet())))


def ladungen(db: Session, bedingungen: list) -> list[list]:
    """Verortete Ladungen für die Karte bei den Ladevorgängen, je Ladung
    [id, lat, lon, land, ort, stromart, kwh, kosten, kwh_geschaetzt, datum, cpo, person, kennzeichen, dauer_min]."""
    art = stichtag_art(db)
    vorgaenge = db.scalars(
        select(Ladevorgang).where(*bedingungen, Ladevorgang.ladeort.has(_verortet()))
        .options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.fahrzeug),
                 selectinload(Ladevorgang.ladeort))
        .order_by(stichtag_spalte(art).desc())).all()
    personen = personen_beim_laden(db, vorgaenge)
    return [[v.id, float(v.ladeort.lat), float(v.ladeort.lon), v.ladeort.land, _ort_text(v.ladeort), v.stromart,
             float(v.kwh), float(v.kosten if v.kosten is not None else v.kosten_netto), v.kwh_geschaetzt, stichtag(v.start, v.ende, art).strftime("%d.%m.%y"),
             v.betreiber_id or "", _person(personen[v.id]), v.fahrzeug.kennzeichen if v.fahrzeug else "",
             max(0, round((v.ende - v.start).total_seconds() / 60))] for v in vorgaenge]


def preis_schnitt(ladungen: list[list]) -> list[tuple[str, float]]:
    """Ø €/kWh (Kosten/kWh) je Stromart über die Ladungen der Karte – wie in karte.js für die Farbe nach Preis."""
    ergebnis = []
    for art, text in (("AC", "AC"), ("DC", "DC"), (None, "ohne Angabe")):
        kwh = sum(v[6] for v in ladungen if v[5] == art)
        if kwh > 0:
            ergebnis.append((text, sum(v[7] for v in ladungen if v[5] == art) / kwh))
    return ergebnis


def aus_bedingungen(db: Session, bedingungen: list) -> Karte:
    """Heatmap-Punkte (Anzahl je Ladeort) für beliebige Filterbedingungen auf Ladevorgang."""
    zeilen = db.execute(
        select(Ladeort, func.count(Ladevorgang.id))
        .join(Ladevorgang, Ladevorgang.ladeort_id == Ladeort.id)
        .where(*bedingungen, Ladeort.genauigkeit.in_(GENAU), Ladeort.lat.is_not(None))
        .group_by(Ladeort.id)).all()
    ohne = db.scalar(select(func.count()).select_from(Ladevorgang)
                     .outerjoin(Ladeort, Ladevorgang.ladeort_id == Ladeort.id)
                     .where(*bedingungen, or_(Ladeort.id.is_(None), Ladeort.genauigkeit.is_(None),
                                              Ladeort.genauigkeit.not_in(GENAU), Ladeort.lat.is_(None))))
    punkte = [Punkt(o.id, float(o.lat), float(o.lon), n, o.adresse, o.land) for o, n in zeilen]
    return Karte(punkte, ohne or 0)


def berechnen(db: Session, z: Zeitraum, einst: AppEinstellungen) -> Karte:
    """Dashboard: öffentliche Ladevorgänge im Zeitraum."""
    spalte = Ladevorgang.start if einst.stichtag == "beginn" else Ladevorgang.ende
    return aus_bedingungen(db, [and_(Ladevorgang.ladeart == Ladeart.oeffentlich, zeitbedingung(spalte, z))])
