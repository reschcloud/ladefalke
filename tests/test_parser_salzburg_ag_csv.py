"""Salzburg AG als CSV-Export („Stromladen_Beleg_<Nr>.csv“): Parser, Erkennung, keine Doppelten mit dem PDF."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Gebuehr, Ladevorgang, Msp
from app.parsers import PARSER, parser_erkennen, passende_parser, salzburg_ag, salzburg_ag_csv
from app.services.importe import bestaetigen, hochladen, vorschau_berechnen
from tests.beispiele import beispiel

D = Decimal
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = Path(__file__).parent.parent / "samples"
CSV = FIXTURES / "salzburg_ag_beispiel.csv"  # erfunden, dieselbe Rechnung wie salzburg_ag_beispiel.txt (PDF-Text)


def test_csv_parsen():
    assert passende_parser(CSV) == ["salzburg_ag"]
    e = salzburg_ag_csv.parsen(CSV)
    r = e.rechnung
    assert (r.nummer, r.datum, r.zeitraum_von, r.zeitraum_bis, r.summe_netto) == (
        "60000002", date(2026, 10, 31), date(2026, 10, 1), date(2026, 10, 31), D("33.00"))
    a, b = e.vorgaenge
    assert e.zeiten_utc
    assert (a.kartenkennung, a.externe_id, a.ladepunkt) == ("K00001", "100000001", "AT*SZG*E000001")
    assert (a.start, a.ende) == (datetime(2026, 10, 3, 11, 37, 44), datetime(2026, 10, 3, 12, 11, 23))  # UTC → Sommerzeit
    assert (a.kwh, a.kosten_netto, a.stromart, a.standort) == (D("28.250"), D("9.89"), "DC", "Teststraße 3, 5000 Testort")
    assert (b.kwh, b.kosten_netto, b.stromart) == (D("52.000"), D("18.11"), None)  # Roaming PLUS: keine Angabe
    assert "Contact Name" not in a.rohdaten["csv"] and a.rohdaten["land"] == "AT"
    (g,) = e.gebuehren  # Grundgebühren fehlen in der CSV: Differenz zur Rechnungssumme
    assert (g.kartenkennung, g.betrag_netto) == (None, D("5.00")) and len(e.warnungen) == 1


def test_andere_entity_nicht_salzburg(tmp_path):
    fremd = tmp_path / "beleg.csv"
    fremd.write_text(CSV.read_text(encoding="utf-8-sig").replace("Salzburg AG für Energie", "Andere Energie"),
                     encoding="utf-8")
    assert passende_parser(fremd) == []


@pytest.mark.parametrize("reihenfolge", ["pdf_zuerst", "csv_zuerst"])
def test_pdf_und_csv_keine_doppelten(db, monkeypatch, reihenfolge):
    text = (FIXTURES / "salzburg_ag_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(type(PARSER["salzburg_ag"]), "parsen", lambda self, pfad: salzburg_ag.parsen_text(text)
                        if salzburg_ag.ist_pdf(pfad) else salzburg_ag_csv.parsen(pfad))
    msp_id = db.scalar(select(Msp.id).where(Msp.parser_key == "salzburg_ag"))
    dateien = [("rechnung.pdf", b"%PDF-1.4 sag"), ("Stromladen_Beleg_60000002.csv", CSV.read_bytes())]
    if reihenfolge == "csv_zuerst":
        dateien.reverse()
    importe = []
    for name, inhalt in dateien:
        imp = hochladen(db, name, inhalt)
        if name.endswith(".pdf"):
            imp.parser_key, imp.msp_id = "salzburg_ag", msp_id
        assert imp.parser_key == "salzburg_ag" and imp.msp_id == msp_id
        vorschau_berechnen(db, imp)
        bestaetigen(db, imp, {})
        importe.append(imp)
    assert db.scalar(select(func.count()).select_from(Ladevorgang).where(Ladevorgang.import_id == importe[1].id)) == 0
    assert db.scalar(select(func.count()).select_from(Gebuehr).where(Gebuehr.import_id == importe[1].id)) == 0


def test_beispiel_csv():
    datei = beispiel("416625102521")  # Stromladen_Beleg_<Nr>.csv
    assert parser_erkennen(datei) == "salzburg_ag"
    e = PARSER["salzburg_ag"].parsen(datei)
    assert (len(e.vorgaenge), e.rechnung.summe_netto) == (11, D("107.56")) and e.rechnung.nummer
    summe = sum((v.kosten_netto for v in e.vorgaenge), D(0)) + sum((g.betrag_netto for g in e.gebuehren), D(0))
    assert summe == e.rechnung.summe_netto
    assert all(v.kartenkennung and v.externe_id and v.ende >= v.start for v in e.vorgaenge)


def test_vorschau_zeigt_utc_hinweis(angemeldet):
    antwort = angemeldet.post("/import", files={"datei_upload": ("Stromladen_Beleg_60000002.csv", CSV.read_bytes(),
                                                                 "text/csv")})
    assert "Uhrzeiten stehen in der Rechnung in UTC" in antwort.text
    assert 'title="Wiener Ortszeit – in der Rechnung in UTC' in antwort.text  # Tooltip an Start/Ende
