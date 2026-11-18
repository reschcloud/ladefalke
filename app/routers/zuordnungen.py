"""Übersicht und Bearbeitung aller Zuordnungsperioden."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import heute
from app.db import get_db
from app.formatierung import datum_lesen
from app.models import Fahrzeug, Ladekarte, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.routers.ladekarten import ziel_lesen, ziele
from app.services import zeitleiste as zeitleiste_service
from app.services.aufloesung import neu_zuordnen_fahrer, neu_zuordnen_karte, zeitraum_vereinigen
from app.services.zuordnung import (ZuordnungsFehler, fahrer_fahrzeug_aendern, gueltig_am,
                                    karten_zuordnung_aendern)
from app.web import meldung, render, weiter

router = APIRouter(prefix="/stammdaten/zuordnungen")


@router.get("")
def uebersicht(request: Request, stichtag: str = "", db: Session = Depends(get_db)):
    fehler = None
    try:
        tag = datum_lesen(stichtag)
    except ValueError as e:
        tag, fehler = None, str(e)
    karten_q = select(ZuordnungKarte).join(Ladekarte).order_by(Ladekarte.kennung, ZuordnungKarte.gueltig_ab.desc())
    fahrer_q = select(ZuordnungFahrerFahrzeug).order_by(ZuordnungFahrerFahrzeug.gueltig_ab.desc())
    if tag:
        karten_q = karten_q.where(gueltig_am(ZuordnungKarte, tag))
        fahrer_q = fahrer_q.where(gueltig_am(ZuordnungFahrerFahrzeug, tag))
    return render(request, "stammdaten/zuordnungen.html", {
        "karten_zuordnungen": db.scalars(karten_q).all(), "fahrer_zuordnungen": db.scalars(fahrer_q).all(),
        "stichtag": tag, "fehler": fehler})


@router.get("/zeitleiste")
def zeitleiste(request: Request, jahr: str = "", ansicht: str = "jahr", status: str = "aktiv",
               nach: str = "fahrzeug", db: Session = Depends(get_db)):
    """Gantt-Ansicht der Zuordnungen; Standard: laufendes Kalenderjahr, Zeilen = Fahrzeuge.
    `ansicht`: jahr (blätterbar), 12monate, 5jahre, alles."""
    stichtag = heute()
    ansicht = ansicht if ansicht in ("12monate", "5jahre", "alles") else "jahr"
    rollierend = ansicht != "jahr"  # fester Zeitraum ohne Blättern
    gewaehlt = int(jahr) if jahr.isdigit() else stichtag.year
    von, bis = {"12monate": lambda: zeitleiste_service.bereich_rollierend(stichtag),
                "5jahre": lambda: zeitleiste_service.bereich_5jahre(stichtag),
                "alles": lambda: zeitleiste_service.bereich_alles(db, stichtag),
                "jahr": lambda: zeitleiste_service.bereich_jahr(gewaehlt)}[ansicht]()
    nach = nach if nach in zeitleiste_service.RICHTUNGEN else "fahrzeug"
    status = status if status in ("aktiv", "inaktiv", "alle") else "aktiv"
    zl = zeitleiste_service.berechnen(db, von, bis, stichtag, status=status, nach=nach)
    parameter = {"status": status, "nach": nach, **({"ansicht": ansicht} if rollierend else {"jahr": gewaehlt})}
    return render(request, "stammdaten/zeitleiste.html", {
        "zl": zl, "rollierend": rollierend, "ansicht": ansicht, "jahr": gewaehlt, "status": status, "nach": nach,
        "url": lambda **neu: "/stammdaten/zuordnungen/zeitleiste?" + urlencode(
            {k: v for k, v in {**parameter, **neu}.items() if v not in (None, "")})})


def _zurueck(request: Request, standard: str) -> str:
    """Nach dem Speichern zur aufrufenden Seite zurück (nur lokale Pfade)."""
    ziel = request.query_params.get("zurueck", "")
    return ziel if ziel.startswith("/") and not ziel.startswith("//") else standard


# ------------------------------------------------------------------ Ladekarte → Fahrer:in / Fahrzeug

def _karten_zuordnung(db: Session, zuordnung_id: int) -> ZuordnungKarte:
    z = db.get(ZuordnungKarte, zuordnung_id)
    if z is None:
        raise HTTPException(404, "Zuordnung nicht gefunden")
    return z


def _karten_formular(request, db, z, werte, fehler=None, status_code=200):
    return render(request, "stammdaten/zuordnung_karte_formular.html",
                  {"z": z, "werte": werte, "fehler": fehler, "zurueck": _zurueck(request, "/stammdaten/zuordnungen"),
                   **ziele(db, z.fahrer_id, z.fahrzeug_id)}, status_code)


@router.get("/karte/{zuordnung_id}")
def karten_zuordnung_formular(request: Request, zuordnung_id: int, db: Session = Depends(get_db)):
    z = _karten_zuordnung(db, zuordnung_id)
    ziel = f"fahrer:{z.fahrer_id}" if z.fahrer_id else f"fahrzeug:{z.fahrzeug_id}"
    werte = {"ziel": ziel, "ab": z.gueltig_ab.isoformat(), "bis": z.gueltig_bis.isoformat() if z.gueltig_bis else "",
             "neu_zuordnen": "on"}  # Vorauswahl: beim Speichern neu zuordnen
    return _karten_formular(request, db, z, werte)


@router.post("/karte/{zuordnung_id}")
def karten_zuordnung_speichern(request: Request, zuordnung_id: int, ziel: str = Form(""), ab: str = Form(""),
                               bis: str = Form(""), neu_zuordnen: str | None = Form(None),
                               db: Session = Depends(get_db)):
    z = _karten_zuordnung(db, zuordnung_id)
    werte = {"ziel": ziel, "ab": ab, "bis": bis, "neu_zuordnen": neu_zuordnen}
    alt = (z.gueltig_ab, z.gueltig_bis)
    try:
        fahrer_id, fahrzeug_id = ziel_lesen(ziel)
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        hinweise = karten_zuordnung_aendern(db, z, ab=ab_datum, bis=datum_lesen(bis), fahrer_id=fahrer_id,
                                            fahrzeug_id=fahrzeug_id, heute=heute())
    except ValueError as e:
        db.rollback()
        return _karten_formular(request, db, z, werte, str(e), 422)
    if neu_zuordnen:
        n = neu_zuordnen_karte(db, z.ladekarte_id, *zeitraum_vereinigen(alt, (z.gueltig_ab, z.gueltig_bis)))
        neu_info = f"{n} bereits importierte Ladevorgänge wurden neu zugeordnet."
    db.commit()
    meldung(request, "Zuordnung gespeichert.")
    if neu_zuordnen:
        meldung(request, neu_info)
    for h in hinweise:
        meldung(request, h, "warnung")
    return weiter(_zurueck(request, "/stammdaten/zuordnungen"))


@router.post("/karte/{zuordnung_id}/loeschen")
def karten_zuordnung_loeschen(request: Request, zuordnung_id: int, neu_zuordnen: str | None = Form(None),
                              db: Session = Depends(get_db)):
    z = _karten_zuordnung(db, zuordnung_id)
    karte_id, von, bis = z.ladekarte_id, z.gueltig_ab, z.gueltig_bis
    db.delete(z)
    if neu_zuordnen:
        meldung(request, f"{neu_zuordnen_karte(db, karte_id, von, bis)} bereits importierte Ladevorgänge "
                         "wurden neu zugeordnet.")
    db.commit()
    meldung(request, "Zuordnung gelöscht.")
    return weiter(_zurueck(request, "/stammdaten/zuordnungen"))


# ------------------------------------------------------------------ Fahrer:in → Fahrzeug

def _fahrer_zuordnung(db: Session, zuordnung_id: int) -> ZuordnungFahrerFahrzeug:
    z = db.get(ZuordnungFahrerFahrzeug, zuordnung_id)
    if z is None:
        raise HTTPException(404, "Zuordnung nicht gefunden")
    return z


def _fahrer_formular(request, db, z, werte, fehler=None, status_code=200):
    fahrzeuge = db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv | (Fahrzeug.id == z.fahrzeug_id))
                           .order_by(Fahrzeug.kennzeichen)).all()
    return render(request, "stammdaten/zuordnung_fahrer_formular.html",
                  {"z": z, "werte": werte, "fehler": fehler, "fahrzeuge": fahrzeuge,
                   "zurueck": _zurueck(request, "/stammdaten/zuordnungen")}, status_code)


@router.get("/fahrer/{zuordnung_id}")
def fahrer_zuordnung_formular(request: Request, zuordnung_id: int, db: Session = Depends(get_db)):
    z = _fahrer_zuordnung(db, zuordnung_id)
    werte = {"fahrzeug_id": str(z.fahrzeug_id), "ab": z.gueltig_ab.isoformat(),
             "bis": z.gueltig_bis.isoformat() if z.gueltig_bis else "", "neu_zuordnen": "on"}
    return _fahrer_formular(request, db, z, werte)


@router.post("/fahrer/{zuordnung_id}")
def fahrer_zuordnung_speichern(request: Request, zuordnung_id: int, fahrzeug_id: str = Form(""),
                               ab: str = Form(""), bis: str = Form(""), neu_zuordnen: str | None = Form(None),
                               db: Session = Depends(get_db)):
    z = _fahrer_zuordnung(db, zuordnung_id)
    werte = {"fahrzeug_id": fahrzeug_id, "ab": ab, "bis": bis, "neu_zuordnen": neu_zuordnen}
    alt = (z.gueltig_ab, z.gueltig_bis)
    try:
        if not fahrzeug_id.isdigit() or db.get(Fahrzeug, int(fahrzeug_id)) is None:
            raise ZuordnungsFehler("Bitte ein Fahrzeug wählen.")
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        hinweise = fahrer_fahrzeug_aendern(db, z, ab=ab_datum, bis=datum_lesen(bis), fahrzeug_id=int(fahrzeug_id),
                                           heute=heute())
    except ValueError as e:
        db.rollback()
        return _fahrer_formular(request, db, z, werte, str(e), 422)
    if neu_zuordnen:
        n = neu_zuordnen_fahrer(db, z.fahrer_id, *zeitraum_vereinigen(alt, (z.gueltig_ab, z.gueltig_bis)))
        neu_info = f"{n} bereits importierte Ladevorgänge wurden neu zugeordnet."
    db.commit()
    meldung(request, "Zuordnung gespeichert.")
    if neu_zuordnen:
        meldung(request, neu_info)
    for h in hinweise:
        meldung(request, h, "warnung")
    return weiter(_zurueck(request, "/stammdaten/zuordnungen"))


@router.post("/fahrer/{zuordnung_id}/loeschen")
def fahrer_zuordnung_loeschen(request: Request, zuordnung_id: int, neu_zuordnen: str | None = Form(None),
                              db: Session = Depends(get_db)):
    z = _fahrer_zuordnung(db, zuordnung_id)
    fahrer_id, von, bis = z.fahrer_id, z.gueltig_ab, z.gueltig_bis
    db.delete(z)
    if neu_zuordnen:
        meldung(request, f"{neu_zuordnen_fahrer(db, fahrer_id, von, bis)} bereits importierte Ladevorgänge "
                         "wurden neu zugeordnet.")
    db.commit()
    meldung(request, "Zuordnung gelöscht.")
    return weiter(_zurueck(request, "/stammdaten/zuordnungen"))
