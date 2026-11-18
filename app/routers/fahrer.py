"""Stammdaten: Fahrer:innen inkl. Fahrzeug-Zuordnung."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import heute
from app.db import get_db
from app.formatierung import email_lesen, datum_lesen
from app.models import Fahrer, Fahrzeug, Ladekarte, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services import app_einstellungen, fahrzeug_hinweise, mini_zeitleiste
from app.services import kartenanzeige, kennzahlen, protokoll
from app.services.kartenanzeige import kurz as kurz_kennung
from app.services.aufloesung import neu_zuordnen_fahrer
from app.services.fristen import naechste_kontrolle
from app.services.ladevorgaenge import letzte_vorgaenge_von_fahrer
from app.services.zuordnung import ZuordnungsFehler, fahrer_fahrzeug_zuordnen, fahrzeug_von_fahrer_am, gueltig_am
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


def _detail(request, db, fahrer, werte, fehler=None, status_code=200, zuordnung_werte=None, zuordnung_fehler=None):
    e = app_einstellungen.laden(db)
    kontext = {"fahrer": fahrer, "werte": werte, "fehler": fehler or {},
               "intervall": e.fuehrerschein_intervall_monate, "fuehrerschein_an": e.feature_fuehrerschein,
               "naechste_kontrolle": naechste_kontrolle(fahrer, e) if fahrer else None,
               "zuordnung_werte": zuordnung_werte or {"ab": heute().isoformat(), "neu_zuordnen": "on"},  # Vorauswahl
               "zuordnung_fehler": zuordnung_fehler,
               "referenziert": fahrer is not None and _ist_referenziert(db, fahrer.id)}
    if fahrer is not None:
        kontext["fahrzeug_historie"] = db.scalars(
            select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer.id)
            .order_by(ZuordnungFahrerFahrzeug.gueltig_ab.desc())).all()
        kontext["karten_historie"] = db.scalars(
            select(ZuordnungKarte).where(ZuordnungKarte.fahrer_id == fahrer.id)
            .order_by(ZuordnungKarte.gueltig_ab.desc())).all()
        zurueck = f"?zurueck=/stammdaten/fahrer/{fahrer.id}"
        kontext["fahrzeug_mini"] = mini_zeitleiste.berechnen(
            [mini_zeitleiste.Eintrag(z.gueltig_ab, z.gueltig_bis, z.fahrzeug.kennzeichen,
                                     f"/stammdaten/fahrzeuge/{z.fahrzeug.id}", z.fahrzeug.modell or "",
                                     f"/stammdaten/zuordnungen/fahrer/{z.id}{zurueck}", art="auto")
             for z in kontext["fahrzeug_historie"]], heute())  # Person ohne Fahrzeug ist keine Lücke
        karten = [mini_zeitleiste.Eintrag(z.gueltig_ab, z.gueltig_bis, z.ladekarte.msp.name,
                                          f"/stammdaten/ladekarten/{z.ladekarte.id}",
                                          f"…{kurz_kennung(z.ladekarte.kennung)} · persönlich",
                                          f"/stammdaten/zuordnungen/karte/{z.id}{zurueck}", art="karte")
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
                    f"/stammdaten/zuordnungen/karte/{zk.id}{zurueck}", art="karte"))
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


@router.get("/{fahrer_id}")
def detail(request: Request, fahrer_id: int, ab: str = "", bis: str = "", db: Session = Depends(get_db)):
    """`ab`/`bis`: Vorbelegung „Neue Zuordnung“ (z. B. aus einer Lücke in der Zeitleiste)."""
    fahrer = _laden(db, fahrer_id)
    return _detail(request, db, fahrer, zuordnung_werte=zuordnung_vorbelegung(ab, bis) if ab else None, werte={"name": fahrer.name, "aktiv": fahrer.aktiv, "fuehrerschein_kontrolle":
                                         fahrer.fuehrerschein_kontrolle.isoformat() if fahrer.fuehrerschein_kontrolle
                                         else "", "email": fahrer.email or ""})


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


def _pool_hinweis(request, db: Session, fahrer: Fahrer, fahrzeug: Fahrzeug, ab, bis) -> None:
    """Hat das Fahrzeug im neuen Zeitraum noch andere Fahrer:innen, ist es jetzt ein Poolfahrzeug. Das kann gewollt
    sein (bisherige werden von der Personenseite aus bewusst nicht ausgetragen); die Meldung bietet das Austragen
    zum Vortag an, sofern alle anderen Zuordnungen vorher beginnen."""
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
        text += " Bei einem Fahrerwechsel bitte die anderen Zuordnungen beim Fahrzeug anpassen."
    else:
        text += f" Bei einem Fahrerwechsel die bisherigen zum {ab - timedelta(days=1):%d.%m.%Y} austragen:"
        aktion = {"url": f"/stammdaten/fahrzeuge/{fahrzeug.id}/bisherige-austragen", "text": "Bisherige austragen",
                  "felder": {"ab": ab.isoformat(), "fahrer_id": fahrer.id, "zurueck": f"/stammdaten/fahrer/{fahrer.id}"}}
    meldung(request, text, "info", aktion)


@router.post("/{fahrer_id}/fahrzeug")
def fahrzeug_zuordnen(request: Request, fahrer_id: int, fahrzeug_id: str = Form(""), ab: str = Form(""),
                      bis: str = Form(""), neu_zuordnen: str | None = Form(None), db: Session = Depends(get_db)):
    fahrer = _laden(db, fahrer_id)
    zuordnung_werte = {"fahrzeug_id": fahrzeug_id, "ab": ab, "bis": bis, "neu_zuordnen": neu_zuordnen}
    try:
        fahrzeug = db.get(Fahrzeug, int(fahrzeug_id)) if fahrzeug_id.isdigit() else None
        if fahrzeug is None:
            raise ZuordnungsFehler("Bitte ein Fahrzeug wählen.")
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        bis_datum = datum_lesen(bis)
        _, hinweise = fahrer_fahrzeug_zuordnen(db, fahrer, fahrzeug, ab=ab_datum, bis=bis_datum, heute=heute())
    except ValueError as e:  # ZuordnungsFehler oder ungültiges Datum
        db.rollback()
        return _detail(request, db, fahrer, {"name": fahrer.name, "aktiv": fahrer.aktiv}, status_code=422,
                       zuordnung_werte=zuordnung_werte, zuordnung_fehler=str(e))
    if neu_zuordnen:
        neu_info = (f"{neu_zuordnen_fahrer(db, fahrer.id, ab_datum, bis_datum)} bereits importierte "
                         "Ladevorgänge wurden neu zugeordnet.")
    db.commit()
    meldung(request, f"{fahrer.name} ist ab {ab_datum:%d.%m.%Y} Fahrzeug {fahrzeug.kennzeichen} zugeordnet.")
    if neu_zuordnen:
        meldung(request, neu_info)
    for h in hinweise:
        meldung(request, h, "warnung")
    _pool_hinweis(request, db, fahrer, fahrzeug, ab_datum, bis_datum)
    return weiter(f"/stammdaten/fahrer/{fahrer.id}")


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
