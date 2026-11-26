"""Nachträgliche Neuzuordnung, „Nicht zugeordnet“, manuelle Zuweisung, Neuschätzung je Fahrzeug."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Gebuehr, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang,
                        Msp, ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services.aufloesung import neu_zuordnen_karte
from app.services.schaetzung import Schaetzbasis

D = Decimal


@pytest.fixture
def basis(db):
    """Wien-Energie-Karte ohne Zuordnung mit zwei geschätzten DC-Vorgängen (je 0,5 h an DC150).

    Gemessene DC-Daten: W-NEU 1 lädt 28 kWh in 28 min (60 kW, Obergrenze 28 kWh) → geschätzt 28 kWh;
    ein weiteres Fahrzeug 40 kWh in 30 min. Flotte: 340 kWh / 4,83 h = 70,3 kW, Obergrenze 40 → 35,15 kWh."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="t.pdf", datei_pfad="t.pdf", datei_hash="x" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    karte = Ladekarte(msp_id=msp.id, kennung="100001", kennung_typ=KennungTyp.kartennummer)
    fz = Fahrzeug(kennzeichen="W-NEU 1")
    fz2 = Fahrzeug(kennzeichen="W-NEU 2")
    andere = Fahrzeug(kennzeichen="W-NEU 3")
    fahrer = Fahrer(name="Test Person")
    db.add_all([imp, karte, fz, fz2, andere, fahrer])
    db.flush()
    for tag in range(1, 6):
        for messfahrzeug, kwh, minuten in ((fz, 28, 28), (andere, 40, 30)):
            start = datetime(2026, 8, tag, 10)
            db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=start, ende=start + timedelta(minutes=minuten),
                               kwh=D(kwh), kosten_netto=D(10), ladeart=Ladeart.oeffentlich, stromart="DC",
                               fahrzeug_id=messfahrzeug.id, zuordnung_quelle=ZuordnungQuelle.manuell))
    vorgaenge = []
    for tag in (5, 20):
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, roh_kartenkennung="100001",
                        start=datetime(2026, 9, tag, 10), ende=datetime(2026, 9, tag, 10, 30), kwh=D("45.000"),
                        kwh_geschaetzt=True, kosten_netto=D("14.20"), ladeart=Ladeart.oeffentlich,
                        zuordnung_quelle=ZuordnungQuelle.keine,
                        rohdaten=Schaetzbasis(D("0.5"), "DC", D(150)).als_rohdaten())
        db.add(v)
        vorgaenge.append(v)
    db.add(Gebuehr(import_id=imp.id, bezeichnung="Grundgebühr", betrag_netto=D("8.25"),
                   roh_kartenkennung="AT-VIE-C1-A"))
    db.flush()
    return {"imp": imp, "karte": karte, "fz": fz, "fz2": fz2, "fahrer": fahrer, "v": vorgaenge}


def test_rueckdatierte_kartenzuordnung_mit_option(angemeldet, db, basis):
    karte, fz = basis["karte"], basis["fz"]
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}/zuordnung", data={
        "ziel": f"fahrzeug:{fz.id}", "ab": "2026-09-10", "neu_zuordnen": "on"})
    assert "1 Ladevorgang (bereits importiert) wurde neu zugeordnet" in antwort.text
    frueh, spaet = basis["v"]
    db.refresh(frueh), db.refresh(spaet)
    assert frueh.fahrzeug_id is None and frueh.kwh == D("35.150")  # vor dem Zuordnungsbeginn: Flottenschnitt
    assert spaet.fahrzeug_id == fz.id and spaet.zuordnung_quelle == ZuordnungQuelle.automatisch
    assert spaet.kwh == D("28.000")  # 0,5 h × 60 kW = 30, gedeckelt auf die Obergrenze 28
    assert spaet.kosten_netto == D("14.20")  # Kosten unverändert


def test_ohne_option_bleibt_alles(angemeldet, db, basis):
    angemeldet.post(f"/stammdaten/ladekarten/{basis['karte'].id}/zuordnung", data={
        "ziel": f"fahrzeug:{basis['fz'].id}", "ab": "2026-09-01"})
    for v in basis["v"]:
        db.refresh(v)
        assert v.fahrzeug_id is None


def test_manuelle_zuordnung_bleibt(db, basis):
    manuell = basis["v"][1]
    manuell.fahrzeug_id, manuell.zuordnung_quelle = basis["fz2"].id, ZuordnungQuelle.manuell
    db.add(ZuordnungKarte(ladekarte_id=basis["karte"].id, fahrzeug_id=basis["fz"].id, gueltig_ab=date(2026, 1, 1)))
    assert neu_zuordnen_karte(db, basis["karte"].id, date(2026, 1, 1), None) == 1
    assert manuell.fahrzeug_id == basis["fz2"].id
    assert basis["v"][0].fahrzeug_id == basis["fz"].id


def test_ueber_fahrer_und_loeschen(angemeldet, db, basis):
    karte, fahrer, fz = basis["karte"], basis["fahrer"], basis["fz"]
    db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=fahrer.id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    angemeldet.post(f"/stammdaten/fahrer/{fahrer.id}/fahrzeug", data={
        "fahrzeug_id": str(fz.id), "ab": "2026-09-01", "neu_zuordnen": "on"})
    for v in basis["v"]:
        db.refresh(v)
        assert v.fahrzeug_id == fz.id

    z = db.scalar(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == fahrer.id))
    antwort = angemeldet.post(f"/stammdaten/zuordnungen/fahrer/{z.id}/loeschen", data={"neu_zuordnen": "on"})
    assert "2 Ladevorgänge (bereits importiert) wurden neu zugeordnet" in antwort.text
    for v in basis["v"]:
        db.refresh(v)
        assert v.fahrzeug_id is None and v.zuordnung_quelle == ZuordnungQuelle.keine
        assert v.kwh == D("35.150")  # wieder mit dem Flottenschnitt geschätzt


def test_zuordnung_aendern_mit_option(angemeldet, db, basis):
    z = ZuordnungKarte(ladekarte_id=basis["karte"].id, fahrzeug_id=basis["fz"].id, gueltig_ab=date(2026, 9, 1),
                       gueltig_bis=date(2026, 9, 10))
    db.add(z)
    db.flush()
    neu_zuordnen_karte(db, basis["karte"].id, date(2026, 9, 1), None)
    # Zeitraum nach hinten verschieben: alter (1.–10.9.) und neuer Zeitraum (15.–30.9.) werden neu ermittelt
    angemeldet.post(f"/stammdaten/zuordnungen/karte/{z.id}", data={
        "ziel": f"fahrzeug:{basis['fz2'].id}", "ab": "2026-09-15", "bis": "2026-09-30", "neu_zuordnen": "on"})
    frueh, spaet = basis["v"]
    db.refresh(frueh), db.refresh(spaet)
    assert frueh.fahrzeug_id is None
    assert spaet.fahrzeug_id == basis["fz2"].id and spaet.kwh == D("35.150")  # fz2 ohne Daten: Flottenschnitt


# ------------------------------------------------------------------ Ansicht „Nicht zugeordnet“

def test_nicht_zugeordnet_und_manuell_zuweisen(angemeldet, db, basis):
    antwort = angemeldet.get("/aufgaben")
    assert antwort.status_code == 200
    assert "100001" in antwort.text and "Grundgebühr" in antwort.text
    ids = [str(v.id) for v in basis["v"]]
    antwort = angemeldet.post("/aufgaben/zuweisen",
                              data={"vorgang_ids": ids, "fahrzeug_id": str(basis["fz"].id)})
    assert "2 Ladevorgänge manuell Fahrzeug W-NEU 1 zugeordnet" in antwort.text
    for v in basis["v"]:
        db.refresh(v)
        assert v.zuordnung_quelle == ZuordnungQuelle.manuell and v.kwh == D("28.000")
    assert 'id="ohne-fahrzeug"' not in antwort.text  # erledigt: Abschnitt entfällt, nur die Kachel ✓


def test_gastkarten_nicht_in_liste(angemeldet, db, basis):
    basis["karte"].ist_gastkarte = True
    db.flush()
    antwort = angemeldet.get("/aufgaben")
    assert 'id="ohne-fahrzeug"' not in antwort.text  # Gastkarten sind keine Aufgabe: Abschnitt entfällt


def test_automatisch_erneut(angemeldet, db, basis):
    db.add(ZuordnungKarte(ladekarte_id=basis["karte"].id, fahrzeug_id=basis["fz"].id, gueltig_ab=date(2026, 9, 1)))
    db.flush()
    antwort = angemeldet.post("/aufgaben/automatisch")
    assert "2 Ladevorgänge automatisch zugeordnet; 0 Ladevorgänge ohne Fahrzeug" in antwort.text


def test_gebuehr_zuweisen(angemeldet, db, basis):
    g = db.scalar(select(Gebuehr).where(Gebuehr.import_id == basis["imp"].id))
    angemeldet.post(f"/aufgaben/gebuehr/{g.id}", data={"ladekarte_id": str(basis["karte"].id)})
    db.refresh(g)
    assert g.ladekarte_id == basis["karte"].id


# ------------------------------------------------------------------ Ladevorgang bearbeiten

def test_ladevorgang_fahrzeug_manuell_und_automatisch(angemeldet, db, basis):
    v = basis["v"][1]
    assert angemeldet.get(f"/ladevorgaenge/{v.id}").status_code == 200
    antwort = angemeldet.post(f"/ladevorgaenge/{v.id}", data={"fahrzeug_id": str(basis["fz2"].id)})
    assert "manuell Fahrzeug W-NEU 2 zugeordnet" in antwort.text
    db.refresh(v)
    assert v.fahrzeug_id == basis["fz2"].id and v.zuordnung_quelle == ZuordnungQuelle.manuell

    db.add(ZuordnungKarte(ladekarte_id=basis["karte"].id, fahrzeug_id=basis["fz"].id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    antwort = angemeldet.post(f"/ladevorgaenge/{v.id}", data={"fahrzeug_id": "auto"})
    assert "automatisch ermittelt: W-NEU 1" in antwort.text
    db.refresh(v)
    assert v.zuordnung_quelle == ZuordnungQuelle.automatisch and v.kwh == D("28.000")


def test_fahrerwechsel_mit_neuzuordnung(angemeldet, db, basis):
    """Fahrzeugkarte an W-NEU 1; Wechsel rückwirkend: Vorgänge werden der neuen Situation nach neu ermittelt."""
    karte, fz, fahrer = basis["karte"], basis["fz"], basis["fahrer"]
    db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=fahrer.id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    antwort = angemeldet.post(f"/stammdaten/fahrer/{fahrer.id}/fahrzeug", data={
        "fahrzeug_id": str(fz.id), "ab": "2026-09-01", "bisherige_austragen": "on", "neu_zuordnen": "on"})
    assert "2 Ladevorgänge (bereits importiert) wurden neu zugeordnet" in antwort.text
    for v in basis["v"]:
        db.refresh(v)
        assert v.fahrzeug_id == fz.id


def test_fruehere_adresse_leitet_um(angemeldet):
    antwort = angemeldet.get("/nicht-zugeordnet?karte_id=1", follow_redirects=False)
    assert antwort.status_code == 301 and antwort.headers["location"] == "/aufgaben?karte_id=1"
    antwort = angemeldet.get("/zu-klaeren?roh=X", follow_redirects=False)
    assert antwort.status_code == 301 and antwort.headers["location"] == "/aufgaben?roh=X"
    text = angemeldet.get("/aufgaben").text
    assert "<h1>Aufgaben</h1>" in text and 'class="kacheln aufgaben-uebersicht"' in text
