"""Fristen: Leasing-Ende je Fahrzeug, Führerscheinkontrolle je Fahrer:in, Info unter „Aufgaben“."""
from datetime import date, timedelta

from sqlalchemy import select

from app.config import heute
from app.models import Fahrer, Fahrzeug
from app.services import app_einstellungen, fristen
from app.services.fristen import leasing_ende_vorschlag, plus_monate
from app.services.offen import zaehlen


def test_monate_rechnen():
    assert plus_monate(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert plus_monate(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert leasing_ende_vorschlag(date(2026, 1, 1), 36) == date(2028, 12, 31)
    assert leasing_ende_vorschlag(date(2026, 3, 15), 24) == date(2028, 3, 14)


def test_leasing_im_formular(angemeldet, db):
    # Ende leer → aus Beginn + Leasingdauer (Standard 36 Monate)
    angemeldet.post("/stammdaten/fahrzeuge/neu", data={"kennzeichen": "L-1", "aktiv": "on",
                                                        "leasing_beginn": "2026-01-01"})
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "L-1"))
    assert (fz.leasing_beginn, fz.leasing_ende) == (date(2026, 1, 1), date(2028, 12, 31))
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'data-leasing-monate="36"' in seite and 'value="2028-12-31"' in seite
    # Ende vor Beginn → Fehler; eigenes Ende bleibt
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data={
        "kennzeichen": "L-1", "aktiv": "on", "leasing_beginn": "2026-01-01", "leasing_ende": "2025-12-31"})
    assert antwort.status_code == 422 and "vor dem Beginn" in antwort.text
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}", data={
        "kennzeichen": "L-1", "aktiv": "on", "leasing_beginn": "2026-01-01", "leasing_ende": "2027-06-30"})
    db.refresh(fz)
    assert fz.leasing_ende == date(2027, 6, 30)


def test_fuehrerschein_im_formular(angemeldet, db):
    app_einstellungen.speichern(db, {"feature_fuehrerschein": "1"})  # Feature einschalten (Standard: aus)
    angemeldet.post("/stammdaten/fahrer/neu", data={"name": "Kontrolle Test", "aktiv": "on",
                                                     "fuehrerschein_kontrolle": "2026-05-10"})
    f = db.scalar(select(Fahrer).where(Fahrer.name == "Kontrolle Test"))
    assert f.fuehrerschein_kontrolle == date(2026, 5, 10)
    assert "nächste fällig am <strong>10.11.2026</strong>" in angemeldet.get(f"/stammdaten/fahrer/{f.id}").text
    morgen = (heute() + timedelta(days=1)).isoformat()
    antwort = angemeldet.post(f"/stammdaten/fahrer/{f.id}", data={"name": f.name, "aktiv": "on",
                                                                   "fuehrerschein_kontrolle": morgen})
    assert antwort.status_code == 422 and "Zukunft" in antwort.text


def test_fristen_und_aufgaben(angemeldet, db):
    app_einstellungen.speichern(db, {"feature_fuehrerschein": "1"})  # Feature einschalten (Standard: aus)
    tag = heute()
    e = app_einstellungen.laden(db)  # Standard: Leasing 90 Tage, Führerschein alle 6 Monate, 30 Tage vorher
    for f in db.scalars(select(Fahrer)):  # vorhandene Testdaten nicht mitzählen
        f.aktiv = False
    db.add_all([
        Fahrzeug(kennzeichen="F-BALD", leasing_ende=tag + timedelta(days=60)),
        Fahrzeug(kennzeichen="F-WEIT", leasing_ende=tag + timedelta(days=200)),
        Fahrzeug(kennzeichen="F-ALT", leasing_ende=tag - timedelta(days=5)),
        Fahrzeug(kennzeichen="F-INAKTIV", leasing_ende=tag, aktiv=False),
        Fahrer(name="Ohne Kontrolle"),
        Fahrer(name="Bald fällig", fuehrerschein_kontrolle=plus_monate(tag, -6) + timedelta(days=10)),
        Fahrer(name="Überfällig", fuehrerschein_kontrolle=plus_monate(tag, -7)),
        Fahrer(name="Frisch kontrolliert", fuehrerschein_kontrolle=tag),
    ])
    db.flush()
    gemeldet = {(x.art, x.name): x for x in fristen.ermitteln(db, e, tag)}
    assert {n for _, n in gemeldet} >= {"F-BALD", "F-ALT", "Ohne Kontrolle", "Bald fällig", "Überfällig"}
    assert not {"F-WEIT", "F-INAKTIV", "Frisch kontrolliert"} & {n for _, n in gemeldet}
    assert gemeldet[("leasing", "F-ALT")].ueberfaellig and gemeldet[("leasing", "F-BALD")].tage == 60
    assert gemeldet[("fuehrerschein", "Ohne Kontrolle")].faellig is None
    assert zaehlen(db).fristen == len(gemeldet) and zaehlen(db).zu_klaeren >= len(gemeldet)

    seite = angemeldet.get("/aufgaben").text
    assert seite.index("aufgaben-uebersicht") < seite.index('class="meldung meldung-warnung fristen"')  # erst Kacheln
    leasing = seite.split('id="frist-leasing"')[1].split("</section>")[0]  # je Art ein eigener Abschnitt
    fuehrerschein = seite.split('id="frist-fuehrerschein"')[1].split("</section>")[0]
    assert "F-ALT" in leasing and "Ohne Kontrolle" not in leasing and "Ohne Kontrolle" in fuehrerschein
    assert 'href="#frist-leasing"' in seite and 'href="#frist-fuehrerschein"' in seite  # Kacheln je Art
    assert "F-ALT</a> <strong>abgelaufen</strong>" in seite and "endet am" in seite
    assert "Ohne Kontrolle</a> <strong>fehlt</strong>" in seite and "Überfällig</a> <strong>überfällig</strong>" in seite

    # Einstellungen: Hinweisfrist verkürzen → F-BALD (60 Tage) fällt heraus
    werte = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
             "leasing_dauer_monate": "48", "leasing_vorwarnung_tage": "30", "fuehrerschein_intervall_monate": "12",
             "fuehrerschein_vorwarnung_tage": "14"}
    angemeldet.post("/einstellungen", data=werte)
    e = app_einstellungen.laden(db)
    assert (e.leasing_dauer_monate, e.leasing_vorwarnung_tage, e.fuehrerschein_intervall_monate,
            e.fuehrerschein_vorwarnung_tage) == (48, 30, 12, 14)
    assert "F-BALD" not in {x.name for x in fristen.ermitteln(db, e, tag)}
    antwort = angemeldet.post("/einstellungen", data={**werte, "leasing_dauer_monate": "0"})
    assert antwort.status_code == 422 and "größer 0" in antwort.text



def test_fuehrerschein_heute_knopf(angemeldet, db):
    app_einstellungen.speichern(db, {"feature_fuehrerschein": "1"})  # Feature einschalten (Standard: aus)
    f = Fahrer(name="Heute Knopf")
    db.add(f)
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrer/{f.id}").text
    assert f'data-datum-setzen="fuehrerschein_kontrolle"\n            data-wert="{heute().isoformat()}">Heute</button>' in seite
