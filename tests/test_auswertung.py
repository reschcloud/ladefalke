"""Auswertungen: Zeiträume, Summen, Topliste, Rechenkern mit Datenbank, Export, Oberfläche."""
import io
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Gebuehr, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang,
                        Msp, Pauschale, ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.config import heute
from app.services import app_einstellungen
from app.services.auswertung import (FahrerZeile, Summe, art_wechseln, auswerten, topliste_berechnen, vergleichen,
                                     vormonat, zeitraum, zeitraum_aus_parametern, zeitraum_frei, zeitraum_mit)
from app.services.export import Spalte, Tabelle, csv_inhalt, xlsx_inhalt

D = Decimal
OEFF, PRIV = Ladeart.oeffentlich, Ladeart.privat


# ------------------------------------------------------------------ Zeiträume

@pytest.mark.parametrize("art, jahr, nr, von, bis, label", [
    ("monat", 2026, 2, date(2026, 2, 1), date(2026, 2, 28), "02/2026"),
    ("quartal", 2026, 3, date(2026, 7, 1), date(2026, 9, 30), "Q3 2026"),
    ("halbjahr", 2026, 1, date(2026, 1, 1), date(2026, 6, 30), "1. Halbjahr 2026"),
    ("jahr", 2026, 1, date(2026, 1, 1), date(2026, 12, 31), "2026"),
])
def test_zeitraeume(art, jahr, nr, von, bis, label):
    z = zeitraum(art, jahr, nr)
    assert (z.von, z.bis, z.label) == (von, bis, label)


def test_blaettern():
    assert zeitraum("monat", 2026, 1).verschoben(-1) == zeitraum("monat", 2025, 12)
    assert zeitraum("quartal", 2026, 4).verschoben(1) == zeitraum("quartal", 2027, 1)
    assert zeitraum("halbjahr", 2026, 1).verschoben(-1) == zeitraum("halbjahr", 2025, 2)
    assert zeitraum("jahr", 2026).verschoben(1) == zeitraum("jahr", 2027)
    frei = zeitraum_frei(date(2026, 9, 1), date(2026, 9, 10)).verschoben(1)
    assert (frei.von, frei.bis) == (date(2026, 9, 11), date(2026, 9, 20))


def test_zeitraum_mit_und_parameter():
    assert zeitraum_mit(date(2026, 8, 15), "quartal") == zeitraum("quartal", 2026, 3)
    assert vormonat(date(2026, 1, 15)) == zeitraum("monat", 2025, 12)
    standard = zeitraum("monat", 2026, 10)
    assert zeitraum_aus_parametern("quartal", "2026", "3", "", "", standard) == zeitraum("quartal", 2026, 3)
    assert zeitraum_aus_parametern("frei", "", "", "2026-09-30", "2026-09-01", standard).von == date(2026, 9, 1)
    assert zeitraum_aus_parametern("monat", "abc", "", "", "", standard) == standard
    assert zeitraum_aus_parametern("unsinn", "", "", "", "", standard) == standard
    assert zeitraum_aus_parametern("quartal", "2026", "7", "", "", standard) == zeitraum("quartal", 2026, 4)
    # ohne Jahr/Nummer (Aufruf ohne Parameter): Standardzeitraum, nicht der Jänner
    assert zeitraum_aus_parametern("monat", "", "", "", "", standard) == standard
    assert zeitraum_aus_parametern("quartal", "", "", "", "", standard) == zeitraum("quartal", 2026, 4)


def test_art_wechseln_springt_zum_vormonat():
    standard = zeitraum("monat", 2026, 10)  # Vormonat
    jahr = zeitraum("jahr", 2026)
    assert art_wechseln(jahr, "monat", standard) == standard  # nicht Jänner
    assert art_wechseln(jahr, "quartal", standard) == zeitraum("quartal", 2026, 4)
    assert art_wechseln(zeitraum("monat", 2026, 3), "jahr", standard) == jahr
    # Vergangenes Jahr: dessen letzter Monat; künftiges: dessen erster
    assert art_wechseln(zeitraum("jahr", 2025), "monat", standard) == zeitraum("monat", 2025, 12)
    assert art_wechseln(zeitraum("jahr", 2027), "halbjahr", standard) == zeitraum("halbjahr", 2027, 1)


# ------------------------------------------------------------------ Summen und Topliste

def test_summe_gewichteter_preis():
    s = Summe()
    s.vorgang(D(10), D("5.00"), OEFF, False)    # 0,50 €/kWh
    s.vorgang(D(90), D("27.00"), PRIV, True)    # 0,30 €/kWh
    s.vorgang(D(0), D(0), PRIV, False)          # zählt nicht als Ladevorgang
    s.gebuehr(D("8.00"), OEFF)
    assert s.anzahl == 2
    assert s.preis == D("0.4")                  # (5 + 27 + 8) / 100 – kein Mittel der Mittelwerte
    assert s.preis_oeffentlich == D("1.3")      # (5 + 8) / 10
    assert s.anteil_geschaetzt == D("0.9")
    assert s.anteil_oeffentlich_kwh == D("0.1")
    assert (s + s).kwh == D(200)


def _zeile(name, kwh, kosten, geschaetzt=D(0)):
    z = FahrerZeile(Fahrer(name=name))
    z.summe.vorgang(D(kwh), D(kosten), OEFF, False)
    z.summe.kwh_geschaetzt = geschaetzt
    return z


@pytest.fixture
def flotte():
    return [_zeile("A", 100, 25), _zeile("B", 20, 2), _zeile("C", 200, 80), _zeile("D", 50, 25), _zeile("E", 15, 15)]


def test_topliste_von_hand_nachgerechnet(flotte):
    t = topliste_berechnen(flotte, None, D(20))
    assert t.p_flotte == D(147) / D(385)
    assert t.m == D(50) and not t.m_aus_einstellung  # Median von 15, 20, 50, 100, 200
    assert t.teilnehmer == 4  # E unter der Mindestmenge, B (genau 20 kWh) dabei
    assert [e.fahrer.name for e in t.guenstigste] == ["A", "B", "C"]
    assert [e.fahrer.name for e in t.teuerste] == ["D"]  # keine Überschneidung
    a = t.guenstigste[0]
    assert round(a.score, 6) == round((D(25) + 50 * t.p_flotte) / 150, 6) == D("0.293939")
    # B: günstigster Ø-Preis (0,10), wegen wenig kWh aber nur Platz 2 (Score 0,3013 > 0,2939)
    assert t.guenstigste[1].preis == D("0.1")
    assert round(t.guenstigste[1].score, 4) == D("0.3013")


def test_topliste_m_aus_einstellung(flotte):
    t = topliste_berechnen(flotte, D(0), D(20))  # m = 0 → nur eigener Preis zählt
    assert t.m_aus_einstellung
    assert [e.fahrer.name for e in t.guenstigste] == ["B", "A", "C"]


def test_topliste_sechs_fahrzeuge(flotte):
    flotte += [_zeile("F", 300, 150), _zeile("G", 40, 8)]
    t = topliste_berechnen(flotte, None, D(20))
    assert len(t.guenstigste) == 3 and len(t.teuerste) == 3
    assert t.teuerste[0].score >= t.teuerste[1].score >= t.teuerste[2].score
    assert not {e.fahrer.name for e in t.guenstigste} & {e.fahrer.name for e in t.teuerste}


def test_topliste_leer():
    t = topliste_berechnen([], None, D(20))
    assert t.teilnehmer == 0 and t.p_flotte is None


# ------------------------------------------------------------------ Rechenkern mit Datenbank

@pytest.fixture
def daten(db):
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    st = db.scalar(select(Msp).where(Msp.parser_key == "hastobe_csv"))
    imp_we = Import(dateiname="we.pdf", datei_pfad="we.pdf", datei_hash="a" * 64, msp_id=we.id, ladeart=OEFF,
                    status=ImportStatus.abgeschlossen, rechnungsdatum=date(2026, 10, 5))
    imp_st = Import(dateiname="st.csv", datei_pfad="st.csv", datei_hash="b" * 64, msp_id=st.id, ladeart=PRIV,
                    status=ImportStatus.abgeschlossen)
    fz1, fz2 = Fahrzeug(kennzeichen="W-AUS 1"), Fahrzeug(kennzeichen="W-AUS 2")
    fahrer = Fahrer(name="Auswertung Test")
    karte = Ladekarte(msp=we, kennung="200001", kennung_typ=KennungTyp.kartennummer)
    gast = Ladekarte(msp=st, kennung="GAST / X", kennung_typ=KennungTyp.kartenlabel, ist_gastkarte=True)
    db.add_all([imp_we, imp_st, fz1, fz2, fahrer, karte, gast])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=fz1.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=fahrer.id, fahrzeug_id=fz1.id, gueltig_ab=date(2026, 9, 1))])

    def lv(imp, start, ende, kwh, kosten, fz=None, karte_=None, geschaetzt=False, standort="Ort A"):
        db.add(Ladevorgang(import_id=imp.id, msp_id=imp.msp_id, ladekarte_id=karte_.id if karte_ else None,
                           start=start, ende=ende, kwh=D(kwh), kosten_netto=D(kosten), ladeart=imp.ladeart,
                           kwh_geschaetzt=geschaetzt, fahrzeug_id=fz.id if fz else None, standort=standort,
                           zuordnung_quelle=ZuordnungQuelle.automatisch if fz else ZuordnungQuelle.keine))

    lv(imp_we, datetime(2026, 9, 5, 10), datetime(2026, 9, 5, 11), "40", "16.00", fz1, karte, geschaetzt=True)
    lv(imp_st, datetime(2026, 9, 10, 18), datetime(2026, 9, 11, 7), "60", "18.00", fz1, standort="Wallbox")
    lv(imp_st, datetime(2026, 9, 12, 18), datetime(2026, 9, 13, 7), "30", "9.00", fz2, standort="Wallbox")
    lv(imp_st, datetime(2026, 9, 14, 8), datetime(2026, 9, 14, 9), "0", "0.00", fz2)  # 0 kWh
    lv(imp_st, datetime(2026, 9, 15, 8), datetime(2026, 9, 15, 9), "10", "3.00", karte_=gast)  # Gast
    lv(imp_st, datetime(2026, 9, 16, 8), datetime(2026, 9, 16, 9), "5", "1.50")  # ohne Karte
    lv(imp_st, datetime(2026, 9, 30, 20), datetime(2026, 10, 1, 6), "20", "6.00", fz2)  # Start Sep, Ende Okt
    db.add_all([
        Gebuehr(import_id=imp_we.id, ladekarte_id=karte.id, bezeichnung="Grundgebühr Sep", betrag_netto=D("8.25"),
                zeitraum_von=date(2026, 9, 1), zeitraum_bis=date(2026, 9, 30)),
        Gebuehr(import_id=imp_we.id, bezeichnung="Grundgebühr ohne Karte", betrag_netto=D("8.25"),
                zeitraum_von=date(2026, 9, 1), zeitraum_bis=date(2026, 9, 30)),
        Gebuehr(import_id=imp_we.id, ladekarte_id=karte.id, bezeichnung="Ohne Zeitraum", betrag_netto=D("1.00")),
        Pauschale(monat=date(2026, 9, 1), bezeichnung="Wallbox Test", kwh=D(100), betrag_netto=D("49.17"),
                  ladeart=PRIV),
    ])
    db.flush()
    return {"fz1": fz1, "fz2": fz2}


def test_auswertung_september(db, daten):
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    z1, z2 = a.fahrzeug(daten["fz1"].id), a.fahrzeug(daten["fz2"].id)
    assert z1.summe.kwh == D(100) and z1.summe.kosten == D("42.25")  # 16 + 18 + Kartengebühr 8,25
    assert z1.summe.gebuehren == D("8.25") and z1.summe.kwh_geschaetzt == D(40)
    assert z1.fahrer_im_zeitraum == ["Auswertung Test"]
    assert dict(z1.standorte)["Wallbox"].kwh == D(60)
    assert z2.summe.anzahl == 1 and z2.summe.kwh == D(30)  # 0-kWh-Vorgang zählt nicht; 30.9.→1.10. zählt nach Ende
    assert a.gaeste.kwh == D(10)
    assert a.nicht_zugeordnet.kwh == D(5)
    assert a.gebuehren_nicht_zuordenbar.kosten == D("8.25")
    assert a.pauschalen.kwh == D(100) and a.pauschalen.kosten == D("49.17")
    g = a.gesamt
    assert g.kwh == D(245)
    assert g.kosten == D("16") + D("18") + D("9") + D("3") + D("1.5") + D("8.25") * 2 + D("49.17")
    assert g.preis == g.kosten / g.kwh
    assert g.kwh_oeffentlich == D(40) and g.kosten_oeffentlich == D("32.50")  # WE-Vorgang + 2 Gebühren
    msp = dict(a.msp)
    assert msp["Wien Energie"].kosten == D("32.50")


def test_gebuehr_ohne_zeitraum_nach_rechnungsdatum(db, daten):
    a = auswerten(db, zeitraum("monat", 2026, 10), app_einstellungen.laden(db))
    z1 = a.fahrzeug(daten["fz1"].id)
    assert z1.summe.gebuehren == D("1.00")  # Rechnungsdatum 5.10.
    assert a.fahrzeug(daten["fz2"].id).summe.kwh == D(20)


def test_stichtag_ladebeginn(db, daten):
    app_einstellungen.speichern(db, {"stichtag": "beginn"})
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert a.stichtag == "beginn"
    assert a.fahrzeug(daten["fz2"].id).summe.kwh == D(50)  # 30 + 20 (Start 30.9.)


def test_quartal_enthaelt_alles(db, daten):
    q = auswerten(db, zeitraum("quartal", 2026, 3), app_einstellungen.laden(db))
    assert q.gesamt.kwh == D(245)
    j = auswerten(db, zeitraum("jahr", 2026), app_einstellungen.laden(db))
    assert j.gesamt.kwh == D(265) and j.gesamt.gebuehren == D("17.50")


# ------------------------------------------------------------------ Export

def test_csv_export():
    t = Tabelle("test", [Spalte("Name"), Spalte("kWh", "kwh"), Spalte("Kosten", "geld"), Spalte("Anteil", "prozent"),
                         Spalte("Ende", "datumzeit")],
                [["Ä", D("1234.5"), D("-3.1"), D("0.4567"), datetime(2026, 9, 5, 7, 3)], ["leer", None, D(0), None, None]])
    inhalt = csv_inhalt(t)
    assert inhalt.startswith("﻿".encode("utf-8"))
    zeilen = inhalt.decode("utf-8-sig").split("\r\n")
    assert zeilen[0] == "Name;kWh;Kosten;Anteil;Ende"
    assert zeilen[1] == "Ä;1.234,500;-3,10;45,7;05.09.2026 07:03"
    assert zeilen[2] == "leer;;0,00;;"


def test_xlsx_export():
    from openpyxl import load_workbook
    t = Tabelle("test", [Spalte("Name"), Spalte("kWh", "kwh"), Spalte("Datum", "datum")],
                [["A", D("12.345"), date(2026, 9, 1)]])
    ws = load_workbook(io.BytesIO(xlsx_inhalt(t))).active
    assert [c.value for c in ws[1]] == ["Name", "kWh", "Datum"]
    assert ws["B2"].value == pytest.approx(12.345) and ws["B2"].number_format == "#,##0.000"
    assert ws["C2"].value.date() == date(2026, 9, 1)


# ------------------------------------------------------------------ Oberfläche

def test_standard_ist_vormonat(angemeldet):
    erwartet = vormonat()
    for pfad in ("/", "/auswertungen"):
        text = angemeldet.get(pfad).text
        assert f'<option value="{erwartet.nr}" selected>' in text, pfad
        assert f'<option value="{erwartet.jahr}" selected>' in text, pfad


def test_pro_fahrer(db, daten):
    """Ladungen zählen für die Person zum Ladezeitpunkt, ohne Gebühren; fz2 hat keine Fahrer:in."""
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert [z.fahrer.name for z in a.fahrer] == ["Auswertung Test"]
    z = a.fahrer[0]
    assert (z.summe.kwh, z.summe.kosten, z.summe.gebuehren) == (D(100), D("34.00"), D(0))  # 16 + 18, ohne 8,25
    assert set(z.fahrzeuge) == {"W-AUS 1"}
    t = a.topliste
    assert [e.fahrer.name for e in t.guenstigste] == ["Auswertung Test"] and t.teilnehmer == 1
    assert t.p_flotte == D("0.34")


def test_topliste_oeffentlich(db, daten):
    """Nur öffentliche Ladungen: Wien Energie (40 kWh, 16,00 €); die private Wallbox-Ladung (60 kWh) zählt nicht."""
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    t = a.topliste_oeffentlich
    assert t.teilnehmer == 1 and t.m == D(40)
    e = t.guenstigste[0]
    assert (e.fahrer.name, e.kwh, e.kosten, e.preis) == ("Auswertung Test", D(40), D("16.00"), D("0.4"))
    assert t.p_flotte == D("0.4")
    assert a.topliste.guenstigste[0].kwh == D(100)  # bestehende Topliste unverändert


def test_topliste_oeffentlich_mindestmenge(db, daten):
    """Mindestmenge gilt für die öffentlich geladenen kWh."""
    app_einstellungen.speichern(db, {"topliste_mindestmenge": "50"})
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert a.topliste.teilnehmer == 1  # 100 kWh insgesamt
    assert a.topliste_oeffentlich.teilnehmer == 0  # nur 40 kWh öffentlich


def test_topliste_nur_oeffentlich_parameter():
    z = FahrerZeile(Fahrer(name="X"))
    z.summe.vorgang(D(100), D(30), PRIV, False)
    z.summe.vorgang(D(30), D(15), OEFF, False)
    z.summe_oeffentlich.vorgang(D(30), D(15), OEFF, False)
    assert topliste_berechnen([z], None, D(20)).guenstigste[0].kwh == D(130)
    assert topliste_berechnen([z], None, D(20), nur_oeffentlich=True).guenstigste[0].preis == D("0.5")


def test_poolfahrzeug_nicht_in_topliste(db, daten):
    """Zwei Fahrer:innen am selben Tag auf fz2, geladen ohne Karte → keiner Person eindeutig zuzurechnen."""
    f1, f2 = Fahrer(name="Pool Eins"), Fahrer(name="Pool Zwei")
    db.add_all([f1, f2])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=daten["fz2"].id, gueltig_ab=date(2026, 1, 1))
                for f in (f1, f2)])
    db.flush()
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert "Pool Eins" not in [z.fahrer.name for z in a.fahrer]
    assert a.fahrer_nicht_eindeutig.kwh == D(30)  # 30 kWh + 0-kWh-Vorgang auf fz2
    assert a.topliste.nicht_eindeutig.kwh == D(30)
    assert a.fahrzeug(daten["fz2"].id).summe.kwh == D(30)  # pro Fahrzeug unverändert


def test_personengebundene_karte_zaehlt_fuer_person(db, daten):
    """Mit personengebundener Karte zählt die Ladung für die Karteninhaberin, auch am Poolfahrzeug."""
    st = db.scalar(select(Msp).where(Msp.parser_key == "hastobe_csv"))
    karte = Ladekarte(msp=st, kennung="Pia Karte", kennung_typ=KennungTyp.kartenlabel)
    pia, anderer = Fahrer(name="Pia Person"), Fahrer(name="Anderer Fahrer")
    db.add_all([karte, pia, anderer])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=pia.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=anderer.id, fahrzeug_id=daten["fz2"].id,
                                        gueltig_ab=date(2026, 1, 1))])
    imp = db.scalar(select(Import).where(Import.datei_hash == "b" * 64))
    db.add(Ladevorgang(import_id=imp.id, msp_id=st.id, ladekarte_id=karte.id, start=datetime(2026, 9, 20, 8),
                       ende=datetime(2026, 9, 20, 9), kwh=D(25), kosten_netto=D("7.50"), ladeart=PRIV,
                       fahrzeug_id=daten["fz2"].id, zuordnung_quelle=ZuordnungQuelle.manuell))
    db.flush()
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    personen = {z.fahrer.name: z.summe.kwh for z in a.fahrer}
    assert personen["Pia Person"] == D(25)
    assert personen["Anderer Fahrer"] == D(30)  # Ladungen ohne Karte an fz2


def _summe(kwh, kosten, oeff_kwh=0, anzahl=1):
    s = Summe()
    for _ in range(anzahl - 1):
        s.vorgang(D(0), D(0), PRIV, False)
    s.vorgang(D(kwh) - D(oeff_kwh), D(kosten), PRIV, False)
    if oeff_kwh:
        s.vorgang(D(oeff_kwh), D(0), OEFF, False)
    s.anzahl = anzahl
    return s


def test_vergleich_mit_vormonaten():
    aktuell = _summe(120, 36, oeff_kwh=30, anzahl=6)  # 0,30 €/kWh, 25 % öffentlich
    vorher = [(zeitraum("monat", 2026, 9), _summe(100, 40, oeff_kwh=10, anzahl=4)),
              (zeitraum("monat", 2026, 8), Summe()),  # ohne Daten – zählt nicht mit
              (zeitraum("monat", 2026, 7), _summe(60, 20, oeff_kwh=6, anzahl=2))]
    v = vergleichen(aktuell, vorher)
    assert (v.perioden, v.basis) == (2, "07/2026 – 09/2026")
    assert v.kwh == D("0.5")  # 120 ggü. Ø 80
    assert v.kosten == D("0.2")  # 36 ggü. Ø 30
    assert v.anzahl == D("1")  # 6 ggü. Ø 3
    assert v.preis == (D("0.3") - D(60) / D(160)) / (D(60) / D(160))  # gewichtet: 60 € / 160 kWh
    assert v.oeffentlich_pp == D("15")  # 25 % ggü. 16/160 = 10 %
    assert vergleichen(aktuell, [(zeitraum("monat", 2026, 9), Summe())]) is None


def test_auswerten_ohne_personen(db, daten):
    voll = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    schlank = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db), mit_personen=False)
    assert schlank.gesamt == voll.gesamt and schlank.fahrer == []


def test_dashboard_vergleich(angemeldet, db, daten):
    text = angemeldet.get("/?art=monat&jahr=2026&nr=10").text
    assert "Vergleich mit dem Durchschnitt von 09/2026" in text and "(1 Zeitraum mit Daten)" in text
    assert "▼ -92 %" in text  # 20 kWh im Oktober ggü. 245 kWh im September
    assert "Vergleich mit dem Durchschnitt" not in angemeldet.get("/?art=monat&jahr=2026&nr=9").text  # keine Vormonate


def test_ladekarten_nutzung(db, daten):
    """Sortiert nach Anzahl Ladevorgängen; aktive Karten ohne Ladung erscheinen als „am wenigsten genutzt“."""
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    ungenutzt = Ladekarte(msp=we, kennung="299999", kennung_typ=KennungTyp.kartennummer, bezeichnung="Reserve")
    inaktiv = Ladekarte(msp=we, kennung="288888", kennung_typ=KennungTyp.kartennummer, aktiv=False)
    db.add_all([ungenutzt, inaktiv])
    db.flush()
    imp = db.scalar(select(Import).where(Import.datei_hash == "a" * 64))
    db.add(Gebuehr(import_id=imp.id, ladekarte_id=ungenutzt.id, bezeichnung="Grundgebühr", betrag_netto=D("9.90"),
                   zeitraum_von=date(2026, 9, 1), zeitraum_bis=date(2026, 9, 30)))
    db.flush()
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    kennungen = [k.karte.kennung for k in a.karten]
    assert kennungen == ["200001", "GAST / X", "299999"]  # inaktive, ungenutzte Karte nicht dabei
    erste = a.karten[0]
    assert (erste.summe.anzahl, erste.summe.kwh, erste.summe.kosten) == (1, D(40), D("24.25"))  # inkl. 8,25 Gebühr
    assert erste.zugeordnet == "Fahrzeug W-AUS 1"
    assert a.karten[1].zugeordnet == "Gäste"
    assert [k.karte.kennung for k in a.karten_meist_genutzt()] == ["200001", "GAST / X"]
    assert [k.karte.kennung for k in a.karten_wenigst_genutzt()] == ["299999"]  # ohne Überschneidung
    assert [k.karte.kennung for k in a.karten_ungenutzt] == ["299999"]
    assert a.karten_ungenutzt[0].summe.gebuehren == D("9.90")


def test_seiten(angemeldet, db, daten):
    p = "art=monat&jahr=2026&nr=9"
    antwort = angemeldet.get(f"/auswertungen?{p}")
    assert antwort.status_code == 200
    assert "09/2026" in antwort.text and "W-AUS 1" in antwort.text and "49,17 €" in antwort.text
    assert angemeldet.get("/auswertungen?art=quartal&jahr=2026&nr=3").status_code == 200
    assert angemeldet.get("/auswertungen?art=frei&von=2026-09-01&bis=2026-09-15").status_code == 200
    antwort = angemeldet.get(f"/auswertungen/fahrzeug/{daten['fz1'].id}?{p}")
    assert antwort.status_code == 200 and "Wallbox" in antwort.text
    leer = angemeldet.get(f"/auswertungen/fahrzeug/{daten['fz1'].id}?art=monat&jahr=2020&nr=1")
    assert leer.status_code == 200 and "Keine Ladevorgänge im Zeitraum 01/2020" in leer.text  # blätterbar statt Fehler
    assert angemeldet.get("/auswertungen/fahrzeug/999999").status_code == 404
    fahrer_id = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db)).fahrer[0].fahrer.id
    antwort = angemeldet.get(f"/auswertungen/fahrer/{fahrer_id}?{p}")
    assert antwort.status_code == 200 and "Nach Fahrzeug" in antwort.text and "W-AUS 1" in antwort.text
    leer = angemeldet.get(f"/auswertungen/fahrer/{fahrer_id}?art=monat&jahr=2020&nr=1")
    assert leer.status_code == 200 and "Keine Ladevorgänge im Zeitraum 01/2020" in leer.text
    assert angemeldet.get("/auswertungen/fahrer/999999").status_code == 404
    antwort = angemeldet.get(f"/auswertungen?{p}")
    assert "Pro Fahrer:in" in antwort.text and f"/auswertungen/fahrer/{fahrer_id}?" in antwort.text
    assert "Ladekarten-Nutzung" in antwort.text and "Am wenigsten genutzt" in antwort.text
    antwort = angemeldet.get("/?art=monat&jahr=2026&nr=9")
    assert antwort.status_code == 200 and "Günstigste" not in antwort.text  # Toplisten nur unter Auswertungen
    assert 'class="torte"' in antwort.text and "Verteilung auf Fahrer:innen" in antwort.text
    assert "kWh je <abbr" in antwort.text and "/ladevorgaenge?msp_id=" in antwort.text  # Diagramm je MSP
    quartal = angemeldet.get("/?art=quartal&jahr=2026&nr=3").text  # Torte (ganzer Zeitraum) über den Säulen
    assert 'class="saeulen"' in quartal and quartal.index('class="torte"') < quartal.index('class="saeulen"')
    # „Alles“: vom Monat der ersten bis zum Monat der letzten Ladung, ohne Blättern und ohne Vergleich
    alles = zeitraum_aus_parametern("alles", "", "", "", "", vormonat(), db=db)
    assert alles.art == "alles" and alles.von.day == 1 and alles.verschoben(-1) == alles
    assert alles.als_parameter() == {"art": "alles"} and alles.label.startswith("Alles (")
    for basis in ("/", "/auswertungen"):
        text = angemeldet.get(f"{basis}?art=alles").text
        assert '<a href="' + basis + '?art=alles" aria-current="true">Alles</a>' in text
        assert 'aria-label="vorheriger Zeitraum"' not in text and f"{alles.label}</strong>" in text
    assert 'class="abweichung' not in angemeldet.get("/?art=alles").text  # keine Vorperioden
    # Auswahl Monat und Jahr
    assert '<option value="9" selected>September</option>' in antwort.text
    assert '<option value="2026" selected>2026</option>' in antwort.text
    assert 'name="nr" aria-label="Quartal"' in angemeldet.get("/?art=quartal&jahr=2026&nr=3").text
    assert 'name="nr"' not in angemeldet.get("/?art=jahr&jahr=2026").text  # Jahr: nur Jahresauswahl
    antwort = angemeldet.get(f"/auswertungen?{p}")
    assert "Günstigste" in antwort.text and "Topliste öffentliches Laden" in antwort.text
    assert '<abbr class="hilfe" tabindex="0" title="Score = (Kosten + m × Ø-Preis aller) / (kWh + m)' in antwort.text
    assert "noch keine Daten" in angemeldet.get("/?art=monat&jahr=2020&nr=1").text


@pytest.mark.parametrize("name", ["fahrzeuge", "fahrer", "ladekarten", "msp", "gesamt", "topliste",
                                  "topliste_oeffentlich"])
@pytest.mark.parametrize("format_", ["csv", "xlsx"])
def test_export_routen(angemeldet, db, daten, name, format_):
    antwort = angemeldet.get(f"/auswertungen/export/{name}.{format_}?art=monat&jahr=2026&nr=9")
    assert antwort.status_code == 200
    assert f'filename="{name}_09-2026.{format_}"' in antwort.headers["content-disposition"]


def test_export_fahrzeuge_inhalt(angemeldet, db, daten):
    text = angemeldet.get("/auswertungen/export/fahrzeuge.csv?art=monat&jahr=2026&nr=9").content.decode("utf-8-sig")
    zeile = next(z for z in text.split("\r\n") if z.startswith("W-AUS 1"))
    assert ";100,000;40,000;42,25;8,25;0,42;" in zeile  # €/kWh mit 2 Stellen


def test_ladevorgaenge_export(angemeldet, db, daten):
    antwort = angemeldet.get("/ladevorgaenge/export.csv?von=2026-09-01&bis=2026-09-30")
    zeilen = antwort.content.decode("utf-8-sig").strip().split("\r\n")
    assert len(zeilen) == 1 + 6
    assert angemeldet.get("/ladevorgaenge/export.xlsx").status_code == 200
    assert angemeldet.get("/ladevorgaenge/export.pdf").status_code == 404


def test_einstellungen(angemeldet, db):
    antwort = angemeldet.get("/einstellungen")
    assert 'value="0,3000"' in antwort.text and 'value="20,0"' in antwort.text
    antwort = angemeldet.post("/einstellungen", data={"stichtag": "beginn", "topliste_m": "",
                                                      "topliste_mindestmenge": "-1", "strompreis_vorbelegung": "x"})
    assert antwort.status_code == 422
    angemeldet.post("/einstellungen", data={"stichtag": "beginn", "topliste_m": "75",
                                            "topliste_mindestmenge": "10", "strompreis_vorbelegung": "0,28"})
    e = app_einstellungen.laden(db)
    assert (e.stichtag, e.topliste_m, e.topliste_mindestmenge, e.strompreis_vorbelegung) == (
        "beginn", D(75), D(10), D("0.28"))
    angemeldet.post("/einstellungen", data={"stichtag": "ende", "topliste_m": "", "topliste_mindestmenge": "20",
                                            "strompreis_vorbelegung": "0,30"})
    assert app_einstellungen.laden(db).topliste_m is None


def test_anzeige_gerundet_export_genau(angemeldet, db, daten):
    """Dashboard/Auswertungen: kWh ganzzahlig, €/kWh mit 2 Stellen; der Export bleibt genau."""
    for pfad in ("/auswertungen?art=monat&jahr=2026&nr=9", "/?art=monat&jahr=2026&nr=9"):
        text = angemeldet.get(pfad).text
        assert '≈</abbr>&nbsp;245</span>' in text, pfad  # 245,0 kWh, davon geschätzt → „≈“ vor der Zahl
        assert '</svg>0,46</span><span>Ø €/kWh</span>' in text, pfad  # 113,17 € / 245 kWh = 0,4619
        assert "0,4619" not in text and "245,000" not in text, pfad
    csv = angemeldet.get("/auswertungen/export/gesamt.csv?art=monat&jahr=2026&nr=9").content.decode("utf-8-sig")
    assert ";245,000;" in csv and ";0,46;" in csv  # kWh genau, €/kWh mit 2 Stellen


def test_ladekarten_top10_und_alle(angemeldet, db, daten):
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    db.add_all([Ladekarte(msp=we, kennung=f"3000{i:02d}", kennung_typ=KennungTyp.kartennummer) for i in range(25)])
    db.flush()
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert len(a.karten) == 27
    assert len(a.karten_meist_genutzt()) == 2  # nur genutzte Karten
    assert len(a.karten_wenigst_genutzt()) == 10

    p = "art=monat&jahr=2026&nr=9"
    text = angemeldet.get(f"/auswertungen?{p}").text
    assert "Alle 27 Karten als Rangliste anzeigen" in text and "karten=alle#ladekarten" in text
    abschnitt = text[text.index('id="ladekarten"'):text.index("</section>", text.index('id="ladekarten"'))]
    assert abschnitt.count("/stammdaten/ladekarten/") == 12  # 2 + 10

    text = angemeldet.get(f"/auswertungen?{p}&karten=alle").text
    abschnitt = text[text.index('id="ladekarten"'):text.index("</section>", text.index('id="ladekarten"'))]
    assert abschnitt.count("/stammdaten/ladekarten/") == 27 and "Platz" in abschnitt
    assert "Nur Top 10 anzeigen" in abschnitt and "Rangliste anzeigen" not in abschnitt


def test_links_von_stammdaten_zur_auswertung(angemeldet, db, daten):
    jahr = heute().year
    fz_id = daten["fz1"].id
    text = angemeldet.get(f"/stammdaten/fahrzeuge/{fz_id}").text
    assert f'href="/auswertungen/fahrzeug/{fz_id}"' in text
    assert f'href="/auswertungen/fahrzeug/{fz_id}?art=jahr&amp;jahr={jahr}"' in text
    fahrer = db.scalar(select(Fahrer).where(Fahrer.name == "Auswertung Test"))
    text = angemeldet.get(f"/stammdaten/fahrer/{fahrer.id}").text
    assert f'href="/auswertungen/fahrer/{fahrer.id}"' in text
    # Ziel funktioniert auch ohne Daten im Vormonat
    assert angemeldet.get(f"/auswertungen/fahrer/{fahrer.id}").status_code == 200


def test_ladevorgaenge_pro_tag(angemeldet, daten):
    """Kachel „Ladevorgänge“ mit Ø pro Tag (Tage des Zeitraums bis heute)."""
    from app.services.auswertung import Zeitraum
    sep = zeitraum("monat", 2026, 9)
    assert sep.tage_bisher == 30 and sep.pro_tag(6) == Decimal("0.2")
    assert Zeitraum("monat", date(2099, 1, 1), date(2099, 1, 31)).pro_tag(5) is None  # noch nicht begonnen
    for url in ("/?art=monat&jahr=2026&nr=9", "/auswertungen?art=monat&jahr=2026&nr=9"):
        seite = angemeldet.get(url).text
        assert 'class="pro-tag"' in seite and "pro Tag</small>" in seite and "über 30 Tage" in seite


def test_weitere_tooltip():
    """„Weitere“ in den Dashboard-Diagrammen listet die zusammengefassten Einträge im Tooltip."""
    from app.services.diagramme import Reihe
    r = Reihe("Weitere", 0, enthalten=[("Bernd", Decimal("12"), Decimal("5.5")), ("Anna", Decimal("30"), Decimal("2"))])
    assert r.enthalten_text(0, "kWh") == "Anna: 30 kWh\nBernd: 12 kWh"
    assert r.enthalten_text(1, "€") == "Bernd: 5,50 €\nAnna: 2,00 €"
    lang = Reihe("Weitere", 0, enthalten=[(f"P{i:02}", Decimal(i), Decimal(i)) for i in range(20)])
    assert lang.enthalten_text(0, "kWh").endswith("… und 5 weitere")


def test_topliste_rang():
    """Vollständige Rangfolge: Platz jeder teilnehmenden Person, None unter der Mindestmenge."""
    zeilen = [FahrerZeile(Fahrer(id=800 + i, name=f"R{i}"), Summe(kwh=D(kwh), kosten=D(kosten)))
              for i, (kwh, kosten) in enumerate([(100, 30), (100, 50), (100, 40), (5, 1), (100, 60)])]
    t = topliste_berechnen(zeilen, D(10), D(20))
    assert [t.rang(800 + i) for i in range(5)] == [1, 3, 2, None, 4] and t.teilnehmer == 4


def test_schnitt_pro_monat_und_auto(db, daten):
    """Kacheln: Ø pro Monat und Auto (Kosten der Fahrzeuge mit Ladungen / Monate / Fahrzeuge)."""
    sep = zeitraum("monat", 2026, 9)
    assert sep.monate_bisher == 1 and zeitraum("quartal", 2026, 3).monate_bisher == 3
    a = auswerten(db, sep, app_einstellungen.laden(db))
    s = a.schnitt_je_auto
    mit = [z for z in a.fahrzeuge if z.summe.anzahl > 0]
    assert s.text == "pro Monat und Auto" and len(mit) == 2
    assert s.kosten == sum((z.summe.kosten for z in mit), D(0)) / 2
    assert sep.schnitt(mit[0].summe).text == "pro Monat"


def test_co2e_einsparung(angemeldet, db, daten):
    """Grobe CO₂e-Einsparung ggü. Verbrennern: kWh × (l/100 km × kg/l ÷ kWh/100 km − g/kWh ÷ 1000); Schalter aus =
    nirgends sichtbar (Flottenbilanz zeigt dann die Zahl der Fahrer:innen); eingeschaltet braucht es CO₂e des Stroms."""
    basis = {"stichtag": "ende", "topliste_m": "", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    sep, dashboard = zeitraum("monat", 2026, 9), "/?art=monat&jahr=2026&nr=9"
    e = app_einstellungen.laden(db)
    assert e.co2e_ersparnis_je_kwh is None and auswerten(db, sep, e).co2e_ersparnis(Summe(kwh=D(1))) is None
    assert (e.co2e_verbrauch_e, e.co2e_verbrauch_verbrenner, e.co2e_verbrenner) == (D(20), D("7.4"), D("3.2"))
    assert "CO₂e" not in angemeldet.get(dashboard).text
    seite = angemeldet.get("/einstellungen").text
    assert 'value="20,0"' in seite and 'value="7,4"' in seite and 'value="3,20"' in seite
    assert 'name="feature_co2e"' in seite and 'name="feature_tank"' in seite
    an = basis | {"feature_co2e": "on"}
    assert angemeldet.post("/einstellungen", data=an).status_code == 422  # eingeschaltet ohne CO₂e des Stroms
    assert angemeldet.post("/einstellungen", data=an | {"co2e_strom": "x"}).status_code == 422
    assert angemeldet.post("/einstellungen", data=an | {"co2e_strom": "150", "co2e_verbrauch_e": "0"}).status_code == 422
    angemeldet.post("/einstellungen", data=basis | {"co2e_strom": "150"})  # Wert ohne Schalter: aus
    assert app_einstellungen.laden(db).co2e_ersparnis_je_kwh is None
    angemeldet.post("/einstellungen", data=an | {"co2e_strom": "150"})
    e = app_einstellungen.laden(db)
    assert e.co2e_ersparnis_je_kwh == D("7.4") * D("3.2") / 20 - D("0.15")  # 1,034 kg je kWh
    a = auswerten(db, sep, e)
    assert a.co2e_ersparnis(a.gesamt) == D(245) * D("1.034")  # 145 kWh Ladungen + 100 kWh Pauschale
    assert a.co2e_ersparnis(a.fahrzeug(daten["fz1"].id).summe) == D("103.4")
    seite = angemeldet.get(dashboard).text
    assert "CO₂e eingespart" in seite and "253 kg" in seite
    assert "103 kg" in angemeldet.get(f"/auswertungen/fahrzeug/{daten['fz1'].id}?art=monat&jahr=2026&nr=9").text
    assert angemeldet.get("/reports/flottenbilanz.pdf?art=monat&jahr=2026&nr=9").status_code == 200
    angemeldet.post("/einstellungen", data=basis | {"co2e_strom": "150"})  # Schalter aus
    assert app_einstellungen.laden(db).co2e_ersparnis_je_kwh is None and "CO₂e" not in angemeldet.get(dashboard).text


def test_co2e_in_tonnen():
    """Über 1.000 kg ganze Tonnen, darunter ganze kg."""
    from app.web import templates
    makro = templates.env.get_template("auswertungen/_bausteine.html").module.co2e_text
    assert str(makro(D("999.4"))) == "999 kg" and str(makro(D("12345"))) == "12 t" and str(makro(D("1500"))) == "2 t"


def test_tankkosten_ersparnis(angemeldet, db, daten):
    """Grobe Tankkosten-Ersparnis ggü. Verbrennern: kWh × l/100 km ÷ kWh/100 km × Spritpreis am Tag der Ladung minus
    Kosten; Schalter aus bzw. ohne Spritpreis = nirgends sichtbar."""
    from app.models import Spritpreis
    basis = {"stichtag": "ende", "topliste_m": "", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    sep, dashboard = zeitraum("monat", 2026, 9), "/?art=monat&jahr=2026&nr=9"
    assert "Tankkosten" not in angemeldet.get(dashboard).text
    angemeldet.post("/einstellungen", data=basis | {"feature_tank": "on"})
    assert "Noch kein Spritpreis" in angemeldet.get("/einstellungen").text
    assert "Tankkosten" not in angemeldet.get(dashboard).text  # an, aber ohne Spritpreis: aus
    assert "nicht gespeichert" in angemeldet.post("/einstellungen/spritpreis", data={"gueltig_ab": "x", "preis": "1"}).text
    angemeldet.post("/einstellungen/spritpreis", data={"gueltig_ab": "2026-01-01", "preis": "1,40"})
    angemeldet.post("/einstellungen/spritpreis", data={"gueltig_ab": "2026-09-15", "preis": "2"})
    e = app_einstellungen.laden(db)
    assert e.liter_je_kwh == D("0.37") and e.co2e_ersparnis_je_kwh is None  # unabhängig von CO₂e
    a = auswerten(db, sep, e)
    # 230 kWh bis 14.9. (inkl. Pauschale 1.9.) zu 1,40 €, 15 kWh ab 15.9. zu 2,00 € → 0,37 l/kWh × 352 € = 130,24 €
    assert a.tank_ersparnis(a.gesamt) == D("130.24") - D("113.17")
    assert a.tank_ersparnis(a.fahrzeug(daten["fz1"].id).summe) == D("51.80") - D("42.25")
    seite = angemeldet.get(dashboard).text
    assert "Tankkosten gespart" in seite and "17 €" in seite and "CO₂e" not in seite
    assert "1,400 €/l" in angemeldet.get("/einstellungen").text
    spaeter = db.scalar(select(Spritpreis).where(Spritpreis.preis == D(2)))
    angemeldet.post(f"/einstellungen/spritpreis/{spaeter.id}/loeschen")
    a = auswerten(db, sep, app_einstellungen.laden(db))
    assert a.tank_ersparnis(a.gesamt) == D(245) * D("0.518") - D("113.17")
    angemeldet.post("/einstellungen", data=basis)  # Schalter aus
    assert "Tankkosten" not in angemeldet.get(dashboard).text


def test_kennzahlen_symbole(angemeldet, db, daten):
    """Kacheln: Ladestation bei den Ladevorgängen, Geld beim Ø €/kWh und den gesparten Tankkosten, Stoppuhr bei der
    Ladedauer, grünes Blatt bei CO₂e (in den PDF-Reports Farbe über das SVG-Attribut)."""
    from app.icons import FARBEN, icon
    assert FARBEN["co2e"] in icon("co2e")
    from app.models import Spritpreis
    app_einstellungen.speichern(db, {"feature_co2e": "1", "co2e_strom": "150", "feature_tank": "1"})
    db.add(Spritpreis(gueltig_ab=date(2026, 1, 1), preis=D("1.4")))
    seite = angemeldet.get("/?art=monat&jahr=2026&nr=9").text
    for name in ("vorgaenge", "geld", "dauer", "co2e"):
        assert f'<span class="kachel-zahl"><svg class="symbol symbol-{name}"' in seite, name
    assert seite.count('<span class="kachel-zahl"><svg class="symbol symbol-geld"') == 2  # Ø €/kWh, Tankkosten
    for pdf in ("zusammenfassung.pdf", "flottenbilanz.pdf"):
        assert angemeldet.get(f"/reports/{pdf}?art=monat&jahr=2026&nr=9").status_code == 200
