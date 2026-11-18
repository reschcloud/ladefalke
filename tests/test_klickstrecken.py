"""Klickstrecken aus dem Review vom 16.11.2026: Rücksprünge mit Filter/Anker, Vorbelegung aus Lücken und Aufgaben,
Pool-Hinweis mit „Bisherige austragen“, Filter aufheben, Status nur über Knöpfe, ungespeicherte Änderungen."""
from datetime import date, datetime
from decimal import Decimal as D

from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)


def _msp(db):
    return db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))


def test_karte_zuordnen_mit_ruecksprung_und_vorbelegung(angemeldet, db):
    fz = Fahrzeug(kennzeichen="W-KS 1")
    karte = Ladekarte(msp=_msp(db), kennung="KS-1", kennung_typ=KennungTyp.kartennummer)
    db.add_all([fz, karte])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/ladekarten/{karte.id}?zurueck=/aufgaben%23ohne-fahrzeug&ab=2026-03-05").text
    assert 'value="2026-03-05"' in seite and 'name="zurueck" value="/aufgaben#ohne-fahrzeug"' in seite
    assert "← Zurück" in seite
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}/zuordnung", follow_redirects=False,
                              data={"ziel": f"fahrzeug:{fz.id}", "ab": "2026-03-05", "zurueck": "/aufgaben#ohne-fahrzeug"})
    assert antwort.status_code == 303 and antwort.headers["location"] == "/aufgaben#ohne-fahrzeug"
    # fremde Ziele werden ignoriert
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{karte.id}/zuordnung", follow_redirects=False,
                              data={"ziel": f"fahrzeug:{fz.id}", "ab": "2026-04-01", "zurueck": "//boese.example"})
    assert antwort.headers["location"] == f"/stammdaten/ladekarten/{karte.id}"


def test_luecke_belegt_formular_vor(angemeldet, db):
    fz, fahrer = Fahrzeug(kennzeichen="W-KS 2"), Fahrer(name="Lena Lücke")
    db.add_all([fz, fahrer])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}?ab=2026-02-01&bis=2026-02-28").text
    assert 'value="2026-02-01"' in seite and 'value="2026-02-28"' in seite
    seite = angemeldet.get(f"/stammdaten/fahrer/{fahrer.id}?ab=kaputt").text  # ungültig → Standard, kein Fehler
    assert "Neue Zuordnung" in seite or "neue-fahrzeug-zuordnung" in seite


def test_pool_hinweis_und_bisherige_austragen(angemeldet, db):
    fz, alt, neu = Fahrzeug(kennzeichen="W-POOL 1"), Fahrer(name="Alt Fahrer"), Fahrer(name="Neu Fahrer")
    db.add_all([fz, alt, neu])
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=alt.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    seite = angemeldet.post(f"/stammdaten/fahrer/{neu.id}/fahrzeug",
                            data={"fahrzeug_id": str(fz.id), "ab": "2026-06-01"}).text
    assert "W-POOL 1 hat damit 2 Fahrer:innen (Pool)" in seite and "Bisherige austragen" in seite
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/bisherige-austragen", follow_redirects=False,
                              data={"ab": "2026-06-01", "fahrer_id": str(neu.id), "zurueck": f"/stammdaten/fahrer/{neu.id}"})
    assert antwort.headers["location"] == f"/stammdaten/fahrer/{neu.id}"
    z = db.scalar(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrer_id == alt.id))
    db.refresh(z)
    assert z.gueltig_bis == date(2026, 5, 31)
    # ohne andere Fahrer:innen kein Pool-Hinweis
    fz2 = Fahrzeug(kennzeichen="W-POOL 2")
    db.add(fz2)
    db.flush()
    seite = angemeldet.post(f"/stammdaten/fahrer/{alt.id}/fahrzeug", data={"fahrzeug_id": str(fz2.id), "ab": "2026-06-01"}).text
    assert "(Pool)" not in seite


def _vorgang(db, karte, tag):
    imp = Import(dateiname="k.pdf", datei_pfad="k.pdf", datei_hash=f"k{tag:063d}", msp_id=karte.msp_id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    v = Ladevorgang(import_id=imp.id, msp_id=karte.msp_id, ladekarte_id=karte.id, start=datetime(2026, 9, tag, 8),
                    ende=datetime(2026, 9, tag, 9), kwh=D("10"), kosten_netto=D("5.00"), ladeart=Ladeart.oeffentlich,
                    zuordnung_quelle=ZuordnungQuelle.keine)
    db.add(v)
    db.flush()
    return v


def test_aufgaben_zuweisen_behaelt_filter(angemeldet, db):
    karte = Ladekarte(msp=_msp(db), kennung="KS-3", kennung_typ=KennungTyp.kartennummer)
    fz = Fahrzeug(kennzeichen="W-KS 3")
    db.add_all([karte, fz])
    db.flush()
    v = _vorgang(db, karte, 3)
    seite = angemeldet.get(f"/aufgaben?karte_id={karte.id}").text
    assert f'name="zurueck" value="/aufgaben?karte_id={karte.id}#vorgaenge"' in seite
    assert f"/stammdaten/ladekarten/{karte.id}?zurueck=%2Faufgaben%23ohne-fahrzeug&ab=2026-09-03" in seite
    antwort = angemeldet.post("/aufgaben/zuweisen", follow_redirects=False, data={
        "vorgang_ids": str(v.id), "fahrzeug_id": str(fz.id), "zurueck": f"/aufgaben?karte_id={karte.id}#vorgaenge"})
    assert antwort.headers["location"] == f"/aufgaben?karte_id={karte.id}#vorgaenge"
    antwort = angemeldet.post("/aufgaben/zuweisen", follow_redirects=False, data={
        "vorgang_ids": str(v.id), "fahrzeug_id": str(fz.id), "zurueck": "https://boese.example"})
    assert antwort.headers["location"] == "/aufgaben"


def test_filter_aufheben_nur_eigener_filter(angemeldet, db):
    karte = Ladekarte(msp=_msp(db), kennung="KS-4", kennung_typ=KennungTyp.kartennummer, bezeichnung="Karte Vier")
    db.add(karte)
    db.flush()
    seite = angemeldet.get(f"/ladevorgaenge?ladekarte_id={karte.id}&suche=abc&import_id=7").text
    assert "Karte Vier (KS-4)" in seite
    assert 'href="/ladevorgaenge?suche=abc&amp;import_id=7"' in seite  # Karte weg, Rest bleibt
    assert f'href="/ladevorgaenge?suche=abc&amp;ladekarte_id={karte.id}"' in seite  # Import weg, Rest bleibt
    # „← Ladevorgänge“ im Detail führt zur zuletzt angesehenen Liste
    v = _vorgang(db, karte, 4)
    angemeldet.get(f"/ladevorgaenge?ladekarte_id={karte.id}")
    assert f'href="/ladevorgaenge?ladekarte_id={karte.id}"' in angemeldet.get(f"/ladevorgaenge/{v.id}").text


def test_status_nur_ueber_knoepfe(angemeldet, db):
    fahrer = Fahrer(name="Ina Aktiv")
    db.add(fahrer)
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrer/{fahrer.id}").text
    assert 'type="checkbox" name="aktiv"' not in seite and '<input type="hidden" name="aktiv" value="on">' in seite
    fahrer.aktiv = False
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrer/{fahrer.id}").text
    assert 'name="aktiv"' not in seite and "Wieder aktivieren" in seite  # Speichern lässt inaktiv


def test_ungespeichert_und_fristen_kachel(angemeldet, db):
    seite = angemeldet.get("/einstellungen").text
    assert 'id="einstellungen-form" data-verfolgen' in seite
    assert seite.count('data-erst-speichern="einstellungen-form"') >= 3  # Testmail, Testmeldung, Jetzt sichern
    aufgaben = angemeldet.get("/aufgaben").text
    assert "Fristen</strong>" in aufgaben  # Kachel immer da (✓ wenn nichts fällig)


# ------------------------------------------------------------------ Review-Runde 2 (17.11.2026)

def test_karte_zuordnen_von_fahrzeugseite(angemeldet, db):
    fz = Fahrzeug(kennzeichen="W-A10 1")
    karte = Ladekarte(msp=_msp(db), kennung="A10-1", kennung_typ=KennungTyp.kartennummer, bezeichnung="Karte Zehn")
    gast = Ladekarte(msp=_msp(db), kennung="A10-G", kennung_typ=KennungTyp.kartennummer, ist_gastkarte=True)
    db.add_all([fz, karte, gast])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'action="/stammdaten/ladekarten/zuordnen"' in seite and "Karte Zehn (A10-1)" in seite and "A10-G" not in seite
    antwort = angemeldet.get(f"/stammdaten/ladekarten/zuordnen?karte_id={karte.id}&ziel=fahrzeug:{fz.id}"
                             f"&zurueck=/stammdaten/fahrzeuge/{fz.id}", follow_redirects=False)
    ziel = antwort.headers["location"]
    assert ziel.startswith(f"/stammdaten/ladekarten/{karte.id}?") and ziel.endswith("#neue-karten-zuordnung")
    seite = angemeldet.get(ziel.split("#")[0]).text
    assert f'value="fahrzeug:{fz.id}" selected' in seite and f'name="zurueck" value="/stammdaten/fahrzeuge/{fz.id}"' in seite


def test_nicht_verorten_rueckgaengig(angemeldet, db):
    from app.models import Ladeort
    karte = Ladekarte(msp=_msp(db), kennung="A13-1", kennung_typ=KennungTyp.kartennummer)
    db.add(karte)
    db.flush()
    v = _vorgang(db, karte, 13)
    ort = Ladeort(schluessel="AT|a13", adresse="Irgendwo 1, Nirgendheim", land="AT")
    db.add(ort)
    db.flush()
    v.ladeort_id = ort.id
    db.flush()
    seite = angemeldet.post(f"/aufgaben/ladeort/{ort.id}/verzichten").text
    assert f'action="/aufgaben/ladeort/{ort.id}/wieder-verorten"' in seite and "Rückgängig" in seite
    assert "Bewusst nicht verortet (1)" in seite
    angemeldet.post(f"/aufgaben/ladeort/{ort.id}/wieder-verorten")
    db.refresh(ort)
    assert ort.genauigkeit != "verzichtet"


def test_menue_anzahlen_und_kein_stammdaten_abschnitt(angemeldet, db):
    db.add(Fahrzeug(kennzeichen="W-B1 1"))
    db.flush()
    seite = angemeldet.get("/").text
    assert "<h2>Stammdaten</h2>" not in seite and 'class="menue-anzahl"' in seite


def test_import_bezeichnung_ohne_nummer(db):
    msp = _msp(db)
    imp = Import(dateiname="sept.pdf", datei_pfad="x", datei_hash="b" * 64, msp_id=msp.id, msp=msp,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    assert imp.bezeichnung == "sept.pdf"
    imp.abrechnungszeitraum_von, imp.abrechnungszeitraum_bis = date(2026, 9, 1), date(2026, 9, 30)
    assert imp.bezeichnung == f"{msp.name} · 09/2026"
    imp.rechnungsnummer = "R-1"
    assert imp.bezeichnung == f"{msp.name} · Rechnung R-1"


def test_einstellungen_zurueck_zur_gruppe_und_msp_bleibt(angemeldet, db):
    basis = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    antwort = angemeldet.post("/einstellungen", data=basis | {"gruppe": "fristen"}, follow_redirects=False)
    assert antwort.headers["location"] == "/einstellungen#fristen"
    antwort = angemeldet.post("/einstellungen", data=basis | {"gruppe": "x\"><script"}, follow_redirects=False)
    assert antwort.headers["location"] == "/einstellungen"
    msp = _msp(db)
    antwort = angemeldet.post(f"/stammdaten/msp/{msp.id}", follow_redirects=False, data={
        "name": msp.name, "parser_key": msp.parser_key or "", "standard_ladeart": "oeffentlich"})
    assert antwort.headers["location"] == f"/stammdaten/msp/{msp.id}"
