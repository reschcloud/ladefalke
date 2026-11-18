"""Autobahn-Vignette und Streckenmaut FLEX (Österreich) sowie schaltbare Fristen-Features je Installation."""
from datetime import date, timedelta

from sqlalchemy import select

from app.config import heute
from app.models import Fahrer, Fahrzeug
from app.services import app_einstellungen, fahrzeug_hinweise, fristen

BASIS = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}


def _fz(db, **werte):
    fz = Fahrzeug(kennzeichen=werte.pop("kennzeichen", "W-VIG 1"), **werte)
    db.add(fz)
    db.flush()
    return fz


def _arten(db):
    return {(f.art, f.name, f.zustand) for f in fristen.ermitteln(db, app_einstellungen.laden(db))}


def test_standard_alles_aus(angemeldet, db):
    """Ohne Features: keine Felder, Hinweise oder Aufgaben – auch keine Führerscheinkontrolle."""
    _fz(db)  # Vignette nötig (Standard), aber ohne Daten
    db.add(Fahrer(name="Ohne Kontrolle"))
    db.flush()
    assert not [f for f in fristen.ermitteln(db, app_einstellungen.laden(db)) if f.art != "leasing"]
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "W-VIG 1"))
    assert 'name="vignette_ab"' not in angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    person = db.scalar(select(Fahrer).where(Fahrer.name == "Ohne Kontrolle"))
    assert 'name="fuehrerschein_kontrolle"' not in angemeldet.get(f"/stammdaten/fahrer/{person.id}").text
    einst = angemeldet.get("/einstellungen").text
    assert 'name="feature_vignette"' in einst and 'data-zeigen-wenn="feature_vignette" hidden' in einst


def test_einstellungen_schalten(angemeldet, db):
    angemeldet.post("/einstellungen", data=BASIS | {"feature_vignette": "on", "vignette_vorwarnung_tage": "45"})
    e = app_einstellungen.laden(db)
    assert e.feature_vignette and not e.feature_streckenmaut and not e.feature_fuehrerschein
    assert e.vignette_vorwarnung_tage == 45 and e.fuehrerschein_intervall_monate == 6  # ausgeblendete Werte bleiben
    seite = angemeldet.get("/einstellungen").text
    assert 'data-zeigen-wenn="feature_vignette" hidden' not in seite  # Vignette an: Frist sichtbar
    assert 'data-zeigen-wenn="feature_streckenmaut" hidden' in seite  # Streckenmaut aus: ausgeblendet


def test_fristen_vignette_und_maut(db):
    app_einstellungen.speichern(db, {"feature_vignette": "1", "feature_streckenmaut": "1"})
    t = heute()
    _fz(db, kennzeichen="V-FEHLT")  # nötig, ohne Daten
    _fz(db, kennzeichen="V-OK", vignette_ab=t - timedelta(days=10), vignette_bis=t + timedelta(days=200))
    _fz(db, kennzeichen="V-BALD", vignette_ab=t - timedelta(days=300), vignette_bis=t + timedelta(days=10))
    _fz(db, kennzeichen="V-ALT", vignette_ab=t - timedelta(days=400), vignette_bis=t - timedelta(days=1))
    _fz(db, kennzeichen="V-SPAETER", vignette_ab=t + timedelta(days=5), vignette_bis=t + timedelta(days=300))
    _fz(db, kennzeichen="V-NICHT", vignette_noetig=False)  # ohne Vignette: nichts
    _fz(db, kennzeichen="M-NOETIG", vignette_noetig=False, streckenmaut_noetig=True)
    _fz(db, kennzeichen="V-INAKTIV", aktiv=False)
    arten = _arten(db)
    assert ("vignette", "V-FEHLT", "fehlt") in arten and ("vignette", "V-BALD", "bald") in arten
    assert ("vignette", "V-ALT", "abgelaufen") in arten and ("vignette", "V-SPAETER", "noch_nicht") in arten
    assert ("streckenmaut", "M-NOETIG", "fehlt") in arten
    namen = {n for _, n, _ in arten}
    assert not {"V-OK", "V-NICHT", "V-INAKTIV"} & namen
    # Streckenmaut ist standardmäßig nicht nötig → nur M-NOETIG
    assert {n for a, n, _ in arten if a == "streckenmaut"} == {"M-NOETIG"}
    # Hinweise in der Fahrzeugliste
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "V-SPAETER"))
    [h] = [h for h in fahrzeug_hinweise.ermitteln(db, [fz], app_einstellungen.laden(db))[fz.id] if "Vignette" in h.text]
    assert h.stufe == "rot" and "erst ab" in h.text


def test_formular_haken_und_jahresvignette(angemeldet, db):
    app_einstellungen.speichern(db, {"feature_vignette": "1", "feature_streckenmaut": "1"})
    seite = angemeldet.get("/stammdaten/fahrzeuge/neu").text
    assert 'name="vignette_noetig" checked' in seite and 'name="streckenmaut_noetig" ' in seite
    j = heute().year
    assert f'data-wert="{j - 1}-12-01,{j + 1}-01-31"' in seite and f"Jahresvignette {j + 1}" in seite
    assert fristen.jahresvignette(2027) == (date(2026, 12, 1), date(2028, 1, 31))
    angemeldet.post("/stammdaten/fahrzeuge/neu", data={"kennzeichen": "W-NEU 1", "aktiv": "on", "vignette_noetig": "on",
                                                       "vignette_ab": "2026-12-01", "vignette_bis": "2028-01-31",
                                                       "streckenmaut_ab": "2026-01-01"})
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "W-NEU 1"))
    assert fz.vignette_noetig and (fz.vignette_ab, fz.vignette_bis) == (date(2026, 12, 1), date(2028, 1, 31))
    assert not fz.streckenmaut_noetig and fz.streckenmaut_ab == date(2026, 1, 1)
    # ohne Haken: Felder ausgegraut (readonly), Daten bleiben gespeichert
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'data-aktiv-wenn="streckenmaut_noetig" class="inaktiv"' in seite
    # Ende vor Beginn
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data={"kennzeichen": "W-NEU 1", "aktiv": "on",
                                                                      "vignette_noetig": "on", "vignette_ab": "2027-01-01",
                                                                      "vignette_bis": "2026-01-01"})
    assert antwort.status_code == 422 and "Das Ende liegt vor dem Beginn." in antwort.text


def test_feature_aus_laesst_daten_unberuehrt(angemeldet, db):
    """Ausgeschaltet: Speichern des Fahrzeugs bzw. der Person ändert Vignette bzw. Kontrolle nicht."""
    fz = _fz(db, vignette_ab=date(2026, 1, 1), vignette_bis=date(2026, 12, 31))
    person = Fahrer(name="Kontrolle bleibt", fuehrerschein_kontrolle=date(2026, 5, 1))
    db.add(person)
    db.flush()
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data={"kennzeichen": fz.kennzeichen, "aktiv": "on"})
    angemeldet.post(f"/stammdaten/fahrer/{person.id}", data={"name": person.name, "aktiv": "on"})
    db.refresh(fz)
    db.refresh(person)
    assert fz.vignette_noetig and fz.vignette_bis == date(2026, 12, 31)
    assert person.fuehrerschein_kontrolle == date(2026, 5, 1)
