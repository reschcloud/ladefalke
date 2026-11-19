"""Parser Elli / Cupra Charging: anonymisierter Textauszug + echte Beispiel-PDF aus samples/ (falls vorhanden)."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import KennungTyp
from app.parsers import parser_erkennen
from app.parsers.basis import zahl_flexibel
from app.parsers.elli import ElliParser, parsen_text

D = Decimal
FIXTURE = Path(__file__).parent / "fixtures" / "elli_beispiel.txt"
SAMPLES = sorted((Path(__file__).parent.parent / "samples").glob("*Easy Charging*.pdf"))


@pytest.fixture
def ergebnis():
    return parsen_text(FIXTURE.read_text(encoding="utf-8"))


def test_kopf(ergebnis):
    r = ergebnis.rechnung
    assert ergebnis.warnungen == []
    assert (r.zeitraum_von, r.zeitraum_bis) == (date(2026, 8, 1), date(2026, 8, 31))
    assert r.summe_netto == D("72.77")  # 86,82 Summe − 14,05 Mehrwertsteuer laut Dokument
    assert r.nummer is None  # keine Rechnung


def test_vorgaenge(ergebnis):
    a, at, b = ergebnis.vorgaenge
    assert (a.kartenkennung, a.kennung_typ) == ("Erika Beispiel", KennungTyp.kartenlabel)
    assert (a.start, a.ende) == (datetime(2026, 8, 12), datetime(2026, 8, 12, 0, 31, 7))  # keine Uhrzeit
    assert (a.kwh, a.ladepunkt) == (D("52.246"), "DE*TST*E000001")
    assert a.standort == "Test Betreiber GmbH, Rastanlage Teststadt Nord, A0, DE"
    assert at.rohdaten["land"] == "AT" and at.rohdaten["ust_satz"] == "20"
    # Netto je Land: 36,05/1,19 = 30,29 · 25,55/1,20 = 21,29 · 25,22/1,19 = 21,19 → Summe 72,77
    assert [v.kosten_netto for v in ergebnis.vorgaenge] == [D("30.29"), D("21.29"), D("21.19")]
    assert sum(v.kosten_netto for v in ergebnis.vorgaenge) == ergebnis.rechnung.summe_netto
    assert sum(v.kwh for v in ergebnis.vorgaenge) == D("114.610")


def test_kontrollen_warnen():
    text = FIXTURE.read_text(encoding="utf-8")
    w = parsen_text(text.replace("00:21:45 36,552 25,22 EUR", "00:21:45 36,552 26,22 EUR")).warnungen
    assert any("Bruttopreise" in x for x in w)
    w = parsen_text(text.replace("Ladevorgänge insgesamt\n3", "Ladevorgänge insgesamt\n4")).warnungen
    assert any("laut Übersicht 4" in x for x in w)
    w = parsen_text(text.replace("AT*PRB*E0000002", "XX*PRB*E0000002").replace("Probeweg 1, AT", "Probeweg 1")).warnungen
    assert any("unbekannt" in x for x in w)


@pytest.mark.parametrize("text, erwartet", [("36,05", "36.05"), ("86.82", "86.82"), ("1.234,5", "1234.5"),
                                            ("52,246", "52.246")])
def test_zahl_flexibel(text, erwartet):
    assert zahl_flexibel(text) == D(erwartet)


@pytest.mark.skipif(not SAMPLES, reason="Beispiel-PDF nicht vorhanden (samples/ ist nicht im Git)")
def test_beispiel_pdf():
    pdf = SAMPLES[0]
    assert parser_erkennen(pdf) == "elli"
    e = ElliParser().parsen(pdf)
    assert e.warnungen == []
    assert len(e.vorgaenge) == 3
    assert sum(v.kwh for v in e.vorgaenge) == D("114.610")
    assert sum(v.kosten_netto for v in e.vorgaenge) == e.rechnung.summe_netto == D("72.77")
    assert {v.ladepunkt[:2] for v in e.vorgaenge} == {"DE", "AT"}


# ------------------------------------------------------------------ älteres Format: Rechnung „Easy Charging“

RECHNUNG = Path(__file__).parent / "fixtures" / "elli_rechnung_beispiel.txt"


def test_rechnung_aelteres_format():
    """Rechnung mit Uhrzeiten, EVSE-ID, AC/DC und eindeutiger ID je Vorgang; netto centgenau auf den Gesamtbetrag."""
    e = parsen_text(RECHNUNG.read_text(encoding="utf-8"))
    r = e.rechnung
    assert e.warnungen == []
    assert (r.nummer, r.datum, r.zeitraum_von, r.zeitraum_bis, r.summe_netto) == (
        "800000000001", date(2023, 11, 30), date(2023, 11, 3), date(2023, 11, 24), D("30.00"))
    a, b, c = e.vorgaenge
    assert (a.kartenkennung, a.kennung_typ) == ("Erika Beispiel", KennungTyp.kartenlabel)
    assert (a.start, a.ende) == (datetime(2023, 11, 3, 9, 15), datetime(2023, 11, 3, 9, 35))  # mit Uhrzeit
    assert (a.kwh, a.kosten_netto, a.stromart) == (D("30.100"), D("10.00"), "DC")
    assert (a.ladepunkt, a.externe_id) == ("AT*TST*E001*1", "00000000-0000-4000-8000-000000000001")
    assert a.standort == "Teststation Nord -, Testort" and a.rohdaten["land"] == "AT"
    assert "uhrzeit_unbekannt" not in a.rohdaten
    assert (c.stromart, c.ende) == ("AC", datetime(2023, 11, 24, 14, 35, 10))
    assert sum(v.kosten_netto for v in e.vorgaenge) == r.summe_netto


def test_rechnung_kontrollen_warnen():
    text = RECHNUNG.read_text(encoding="utf-8")
    w = parsen_text(text.replace("25,0000 02:30:00 12,00 EUR", "25,0000 02:30:00 13,00 EUR")).warnungen
    assert any("Bruttopreise" in x for x in w)
    w = parsen_text(text.replace("Ladevorgänge in Österreich 3", "Ladevorgänge in Österreich 4")).warnungen
    assert any("laut Rechnung 4" in x for x in w)


def test_rechnung_echt():
    from tests.beispiele import beispiel
    pdf = beispiel("52012e1e1e0c")  # Easy Charging, Rechnung 12/2023
    assert parser_erkennen(pdf) == "elli"
    e = ElliParser().parsen(pdf)
    assert e.warnungen == [] and len(e.vorgaenge) == 6
    assert sum(v.kosten_netto for v in e.vorgaenge) == e.rechnung.summe_netto
    assert all(v.externe_id and v.ladepunkt and v.stromart and v.ende > v.start for v in e.vorgaenge)
