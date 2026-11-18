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
