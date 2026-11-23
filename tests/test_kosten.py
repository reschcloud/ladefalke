"""Tatsächliche Kosten (services/kosten.py): netto + nicht abziehbare USt.; Vorsteuerabzug je Fahrzeug nur für
inländische USt., ausländische zählt voll; USt. ohne Rechnungsangabe aus der Steuersatz-Tabelle geschätzt."""
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models import Fahrzeug, Import, ImportStatus, Ladeart, Ladevorgang, Msp, Steuersatz, ZuordnungQuelle
from app.services import app_einstellungen, kosten
from app.services.auswertung import auswerten, zeitraum

SEP = "art=monat&jahr=2026&nr=9"


@pytest.fixture
def daten(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    imp = Import(dateiname="k.pdf", datei_pfad="k.pdf", datei_hash="k" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    voll, keiner, halb = (Fahrzeug(kennzeichen=k, vorsteuer_prozent=D(p)) for k, p in
                          (("K-VOLL 1", 100), ("K-KEIN 2", 0), ("K-HALB 3", 50)))
    db.add_all([imp, voll, keiner, halb])
    db.flush()

    def lv(fz, netto, tag=10, land="AT", **ust):
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, tag, 8), ende=datetime(2026, 9, tag, 9),
                        kwh=D(10), kosten_netto=D(netto), ladeart=Ladeart.oeffentlich, land=land,
                        fahrzeug_id=fz.id if fz else None,
                        zuordnung_quelle=ZuordnungQuelle.automatisch if fz else ZuordnungQuelle.keine, **ust)
        db.add(v)
        return v

    vorgaenge = {
        "voll": lv(voll, "10.00"),  # AT, USt. geschätzt 20 % = 2,00, voll abziehbar → 10,00
        "keiner": lv(keiner, "10.00"),  # → 12,00 (≈)
        "halb": lv(halb, "10.00"),  # → 11,00 (≈)
        "ausland": lv(voll, "10.00", land="DE", ust_betrag=D("1.90"), ust_satz=D(19), ust_land="DE"),  # → 11,90
        "ohne": lv(None, "10.00"),  # ohne Fahrzeug: Standard 100 % → 10,00
    }
    db.flush()
    return {"voll": voll, "keiner": keiner, "halb": halb, **{f"v_{k}": v for k, v in vorgaenge.items()}}


def test_berechnen():
    k = kosten.Kontext("AT", D(100), {"AT": [(date(2020, 1, 1), D(20))],
                                      "DE": [(date(2020, 1, 1), D(19)), (date(2020, 7, 1), D(16)),
                                             (date(2021, 1, 1), D(19))]})
    tag = date(2026, 9, 1)
    assert kosten.berechnen(k, D(10), None, None, None, "AT", False, tag, D(100)).kosten == D(10)
    e = kosten.berechnen(k, D(10), None, None, None, "AT", False, tag, D(0))
    assert (e.kosten, e.ust, e.satz, e.land, e.geschaetzt, e.geschaetzt_in_kosten) == (D(12), D(2), D(20), "AT", True, D(2))
    assert kosten.berechnen(k, D(10), None, None, None, "AT", False, tag, D(50)).kosten == D(11)
    # ausländische USt. laut Rechnung: zählt voll, auch bei 100 % Vorsteuerabzug
    e = kosten.berechnen(k, D(10), D("1.90"), D(19), "DE", "DE", False, tag, D(100))
    assert (e.kosten, e.geschaetzt, e.abzug) == (D("11.90"), False, D(0))
    # geschätzt mit dem Satz am Tag der Ladung (DE 16 % im 2. Halbjahr 2020)
    assert kosten.berechnen(k, D(10), None, None, None, "DE", False, date(2020, 9, 1), D(100)).kosten == D("11.60")
    # Land ohne Satz: Satz des Unternehmens; privat: ohne USt.
    assert kosten.berechnen(k, D(10), None, None, None, "XX", False, tag, D(0)).kosten == D(12)
    assert kosten.berechnen(k, D(10), None, None, None, "AT", True, tag, D(0)).kosten == D(10)


def test_beim_speichern_und_auswertung(db, daten):
    assert [daten[f"v_{n}"].kosten for n in ("voll", "keiner", "halb", "ausland", "ohne")] == [
        D(10), D(12), D(11), D("11.90"), D(10)]
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert a.gesamt.kosten == D("54.90") and a.gesamt.kosten_geschaetzt == D(3)  # 2 + 1 geschätzte USt.
    assert a.fahrzeug(daten["keiner"].id).summe.kosten == D(12)
    # Fahrzeugwechsel am Vorgang: neu berechnet
    daten["v_voll"].fahrzeug_id = daten["keiner"].id
    db.flush()
    assert daten["v_voll"].kosten == D(12)


def test_vorsteuer_je_fahrzeug_aendern(angemeldet, db, daten):
    fz = daten["keiner"]
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'name="vorsteuer_prozent" value="0"' in seite
    formular = {"kennzeichen": fz.kennzeichen, "aktiv": "on"}
    assert angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data=formular | {"vorsteuer_prozent": "150"}).status_code == 422
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data=formular | {"vorsteuer_prozent": "100"})
    db.refresh(daten["v_keiner"])
    assert fz.vorsteuer_prozent == D(100) and daten["v_keiner"].kosten == D(10)


def test_einstellungen_und_steuersaetze(angemeldet, db, daten):
    basis = {"stichtag": "ende", "topliste_m": "", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    seite = angemeldet.get("/einstellungen").text
    assert 'id="kosten"' in seite and "Österreich <small>(AT)</small>" in seite and '<option value="AT" selected>' in seite
    # Standard ohne Fahrzeug 0 % → Vorgang ohne Fahrzeug trägt die geschätzte USt.
    angemeldet.post("/einstellungen", data=basis | {"vorsteuer_standard": "0"})
    db.refresh(daten["v_ohne"])
    assert app_einstellungen.laden(db).vorsteuer_standard == D(0) and daten["v_ohne"].kosten == D(12)
    # Land des Unternehmens DE: österreichische USt. wird ausländisch (voll), deutsche abziehbar
    angemeldet.post("/einstellungen", data=basis | {"vorsteuer_standard": "100", "firmenland": "DE"})
    for n in ("voll", "ausland"):
        db.refresh(daten[f"v_{n}"])
    assert daten["v_voll"].kosten == D(12) and daten["v_ausland"].kosten == D(10)
    angemeldet.post("/einstellungen", data=basis | {"firmenland": "AT"})
    # Steuersatz AT ab 1.9.2026 auf 10 %: geschätzte USt. sinkt
    angemeldet.post("/einstellungen/steuersatz", data={"land": "AT", "gueltig_ab": "2026-09-01", "satz": "10"})
    db.refresh(daten["v_keiner"])
    assert daten["v_keiner"].kosten == D(11)
    neu = db.scalar(select(Steuersatz).where(Steuersatz.land == "AT", Steuersatz.gueltig_ab == date(2026, 9, 1)))
    angemeldet.post(f"/einstellungen/steuersatz/{neu.id}/loeschen")
    db.refresh(daten["v_keiner"])
    assert daten["v_keiner"].kosten == D(12)
    antwort = angemeldet.post("/einstellungen/steuersatz", data={"land": "AT", "gueltig_ab": "x", "satz": "10"})
    assert "nicht gespeichert" in antwort.text


def test_anzeige(angemeldet, db, daten):
    detail = angemeldet.get(f"/ladevorgaenge/{daten['v_keiner'].id}").text
    assert "12,00 €" in detail and "netto 10,00 €" in detail and "USt. 2,00 € (20 % AT) geschätzt" in detail
    dashboard = angemeldet.get(f"/?{SEP}").text
    assert "enthält 3,00 € geschätzte USt." in dashboard and "55 €" in dashboard
    csv = angemeldet.get(f"/ladevorgaenge/export.csv?{SEP}").content.decode("utf-8-sig")
    assert "Kosten €;Netto €;USt. €;USt.-Satz %;USt.-Land;USt. geschätzt" in csv
    assert "12,00;10,00;2,00;20,00;AT;ja" in csv
