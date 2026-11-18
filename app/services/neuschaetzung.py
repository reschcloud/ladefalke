"""Automatische Neuschätzung geschätzter kWh.

Die Schätzgrundlage (services/ladeprofil.py) hängt von allen gemessenen Vorgängen und den Plausibilitäts-
grenzen ab. Ändern sich Ladevorgänge, Importe oder Einstellungen, werden deshalb beim Commit alle geschätzten
kWh mit der aktuellen Grundlage neu berechnet (betrifft nur Zeittarife, also wenige Vorgänge). Kosten bleiben
unverändert. Registriert wird das beim Import dieses Moduls (app/main.py).
"""
from itertools import chain

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import Einstellung, Import, Ladevorgang
from app.services.ladeprofil import schaetzgrundlage
from app.services.schaetzung import basis_aus_rohdaten, kwh_schaetzen

AUSLOESER = (Ladevorgang, Import, Einstellung)
_MERKER = "kwh_neu_schaetzen"


def alle_neu_schaetzen(db: Session) -> int:
    """Alle geschätzten kWh neu berechnen. Liefert die Anzahl geänderter Vorgänge."""
    grundlage, anzahl = schaetzgrundlage(db), 0
    for v in db.scalars(select(Ladevorgang).where(Ladevorgang.kwh_geschaetzt)):
        if (basis := basis_aus_rohdaten(v.rohdaten)) is None:
            continue
        if v.stromart in ("AC", "DC"):  # gesetzte Stromart (Vorgabe/manuell) hat Vorrang vor der des Parsers
            basis.art = v.stromart
        kwh = kwh_schaetzen(basis, grundlage.fuer(v.fahrzeug_id))
        if kwh != v.kwh:
            v.kwh, anzahl = kwh, anzahl + 1
    return anzahl


@event.listens_for(Session, "after_flush")
def _aenderung_merken(session: Session, _kontext) -> None:
    if any(isinstance(o, AUSLOESER) for o in chain(session.new, session.dirty, session.deleted)):
        session.info[_MERKER] = True


@event.listens_for(Session, "before_commit")
def _vor_dem_commit(session: Session) -> None:
    session.flush()  # offene Änderungen zuerst, damit der Merker vollständig ist
    if session.info.pop(_MERKER, False):
        from app.services import protokoll
        with protokoll.still(session, Ladevorgang):  # abgeleitete Werte, nicht ins Änderungsprotokoll
            alle_neu_schaetzen(session)
        session.info.pop(_MERKER, None)  # die Neuschätzung selbst löst keine weitere aus
