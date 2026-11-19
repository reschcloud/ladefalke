from datetime import date
from decimal import Decimal

import pytest

from app.formatierung import datum, datum_lesen, decimal_lesen, geld, zahl, zeitraum


def test_datum():
    assert datum(date(2026, 3, 5)) == "05.03.2026"
    assert datum(None) == ""


@pytest.mark.parametrize("wert, stellen, erwartet", [
    (Decimal("1234.5"), 2, "1.234,50"),
    (Decimal("0.3"), 3, "0,300"),
    (Decimal("-1234567.891"), 3, "-1.234.567,891"),
    (0, 2, "0,00"),
    (Decimal("72.5"), 0, "73"),  # kaufmännisch, nicht „auf gerade“
    (Decimal("0.125"), 2, "0,13"),
    (Decimal("-2.5"), 0, "-3"),
])
def test_zahl(wert, stellen, erwartet):
    assert zahl(wert, stellen) == erwartet


def test_geld():
    assert geld(Decimal("49.17")) == "49,17 €"


def test_zeitraum():
    assert zeitraum(date(2026, 1, 1), None) == "01.01.2026 – offen"
    assert zeitraum(date(2026, 1, 1), date(2026, 1, 31)) == "01.01.2026 – 31.01.2026"


@pytest.mark.parametrize("text, erwartet", [
    ("0,30", Decimal("0.30")),
    ("1.234,56", Decimal("1234.56")),
    ("0.30", Decimal("0.30")),
    ("0.300", Decimal("0.300")),       # führende 0: Dezimalpunkt
    ("1.000", Decimal("1000")),        # Tausenderpunkt, wie die Seite ihn anzeigt
    ("12.500", Decimal("12500")),
    ("1.000,5", Decimal("1000.5")),
    ("1.5", Decimal("1.5")),
    (" 12,5 € ", Decimal("12.5")),
    ("", None),
])
def test_decimal_lesen(text, erwartet):
    assert decimal_lesen(text) == erwartet


def test_decimal_lesen_ungueltig():
    with pytest.raises(ValueError, match="keine gültige Zahl"):
        decimal_lesen("abc")


@pytest.mark.parametrize("text, erwartet", [
    ("2026-11-04", date(2026, 11, 4)),
    ("04.11.2026", date(2026, 11, 4)),
    ("", None),
])
def test_datum_lesen(text, erwartet):
    assert datum_lesen(text) == erwartet


def test_datum_lesen_ungueltig():
    with pytest.raises(ValueError, match="kein gültiges Datum"):
        datum_lesen("31.02.2026")


def test_flagge():
    from app.formatierung import flagge
    assert flagge("AT") == "\U0001F1E6\U0001F1F9" and flagge("de") == "\U0001F1E9\U0001F1EA"
    assert flagge(None) == flagge("") == flagge("A*") == flagge("AUT") == ""


def test_vor_tagen():
    from datetime import date, datetime
    from app.formatierung import vor_tagen
    heute = date(2026, 11, 19)
    assert vor_tagen(datetime(2026, 11, 19, 7, 0), heute) == "heute"
    assert vor_tagen(date(2026, 11, 18), heute) == "gestern"
    assert vor_tagen(datetime(2026, 9, 3, 9, 30), heute) == "vor 77 Tagen"
    assert vor_tagen(date(2026, 11, 20), heute) == "morgen" and vor_tagen(date(2026, 11, 25), heute) == "in 6 Tagen"
    assert vor_tagen(None, heute) == ""
