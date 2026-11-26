"""EnBW mobility+ als CSV-Export: Parser, Erkennung und keine Doppelten bei PDF und CSV derselben Rechnung."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Gebuehr, Ladevorgang, Msp
from app.parsers import PARSER, enbw, enbw_csv, parser_erkennen
from app.services.importe import bestaetigen, hochladen, vorschau_berechnen

D = Decimal
FIXTURES = Path(__file__).parent / "fixtures"
CSV = FIXTURES / "enbw_beispiel.csv"  # erfunden, dieselbe Rechnung wie enbw_beispiel.txt (PDF-Text)
NAME = "Rechnung_2026_10_AT_180000000001.csv"


def test_csv_parsen():
    assert parser_erkennen(CSV) == "enbw"
    e = enbw_csv.parsen(CSV, NAME)
    r = e.rechnung
    assert (r.nummer, r.datum, r.zeitraum_von, r.zeitraum_bis, r.summe_netto) == (
        "180000000001", date(2026, 11, 3), date(2026, 10, 1), date(2026, 10, 31), D("38.76"))
    a, b = e.vorgaenge
    assert (a.kartenkennung, a.externe_id, a.ladepunkt, a.stromart) == (
        "T100-AAAA-BBBB-0001", "TST-0001", "AT*TST*E0001*1", "DC")
    assert (a.start, a.ende) == (datetime(2026, 10, 28, 12, 41, 37), datetime(2026, 10, 28, 13, 13, 28))
    assert (a.kwh, a.kosten_netto, a.standort) == (D("37.000"), D("12.02"), "Teststraße 60, 4999 Testort")  # 12,0250
    assert (b.kwh, b.kosten_netto, b.stromart) == (D("20.460"), D("9.98"), "AC")  # inkl. Blockiergebühr
    assert a.rohdaten["land"] == "AT" and "Fahrer Nachname" not in a.rohdaten["csv"]  # keine Personendaten
    grund, bestellung = e.gebuehren
    assert (grund.bezeichnung, grund.kartenkennung, grund.betrag_netto) == (
        "Monatliche Grundgebühr", "T100-AAAA-BBBB-0001", D("11.76"))
    assert (bestellung.kartenkennung, bestellung.betrag_netto, bestellung.zeitraum_von) == (
        None, D("5.00"), date(2026, 10, 1))
    assert e.warnungen == []


def test_abrechnungsmonat_ohne_dateinamen():
    """Ohne Monat im Dateinamen: aus den Ladevorgängen (hier Oktober), sonst Vormonat des Rechnungsdatums."""
    e = enbw_csv.parsen(CSV, "export.csv")
    assert (e.rechnung.zeitraum_von, e.rechnung.zeitraum_bis) == (date(2026, 10, 1), date(2026, 10, 31))


def _importieren(db, name, inhalt):
    imp = hochladen(db, name, inhalt)
    msp_id = db.scalar(select(Msp.id).where(Msp.parser_key == "enbw"))
    if name.endswith(".pdf"):  # Platzhalter-PDF: Parser wie in den übrigen Import-Tests setzen
        imp.parser_key, imp.msp_id = "enbw", msp_id
    assert imp.parser_key == "enbw" and imp.msp_id == msp_id  # CSV wird am Inhalt erkannt
    return imp, vorschau_berechnen(db, imp)


def _anzahl(db, modell, *bedingungen):
    return db.scalar(select(func.count()).select_from(modell).where(*bedingungen))


@pytest.mark.parametrize("reihenfolge", ["pdf_zuerst", "csv_zuerst"])
def test_pdf_und_csv_keine_doppelten(db, monkeypatch, reihenfolge):
    text = (FIXTURES / "enbw_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(type(PARSER["enbw"]), "parsen",
                        lambda self, pfad: enbw.parsen_text(text) if enbw.ist_pdf(pfad) else enbw_csv.parsen(pfad))
    dateien = [("rechnung.pdf", b"%PDF-1.4 enbw"), (NAME, CSV.read_bytes())]
    if reihenfolge == "csv_zuerst":
        dateien.reverse()

    erster, vs = _importieren(db, *dateien[0])
    assert len(vs.neue) == 2 and len(vs.neue_gebuehren) == 2
    bestaetigen(db, erster, {})

    zweiter, vs = _importieren(db, *dateien[1])
    assert vs.neue == [] and vs.neue_gebuehren == [] and vs.summe_gebuehren == 0
    assert any("bereits mit der Datei" in w and "Gebühren werden übersprungen" in w for w in vs.warnungen)
    assert not any("Rechnungssumme" in w for w in vs.warnungen)
    bestaetigen(db, zweiter, {})
    assert _anzahl(db, Ladevorgang, Ladevorgang.import_id == zweiter.id) == 0
    assert _anzahl(db, Gebuehr, Gebuehr.import_id == zweiter.id) == 0
    assert _anzahl(db, Ladevorgang, Ladevorgang.externe_id.in_(["TST-0001", "TST-0002"])) == 2


# ------------------------------------------------------------------ echte Beispieldateien

# Prüfsummen statt Dateinamen (tests/beispiele.py): CSV und, falls vorhanden, dieselbe Rechnung als PDF
@pytest.mark.parametrize("csv_praefix, pdf_praefix, anzahl_vorgaenge, anzahl_gebuehren", [
    ("af984a6a2abb", "54152a7b587e", 0, 1),   # Rechnung 09: nur Grundgebühr
    ("08421d9ace02", "ca74350c2acc", 1, 0)])  # Rechnung 10: ein Ladevorgang
def test_beispiel_csv(csv_praefix, pdf_praefix, anzahl_vorgaenge, anzahl_gebuehren):
    from tests.beispiele import beispiel, finden
    csv = beispiel(csv_praefix)
    pdf = [p for p in [finden(pdf_praefix)] if p]
    assert parser_erkennen(csv) == "enbw"
    e = PARSER["enbw"].parsen_datei(csv, csv.name)
    assert e.warnungen == []
    assert (len(e.vorgaenge), len(e.gebuehren)) == (anzahl_vorgaenge, anzahl_gebuehren)
    if pdf:  # dieselbe Rechnung als PDF: gleiche Nummer, Zeitraum, Beträge und Transaktions-IDs
        p = PARSER["enbw"].parsen(pdf[0])
        assert (e.rechnung.nummer, e.rechnung.zeitraum_von, e.rechnung.summe_netto) == (
            p.rechnung.nummer, p.rechnung.zeitraum_von, p.rechnung.summe_netto)
        assert [v.externe_id for v in e.vorgaenge] == [v.externe_id for v in p.vorgaenge]
        assert [(v.start, v.ende, v.kosten_netto) for v in e.vorgaenge] == [
            (v.start, v.ende, v.kosten_netto) for v in p.vorgaenge]
        assert [(g.kartenkennung, g.betrag_netto) for g in e.gebuehren] == [
            (g.kartenkennung, g.betrag_netto) for g in p.gebuehren]


def test_erkennung_unabhaengig_von_der_reihenfolge(monkeypatch):
    """Jeder Parser entscheidet allein; passen mehrere, wird nicht geraten (Reihenfolge egal)."""
    import app.parsers as parsers

    class Gierig(type(PARSER["hastobe_csv"])):  # beansprucht jede Datei
        key, name = "gierig", "Gierig"

        def erkennen(self, pfad):
            return True

    assert parsers.passende_parser(CSV) == ["enbw"]
    for reihenfolge in ({"gierig": Gierig(), **PARSER}, {**PARSER, "gierig": Gierig()}):
        monkeypatch.setattr(parsers, "PARSER", reihenfolge)
        assert sorted(parsers.passende_parser(CSV)) == ["enbw", "gierig"]
        assert parsers.parser_erkennen(CSV) is None
