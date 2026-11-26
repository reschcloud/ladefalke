"""Abfragen rund um gespeicherte Ladevorgänge (Detailseiten von Ladekarte, Fahrzeug und Fahrer:in)."""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Fahrer, Ladevorgang, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services.aufloesung import fahrer_beim_laden, stichtag, stichtag_spalte

ANZAHL = 15  # „Letzte Ladevorgänge“ auf Detailseiten; alle über den Link zur gefilterten Liste


def _mit_fahrer(db: Session, abfrage):
    """Ladevorgänge der Abfrage (neueste zuerst) jeweils mit den Fahrer:innen zum Zeitpunkt des Ladevorgangs."""
    cache: dict[tuple, list[Fahrer]] = {}
    for v in db.scalars(abfrage.options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.fahrzeug))
                        .order_by(stichtag_spalte().desc())):
        tag = stichtag(v.start)
        schluessel = (v.ladekarte_id, v.fahrzeug_id, tag)
        if schluessel not in cache:
            cache[schluessel] = fahrer_beim_laden(db, v.ladekarte, v.fahrzeug, tag)
        yield v, cache[schluessel]


def letzte_vorgaenge(db: Session, *bedingungen, anzahl: int = ANZAHL) -> list[tuple[Ladevorgang, list[Fahrer]]]:
    """Neueste Ladevorgänge (nach Stichtag) samt Fahrer:in(nen) zum Zeitpunkt des Ladevorgangs."""
    return _mit_karten(db, list(_mit_fahrer(db, select(Ladevorgang).where(*bedingungen).limit(anzahl))))


def letzte_vorgaenge_von_fahrer(db: Session, fahrer_id: int,
                                anzahl: int = ANZAHL) -> list[tuple[Ladevorgang, list[Fahrer]]]:
    """Neueste Ladevorgänge, bei denen diese Person zum Ladezeitpunkt gefahren ist.

    Kandidaten: Vorgänge mit Karten, die ihr je zugeordnet waren, und Vorgänge von Fahrzeugen, die sie je
    gefahren hat; entschieden wird je Vorgang über `fahrer_beim_laden` (Stichtag Ladebeginn).
    """
    karten = select(ZuordnungKarte.ladekarte_id).where(ZuordnungKarte.fahrer_id == fahrer_id)
    fahrzeuge = select(ZuordnungFahrerFahrzeug.fahrzeug_id).where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer_id)
    abfrage = select(Ladevorgang).where(or_(Ladevorgang.ladekarte_id.in_(karten),
                                            Ladevorgang.fahrzeug_id.in_(fahrzeuge)))
    ergebnis = []
    for v, fahrer in _mit_fahrer(db, abfrage):
        if any(f.id == fahrer_id for f in fahrer):
            ergebnis.append((v, fahrer))
            if len(ergebnis) >= anzahl:
                break
    return _mit_karten(db, ergebnis)


def _mit_karten(db: Session, zeilen: list) -> list:
    """Anzeige der Karte (Person/Fahrzeug am Ladetag) an jedem Vorgang setzen (services/kartenanzeige.py)."""
    from app.services import kartenanzeige
    kartenanzeige.fuer_vorgaenge(db, [v for v, _ in zeilen])
    return zeilen
