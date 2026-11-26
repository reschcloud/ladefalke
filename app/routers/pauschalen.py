"""Pauschalen: Wallboxen mit monatlichem Fixbetrag (z. B. eSolution Home), manuell erfasst, Beträge netto.

Teil der Import-Seite: die Übersicht steht dort im Abschnitt „Pauschalen“ (`uebersicht`), Anlegen und Bearbeiten
unter /import/pauschalen/…; die frühere Adresse /pauschalen leitet dorthin um.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import extract, select
from sqlalchemy.orm import Session

from app.config import heute
from app.db import get_db
from app.formatierung import decimal_lesen, monat_lesen, zahl
from app.models import Ladeart, Pauschale
from app.web import meldung, render, weiter

router = APIRouter(prefix="/import/pauschalen")
alt = APIRouter()  # frühere Adresse (Lesezeichen)


def ziel(jahr: int | None = None) -> str:
    """Abschnitt „Pauschalen“ auf der Import-Seite, optional für ein Jahr."""
    return f"/import?pauschalen_jahr={jahr}#pauschalen" if jahr else "/import#pauschalen"


def _laden(db: Session, pauschale_id: int) -> Pauschale:
    p = db.get(Pauschale, pauschale_id)
    if p is None:
        raise HTTPException(404, "Pauschale nicht gefunden")
    return p


def _pruefen(db: Session, werte: dict, pauschale_id: int | None) -> tuple[dict, dict]:
    """Liefert (Fehler, Daten zum Speichern)."""
    fehler, daten = {}, {"bezeichnung": werte["bezeichnung"].strip(), "notiz": werte["notiz"].strip() or None}
    try:
        daten["monat"] = monat_lesen(werte["monat"])
        if daten["monat"] is None:
            fehler["monat"] = "Bitte einen Monat angeben."
    except ValueError as e:
        fehler["monat"] = str(e)
    if not daten["bezeichnung"]:
        fehler["bezeichnung"] = "Bitte eine Bezeichnung angeben (z. B. Wallbox-Standort)."
    for feld, stellen, name in (("kwh", "0.001", "kWh"), ("betrag_netto", "0.01", "Betrag")):
        try:
            wert = decimal_lesen(werte[feld])
        except ValueError as e:
            fehler[feld] = str(e)
            continue
        if wert is None or wert < 0:
            fehler[feld] = f"Bitte {name} angeben (0 oder mehr)."
        else:
            daten[feld] = wert.quantize(Decimal(stellen))
    if werte["ladeart"] not in Ladeart.__members__:
        fehler["ladeart"] = "Bitte eine Ladeart wählen."
    else:
        daten["ladeart"] = Ladeart(werte["ladeart"])
    if "monat" not in fehler and "bezeichnung" not in fehler and db.scalar(select(Pauschale.id).where(
            Pauschale.monat == daten["monat"], Pauschale.bezeichnung == daten["bezeichnung"],
            Pauschale.id != (pauschale_id or 0))):
        fehler["bezeichnung"] = "Für diesen Monat gibt es bereits eine Pauschale mit dieser Bezeichnung."
    return fehler, daten


def _formular(request, p, werte, fehler=None, status_code=200, bezeichnungen=()):
    return render(request, "pauschalen/formular.html", {
        "p": p, "werte": werte, "fehler": fehler or {}, "bezeichnungen": bezeichnungen,
        "ladearten": [(a.value, a.label) for a in Ladeart]}, status_code)


def _bisherige_bezeichnungen(db: Session) -> list[str]:
    return list(db.scalars(select(Pauschale.bezeichnung).distinct().order_by(Pauschale.bezeichnung)))


def uebersicht(db: Session, jahr: str = "") -> dict:
    """Daten für den Abschnitt „Pauschalen“ der Import-Seite: Pauschalen des gewählten Jahres mit Summen."""
    jahre = sorted({int(j) for j in db.scalars(select(extract("year", Pauschale.monat)).distinct())}
                   | {heute().year}, reverse=True)
    gewaehlt = int(jahr) if jahr.isdigit() else jahre[0]
    pauschalen = db.scalars(select(Pauschale).where(extract("year", Pauschale.monat) == gewaehlt)
                            .order_by(Pauschale.monat.desc(), Pauschale.bezeichnung)).all()
    return {"pauschalen": pauschalen, "pauschalen_jahre": jahre, "pauschalen_jahr": gewaehlt,
            "pauschalen_kwh": sum((p.kwh for p in pauschalen), Decimal(0)),
            "pauschalen_betrag": sum((p.betrag_netto for p in pauschalen), Decimal(0))}


@alt.get("/pauschalen")
def frueher(jahr: str = ""):
    return RedirectResponse(ziel(int(jahr) if jahr.isdigit() else None), status_code=301)


@router.get("/neu")
def neu_formular(request: Request, db: Session = Depends(get_db)):
    werte = {"monat": heute().strftime("%Y-%m"), "ladeart": "privat"}
    return _formular(request, None, werte, bezeichnungen=_bisherige_bezeichnungen(db))


@router.post("/neu")
def neu(request: Request, monat: str = Form(""), bezeichnung: str = Form(""), kwh: str = Form(""),
        betrag_netto: str = Form(""), ladeart: str = Form("privat"), notiz: str = Form(""),
        db: Session = Depends(get_db)):
    werte = {"monat": monat, "bezeichnung": bezeichnung, "kwh": kwh, "betrag_netto": betrag_netto,
             "ladeart": ladeart, "notiz": notiz}
    fehler, daten = _pruefen(db, werte, None)
    if fehler:
        return _formular(request, None, werte, fehler, 422, _bisherige_bezeichnungen(db))
    p = Pauschale(**daten)
    db.add(p)
    db.commit()
    meldung(request, f"Pauschale „{p.bezeichnung}“ für {p.monat:%m/%Y} angelegt.")
    return weiter(ziel(p.monat.year))


@router.get("/{pauschale_id}")
def bearbeiten_formular(request: Request, pauschale_id: int, db: Session = Depends(get_db)):
    p = _laden(db, pauschale_id)
    werte = {"monat": p.monat.strftime("%Y-%m"), "bezeichnung": p.bezeichnung, "kwh": zahl(p.kwh, 3),
             "betrag_netto": zahl(p.betrag_netto, 2), "ladeart": p.ladeart.value, "notiz": p.notiz}
    return _formular(request, p, werte, bezeichnungen=_bisherige_bezeichnungen(db))


@router.post("/{pauschale_id}")
def bearbeiten(request: Request, pauschale_id: int, monat: str = Form(""), bezeichnung: str = Form(""),
               kwh: str = Form(""), betrag_netto: str = Form(""), ladeart: str = Form("privat"),
               notiz: str = Form(""), db: Session = Depends(get_db)):
    p = _laden(db, pauschale_id)
    werte = {"monat": monat, "bezeichnung": bezeichnung, "kwh": kwh, "betrag_netto": betrag_netto,
             "ladeart": ladeart, "notiz": notiz}
    fehler, daten = _pruefen(db, werte, p.id)
    if fehler:
        return _formular(request, p, werte, fehler, 422, _bisherige_bezeichnungen(db))
    for feld, wert in daten.items():
        setattr(p, feld, wert)
    db.commit()
    meldung(request, f"Pauschale „{p.bezeichnung}“ für {p.monat:%m/%Y} gespeichert.")
    return weiter(ziel(p.monat.year))


@router.post("/{pauschale_id}/loeschen")
def loeschen(request: Request, pauschale_id: int, db: Session = Depends(get_db)):
    p = _laden(db, pauschale_id)
    jahr = p.monat.year
    db.delete(p)
    db.commit()
    meldung(request, f"Pauschale „{p.bezeichnung}“ für {p.monat:%m/%Y} gelöscht.")
    return weiter(ziel(jahr))
