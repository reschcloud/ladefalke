"""Seiten-Cache für rechenintensive Seiten (Dashboard, Auswertungen, Reports-Übersicht).

Eine fertig gerenderte Seite wird je Benutzer:in und Adresse gemerkt und bis zur nächsten Datenänderung wieder
ausgeliefert. Ungültig wird alles auf einmal über den **Datenstand**: Jede gespeicherte Änderung (ORM-Änderungen und
INSERT/UPDATE/DELETE über die Session, auch aus dem Taktgeber) zählt ihn beim Commit hoch. Änderungen von außerhalb
der App (z. B. Wiederherstellen per Kommandozeile) sieht er nicht – dafür gilt ein Eintrag höchstens `GUELTIG`
Sekunden und nur am selben Tag (Fristen, „heute“). Seiten mit Meldungen (Toasts) werden weder gemerkt noch aus dem
Cache bedient. Nur im Speicher dieses Prozesses; nach einem Neustart leer.

Von Hand neu rechnen: harter Reload im Browser (Strg+Shift+R bzw. Cmd+Shift+R) – der Browser schickt dabei
`Cache-Control: no-cache` bzw. `Pragma: no-cache`; die Seite wird neu berechnet und das Ergebnis neu gemerkt. Ein
normaler Reload (F5, `max-age=0`) und die Navigation in der App nutzen den Cache.
"""
import threading
import time
from collections import OrderedDict
from functools import wraps
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import heute

GUELTIG = 600  # Sekunden
MAX_EINTRAEGE = 100
_sperre = threading.Lock()
_eintraege: "OrderedDict[tuple, tuple[int, float, date, bytes, int]]" = OrderedDict()
_datenstand = 0
_GEAENDERT = "seitencache_geaendert"


def datenstand() -> int:
    return _datenstand


def ungueltig_machen() -> None:
    global _datenstand
    with _sperre:
        _datenstand += 1
        _eintraege.clear()


@event.listens_for(Session, "after_flush")
def _nach_flush(session: Session, _kontext) -> None:
    if session.new or session.deleted or any(session.is_modified(o) for o in session.dirty):
        session.info[_GEAENDERT] = True


@event.listens_for(Session, "do_orm_execute")
def _bei_ausfuehrung(zustand) -> None:
    if zustand.is_insert or zustand.is_update or zustand.is_delete:
        zustand.session.info[_GEAENDERT] = True


@event.listens_for(Session, "after_commit")
def _nach_commit(session: Session) -> None:
    if session.info.pop(_GEAENDERT, False):
        ungueltig_machen()


@event.listens_for(Session, "after_rollback")
def _nach_rollback(session: Session) -> None:
    session.info.pop(_GEAENDERT, None)


def _schluessel(request: Request) -> tuple | None:
    benutzer = getattr(request.state, "benutzer", None)
    if benutzer is None or request.method != "GET":
        return None
    return (benutzer.id, request.url.path, tuple(sorted(request.query_params.multi_items())),
            bool(request.headers.get("HX-Request")))


def neu_rechnen(request: Request) -> bool:
    """Harter Reload im Browser: „no-cache“ in Cache-Control oder Pragma."""
    return any("no-cache" in request.headers.get(kopf, "").lower() for kopf in ("cache-control", "pragma"))


def holen(request: Request) -> HTMLResponse | None:
    """Gemerkte Seite oder None. Merkt sich am Request, ob die Seite danach gemerkt werden darf."""
    request.state.seitencache = None
    schluessel = _schluessel(request)
    if schluessel is None or request.session.get("meldungen"):
        return None  # Meldungen gehören in genau eine Seite – nie aus dem Cache und nie gemerkt
    request.state.seitencache = (schluessel, _datenstand)
    if neu_rechnen(request):  # harter Reload: nicht ausliefern, aber das frische Ergebnis merken
        return None
    with _sperre:
        eintrag = _eintraege.get(schluessel)
        if eintrag is None:
            return None
        stand, zeit, tag, inhalt, status = eintrag
        if stand != _datenstand or time.monotonic() - zeit > GUELTIG or tag != heute():
            del _eintraege[schluessel]
            return None
        _eintraege.move_to_end(schluessel)
    return HTMLResponse(inhalt, status_code=status, headers={"X-Seitencache": "treffer"})


def merken(request: Request, antwort):
    """Fertige Antwort merken (nur 200, nur wenn `holen` es erlaubt hat und der Datenstand gleich geblieben ist)."""
    vorgemerkt = getattr(request.state, "seitencache", None)
    if vorgemerkt is None or antwort.status_code != 200 or not hasattr(antwort, "body"):
        return antwort
    schluessel, stand = vorgemerkt
    with _sperre:
        if stand == _datenstand:  # während des Rechnens geändert: nicht merken, könnte schon veraltet sein
            _eintraege[schluessel] = (stand, time.monotonic(), heute(), bytes(antwort.body), antwort.status_code)
            _eintraege.move_to_end(schluessel)
            while len(_eintraege) > MAX_EINTRAEGE:
                _eintraege.popitem(last=False)
    return antwort


def gecacht(route):
    """Dekorator für GET-Seiten mit `request` als Argument: gemerkte Seite ausliefern bzw. die neue merken."""
    @wraps(route)
    def mit_cache(*args, **kwargs):
        request = kwargs["request"]
        if (treffer := holen(request)) is not None:
            return treffer
        return merken(request, route(*args, **kwargs))
    return mit_cache
