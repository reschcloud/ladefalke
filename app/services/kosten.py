"""Tatsächliche Kosten (SPEC Abschnitt 6 „Kosten und Umsatzsteuer“).

Tatsächliche Kosten = netto + USt. × (1 − Abzug). Abzug = Vorsteuer-% des Fahrzeugs (ohne Fahrzeug: Standard aus den
Einstellungen), aber nur bei inländischer USt. (Land der USt. = Land des Unternehmens); ausländische USt. zählt voll.

USt.: laut Rechnung (`ust_betrag`); Reverse Charge (`ust_land` „RC“): Satz des Unternehmenslands, abziehbar wie
inländische USt.; sonst geschätzt = netto × Normalsatz des Landes (Steuersatz-Tabelle) am Tag der
Ladung – Land der USt. bzw. des Ladevorgangs, sonst des Unternehmens. Private Ladungen (Kostenersatz, Wallbox-Importe
mit Strompreis) ohne USt.: der Betrag ist der tatsächlich gezahlte.

Ladevorgänge speichern das Ergebnis in `kosten` (für Summen, Filter, Sortierung): beim Speichern über ein Ereignis,
nach Änderungen der Einstellungen, Steuersätze oder des Vorsteuer-% eines Fahrzeugs über `neu_berechnen`. Gebühren
werden in der Auswertung berechnet (ihr Fahrzeug hängt von der Kartenzuordnung am Leistungsende ab).
"""
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from app.models import Fahrzeug, Gebuehr, Ladeart, Ladevorgang, Steuersatz
from app.parsers.basis import REVERSE_CHARGE, cent as _cent

# Felder, deren Änderung die Kosten eines Ladevorgangs beeinflusst
RELEVANT = ("kosten_netto", "ust_betrag", "ust_satz", "ust_land", "fahrzeug_id", "land", "ladeart", "start", "ende")


@dataclass
class Ergebnis:
    kosten: Decimal  # tatsächliche Kosten
    ust: Decimal  # USt. (laut Rechnung oder geschätzt)
    satz: Decimal | None  # USt.-Satz in Prozent
    land: str | None  # Land der USt.
    geschaetzt: bool  # USt. geschätzt (nicht laut Rechnung)
    abzug: Decimal  # angewandter Vorsteuerabzug in Prozent

    @property
    def geschaetzt_in_kosten(self) -> Decimal:
        """Geschätzte USt., soweit sie die Kosten erhöht (für die Kennzeichnung ≈)."""
        return _cent(self.ust * (1 - self.abzug / 100)) if self.geschaetzt else Decimal(0)


@dataclass
class Kontext:
    """Einmal geladene Grundlagen für viele Berechnungen."""
    firmenland: str
    standard_abzug: Decimal
    saetze: dict[str, list[tuple[date, Decimal]]]
    fahrzeuge: dict[int, Decimal] = field(default_factory=dict)  # Fahrzeug-ID → Vorsteuer-%

    def satz(self, land: str | None, tag: date) -> Decimal | None:
        liste = self.saetze.get(land or "") or self.saetze.get(self.firmenland) or []
        i = bisect_right([ab for ab, _ in liste], tag)
        return liste[i - 1][1] if i else (liste[0][1] if liste else None)

    def abzug(self, db: Session | None, fahrzeug_id: int | None) -> Decimal:
        if fahrzeug_id is None:
            return self.standard_abzug
        if fahrzeug_id not in self.fahrzeuge and db is not None:
            fz = db.get(Fahrzeug, fahrzeug_id)
            self.fahrzeuge[fahrzeug_id] = fz.vorsteuer_prozent if fz is not None else self.standard_abzug
        return self.fahrzeuge.get(fahrzeug_id, self.standard_abzug)


def kontext(db: Session) -> Kontext:
    from app.services.app_einstellungen import laden
    e = laden(db)
    saetze: dict[str, list[tuple[date, Decimal]]] = {}
    for s in db.scalars(select(Steuersatz).order_by(Steuersatz.land, Steuersatz.gueltig_ab)):
        saetze.setdefault(s.land, []).append((s.gueltig_ab, s.satz))
    fahrzeuge = dict(db.execute(select(Fahrzeug.id, Fahrzeug.vorsteuer_prozent)).all())
    return Kontext(e.firmenland, e.vorsteuer_standard, saetze, fahrzeuge)


def berechnen(k: Kontext, netto: Decimal, ust_betrag: Decimal | None, ust_satz: Decimal | None,
              ust_land: str | None, land: str | None, privat: bool, tag: date, abzug: Decimal) -> Ergebnis:
    """Tatsächliche Kosten eines Postens (siehe Moduldoku)."""
    netto = netto or Decimal(0)
    if ust_land == REVERSE_CHARGE:  # Steuer schuldet das Unternehmen: inländische USt., abziehbar je Fahrzeug
        ust_land, satz, geschaetzt = k.firmenland, k.satz(k.firmenland, tag), False
        ust = _cent(netto * satz / 100) if satz is not None else Decimal(0)
    elif ust_betrag is not None:
        ust, satz, ust_land, geschaetzt = ust_betrag, ust_satz, ust_land or k.firmenland, False
    elif privat:
        ust, satz, ust_land, geschaetzt = Decimal(0), None, None, False
    else:
        ust_land = ust_land or land or k.firmenland
        satz = k.satz(ust_land, tag)
        ust, geschaetzt = (_cent(netto * satz / 100) if satz is not None else Decimal(0)), satz is not None
    wirksam = abzug if ust_land == k.firmenland else Decimal(0)
    kosten = _cent(netto + ust * (1 - wirksam / 100))
    return Ergebnis(kosten, ust, satz, ust_land, geschaetzt and bool(ust), wirksam)


def fuer_vorgang(k: Kontext, v: Ladevorgang, db: Session | None = None) -> Ergebnis:
    return berechnen(k, v.kosten_netto, v.ust_betrag, v.ust_satz, v.ust_land, v.land, v.ladeart == Ladeart.privat,
                     (v.ende or v.start).date(), k.abzug(db, v.fahrzeug_id))


def fuer_gebuehr(k: Kontext, g: Gebuehr, fahrzeug_id: int | None, tag: date, privat: bool,
                 db: Session | None = None) -> Ergebnis:
    return berechnen(k, g.betrag_netto, g.ust_betrag, g.ust_satz, g.ust_land, None, privat, tag,
                     k.abzug(db, fahrzeug_id))


def neu_berechnen(db: Session, fahrzeug_id: int | None = None) -> int:
    """`kosten` aller Ladevorgänge (bzw. eines Fahrzeugs) neu berechnen; liefert die Zahl der geänderten."""
    db.flush()
    k = kontext(db)
    abfrage = select(Ladevorgang.id, Ladevorgang.kosten_netto, Ladevorgang.ust_betrag, Ladevorgang.ust_satz,
                     Ladevorgang.ust_land, Ladevorgang.land, Ladevorgang.ladeart, Ladevorgang.start, Ladevorgang.ende,
                     Ladevorgang.fahrzeug_id, Ladevorgang.kosten)
    if fahrzeug_id is not None:
        abfrage = abfrage.where(Ladevorgang.fahrzeug_id == fahrzeug_id)
    aenderungen = []
    for (id_, netto, ust, satz, ust_land, land, ladeart, start, ende, fz_id, alt) in db.execute(abfrage):
        neu = berechnen(k, netto, ust, satz, ust_land, land, ladeart == Ladeart.privat, (ende or start).date(),
                        k.abzug(db, fz_id)).kosten
        if neu != alt:
            aenderungen.append({"id": id_, "kosten": neu})
    for i in range(0, len(aenderungen), 1000):
        db.execute(update(Ladevorgang), aenderungen[i:i + 1000])
    return len(aenderungen)


def beim_start() -> None:
    """Fehlende Kosten berechnen (nach der Migration 0031)."""
    from app.db import SessionLocal
    with SessionLocal() as db:
        if db.scalar(select(Ladevorgang.id).where(Ladevorgang.kosten.is_(None)).limit(1)) is not None:
            neu_berechnen(db)
            db.commit()


@event.listens_for(Session, "before_flush")
def _beim_speichern(session: Session, _kontext, _instanzen) -> None:
    """Neue oder geänderte Ladevorgänge: tatsächliche Kosten berechnen."""
    from sqlalchemy import inspect
    offen = []
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Ladevorgang):
            continue
        zustand = inspect(obj)
        if obj in session.new or obj.kosten is None or any(
                zustand.attrs[f].history.has_changes() for f in RELEVANT):
            offen.append(obj)
    if not offen:
        return
    with session.no_autoflush:
        k = kontext(session)
        for v in offen:
            if v.kosten_netto is not None and (v.ende or v.start) is not None:
                v.kosten = fuer_vorgang(k, v, session).kosten
