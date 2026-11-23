"""Mein Konto: eigenes Passwort und Theme – für alle Rollen (auch Viewer); eigene E-Mail nur Admin und Operator."""
from fastapi import APIRouter, HTTPException, Depends, Form, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.formatierung import email_lesen
from app.services import benutzer as benutzer_service
from app.web import meldung, render, weiter

router = APIRouter()


def _seite(request: Request, fehler: dict | None = None, status_code: int = 200):
    return render(request, "konto.html", {"fehler": fehler or {}, "themes": benutzer_service.THEMES,
                                          "zeitraeume": benutzer_service.ZEITRAEUME,
                                          "rollen": benutzer_service.ROLLEN,
                                          "rollen_text": benutzer_service.ROLLEN_TEXT,
                                          "mindestlaenge": benutzer_service.MINDESTLAENGE}, status_code)


@router.get("/konto")
def anzeigen(request: Request):
    return _seite(request)


@router.post("/konto/passwort")
def passwort_aendern(request: Request, aktuell: str = Form(""), neu: str = Form(""), wiederholung: str = Form(""),
                     db: Session = Depends(get_db)):
    b = request.state.benutzer
    if not benutzer_service.passwort_pruefen(aktuell, b.passwort_hash):
        return _seite(request, {"aktuell": "Das bisherige Passwort stimmt nicht."}, 422)
    if fehler := benutzer_service.passwort_fehler(neu, wiederholung):
        return _seite(request, {"neu": fehler}, 422)
    b.passwort_hash = benutzer_service.passwort_hash(neu)
    db.commit()
    meldung(request, "Passwort geändert.")
    return weiter("/konto")


@router.post("/konto/email")
def email_aendern(request: Request, email: str = Form(""), db: Session = Depends(get_db)):
    """Eigene E-Mail-Adresse – Admin und Operator; die Adresse eines Viewers pflegt ein Admin (Benutzerverwaltung)."""
    b = request.state.benutzer
    if not benutzer_service.darf(b, "schreiben"):
        return _seite(request, {"email": "Unzureichende Berechtigungen – die Adresse pflegt ein Admin."}, 403)
    try:
        b.email = email_lesen(email)
    except ValueError as e:
        return _seite(request, {"email": str(e)}, 422)
    db.commit()
    meldung(request, "E-Mail-Adresse gespeichert." if b.email else "E-Mail-Adresse entfernt.")
    return weiter("/konto")


@router.post("/konto/abo")
def abo_setzen(request: Request, abo: str = Form(""), db: Session = Depends(get_db)):
    """Zusammenfassungen per Mail erhalten (automatischer Versand) – jede:r für sich, auch Viewer."""
    b = request.state.benutzer
    b.zusammenfassung_abo = bool(abo)
    db.commit()
    meldung(request, "Zusammenfassungen werden dir per Mail geschickt." if b.zusammenfassung_abo
            else "Zusammenfassungen abbestellt.")
    return weiter("/konto#email")


@router.post("/konto/fristen-abo")
def fristen_abo_setzen(request: Request, abo: str = Form(""), db: Session = Depends(get_db)):
    """Benachrichtigung per Mail bei Fristen (Hinweisfrist erreicht, abgelaufen) – jede:r für sich, auch Viewer."""
    b = request.state.benutzer
    b.fristen_abo = bool(abo)
    db.commit()
    meldung(request, "Du wirst per Mail benachrichtigt, wenn Fristen fällig werden oder ablaufen." if b.fristen_abo
            else "Benachrichtigung bei Fristen abbestellt.")
    return weiter("/konto#email")


@router.post("/konto/aufgaben-abo")
def aufgaben_abo_setzen(request: Request, abo: str = Form(""), db: Session = Depends(get_db)):
    """Report „Offene Aufgaben“ per Mail erhalten (automatischer Versand) – jede:r für sich, auch Viewer."""
    b = request.state.benutzer
    b.aufgaben_abo = bool(abo)
    db.commit()
    meldung(request, "Die offenen Aufgaben werden dir per Mail geschickt." if b.aufgaben_abo
            else "Offene Aufgaben abbestellt.")
    return weiter("/konto#email")


WEITERE_ABOS = {"karten_cpo": "Karten und Preise je CPO", "flottenbilanz": "Flottenbilanz",
                "zuordnungen": "Zuordnungen am Stichtag"}


@router.post("/konto/report-abo/{art}")
def report_abo_setzen(request: Request, art: str, abo: str = Form(""), db: Session = Depends(get_db)):
    """Weitere Reports per Mail erhalten (automatischer Versand) – jede:r für sich, auch Viewer."""
    if art not in WEITERE_ABOS:
        raise HTTPException(404, "Unbekannter Report")
    b = request.state.benutzer
    setattr(b, f"{art}_abo", bool(abo))
    db.commit()
    meldung(request, f"„{WEITERE_ABOS[art]}“ wird dir per Mail geschickt." if abo else f"„{WEITERE_ABOS[art]}“ abbestellt.")
    return weiter("/konto#email")


@router.post("/konto/theme")
def theme_setzen(request: Request, theme: str = Form("auto"), db: Session = Depends(get_db)):
    """Theme nur für den angemeldeten Benutzer; die Seite wird vollständig neu geladen (Theme steht am <html>)."""
    request.state.benutzer.theme = theme if theme in benutzer_service.THEMES else "auto"
    db.commit()
    return weiter("/konto")


@router.post("/konto/zeitraum")
def zeitraum_setzen(request: Request, zeitraum: str = Form("monat"), db: Session = Depends(get_db)):
    """Vorausgewählter Zeitraum für Dashboard, Auswertungen und Reports – nur für den angemeldeten Benutzer."""
    b = request.state.benutzer
    b.zeitraum = zeitraum if zeitraum in benutzer_service.ZEITRAEUME else "monat"
    db.commit()
    meldung(request, f"Vorausgewählter Zeitraum: {benutzer_service.ZEITRAEUME[b.zeitraum]}.")
    return weiter("/konto#darstellung")


@router.get("/keine-berechtigung")
def keine_berechtigung(request: Request):
    return render(request, "keine_berechtigung.html", {}, 403)
