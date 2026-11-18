"""Parser has.to.be-CSV: anonymisierte Fixture + echte Beispieldatei aus samples/ (falls vorhanden)."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import KennungTyp
from app.parsers import parser_erkennen
from app.parsers.hastobe_csv import HastobeCsvParser, kartenkennung
from tests.beispiele import beispiel

FIXTURE = Path(__file__).parent / "fixtures" / "hastobe_beispiel.csv"


@pytest.mark.parametrize("label, referenz, erwartet", [
    ("Muster, Max", "Zutrittschip", ("Muster, Max", False)),
    ("Muster, Max", "", ("Muster, Max", False)),
    ("GAST", "Gast Anna", ("GAST / Gast Anna", True)),
    ("GAST", "", ("GAST", True)),
])
def test_kartenkennung(label, referenz, erwartet):
    assert kartenkennung(label, referenz) == erwartet


def test_erkennung():
    assert parser_erkennen(FIXTURE) == "hastobe_csv"
    assert not HastobeCsvParser().erkennen(Path(__file__).parent / "fixtures" / "wien_energie_beispiel.txt")


def test_fixture():
    e = HastobeCsvParser().parsen(FIXTURE)
    assert len(e.vorgaenge) == 6
    v = e.vorgaenge[0]
    assert v.externe_id == "900001"
    assert v.kartenkennung == "Muster, Max" and not v.gastkarte
    assert v.kennung_typ == KennungTyp.kartenlabel
    assert v.start == datetime(2026, 9, 29, 17, 8, 12)  # Start im Vormonat
    assert v.kwh == Decimal("67.088")
    assert v.kosten_netto is None  # wird beim Import aus dem Strompreis berechnet
    assert v.standort == "Teststandort 3 (Musterweg 1, 1010 Wien)"
    assert v.ladepunkt == "SER0000003"
    assert {x.kartenkennung for x in e.vorgaenge if x.gastkarte} == {"GAST / Gast Anna", "GAST / Gästechip X"}
    assert sum(x.kwh for x in e.vorgaenge) == Decimal("166.689")
    assert e.rechnung.zeitraum_von == date(2026, 10, 1)
    assert e.rechnung.zeitraum_bis == date(2026, 10, 31)
    assert any("0 kWh" in w for w in e.warnungen)


def test_beispieldatei():
    SAMPLE = beispiel("49870f5a050d")  # Monatsexport der Wallboxen (11/2026)
    assert parser_erkennen(SAMPLE) == "hastobe_csv"
    e = HastobeCsvParser().parsen(SAMPLE)
    assert len(e.vorgaenge) == 131
    assert sum(1 for v in e.vorgaenge if v.kwh == 0) == 4
    assert sum(1 for v in e.vorgaenge if v.start.month == 9) == 2
    assert len({v.kartenkennung for v in e.vorgaenge}) == 13
    assert all(v.kartenkennung.startswith("GAST / ") for v in e.vorgaenge if v.gastkarte)
    assert len({v.externe_id for v in e.vorgaenge}) == 131
