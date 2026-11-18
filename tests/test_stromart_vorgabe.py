"""AC/DC-Vorgabe je MSP: beim Import, nachträglich beim MSP, je Import und unter „Aufgaben“."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.models import Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from app.services import app_einstellungen, stromart
from app.services.importe import bestaetigen, hochladen
from app.services.offen import zaehlen

CSV = (Path(__file__).parent / "fixtures" / "hastobe_beispiel.csv").read_bytes()  # ohne AC/DC-Angabe


def _msp(db, key):
    return db.scalar(select(Msp).where(Msp.parser_key == key))


def _arten(db, *bedingungen):
    return sorted((v.stromart or "-", v.stromart_quelle or "-")
                  for v in db.scalars(select(Ladevorgang).where(*bedingungen)))


def test_vorgabe_beim_import(db):
    msp = _msp(db, "hastobe_csv")
    msp.stromart_standard = "AC"
    imp = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, imp, {}, stromart.wert("AC"))
    assert set(_arten(db, Ladevorgang.import_id == imp.id)) == {("AC", "vorgabe")}
    assert zaehlen(db).ohne_stromart == 0


def test_ohne_vorgabe_wird_aufgabe_und_nachtraeglich_setzbar(angemeldet, db):
    msp = _msp(db, "hastobe_csv")
    imp = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, imp, {})
    anzahl = len(_arten(db, Ladevorgang.import_id == imp.id))
    assert set(_arten(db, Ladevorgang.import_id == imp.id)) == {("-", "-")}
    assert zaehlen(db).ohne_stromart == anzahl

    # Aufgaben: Kachel und Abschnitt je MSP
    text = angemeldet.get("/aufgaben").text
    assert f'<span class="kachel-zahl">{anzahl}</span>\n  <strong>Ladevorgänge ohne AC/DC-Angabe</strong>' in text
    assert f'<input type="hidden" name="msp_id" value="{msp.id}">' in text

    # Einzeln manuell „keine Angabe“ → bewusst entschieden, keine Aufgabe mehr und von Vorgaben unberührt
    erster = db.scalar(select(Ladevorgang).where(Ladevorgang.import_id == imp.id).order_by(Ladevorgang.id))
    angemeldet.post(f"/ladevorgaenge/{erster.id}/stromart", data={"stromart": ""})
    db.refresh(erster)
    assert (erster.stromart, erster.stromart_quelle) == (None, "manuell")

    # Import-Detail verweist auf die Aufgaben (gesetzt wird nur dort), dann je MSP setzen
    assert f"/aufgaben?stromart_msp={msp.id}#stromart" in angemeldet.get(f"/import/{imp.id}").text
    angemeldet.post("/aufgaben/stromart", data={"msp_id": msp.id, "stromart_vorgabe": "DC"})
    arten = _arten(db, Ladevorgang.import_id == imp.id)
    assert arten.count(("DC", "vorgabe")) == anzahl - 1 and ("-", "manuell") in arten
    assert zaehlen(db).ohne_stromart == 0
    assert "Vorgabe des" in angemeldet.get(f"/ladevorgaenge/{erster.id + 1}").text  # Herkunft auf der Detailseite


def test_vorgabe_beim_msp_und_in_aufgaben(angemeldet, db):
    msp = _msp(db, "dcs")
    imp = Import(dateiname="v.pdf", datei_pfad="v.pdf", datei_hash="v" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    for tag, (art, quelle) in enumerate([(None, None), (None, None), ("AC", "rechnung"), (None, "manuell")], start=1):
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, tag, 8),
                           ende=datetime(2026, 9, tag, 9), kwh=Decimal(30), kosten_netto=Decimal(15), stromart=art,
                           stromart_quelle=quelle, ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    bedingung = Ladevorgang.import_id == imp.id

    # MSP: Vorgabe speichern, Anzahl ohne Angabe, anwenden – Rechnung und manuelle Entscheidung bleiben
    angemeldet.post(f"/stammdaten/msp/{msp.id}", data={"name": msp.name, "parser_key": "dcs",
                                                       "standard_ladeart": "oeffentlich", "stromart_standard": "DC"})
    db.refresh(msp)
    assert msp.stromart_standard == "DC"
    assert "2 Ladevorgänge dieses" in angemeldet.get(f"/stammdaten/msp/{msp.id}").text
    liste = angemeldet.get("/stammdaten/msp").text  # Übersicht zeigt die Vorgabe
    assert "<th>Standard AC/DC</th>" in liste and '<span class="marker stromart-dc">DC</span>' in liste and "keine Angabe" in liste
    assert f"/aufgaben?stromart_msp={msp.id}#stromart" in angemeldet.get(f"/stammdaten/msp/{msp.id}").text
    angemeldet.post("/aufgaben/stromart", data={"msp_id": msp.id, "stromart_vorgabe": "DC"})
    assert _arten(db, bedingung) == [("-", "manuell"), ("AC", "rechnung"), ("DC", "vorgabe"), ("DC", "vorgabe")]

    # Aufgaben: setzen je MSP
    db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, 9, 8), ende=datetime(2026, 9, 9, 9),
                       kwh=Decimal(30), kosten_netto=Decimal(15), ladeart=Ladeart.oeffentlich,
                       zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    angemeldet.post("/aufgaben/stromart", data={"msp_id": msp.id, "stromart_vorgabe": "AC"})
    assert db.scalar(select(func.count()).select_from(Ladevorgang).where(bedingung, stromart.ohne_angabe())) == 0
    assert ("AC", "vorgabe") in _arten(db, bedingung)


def test_einzeln_in_aufgaben_setzen(angemeldet, db):
    msp = _msp(db, "dcs")
    imp = Import(dateiname="e.pdf", datei_pfad="e.pdf", datei_hash="e" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    vs = []
    for tag in range(1, 5):  # 30 kWh in 1 h → 30 kW (Hinweis DC)
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 8, tag, 8), ende=datetime(2026, 8, tag, 9),
                        kwh=Decimal(30), kosten_netto=Decimal(15), stromart="AC" if tag == 4 else None,
                        stromart_quelle="rechnung" if tag == 4 else None, ladeart=Ladeart.oeffentlich,
                        zuordnung_quelle=ZuordnungQuelle.keine)
        db.add(v)
        vs.append(v)
    db.flush()
    text = angemeldet.get(f"/aufgaben?stromart_msp={msp.id}").text
    assert 'id="stromart-einzeln"' in text and "30,0 kW" in text
    liste = text.split('id="stromart-einzeln"')[1].split("</form>")[0]
    assert liste.count('name="vorgang_ids"') == 3 and f'value="{vs[3].id}"' not in liste  # nur ohne Angabe
    assert liste.count('data-vorschlag="DC"') == 3 and "DC-Vorschläge markieren" in liste  # 30 kW → DC

    angemeldet.post("/aufgaben/stromart-einzeln", data={"vorgang_ids": [vs[0].id, vs[1].id], "stromart_wert": "DC",
                                                          "stromart_msp": str(msp.id)})
    angemeldet.post("/aufgaben/stromart-einzeln", data={"vorgang_ids": [vs[2].id], "stromart_wert": ""})
    for v in vs:
        db.refresh(v)
    assert [(v.stromart, v.stromart_quelle) for v in vs] == [
        ("DC", "manuell"), ("DC", "manuell"), (None, "manuell"), ("AC", "rechnung")]
    assert db.scalar(select(func.count()).select_from(Ladevorgang).where(
        Ladevorgang.import_id == imp.id, stromart.ohne_angabe())) == 0


def test_vorschlag_nach_leistung(db):
    e = app_einstellungen.laden(db)  # Standard: AC unplausibel ab 12 kW, DC unplausibel unter 10 kW
    assert stromart.vorschlag(Decimal("30"), e)[0] == "DC"
    assert stromart.vorschlag(Decimal("12"), e)[0] == "DC"
    assert stromart.vorschlag(Decimal("11.9"), e)[0] is None      # zwischen den Grenzen
    assert stromart.vorschlag(Decimal("10"), e)[0] is None
    assert stromart.vorschlag(Decimal("9.9"), e)[0] == "AC"
    assert stromart.vorschlag(None, e)[0] is None                 # geschätzte kWh: keine Leistung
    assert stromart.vorschlag(Decimal("10.3"), e)[1] == "Ø 10,3 kW zwischen den Grenzen – kein sicherer Vorschlag"
