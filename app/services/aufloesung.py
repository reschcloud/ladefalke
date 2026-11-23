"""Fahrzeugauflösung je Ladevorgang (SPEC Abschnitt 5).

Karte → Fahrzeug direkt oder Karte → Fahrer:in → Fahrzeug, jeweils gültig am Stichtag.
Das Ergebnis wird am Ladevorgang gespeichert; spätere Zuordnungsänderungen wirken nur,
wenn ausdrücklich neu zugeordnet wird. Manuelle Zuordnungen werden nie überschrieben.
Geschätzte kWh werden beim Commit mit der neuen Zuordnung neu berechnet (services/neuschaetzung.py).
"""
from datetime import date, datetime, timedelta

from sqlalchemy import Date, and_, cast, exists, not_, or_, select
from sqlalchemy.orm import Session

from app.models import (Fahrer, Fahrzeug, Ladekarte, Ladevorgang, ZuordnungFahrerFahrzeug, ZuordnungKarte,
                        ZuordnungQuelle)
from app.services.zuordnung import (fahrer_von_fahrzeug_am, fahrzeug_von_fahrer_am, gueltig_am,
                                    karten_zuordnung_am)


def stichtag_art(db: Session) -> str:
    """„ende“ (Standard) oder „beginn“ laut Einstellungen – gilt für Zuordnung, Auswertung, Filter und Sortierung."""
    from app.services.app_einstellungen import laden  # hier: app_einstellungen importiert models, nicht umgekehrt
    return laden(db).stichtag


def stichtag(start: datetime, ende: datetime, art: str = "ende") -> date:
    """Stichtag eines Ladevorgangs: Datum von Ladeende bzw. Ladebeginn (`art` aus `stichtag_art`)."""
    return (start if art == "beginn" else ende).date()


def stichtag_spalte(art: str = "ende"):
    """Spalte des Stichtags für SQL (Filter, Sortierung, Zeiträume)."""
    return Ladevorgang.start if art == "beginn" else Ladevorgang.ende


def fahrzeug_fuer_karte(db: Session, karte: Ladekarte | None, tag: date) -> Fahrzeug | None:
    if karte is None or karte.ist_gastkarte:
        return None
    z = karten_zuordnung_am(db, karte.id, tag)
    if z is None:
        return None
    if z.fahrzeug_id is not None:
        return z.fahrzeug
    return fahrzeug_von_fahrer_am(db, z.fahrer_id, tag)


def fahrer_beim_laden(db: Session, karte: Ladekarte | None, fahrzeug: Fahrzeug | None, tag: date) -> list[Fahrer]:
    """Wer am Stichtag geladen hat: bei personengebundener Karte die Person der Karte (auch bei
    Poolfahrzeugen eindeutig), sonst die Fahrer:innen des Fahrzeugs an diesem Tag."""
    if karte is not None and not karte.ist_gastkarte:
        z = karten_zuordnung_am(db, karte.id, tag)
        if z is not None and z.fahrer is not None:
            return [z.fahrer]
    if fahrzeug is None:
        return []
    return fahrer_von_fahrzeug_am(db, fahrzeug.id, tag)


def personen_beim_laden(db: Session, vorgaenge) -> dict[int, list[Fahrer] | None]:
    """Person(en) je Ladevorgang für Listen, Regel wie `fahrer_beim_laden`: None = Gastkarte, [] = unbekannt,
    mehrere = Poolfahrzeug ohne personengebundene Karte."""
    cache: dict[tuple, list[Fahrer]] = {}
    ergebnis: dict[int, list[Fahrer] | None] = {}
    art = stichtag_art(db)
    for v in vorgaenge:
        if v.ladekarte is not None and v.ladekarte.ist_gastkarte:
            ergebnis[v.id] = None
            continue
        schluessel = (v.ladekarte_id, v.fahrzeug_id, stichtag(v.start, v.ende, art))
        if schluessel not in cache:
            cache[schluessel] = fahrer_beim_laden(db, v.ladekarte, v.fahrzeug, schluessel[2])
        ergebnis[v.id] = cache[schluessel]
    return ergebnis


def personen_filter(fahrer_ids, gaeste: bool = False, ohne: bool = False, art: str = "ende"):
    """SQL-Bedingung auf Ladevorgang nach Person beim Laden – gleiche Regel wie `fahrer_beim_laden`
    (Stichtag laut `art`): personengebundene Karte → deren Person, sonst die Fahrer:innen des Fahrzeugs (Pool: jede).
    `fahrer_ids`: Liste von IDs oder Unterabfrage (z. B. Namenssuche); `gaeste`: Gastkarten; `ohne`: keine Person
    ermittelbar. None, wenn nichts gewählt ist."""
    tag = cast(stichtag_spalte(art), Date)
    gast = Ladevorgang.ladekarte.has(Ladekarte.ist_gastkarte)
    kein_gast = not_(gast)

    def karte_person(*bedingung):
        return exists().where(ZuordnungKarte.ladekarte_id == Ladevorgang.ladekarte_id, gueltig_am(ZuordnungKarte, tag),
                              ZuordnungKarte.fahrer_id.is_not(None), *bedingung)

    def fahrzeug_person(*bedingung):
        return exists().where(ZuordnungFahrerFahrzeug.fahrzeug_id == Ladevorgang.fahrzeug_id,
                              gueltig_am(ZuordnungFahrerFahrzeug, tag), *bedingung)

    teile = []
    if not isinstance(fahrer_ids, list) or fahrer_ids:
        teile.append(and_(kein_gast, or_(
            karte_person(ZuordnungKarte.fahrer_id.in_(fahrer_ids)),
            and_(not_(karte_person()), fahrzeug_person(ZuordnungFahrerFahrzeug.fahrer_id.in_(fahrer_ids))))))
    if gaeste:
        teile.append(gast)
    if ohne:
        teile.append(and_(kein_gast, not_(karte_person()), not_(fahrzeug_person())))
    return or_(*teile) if teile else None


def person_text(personen: list[Fahrer] | None) -> str:
    """Für Exporte: Name, „Gäste“, „Pool: A, B“ oder leer."""
    if personen is None:
        return "Gäste"
    if len(personen) > 1:
        return "Pool: " + ", ".join(p.name for p in personen)
    return personen[0].name if personen else ""


def fahrzeug_setzen(v: Ladevorgang, fahrzeug: Fahrzeug | None, quelle: ZuordnungQuelle) -> None:
    v.fahrzeug_id = fahrzeug.id if fahrzeug else None
    v.fahrzeug = fahrzeug
    v.zuordnung_quelle = quelle


def vorgang_aktualisieren(db: Session, v: Ladevorgang, art: str | None = None) -> bool:
    """Fahrzeug automatisch neu ermitteln (außer bei manueller Zuordnung). True, wenn sich etwas geändert hat."""
    if v.zuordnung_quelle == ZuordnungQuelle.manuell:
        return False
    vorher = (v.fahrzeug_id, v.zuordnung_quelle)
    fahrzeug = fahrzeug_fuer_karte(db, v.ladekarte, stichtag(v.start, v.ende, art or stichtag_art(db)))
    fahrzeug_setzen(v, fahrzeug, ZuordnungQuelle.automatisch if fahrzeug else ZuordnungQuelle.keine)
    return (v.fahrzeug_id, v.zuordnung_quelle) != vorher


def stichtag_festlegen() -> None:
    """Beim Start: Der Stichtag ist fest Ladebeginn (früher wählbar). War „Ladeende“ gespeichert, einmal alle
    automatisch zugeordneten Ladevorgänge am Ladebeginn neu zuordnen (manuelle bleiben) und umstellen."""
    from app.db import SessionLocal
    from app.models import Einstellung
    with SessionLocal() as db:
        eintrag = db.get(Einstellung, "stichtag")
        if eintrag is None or eintrag.wert == "beginn":
            return
        anzahl = neu_zuordnen(db)
        eintrag.wert = "beginn"
        db.commit()
        import logging
        logging.getLogger(__name__).info("Stichtag auf Ladebeginn umgestellt, %s Ladevorgänge neu zugeordnet", anzahl)


def neu_zuordnen(db: Session, *bedingungen) -> int:
    """Alle Ladevorgänge, die die Bedingungen erfüllen, neu auflösen. Liefert die Anzahl geänderter Vorgänge.
    Im Änderungsprotokoll ein Sammeleintrag statt eines Eintrags je Vorgang."""
    from app.services import protokoll
    with protokoll.still(db, Ladevorgang):
        art = stichtag_art(db)
        anzahl = sum(vorgang_aktualisieren(db, v, art) for v in db.scalars(select(Ladevorgang).where(*bedingungen)))
    if anzahl:
        protokoll.eintragen(db, "geaendert", "ladevorgang", f"{anzahl} Ladevorgänge automatisch neu zugeordnet",
                            felder={"Fahrzeug": [None, "neu ermittelt"]})
    return anzahl


def im_zeitraum(von: date, bis: date | None, art: str = "ende"):
    """Bedingungen: Stichtag liegt im Zeitraum [von, bis] (bis inklusive, None = offen)."""
    spalte = stichtag_spalte(art)
    bedingungen = [spalte >= datetime.combine(von, datetime.min.time())]
    if bis is not None:
        bedingungen.append(spalte < datetime.combine(bis + timedelta(days=1), datetime.min.time()))
    return bedingungen


def zeitraum_vereinigen(a: tuple[date, date | None], b: tuple[date, date | None]) -> tuple[date, date | None]:
    """Kleinster Zeitraum, der beide umfasst (für Änderungen: alter und neuer Zeitraum)."""
    bis = None if a[1] is None or b[1] is None else max(a[1], b[1])
    return min(a[0], b[0]), bis


def neu_zuordnen_karte(db: Session, karte_id: int, von: date, bis: date | None) -> int:
    return neu_zuordnen(db, Ladevorgang.ladekarte_id == karte_id, *im_zeitraum(von, bis, stichtag_art(db)))


def neu_zuordnen_fahrer(db: Session, fahrer_id: int, von: date, bis: date | None) -> int:
    """Vorgänge aller Karten, die (irgendwann) dieser Person zugeordnet waren – die Auflösung entscheidet je Tag."""
    karten = select(ZuordnungKarte.ladekarte_id).where(ZuordnungKarte.fahrer_id == fahrer_id)
    return neu_zuordnen(db, Ladevorgang.ladekarte_id.in_(karten), *im_zeitraum(von, bis, stichtag_art(db)))

