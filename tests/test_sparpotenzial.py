"""Karten und Preise je CPO: Sparpotenzial gegenüber dem günstigsten MSP je Stromart (mit Mindestmenge),
Web-Ansicht, Export und PDF-Report."""
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp, ZuordnungKarte,
                        ZuordnungQuelle)
from app.services import app_einstellungen
from app.services.auswertung import auswerten, zeitraum

SEP = "art=monat&jahr=2026&nr=9"
CPO = "AT*SPA"


@pytest.fixture
def spardaten(db):
    msp = {k: db.scalar(select(Msp).where(Msp.parser_key == k)) for k in ("dcs", "wien_energie", "linz_ag")}
    anna, bernd, cora = Fahrer(name="Anna Spar"), Fahrer(name="Bernd Spar"), Fahrer(name="Cora Spar")
    karten = {n: Ladekarte(msp=msp[m], kennung=f"SPAR-{n}", kennung_typ=KennungTyp.kartennummer)
              for n, m in (("anna", "dcs"), ("bernd", "wien_energie"), ("cora", "linz_ag"))}
    imp = {k: Import(dateiname=f"{k}.pdf", datei_pfad=f"{k}.pdf", datei_hash=k[0] * 64, msp_id=m.id,
                     ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen) for k, m in msp.items()}
    db.add_all([anna, bernd, cora, *karten.values(), *imp.values()])
    db.flush()
    for name, person in (("anna", anna), ("bernd", bernd), ("cora", cora)):
        db.add(ZuordnungKarte(ladekarte_id=karten[name].id, fahrer_id=person.id, gueltig_ab=date(2026, 1, 1)))
    tag = iter(range(1, 29))

    def lv(name, m, kwh, kosten, stromart):
        t = next(tag)
        db.add(Ladevorgang(import_id=imp[m].id, msp_id=msp[m].id, ladekarte_id=karten[name].id,
                           start=datetime(2026, 9, t, 8), ende=datetime(2026, 9, t, 9), kwh=D(kwh), kosten_netto=D(kosten),
                           betreiber_id=CPO, stromart=stromart, ladeart=Ladeart.oeffentlich,
                           zuordnung_quelle=ZuordnungQuelle.keine))

    lv("anna", "dcs", "30", "21.00", "AC")          # 0,70 €/kWh AC
    lv("bernd", "wien_energie", "25", "10.00", "AC")  # 0,40 €/kWh AC → Maßstab
    lv("cora", "linz_ag", "5", "1.00", "AC")        # 0,20 €/kWh, aber unter 20 kWh → kein Maßstab
    lv("anna", "dcs", "40", "32.00", "DC")          # DC: nur ein MSP → kein Vergleich
    db.flush()
    return {"anna": anna, "bernd": bernd, "karten": karten}


def test_berechnung(db, spardaten):
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    d = a.cpo_personen[CPO]
    assert d.massstab["AC"] == ("Wien Energie", D("0.40")) and "DC" in d.massstab  # DC: DCS selbst ist Maßstab
    anna = next(p for p in d.personen if p.fahrer and p.fahrer.id == spardaten["anna"].id)
    assert anna.ersparnis == D("9.00")  # 21 − 30 × 0,40; DC ohne anderen MSP
    assert anna.ersparnis_karte(spardaten["karten"]["anna"]) == D("9.00")
    assert [x.stromart for x in anna.posten_karte(spardaten["karten"]["anna"])] == ["AC", "DC"]
    assert d.ersparnis == D("9.00") and a.ersparnis_gesamt == D("9.00")  # Bernd (Maßstab) und Cora (günstiger) 0
    [(dd, p, x)] = a.sparpotenziale()
    assert dd is d and p is anna and x.stromart == "AC" and x.ersparnis == D("9.00")


def test_web_ansicht_und_export(angemeldet, db, spardaten):
    seite = angemeldet.get(f"/auswertungen/cpo/{CPO}?{SEP}").text
    assert "Sparpotenzial" in seite and "9,00 €" in seite
    assert "AC: 0,70 €/kWh, günstigster Wien Energie 0,40 €/kWh → 9,00 €" in seite  # Tooltip je Stromart
    assert "wenig Daten" in seite and 'class="marker">günstigster' in seite
    csv = angemeldet.get(f"/auswertungen/export/cpo_personen.csv?{SEP}&cpo={CPO}").content.decode("utf-8-sig")
    assert "Sparpotenzial €" in csv.splitlines()[0] and "SPAR-anna" in csv and "9,00" in csv


def test_pdf_report(angemeldet, db, spardaten):
    assert "Karten und Preise je" in angemeldet.get(f"/reports?{SEP}").text
    antwort = angemeldet.get(f"/reports/karten-cpo.pdf?{SEP}")
    assert antwort.status_code == 200 and antwort.content.startswith(b"%PDF")
    assert "ladefalke-karten-cpo-09-2026.pdf" in antwort.headers["content-disposition"]
    import pypdfium2
    text = "".join(seite.get_textpage().get_text_range() for seite in pypdfium2.PdfDocument(antwort.content))
    assert "Größte Sparpotenziale" in text and "Anna Spar" in text and "9,00 €" in text and "Wien Energie" in text
