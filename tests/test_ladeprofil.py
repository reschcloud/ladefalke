"""Stromart (AC/DC) aus den Rechnungen und tatsächliches Ladeprofil je Fahrzeug."""
import importlib.util
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Fahrzeug, Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from app.parsers import dcs, elli, enbw, linz_ag, salzburg_ag, wien_energie
from app.parsers.basis import stromart_normieren
from app.parsers.hastobe_csv import HastobeCsvParser
from app.services.ladeprofil import ladeprofile, ohne_angabe

D = Decimal
FIXTURES = Path(__file__).parent / "fixtures"


def _text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("text, erwartet", [("AC", "AC"), ("dc", "DC"), ("HPC", "DC"), ("", None), (None, None),
                                            ("AC/DC", None)])
def test_stromart_normieren(text, erwartet):
    assert stromart_normieren(text) == erwartet


def test_stromart_aus_parsern():
    assert [v.stromart for v in dcs.parsen_text(_text("dcs_beispiel.txt")).vorgaenge] == ["DC", "DC", "AC"]
    assert [v.stromart for v in linz_ag.parsen_text(_text("linz_ag_beispiel.txt")).vorgaenge] == ["AC"] * 3
    assert [v.stromart for v in enbw.parsen_text(_text("enbw_beispiel.txt")).vorgaenge] == ["DC", "AC"]
    assert [v.stromart for v in wien_energie.parsen_text(_text("wien_energie_beispiel.txt")).vorgaenge] == [
        "AC", "DC", "DC", "DC"]
    # keine Angabe in der Rechnung → None
    assert {v.stromart for v in salzburg_ag.parsen_text(_text("salzburg_ag_beispiel.txt")).vorgaenge} == {None}
    assert {v.stromart for v in elli.parsen_text(_text("elli_beispiel.txt")).vorgaenge} == {None}
    assert {v.stromart for v in HastobeCsvParser().parsen(FIXTURES / "hastobe_beispiel.csv").vorgaenge} == {None}


def test_migration_nachtrag_aus_rohdaten():
    pfad = Path(__file__).parent.parent / "alembic" / "versions" / "0007_stromart.py"
    spec = importlib.util.spec_from_file_location("migration_0007", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    for key, name in [("dcs", "dcs_beispiel.txt"), ("linz_ag", "linz_ag_beispiel.txt"), ("enbw", "enbw_beispiel.txt"),
                      ("wien_energie", "wien_energie_beispiel.txt")]:
        modul_parser = {"dcs": dcs, "linz_ag": linz_ag, "enbw": enbw, "wien_energie": wien_energie}[key]
        for v in modul_parser.parsen_text(_text(name)).vorgaenge:
            assert modul._aus_rohdaten(key, v.rohdaten) == v.stromart, (key, v.rohdaten)
    assert modul._aus_rohdaten("salzburg_ag", {"zeilen": ["… DC …"]}) is None


@pytest.fixture
def fahrzeug_mit_vorgaengen(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="p.pdf", datei_pfad="p.pdf", datei_hash="p" * 64, msp_id=msp.id, ladeart=Ladeart.oeffentlich,
                 status=ImportStatus.abgeschlossen)
    fz = Fahrzeug(kennzeichen="W-PROFIL 1")
    db.add_all([imp, fz])
    db.flush()

    def lv(tag, art, kwh, minuten, geschaetzt=False):
        start = datetime(2026, 9, tag, 10)
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=start, ende=start + timedelta(minutes=minuten),
                           kwh=D(kwh), kwh_geschaetzt=geschaetzt, kosten_netto=D(5), ladeart=Ladeart.oeffentlich,
                           stromart=art, fahrzeug_id=fz.id, zuordnung_quelle=ZuordnungQuelle.automatisch))

    lv(1, "DC", 40, 30)    # 80 kW
    lv(2, "DC", 20, 30)    # 40 kW → gewichtet 60 kWh / 1 h = 60 kW
    lv(3, "AC", 22, 240)   # 5,5 kW
    lv(4, "AC", 11, 60)    # 11 kW → gewichtet 33 kWh / 5 h = 6,6 kW
    lv(5, "DC", 50, 20, geschaetzt=True)   # geschätzt → nicht dabei
    lv(6, None, 30, 600)   # ohne Angabe → nicht dabei
    lv(7, "DC", 0, 5)      # 0 kWh → nicht dabei
    db.flush()
    return fz


def test_ladeprofil(db, fahrzeug_mit_vorgaengen):
    fz = fahrzeug_mit_vorgaengen
    profil = ladeprofile(db)[fz.id]
    dc, ac = profil["DC"], profil["AC"]
    assert (dc.anzahl, dc.kwh_schnitt, dc.leistung) == (2, D(30), D(60))
    assert (ac.anzahl, ac.kwh_schnitt) == (2, D("16.5"))
    assert round(ac.leistung, 6) == D("6.6")
    assert (dc.von.day, dc.bis.day) == (1, 2)
    assert ohne_angabe(db, fz.id) == 1
    assert ladeprofile(db, fz.id) == {fz.id: profil}


def test_anzeige(angemeldet, db, fahrzeug_mit_vorgaengen):
    fz = fahrzeug_mit_vorgaengen
    text = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert "Ladeprofil und kWh-Schätzung" in text  # eine Tabelle (die Liste zeigt keine Profilspalten mehr)
    assert "16,5 kWh" in text and "6,6 kW" in text and "60,0 kW" in text
    assert "1 Vorgänge ohne AC/DC-Angabe" in text
    assert "11,0 kW, höchstens" in text and "<small>Vorgabe</small>" in text  # nur 2 Vorgänge je Stromart


# ------------------------------------------------------------------ Plausibilität AC/DC

@pytest.fixture
def mit_unplausiblem(db, fahrzeug_mit_vorgaengen):
    """Wie im Beispiel: 35,062 kWh in 53 min als AC deklariert (≈ 39,7 kW) – kann nur DC sein."""
    fz = fahrzeug_mit_vorgaengen
    v = db.scalar(select(Ladevorgang).where(Ladevorgang.fahrzeug_id == fz.id, Ladevorgang.stromart == "AC"))
    falsch = Ladevorgang(import_id=v.import_id, msp_id=v.msp_id, start=datetime(2026, 8, 4, 8, 47),
                         ende=datetime(2026, 8, 4, 9, 40), kwh=D("35.062"), kosten_netto=D("19.62"),
                         ladeart=Ladeart.oeffentlich, stromart="AC", fahrzeug_id=fz.id,
                         zuordnung_quelle=ZuordnungQuelle.automatisch)
    db.add(falsch)
    db.flush()
    return fz, falsch


def test_unplausibel_markiert_und_nicht_im_profil(db, mit_unplausiblem):
    from app.services import app_einstellungen
    from app.services.ladeprofil import unplausibel_grund, unplausible_anzahl
    fz, falsch = mit_unplausiblem
    e = app_einstellungen.laden(db)
    assert unplausibel_grund(falsch, e) == "AC mit 39,7 kW – plausibel nur unter 12 kW"
    assert unplausible_anzahl(db, fz.id) == 1
    assert ladeprofile(db, fz.id)[fz.id]["AC"].anzahl == 2  # unplausibler Vorgang zählt nicht


@pytest.mark.parametrize("art, kwh, minuten, unplausibel", [
    ("AC", "11.9", 60, False), ("AC", "12", 60, True),       # AC ab 12 kW
    ("DC", "9.9", 60, True), ("DC", "10", 60, False),        # DC unter 10 kW
    ("DC", "999", 60, False), ("DC", "1000", 60, True),      # DC ab 1.000 kW
])
def test_grenzen(db, art, kwh, minuten, unplausibel):
    from app.services import app_einstellungen
    from app.services.ladeprofil import unplausibel_grund
    v = Ladevorgang(start=datetime(2026, 9, 1, 10), ende=datetime(2026, 9, 1, 10) + timedelta(minutes=minuten),
                    kwh=D(kwh), kwh_geschaetzt=False, stromart=art)
    assert (unplausibel_grund(v, app_einstellungen.laden(db)) is not None) is unplausibel


def test_grenzen_einstellbar(angemeldet, db, mit_unplausiblem):
    from app.services import app_einstellungen
    from app.services.ladeprofil import unplausible_anzahl
    fz, _ = mit_unplausiblem
    antwort = angemeldet.post("/einstellungen", data={
        "stichtag": "ende", "topliste_m": "", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
        "plausibel_ac_max_kw": "45", "plausibel_dc_min_kw": "10", "plausibel_dc_max_kw": "1000"})
    assert antwort.status_code == 200
    assert app_einstellungen.laden(db).plausibel_ac_max_kw == D(45)
    assert unplausible_anzahl(db, fz.id) == 0  # 39,7 kW AC jetzt erlaubt
    antwort = angemeldet.post("/einstellungen", data={
        "stichtag": "ende", "topliste_m": "", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
        "plausibel_ac_max_kw": "12", "plausibel_dc_min_kw": "500", "plausibel_dc_max_kw": "100"})
    assert antwort.status_code == 422 and "Obergrenze für DC" in antwort.text


def test_korrigieren_und_ausnehmen(angemeldet, db, mit_unplausiblem):
    fz, falsch = mit_unplausiblem
    text = angemeldet.get(f"/ladevorgaenge?fahrzeug_id={fz.id}&unplausibel=1").text
    assert "<strong>1</strong> Vorgänge" in text and 'class="unplausibel"' in text
    text = angemeldet.get(f"/ladevorgaenge/{falsch.id}").text
    assert f'href="/import/{falsch.import_id}/datei?anzeigen=1" target="_blank"' in text  # Originaldokument
    # Liste unter „Aufgaben“, Zähler in der Menüleiste
    text = angemeldet.get("/aufgaben").text
    assert '<span class="kachel-zahl">1</span>\n  <strong>Unplausible AC/DC-Angaben</strong>' in text and f'<a href="/ladevorgaenge/{falsch.id}" role="button" data-schreiben class="outline klein">Korrigieren</a>' in text
    assert "1 unplausible AC/DC-Angaben" in text

    # Ausnehmen: Markierung weg, Profil unverändert
    angemeldet.post(f"/ladevorgaenge/{falsch.id}/profil", data={"ausnehmen": "1"})
    db.refresh(falsch)
    assert falsch.profil_ausgenommen
    assert "<strong>0</strong> Vorgänge" in angemeldet.get(f"/ladevorgaenge?fahrzeug_id={fz.id}&unplausibel=1").text
    angemeldet.post(f"/ladevorgaenge/{falsch.id}/profil", data={"ausnehmen": "0"})

    # Korrigieren auf DC: plausibel, zählt jetzt als DC; Original bleibt erhalten
    antwort = angemeldet.post(f"/ladevorgaenge/{falsch.id}/stromart", data={"stromart": "DC"})
    assert "Stromart auf DC gesetzt" in antwort.text and "Weiterhin unplausibel" not in antwort.text
    db.refresh(falsch)
    assert (falsch.stromart, falsch.stromart_quelle == "manuell", falsch.rohdaten["stromart_rechnung"]) == ("DC", True, "AC")
    profil = ladeprofile(db, fz.id)[fz.id]
    assert profil["DC"].anzahl == 3 and profil["AC"].anzahl == 2
    text = angemeldet.get(f"/ladevorgaenge/{falsch.id}").text
    assert "manuell gesetzt" in text and "Rechnung: AC" in text and "39,7" in text  # Kachel Ø kW mit Herkunft AC/DC
    assert "✎" not in angemeldet.get(f"/ladevorgaenge?fahrzeug_id={fz.id}").text  # Herkunft nur auf der Detailseite


def test_liste_zeigt_ac_dc(angemeldet, db, fahrzeug_mit_vorgaengen):
    text = angemeldet.get(f"/ladevorgaenge?fahrzeug_id={fahrzeug_mit_vorgaengen.id}").text
    assert 'class="marker stromart-ac">AC<' in text and 'class="marker stromart-dc">DC<' in text
    csv = angemeldet.get(f"/ladevorgaenge/export.csv?fahrzeug_id={fahrzeug_mit_vorgaengen.id}").content
    assert ";AC/DC;Unplausibel;" in csv.decode("utf-8-sig").split("\r\n")[0]
