"""Sektion „Letzte Ladevorgänge“ auf den Detailseiten von Ladekarte, Fahrzeug, Fahrer:in und MSP."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services.ladevorgaenge import letzte_vorgaenge, letzte_vorgaenge_von_fahrer

D = Decimal


@pytest.fixture
def daten(db):
    """Karte A personengebunden an Paula (fährt W-A 1); Karte B fahrzeuggebunden an W-B 2 (fährt Quirin)."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    imp = Import(dateiname="t.pdf", datei_pfad="t.pdf", datei_hash="c" * 64, msp_id=msp.id, ladeart=Ladeart.oeffentlich,
                 status=ImportStatus.abgeschlossen)
    karte_a = Ladekarte(msp=msp, kennung="50001", kennung_typ=KennungTyp.kartennummer, bezeichnung="Karte Paula")
    karte_b = Ladekarte(msp=msp, kennung="50002", kennung_typ=KennungTyp.kartennummer)
    fz_a, fz_b = Fahrzeug(kennzeichen="W-A 1"), Fahrzeug(kennzeichen="W-B 2")
    paula, quirin = Fahrer(name="Paula Probe"), Fahrer(name="Quirin Test")
    db.add_all([imp, karte_a, karte_b, fz_a, fz_b, paula, quirin])
    db.flush()
    db.add_all([
        ZuordnungKarte(ladekarte_id=karte_a.id, fahrer_id=paula.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungKarte(ladekarte_id=karte_b.id, fahrzeug_id=fz_b.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=paula.id, fahrzeug_id=fz_a.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=quirin.id, fahrzeug_id=fz_b.id, gueltig_ab=date(2026, 1, 1)),
    ])

    def lv(karte, fz, tag, kwh="10"):
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, roh_kartenkennung=karte.kennung,
                        start=datetime(2026, 10, tag, 8), ende=datetime(2026, 10, tag, 9), kwh=D(kwh),
                        kosten_netto=D("3.50"), ladeart=Ladeart.oeffentlich, fahrzeug_id=fz.id,
                        zuordnung_quelle=ZuordnungQuelle.automatisch, standort=f"Ort {tag}")
        db.add(v)
        return v

    v1, v2, v3 = lv(karte_a, fz_a, 1), lv(karte_b, fz_b, 2), lv(karte_b, fz_b, 3)
    db.flush()
    return {"msp": msp, "karte_a": karte_a, "karte_b": karte_b, "fz_a": fz_a, "fz_b": fz_b, "paula": paula,
            "quirin": quirin, "v": (v1, v2, v3), "lv": lv}


def _ids(zeilen):
    return [v.id for v, _ in zeilen]


def test_je_karte_und_fahrzeug(db, daten):
    v1, v2, v3 = daten["v"]
    zeilen = letzte_vorgaenge(db, Ladevorgang.ladekarte_id == daten["karte_a"].id)
    assert _ids(zeilen) == [v1.id]
    assert [f.name for f in zeilen[0][1]] == ["Paula Probe"]  # personengebundene Karte
    zeilen = letzte_vorgaenge(db, Ladevorgang.fahrzeug_id == daten["fz_b"].id)
    assert _ids(zeilen) == [v3.id, v2.id]  # neueste zuerst
    assert [f.name for f in zeilen[0][1]] == ["Quirin Test"]  # fahrzeuggebundene Karte → Fahrer:in des Fahrzeugs


def test_je_fahrer(db, daten):
    v1, v2, v3 = daten["v"]
    assert _ids(letzte_vorgaenge_von_fahrer(db, daten["paula"].id)) == [v1.id]
    assert _ids(letzte_vorgaenge_von_fahrer(db, daten["quirin"].id)) == [v3.id, v2.id]


def test_fahrerwechsel_zaehlt_zum_ladezeitpunkt(db, daten):
    """Quirin fährt W-B 2 nur bis 2.10.; der Vorgang vom 3.10. gehört dann zur neuen Fahrerin."""
    z = db.scalar(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == daten["quirin"].id))
    z.gueltig_bis = date(2026, 10, 2)
    neu = Fahrer(name="Nora Neu")
    db.add(neu)
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=neu.id, fahrzeug_id=daten["fz_b"].id, gueltig_ab=date(2026, 10, 3)))
    db.flush()
    v1, v2, v3 = daten["v"]
    assert _ids(letzte_vorgaenge_von_fahrer(db, daten["quirin"].id)) == [v2.id]
    assert _ids(letzte_vorgaenge_von_fahrer(db, neu.id)) == [v3.id]


def test_begrenzung(db, daten):
    for i in range(20):
        daten["lv"](daten["karte_a"], daten["fz_a"], 1 + i % 28)
    db.flush()
    assert len(letzte_vorgaenge(db, Ladevorgang.ladekarte_id == daten["karte_a"].id)) == 15
    assert len(letzte_vorgaenge_von_fahrer(db, daten["paula"].id)) == 15


def test_detailseiten(angemeldet, db, daten):
    seiten = {
        f"/stammdaten/ladekarten/{daten['karte_a'].id}": ["W-A 1</a> <small>(Paula Probe)</small>",
                                                         f"/ladevorgaenge?ladekarte_id={daten['karte_a'].id}"],
        f"/stammdaten/fahrzeuge/{daten['fz_b'].id}": ["Quirin Test", ">W-B 2 (Quirin Test)</a>",
                                                      f"/ladevorgaenge?fahrzeug_id={daten['fz_b'].id}"],
        f"/stammdaten/fahrer/{daten['paula'].id}": [f'href="/stammdaten/ladekarten/{daten["karte_a"].id}">Paula Probe</a>', "W-A 1</a>"],
        f"/stammdaten/msp/{daten['msp'].id}": ["W-B 2</a> <small>(Quirin Test)</small>",
                                              f"/ladevorgaenge?msp_id={daten['msp'].id}"],
    }
    for pfad, erwartet in seiten.items():
        text = angemeldet.get(pfad).text
        assert "Letzte Ladevorgänge" in text, pfad
        for e in erwartet:
            assert e in text, (pfad, e)
    # Fahrer:innen-Seite: keine eigene Fahrer-Spalte (es ist ja die Person selbst)
    assert "(Paula Probe)" not in angemeldet.get(f"/stammdaten/fahrer/{daten['paula'].id}").text.split(
        "Letzte Ladevorgänge")[1]


def test_ohne_vorgaenge(angemeldet, db):
    fz = Fahrzeug(kennzeichen="W-LEER 1")
    db.add(fz)
    db.flush()
    assert "Noch keine Ladevorgänge" in angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text


def test_ladevorgaenge_filter_nach_karte(angemeldet, db, daten):
    antwort = angemeldet.get(f"/ladevorgaenge?ladekarte_id={daten['karte_b'].id}")
    assert "<strong>2</strong> Vorgänge" in antwort.text and "Filter aufheben" in antwort.text
    zeilen = angemeldet.get(f"/ladevorgaenge/export.csv?ladekarte_id={daten['karte_b'].id}").content
    assert len(zeilen.decode("utf-8-sig").strip().split("\r\n")) == 1 + 2


def test_gesamt_kacheln(angemeldet, db, daten):
    """Kennzahlen über den gesamten Zeitraum: Fahrzeug nach Fahrzeug, Person nach „wer ist gefahren“."""
    from app.services import kennzahlen
    g = kennzahlen.fahrzeug(db, daten["fz_b"].id)
    assert (g.anzahl, g.kwh, g.kosten, g.anteil_oeffentlich) == (2, D("20.000"), D("7.00"), D(1))
    assert g.dc_minuten is None and g.dc_anzahl == 0  # keine DC-Ladungen
    assert kennzahlen.fahrer(db, daten["quirin"].id).anzahl == 2 and kennzahlen.fahrer(db, daten["paula"].id).anzahl == 1
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{daten['fz_b'].id}").text
    kacheln = seite.split('class="kacheln kacheln-klein"')[1].split("</div>\n</div>")[0]
    assert "Ladevorgänge seit 10/2026" in kacheln and "7,00 €" in kacheln and "0,35" in kacheln
    assert 'class="kacheln kacheln-klein"' in angemeldet.get(f"/stammdaten/fahrer/{daten['paula'].id}").text
