"""Parser Wien Energie: anonymisierter Textauszug + echte Beispiel-PDF aus samples/ (falls vorhanden)."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import KennungTyp
from app.parsers import parser_erkennen
from app.parsers.basis import auf_summe_verteilen
from app.parsers.wien_energie import parsen_text

FIXTURE = Path(__file__).parent / "fixtures" / "wien_energie_beispiel.txt"
SAMPLES = sorted((Path(__file__).parent.parent / "samples").glob("*Wien Energie*.pdf"))


@pytest.fixture
def ergebnis():
    return parsen_text(FIXTURE.read_text(encoding="utf-8"))


def test_rechnungskopf(ergebnis):
    r = ergebnis.rechnung
    assert r.nummer == "7100000001"
    assert r.datum == date(2026, 10, 5)
    assert r.summe_netto == Decimal("71.47")
    assert (r.zeitraum_von, r.zeitraum_bis) == (date(2026, 9, 1), date(2026, 9, 30))
    assert ergebnis.warnungen == []


def test_tarifzeitfenster_ergeben_einen_vorgang(ergebnis):
    v = ergebnis.vorgaenge[0]
    assert (v.start, v.ende) == (datetime(2026, 9, 15, 18, 26), datetime(2026, 9, 16, 6, 53))
    assert len(v.rohdaten["abschnitte"]) == 3
    assert v.kwh == Decimal("50.000") and v.kwh_geschaetzt  # 12,433 h × 11 kW, gedeckelt auf 50
    assert v.kartenkennung == "100001" and v.kennung_typ == KennungTyp.kartennummer
    assert v.vertragsnummer == "AT-VIE-C00000001-A"
    assert v.standort == "1010 Wien, Teststraße 1"
    assert v.ladepunkt == "AT*VIE*E0000001*1"
    assert v.schaetzbasis.art == "AC" and v.schaetzbasis.nennleistung_kw == Decimal(11)
    assert v.rohdaten["standzeit_std"] == "12.433"


def test_umgebrochene_kopfzeile_und_nennleistung(ergebnis):
    dc150, dc50 = ergebnis.vorgaenge[1], ergebnis.vorgaenge[2]
    assert dc150.kartenkennung == "100001"  # Kartennummer stand in der Folgezeile
    assert dc150.kwh == Decimal("33.030")  # 0,367 h × 90 kW
    assert dc50.kwh == Decimal("15.850")  # 0,317 h × 50 kW (Nennleistung DC50)


def test_kwh_aus_rechnung_nicht_geschaetzt(ergebnis):
    v = ergebnis.vorgaenge[3]
    assert v.kwh == Decimal("20.500") and not v.kwh_geschaetzt
    assert v.schaetzbasis is None and "standzeit_std" not in v.rohdaten


def test_netto_summiert_exakt(ergebnis):
    netto = [v.kosten_netto for v in ergebnis.vorgaenge] + [g.betrag_netto for g in ergebnis.gebuehren]
    assert netto == [Decimal(x) for x in ("17.23", "10.42", "8.99", "10.08", "8.25", "8.25", "8.25")]
    assert sum(netto) == ergebnis.rechnung.summe_netto


def test_gebuehren(ergebnis):
    a, b, c = ergebnis.gebuehren
    assert a.kartenkennung == "100001"
    assert b.kartenkennung is None and b.vertragsnummer == "AT-VIE-C00000002-B"  # Vertrag ohne Ladevorgang
    assert c.kartenkennung == "100003"
    assert (a.zeitraum_von, a.zeitraum_bis) == (date(2026, 9, 1), date(2026, 9, 30))
    assert a.bezeichnung == "Monatliche Grundgebühr für Tarif-Paket PLUS 1.9.2026 - 30.9.2026"


def test_unvollstaendiges_pdf_warnt():
    text = FIXTURE.read_text(encoding="utf-8")
    gekuerzt = text[:text.index("Vertrag Wien Energie AT-VIE-C00000003-C")]
    e = parsen_text(gekuerzt)
    assert any("Details" in w and "C00000003" in w for w in e.warnungen)
    ohne_summe = parsen_text(text.replace("Rechnungsbetrag (inkl. USt.) 85,76", "Rechnungsbetrag fehlt"))
    assert any("unvollständig" in w for w in ohne_summe.warnungen)


def test_abweichende_abschnittssumme_warnt():
    text = FIXTURE.read_text(encoding="utf-8").replace("Wien Energie-Ladestationen 12,10\nGebühren",
                                                      "Wien Energie-Ladestationen 13,10\nGebühren")
    assert any("Positionen" in w for w in parsen_text(text).warnungen)


def test_auf_summe_verteilen():
    werte = [Decimal("17.225"), Decimal("10.41667"), Decimal("8.99167")]
    assert auf_summe_verteilen(werte, Decimal("36.64")) == [Decimal("17.23"), Decimal("10.42"), Decimal("8.99")]
    assert auf_summe_verteilen(werte, Decimal("50.00")) is None


@pytest.mark.skipif(not SAMPLES, reason="Beispiel-PDF nicht vorhanden (samples/ ist nicht im Git)")
def test_beispiel_pdf():
    from app.parsers.wien_energie import WienEnergieParser
    pdf = SAMPLES[0]
    assert parser_erkennen(pdf) == "wien_energie"
    e = WienEnergieParser().parsen(pdf)
    assert e.warnungen == []
    assert e.rechnung.summe_netto == Decimal("574.46")
    assert len(e.gebuehren) == 10
    assert sum(v.kosten_netto for v in e.vorgaenge) + sum(g.betrag_netto for g in e.gebuehren) == Decimal("574.46")
    assert all(v.kwh_geschaetzt for v in e.vorgaenge)  # nur Zeittarife
    assert all(v.kwh <= 50 for v in e.vorgaenge)
    assert sum(1 for g in e.gebuehren if g.kartenkennung is None) == 1  # Vertrag nur mit Grundgebühr
    assert len({v.kartenkennung for v in e.vorgaenge}) == 9
    assert all(v.kartenkennung and v.kartenkennung.isdigit() for v in e.vorgaenge)


def test_aeltere_rechnung_vertragsnummer_als_ladekarte():
    """Ältere Rechnungen nennen nach „Ladekarte:“ die Vertragsnummer statt einer Kartennummer."""
    text = FIXTURE.read_text(encoding="utf-8").replace("Ladekarte: 100001", "Ladekarte: AT-VIE-C00000001-A")
    text = text.replace("DC150 - Ladekarte:\n100001", "DC150 - Ladekarte:\nAT-VIE-C00000001-A")
    e = parsen_text(text)
    assert e.warnungen == [] and len(e.vorgaenge) == len(parsen_text(FIXTURE.read_text(encoding="utf-8")).vorgaenge)
    v = e.vorgaenge[0]
    assert (v.kartenkennung, v.kennung_typ) == ("AT-VIE-C00000001-A", KennungTyp.vertragsnummer)
    assert any(x.kartenkennung == "AT-VIE-C00000001-A" for x in e.vorgaenge if x.ladepunkt.endswith("E0002*1"))


def test_aeltere_rechnung_echt():
    from tests.beispiele import beispiel
    pdf = beispiel("7d3a5f89fca6")  # Wien Energie, Servicerechnung 05/2023
    assert parser_erkennen(pdf) == "wien_energie"
    from app.parsers import PARSER
    e = PARSER["wien_energie"].parsen(pdf)
    assert e.warnungen == [] and len(e.vorgaenge) == 4 and len(e.gebuehren) == 1
    assert all(v.kennung_typ == KennungTyp.vertragsnummer for v in e.vorgaenge)
    assert sum(v.kosten_netto for v in e.vorgaenge) + e.gebuehren[0].betrag_netto == e.rechnung.summe_netto
