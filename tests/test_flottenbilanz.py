"""Flottenbilanz (Marketing-Report): 16:9, zwei Seiten ohne Kopf- und Fußzeile. Seite 1 nur Flottenzahlen (keine
Personen-, Fahrzeug- und Kartendaten, keine Kostensummen), Seite 2 die drei günstigsten Öffentlich-Lader:innen mit
Namen (ohne Kennzeichen); ohne TLP-Kennzeichnung."""
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladeort, Ladevorgang,
                        Msp, ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services import app_einstellungen

ZEITRAUM = "art=quartal&jahr=2026&nr=3"


@pytest.fixture
def flotte(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="f.pdf", datei_pfad="f", datei_hash="f" * 64, msp_id=msp.id, ladeart=Ladeart.oeffentlich,
                 status=ImportStatus.abgeschlossen)
    ort = Ladeort(schluessel="AT|flotte", land="AT", adresse="Teststraße 1, 1010 Wien", lat=D("48.2"), lon=D("16.37"),
                  genauigkeit="plz")
    db.add_all([imp, ort])
    db.flush()
    tag = iter(range(1, 29))
    # je Person: Fahrzeug, persönliche Karte, öffentliche Ladungen (Ø €/kWh: Anna 0,30 < Bert 0,40 < Cora 0,50 < Gerda 0,80)
    for name, preis, stromart in (("Anna Vorn", "0.30", "AC"), ("Bert Mitte", "0.40", "DC"), ("Cora Dritt", "0.50", "DC"),
                                  ("Gerda Geheim", "0.80", "DC")):
        person, fz = Fahrer(name=name), Fahrzeug(kennzeichen=f"W-GEHEIM {name[0]}")
        karte = Ladekarte(msp=msp, kennung=f"KARTE-GEHEIM-{name[0]}", kennung_typ=KennungTyp.kartennummer)
        db.add_all([person, fz, karte])
        db.flush()
        db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=person.id, gueltig_ab=date(2026, 1, 1)))
        db.add(ZuordnungFahrerFahrzeug(fahrer_id=person.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)))
        for monat in (7, 8, 9):
            t = next(tag)
            db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, fahrzeug_id=fz.id,
                               start=datetime(2026, monat, t, 8), ende=datetime(2026, monat, t, 9), kwh=D("30"),
                               kosten_netto=D(preis) * 30, stromart=stromart, land="AT", betreiber_id="AT*FLT",
                               ladeort_id=ort.id, ladeart=Ladeart.oeffentlich,
                               zuordnung_quelle=ZuordnungQuelle.automatisch))
    db.flush()


def _seiten(pdf: bytes) -> list[str]:
    import pypdfium2
    return [s.get_textpage().get_text_range() for s in pypdfium2.PdfDocument(pdf)]


def test_pdf(angemeldet, db, flotte):
    import pypdfium2
    app_einstellungen.speichern(db, {"report_tlp": "AMBER"})  # gilt für die anderen Reports, nicht hier
    assert "Flottenbilanz" in angemeldet.get(f"/reports?{ZEITRAUM}").text
    antwort = angemeldet.get(f"/reports/flottenbilanz.pdf?{ZEITRAUM}")
    assert antwort.status_code == 200 and antwort.content.startswith(b"%PDF")
    assert "ladefalke-flottenbilanz-" in antwort.headers["content-disposition"]
    breite, hoehe = pypdfium2.PdfDocument(antwort.content)[0].get_size()
    assert abs(breite / hoehe - 16 / 9) < 0.01  # Querformat 16:9
    titel, treppchen = _seiten(antwort.content)  # genau zwei Seiten
    # Seite 1: nur Flottenzahlen
    assert "Flottenbilanz Q3 2026" in titel and "360" in titel and "Ladefalke" in titel  # 12 × 30 kWh, Logo
    for geheim in ("Anna", "Bert", "Cora", "Gerda", "W-GEHEIM", "KARTE-GEHEIM", "Teststraße", "€ ", "TLP"):
        assert geheim not in titel, geheim
    # Seite 2: Treppchen mit Namen der drei Günstigsten, keine Kennzeichen, keine Vierte
    assert "Anna Vorn" in treppchen and "Bert Mitte" in treppchen and "Cora Dritt" in treppchen
    assert "0,30 €/kWh" in treppchen
    for geheim in ("Gerda", "W-GEHEIM", "KARTE-GEHEIM", "TLP"):
        assert geheim not in treppchen, geheim
    # ohne Kopf- und Fußzeile
    assert not any("Seite 1 von" in s or "erstellt" in s for s in (titel, treppchen))
    assert "Ladevorgänge nach" not in titel  # Untertitel nur Zeitraum und „Preise netto“


def test_jpg(angemeldet, db, flotte):
    import io
    from PIL import Image
    for seite in (1, 2):
        antwort = angemeldet.get(f"/reports/flottenbilanz-{seite}.jpg?{ZEITRAUM}")
        assert antwort.status_code == 200 and antwort.headers["content-type"] == "image/jpeg"
        assert f"ladefalke-flottenbilanz-q3-2026-seite-{seite}.jpg" in antwort.headers["content-disposition"]
        assert Image.open(io.BytesIO(antwort.content)).size == (3840, 2160)  # 4K
    assert angemeldet.get(f"/reports/flottenbilanz-3.jpg?{ZEITRAUM}").status_code == 404
