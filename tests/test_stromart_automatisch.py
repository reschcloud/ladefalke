"""AC/DC ohne Angabe der Rechnung automatisch: CPO-Liste vor Ø-Leistung vor Vorgabe des MSP (services/stromart.py)."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (CpoStromart, Import, ImportStatus, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungQuelle)
from app.services import app_einstellungen, stromart
from app.services.importe import bestaetigen, hochladen, vorschau_berechnen

D = Decimal
CSV = (Path(__file__).parent / "fixtures" / "hastobe_beispiel.csv").read_bytes()  # ohne AC/DC-Angabe


@pytest.fixture
def imp(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    i = Import(dateiname="t.pdf", datei_pfad="t.pdf", datei_hash="t" * 64, msp_id=msp.id,
               ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(i)
    db.flush()
    return i


def _vorgang(db, imp, tag, *, kwh="30", minuten=60, betreiber=None, art=None, quelle=None, geschaetzt=False):
    start = datetime(2026, 9, tag, 8)
    v = Ladevorgang(import_id=imp.id, msp_id=imp.msp_id, start=start, ende=start + timedelta(minutes=minuten),
                    kwh=D(kwh), kwh_geschaetzt=geschaetzt, kosten_netto=D(10), betreiber_id=betreiber,
                    stromart=art, stromart_quelle=quelle, ladeart=Ladeart.oeffentlich,
                    zuordnung_quelle=ZuordnungQuelle.keine)
    db.add(v)
    db.flush()
    return v


def test_bestimmen_reihenfolge(db):
    e = app_einstellungen.laden(db)  # Standard: AC unplausibel ab 12 kW, DC unter 10 kW
    regeln = {"at*ioy": CpoStromart(name="Ionity", kennungen=["AT*IOY"], stromart="DC")}
    # CPO-Liste schlägt die Leistung (lange angesteckt: nur 8 kW, trotzdem DC)
    assert stromart.bestimmen("AT*IOY", D(8), regeln, e)[:2] == ("DC", "cpo")
    assert stromart.bestimmen("at*ioy", None, regeln, e)[:2] == ("DC", "cpo")  # Groß-/Kleinschreibung egal
    assert stromart.bestimmen("AT*XYZ", D(40), regeln, e)[:2] == ("DC", "leistung")
    assert stromart.bestimmen(None, D(7), regeln, e)[:2] == ("AC", "leistung")
    assert stromart.bestimmen(None, D(11), regeln, e) is None  # zwischen den Grenzen: keine sichere Aussage
    assert stromart.bestimmen(None, None, regeln, e) is None
    assert stromart.leistung_kw(D(30), True, datetime(2026, 1, 1), datetime(2026, 1, 1, 1)) is None  # geschätzt


def test_neu_bestimmen_laesst_rechnung_und_manuell(db, imp):
    db.add(CpoStromart(name="Ionity", kennungen=["AT*IOY", "DE*ION"], stromart="DC"))
    db.add(CpoStromart(name="Eigene Firma", kennungen=[], stromart="AC"))  # ohne Kennung: über den Namen
    rechnung = _vorgang(db, imp, 1, betreiber="AT*IOY", art="AC", quelle="rechnung")
    manuell = _vorgang(db, imp, 2, betreiber="AT*IOY", quelle="manuell")
    vorgabe = _vorgang(db, imp, 3, kwh="11", betreiber="DE*ION", art="AC", quelle="vorgabe")  # 11 kW
    schnell = _vorgang(db, imp, 4, kwh="40")  # 40 kW → DC
    langsam = _vorgang(db, imp, 5, kwh="5", minuten=120)  # 2,5 kW → AC
    mitte = _vorgang(db, imp, 6, kwh="11")  # 11 kW: keine Aussage
    firma = _vorgang(db, imp, 7, kwh="11", betreiber="eigene firma")
    geschaetzt = _vorgang(db, imp, 8, kwh="40", geschaetzt=True)

    assert stromart.neu_bestimmen(db) == 4
    assert (rechnung.stromart, rechnung.stromart_quelle) == ("AC", "rechnung")
    assert (manuell.stromart, manuell.stromart_quelle) == (None, "manuell")
    assert (vorgabe.stromart, vorgabe.stromart_quelle) == ("DC", "cpo")
    assert vorgabe.rohdaten["stromart_vorgabe"] == "AC"  # verdrängte MSP-Vorgabe gemerkt
    assert (schnell.stromart, schnell.stromart_quelle) == ("DC", "leistung")
    assert (langsam.stromart, langsam.stromart_quelle) == ("AC", "leistung")
    assert (mitte.stromart, mitte.stromart_quelle) == (None, None)
    assert (firma.stromart, firma.stromart_quelle) == ("AC", "cpo")
    assert (geschaetzt.stromart, geschaetzt.stromart_quelle) == (None, None)
    assert stromart.neu_bestimmen(db) == 0  # nichts mehr zu tun

    # Eintrag entfernt: die frühere MSP-Vorgabe gilt wieder
    db.delete(db.scalar(select(CpoStromart).where(CpoStromart.name == "Ionity")))
    db.flush()
    assert stromart.neu_bestimmen(db) == 1
    assert (vorgabe.stromart, vorgabe.stromart_quelle) == ("AC", "vorgabe")


def test_manuell_nach_automatisch_merkt_keine_rechnungsangabe(db, imp):
    v = _vorgang(db, imp, 1, kwh="40")
    stromart.neu_bestimmen(db)
    stromart.manuell_setzen(v, "AC")
    assert (v.stromart, v.stromart_quelle, v.rohdaten["stromart_rechnung"]) == ("AC", "manuell", None)


def test_import_mit_cpo_liste(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "hastobe_csv"))
    msp.cpo_standard = "AT*TST"  # CSV liefert keinen CPO → Vorgabe des MSP
    db.add(CpoStromart(name="Test-Wallboxen", kennungen=["AT*TST"], stromart="AC"))
    db.commit()
    antwort = angemeldet.post("/import", files={"datei_upload": ("oktober.csv", CSV, "text/csv")})
    assert "laut CPO-Liste" in antwort.text and 'href="/einstellungen#cpo-stromart"' in antwort.text
    assert 'name="stromart_vorgabe"' not in antwort.text  # alle bestimmt: keine Auswahl nötig
    assert "Daten vollständig" not in antwort.text  # neue Karten: Vorgänge noch ohne Zuordnung
    imp = db.scalar(select(Import).where(Import.dateiname == "oktober.csv"))
    # Alle Karten bekannt (Gastkarten gelten als zugeordnet), AC/DC laut CPO-Liste: grüner Haken
    for k in vorschau_berechnen(db, imp).karten:
        db.add(Ladekarte(msp_id=msp.id, kennung=k.kennung, kennung_typ=k.kennung_typ, ist_gastkarte=True))
    db.commit()
    assert "Daten vollständig" not in angemeldet.get(f"/import/{imp.id}").text  # Hinweis: Vorgang mit 0 kWh
    ohne_null = b"\n".join(z for z in CSV.split(b"\n") if b";0;" not in z)
    antwort = angemeldet.post("/import", files={"datei_upload": ("ohne-null.csv", ohne_null, "text/csv")})
    assert "Daten vollständig" in angemeldet.get(antwort.url).text
    bestaetigen(db, imp, {}, "DC")
    arten = {(v.stromart, v.stromart_quelle, v.rohdaten.get("stromart_vorgabe"))
             for v in db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id))}
    assert arten == {("AC", "cpo", "DC")}  # gewählte Vorgabe gemerkt, falls die Regel später wegfällt


def test_import_aus_leistung(db):
    imp = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, imp, {})
    quellen = {v.stromart_quelle for v in db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id))}
    assert quellen <= {"leistung", None} and "leistung" in quellen


def test_einstellungen_cpo_liste(angemeldet, db, imp):
    v = _vorgang(db, imp, 1, kwh="11", betreiber="AT*IOY")
    andere = _vorgang(db, imp, 2, kwh="11", betreiber="DE*ION")
    db.commit()

    antwort = angemeldet.post("/einstellungen/cpo-stromart", data={"name": " Ionity ", "kennungen": "at*ioy",
                                                                   "art": "DC"})
    assert "CPO „Ionity“ = immer DC gespeichert – AC/DC von 1 Ladevorgang neu bestimmt." in antwort.text
    eintrag = db.scalar(select(CpoStromart))
    assert (eintrag.name, eintrag.kennungen) == ("Ionity", ["AT*IOY"])
    db.refresh(v)
    assert (v.stromart, v.stromart_quelle) == ("DC", "cpo")
    assert "DE*ION (1)" in antwort.text  # Vorschlag: häufiger CPO ohne Angabe der Rechnung

    # gleicher Name (Groß-/Kleinschreibung egal) ändert den Eintrag, auch die Schreibweise; Kennungen normiert
    angemeldet.post("/einstellungen/cpo-stromart", data={"name": "IONITY", "kennungen": "AT*IOY, de*ion", "art": "DC"})
    db.refresh(eintrag)
    db.refresh(andere)
    assert (eintrag.name, eintrag.kennungen) == ("IONITY", ["AT*IOY", "DE*ION"]) and andere.stromart_quelle == "cpo"
    angemeldet.post("/einstellungen/cpo-stromart", data={"name": "Ionity", "kennungen": "AT*IOY DE*ION", "art": "DC"})

    # dieselbe Kennung in einem zweiten Eintrag: abgelehnt
    antwort = angemeldet.post("/einstellungen/cpo-stromart", data={"name": "Anders", "kennungen": "AT*IOY", "art": "AC"})
    assert "AT*IOY gehört schon zu „Ionity“" in antwort.text
    seite = angemeldet.get("/einstellungen").text
    assert 'id="cpo-stromart"' in seite and "AT*IOY, DE*ION" in seite

    antwort = angemeldet.post(f"/einstellungen/cpo-stromart/{eintrag.id}/loeschen")
    assert "CPO „Ionity“ entfernt – AC/DC von 2 Ladevorgängen neu bestimmt." in antwort.text
    db.refresh(v)
    assert (v.stromart, v.stromart_quelle) == (None, None)
    assert "keine Änderungen" in angemeldet.post("/einstellungen/stromart-neu-bestimmen").text


def test_grenzen_aendern_bestimmt_neu(angemeldet, db, imp):
    v = _vorgang(db, imp, 1, kwh="11")  # 11 kW: bei Standardgrenzen keine Aussage
    db.commit()
    werte = {k: (w if isinstance(w, str) else "") for k, w in app_einstellungen.STANDARD.items()}
    werte.update({"plausibel_ac_max_kw": "10,5", "firmenland": "AT", "gruppe": "ladevorgaenge"})
    antwort = angemeldet.post("/einstellungen", data=werte)
    assert "AC/DC von 1 Ladevorgang neu bestimmt." in antwort.text, antwort.text[:2000]
    db.refresh(v)
    assert (v.stromart, v.stromart_quelle) == ("DC", "leistung")


def test_detailseite_zeigt_herkunft(angemeldet, db, imp):
    db.add(CpoStromart(name="Ionity", kennungen=["AT*IOY"], stromart="DC"))
    v = _vorgang(db, imp, 1, betreiber="AT*IOY")
    w = _vorgang(db, imp, 2, kwh="40")
    stromart.neu_bestimmen(db)
    db.commit()
    assert "laut CPO-Liste (Ionity)" in angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert "aus der Ø-Leistung" in angemeldet.get(f"/ladevorgaenge/{w.id}").text


def test_kennung_ohne_land_gilt_fuer_alle_laender(db, imp):
    assert [stromart.kennung_normieren(t) for t in ("ion", "*IOY", "at*ioy", "Eigene Firma")] == \
        ["ION", "IOY", "AT*IOY", "Eigene Firma"]
    db.add(CpoStromart(name="Ionity", kennungen=["IOY"], stromart="DC"))
    at, de, fremd = (_vorgang(db, imp, 1, kwh="11", betreiber="AT*IOY"), _vorgang(db, imp, 2, kwh="11", betreiber="DE*IOY"),
                     _vorgang(db, imp, 3, kwh="11", betreiber="AT*IOX"))
    assert stromart.neu_bestimmen(db) == 2
    assert [v.stromart_quelle for v in (at, de, fremd)] == ["cpo", "cpo", None]


def test_vorschau_stromart_je_vorgang_aendern(angemeldet, db):
    antwort = angemeldet.post("/import", files={"datei_upload": ("oktober.csv", CSV, "text/csv")})
    imp = db.scalar(select(Import).where(Import.dateiname == "oktober.csv"))
    assert 'name="stromart_0" data-stromart-wahl' in antwort.text and "Ø-Leistung" in antwort.text
    vs = vorschau_berechnen(db, imp)
    ids = [x.v.externe_id for x in vs.vorgaenge[:3]]
    auto = [x.stromart_auto[0] if x.stromart_auto else None for x in vs.vorgaenge[:3]]
    andere = "AC" if auto[0] == "DC" else "DC"  # bewusst abweichend gewählt
    angemeldet.post(f"/import/{imp.id}/bestaetigen", data={"stromart_0": andere, "stromart_1": "keine",
                                                           "stromart_2": "", "stromart_vorgabe": ""})
    je_id = {v.externe_id: v for v in db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id))}
    erster, zweiter, dritter = (je_id[i] for i in ids)
    assert (erster.stromart, erster.stromart_quelle) == (andere, "manuell")
    assert erster.rohdaten["stromart_rechnung"] is None  # die Rechnung nennt keine Stromart
    assert (zweiter.stromart, zweiter.stromart_quelle) == (None, "manuell")
    assert (dritter.stromart, dritter.stromart_quelle != "manuell") == (auto[2], True)  # „so lassen“
