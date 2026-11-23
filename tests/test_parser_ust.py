"""USt. aus den Rechnungen (Parser): Satz, Land und Betrag je Posten; Reverse Charge („RC“) bei DCS; ausländische
USt. (DCS, EnBW, Elli) statt sie zu verwerfen. Testdaten erfunden (tests/fixtures)."""
from collections import Counter
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from app.models import KennungTyp
from app.parsers import dcs, elli, enbw, enbw_csv, linz_ag, salzburg_ag, wien_energie
from app.parsers.basis import REVERSE_CHARGE, Gebuehr, ust_setzen
from app.services import kosten

F = Path(__file__).parent / "fixtures"


def _ust(e):
    posten = e.vorgaenge + e.gebuehren
    return (sum((p.ust_betrag or D(0) for p in posten), D(0)),
            dict(Counter((p.ust_land, p.ust_satz) for p in posten)))


def test_ust_setzen_verteilt_centgenau():
    posten = [Gebuehr("a", D("3.33")), Gebuehr("b", D("3.33")), Gebuehr("c", D("3.34"))]
    assert ust_setzen(posten, D(20), "AT", D("2.00")) is None
    assert sum(p.ust_betrag for p in posten) == D("2.00")  # 0,666/0,666/0,668 → centgenau auf 2,00
    assert {(p.ust_land, p.ust_satz) for p in posten} == {("AT", D(20))}
    warnung = ust_setzen(posten, D(20), "AT", D("5.00"))  # passt nicht: je Posten gerundet, mit Warnung
    assert "passt nicht" in warnung and sum(p.ust_betrag for p in posten) == D("2.01")  # 3 × 0,67
    ust_setzen(posten, None, REVERSE_CHARGE)
    assert {(p.ust_land, p.ust_satz, p.ust_betrag) for p in posten} == {("RC", None, None)}


def test_dcs_reverse_charge_und_auslaendische_ust():
    betrag, arten = _ust(dcs.parsen_text((F / "dcs_beispiel.txt").read_text(encoding="utf-8")))
    assert betrag == D("2.38") and arten == {("RC", None): 4, ("HU", D(27)): 1}  # „+ 27% MwSt. HU … 2,38 EUR“
    betrag, arten = _ust(dcs.parsen_text((F / "dcs_gutschein_de.txt").read_text(encoding="utf-8")))
    assert betrag == D("1.23") and arten[("DE", D(19))] == 1  # deutsche Teilrechnung
    assert arten[(None, None)] == 6  # österreichische Teilrechnung ohne Angabe: die App schätzt


def test_inlandsrechnungen():
    for e, betrag, satz in (
            (linz_ag.parsen_text((F / "linz_ag_beispiel.txt").read_text(encoding="utf-8")), D("9.08"), D(20)),
            (salzburg_ag.parsen_text((F / "salzburg_ag_beispiel.txt").read_text(encoding="utf-8")), D("6.60"), D(20)),
            (enbw.parsen_text((F / "enbw_beispiel.txt").read_text(encoding="utf-8")), D("7.75"), D(20)),
            (wien_energie.parsen_text((F / "wien_energie_beispiel.txt").read_text(encoding="utf-8")), D("14.29"), D(20))):
        b, arten = _ust(e)
        assert b == betrag and set(arten) == {("AT", satz)}, arten
    # EnBW-CSV: Brutto − Netto je Position (centgenau wie die Rechnung)
    b, arten = _ust(enbw_csv.parsen(F / "enbw_beispiel.csv", "Rechnung_2026_09_AT_1.csv"))
    assert b == D("7.76") and set(arten) == {("AT", D(20))}


def test_elli_ust_je_land():
    b, arten = _ust(elli.parsen_text((F / "elli_beispiel.txt").read_text(encoding="utf-8")))
    assert b == D("14.05") and arten == {("DE", D(19)): 2, ("AT", D(20)): 1}
    b, arten = _ust(elli.parsen_text((F / "elli_grundgebuehr_beispiel.txt").read_text(encoding="utf-8")))
    assert b == D("4.60") and {land for land, _ in arten} == {"AT"}


def test_reverse_charge_in_kosten():
    k = kosten.Kontext("AT", D(100), {"AT": [(date(2020, 1, 1), D(20))]})
    tag = date(2026, 9, 1)
    e = kosten.berechnen(k, D(10), None, None, "RC", "AT", False, tag, D(100))
    assert (e.kosten, e.ust, e.land, e.geschaetzt) == (D(10), D(2), "AT", False)  # abziehbar wie inländische USt.
    assert kosten.berechnen(k, D(10), None, None, "RC", "AT", False, tag, D(0)).kosten == D(12)
