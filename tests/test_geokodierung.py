"""Geokodierung der Ladeorte mit dem lokalen Postleitzahlen-Verzeichnis (Raumbezug Phase 3)."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import GeoPlz, Import, ImportStatus, Ladeart, Ladeort, Ladevorgang, Msp, ZuordnungQuelle
from app.services.geokodierung import einlesen, geokodieren, name_norm, plz_norm, verzeichnis_vorhanden
from app.services.ladeorte import ladeort_fuer

D = Decimal
GEO = Path(__file__).parent / "fixtures" / "geonames_beispiel.txt"  # erfundene Auszüge im GeoNames-Format


@pytest.fixture
def verzeichnis(db):
    assert einlesen(db, GEO) == 13  # nur europäische Länder (US entfällt)
    return db


def test_normieren():
    assert plz_norm("123 45", "SE") == "12345" and plz_norm("LV-1050", "LV") == "1050"
    assert plz_norm("1012 AB", "NL") == "1012" and plz_norm(None) == ""
    assert name_norm("St. Pölten") == "sankt polten" == name_norm("Sankt-Pölten")
    assert name_norm("Straße") == "strasse"


@pytest.mark.parametrize("standort, land, genauigkeit, lat", [
    ("1010 Wien, Teststraße 1", "AT", "plz", D("48.207700")),           # zwei Einträge: der mit passendem Namen
    ("1010 Innere Stadt, Testgasse 1", "AT", "plz", D("48.208500")),    # kein Name passt → Mittel
    ("Testgatan 1, 123 45 Teststad", "SE", "plz", D("59.300000")),
    ("Teststraße 1, Testdorf", "AT", "ort", D("46.600000")),             # ohne PLZ: Ortsname
    ("Hauptplatz 1, St. Pölten", "AT", "ort", D("48.204700")),          # „St.“ = „Sankt“
    ("Test GmbH, Rastanlage Teststadt Nord, A0, DE", "DE", "ort", D("48.040000")),  # Wort aus dem Abschnitt
    ("Bahnhofstraße 2, Testkirchen", "AT", "ort", D("47.905000")),      # Namensanfang, eindeutig
    ("Hauptstraße 30, Testmarkt", "AT", "land", None),                   # Namensanfang mehrdeutig → offen
    ("Hauptstraße 9876, Nirgendwo", "AT", "land", None),                 # PLZ unbekannt, Ort unbekannt → Landesmitte
    ("Irgendwo 1, Nirgendwo", "IT", None, None),                         # Land ohne Verzeichnis
])
def test_geokodieren(verzeichnis, standort, land, genauigkeit, lat):
    ort = ladeort_fuer(verzeichnis, standort, land)
    assert geokodieren(verzeichnis, ort) == genauigkeit
    if lat is not None:
        assert ort.lat == lat
    assert (ort.lat is None) == (genauigkeit is None)


def test_manuelle_plz_und_zu_klaeren(angemeldet, verzeichnis):
    db = verzeichnis
    we = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    imp = Import(dateiname="g.pdf", datei_pfad="g.pdf", datei_hash="g" * 64, msp_id=we.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    ort = ladeort_fuer(db, "Testgasse 9, Seedorf", "AT")
    geokodieren(db, ort)
    v = Ladevorgang(import_id=imp.id, msp_id=we.id, start=datetime(2026, 9, 1, 8), ende=datetime(2026, 9, 1, 9),
                    kwh=D(10), kosten_netto=D(5), ladeart=Ladeart.oeffentlich, land="AT", standort="Testgasse 9, Seedorf",
                    ladeort=ort, zuordnung_quelle=ZuordnungQuelle.keine)
    db.add(v)
    db.commit()
    assert ort.genauigkeit == "land"
    text = angemeldet.get("/aufgaben").text
    assert '<span class="kachel-zahl">1</span>\n  <strong>Ladeorte ohne Koordinaten</strong>' in text and "https://www.google.com/maps/search/?api=1&amp;query=Testgasse%209%2C%20Seedorf%2C%20%C3%96sterreich" in text and "Testgasse 9, Seedorf" in text and "nur Landesmitte" in text
    antwort = angemeldet.post(f"/aufgaben/ladeort/{ort.id}", data={"plz": "4020"})
    db.refresh(ort)
    assert (ort.plz_manuell, ort.genauigkeit, ort.lat) == ("4020", "plz", D("48.306900"))
    assert "Verortet über die PLZ" in antwort.text and 'id="ladeorte"' not in antwort.text  # erledigt

    # Ortsname statt PLZ (manueller Name, auch als eindeutiger Namensanfang)
    angemeldet.post(f"/aufgaben/ladeort/{ort.id}", data={"plz": "", "ort": "Testkirchen"})
    db.refresh(ort)
    assert (ort.plz_manuell, ort.ort_manuell, ort.genauigkeit, ort.lat) == (None, "Testkirchen", "ort", D("47.905000"))

    # Bewusst ohne Verortung: keine Aufgabe mehr, übersteht „Alle neu verorten“; PLZ eintragen hebt es auf
    angemeldet.post(f"/aufgaben/ladeort/{ort.id}", data={"plz": "", "ort": ""})
    db.refresh(ort)
    assert ort.genauigkeit == "land"
    angemeldet.post(f"/aufgaben/ladeort/{ort.id}/verzichten")
    angemeldet.post("/aufgaben/ladeorte/verorten")
    db.refresh(ort)
    assert (ort.genauigkeit, ort.lat) == ("verzichtet", None)
    assert 'id="ladeorte"' not in angemeldet.get("/aufgaben").text
    angemeldet.post(f"/ladevorgaenge/{v.id}/ladeort", data={"plz": "4020", "ort": ""})
    db.refresh(ort)
    assert ort.genauigkeit == "plz"


def test_ohne_verzeichnis_hinweis(angemeldet, db):
    if verzeichnis_vorhanden(db):
        pytest.skip("Test-Datenbank enthält bereits ein Verzeichnis")
    assert "noch nicht eingespielt" in angemeldet.get("/aufgaben").text
