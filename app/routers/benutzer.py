"""Benutzerverwaltung (nur Admin): anlegen, Rolle und Status ändern, Passwort zurücksetzen, löschen.

Gelöscht werden können nur deaktivierte Benutzer (erst deaktivieren, dann löschen) und nie das eigene Konto. Der
letzte aktive Admin lässt sich weder herabstufen noch deaktivieren – und damit auch nicht löschen.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.formatierung import email_lesen
from app.db import get_db
from app.models import Benutzer
from app.services import benutzer as benutzer_service
from app.web import blaettern, meldung, render, weiter

router = APIRouter(prefix="/benutzer")


def _laden(db: Session, benutzer_id: int) -> Benutzer:
    b = db.get(Benutzer, benutzer_id)
    if b is None:
        raise HTTPException(404, "Benutzer:in nicht gefunden")
    return b


def _formular(request: Request, b: Benutzer | None, werte: dict, fehler: dict | None = None, status_code: int = 200):
    return render(request, "benutzer/formular.html", {
        "blaettern": blaettern(request, object_session(b), "benutzer", "/benutzer",
                               lambda: select(Benutzer.id, Benutzer.name).order_by(  # wie in der Liste
                                   Benutzer.aktiv.desc(), Benutzer.name, Benutzer.id), b.id) if b else None,
        "b": b, "werte": werte, "fehler": fehler or {}, "rollen": benutzer_service.ROLLEN,
        "rollen_text": benutzer_service.ROLLEN_TEXT, "mindestlaenge": benutzer_service.MINDESTLAENGE}, status_code)


def _pruefen(db: Session, werte: dict, b: Benutzer | None, ich: Benutzer) -> dict:
    fehler = {}
    if not werte["name"]:
        fehler["name"] = "Bitte einen Namen angeben."
    if werte["rolle"] not in benutzer_service.ROLLEN:
        fehler["rolle"] = "Bitte eine Rolle wählen."
    try:
        werte["email_wert"] = email_lesen(werte.get("email", ""))
    except ValueError as e:
        fehler["email"] = str(e)
    if b is None:
        if not werte["benutzername"]:
            fehler["benutzername"] = "Bitte einen Benutzernamen angeben."
        elif benutzer_service.finden(db, werte["benutzername"]):
            fehler["benutzername"] = "Diesen Benutzernamen gibt es bereits."
    else:
        verliert_admin = b.rolle == "admin" and b.aktiv and (werte["rolle"] != "admin" or not werte["aktiv"])
        if verliert_admin and benutzer_service.aktive_admins(db) <= 1:
            fehler["rolle"] = "Das ist der letzte aktive Admin – zuerst einen anderen Admin anlegen."
        if b.id == ich.id and not werte["aktiv"]:
            fehler["aktiv"] = "Das eigene Konto lässt sich nicht deaktivieren."
    return fehler


@router.get("")
def liste(request: Request, db: Session = Depends(get_db)):
    alle = db.scalars(select(Benutzer).order_by(Benutzer.aktiv.desc(), Benutzer.name)).all()
    return render(request, "benutzer/liste.html", {"benutzer": alle, "rollen": benutzer_service.ROLLEN,
                                                   "rollen_text": benutzer_service.ROLLEN_TEXT})


@router.get("/neu")
def neu_formular(request: Request):
    return _formular(request, None, {"rolle": "viewer", "aktiv": True})


@router.post("/neu")
def neu(request: Request, benutzername: str = Form(""), name: str = Form(""), rolle: str = Form(""),
        passwort: str = Form(""), wiederholung: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    werte = {"benutzername": benutzername.strip(), "name": name.strip(), "rolle": rolle, "aktiv": True,
             "email": email.strip()}
    fehler = _pruefen(db, werte, None, request.state.benutzer)
    if pw_fehler := benutzer_service.passwort_fehler(passwort, wiederholung):
        fehler["passwort"] = pw_fehler
    if fehler:
        return _formular(request, None, werte, fehler, 422)
    b = Benutzer(benutzername=werte["benutzername"], name=werte["name"], rolle=rolle, email=werte["email_wert"],
                 passwort_hash=benutzer_service.passwort_hash(passwort))
    db.add(b)
    db.commit()
    meldung(request, f"Benutzer:in „{b.benutzername}“ ({benutzer_service.ROLLEN[b.rolle]}) angelegt.")
    return weiter("/benutzer")


@router.get("/{benutzer_id}")
def bearbeiten_formular(request: Request, benutzer_id: int, db: Session = Depends(get_db)):
    b = _laden(db, benutzer_id)
    return _formular(request, b, {"benutzername": b.benutzername, "name": b.name, "rolle": b.rolle, "aktiv": b.aktiv,
                                  "email": b.email or ""})


@router.post("/{benutzer_id}")
def bearbeiten(request: Request, benutzer_id: int, name: str = Form(""), rolle: str = Form(""),
               aktiv: str | None = Form(None), email: str = Form(""), db: Session = Depends(get_db)):
    b = _laden(db, benutzer_id)
    werte = {"benutzername": b.benutzername, "name": name.strip(), "rolle": rolle, "aktiv": aktiv is not None,
             "email": email.strip()}
    if fehler := _pruefen(db, werte, b, request.state.benutzer):
        return _formular(request, b, werte, fehler, 422)
    b.name, b.rolle, b.aktiv, b.email = werte["name"], rolle, werte["aktiv"], werte["email_wert"]
    db.commit()
    if b.id == request.state.benutzer.id and rolle != "admin":  # selbst herabgestuft: Verwaltung nicht mehr erlaubt
        meldung(request, f"Deine Rolle ist jetzt {benutzer_service.ROLLEN[rolle]}.")
        return weiter("/")
    meldung(request, f"Benutzer:in „{b.benutzername}“ gespeichert.")
    return weiter("/benutzer")


@router.post("/{benutzer_id}/passwort")
def passwort_setzen(request: Request, benutzer_id: int, passwort: str = Form(""), wiederholung: str = Form(""),
                    db: Session = Depends(get_db)):
    b = _laden(db, benutzer_id)
    if fehler := benutzer_service.passwort_fehler(passwort, wiederholung):
        return _formular(request, b, {"benutzername": b.benutzername, "name": b.name, "rolle": b.rolle,
                                      "aktiv": b.aktiv}, {"passwort": fehler}, 422)
    b.passwort_hash = benutzer_service.passwort_hash(passwort)
    db.commit()
    meldung(request, f"Neues Passwort für „{b.benutzername}“ gesetzt – bitte sicher weitergeben.")
    return weiter("/benutzer")


@router.post("/{benutzer_id}/loeschen")
def loeschen(request: Request, benutzer_id: int, db: Session = Depends(get_db)):
    b = _laden(db, benutzer_id)
    if b.id == request.state.benutzer.id:
        meldung(request, "Das eigene Konto lässt sich nicht löschen.", "fehler")
        return weiter(f"/benutzer/{b.id}")
    if b.aktiv:
        meldung(request, f"„{b.benutzername}“ ist noch aktiv – bitte zuerst deaktivieren.", "fehler")
        return weiter(f"/benutzer/{b.id}")
    name = b.benutzername
    db.delete(b)
    db.commit()
    meldung(request, f"Benutzer:in „{name}“ gelöscht.")
    return weiter("/benutzer")
