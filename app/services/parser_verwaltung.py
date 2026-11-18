"""Verwaltung der Parser über die MSP: welche Parser aktiv sind, MSP für neue Parser anlegen, fehlende melden.

- Installiert = jedes Modul in app/parsers/ mit einer Parser-Klasse (app.parsers.PARSER, beim Start gefunden).
- Aktiv = installiert und nicht am MSP deaktiviert. Ein deaktivierter Parser erkennt keine Dateien, steht in keiner
  Auswahl und nicht in der Liste der Formate – die App tut, als gäbe es ihn nicht. Importierte Daten bleiben.
- Beim Start bekommt jeder installierte Parser ohne MSP einen (Name, Ladeart, Abrechnung je Karte aus dem Parser).
- Fehlend = ein MSP verweist auf einen Parser, dessen Modul nicht (mehr) installiert ist → Warnung in der MSP-Liste;
  ist er dort deaktiviert, lässt sich die Verknüpfung entfernen (der MSP mit allen Daten bleibt).
"""
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Ladeart, Msp
from app.parsers import PARSER, Parser, passende_parser

log = logging.getLogger(__name__)


def aktive(db: Session) -> dict[str, Parser]:
    aus = set(db.scalars(select(Msp.parser_key).where(Msp.parser_key.is_not(None), Msp.parser_aktiv.is_(False))))
    return {k: p for k, p in PARSER.items() if k not in aus}


def passende(db: Session, pfad: Path) -> list[str]:
    """Schlüssel der aktiven Parser, die die Datei erkennen."""
    aktiv = aktive(db)
    return [k for k in passende_parser(pfad) if k in aktiv]


def erkennen(db: Session, pfad: Path) -> str | None:
    """Schlüssel des aktiven Parsers, wenn genau einer passt; sonst None (dann wird gewählt, nicht geraten)."""
    passend = passende(db, pfad)
    return passend[0] if len(passend) == 1 else None


def formate(db: Session) -> str:
    return ", ".join(p.name for p in aktive(db).values())


def fehlend(msp: Msp) -> bool:
    return bool(msp.parser_key) and msp.parser_key not in PARSER


def abgleichen(db: Session) -> list[str]:
    """Für jeden installierten Parser ohne MSP einen anlegen; liefert die Namen der neuen MSP."""
    vorhanden = set(db.scalars(select(Msp.parser_key).where(Msp.parser_key.is_not(None))))
    neu = []
    for key, p in PARSER.items():
        if key in vorhanden:
            continue
        name = p.msp_name or p.name
        gleichnamig = db.scalar(select(Msp).where(func.lower(Msp.name) == name.lower()))
        if gleichnamig is not None and gleichnamig.parser_key is None:  # von Hand angelegt: verknüpfen
            gleichnamig.parser_key = key
            continue
        if gleichnamig is not None:
            name = f"{name} ({key})"
        db.add(Msp(name=name, parser_key=key, standard_ladeart=Ladeart(p.standard_ladeart),
                   abrechnung_je_karte=p.abrechnung_je_karte))
        neu.append(name)
    db.flush()
    return neu


def beim_start() -> None:
    """Beim Start der App: MSP für neue Parser anlegen. Fehler (z. B. Datenbank noch nicht migriert) blockieren
    den Start nicht."""
    from app.db import SessionLocal
    try:
        with SessionLocal() as db:
            if neu := abgleichen(db):
                log.info("MSP für neue Parser angelegt: %s", ", ".join(neu))
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("MSP-Abgleich mit den installierten Parsern fehlgeschlagen")
