"""Fahrer:in „ohne Dienstwagen“ (Ladungen mit der persönlichen Karte ohne Fahrzeug sind begründet) und
Ersatz-/Mietfahrzeuge (zugeordnet und ausgewertet, aber nicht wie Dienstwagen geprüft). Erfundene Daten."""
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.models import (Fahrer, FahrerOhneFahrzeug, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte,
                        Ladevorgang, Msp, ZuordnungKarte, ZuordnungQuelle)
from app.services import app_einstellungen, fahrzeug_hinweise, kartenpruefung, luecken
from app.services.auswertung import auswerten, zeitraum_mit
from app.services.offen import ohne_fahrzeug_bedingungen
from app.services.zuordnung import (ZuordnungsFehler, fahrer_fahrzeug_zuordnen, ohne_dienstwagen_aendern,
                                    ohne_dienstwagen_eintragen)

HEUTE = date(2026, 11, 24)


@pytest.fixture
def lisa(db):
    """Lisa hat eine persönliche Karte, aber im Oktober keinen Dienstwagen; zwei Ladungen im Oktober."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="odw.pdf", datei_pfad="odw", datei_hash="d" * 64, msp_id=msp.id, ladeart=Ladeart.oeffentlich,
                 status=ImportStatus.abgeschlossen)
    person = Fahrer(name="Lisa Ohnewagen")
    karte = Ladekarte(msp=msp, kennung="KARTE-ODW-1", kennung_typ=KennungTyp.kartennummer)
    db.add_all([imp, person, karte])
    db.flush()
    db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=person.id, gueltig_ab=date(2026, 1, 1)))
    for tag in (6, 20):
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, fahrzeug_id=None,
                           start=datetime(2026, 10, tag, 8), ende=datetime(2026, 10, tag, 9), kwh=D("20"),
                           kosten_netto=D("8"), land="AT", ladeart=Ladeart.oeffentlich,
                           zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    return person


def _offen(db) -> int:
    return db.scalar(select(func.count()).select_from(Ladevorgang).where(
        *ohne_fahrzeug_bedingungen(), Ladevorgang.ladekarte.has(Ladekarte.kennung == "KARTE-ODW-1")))


def test_ohne_dienstwagen_ist_begruendet(db, lisa):
    e = app_einstellungen.laden(db)
    oktober = zeitraum_mit(date(2026, 10, 1), "monat")
    assert _offen(db) == 2
    vorher = auswerten(db, oktober, e)
    assert vorher.nicht_zugeordnet.anzahl == 2 and vorher.ohne_dienstwagen.anzahl == 0

    ohne_dienstwagen_eintragen(db, lisa, ab=date(2026, 10, 1), bis=date(2026, 10, 15), grund="noch kein Dienstwagen")
    assert _offen(db) == 1  # nur der 20.10. ist noch offen
    ohne_dienstwagen_eintragen(db, lisa, ab=date(2026, 10, 16), bis=date(2026, 10, 31), grund="noch kein Dienstwagen")
    assert _offen(db) == 0
    perioden = db.scalars(select(FahrerOhneFahrzeug).where(FahrerOhneFahrzeug.fahrer_id == lisa.id)).all()
    assert [(p.gueltig_ab, p.gueltig_bis) for p in perioden] == [(date(2026, 10, 1), date(2026, 10, 31))]  # zusammengefasst

    a = auswerten(db, oktober, e)
    assert a.ohne_dienstwagen.anzahl == 2 and a.nicht_zugeordnet.anzahl == 0
    assert a.gesamt.anzahl == vorher.gesamt.anzahl  # nur anders aufgeteilt
    assert a.fahrer_zeile(lisa.id).summe.kosten == vorher.fahrer_zeile(lisa.id).summe.kosten == D("16")


def test_ueberschneidung_mit_fahrzeug_und_automatisches_ende(db, lisa):
    fz = Fahrzeug(kennzeichen="ODW-1")
    db.add(fz)
    db.flush()
    fahrer_fahrzeug_zuordnen(db, lisa, fz, ab=date(2026, 11, 1), heute=HEUTE)
    with pytest.raises(ZuordnungsFehler):
        ohne_dienstwagen_eintragen(db, lisa, ab=date(2026, 10, 1))  # offen → überschneidet das Auto ab November
    o_hinweise = ohne_dienstwagen_eintragen(db, lisa, ab=date(2026, 10, 1), bis=date(2026, 10, 31))
    assert o_hinweise == []
    # offener Zeitraum vorher endet, wenn die Person später offen ein Fahrzeug bekommt
    anna = Fahrer(name="Anna Später")
    fz2 = Fahrzeug(kennzeichen="ODW-2")
    db.add_all([anna, fz2])
    db.flush()
    ohne_dienstwagen_eintragen(db, anna, ab=date(2026, 9, 1))
    _, hinweise = fahrer_fahrzeug_zuordnen(db, anna, fz2, ab=date(2026, 11, 10), heute=HEUTE)
    o = db.scalar(select(FahrerOhneFahrzeug).where(FahrerOhneFahrzeug.fahrer_id == anna.id))
    assert o.gueltig_bis == date(2026, 11, 9) and any("ohne Dienstwagen" in h for h in hinweise)
    with pytest.raises(ZuordnungsFehler):
        ohne_dienstwagen_aendern(db, o, ab=date(2026, 9, 1), bis=None, grund="")


def test_seiten_eintragen_bearbeiten_loeschen(angemeldet, db, lisa):
    antwort = angemeldet.post(f"/stammdaten/fahrer/{lisa.id}/ohne-dienstwagen",
                              data={"ab": "2026-10-01", "bis": "2026-10-31", "grund": "Mietwagen",
                                    "zurueck": "/aufgaben#ohne-fahrzeug"})
    assert antwort.url.path == "/aufgaben" and "ohne Dienstwagen" in antwort.text
    o = db.scalar(select(FahrerOhneFahrzeug).where(FahrerOhneFahrzeug.fahrer_id == lisa.id))
    assert o.grund == "Mietwagen"
    seite = angemeldet.get(f"/stammdaten/fahrer/{lisa.id}").text
    assert "ohne Dienstwagen" in seite and f"/stammdaten/zuordnungen/ohne-dienstwagen/{o.id}" in seite
    assert angemeldet.get(f"/stammdaten/zuordnungen/ohne-dienstwagen/{o.id}").status_code == 200
    angemeldet.post(f"/stammdaten/zuordnungen/ohne-dienstwagen/{o.id}",
                    data={"ab": "2026-10-01", "bis": "2026-10-10", "grund": "noch kein Dienstwagen"})
    db.refresh(o)
    assert o.gueltig_bis == date(2026, 10, 10) and o.grund == "noch kein Dienstwagen"
    angemeldet.post(f"/stammdaten/zuordnungen/ohne-dienstwagen/{o.id}/loeschen")
    assert db.scalar(select(FahrerOhneFahrzeug).where(FahrerOhneFahrzeug.fahrer_id == lisa.id)) is None
    fehler = angemeldet.post(f"/stammdaten/fahrer/{lisa.id}/ohne-dienstwagen", data={"ab": ""})
    assert "Beginndatum" in fehler.text


def test_aufgaben_bieten_ohne_dienstwagen_an(angemeldet, lisa):
    seite = angemeldet.get("/aufgaben").text
    assert f"/stammdaten/fahrer/{lisa.id}?zurueck=%2Faufgaben%23ohne-fahrzeug&ab=2026-10-06&bis=2026-10-20#ohne-dienstwagen" in seite


# ------------------------------------------------------------------ Ersatz-/Mietfahrzeug

def test_ersatzfahrzeug_ohne_dienstwagen_pruefungen(db, lisa):
    e = app_einstellungen.laden(db)
    miet = Fahrzeug(kennzeichen="MIET-1", modell="Mietwagen", ersatzfahrzeug=True, vignette_noetig=True)
    dienst = Fahrzeug(kennzeichen="DIENST-1", vignette_noetig=True)
    db.add_all([miet, dienst])
    db.flush()
    fahrer_fahrzeug_zuordnen(db, lisa, miet, ab=date(2026, 10, 1), bis=date(2026, 10, 31), heute=HEUTE)
    ids = {l.fahrzeug.id for l in luecken.ermitteln(db, HEUTE)}
    assert miet.id not in ids  # nach Oktober wäre beim Dienstwagen eine Lücke
    assert miet.id not in {b.fahrzeug.id for b in kartenpruefung.ermitteln(db, e)}
    hinweise = fahrzeug_hinweise.ermitteln(db, [miet, dienst], e)
    assert [h.text for h in hinweise[miet.id]] == ["Ersatz-/Mietfahrzeug"]
    assert any(h.text == "keine Leasingdaten" for h in hinweise[dienst.id])


def test_ersatzfahrzeug_im_formular(angemeldet, db):
    angemeldet.post("/stammdaten/fahrzeuge/neu", data={"kennzeichen": "MIET-2", "aktiv": "on", "ersatzfahrzeug": "on",
                                                       "vorsteuer_prozent": "100"})
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "MIET-2"))
    assert fz is not None and fz.ersatzfahrzeug
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'name="ersatzfahrzeug" checked' in seite
