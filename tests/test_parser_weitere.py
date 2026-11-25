"""Parser DCS, LINZ AG, Salzburg AG, EnBW: anonymisierte Textauszüge + echte Beispiel-PDFs (falls vorhanden)."""
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Gebuehr, KennungTyp, Ladekarte, Ladevorgang, Msp
from app.parsers import PARSER, dcs, elli, enbw, linz_ag, parser_erkennen, salzburg_ag
from app.services.importe import bestaetigen, hochladen, vorschau_berechnen
from tests.beispiele import beispiel

D = Decimal
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = Path(__file__).parent.parent / "samples"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _summe(e) -> Decimal:
    return sum((v.kosten_netto for v in e.vorgaenge), D(0)) + sum((g.betrag_netto for g in e.gebuehren), D(0))


# ------------------------------------------------------------------ DCS

def test_dcs():
    e = dcs.parsen_text(_text("dcs_beispiel.txt"))
    r = e.rechnung
    assert e.warnungen == []
    assert r.nummer == "BAT00000001, BHU00000001"  # Übersichtsseite übersprungen
    assert (r.datum, r.zeitraum_von, r.zeitraum_bis) == (date(2026, 6, 30), date(2026, 6, 1), date(2026, 6, 30))
    assert r.summe_netto == D("38.82") == _summe(e)  # 30,00 + 8,82; ausländische MwSt ignoriert

    a, b, hu = e.vorgaenge
    assert a.kartenkennung == "AT*DCS*000TST*1" and a.kennung_typ == KennungTyp.vertragsnummer
    assert a.kartenbezeichnung.startswith("DCS AT-B-1000001-1")
    assert (a.start, a.ende) == (datetime(2026, 6, 9, 18, 12), datetime(2026, 6, 9, 18, 18))  # UTC → MESZ
    assert a.kwh == D("11.000") and not a.kwh_geschaetzt
    assert a.ladepunkt == "AT*OMV*EA0000DC0001"  # ID in die nächste Zeile umgebrochen
    assert a.standort == "Teststraße 1 1010, Teststadt am See"
    # über Mitternacht und Monatsgrenze: 30.06. 23:30 UTC = 01.07. 01:30 Wiener Zeit
    assert (b.start, b.ende) == (datetime(2026, 7, 1, 1, 10), datetime(2026, 7, 1, 2, 15))
    assert b.ladepunkt == "AT*MER*E000002*02"
    # 6,4481 + 14,4044 = 20,8525 → centgenau auf 20,85 verteilt
    assert (a.kosten_netto, b.kosten_netto) == (D("6.45"), D("14.40"))
    assert hu.kosten_netto == D("8.82") and hu.ladepunkt == "HUTEST00001*1"
    assert (hu.start, hu.ende) == (datetime(2026, 6, 5, 7, 41), datetime(2026, 6, 5, 10, 30))

    ionity, grund = e.gebuehren
    assert (ionity.bezeichnung, ionity.betrag_netto, ionity.kartenkennung) == ("IONITY Plus", D("4.99"),
                                                                              "AT*DCS*000TST*1")
    assert (grund.bezeichnung, grund.zeitraum_von, grund.zeitraum_bis) == (
        "Monatliche Grundgebühr", date(2026, 6, 1), date(2026, 6, 30))  # Enddatum in der Folgezeile


def test_dcs_abweichung_warnt():
    text = _text("dcs_beispiel.txt").replace("Gesamtbetrag* 30,00 EUR 30,00 EUR", "Gesamtbetrag* 31,00 EUR 31,00 EUR")
    assert any("Summe Ladevorgänge" in w for w in dcs.parsen_text(text).warnungen)


def test_dcs_gutschein_und_dezimalpunkt():
    """DE-Teilrechnung schreibt kWh mit Dezimalpunkt („15.757 kWh“); Gutscheine sind negative Gebühren."""
    e = dcs.parsen_text(_text("dcs_gutschein_de.txt"))
    assert e.warnungen == []
    assert e.rechnung.nummer == "BAT00000002, BDE00000002"
    assert e.rechnung.summe_netto == D("17.76") == _summe(e)  # 11,27 + 6,49 (deutsche MwSt ignoriert)
    assert len(e.vorgaenge) == 3
    de = e.vorgaenge[2]
    assert (de.kwh, de.kosten_netto) == (D("15.757"), D("6.49"))
    assert de.start == datetime(2026, 9, 18, 17, 21)  # 15:21 UTC = 17:21 MESZ
    assert de.ladepunkt == "DE*TST*E0AB001*02*05"  # letzter Teil in der Folgezeile
    assert de.standort == "Probeweg 4, 99999 Probedorf am See"
    gutscheine = [g for g in e.gebuehren if g.bezeichnung == "Verrechnete Gutscheine"]
    assert sorted(g.betrag_netto for g in gutscheine) == [D("-4.99"), D("-4.16")]
    assert all(g.kartenkennung == "AT*DCS*000TST*2" for g in e.gebuehren)
    assert sum(g.betrag_netto for g in e.gebuehren) == 0  # Gutscheine gleichen Gebühren aus


@pytest.mark.parametrize("text, erwartet", [("12,340", "12.340"), ("15.757", "15.757"), ("1.234,5", "1234.5"),
                                            ("7", "7")])
def test_dcs_kwh_lesen(text, erwartet):
    assert dcs.kwh_lesen(text) == D(erwartet)


def test_dcs_dauer_eine_zeile_tiefer():
    """Lange Vorgänge (z. B. „11:07 hh:mm“): die Dauer steht unter „(kWh)“, eine Zeile tiefer als sonst."""
    normal = dcs.parsen_text(_text("dcs_beispiel.txt"))
    text = _text("dcs_beispiel.txt").replace(
        "AT * MER * E000002 * 02 Salzburg 1:05 hh:mm (kWh) 0,0000 EUR\nTest Betreiber\n",
        "AT * MER * E000002 * 02 Salzburg (kWh) 0,0000 EUR\nTest Betreiber 11:07 hh:mm\nGmbH & Co KG\n")
    e = dcs.parsen_text(text)
    assert not any("Ladedauer" in w for w in e.warnungen)
    v, vorher = e.vorgaenge[1], normal.vorgaenge[1]
    assert v.ende - v.start == timedelta(hours=11, minutes=7)
    assert (v.ladepunkt, v.standort) == (vorher.ladepunkt, vorher.standort)  # Ort ohne „(kWh) 0,0000 EUR“
    # Ohne Dauer: nicht die des nächsten Vorgangs lesen, sondern warnen
    ohne = _text("dcs_beispiel.txt").replace("Salzburg 1:05 hh:mm (kWh)", "Salzburg (kWh)")
    assert any("Ladedauer nicht gefunden" in w for w in dcs.parsen_text(ohne).warnungen)


def test_dcs_unlesbarer_vorgang_warnt():
    """Ein nicht lesbarer Ladevorgang darf nicht stillschweigend fehlen."""
    text = _text("dcs_gutschein_de.txt").replace("15.757 kWh DC 6,4882", "15.757 kWhDC 6,4882")
    w = dcs.parsen_text(text).warnungen
    assert any("nicht lesbar" in x for x in w)
    assert any("BDE00000002" in x and "passen nicht" in x for x in w)


# ------------------------------------------------------------------ LINZ AG

def test_linz_ag():
    e = linz_ag.parsen_text(_text("linz_ag_beispiel.txt"))
    r = e.rechnung
    assert e.warnungen == []
    assert (r.nummer, r.datum, r.summe_netto) == ("1900000001", date(2026, 10, 31), D("45.40"))
    assert _summe(e) == r.summe_netto
    a, b, c = e.vorgaenge
    assert (a.kartenkennung, a.kennung_typ, a.kwh, a.kosten_netto) == ("10001", KennungTyp.kartennummer,
                                                                     D("20.000"), D("6.50"))
    assert (a.start, a.ende) == (datetime(2026, 10, 13, 7, 45), datetime(2026, 10, 13, 10, 25))  # 160 MIN
    assert a.standort == "Teststraße 1, Testdorf"
    assert b.kosten_netto == D("16.90")  # Energie + Infrastrukturbelegung
    assert b.ende == datetime(2026, 10, 22, 20, 41)  # Dauer umgebrochen („279 / MIN“)
    assert (c.kartenkennung, c.kwh) == ("10003", D("10.000"))  # über Seitenumbruch
    assert [(g.kartenkennung, g.betrag_netto) for g in e.gebuehren] == [
        ("10001", D("6.25")), ("10002", D("6.25")), ("10003", D("6.25"))]
    assert e.gebuehren[0].bezeichnung == "Grundgebühr (anteilig 1/3)"
    assert (e.gebuehren[0].zeitraum_von, e.gebuehren[0].zeitraum_bis) == (date(2026, 10, 1), date(2026, 10, 31))


def test_linz_ag_cent_aufteilung():
    text = _text("linz_ag_beispiel.txt").replace("3 MON 18,75", "3 MON 18,76").replace("45,40", "45,41")
    e = linz_ag.parsen_text(text)
    assert [g.betrag_netto for g in e.gebuehren] == [D("6.26"), D("6.25"), D("6.25")]
    assert e.warnungen == []


# ------------------------------------------------------------------ Salzburg AG

def test_salzburg_ag():
    e = salzburg_ag.parsen_text(_text("salzburg_ag_beispiel.txt"))
    r = e.rechnung
    assert e.warnungen == []
    assert (r.nummer, r.summe_netto, r.zeitraum_von) == ("60000002", D("33.00"), date(2026, 10, 1))  # ohne Deckblatt
    assert _summe(e) == r.summe_netto
    a, b = e.vorgaenge
    assert (a.kartenkennung, a.externe_id, a.ladepunkt) == ("K00001", "100000001", "AT*SZG*E000001")
    assert (a.start, a.ende, a.kwh, a.kosten_netto) == (datetime(2026, 10, 3, 11, 37), datetime(2026, 10, 3, 12, 11),
                                                        D("28.250"), D("9.89"))
    assert a.standort == "Salzburg AG Hypercharger Testmarkt rechts, Teststraße 3, Testort"
    assert "Testtal" in b.standort and b.externe_id == "100000002"
    assert [(g.kartenkennung, g.betrag_netto) for g in e.gebuehren] == [("K00001", D("2.50")), ("K00002", D("2.50"))]


def test_salzburg_ag_seitenkopf_mitten_im_vorgang():
    """Seitenumbruch zwischen Adresszeilen: der Seitenkopf (Rechnungsempfänger mit Adresse, Vertragskonto) landet
    nicht im Standort – ohne festen Empfängernamen."""
    kopf = ("Salzburg AG, Bayerhamerstraße 16, 5020 Salzburg Dokument-Nr. 60000002\nDatum 31.10.2026\n"
            "Beispiel Handel GmbH Zeitraum 01.10.2026 - 31.10.2026\nMusterallee 7\n1010 Wien\nAustria Ihre UID ATU0\n"
            "Vertragskonto 123\nSeite AT 3 / 3\n")
    text = _text("salzburg_ag_beispiel.txt").replace("Probeweg 113, Probe im\n", "Probeweg 113, Probe im\n" + kopf)
    _, b = salzburg_ag.parsen_text(text).vorgaenge
    assert b.standort == "Salzburg AG Hypercharger Probe Süd - rechts, Probeweg 113, Probe im, Testtal"
    assert b.ladepunkt == "AT*SZG*E000002" and b.externe_id == "100000002"


def test_salzburg_ag_aelteres_format():
    """Älteres Layout: „Belegnummer“, Beträge mit nachgestelltem „EUR“, Transaktionsnummer mit Session, Land aus
    „(AT)“ in der Position."""
    e = salzburg_ag.parsen_text(_text("salzburg_ag_alt_beispiel.txt"))
    r = e.rechnung
    assert e.warnungen == []
    assert (r.nummer, r.datum, r.zeitraum_von, r.zeitraum_bis, r.summe_netto) == (
        "60000010", date(2023, 10, 31), date(2023, 10, 2), date(2023, 10, 5), D("12.40"))
    assert _summe(e) == r.summe_netto
    a, b = e.vorgaenge
    assert (a.kartenkennung, a.externe_id, a.ladepunkt) == ("00042", "10000001", "AT*TST*E0001*1")
    assert (a.start, a.ende, a.kwh, a.kosten_netto) == (datetime(2023, 10, 2, 8, 12), datetime(2023, 10, 2, 8, 53),
                                                        D("6.200"), D("2.15"))
    assert a.standort == "Test Energie - Probe Parkhaus Teststraße 7, Testort"  # Seitenkopf nicht im Standort
    assert a.rohdaten["session"] == "20000001" and a.rohdaten["land"] == "AT"
    assert (b.externe_id, b.kosten_netto) == ("10000002", D("10.25"))
    assert [(g.kartenkennung, g.betrag_netto) for g in e.gebuehren] == [("00042", D("0.00"))]


def test_salzburg_ag_aelteres_format_echt():
    pfad = beispiel("7bf78dbeb8be")  # Salzburg AG, älteres Layout (2023)
    assert parser_erkennen(pfad) == "salzburg_ag"
    e = PARSER["salzburg_ag"].parsen(pfad)
    assert e.warnungen == [] and len(e.vorgaenge) == 3
    assert _summe(e) == e.rechnung.summe_netto
    assert all(v.externe_id and v.ladepunkt and v.ende > v.start for v in e.vorgaenge)


def test_salzburg_ag_abweichung_warnt():
    text = _text("salzburg_ag_beispiel.txt").replace("EUR 28,00 20,00% EUR 28,00", "EUR 29,00 20,00% EUR 29,00")
    w = salzburg_ag.parsen_text(text).warnungen
    assert any("Ladenachweis" in x for x in w) and any("Nettobetrag" in x for x in w)


# ------------------------------------------------------------------ EnBW

def test_enbw():
    e = enbw.parsen_text(_text("enbw_beispiel.txt"))
    r = e.rechnung
    assert (r.nummer, r.datum, r.zeitraum_von, r.zeitraum_bis, r.summe_netto) == (
        "180000000001", date(2026, 11, 3), date(2026, 10, 1), date(2026, 10, 31), D("38.76"))
    a, b = e.vorgaenge
    assert (a.kartenkennung, a.externe_id, a.ladepunkt) == ("T100-AAAA-BBBB-0001", "TST-0001", "AT*TST*E0001*1")
    assert (a.start, a.ende) == (datetime(2026, 10, 28, 12, 41, 37), datetime(2026, 10, 28, 13, 13, 28))
    assert (a.kwh, a.kosten_netto, a.standort) == (D("37.003"), D("12.02"), "Teststraße 60, 4999 Testort")
    assert (b.kwh, b.kosten_netto, b.standort) == (D("20.456"), D("9.98"), "Probeweg 2, 1010 Wien")  # inkl. Zeit
    grund, sonstige = e.gebuehren
    assert (grund.kartenkennung, grund.betrag_netto) == ("T100-AAAA-BBBB-0001", D("11.76"))
    assert (sonstige.kartenkennung, sonstige.betrag_netto) == (None, D("5.00"))  # Kartenbestellung
    assert len(e.warnungen) == 1 and "ohne Kartenbezug" in e.warnungen[0]
    assert _summe(e) == r.summe_netto


# ------------------------------------------------------------------ echte Beispiel-PDFs

ERWARTET = {  # Prüfsumme der Datei (tests/beispiele.py) → (Parser, Vorgänge, Gebühren)
    "b96d3425e06a": ("dcs", 12, 2),
    "17c9b255dafb": ("dcs", 8, 1),
    "3aacf340ca8b": ("dcs", 2, 2),
    "2b91c8df5861": ("dcs", 3, 4),  # AT + DE (kWh mit Dezimalpunkt), Gutscheine
    "9516f0bcf44e": ("linz_ag", 5, 4),
    "76116efa750d": ("salzburg_ag", 10, 6),
    "54152a7b587e": ("enbw", 0, 1),
    "ca74350c2acc": ("enbw", 1, 0),
}


@pytest.mark.parametrize("pruefsumme", ERWARTET)
def test_beispiel_pdfs(pruefsumme):
    pfad = beispiel(pruefsumme)
    key, anzahl_vorgaenge, anzahl_gebuehren = ERWARTET[pruefsumme]
    assert parser_erkennen(pfad) == key
    e = PARSER[key].parsen(pfad)
    assert e.warnungen == []
    assert (len(e.vorgaenge), len(e.gebuehren)) == (anzahl_vorgaenge, anzahl_gebuehren)
    assert _summe(e) == e.rechnung.summe_netto
    assert all(v.kartenkennung and v.ende >= v.start and v.kwh > 0 for v in e.vorgaenge)
    if key in ("salzburg_ag", "enbw"):
        assert all(v.externe_id for v in e.vorgaenge)


# ------------------------------------------------------------------ Import-Ablauf

@pytest.mark.parametrize("key, fixture", [("dcs", "dcs_beispiel.txt"), ("linz_ag", "linz_ag_beispiel.txt"),
                                          ("salzburg_ag", "salzburg_ag_beispiel.txt"), ("enbw", "enbw_beispiel.txt"),
                                          ("elli", "elli_beispiel.txt")])
def test_import_ablauf(db, monkeypatch, key, fixture):
    modul = {"dcs": dcs, "linz_ag": linz_ag, "salzburg_ag": salzburg_ag, "enbw": enbw, "elli": elli}[key]
    text = _text(fixture)
    monkeypatch.setattr(type(PARSER[key]), "parsen", lambda self, pfad: modul.parsen_text(text))
    msp = db.scalar(select(Msp).where(Msp.parser_key == key))

    imp = hochladen(db, f"{key}.pdf", f"%PDF-1.4 {key}".encode())
    imp.parser_key, imp.msp_id = key, msp.id
    vs = vorschau_berechnen(db, imp)
    assert not [w for w in vs.warnungen if "Rechnungssumme" in w]
    bestaetigen(db, imp, {})

    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)).all()
    gebuehren = db.scalars(select(Gebuehr).where(Gebuehr.import_id == imp.id)).all()
    assert sum(v.kosten_netto for v in vorgaenge) + sum(g.betrag_netto for g in gebuehren) == imp.rechnungssumme_netto
    assert all(v.ladekarte_id for v in vorgaenge)
    karten = db.scalars(select(Ladekarte).where(Ladekarte.msp_id == msp.id)).all()
    # auch Karten, die nur eine Gebühr haben (z. B. Salzburg AG K00002), werden angelegt
    assert all(g.ladekarte_id in {k.id for k in karten} for g in gebuehren
               if g.bezeichnung != "Sonstige Positionen ohne Kartenbezug")
    if key == "salzburg_ag":
        assert {k.kennung: k.bezeichnung for k in karten}["K00002"] == "Salzburg AG Dienstwagen 2"
    if key == "dcs":
        assert all(k.kennung_typ == KennungTyp.vertragsnummer for k in karten)

    # erneuter Import derselben Rechnung (andere Datei): alle Vorgänge sind Dubletten
    zweiter = hochladen(db, f"{key}-kopie.pdf", f"%PDF-1.4 {key} kopie".encode())
    zweiter.parser_key, zweiter.msp_id = key, msp.id
    vs = vorschau_berechnen(db, zweiter)
    assert vs.vorgaenge and all(x.duplikat for x in vs.vorgaenge)
    if imp.rechnungsnummer:  # Elli: Übersicht ohne Rechnungsnummer
        assert any("bereits mit der Datei" in w for w in vs.warnungen)


def test_dcs_uhrzeit_ueberlauf():
    """DCS schreibt gelegentlich Uhrzeiten wie „24:57“: Überlauf in den Folgetag (UTC), dann Wiener Zeit."""
    from app.parsers.dcs import _uhrzeit_ungueltig, _utc_nach_wien
    assert _utc_nach_wien("15.12.2025", "24:57") == datetime(2025, 12, 16, 1, 57)  # 00:57 UTC = 01:57 MEZ
    assert _utc_nach_wien("30.06.2026", "22:10") == datetime(2026, 7, 1, 0, 10)  # Sommerzeit
    assert _uhrzeit_ungueltig("24:57") and not _uhrzeit_ungueltig("23:59")


def test_dcs_zeitzone_aus_spaltenkopf():
    """„Datum/Uhrzeit (UTC)“ → UTC umrechnen; nur „Datum/Uhrzeit“ (neueres Format) → bereits Ortszeit."""
    from app.parsers.dcs import _zeiten_in_utc
    assert _zeiten_in_utc("Datum/Uhrzeit (UTC) Adresse Menge Produkt")
    assert not _zeiten_in_utc("Datum/Uhrzeit Adresse Menge Produkt Grundbetrag")
    assert _zeiten_in_utc("ohne Spaltenkopf")  # wie bisher: UTC


def test_beispiel_dcs_mit_ungueltiger_uhrzeit():
    pfad = beispiel("cb493aa654f0")
    assert parser_erkennen(pfad) == "dcs"
    e = PARSER["dcs"].parsen(pfad)
    assert (len(e.vorgaenge), len(e.gebuehren)) == (5, 1)
    assert _summe(e) == e.rechnung.summe_netto
    [warnung] = e.warnungen
    m = re.search(r"ungültige Uhrzeit „(\d\d\.\d\d\.\d{4}) 24:57“", warnung)
    assert m
    # Spalte nur „Datum/Uhrzeit“ (ohne „(UTC)“): Zeiten sind bereits Ortszeit – 24:57 = 00:57 am Folgetag
    folgetag = datetime.strptime(m.group(1), "%d.%m.%Y") + timedelta(days=1)
    assert any(v.start == folgetag.replace(minute=57) for v in e.vorgaenge)
    assert any(v.start.time() == time(14, 36) for v in e.vorgaenge)  # wie in der Rechnung, nicht 15:36


# ------------------------------------------------------------------ EnBW Privatkonto (gleicher Parser)

def test_enbw_privatkonto():
    """Privatkonto: „Start:/Ende:“ statt „Ladestart:/Ladeende:“, Kopf je Vorgang, keine Grundgebühr;
    abgebrochener Vorgang mit 0 kWh und 0 € wird nicht übernommen (Hinweis)."""
    e = enbw.parsen_text(_text("enbw_privat_beispiel.txt"))
    assert e.rechnung.nummer == "180000000002" and e.rechnung.summe_netto == D("11.44") == _summe(e)
    assert (e.rechnung.datum, e.rechnung.zeitraum_von) == (date(2026, 3, 10), date(2026, 2, 1))
    [v] = e.vorgaenge
    assert (v.start, v.ende) == (datetime(2026, 2, 11, 18, 3, 2), datetime(2026, 2, 11, 18, 23, 12))
    assert (v.kwh, v.kosten_netto, v.stromart) == (D("24.532"), D("11.44"), "DC")
    assert v.kartenkennung == "P100-AAAA-BBBB-0002" and v.externe_id == "TST-PRIV-0002"
    assert v.standort == "Teststraße 180, 3999 Testort" and not e.gebuehren
    assert e.warnungen == ["Abgebrochener Ladevorgang vom 11.02.2026 17:42:11 ohne Energie und Kosten nicht übernommen."]


def test_enbw_privatkonto_echt():
    pfad = beispiel("2ae5f9f43cd0")  # EnBW, Privatkonto
    assert parser_erkennen(pfad) == "enbw"
    e = PARSER["enbw"].parsen(pfad)
    assert len(e.vorgaenge) == 1 and _summe(e) == e.rechnung.summe_netto == D("11.44") and not e.gebuehren
    assert len(e.warnungen) == 1 and "Abgebrochener" in e.warnungen[0]
