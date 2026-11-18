"""EVSE-ID normieren, Land bestimmen (Phase 1 Raumbezug)."""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from app.services import app_einstellungen
from app.services.auswertung import auswerten, zeitraum
from app.services.evse import Ortsdaten, ermitteln, evse_zerlegen, land_aus_text, landname


@pytest.mark.parametrize("roh, erwartet", [
    ("AT*MER*E000001*02", Ortsdaten("AT*MER*E000001*02", "AT*MER", "AT")),
    ("ATMERE000001*02", Ortsdaten("AT*MER*E000001*02", "AT*MER", "AT")),       # ohne Trenner
    (" de*ion*e 1234 ", Ortsdaten("DE*ION*E1234", "DE*ION", "DE")),             # klein, Leerzeichen
    ("HR*ELE1234*01*2", Ortsdaten()),                                           # nach dem Betreiber kein „E“
    ("SI*SLOE1234*01", Ortsdaten("SI*SLO*E1234*01", "SI*SLO", "SI")),           # Betreiber + E zusammengeschrieben
    ("AT*MER*E*1234*5678", Ortsdaten("AT*MER*E*1234*5678", "AT*MER", "AT")),
    ("AT*MER", Ortsdaten(None, "AT*MER", "AT")),                                # gekürzt: nur Betreiber und Land
    ("10023456", Ortsdaten()),                                                  # Wallbox-Seriennummer
    ("XX*ABC*E123", Ortsdaten()),                                               # kein Land
    (None, Ortsdaten()),
])
def test_evse_zerlegen(roh, erwartet):
    assert evse_zerlegen(roh) == erwartet


def test_land_aus_text_und_namen():
    assert land_aus_text("Österreich") == "AT" and land_aus_text("DE") == "DE" and land_aus_text("at") == "AT"
    assert land_aus_text("Litauen") == "LT" and land_aus_text("") is None and land_aus_text("Atlantis") is None
    assert landname("HR") == "Kroatien" and landname(None) == "unbekannt"


def test_ermitteln_reihenfolge():
    # 1. EVSE-ID schlägt Rechnungsangabe
    assert ermitteln("dcs", "DE*ION*E1234", {"land": "Österreich"}).land == "DE"
    # 2. Rechnungsangabe (DCS-Teilrechnung je Land)
    assert ermitteln("dcs", "AT*MER*", {"land": "Kroatien"}).land == "AT"
    assert ermitteln("dcs", "kaputt", {"land": "Kroatien"}) == Ortsdaten(None, None, "HR")
    # 3. Standardland des Inlandsanbieters; Wallbox-Nummer ist keine EVSE-ID
    assert ermitteln("linz_ag", None, {}) == Ortsdaten(None, None, "AT")
    assert ermitteln("hastobe_csv", "AT1234E567", {}) == Ortsdaten(None, None, "AT")
    assert ermitteln("enbw", None, {}) == Ortsdaten()


def test_import_speichert_und_auswertung_pro_land(angemeldet, db):
    dcs = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="l.pdf", datei_pfad="l.pdf", datei_hash="l" * 64, msp_id=dcs.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    for tag, land, kwh in ((1, "AT", 30), (2, "DE", 50), (3, "DE", 10), (4, None, 5)):
        db.add(Ladevorgang(import_id=imp.id, msp_id=dcs.id, start=datetime(2026, 9, tag, 8),
                           ende=datetime(2026, 9, tag, 9), kwh=Decimal(kwh), kosten_netto=Decimal(5),
                           ladeart=Ladeart.oeffentlich, land=land, betreiber_id=f"{land}*ION" if land else None,
                           zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert [(c, s.kwh) for c, s in a.laender] == [("DE", 60), ("AT", 30), (None, 5)]
    text = angemeldet.get("/auswertungen?art=monat&jahr=2026&nr=9").text
    assert "Pro Land" in text and "\U0001F1E9\U0001F1EA Deutschland</a>" in text  # mit Flagge
    assert "<td>\U0001F1E9\U0001F1EA Deutschland</td>" in text  # Pro CPO
    for name in ("land", "cpo"):  # Exporte ohne Flaggen
        csv = angemeldet.get(f"/auswertungen/export/{name}.csv?art=monat&jahr=2026&nr=9").text
        assert "Deutschland" in csv and "\U0001F1E9" not in csv
    text = angemeldet.get("/ladevorgaenge?land=DE").text
    assert "<strong>2</strong> Vorgänge" in text and 'name="land" value="DE" checked> Deutschland' in text
    # Mehrfachauswahl: DE oder unbekannt
    text = angemeldet.get("/ladevorgaenge?land=DE&land=-").text
    assert "<strong>3</strong> Vorgänge" in text
    assert "land=DE&amp;land=-" in text  # Export übernimmt die Auswahl
    # Pro CPO (Betreiber aus der EVSE-ID), unbekannte zuletzt; Link filtert die Ladevorgänge
    assert [(c, s.kwh) for c, s in a.cpo] == [("DE*ION", 60), ("AT*ION", 30), (None, 5)]
    text = angemeldet.get("/auswertungen?art=monat&jahr=2026&nr=9").text
    assert "Pro <abbr title=\"Charge Point Operator" in text and "<code>DE*ION</code>" in text
    text = angemeldet.get("/ladevorgaenge?betreiber=DE*ION").text
    assert "<strong>2</strong> Vorgänge" in text and 'name="betreiber" value="DE*ION" checked> DE*ION' in text
    assert '>DE*ION</a></td>' in text  # Spalte CPO
    assert "<strong>1</strong> Vorgänge" in angemeldet.get("/ladevorgaenge?betreiber=-").text  # ohne Kennung


def test_land_manuell_korrigieren(angemeldet, db):
    """Ausländische EVSE-ID an einem Ladepunkt in Österreich: Land korrigieren, Ladeort neu zuordnen."""
    from app.models import Ladeort
    dcs = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="n.pdf", datei_pfad="n.pdf", datei_hash="n" * 64, msp_id=dcs.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    nl = Ladeort(schluessel="NL|testufer 13 9998 testsee", land="NL", adresse="Testufer 13 9998 Testsee")
    db.add_all([imp, nl])
    db.flush()
    v = Ladevorgang(import_id=imp.id, msp_id=dcs.id, start=datetime(2026, 8, 24, 13), ende=datetime(2026, 8, 24, 17),
                    kwh=Decimal(26), kosten_netto=Decimal(14), ladeart=Ladeart.oeffentlich, land="NL",
                    betreiber_id="NL*LMS", standort="Testufer 13 9998 Testsee", ladeort_id=nl.id,
                    zuordnung_quelle=ZuordnungQuelle.keine, rohdaten={})
    db.add(v)
    db.flush()
    angemeldet.post(f"/ladevorgaenge/{v.id}/land", data={"land": "AT"})
    db.refresh(v)
    assert v.land == "AT" and v.rohdaten["land_ermittelt"] == "NL"
    assert v.ladeort.land == "AT" and v.ladeort.plz == "9998" and v.ladeort.id != nl.id  # PLZ nach AT-Format
    text = angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert "manuell korrigiert, ermittelt:\n        Niederlande" in text
    angemeldet.post(f"/ladevorgaenge/{v.id}/land", data={"land": "DE"})  # erneut: ermitteltes Land bleibt NL
    db.refresh(v)
    assert v.land == "DE" and v.rohdaten["land_ermittelt"] == "NL"
