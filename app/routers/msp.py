"""Stammdaten: Mobility Service Provider – mit Verwaltung der Parser (aktiv/deaktiviert, fehlende Parser).

Lesen dürfen alle, ändern nur Admins (auth.admin_fuer_aenderungen). Siehe services/parser_verwaltung.py."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.db import get_db
from decimal import Decimal

from app.formatierung import decimal_lesen, zahl
from app.parsers import PARSER
from app.models import Import, Ladeart, Ladekarte, Ladevorgang, Msp
from app.services.ladevorgaenge import letzte_vorgaenge
from app.services import cpo, parser_verwaltung, stromart
from app.web import blaettern, meldung, render, weiter

router = APIRouter(prefix="/stammdaten/msp", dependencies=[Depends(auth.admin_fuer_aenderungen)])


def _laden(db: Session, msp_id: int) -> Msp:
    msp = db.get(Msp, msp_id)
    if msp is None:
        raise HTTPException(404, "MSP nicht gefunden")
    return msp


def _formular(request, msp, werte, fehler=None, status_code=200, db: Session | None = None):
    vorgaenge = letzte_vorgaenge(db, Ladevorgang.msp_id == msp.id) if msp is not None and db is not None else []
    def anzahl(*bedingungen):
        return db.scalar(select(func.count()).select_from(Ladevorgang).where(
            Ladevorgang.msp_id == msp.id, *bedingungen)) if msp is not None and db is not None else 0
    return render(request, "stammdaten/msp_formular.html",
                  {"msp": msp, "werte": werte, "fehler": fehler or {}, "vorgaenge": vorgaenge, "installiert": PARSER,
                   "blaettern": blaettern(request, db, "msp", "/stammdaten/msp",
                                          lambda: select(Msp.id, Msp.name).order_by(Msp.name, Msp.id), msp.id)
                   if msp is not None and db is not None else None,
                   "ohne_stromart": anzahl(stromart.ohne_angabe()),
                   "hat_daten": msp is not None and db is not None and bool(msp.ladekarten or db.scalar(
                       select(Import.id).where(Import.msp_id == msp.id).limit(1))),
                   "ohne_cpo": anzahl(Ladevorgang.betreiber_id.is_(None)),
                   "cpo_abweichend": anzahl(cpo.ohne_rechnung(), Ladevorgang.betreiber_id.is_distinct_from(
                       msp.cpo_standard)) if msp is not None and msp.cpo_standard else 0,
                   "ladearten": [(a.value, a.label) for a in Ladeart],
                   "parser_wahl": _parser_wahl(db, msp, werte.get("parser_key")),
                   "parser_fehlt": msp is not None and parser_verwaltung.fehlend(msp)}, status_code)


def _parser_wahl(db: Session | None, msp: Msp | None, aktuell: str | None) -> list[tuple[str, str]]:
    """Auswahl „Parser“: installierte Parser ohne anderen MSP, dazu der aktuelle (auch wenn er fehlt)."""
    vergeben = set(db.scalars(select(Msp.parser_key).where(Msp.parser_key.is_not(None), Msp.id != (msp.id if msp else 0)))) \
        if db is not None else set()
    wahl = [("", "– kein Parser –")] + [(k, p.name) for k, p in PARSER.items() if k not in vergeben]
    if aktuell and aktuell not in PARSER:
        wahl.append((aktuell, f"{aktuell} (nicht installiert)"))
    return wahl


def _pruefen(db: Session, werte: dict, msp_id: int | None) -> dict:
    fehler = {}
    if not werte["name"]:
        fehler["name"] = "Bitte einen Namen angeben."
    elif db.scalar(select(Msp.id).where(func.lower(Msp.name) == werte["name"].lower(), Msp.id != (msp_id or 0))):
        fehler["name"] = "Ein MSP mit diesem Namen existiert bereits."
    if werte["parser_key"] and db.scalar(
            select(Msp.id).where(Msp.parser_key == werte["parser_key"], Msp.id != (msp_id or 0))):
        fehler["parser_key"] = "Dieser Parser ist bereits einem anderen MSP zugeordnet."
    bisher = db.scalar(select(Msp.parser_key).where(Msp.id == msp_id)) if msp_id else None
    if werte["parser_key"] and werte["parser_key"] not in PARSER and werte["parser_key"] != bisher:
        fehler["parser_key"] = "Dieser Parser ist nicht installiert."
    if werte["standard_ladeart"] not in Ladeart.__members__:
        fehler["standard_ladeart"] = "Bitte eine Ladeart wählen."
    return fehler


def _werte(name: str, parser_key: str, standard_ladeart: str, stromart_standard: str = "",
           cpo_standard: str = "", parser_aktiv: str = "", abrechnung_je_karte: str = "", strompreis: str = "") -> dict:
    return {"name": name.strip(), "parser_key": parser_key.strip() or None, "standard_ladeart": standard_ladeart,
            "strompreis": strompreis.strip(),
            "stromart_standard": stromart.wert(stromart_standard), "cpo_standard": cpo.normieren(cpo_standard),
            "parser_aktiv": bool(parser_aktiv), "abrechnung_je_karte": bool(abrechnung_je_karte)}


def _strompreis(werte: dict, fehler: dict) -> Decimal | None:
    """Strompreis je kWh (Vorbelegung für Importe ohne Kosten); leer = keiner."""
    try:
        preis = decimal_lesen(werte["strompreis"])
    except ValueError as e:
        fehler["strompreis"] = str(e)
        return None
    if preis is not None and not 0 <= preis < 10:
        fehler["strompreis"] = "Bitte einen Preis je kWh zwischen 0 und 10 € angeben."
    return preis


@router.get("")
def liste(request: Request, db: Session = Depends(get_db)):
    msps = db.execute(
        select(Msp, func.count(Ladekarte.id)).outerjoin(Ladekarte).group_by(Msp.id).order_by(Msp.name)).all()
    return render(request, "stammdaten/msp_liste.html", {
        "msps": msps, "fehlend": [msp for msp, _ in msps if parser_verwaltung.fehlend(msp)], "installiert": PARSER})


@router.get("/neu")
def neu_formular(request: Request, db: Session = Depends(get_db)):
    return _formular(request, None, {"standard_ladeart": "oeffentlich", "parser_aktiv": True}, db=db)


@router.post("/neu")
def neu(request: Request, name: str = Form(""), parser_key: str = Form(""),
        standard_ladeart: str = Form(""), stromart_standard: str = Form(""), cpo_standard: str = Form(""),
        parser_aktiv: str = Form(""), abrechnung_je_karte: str = Form(""), strompreis: str = Form(""),
        db: Session = Depends(get_db)):
    werte = _werte(name, parser_key, standard_ladeart, stromart_standard, cpo_standard, parser_aktiv,
                   abrechnung_je_karte, strompreis)
    fehler = _pruefen(db, werte, None)
    preis = _strompreis(werte, fehler)
    if fehler:
        return _formular(request, None, werte, fehler, 422, db=db)
    msp = Msp(**{k: v for k, v in werte.items() if k != "strompreis"}, strompreis=preis)
    db.add(msp)
    db.commit()
    meldung(request, f"MSP „{msp.name}“ angelegt.")
    return weiter(f"/stammdaten/msp/{msp.id}")


@router.get("/{msp_id}")
def bearbeiten_formular(request: Request, msp_id: int, db: Session = Depends(get_db)):
    msp = _laden(db, msp_id)
    werte = {"name": msp.name, "parser_key": msp.parser_key, "standard_ladeart": msp.standard_ladeart.value,
             "stromart_standard": msp.stromart_standard, "cpo_standard": msp.cpo_standard,
             "parser_aktiv": msp.parser_aktiv, "abrechnung_je_karte": msp.abrechnung_je_karte,
             "strompreis": zahl(msp.strompreis, 4) if msp.strompreis is not None else ""}
    return _formular(request, msp, werte, db=db)


@router.post("/{msp_id}")
def bearbeiten(request: Request, msp_id: int, name: str = Form(""), parser_key: str = Form(""),
               standard_ladeart: str = Form(""), stromart_standard: str = Form(""), cpo_standard: str = Form(""),
               parser_aktiv: str = Form(""), abrechnung_je_karte: str = Form(""), strompreis: str = Form(""),
               db: Session = Depends(get_db)):
    msp = _laden(db, msp_id)
    werte = _werte(name, parser_key, standard_ladeart, stromart_standard, cpo_standard, parser_aktiv,
                   abrechnung_je_karte, strompreis)
    fehler = _pruefen(db, werte, msp.id)
    preis = _strompreis(werte, fehler)
    if fehler:
        return _formular(request, msp, werte, fehler, 422, db=db)
    msp.strompreis = preis
    msp.name, msp.parser_key, msp.standard_ladeart = werte["name"], werte["parser_key"], Ladeart(standard_ladeart)
    msp.stromart_standard, msp.cpo_standard = werte["stromart_standard"], werte["cpo_standard"]
    msp.parser_aktiv, msp.abrechnung_je_karte = werte["parser_aktiv"], werte["abrechnung_je_karte"]
    db.commit()
    meldung(request, f"MSP „{msp.name}“ gespeichert.")
    return weiter(f"/stammdaten/msp/{msp.id}")


@router.post("/{msp_id}/cpo-anwenden")
def cpo_anwenden(request: Request, msp_id: int, db: Session = Depends(get_db)):
    """CPO-Vorgabe des MSP auf seine bestehenden Ladevorgänge ohne CPO aus der Rechnung anwenden."""
    msp = _laden(db, msp_id)
    if msp.cpo_standard is None:
        meldung(request, f"„{msp.name}“ hat keine CPO-Vorgabe.", "fehler")
    else:
        anzahl = cpo.vorgabe_anwenden(db, msp.cpo_standard, Ladevorgang.msp_id == msp.id)
        db.commit()
        meldung(request, f"{anzahl} Ladevorgänge von „{msp.name}“ auf CPO „{msp.cpo_standard}“ gesetzt.")
    return weiter(f"/stammdaten/msp/{msp.id}")


@router.post("/{msp_id}/parser-entfernen")
def parser_entfernen(request: Request, msp_id: int, db: Session = Depends(get_db)):
    """Verknüpfung zu einem nicht mehr installierten Parser entfernen – nur wenn er am MSP deaktiviert ist.
    Der MSP bleibt mit allen Ladekarten, Importen und Ladevorgängen erhalten."""
    msp = _laden(db, msp_id)
    if not parser_verwaltung.fehlend(msp):
        meldung(request, "Der Parser ist installiert – entfernt werden kann nur die Verknüpfung zu einem fehlenden Parser.",
                "fehler")
    elif msp.parser_aktiv:
        meldung(request, "Bitte den Parser zuerst deaktivieren und speichern.", "fehler")
    else:
        alt, msp.parser_key = msp.parser_key, None
        db.commit()
        meldung(request, f"Verknüpfung von „{msp.name}“ zum Parser „{alt}“ entfernt; der MSP und seine Daten bleiben.")
    return weiter(f"/stammdaten/msp/{msp.id}")


@router.post("/{msp_id}/loeschen")
def loeschen(request: Request, msp_id: int, db: Session = Depends(get_db)):
    msp = _laden(db, msp_id)
    if msp.ladekarten or db.scalar(select(Import.id).where(Import.msp_id == msp.id).limit(1)):
        meldung(request, f"MSP „{msp.name}“ hat Ladekarten oder Importe und kann nicht gelöscht werden.", "fehler")
        return weiter(f"/stammdaten/msp/{msp.id}")
    db.delete(msp)
    db.commit()
    meldung(request, f"MSP „{msp.name}“ gelöscht.")
    return weiter("/stammdaten/msp")
