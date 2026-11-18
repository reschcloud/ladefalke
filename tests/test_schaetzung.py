from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Fahrzeug, Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from app.services.ladeprofil import schaetzgrundlage
from app.services.neuschaetzung import alle_neu_schaetzen
from app.services.schaetzung import Schaetzbasis, Schaetzwert, basis_aus_rohdaten, kwh_schaetzen

D = Decimal


@pytest.mark.parametrize("stunden, art, nenn, erwartet", [
    ("1", "AC", "11", "11.000"),
    ("1", "AC", "3.7", "3.700"),      # Ladepunkt begrenzt
    ("1", "DC", "150", "50.000"),     # 90 kWh, aber Obergrenze 50
    ("0.5", "DC", "150", "45.000"),
    ("0.5", "DC", "50", "25.000"),    # DC50-Säule
    ("0.5", "DC", None, "45.000"),
])
def test_vorgaben_ohne_fahrzeug(stunden, art, nenn, erwartet):
    assert kwh_schaetzen(Schaetzbasis(D(stunden), art, D(nenn) if nenn else None)) == D(erwartet)


@pytest.mark.parametrize("stunden, art, nenn, erwartet", [
    ("0.4", "DC", "150", "24.000"),   # 0,4 h × 60 kW
    ("0.75", "DC", "150", "30.000"),  # 45 kWh, gedeckelt auf Ø Ladeenergie 30
    ("0.5", "DC", "50", "25.000"),    # Ladepunkt (50 kW) kleiner als Fahrzeug (60 kW)
    ("1", "AC", "11", "11.000"),      # AC bleibt 11 kW
    ("5", "AC", "11", "30.000"),      # gedeckelt
])
def test_mit_fahrzeugwerten(stunden, art, nenn, erwartet):
    werte = {"DC": Schaetzwert(D(60), D(30)), "AC": Schaetzwert(D(11), D(30))}
    assert kwh_schaetzen(Schaetzbasis(D(stunden), art, D(nenn) if nenn else None), werte) == D(erwartet)


def test_rohdaten_hin_und_zurueck():
    basis = Schaetzbasis(D("0.367"), "DC", D(150))
    assert basis_aus_rohdaten({"vertrag": "x", **basis.als_rohdaten()}) == basis
    assert basis_aus_rohdaten(Schaetzbasis(D(2), "AC").als_rohdaten()) == Schaetzbasis(D(2), "AC", None)


@pytest.mark.parametrize("rohdaten", [None, {}, {"Id": "1"}, {"standzeit_std": "1", "art": "XY"},
                                      {"standzeit_std": "abc", "art": "AC"}])
def test_keine_schaetzbasis(rohdaten):
    assert basis_aus_rohdaten(rohdaten) is None


# ------------------------------------------------------------------ Grundlage aus den Daten

@pytest.fixture
def flotte(db):
    """Fahrzeug A: 5 DC-Vorgänge à 30 min (10–50 kWh); Fahrzeug B: 2 DC-Vorgänge à 20 kWh / 30 min; kein AC."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="s.pdf", datei_pfad="s.pdf", datei_hash="s" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    a, b = Fahrzeug(kennzeichen="W-SCH 1"), Fahrzeug(kennzeichen="W-SCH 2")
    db.add_all([imp, a, b])
    db.flush()

    def lv(fz, tag, kwh, minuten=30, geschaetzt=False, rohdaten=None):
        start = datetime(2026, 8, tag, 10)
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=start, ende=start + timedelta(minutes=minuten),
                        kwh=D(kwh), kwh_geschaetzt=geschaetzt, kosten_netto=D(5), ladeart=Ladeart.oeffentlich,
                        stromart=None if geschaetzt else "DC", fahrzeug_id=fz.id if fz else None,
                        zuordnung_quelle=ZuordnungQuelle.manuell if fz else ZuordnungQuelle.keine, rohdaten=rohdaten)
        db.add(v)
        return v

    for tag, kwh in enumerate((10, 20, 30, 40, 50), start=1):
        lv(a, tag, kwh)
    lv(b, 10, 20)
    lv(b, 11, 20)
    db.flush()
    return {"a": a, "b": b, "lv": lv}


def test_grundlage_eigene_flotte_vorgabe(db, flotte):
    g = schaetzgrundlage(db)
    eigen = g.fuer(flotte["a"].id)["DC"]
    assert (eigen.kw, eigen.obergrenze_kwh, eigen.quelle, eigen.anzahl) == (D(60), D(48), "fahrzeug", 5)
    # B hat nur 2 Vorgänge → Flottenschnitt: 190 kWh / 3,5 h = 54,3 kW; 95. Perzentil der 7 Vorgänge = 47 kWh
    flotten = g.fuer(flotte["b"].id)["DC"]
    assert (flotten.kw, flotten.obergrenze_kwh, flotten.quelle, flotten.anzahl) == (D("54.3"), D(47), "flotte", 7)
    assert g.fuer(None)["DC"] == flotten  # ohne Fahrzeug
    assert g.fuer(flotte["a"].id)["AC"] == Schaetzwert(D(11), D(50))  # keine AC-Daten → Vorgabe
    assert flotten.quelle_text == "Flottenschnitt (7 Vorgänge)"


def test_neuschaetzung_beim_commit(db, flotte):
    basis = Schaetzbasis(D("0.5"), "DC", D(150)).als_rohdaten()
    eigen = flotte["lv"](flotte["a"], 20, 1, geschaetzt=True, rohdaten=basis)
    ohne = flotte["lv"](None, 21, 1, geschaetzt=True, rohdaten=basis)
    db.commit()
    assert eigen.kwh == D("30.000")  # 0,5 h × 60 kW
    assert ohne.kwh == D("27.150")  # 0,5 h × 54,3 kW (Flotte)
    # Neue gemessene Daten verändern die Grundlage → beim nächsten Commit neu geschätzt
    flotte["lv"](flotte["a"], 22, 20, minuten=60)  # 20 kW
    db.commit()
    assert eigen.kwh < D("30.000") and ohne.kwh < D("27.150")
    assert alle_neu_schaetzen(db) == 0  # bereits aktuell


def test_fahrzeugseite_herkunft_der_schaetzung(angemeldet, db, flotte):
    from app.models import Fahrzeug
    def seite(kennzeichen):
        fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == kennzeichen))
        return angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert "<small>Flottenschnitt (7 Vorgänge)</small>" in seite("W-SCH 2")  # weniger als 5 eigene DC-Vorgänge
    assert "diesen Werten" in seite("W-SCH 1")  # eigene Daten


def test_vorgaben_aus_einstellungen(angemeldet, db):
    """Ohne Messdaten gelten die Vorgaben aus den Einstellungen; Speichern schätzt neu."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="e.pdf", datei_pfad="e.pdf", datei_hash="e" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    start = datetime(2026, 8, 1, 10)
    v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=start, ende=start + timedelta(hours=2), kwh=D(1),
                    kwh_geschaetzt=True, kosten_netto=D(5), ladeart=Ladeart.oeffentlich,
                    zuordnung_quelle=ZuordnungQuelle.keine, rohdaten=Schaetzbasis(D(2), "AC", D(22)).als_rohdaten())
    db.add(v)
    db.commit()
    assert v.kwh == D("22.000")  # 2 h × 11 kW (Standard), unter der Obergrenze 50
    antwort = angemeldet.post("/einstellungen", data={
        "stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
        "plausibel_ac_max_kw": "12", "plausibel_dc_min_kw": "10", "plausibel_dc_max_kw": "1000",
        "schaetzung_ac_kw": "7,4", "schaetzung_dc_kw": "60", "schaetzung_obergrenze_kwh": "40"})
    assert antwort.status_code == 200 and "Einstellungen gespeichert" in antwort.text
    db.refresh(v)
    assert v.kwh == D("14.800")  # 2 h × 7,4 kW
    assert schaetzgrundlage(db).fuer(None)["DC"] == Schaetzwert(D(60), D(40))
    fehler = angemeldet.post("/einstellungen", data={"stichtag": "ende", "topliste_mindestmenge": "20",
                                                     "strompreis_vorbelegung": "0,30", "schaetzung_dc_kw": "0"})
    assert fehler.status_code == 422 and "größer 0" in fehler.text
