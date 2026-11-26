"""Kennzahlen über den gesamten Zeitraum für Detailseiten (Fahrzeug, Fahrer:in, Ladekarte) – direkt in der Datenbank summiert,
ohne die ganze Auswertung zu rechnen. Nur Ladevorgänge (ohne Gebühren und Pauschalen), Beträge netto."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models import Ladeart, Ladevorgang
from app.services.aufloesung import personen_filter, stichtag_spalte


@dataclass
class Gesamt:
    anzahl: int
    kwh: Decimal
    kosten: Decimal
    kwh_oeffentlich: Decimal
    geschaetzt: bool  # mindestens ein Teil der kWh geschätzt
    erste: datetime | None
    letzte: datetime | None
    dc_minuten: int | None = None  # Ø Ladedauer DC (angesteckt), nur DC-Ladungen mit kWh
    dc_anzahl: int = 0

    @property
    def preis(self) -> Decimal | None:
        return self.kosten / self.kwh if self.kwh else None

    @property
    def anteil_oeffentlich(self) -> Decimal | None:
        return self.kwh_oeffentlich / self.kwh if self.kwh else None


def _summen(db: Session, *bedingungen) -> Gesamt:
    spalte = stichtag_spalte()
    oeffentlich = case((Ladevorgang.ladeart == Ladeart.oeffentlich, Ladevorgang.kwh), else_=0)
    ist_dc = and_(Ladevorgang.stromart == "DC", Ladevorgang.kwh > 0)
    dc_dauer = case((ist_dc, func.extract("epoch", Ladevorgang.ende - Ladevorgang.start)), else_=None)
    anzahl, kwh, kosten, kwh_oeff, geschaetzt, erste, letzte, dc_schnitt, dc_anzahl = db.execute(select(
        func.count(), func.coalesce(func.sum(Ladevorgang.kwh), 0), func.coalesce(func.sum(Ladevorgang.kosten), 0),
        func.coalesce(func.sum(oeffentlich), 0), func.bool_or(Ladevorgang.kwh_geschaetzt), func.min(spalte),
        func.max(spalte), func.avg(dc_dauer), func.count(dc_dauer)).where(*bedingungen)).one()
    return Gesamt(anzahl, Decimal(kwh), Decimal(kosten), Decimal(kwh_oeff), bool(geschaetzt), erste, letzte,
                  round(float(dc_schnitt) / 60) if dc_schnitt is not None else None, dc_anzahl)


def ladekarte(db: Session, karte_id: int) -> Gesamt:
    return _summen(db, Ladevorgang.ladekarte_id == karte_id)


def erster_und_letzter(db: Session, *bedingungen) -> tuple[Ladevorgang | None, Ladevorgang | None]:
    """Erster und letzter Ladevorgang (nach Stichtag, wie die Auswertungen) – für Links auf der Detailseite."""
    spalte = stichtag_spalte()
    q = select(Ladevorgang).where(*bedingungen)
    return (db.scalars(q.order_by(spalte, Ladevorgang.id).limit(1)).first(),
            db.scalars(q.order_by(spalte.desc(), Ladevorgang.id.desc()).limit(1)).first())


def letzte_je_karte(db: Session) -> dict[int, datetime]:
    """Letzter Ladevorgang (Stichtag) je Ladekarte – für die Kartenliste."""
    spalte = stichtag_spalte()
    return dict(db.execute(select(Ladevorgang.ladekarte_id, func.max(spalte))
                           .where(Ladevorgang.ladekarte_id.is_not(None)).group_by(Ladevorgang.ladekarte_id)).all())


def fahrzeug(db: Session, fahrzeug_id: int) -> Gesamt:
    return _summen(db, Ladevorgang.fahrzeug_id == fahrzeug_id)


def fahrer(db: Session, fahrer_id: int) -> Gesamt:
    """Ladungen, bei denen die Person gefahren ist (Regel wie überall: Karte der Person, sonst Fahrer:in des
    Fahrzeugs am Stichtag; Poolfahrzeug zählt für jede)."""
    return _summen(db, personen_filter([fahrer_id]))


# ------------------------------------------------------------------ Ø Ladedauer DC (Dashboard, Auswertungen, Reports)

@dataclass
class LadedauerDc:
    """Ø Dauer (Minuten, angesteckt: Start bis Ende) der DC-Ladungen mit kWh – `eigen` für die Auswahl (Flotte,
    Fahrzeug oder Person), `flotte` zum Vergleich (nur bei Fahrzeug/Person), `vorher` Ø der Vorperioden."""
    eigen: int | None
    anzahl: int
    flotte: int | None = None
    vorher: int | None = None
    vorher_text: str | None = None  # z. B. „07/2026 – 09/2026“

    def __getitem__(self, schluessel):  # Reports greifen wie bisher mit dc.eigen / dc["eigen"] zu
        return getattr(self, schluessel)


def _dc(db: Session, von_bis, *bedingungen) -> tuple[int | None, int]:
    from app.services.auswertung import zeitbedingung
    spalte = stichtag_spalte()
    dauer = func.avg(func.extract("epoch", Ladevorgang.ende - Ladevorgang.start))
    schnitt, anzahl = db.execute(select(dauer, func.count()).where(
        Ladevorgang.stromart == "DC", Ladevorgang.kwh > 0, zeitbedingung(spalte, von_bis), *bedingungen)).one()
    return (round(float(schnitt) / 60) if schnitt is not None else None), anzahl


def ladedauer_dc(db: Session, z, fahrer_id: int | None = None,
                 fahrzeug_id: int | None = None, vorperioden: int = 0) -> LadedauerDc:
    """Ø DC-Ladedauer im Zeitraum `z` – der Flotte, eines Fahrzeugs oder einer Person (dann mit Flottenschnitt).
    `vorperioden`: zusätzlich der Ø über so viele Vorperioden (wie der Vergleich im Dashboard)."""
    from app.services.auswertung import zeitraum_frei
    bedingungen = []
    if fahrer_id is not None:
        bedingungen.append(personen_filter([fahrer_id]))
    if fahrzeug_id is not None:
        bedingungen.append(Ladevorgang.fahrzeug_id == fahrzeug_id)
    eigen, anzahl = _dc(db, z, *bedingungen)
    ergebnis = LadedauerDc(eigen, anzahl)
    if bedingungen:
        ergebnis.flotte = _dc(db, z)[0]
    if vorperioden and z.art != "alles":
        erste, letzte = z.verschoben(-vorperioden), z.verschoben(-1)
        ergebnis.vorher = _dc(db, zeitraum_frei(erste.von, letzte.bis), *bedingungen)[0]
        ergebnis.vorher_text = erste.label if vorperioden == 1 else f"{erste.label} – {letzte.label}"
    return ergebnis
