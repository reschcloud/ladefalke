"""Anmeldung und Rechte (SPEC Abschnitt 8): Benutzer mit Rolle aus der Datenbank (services/benutzer.py).

Die Sitzung speichert nur die Benutzer-ID; Rolle und Aktiv-Status werden bei jeder Anfrage frisch gelesen.
Durchsetzung auf dem Server: Viewer dürfen nichts ändern (jede Anfrage außer GET/HEAD wird abgewiesen, ausgenommen
Abmelden und das eigene Konto); Einstellungen und Benutzerverwaltung nur für Admins.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Benutzer
from app.services import benutzer as benutzer_service, protokoll
from app.web import render, weiter, weiter as weiter_zu_seite  # „weiter“ ist in login_formular ein Parameter

router = APIRouter()

# Ändernde Anfragen, die auch Viewer stellen dürfen (eigenes Konto, Abmelden)
FUER_ALLE = ("/logout", "/konto/")


class NichtAngemeldet(Exception):
    pass


class KeineBerechtigung(Exception):
    pass


def login_erforderlich(request: Request, db: Session = Depends(get_db)) -> Benutzer:
    """Angemeldeter, aktiver Benutzer (in request.state.benutzer); Viewer dürfen nur lesen."""
    b = db.get(Benutzer, request.session.get("benutzer_id") or 0)
    if b is None or not b.aktiv:
        request.session.clear()
        raise NichtAngemeldet()
    request.state.benutzer = b
    protokoll.setzen(db, b)  # Änderungsprotokoll: wer ändert
    if request.method not in ("GET", "HEAD") and not benutzer_service.darf(b, "schreiben") \
            and not request.url.path.startswith(FUER_ALLE):
        raise KeineBerechtigung()
    return b


def admin_fuer_aenderungen(request: Request) -> None:
    """Nach login_erforderlich: lesen dürfen alle, ändern (alles außer GET/HEAD) nur Admins – z. B. MSP."""
    if request.method not in ("GET", "HEAD"):
        admin_erforderlich(request)


def admin_erforderlich(request: Request) -> None:
    """Nach login_erforderlich: Einstellungen und Benutzerverwaltung."""
    if not benutzer_service.darf(getattr(request.state, "benutzer", None), "verwalten"):
        raise KeineBerechtigung()


def nicht_angemeldet_behandeln(request: Request, exc: NichtAngemeldet) -> Response:
    ziel = "/login?weiter=" + quote(request.url.path)
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": ziel})
    return weiter(ziel)


def keine_berechtigung_behandeln(request: Request, exc: KeineBerechtigung) -> Response:
    if request.headers.get("HX-Request"):  # htmx tauscht 403 nicht ein – auf die Hinweisseite umleiten
        return Response(status_code=200, headers={"HX-Redirect": "/keine-berechtigung"})
    return render(request, "keine_berechtigung.html", {}, 403)


def _sicheres_ziel(ziel: str | None) -> str:
    # Nur lokale Pfade zulassen (kein Open Redirect)
    if ziel and ziel.startswith("/") and not ziel.startswith("//"):
        return ziel
    return "/"


@router.get("/login")
def login_formular(request: Request, weiter: str = "/", db: Session = Depends(get_db)):
    if benutzer_service.ersteinrichtung_offen(db):
        return weiter_zu_seite("/einrichten")
    return render(request, "login.html", {"weiter": weiter})


# ------------------------------------------------------------------ Ersteinrichtung: ersten Admin im Browser anlegen
# Nur solange es keine Benutzer:innen gibt und kein ADMIN_PASSWORD gesetzt ist (services/benutzer.py). Wer zuerst
# kommt, legt den Admin an – deshalb gleich nach der Installation erledigen (setup.sh legt ihn selbst an).

def _einrichten_formular(request: Request, werte: dict, fehler: dict | None = None, status_code: int = 200):
    return render(request, "einrichten.html", {"werte": werte, "fehler": fehler or {},
                                               "mindestlaenge": benutzer_service.MINDESTLAENGE}, status_code)


@router.get("/einrichten")
def einrichten_formular(request: Request, db: Session = Depends(get_db)):
    if not benutzer_service.ersteinrichtung_offen(db):
        return weiter_zu_seite("/login")
    return _einrichten_formular(request, {"benutzername": "admin", "name": ""})


@router.post("/einrichten")
def einrichten(request: Request, benutzername: str = Form(""), name: str = Form(""), passwort: str = Form(""),
               wiederholung: str = Form(""), db: Session = Depends(get_db)):
    db.execute(text("SELECT pg_advisory_xact_lock(4711)"))  # zwei gleichzeitige Versuche: nur einer legt an
    if not benutzer_service.ersteinrichtung_offen(db):
        return weiter_zu_seite("/login")
    werte = {"benutzername": benutzername.strip(), "name": name.strip()}
    fehler = {}
    if not werte["benutzername"] or len(werte["benutzername"]) > 80:
        fehler["benutzername"] = "Bitte einen Benutzernamen angeben (höchstens 80 Zeichen)."
    if len(werte["name"]) > 200:
        fehler["name"] = "Höchstens 200 Zeichen."
    if text_fehler := benutzer_service.passwort_fehler(passwort, wiederholung):
        fehler["passwort"] = text_fehler
    if fehler:
        return _einrichten_formular(request, werte, fehler, 422)
    b = Benutzer(benutzername=werte["benutzername"], name=werte["name"] or werte["benutzername"], rolle="admin",
                 passwort_hash=benutzer_service.passwort_hash(passwort))
    db.add(b)
    db.flush()
    protokoll.setzen(db, b)
    protokoll.eintragen(db, "anmeldung", "anmeldung", f"{b.name} ({b.benutzername})", b.id,
                        kontext_text=_herkunft(request))
    db.commit()
    request.session.clear()
    request.session["benutzer_id"] = b.id
    return weiter_zu_seite("/")


@router.post("/login")
def login(request: Request, benutzer: str = Form(""), passwort: str = Form(""), weiter_zu: str = Form("/"),
          db: Session = Depends(get_db)):
    b = benutzer_service.anmelden(db, benutzer, passwort)
    if b is None:
        name = benutzer.strip()[:80] or "(leer)"
        protokoll.eintragen(db, "anmeldung_fehlgeschlagen", "anmeldung", name, wer=(None, name),
                            kontext_text=_herkunft(request))
        db.commit()  # ggf. eingerichteten ersten Admin behalten
        return render(request, "login.html", {"weiter": weiter_zu, "benutzer": benutzer,
                                              "fehler": "Benutzername oder Passwort falsch."}, 422)
    protokoll.setzen(db, b)
    protokoll.eintragen(db, "anmeldung", "anmeldung", f"{b.name} ({b.benutzername})", b.id,
                        kontext_text=_herkunft(request))
    db.commit()
    request.session.clear()
    request.session["benutzer_id"] = b.id
    return weiter(_sicheres_ziel(weiter_zu))


def _herkunft(request: Request) -> str | None:
    """Adresse der Anfrage für das Protokoll (hinter einem Proxy die weitergereichte)."""
    weitergeleitet = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    adresse = weitergeleitet or (request.client.host if request.client else "")
    return f"von {adresse}" if adresse else None


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return weiter("/login")
