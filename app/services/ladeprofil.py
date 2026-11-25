"""Tatsächliches Ladeprofil je Fahrzeug aus den Daten, getrennt nach AC und DC, über den gesamten Datenzeitraum.

- Nur Vorgänge mit AC/DC-Angabe (Rechnung oder manuell korrigiert), gemessenen kWh (nicht geschätzt),
  kWh > 0 und Ende nach Start; ohne ausdrücklich ausgenommene und ohne unplausible Vorgänge.
- Ø Lademenge = Σ kWh / Anzahl Vorgänge.
- Ø Ladeleistung = Σ kWh / Σ angesteckte Stunden (gewichtet; „theoretische“ Leistung über den Vorgang).
- 95. Perzentil der kWh je Vorgang (Obergrenze der kWh-Schätzung).
Daraus und aus dem Flottenprofil entsteht die Grundlage der kWh-Schätzung (services/schaetzung.py).

Plausibilität (Leistung = kWh / angesteckte Dauer – eine Untergrenze der echten Ladeleistung): Grenzen aus den
Einstellungen, Standard AC ab 12 kW sowie DC unter 10 kW oder ab 1.000 kW unplausibel (z. B. falsch deklariertes AC).
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, and_, cast, extract, false, func, not_, or_, select
from sqlalchemy.orm import Session

from app.formatierung import zahl
from app.models import Ladevorgang
from app.services.app_einstellungen import AppEinstellungen
from app.services.app_einstellungen import laden as einstellungen_laden
from app.services.schaetzung import MINDESTANZAHL, Schaetzwert, vorgabe

STROMARTEN = ("AC", "DC")


@dataclass(frozen=True)
class Profil:
    anzahl: int
    kwh: Decimal
    stunden: Decimal
    von: datetime
    bis: datetime
    kwh_p95: Decimal  # 95. Perzentil der kWh je Vorgang

    @property
    def kwh_schnitt(self) -> Decimal:
        return self.kwh / self.anzahl

    @property
    def leistung(self) -> Decimal | None:
        return self.kwh / self.stunden if self.stunden else None


# ------------------------------------------------------------------ Plausibilität

def leistung(v: Ladevorgang) -> Decimal | None:
    """Theoretische Ladeleistung in kW (kWh / angesteckte Dauer); None ohne gemessene kWh oder Dauer."""
    from app.services.stromart import leistung_kw
    return leistung_kw(v.kwh, v.kwh_geschaetzt, v.start, v.ende)


def unplausibel_grund(v: Ladevorgang, e: AppEinstellungen) -> str | None:
    """Begründung, wenn AC/DC-Angabe und Leistung nicht zusammenpassen (unabhängig von „ausgenommen“)."""
    kw = leistung(v)
    if kw is None or v.stromart not in STROMARTEN:
        return None
    if v.stromart == "AC" and kw >= e.plausibel_ac_max_kw:
        return f"AC mit {zahl(kw, 1)} kW – plausibel nur unter {zahl(e.plausibel_ac_max_kw, 0)} kW"
    if v.stromart == "DC" and kw < e.plausibel_dc_min_kw:
        return f"DC mit nur {zahl(kw, 1)} kW – plausibel erst ab {zahl(e.plausibel_dc_min_kw, 0)} kW"
    if v.stromart == "DC" and kw >= e.plausibel_dc_max_kw:
        return f"DC mit {zahl(kw, 0)} kW – plausibel nur unter {zahl(e.plausibel_dc_max_kw, 0)} kW"
    return None


def _auswertbar():
    return and_(Ladevorgang.kwh_geschaetzt == false(), Ladevorgang.kwh > 0, Ladevorgang.ende > Ladevorgang.start)


def _leistung_sql():
    return Ladevorgang.kwh / (extract("epoch", Ladevorgang.ende - Ladevorgang.start) / 3600)


def plausibel_sql(e: AppEinstellungen):
    kw = _leistung_sql()
    return or_(and_(Ladevorgang.stromart == "AC", kw < e.plausibel_ac_max_kw),
               and_(Ladevorgang.stromart == "DC", kw >= e.plausibel_dc_min_kw, kw < e.plausibel_dc_max_kw))


def unplausibel_sql(e: AppEinstellungen):
    """Markierte Vorgänge: unplausibel und nicht ausdrücklich aus dem Ladeprofil ausgenommen."""
    return and_(_auswertbar(), Ladevorgang.stromart.in_(STROMARTEN), Ladevorgang.profil_ausgenommen == false(),
                not_(plausibel_sql(e)))


# ------------------------------------------------------------------ Ladeprofil

def _profil_abfrage(db: Session, *gruppen):
    """Kennzahlen je Gruppe (Gruppenspalten zuerst), nur Vorgänge, die ins Ladeprofil einfließen."""
    e = einstellungen_laden(db)
    tag = Ladevorgang.start if e.stichtag == "beginn" else Ladevorgang.ende  # Zeitraum der Vorgänge laut Stichtag
    return (select(*gruppen, func.count(), func.sum(Ladevorgang.kwh),
                   func.sum(extract("epoch", Ladevorgang.ende - Ladevorgang.start)) / 3600,
                   func.min(tag), func.max(tag),
                   cast(func.percentile_cont(0.95).within_group(Ladevorgang.kwh), Numeric(12, 3)))
            .where(Ladevorgang.fahrzeug_id.is_not(None), Ladevorgang.stromart.in_(STROMARTEN), _auswertbar(),
                   Ladevorgang.profil_ausgenommen == false(), plausibel_sql(e))
            .group_by(*gruppen))


def _profil(anzahl, kwh, stunden, von, bis, p95) -> Profil:
    return Profil(anzahl, Decimal(kwh), Decimal(stunden), von, bis, Decimal(p95))


def ladeprofile(db: Session, fahrzeug_id: int | None = None) -> dict[int, dict[str, Profil]]:
    """{Fahrzeug-ID: {"AC": Profil, "DC": Profil}} – fehlt eine Stromart, fehlt der Schlüssel."""
    q = _profil_abfrage(db, Ladevorgang.fahrzeug_id, Ladevorgang.stromart)
    if fahrzeug_id is not None:
        q = q.where(Ladevorgang.fahrzeug_id == fahrzeug_id)
    ergebnis: dict[int, dict[str, Profil]] = {}
    for fz_id, art, *werte in db.execute(q):
        ergebnis.setdefault(fz_id, {})[art] = _profil(*werte)
    return ergebnis


def flottenprofil(db: Session) -> dict[str, Profil]:
    """Ladeprofil über alle Fahrzeuge, je Stromart."""
    return {art: _profil(*werte) for art, *werte in db.execute(_profil_abfrage(db, Ladevorgang.stromart))}


# ------------------------------------------------------------------ Grundlage der kWh-Schätzung

def _schaetzwert(p: Profil | None, quelle: str) -> Schaetzwert | None:
    if p is None or p.anzahl < MINDESTANZAHL or not p.leistung:
        return None
    eine_stelle = Decimal("0.1")
    return Schaetzwert(p.leistung.quantize(eine_stelle), p.kwh_p95.quantize(eine_stelle), quelle, p.anzahl)


@dataclass
class Schaetzgrundlage:
    fahrzeuge: dict[int, dict[str, Profil]] = field(default_factory=dict)
    flotte: dict[str, Profil] = field(default_factory=dict)
    vorgaben: dict[str, Schaetzwert] = field(default_factory=lambda: {art: vorgabe(art) for art in STROMARTEN})

    def fuer(self, fahrzeug_id: int | None) -> dict[str, Schaetzwert]:
        """Schätzwerte je Stromart: eigene Daten, sonst Flottenschnitt, sonst Vorgabe."""
        eigene = self.fahrzeuge.get(fahrzeug_id, {}) if fahrzeug_id is not None else {}
        return {art: _schaetzwert(eigene.get(art), "fahrzeug") or _schaetzwert(self.flotte.get(art), "flotte")
                or self.vorgaben[art] for art in STROMARTEN}


def schaetzgrundlage(db: Session) -> Schaetzgrundlage:
    e = einstellungen_laden(db)
    vorgaben = {"AC": Schaetzwert(e.schaetzung_ac_kw, e.schaetzung_obergrenze_kwh),
                "DC": Schaetzwert(e.schaetzung_dc_kw, e.schaetzung_obergrenze_kwh)}
    return Schaetzgrundlage(ladeprofile(db), flottenprofil(db), vorgaben)


def ohne_angabe(db: Session, fahrzeug_id: int) -> int:
    """Vorgänge mit gemessenen kWh, aber ohne AC/DC-Angabe (fließen nicht in das Profil ein)."""
    return db.scalar(select(func.count()).select_from(Ladevorgang).where(
        Ladevorgang.fahrzeug_id == fahrzeug_id, Ladevorgang.stromart.is_(None), _auswertbar()))


def unplausible_anzahl(db: Session, fahrzeug_id: int | None = None) -> int:
    bed = [unplausibel_sql(einstellungen_laden(db))] + ([Ladevorgang.fahrzeug_id == fahrzeug_id] if fahrzeug_id is not None else [])
    return db.scalar(select(func.count()).select_from(Ladevorgang).where(*bed))
