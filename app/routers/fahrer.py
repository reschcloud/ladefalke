"""Stammdaten: Fahrer:innen inkl. Fahrzeug-Zuordnung."""
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import heute
from app.db import get_db
from app.formatierung import email_lesen, datum_lesen
from app.models import Fahrer, FahrerOhneFahrzeug, Fahrzeug, Ladekarte, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services import app_einstellungen, fahrzeug_hinweise, mini_zeitleiste
from app.services import kartenanzeige, kennzahlen, protokoll
from app.services.kartenanzeige import kurz as kurz_kennung
from app.services.aufloesung import neu_zuordnen_fahrer
from app.services.fristen import naechste_kontrolle
from app.services.ladevorgaenge import letzte_vorgaenge_von_fahrer
from app.services.zuordnung import (ZuordnungsFehler, bisherige_fahrer_austragen, fahrer_fahrzeug_zuordnen,
                                    fahrzeug_von_fahrer_am, gueltig_am, ohne_dienstwagen_eintragen)
from app.web import blaettern, liste_merken, meldung, render, weiter, zuordnung_vorbelegung

router = APIRouter(prefix="/stammdaten/fahrer")


def _laden(db: Session, fahrer_id: int) -> Fahrer:
    fahrer = db.get(Fahrer, fahrer_id)
    if fahrer is None:
        raise HTTPException(404, "Fahrer:in nicht gefunden")
    return fahrer


def _ist_referenziert(db: Session, fahrer_id: int) -> bool:
    return bool(db.scalar(select(func.count()).select_from(ZuordnungKarte)
                          .where(ZuordnungKarte.fahrer_id == fahrer_id))
                or db.scalar(select(func.count()).select_from(ZuordnungFahrerFahrzeug)
                             .where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer_id)))


def _pruefen(werte: dict, mit_fuehrerschein: bool = True) -> tuple[dict, dict]:
    """Formularwerte → (Spaltenwerte, Fehler je Feld). Ohne Feature Führerscheinkontrolle bleibt das Datum unverändert."""
    fehler, daten = {}, {"name": werte["name"], "aktiv": werte["aktiv"], "fuehrerschein_kontrolle": None}
    if not werte["name"]:
        fehler["name"] = "Bitte einen Namen angeben."
    try:
        daten["email"] = email_lesen(werte.get("email", ""))
    except ValueError as e:
        fehler["email"] = str(e)
    if text := werte["fuehrerschein_kontrolle"].strip():
        try:
            daten["fuehrerschein_kontrolle"] = date.fromisoformat(text)
        except ValueError:
            fehler["fuehrerschein_kontrolle"] = "Bitte ein gültiges Datum angeben."
        else:
            if daten["fuehrerschein_kontrolle"] > heute():
                fehler["fuehrerschein_kontrolle"] = "Die letzte Kontrolle kann nicht in der Zukunft liegen."
    if not mit_fuehrerschein:
        daten.pop("fuehrerschein_kontrolle")
        fehler.pop("fuehrerschein_kontrolle", None)
    return daten, fehler


def _detail(request, db, fahrer, werte, fehler=None, status_code=200, zuordnung_werte=None, zuordnung_fehler=None,
            zurueck="", ohne_werte=None, ohne_fehler=None):
    """`ohne_werte`/`ohne_fehler`: Formular „Ohne Dienstwagen“ nach einem Fehler; sonst wie „Neue Zuordnung“
    vorbelegt (z. B. aus einer Lücke der Zeitleiste – passt für beide)."""
    e = app_einstellungen.laden(db)
    zuordnung_werte = zuordnung_werte or {"ab": heute().isoformat(), "neu_zuordnen": "on"}  # Vorauswahl
    kontext = {"fahrer": fahrer, "werte": werte, "fehler": fehler or {},
               "intervall": e.fuehrerschein_intervall_monate, "fuehrerschein_an": e.feature_fuehrerschein,
               "naechste_kontrolle": naechste_kontrolle(fahrer, e) if fahrer else None,
               "zuordnung_werte": zuordnung_werte, "zuordnung_fehler": zuordnung_fehler, "zurueck": _lokal(zurueck),
               "ohne_werte": ohne_werte or {k: zuordnung_werte.get(k) for k in ("ab", "bis")},
               "ohne_fehler": ohne_fehler,
               "referenziert": fahrer is not None and _ist_referenziert(db, fahrer.id)}
    if fahrer is not None:
        kontext["fahrzeug_historie"] = db.scalars(
            select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer.id)
            .order_by(ZuordnungFahrerFahrzeug.gueltig_ab.desc())).all()
        kontext["karten_historie"] = db.scalars(
            select(ZuordnungKarte).where(ZuordnungKarte.fahrer_id == fahrer.id)
            .order_by(ZuordnungKarte.gueltig_ab.desc())).all()
        zurueck = f"?zurueck=/stammdaten/fahrer/{fahrer.id}"
        ohne_dw = db.scalars(select(FahrerOhneFahrzeug).where(FahrerOhneFahrzeug.fahrer_id == fahrer.id)).all()
        kontext["fahrzeug_mini"] = mini_zeitleiste.berechnen(
            [mini_zeitleiste.Eintrag(z.gueltig_ab, z.gueltig_bis, z.fahrzeug.kennzeichen,
                                     f"/stammdaten/fahrzeuge/{z.fahrzeug.id}",
                                     (z.fahrzeug.modell or "") + (" · Ersatz-/Mietfahrzeug" if z.fahrzeug.ersatzfahrzeug else ""),
                                     f"/stammdaten/zuordnungen/fahrer/{z.id}{zurueck}", art="auto")
             for z in kontext["fahrzeug_historie"]]
            + [mini_zeitleiste.Eintrag(o.gueltig_ab, o.gueltig_bis, "ohne Dienstwagen", zusatz=o.grund or "", stil="ohne",
                                       bearbeiten=f"/stammdaten/zuordnungen/ohne-dienstwagen/{o.id}{zurueck}")
               for o in ohne_dw], heute())  # Person ohne Fahrzeug ist keine Lücke
        karten = [mini_zeitleiste.Eintrag(z.gueltig_ab, z.gueltig_bis, z.ladekarte.msp.name,
                                          f"/stammdaten/ladekarten/{z.ladekarte.id}",
                                          f"…{kurz_kennung(z.ladekarte.kennung)} · persönlich",
                                          f"/stammdaten/ladekarten/{z.ladekarte.id}#zuordnungen", art="karte",
                                          bearbeiten_text="bei Karte ändern →")
                  for z in kontext["karten_historie"]]
        # dazu die Karten der Fahrzeuge, die die Person gefahren ist – für die gemeinsame Zeit
        fz_ids = {z.fahrzeug_id for z in kontext["fahrzeug_historie"]}
        fz_karten = db.scalars(select(ZuordnungKarte).where(ZuordnungKarte.fahrzeug_id.in_(fz_ids))).all() if fz_ids else []
        for zf in kontext["fahrzeug_historie"]:
            for zk in fz_karten:
                if zk.fahrzeug_id != zf.fahrzeug_id:
                    continue
                ab = max(zf.gueltig_ab, zk.gueltig_ab)
                enden = [d for d in (zf.gueltig_bis, zk.gueltig_bis) if d]
                bis = min(enden) if enden else None
                if bis is not None and bis < ab:
                    continue
                karten.append(mini_zeitleiste.Eintrag(
                    ab, bis, f"{zk.ladekarte.msp.name} · {zf.fahrzeug.kennzeichen}",
                    f"/stammdaten/ladekarten/{zk.ladekarte.id}",
                    f"…{kurz_kennung(zk.ladekarte.kennung)} · über Fahrzeug {zf.fahrzeug.kennzeichen}",
                    f"/stammdaten/ladekarten/{zk.ladekarte.id}#zuordnungen", art="karte",
                    bearbeiten_text="bei Karte ändern →"))
        kontext["karten_mini"] = mini_zeitleiste.berechnen(karten, heute())
        kontext["karten_auswahl"] = kartenanzeige.zuordenbare_karten(db)
        kontext["fahrzeuge"] = db.scalars(
            select(Fahrzeug).where(Fahrzeug.aktiv).order_by(Fahrzeug.kennzeichen)).all()
        kontext["vorgaenge"] = letzte_vorgaenge_von_fahrer(db, fahrer.id)
    kontext["verlauf"] = protokoll.verlauf(request, db, "fahrer", fahrer.id if fahrer else None)
    kontext["gesamt"] = kennzahlen.fahrer(db, fahrer.id) if fahrer else None
    kontext["offen"] = fahrzeug_hinweise.fuer_fahrer([fahrer], e).get(fahrer.id, ([], ""))[0] if fahrer else []
    kontext["blaettern"] = blaettern(
        request, db, "fahrer", "/stammdaten/fahrer", lambda **f: _abfrage(Fahrer.id, Fahrer.name, **f),
        fahrer.id, alle={"status": "alle"}) if fahrer else None
    return render(request, "stammdaten/fahrer_detail.html", kontext, status_code)


def _abfrage(*spalten, status: str = "aktiv"):
    """Fahrer:innen der Liste in deren Reihenfolge (auch fürs Blättern in der Detailseite)."""
    q = select(*spalten).order_by(Fahrer.name, Fahrer.id)
    return q if status == "alle" else q.where(Fahrer.aktiv == (status == "aktiv"))


@router.get("")
def liste(request: Request, status: str = "aktiv", db: Session = Depends(get_db)):
    q = _abfrage(Fahrer, status=status)
    liste_merken(request, "fahrer", status=status)
    stichtag = heute()
    zeilen = [(f, fahrzeug_von_fahrer_am(db, f.id, stichtag)) for f in db.scalars(q)]
    e = app_einstellungen.laden(db)
    # Ladekarten heute: persönliche (Karte → Person) und die des aktuellen Fahrzeugs, je als MSP-Namen
    persoenlich: dict[int, set[str]] = {}
    am_fahrzeug: dict[int, set[str]] = {}
    for z in db.scalars(select(ZuordnungKarte).where(gueltig_am(ZuordnungKarte, stichtag))
                        .options(selectinload(ZuordnungKarte.ladekarte).selectinload(Ladekarte.msp))):
        ziel = persoenlich.setdefault(z.fahrer_id, set()) if z.fahrer_id else am_fahrzeug.setdefault(z.fahrzeug_id, set())
        ziel.add(z.ladekarte.msp.name)
    karten_msp = {f.id: (sorted(persoenlich.get(f.id, ())),
                         sorted(am_fahrzeug.get(fz.id, set()) - persoenlich.get(f.id, set())) if fz else [],
                         fz.kennzeichen if fz else None)
                  for f, fz in zeilen}
    return render(request, "stammdaten/fahrer_liste.html",
                  {"hinweise": fahrzeug_hinweise.fuer_fahrer([f for f, _ in zeilen], e), "karten_msp": karten_msp,
                   "zeilen": zeilen, "status": status, "fuehrerschein_an": e.feature_fuehrerschein})


@router.get("/neu")
def neu_formular(request: Request, db: Session = Depends(get_db)):
    return _detail(request, db, None, {"aktiv": True})


@router.post("/neu")
def neu(request: Request, name: str = Form(""), aktiv: str | None = Form(None),
        fuehrerschein_kontrolle: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    werte = {"name": name.strip(), "aktiv": aktiv is not None, "fuehrerschein_kontrolle": fuehrerschein_kontrolle,
             "email": email.strip()}
    daten, fehler = _pruefen(werte, app_einstellungen.laden(db).feature_fuehrerschein)
    if fehler:
        return _detail(request, db, None, werte, fehler, 422)
    fahrer = Fahrer(**daten)
    db.add(fahrer)
    db.commit()
    meldung(request, f"Fahrer:in {fahrer.name} angelegt.")
    return weiter(f"/stammdaten/fahrer/{fahrer.id}")


def _lokal(ziel: str) -> str:
    """Rücksprungziel nur als lokaler Pfad (sonst leer)."""
    return ziel if ziel.startswith("/") and not ziel.startswith("//") else ""


@router.get("/zuordnen")
def zuordnen_auswahl(fahrer_id: str = "", fahrzeug_id: str = "", ab: str = "", bis: str = "", zurueck: str = ""):
    """Von der Fahrzeugseite („Fahrer:in zuordnen →“): gewählte Person mit vorbelegtem Fahrzeug öffnen, danach
    zurück. Zugeordnet wird nur bei der Person – dort sind ihre übrigen Fahrzeuge (mögliche Überschneidungen) sichtbar."""
    if not fahrer_id.isdigit():
        return weiter(_lokal(zurueck) or "/stammdaten/fahrer")
    parameter = {"fahrzeug": fahrzeug_id, "ab": ab, "bis": bis, "zurueck": _lokal(zurueck)}
    return weiter(f"/stammdaten/fahrer/{fahrer_id}?" + urlencode({k: v for k, v in parameter.items() if v})
                  + "#neue-fahrzeug-zuordnung")


@router.get("/{fahrer_id}")
def detail(request: Request, fahrer_id: int, ab: str = "", bis: str = "", fahrzeug: str = "", zurueck: str = "",
           db: Session = Depends(get_db)):
    """`ab`/`bis`: Vorbelegung „Neue Zuordnung“ (z. B. aus einer Lücke in der Zeitleiste); `fahrzeug`: vorbelegtes
    Fahrzeug, `zurueck`: nach dem Zuordnen dorthin (beides von „Fahrer:in zuordnen →“ auf der Fahrzeugseite)."""
    fahrer = _laden(db, fahrer_id)
    zuordnung_werte = zuordnung_vorbelegung(ab, bis) if ab else None
    if fahrzeug.isdigit():
        zuordnung_werte = (zuordnung_werte or {"ab": heute().isoformat(), "neu_zuordnen": "on"}) | {"fahrzeug_id": fahrzeug}
    return _detail(request, db, fahrer, _werte(fahrer), zuordnung_werte=zuordnung_werte, zurueck=zurueck)


def _werte(fahrer) -> dict:
    """Gespeicherte Stammdaten fürs Formular (auch, wenn ein anderes Formular der Seite mit Fehler zurückkommt)."""
    return {"name": fahrer.name, "aktiv": fahrer.aktiv, "email": fahrer.email or "",
            "fuehrerschein_kontrolle": fahrer.fuehrerschein_kontrolle.isoformat() if fahrer.fuehrerschein_kontrolle else ""}


@router.post("/{fahrer_id}")
def bearbeiten(request: Request, fahrer_id: int, name: str = Form(""), aktiv: str | None = Form(None),
               fuehrerschein_kontrolle: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    fahrer = _laden(db, fahrer_id)
    werte = {"name": name.strip(), "aktiv": aktiv is not None, "fuehrerschein_kontrolle": fuehrerschein_kontrolle,
             "email": email.strip()}
    daten, fehler = _pruefen(werte, app_einstellungen.laden(db).feature_fuehrerschein)
    if fehler:
        return _detail(request, db, fahrer, werte, fehler, 422)
    for feld, wert in daten.items():
        setattr(fahrer, feld, wert)
    db.commit()
    meldung(request, f"Fahrer:in {fahrer.name} gespeichert.")
    return weiter(f"/stammdaten/fahrer/{fahrer.id}")


def _pool_hinweis(request, db: Session, fahrer: Fahrer, fahrzeug: Fahrzeug, ab, bis, ziel: str) -> None:
    """Hat das Fahrzeug im neuen Zeitraum noch andere Fahrer:innen, ist es jetzt ein Poolfahrzeug. Das kann gewollt
    sein (Haken „Fahrerwechsel“ nicht gesetzt); die Meldung bietet das Austragen zum Vortag nachträglich an, sofern
    alle anderen Zuordnungen vorher beginnen. `ziel`: Seite, auf die es danach zurückgeht."""
    andere = db.scalars(select(ZuordnungFahrerFahrzeug).where(
        ZuordnungFahrerFahrzeug.fahrzeug_id == fahrzeug.id, ZuordnungFahrerFahrzeug.fahrer_id != fahrer.id,
        or_(ZuordnungFahrerFahrzeug.gueltig_bis.is_(None), ZuordnungFahrerFahrzeug.gueltig_bis >= ab),
        *([ZuordnungFahrerFahrzeug.gueltig_ab <= bis] if bis else []))).all()
    if not andere:
        return
    namen = ", ".join(sorted({z.fahrer.name for z in andere}))
    text = f"{fahrzeug.kennzeichen} hat damit {len({z.fahrer_id for z in andere}) + 1} Fahrer:innen (Pool) – außerdem {namen}."
    aktion = None
    if not all(z.gueltig_ab < ab for z in andere):
        text += " Bei einem Fahrerwechsel bitte die Zuordnungen bei den anderen Personen anpassen."
    else:
        text += f" Bei einem Fahrerwechsel die bisherigen zum {ab - timedelta(days=1):%d.%m.%Y} austragen:"
        aktion = {"url": f"/stammdaten/fahrzeuge/{fahrzeug.id}/bisherige-austragen", "text": "Bisherige austragen",
                  "felder": {"ab": ab.isoformat(), "fahrer_id": fahrer.id, "zurueck": ziel}}
    meldung(request, text, "info", aktion)


@router.post("/{fahrer_id}/fahrzeug")
def fahrzeug_zuordnen(request: Request, fahrer_id: int, fahrzeug_id: str = Form(""), ab: str = Form(""),
                      bis: str = Form(""), neu_zuordnen: str | None = Form(None),
                      bisherige_austragen: str | None = Form(None), zurueck: str = Form(""),
                      db: Session = Depends(get_db)):
    """Fahrzeug zuordnen – die einzige Stelle für neue Zuordnungen Person ↔ Fahrzeug. Optional Fahrerwechsel:
    die bisherigen Fahrer:innen des Fahrzeugs zum Vortag austragen."""
    fahrer = _laden(db, fahrer_id)
    zuordnung_werte = {"fahrzeug_id": fahrzeug_id, "ab": ab, "bis": bis, "neu_zuordnen": neu_zuordnen,
                       "bisherige_austragen": bisherige_austragen}
    ausgetragen = []
    try:
        fahrzeug = db.get(Fahrzeug, int(fahrzeug_id)) if fahrzeug_id.isdigit() else None
        if fahrzeug is None:
            raise ZuordnungsFehler("Bitte ein Fahrzeug wählen.")
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        bis_datum = datum_lesen(bis)
        hinweise = []
        if bisherige_austragen:
            ausgetragen, hinweise = bisherige_fahrer_austragen(db, fahrzeug, ab_datum, fahrer.id)
        _, weitere = fahrer_fahrzeug_zuordnen(db, fahrer, fahrzeug, ab=ab_datum, bis=bis_datum, heute=heute())
        hinweise += weitere
    except ValueError as e:  # ZuordnungsFehler oder ungültiges Datum
        db.rollback()
        return _detail(request, db, fahrer, _werte(fahrer), status_code=422,
                       zuordnung_werte=zuordnung_werte, zuordnung_fehler=str(e), zurueck=zurueck)
    if neu_zuordnen:
        n = neu_zuordnen_fahrer(db, fahrer.id, ab_datum, bis_datum)
        n += sum(neu_zuordnen_fahrer(db, fid, ab_datum, None) for fid in ausgetragen)
        neu_info = f"{n} bereits importierte Ladevorgänge wurden neu zugeordnet."
    db.commit()
    meldung(request, f"{fahrer.name} ist ab {ab_datum:%d.%m.%Y} Fahrzeug {fahrzeug.kennzeichen} zugeordnet.")
    if neu_zuordnen:
        meldung(request, neu_info)
    for h in hinweise:
        meldung(request, h, "warnung")
    ziel = _lokal(zurueck) or f"/stammdaten/fahrer/{fahrer.id}"
    if not bisherige_austragen:
        _pool_hinweis(request, db, fahrer, fahrzeug, ab_datum, bis_datum, ziel)
    return weiter(ziel)


@router.post("/{fahrer_id}/ohne-dienstwagen")
def ohne_dienstwagen(request: Request, fahrer_id: int, ab: str = Form(""), bis: str = Form(""), grund: str = Form(""),
                     zurueck: str = Form(""), db: Session = Depends(get_db)):
    """Zeitraum „ohne Dienstwagen“ eintragen: Ladungen mit der persönlichen Karte sind dann begründet ohne Fahrzeug
    (keine Aufgabe; in den Auswertungen „ohne Dienstwagen“). Vom Formular der Personenseite bzw. aus den Aufgaben."""
    fahrer = _laden(db, fahrer_id)
    ziel = _lokal(zurueck) or f"/stammdaten/fahrer/{fahrer.id}#fahrzeug"
    try:
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        bis_datum = datum_lesen(bis)
        hinweise = ohne_dienstwagen_eintragen(db, fahrer, ab=ab_datum, bis=bis_datum, grund=grund)
    except ValueError as e:
        db.rollback()  # Formular mit Fehler und den Eingaben neu zeigen (422 tauscht htmx ein)
        return _detail(request, db, fahrer, _werte(fahrer), status_code=422, zurueck=zurueck,
                       ohne_werte={"ab": ab, "bis": bis, "grund": grund}, ohne_fehler=str(e))
    db.commit()
    von_bis = f"ab {ab_datum:%d.%m.%Y}" if bis_datum is None else f"vom {ab_datum:%d.%m.%Y} bis {bis_datum:%d.%m.%Y}"
    meldung(request, f"{fahrer.name} ist {von_bis} ohne Dienstwagen – Ladungen mit der persönlichen Karte sind in "
                     "dieser Zeit begründet ohne Fahrzeug.")
    for h in hinweise:
        meldung(request, h, "warnung")
    return weiter(ziel)


@router.post("/{fahrer_id}/aktivieren")
def aktivieren(request: Request, fahrer_id: int, db: Session = Depends(get_db)):
    obj = _laden(db, fahrer_id)
    obj.aktiv = True
    db.commit()
    meldung(request, f"{obj.name} ist wieder aktiv.")
    return weiter(f"/stammdaten/fahrer/{obj.id}")


@router.post("/{fahrer_id}/loeschen")
def loeschen(request: Request, fahrer_id: int, db: Session = Depends(get_db)):
    fahrer = _laden(db, fahrer_id)
    if _ist_referenziert(db, fahrer.id):
        fahrer.aktiv = False
        db.commit()
        meldung(request, f"{fahrer.name} hat Zuordnungen und wurde deaktiviert statt gelöscht.", "warnung")
        return weiter(f"/stammdaten/fahrer/{fahrer.id}")
    db.delete(fahrer)
    db.commit()
    meldung(request, f"Fahrer:in {fahrer.name} gelöscht.")
    return weiter("/stammdaten/fahrer")
