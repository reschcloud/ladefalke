"""Stammdaten: Fahrzeuge."""
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import heute
from app.db import get_db
from app.models import Fahrer, Fahrzeug, FahrzeugOhneFahrer, Ladevorgang, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services import app_einstellungen, fahrzeug_hinweise, kosten, mini_zeitleiste
from app.services import kartenanzeige, kartenpruefung, kennzahlen, leasingpruefung, protokoll
from app.services.kartenanzeige import kurz as kurz_kennung
from app.services.fristen import jahresvignette, leasing_ende_vorschlag
from app.services.ladeprofil import ohne_angabe, schaetzgrundlage, unplausible_anzahl
from app.services.ladevorgaenge import letzte_vorgaenge
from app.services.schaetzung import MINDESTANZAHL
from app.formatierung import datum_lesen, decimal_lesen, zahl, zeitraum
from app.routers.ladevorgaenge import mehrfach_bedingung
from app.services.aufloesung import neu_zuordnen_fahrer
from app.services.zuordnung import (ZuordnungsFehler, bisherige_fahrer_austragen, fahrer_von_fahrzeug_am,
                                    gueltig_am, ohne_fahrer_eintragen)
from app.web import blaettern, liste_merken, meldung, render, weiter, zuordnung_vorbelegung

router = APIRouter(prefix="/stammdaten/fahrzeuge")


def _laden(db: Session, fahrzeug_id: int) -> Fahrzeug:
    fz = db.get(Fahrzeug, fahrzeug_id)
    if fz is None:
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    return fz


MAUT_FELDER = ("vignette_ab", "vignette_bis", "streckenmaut_ab", "streckenmaut_bis")


def _werte(kennzeichen: str, modell: str, notiz: str, aktiv: str | None, leasing_beginn: str = "",
           leasing_ende: str = "", maut: dict | None = None) -> dict:
    """`maut`: weitere Formularwerte – Vignette und Streckenmaut FLEX (nur gesendet, wenn das Feature aktiv ist) und
    der Vorsteuerabzug."""
    maut = maut or {}
    return {"kennzeichen": " ".join(kennzeichen.upper().split()), "modell": modell.strip() or None,
            "vorsteuer_prozent": (maut.get("vorsteuer_prozent") or "100").strip(),
            "notiz": notiz.strip() or None, "aktiv": aktiv is not None,
            "leasing_beginn": leasing_beginn.strip(), "leasing_ende": leasing_ende.strip(),
            "vignette_noetig": bool(maut.get("vignette_noetig")), "streckenmaut_noetig": bool(maut.get("streckenmaut_noetig")),
            **{f: (maut.get(f) or "").strip() for f in MAUT_FELDER}}


def _werte_von(fz: Fahrzeug) -> dict:
    return {"kennzeichen": fz.kennzeichen, "modell": fz.modell, "notiz": fz.notiz, "aktiv": fz.aktiv,
            "leasing_beginn": fz.leasing_beginn.isoformat() if fz.leasing_beginn else "",
            "leasing_ende": fz.leasing_ende.isoformat() if fz.leasing_ende else "",
            "vorsteuer_prozent": zahl(fz.vorsteuer_prozent, 0 if fz.vorsteuer_prozent % 1 == 0 else 2),
            "vignette_noetig": fz.vignette_noetig, "streckenmaut_noetig": fz.streckenmaut_noetig,
            **{f: getattr(fz, f).isoformat() if getattr(fz, f) else "" for f in MAUT_FELDER}}


def _daten(db: Session, werte: dict, fehler: dict) -> dict:
    """Formularwerte → Spaltenwerte; Leasing-Ende ohne Angabe = Beginn + Leasingdauer (Einstellungen)."""
    daten = {k: werte[k] for k in ("kennzeichen", "modell", "notiz", "aktiv")}
    try:
        prozent = decimal_lesen(werte["vorsteuer_prozent"])
        if prozent is None or not 0 <= prozent <= 100:
            raise ValueError
        daten["vorsteuer_prozent"] = prozent
    except ValueError:
        fehler["vorsteuer_prozent"] = "Bitte einen Wert zwischen 0 und 100 % angeben."
    for feld in ("leasing_beginn", "leasing_ende"):
        try:
            daten[feld] = date.fromisoformat(werte[feld]) if werte[feld] else None
        except ValueError:
            fehler[feld] = "Bitte ein gültiges Datum angeben."
    if fehler:
        return daten
    if daten["leasing_beginn"] and not daten["leasing_ende"]:
        daten["leasing_ende"] = leasing_ende_vorschlag(daten["leasing_beginn"],
                                                       app_einstellungen.laden(db).leasing_dauer_monate)
    if daten["leasing_beginn"] and daten["leasing_ende"] and daten["leasing_ende"] < daten["leasing_beginn"]:
        fehler["leasing_ende"] = "Das Leasing-Ende liegt vor dem Beginn."
    # Vignette / Streckenmaut FLEX: nur übernehmen, wenn das Feature aktiv ist (sonst bleiben die Daten unverändert)
    e = app_einstellungen.laden(db)
    for art, aktiv in (("vignette", e.feature_vignette), ("streckenmaut", e.feature_streckenmaut)):
        if not aktiv:
            continue
        daten[f"{art}_noetig"] = werte[f"{art}_noetig"]
        for teil in ("ab", "bis"):
            feld = f"{art}_{teil}"
            try:
                daten[feld] = date.fromisoformat(werte[feld]) if werte[feld] else None
            except ValueError:
                fehler[feld] = "Bitte ein gültiges Datum angeben."
        if daten.get(f"{art}_ab") and daten.get(f"{art}_bis") and daten[f"{art}_bis"] < daten[f"{art}_ab"]:
            fehler[f"{art}_bis"] = "Das Ende liegt vor dem Beginn."
    return daten


def _pruefen(db: Session, werte: dict, fahrzeug_id: int | None) -> dict:
    """Liefert die Fehler je Feld."""
    fehler = {}
    if not werte["kennzeichen"]:
        fehler["kennzeichen"] = "Bitte ein Kennzeichen angeben."
    elif db.scalar(select(Fahrzeug.id).where(Fahrzeug.kennzeichen == werte["kennzeichen"],
                                             Fahrzeug.id != (fahrzeug_id or 0))):
        fehler["kennzeichen"] = "Ein Fahrzeug mit diesem Kennzeichen existiert bereits."
    return fehler


def _ist_referenziert(db: Session, fahrzeug_id: int) -> bool:
    return bool(db.scalar(select(func.count()).select_from(ZuordnungKarte)
                          .where(ZuordnungKarte.fahrzeug_id == fahrzeug_id))
                or db.scalar(select(func.count()).select_from(ZuordnungFahrerFahrzeug)
                             .where(ZuordnungFahrerFahrzeug.fahrzeug_id == fahrzeug_id))
                or db.scalar(select(func.count()).select_from(Ladevorgang)
                             .where(Ladevorgang.fahrzeug_id == fahrzeug_id)))


def _detail(request, db, fz, werte, fehler=None, status_code=200, zuordnung_werte=None, zuordnung_fehler=None):
    e = app_einstellungen.laden(db)
    jahr = heute().year
    kontext = {"fahrzeug": fz, "werte": werte, "fehler": fehler or {},
               "leasing_monate": e.leasing_dauer_monate, "einst": e,
               "jahresvignetten": [(j, *jahresvignette(j)) for j in (jahr, jahr + 1)],
               "zuordnung_werte": zuordnung_werte or {"ab": heute().isoformat(), "neu_zuordnen": "on"},  # Vorauswahl
               "zuordnung_fehler": zuordnung_fehler, "referenziert": fz is not None and _ist_referenziert(db, fz.id)}
    if fz is not None:
        kontext["fahrer_liste"] = db.scalars(select(Fahrer).where(Fahrer.aktiv).order_by(Fahrer.name)).all()
        kontext["fahrer_historie"] = db.scalars(
            select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrzeug_id == fz.id)
            .order_by(ZuordnungFahrerFahrzeug.gueltig_ab, ZuordnungFahrerFahrzeug.id)).all()  # alt → neu, aktuell unten
        kontext["karten_historie"] = db.scalars(
            select(ZuordnungKarte).where(ZuordnungKarte.fahrzeug_id == fz.id)
            .order_by(ZuordnungKarte.gueltig_ab.desc())).all()
        zurueck = f"?zurueck=/stammdaten/fahrzeuge/{fz.id}"
        ohne = db.scalars(select(FahrzeugOhneFahrer).where(FahrzeugOhneFahrer.fahrzeug_id == fz.id)).all()
        # Zuordnungen werden dort geändert, wo ihre Regel gilt: Person ↔ Fahrzeug bei der Person (eine Person hat
        # höchstens ein Fahrzeug), Karte → Fahrzeug bei der Karte; hier nur „ohne Fahrer:in“ (gehört zum Fahrzeug)
        fahrer_eintraege = (
            [mini_zeitleiste.Eintrag(z.gueltig_ab, z.gueltig_bis, z.fahrer.name, f"/stammdaten/fahrer/{z.fahrer.id}",
                                     bearbeiten=f"/stammdaten/fahrer/{z.fahrer.id}#fahrzeug", art="person",
                                     bearbeiten_text="bei Person ändern →")
             for z in kontext["fahrer_historie"]]
            + [mini_zeitleiste.Eintrag(o.gueltig_ab, o.gueltig_bis, "ohne Fahrer:in", stil="ohne",
                                       bearbeiten=f"/stammdaten/zuordnungen/ohne/{o.id}{zurueck}") for o in ohne])
        karten_eintraege = [
            mini_zeitleiste.Eintrag(z.gueltig_ab, z.gueltig_bis, z.ladekarte.msp.name,
                                    f"/stammdaten/ladekarten/{z.ladekarte.id}", f"…{kurz_kennung(z.ladekarte.kennung)}",
                                    f"/stammdaten/ladekarten/{z.ladekarte.id}#zuordnungen", art="karte",
                                    bearbeiten_text="bei Karte ändern →")
            for z in kontext["karten_historie"]]
        # Zeitleiste des Fahrzeugs: Fahrer:innen und Karten auf gemeinsamer Achse, Befunde der Kartenprüfung markiert
        achse = mini_zeitleiste.bereich(fahrer_eintraege + karten_eintraege, heute(), fz.leasing_beginn, fz.leasing_ende)
        kontext["fahrer_mini"] = mini_zeitleiste.berechnen(fahrer_eintraege, heute(), fz.leasing_beginn,
                                                           fz.leasing_ende, "warnung", achse=achse)
        kontext["karten_auswahl"] = kartenanzeige.zuordenbare_karten(db)
        kontext["karten_mini"] = mini_zeitleiste.berechnen(karten_eintraege, heute(), fz.leasing_beginn,
                                                           fz.leasing_ende, achse=achse)
        kontext["fahrer_markierungen"] = mini_zeitleiste.markierungen(kontext["fahrer_mini"], [
            (w.von, w.bis, "gelb", w.text) for w in leasingpruefung.ermitteln(db, e, [fz]).get(fz.id, [])
            if w.art == "fahrer"])
        kontext["karten_markierungen"] = mini_zeitleiste.markierungen(kontext["karten_mini"], [
            (b.von, b.bis, b.stufe, f"{b.text} – {b.erklaerung}") for b in kartenpruefung.ermitteln(db, e, fahrzeuge=[fz])])
        kontext["vorgaenge"] = letzte_vorgaenge(db, Ladevorgang.fahrzeug_id == fz.id)
        grundlage = schaetzgrundlage(db)
        kontext["profil"] = grundlage.fahrzeuge.get(fz.id, {})
        kontext["schaetzwerte"] = grundlage.fuer(fz.id)
        kontext["mindestanzahl"] = MINDESTANZAHL
        kontext["profil_ohne_angabe"] = ohne_angabe(db, fz.id)
        kontext["profil_unplausibel"] = unplausible_anzahl(db, fz.id)
    kontext["verlauf"] = protokoll.verlauf(request, db, "fahrzeug", fz.id if fz else None)
    kontext["gesamt"] = kennzahlen.fahrzeug(db, fz.id) if fz else None
    kontext["offen"] = fahrzeug_hinweise.ermitteln(db, [fz], e).get(fz.id, []) if fz else []
    kontext["blaettern"] = blaettern(
        request, db, "fahrzeuge", "/stammdaten/fahrzeuge",
        lambda **f: _abfrage(Fahrzeug.id, Fahrzeug.kennzeichen, **f), fz.id, alle={"status": "alle"}) if fz else None
    return render(request, "stammdaten/fahrzeug_detail.html", kontext, status_code)


def _abfrage(*spalten, status: str = "aktiv", modell: list[str] = ()):
    """Fahrzeuge der Liste in deren Reihenfolge (auch fürs Blättern in der Detailseite)."""
    q = select(*spalten).order_by(Fahrzeug.kennzeichen, Fahrzeug.id)
    if status != "alle":
        q = q.where(Fahrzeug.aktiv == (status == "aktiv"))
    if (b := mehrfach_bedingung(Fahrzeug.modell, list(modell))) is not None:
        q = q.where(b)
    return q


@router.get("")
def liste(request: Request, status: str = "aktiv", modell: list[str] = Query([]), db: Session = Depends(get_db)):
    """`modell`: nur diese Modelle (Mehrfachauswahl); „-“ = Fahrzeuge ohne Modell."""
    modell = [m for m in modell if m]
    q = _abfrage(Fahrzeug, status=status, modell=modell)
    liste_merken(request, "fahrzeuge", status=status, modell=modell)
    modelle = db.scalars(select(Fahrzeug.modell).where(Fahrzeug.modell.is_not(None)).distinct()
                         .order_by(Fahrzeug.modell)).all()
    stichtag = heute()
    zeilen = [(fz, fahrer_von_fahrzeug_am(db, fz.id, stichtag)) for fz in db.scalars(q)]
    ohne_heute = set(db.scalars(select(FahrzeugOhneFahrer.fahrzeug_id).where(gueltig_am(FahrzeugOhneFahrer, stichtag))))
    return render(request, "stammdaten/fahrzeug_liste.html", {
        "zeilen": zeilen, "status": status, "ohne_heute": ohne_heute, "modell": modell, "modell_paare": [("modell", m) for m in modell],
        "modell_optionen": [(x, x) for x in modelle] + [("-", "ohne Modell")],
        "hinweise": fahrzeug_hinweise.ermitteln(db, [fz for fz, _ in zeilen], app_einstellungen.laden(db))})


@router.get("/neu")
def neu_formular(request: Request, db: Session = Depends(get_db)):
    return _detail(request, db, None, {"aktiv": True, "vignette_noetig": True, "streckenmaut_noetig": False})


async def _maut_formular(request: Request) -> dict:
    formular = await request.form()
    return {k: formular.get(k) for k in ("vignette_noetig", "streckenmaut_noetig", "vorsteuer_prozent", *MAUT_FELDER)}


@router.post("/neu")
async def neu(request: Request, kennzeichen: str = Form(""), modell: str = Form(""), notiz: str = Form(""),
              aktiv: str | None = Form(None), leasing_beginn: str = Form(""), leasing_ende: str = Form(""),
              db: Session = Depends(get_db)):
    werte = _werte(kennzeichen, modell, notiz, aktiv, leasing_beginn, leasing_ende, await _maut_formular(request))
    fehler = _pruefen(db, werte, None)
    daten = _daten(db, werte, fehler)
    if fehler:
        return _detail(request, db, None, werte, fehler, 422)
    fz = Fahrzeug(**daten)
    db.add(fz)
    db.commit()
    meldung(request, f"Fahrzeug {fz.kennzeichen} angelegt.")
    return weiter(f"/stammdaten/fahrzeuge/{fz.id}")


@router.get("/{fahrzeug_id}")
def detail(request: Request, fahrzeug_id: int, ab: str = "", bis: str = "", db: Session = Depends(get_db)):
    """`ab`/`bis`: Vorbelegung „Neue Zuordnung“ (z. B. aus einer Lücke in der Zeitleiste)."""
    fz = _laden(db, fahrzeug_id)
    return _detail(request, db, fz, _werte_von(fz), zuordnung_werte=zuordnung_vorbelegung(ab, bis) if ab else None)


@router.post("/{fahrzeug_id}")
async def bearbeiten(request: Request, fahrzeug_id: int, kennzeichen: str = Form(""), modell: str = Form(""),
                     notiz: str = Form(""), aktiv: str | None = Form(None), leasing_beginn: str = Form(""),
                     leasing_ende: str = Form(""), db: Session = Depends(get_db)):
    fz = _laden(db, fahrzeug_id)
    werte = _werte(kennzeichen, modell, notiz, aktiv, leasing_beginn, leasing_ende, await _maut_formular(request))
    fehler = _pruefen(db, werte, fz.id)
    daten = _daten(db, werte, fehler)
    if fehler:
        return _detail(request, db, fz, werte, fehler, 422)
    vorher = fz.vorsteuer_prozent
    for feld, wert in daten.items():
        setattr(fz, feld, wert)
    if fz.vorsteuer_prozent != vorher:
        kosten.neu_berechnen(db, fz.id)  # tatsächliche Kosten der Ladevorgänge dieses Fahrzeugs
    db.commit()
    meldung(request, f"Fahrzeug {fz.kennzeichen} gespeichert.")
    return weiter(f"/stammdaten/fahrzeuge/{fz.id}")


@router.post("/{fahrzeug_id}/bisherige-austragen")
def bisherige_austragen_route(request: Request, fahrzeug_id: int, ab: str = Form(""), fahrer_id: str = Form(""),
                              zurueck: str = Form(""), db: Session = Depends(get_db)):
    """Fahrerwechsel nachträglich (Pool-Hinweis nach einer Zuordnung von der Personenseite): alle anderen
    Fahrer:innen zum Vortag von `ab` austragen und deren Ladevorgänge neu zuordnen."""
    fz = _laden(db, fahrzeug_id)
    ziel = zurueck if zurueck.startswith("/") and not zurueck.startswith("//") else f"/stammdaten/fahrzeuge/{fz.id}"
    try:
        ab_datum = datum_lesen(ab)
        if ab_datum is None or not fahrer_id.isdigit():
            raise ZuordnungsFehler("Unvollständige Angaben.")
        ausgetragen, hinweise = bisherige_fahrer_austragen(db, fz, ab_datum, int(fahrer_id))
    except ValueError as e:
        db.rollback()
        meldung(request, str(e), "fehler")
        return weiter(ziel)
    n = sum(neu_zuordnen_fahrer(db, fid, ab_datum, None) for fid in ausgetragen)
    db.commit()
    for h in hinweise:
        meldung(request, h)
    if n:
        meldung(request, f"{n} bereits importierte Ladevorgänge wurden neu zugeordnet.")
    return weiter(ziel)


@router.post("/{fahrzeug_id}/ohne-fahrer")
def ohne_fahrer(request: Request, fahrzeug_id: int, ab: str = Form(""), bis: str = Form(""), zurueck: str = Form(""),
                bisherige_austragen: str | None = Form(None), neu_zuordnen: str | None = Form(None),
                db: Session = Depends(get_db)):
    """Zeitraum „ohne Fahrer:in“ eintragen: bewusst niemandem zugeordnet (Werkstatt, Reserve), dann keine Lücke.
    Vom Knopf an einer Lücke (Zuordnungs-Abschnitt, Aufgaben) oder vom Formular auf der Fahrzeugseite – dort optional
    die bisherigen Fahrer:innen zum Vortag austragen und deren Ladevorgänge neu zuordnen."""
    fz = _laden(db, fahrzeug_id)
    ziel = zurueck if zurueck.startswith("/") and not zurueck.startswith("//") else f"/stammdaten/fahrzeuge/{fz.id}"
    try:
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        bis_datum = datum_lesen(bis)
        ausgetragen, hinweise = ohne_fahrer_eintragen(db, fz, ab=ab_datum, bis=bis_datum,
                                                      bisherige_austragen=bool(bisherige_austragen))
    except ValueError as e:
        db.rollback()
        meldung(request, str(e), "fehler")
        return weiter(ziel)
    n = sum(neu_zuordnen_fahrer(db, fid, ab_datum, None) for fid in ausgetragen) if neu_zuordnen else 0
    db.commit()
    meldung(request, f"Fahrzeug {fz.kennzeichen} ist {zeitraum(ab_datum, bis_datum)} ohne Fahrer:in.")
    if n:
        meldung(request, f"{n} bereits importierte Ladevorgänge wurden neu zugeordnet.")
    for h in hinweise:
        meldung(request, h, "warnung")
    return weiter(ziel)


@router.post("/{fahrzeug_id}/aktivieren")
def aktivieren(request: Request, fahrzeug_id: int, db: Session = Depends(get_db)):
    obj = _laden(db, fahrzeug_id)
    obj.aktiv = True
    db.commit()
    meldung(request, f"{f"Fahrzeug {obj.kennzeichen}"} ist wieder aktiv.")
    return weiter(f"/stammdaten/fahrzeuge/{obj.id}")


@router.post("/{fahrzeug_id}/loeschen")
def loeschen(request: Request, fahrzeug_id: int, db: Session = Depends(get_db)):
    fz = _laden(db, fahrzeug_id)
    if _ist_referenziert(db, fz.id):
        fz.aktiv = False
        db.commit()
        meldung(request, f"Fahrzeug {fz.kennzeichen} wird verwendet und wurde deaktiviert statt gelöscht.",
                "warnung")
        return weiter(f"/stammdaten/fahrzeuge/{fz.id}")
    db.delete(fz)
    db.commit()
    meldung(request, f"Fahrzeug {fz.kennzeichen} gelöscht.")
    return weiter("/stammdaten/fahrzeuge")
