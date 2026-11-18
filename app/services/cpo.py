"""CPO (Betreiber der Ladestation) je Ladevorgang: aus der EVSE-ID der Rechnung oder als Vorgabe des MSP.

Die Vorgabe (`Msp.cpo_standard`) gilt für Vorgänge, deren Datei keinen CPO liefert (z. B. has.to.be-CSV der eigenen
Ladestationen). Sie ist eine öffentliche Kennung („AT*XXX“, wird normiert) oder ein freier Name, wenn der Betreiber
keine hat (z. B. das eigene Unternehmen). Herkunft (`Ladevorgang.betreiber_quelle`): „rechnung“ oder „vorgabe“; ein
CPO aus der Rechnung wird von Vorgaben nie überschrieben.
"""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Ladevorgang
from app.services.evse import evse_zerlegen

LAENGE = 60


def normieren(text: str | None) -> str | None:
    """Eingabe → „AT*XXX“ (bei Kennung, auch „at*xxx“ oder einer EVSE-ID) oder der Name wie eingegeben; leer → None."""
    text = " ".join((text or "").split())
    if not text:
        return None
    return evse_zerlegen(text).betreiber_id or text[:LAENGE]  # auch aus einer ganzen EVSE-ID


def ohne_rechnung():
    """SQL-Bedingung: CPO nicht aus der Rechnung (fehlt oder stammt aus einer Vorgabe)."""
    return or_(Ladevorgang.betreiber_id.is_(None), Ladevorgang.betreiber_quelle == "vorgabe")


def vorgabe_anwenden(db: Session, cpo: str | None, *bedingungen) -> int:
    """CPO-Vorgabe für alle Vorgänge ohne CPO aus der Rechnung setzen (ersetzt frühere Vorgaben). Liefert die Anzahl
    der geänderten Vorgänge."""
    if cpo is None:
        return 0
    anzahl = 0
    for v in db.scalars(select(Ladevorgang).where(ohne_rechnung(), *bedingungen)):
        if (v.betreiber_id, v.betreiber_quelle) != (cpo, "vorgabe"):
            v.betreiber_id, v.betreiber_quelle = cpo, "vorgabe"
            anzahl += 1
    return anzahl
