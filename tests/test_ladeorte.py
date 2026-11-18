"""Ladeorte: Adresse → PLZ/Ort, gleiche Adresse → ein Ladeort (Raumbezug Phase 2)."""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Import, ImportStatus, Ladeart, Ladeort, Ladevorgang, Msp, ZuordnungQuelle
from app.services.ladeorte import analysieren, ladeort_fuer


@pytest.mark.parametrize("standort, land, plz, ort", [
    ("1010 Wien, Teststraße 1", "AT", "1010", "Wien"),                                   # Wien Energie
    ("2000 Teststadt, Autobahn A1 KM 1", "AT", "2000", "Teststadt"),
    ("Teststraße 1 1010, Teststadt am See", "AT", "1010", "Teststadt am See"),          # DCS
    ("Próba utca 1133 Budapest", "HU", "1133", "Budapest"),
    ("Teststraße 60, 4999 Testort", "AT", "4999", "Testort"),                           # EnBW
    ("Teststraße 1, Testdorf", "AT", None, "Testdorf"),                                 # LINZ AG
    ("Salzburg AG Hypercharger Testmarkt rechts, Teststraße 3, Testort", "AT", None, "Testort"),  # Salzburg AG
    ("Test Betreiber GmbH, Rastanlage Teststadt Nord, A0, DE", "DE", None, "Rastanlage Teststadt Nord"),  # Elli
    ("Teststandort 3 (Musterweg 1, 1010 Wien)", "AT", "1010", "Wien"),                  # Wallbox
    ("ul. Testowa 1, 00-950 Warszawa", "PL", "00-950", "Warszawa"),
    ("Testgatan 1, 123 45 Teststad", "SE", "12345", "Teststad"),
    ("Testiela 1, LV-1050 Rīga", "LV", "1050", "Rīga"),
    ("Hauptplatz 1, Testort", None, None, "Testort"),                                    # ohne Land
])
def test_analysieren(standort, land, plz, ort):
    a = analysieren(standort, land)
    assert (a.plz, a.ort) == (plz, ort)


def test_gleiche_adresse_gleicher_ladeort():
    a, b = analysieren("1010 Wien,  Teststraße 1", "AT"), analysieren("1010 wien , Teststraße 1", "AT")
    assert a.schluessel == b.schluessel and a.text == "1010 Wien, Teststraße 1"
    assert analysieren("1010 Wien, Teststraße 1", "DE").schluessel != a.schluessel  # anderes Land
    assert analysieren("  ", "AT") is None and analysieren(None, "AT") is None


def test_ladeort_fuer_und_manuelle_plz(angemeldet, db):
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="o.pdf", datei_pfad="o.pdf", datei_hash="o" * 64, msp_id=we.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    cache = {}
    ort1 = ladeort_fuer(db, "Teststraße 1, Testdorf", "AT", cache)
    assert ladeort_fuer(db, "Teststraße 1,Testdorf", "AT", cache) is ort1
    assert ladeort_fuer(db, "Teststraße 1, Testdorf", "AT") is ort1  # auch ohne Cache aus der Datenbank
    assert db.scalar(select(func.count()).select_from(Ladeort)) >= 1 and ort1.plz is None
    v = Ladevorgang(import_id=imp.id, msp_id=we.id, start=datetime(2026, 9, 1, 8), ende=datetime(2026, 9, 1, 9),
                    kwh=Decimal(10), kosten_netto=Decimal(5), ladeart=Ladeart.oeffentlich, land="AT",
                    standort="Teststraße 1, Testdorf", ladeort=ort1, zuordnung_quelle=ZuordnungQuelle.keine)
    db.add(v)
    db.commit()
    text = angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert "Erkannt: keine PLZ, „" in text
    antwort = angemeldet.post(f"/ladevorgaenge/{v.id}/ladeort", data={"plz": "12"})
    assert "keine gültige PLZ für Österreich" in antwort.text
    antwort = angemeldet.post(f"/ladevorgaenge/{v.id}/ladeort", data={"plz": "4040"})
    db.refresh(ort1)
    assert ort1.plz_manuell == "4040" and ort1.plz_wirksam == "4040" and "(manuell)" in antwort.text
    angemeldet.post(f"/ladevorgaenge/{v.id}/ladeort", data={"plz": ""})
    db.refresh(ort1)
    assert ort1.plz_manuell is None
