"""Zeiträume „ohne Fahrer:in“ (Fahrzeug bewusst niemandem zugeordnet) und Zusammenfassen aneinandergrenzender
Zuordnungen."""
import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.models import (Fahrer, Fahrzeug, FahrzeugOhneFahrer, KennungTyp, Ladekarte, Msp, ZuordnungFahrerFahrzeug,
                        ZuordnungKarte)
from app.services import fahrzeug_hinweise, luecken, app_einstellungen
from app.services.zuordnung import (ZuordnungsFehler, fahrer_fahrzeug_aendern, fahrer_fahrzeug_zuordnen,
                                    karte_zuordnen, ohne_fahrer_aendern, ohne_fahrer_eintragen)

D = date
HEUTE = D(2026, 11, 18)


@pytest.fixture
def fz_anna(db):
    fz = Fahrzeug(kennzeichen="OF-1", leasing_beginn=D(2026, 1, 1))
    anna = Fahrer(name="Anna Ohne")
    db.add_all([fz, anna])
    db.flush()
    return fz, anna


def _ohne(db, fz):
    return [(o.gueltig_ab, o.gueltig_bis) for o in
            db.scalars(select(FahrzeugOhneFahrer).where(FahrzeugOhneFahrer.fahrzeug_id == fz.id)
                       .order_by(FahrzeugOhneFahrer.gueltig_ab))]


def _fahrer(db, fz):
    return [(z.fahrer_id, z.gueltig_ab, z.gueltig_bis) for z in
            db.scalars(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrzeug_id == fz.id)
                       .order_by(ZuordnungFahrerFahrzeug.gueltig_ab))]


def test_ohne_fahrer_ist_keine_luecke(db, fz_anna):
    fz, anna = fz_anna
    fahrer_fahrzeug_zuordnen(db, anna, fz, ab=D(2026, 1, 1), bis=D(2026, 5, 31), heute=HEUTE)
    tag = D(2026, 11, 1)
    assert [(l.von, l.bis, l.offen) for l in luecken.ermitteln(db, tag) if l.fahrzeug.id == fz.id] == [
        (D(2026, 6, 1), tag, True)]
    ohne_fahrer_eintragen(db, fz, ab=D(2026, 6, 1))  # offen: seitdem bewusst niemandem zugeordnet
    assert [l for l in luecken.ermitteln(db, tag) if l.fahrzeug.id == fz.id] == []


def test_ohne_fahrer_hinweis_heute(db, fz_anna):
    """Kein roter Hinweis „ohne Fahrer:in“, wenn das Fahrzeug heute bewusst niemandem zugeordnet ist."""
    fz, _ = fz_anna
    e = app_einstellungen.laden(db)
    assert any("ohne Fahrer:in" in h.text for h in fahrzeug_hinweise.ermitteln(db, [fz], e)[fz.id])
    ohne_fahrer_eintragen(db, fz, ab=D(2026, 1, 1))
    assert not any("ohne Fahrer:in" in h.text for h in fahrzeug_hinweise.ermitteln(db, [fz], e)[fz.id])


def test_ohne_fahrer_ueberschneidung_und_fahrerwechsel(db, fz_anna):
    fz, anna = fz_anna
    fahrer_fahrzeug_zuordnen(db, anna, fz, ab=D(2026, 1, 1), heute=HEUTE)
    with pytest.raises(ZuordnungsFehler, match="Anna Ohne ist dem Fahrzeug"):
        ohne_fahrer_eintragen(db, fz, ab=D(2026, 6, 1))
    # Mit „bisherige austragen“: Anna endet am Vortag, ab dann ohne Fahrer:in
    ausgetragen, _ = ohne_fahrer_eintragen(db, fz, ab=D(2026, 6, 1), bisherige_austragen=True)
    assert ausgetragen == [anna.id]
    assert _fahrer(db, fz) == [(anna.id, D(2026, 1, 1), D(2026, 5, 31))]
    assert _ohne(db, fz) == [(D(2026, 6, 1), None)]
    # Neue offene Fahrer:in-Zuordnung beendet den offenen Zeitraum „ohne Fahrer:in“ am Vortag
    _, hinweise = fahrer_fahrzeug_zuordnen(db, anna, fz, ab=D(2026, 9, 1), heute=HEUTE)
    assert _ohne(db, fz) == [(D(2026, 6, 1), D(2026, 8, 31))] and any("ohne Fahrer:in" in h for h in hinweise)
    # Befristete Zuordnung mitten in „ohne Fahrer:in“: Fehler statt stillem Kürzen
    with pytest.raises(ZuordnungsFehler, match="als „ohne Fahrer:in“ eingetragen"):
        fahrer_fahrzeug_zuordnen(db, Fahrer(name="X"), fz, ab=D(2026, 7, 1), bis=D(2026, 7, 10), heute=HEUTE)


def test_zusammenfassen_fahrer_karte_ohne(db, fz_anna):
    fz, anna = fz_anna
    fahrer_fahrzeug_zuordnen(db, anna, fz, ab=D(2026, 1, 1), bis=D(2026, 3, 31), heute=HEUTE)
    _, hinweise = fahrer_fahrzeug_zuordnen(db, anna, fz, ab=D(2026, 4, 1), bis=D(2026, 6, 30), heute=HEUTE)
    assert _fahrer(db, fz) == [(anna.id, D(2026, 1, 1), D(2026, 6, 30))]
    assert any("zusammengefasst" in h for h in hinweise)
    # Ändern, sodass zwei Perioden aneinandergrenzen
    z2, _ = fahrer_fahrzeug_zuordnen(db, anna, fz, ab=D(2026, 8, 1), bis=None, heute=HEUTE)
    fahrer_fahrzeug_aendern(db, z2, ab=D(2026, 7, 1), bis=None, fahrzeug_id=fz.id, heute=HEUTE)
    assert _fahrer(db, fz) == [(anna.id, D(2026, 1, 1), None)]
    # Nicht zusammengefasst: Lücke dazwischen bzw. andere Person
    fz2 = Fahrzeug(kennzeichen="OF-2")
    bernd = Fahrer(name="Bernd Ohne")
    db.add_all([fz2, bernd])
    db.flush()
    fahrer_fahrzeug_zuordnen(db, bernd, fz2, ab=D(2026, 1, 1), bis=D(2026, 1, 31), heute=HEUTE)
    fahrer_fahrzeug_zuordnen(db, bernd, fz2, ab=D(2026, 2, 2), bis=D(2026, 2, 28), heute=HEUTE)
    assert len(_fahrer(db, fz2)) == 2
    # Ladekarte: gleiche Karte, gleiches Ziel
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    karte = Ladekarte(msp=msp, kennung="OF-K1", kennung_typ=KennungTyp.kartennummer)
    db.add(karte)
    db.flush()
    karte_zuordnen(db, karte, ab=D(2026, 1, 1), bis=D(2026, 1, 31), fahrer_id=bernd.id, heute=HEUTE)
    karte_zuordnen(db, karte, ab=D(2026, 2, 1), bis=D(2026, 2, 28), fahrer_id=bernd.id, heute=HEUTE)
    karte_zuordnen(db, karte, ab=D(2026, 3, 1), bis=None, fahrzeug_id=fz2.id, heute=HEUTE)  # anderes Ziel
    assert [(z.fahrer_id, z.fahrzeug_id, z.gueltig_ab, z.gueltig_bis) for z in db.scalars(
        select(ZuordnungKarte).where(ZuordnungKarte.ladekarte_id == karte.id).order_by(ZuordnungKarte.gueltig_ab))] \
        == [(bernd.id, None, D(2026, 1, 1), D(2026, 2, 28)), (None, fz2.id, D(2026, 3, 1), None)]
    # „ohne Fahrer:in“ aneinander
    fz3 = Fahrzeug(kennzeichen="OF-3")
    db.add(fz3)
    db.flush()
    ohne_fahrer_eintragen(db, fz3, ab=D(2026, 1, 1), bis=D(2026, 1, 31))
    ohne_fahrer_eintragen(db, fz3, ab=D(2026, 2, 1), bis=D(2026, 2, 28))
    assert _ohne(db, fz3) == [(D(2026, 1, 1), D(2026, 2, 28))]
    o = db.scalar(select(FahrzeugOhneFahrer).where(FahrzeugOhneFahrer.fahrzeug_id == fz3.id))
    ohne_fahrer_eintragen(db, fz3, ab=D(2026, 4, 1), bis=D(2026, 4, 30))
    ohne_fahrer_aendern(db, o, ab=D(2026, 1, 1), bis=D(2026, 3, 31))
    assert _ohne(db, fz3) == [(D(2026, 1, 1), D(2026, 4, 30))]


def test_migration_fasst_bestehende_zusammen(db, fz_anna):
    fz, anna = fz_anna
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=D(2025, 1, 1),
                                        gueltig_bis=D(2025, 6, 30)),
                ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=D(2025, 7, 1),
                                        gueltig_bis=D(2025, 12, 31)),
                ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 1, 1),
                                        gueltig_bis=None)])
    db.flush()
    pfad = Path(__file__).parent.parent / "alembic" / "versions" / "0029_ohne_fahrer.py"
    spec = importlib.util.spec_from_file_location("migration_0029", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    modul._zusammenfassen(db.connection(), "zuordnung_fahrer_fahrzeug", "fahrer_id, fahrzeug_id")
    db.expire_all()
    assert _fahrer(db, fz) == [(anna.id, D(2025, 1, 1), None)]
    assert db.scalar(text("SELECT count(*) FROM zuordnung_fahrer_fahrzeug WHERE fahrzeug_id = :f"), {"f": fz.id}) == 1


def test_oberflaeche(angemeldet, db, fz_anna):
    fz, anna = fz_anna
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'id="ohne-fahrer"' in seite and "Ohne Fahrer:in eintragen" in seite  # eigenes Formular beim Fahrzeug
    assert f'action="/stammdaten/fahrzeuge/{fz.id}/ohne-fahrer"' in seite  # Formular und Knopf an der Lücke
    # Neue Zuordnung „ohne Fahrer:in“ über das Formular
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/ohne-fahrer", data={"ab": "2026-01-01", "bis": "2026-03-31",
                                                                        "bisherige_austragen": "on"})
    assert _ohne(db, fz) == [(D(2026, 1, 1), D(2026, 3, 31))]
    # Lücke danach per Knopf (Aufgaben) markieren – grenzt an und wird zusammengefasst
    assert "/ohne-fahrer" in angemeldet.get("/aufgaben").text
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/ohne-fahrer", data={"ab": "2026-04-01", "bis": "",
                                                                        "zurueck": "/aufgaben"})
    assert _ohne(db, fz) == [(D(2026, 1, 1), None)]
    o = db.scalar(select(FahrzeugOhneFahrer).where(FahrzeugOhneFahrer.fahrzeug_id == fz.id))
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert f"/stammdaten/zuordnungen/ohne/{o.id}" in seite and "mini-zl-balken offen ohne" in seite
    zl = angemeldet.get("/stammdaten/zuordnungen/zeitleiste?ansicht=alles").text
    assert 'class="zl-ohne"' in zl
    assert "ohne Fahrer:in" in angemeldet.get("/stammdaten/fahrzeuge").text  # Spalte Fahrer:in heute
    # Bearbeiten und Löschen
    assert angemeldet.get(f"/stammdaten/zuordnungen/ohne/{o.id}").status_code == 200
    angemeldet.post(f"/stammdaten/zuordnungen/ohne/{o.id}", data={"ab": "2026-02-01", "bis": ""})
    assert _ohne(db, fz) == [(D(2026, 2, 1), None)]
    antwort = angemeldet.post(f"/stammdaten/zuordnungen/ohne/{o.id}", data={"ab": "2026-02-01", "bis": "2026-01-01"})
    assert antwort.status_code == 422
    angemeldet.post(f"/stammdaten/zuordnungen/ohne/{o.id}/loeschen")
    assert _ohne(db, fz) == []


def test_formular_fehler_behaelt_eingaben(angemeldet, db, fz_anna):
    """Fehler im Formular der Fahrzeugseite: 422 mit Meldung im Kasten und den Eingaben; vom Knopf an einer Lücke
    (mit `zurueck`) bleibt es bei Meldung und Weiterleitung."""
    fz, _ = fz_anna
    db.commit()  # der Fehlerpfad macht rollback() – nur geflushte Testdaten wären danach weg
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/ohne-fahrer", data={"ab": "", "bis": "2026-12-24"})
    assert antwort.status_code == 422
    kasten = antwort.text.split("<strong>Ohne Fahrer:in</strong>")[1].split("</article>")[0]
    assert "Bitte ein Beginndatum angeben." in kasten and 'name="bis" value="2026-12-24"' in kasten
    haken = kasten.split('name="bisherige_austragen"')[1].split(">")[0]
    assert "checked" not in haken  # nicht angehakt gesendet → bleibt leer (Standard sonst: angehakt)
    assert "checked" in angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text.split('name="bisherige_austragen"')[1].split(">")[0]
    weiter = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/ohne-fahrer", data={"ab": "", "zurueck": "/aufgaben"})
    assert weiter.status_code == 200 and str(weiter.url).endswith("/aufgaben")
