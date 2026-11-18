from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (Fahrer, Fahrzeug, KennungTyp, Ladekarte, Msp, ZuordnungFahrerFahrzeug,
                        ZuordnungKarte)
from app.services.zuordnung import (ZuordnungsFehler, fahrer_fahrzeug_aendern, fahrer_fahrzeug_zuordnen,
                                    fahrer_von_fahrzeug_am, fahrzeug_von_fahrer_am, karte_zuordnen,
                                    karten_zuordnung_aendern, karten_zuordnung_am, ueberlappt)

HEUTE = date(2026, 11, 4)


@pytest.fixture
def daten(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    karte = Ladekarte(msp=msp, kennung="AT*TST*E0001*1", kennung_typ=KennungTyp.vertragsnummer)
    gast = Ladekarte(msp=msp, kennung="GAST / Test", kennung_typ=KennungTyp.kartenlabel, ist_gastkarte=True)
    fz1, fz2 = Fahrzeug(kennzeichen="S-TEST 1"), Fahrzeug(kennzeichen="S-TEST 2")
    f1, f2 = Fahrer(name="Test Eins"), Fahrer(name="Test Zwei")
    db.add_all([karte, gast, fz1, fz2, f1, f2])
    db.flush()
    return {"karte": karte, "gast": gast, "fz1": fz1, "fz2": fz2, "f1": f1, "f2": f2}


@pytest.mark.parametrize("a, b, erwartet", [
    ((date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 2, 1), None), False),
    ((date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 1, 31), None), True),  # Ende inklusive
    ((date(2026, 1, 1), None), (date(2027, 1, 1), None), True),
    ((date(2026, 3, 1), date(2026, 3, 10)), (date(2026, 1, 1), date(2026, 2, 28)), False),
])
def test_ueberlappt(a, b, erwartet):
    assert ueberlappt(*a, *b) is erwartet
    assert ueberlappt(*b, *a) is erwartet


def test_karte_an_fahrzeug_und_stichtag(db, daten):
    z, hinweise = karte_zuordnen(db, daten["karte"], ab=date(2026, 11, 4), fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    assert hinweise == []
    assert karten_zuordnung_am(db, daten["karte"].id, date(2026, 12, 31)).id == z.id
    assert karten_zuordnung_am(db, daten["karte"].id, date(2026, 11, 3)) is None


def test_vorperiode_wird_beendet(db, daten):
    alt, _ = karte_zuordnen(db, daten["karte"], ab=date(2026, 1, 1), fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    neu, hinweise = karte_zuordnen(db, daten["karte"], ab=date(2026, 11, 10), fahrer_id=daten["f1"].id,
                                   heute=HEUTE)
    assert alt.gueltig_bis == date(2026, 11, 9)
    assert neu.gueltig_bis is None
    assert any("beendet" in h for h in hinweise)
    assert karten_zuordnung_am(db, daten["karte"].id, date(2026, 11, 9)).fahrzeug_id == daten["fz1"].id
    assert karten_zuordnung_am(db, daten["karte"].id, date(2026, 11, 10)).fahrer_id == daten["f1"].id


def test_befristete_periode_in_offener_wird_abgewiesen(db, daten):
    karte_zuordnen(db, daten["karte"], ab=date(2026, 1, 1), fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    with pytest.raises(ZuordnungsFehler, match="Überschneidung"):
        karte_zuordnen(db, daten["karte"], ab=date(2026, 3, 1), bis=date(2026, 3, 31),
                       fahrzeug_id=daten["fz2"].id, heute=HEUTE)


def test_neue_periode_vor_bestehender_wird_abgewiesen(db, daten):
    karte_zuordnen(db, daten["karte"], ab=date(2026, 6, 1), fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    with pytest.raises(ZuordnungsFehler, match="Überschneidung"):
        karte_zuordnen(db, daten["karte"], ab=date(2026, 1, 1), fahrzeug_id=daten["fz2"].id, heute=HEUTE)


def test_luecke_und_anschluss_erlaubt(db, daten):
    karte_zuordnen(db, daten["karte"], ab=date(2026, 1, 1), bis=date(2026, 1, 31), fahrzeug_id=daten["fz1"].id,
                   heute=HEUTE)
    karte_zuordnen(db, daten["karte"], ab=date(2026, 2, 1), bis=date(2026, 2, 28), fahrzeug_id=daten["fz2"].id,
                   heute=HEUTE)
    karte_zuordnen(db, daten["karte"], ab=date(2026, 5, 1), fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    assert karten_zuordnung_am(db, daten["karte"].id, date(2026, 3, 15)) is None


def test_genau_ein_ziel(db, daten):
    with pytest.raises(ZuordnungsFehler, match="genau eine"):
        karte_zuordnen(db, daten["karte"], ab=HEUTE, heute=HEUTE)
    with pytest.raises(ZuordnungsFehler, match="genau eine"):
        karte_zuordnen(db, daten["karte"], ab=HEUTE, fahrer_id=daten["f1"].id, fahrzeug_id=daten["fz1"].id,
                       heute=HEUTE)


def test_ende_vor_beginn(db, daten):
    with pytest.raises(ZuordnungsFehler, match="Ende"):
        karte_zuordnen(db, daten["karte"], ab=HEUTE, bis=date(2026, 1, 1), fahrzeug_id=daten["fz1"].id,
                       heute=HEUTE)


def test_gastkarte_ohne_zuordnung(db, daten):
    with pytest.raises(ZuordnungsFehler, match="Gastkarten"):
        karte_zuordnen(db, daten["gast"], ab=HEUTE, fahrzeug_id=daten["fz1"].id, heute=HEUTE)


def test_rueckwirkend_warnt(db, daten):
    _, hinweise = karte_zuordnen(db, daten["karte"], ab=date(2026, 10, 1), fahrzeug_id=daten["fz1"].id,
                                 heute=HEUTE)
    assert any("rückwirkend" in h for h in hinweise)


def test_karten_zuordnung_aendern(db, daten):
    a, _ = karte_zuordnen(db, daten["karte"], ab=date(2026, 1, 1), bis=date(2026, 1, 31),
                          fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    b, _ = karte_zuordnen(db, daten["karte"], ab=date(2026, 2, 1), fahrzeug_id=daten["fz2"].id, heute=HEUTE)
    with pytest.raises(ZuordnungsFehler):
        karten_zuordnung_aendern(db, a, ab=date(2026, 1, 1), bis=date(2026, 2, 15), fahrer_id=None,
                                 fahrzeug_id=daten["fz1"].id, heute=HEUTE)
    karten_zuordnung_aendern(db, a, ab=date(2026, 1, 1), bis=date(2026, 1, 20), fahrer_id=daten["f2"].id,
                             fahrzeug_id=None, heute=HEUTE)
    assert a.fahrer_id == daten["f2"].id and a.fahrzeug_id is None
    assert a.ziel_name == "Fahrer:in Test Zwei"


def test_db_constraint_verhindert_ueberlappung(db, daten):
    """Auch ohne Service-Prüfung greift der EXCLUDE-Constraint."""
    db.add(ZuordnungKarte(ladekarte_id=daten["karte"].id, fahrzeug_id=daten["fz1"].id, gueltig_ab=date(2026, 1, 1)))
    db.flush()
    db.add(ZuordnungKarte(ladekarte_id=daten["karte"].id, fahrzeug_id=daten["fz2"].id, gueltig_ab=date(2026, 6, 1)))
    with pytest.raises(IntegrityError):
        db.flush()


def test_db_constraint_genau_ein_ziel(db, daten):
    db.add(ZuordnungKarte(ladekarte_id=daten["karte"].id, gueltig_ab=date(2026, 1, 1)))
    with pytest.raises(IntegrityError):
        db.flush()


# ------------------------------------------------------------------ Fahrer:in → Fahrzeug

def test_fahrer_fahrzeug_wechsel(db, daten):
    alt, _ = fahrer_fahrzeug_zuordnen(db, daten["f1"], daten["fz1"], ab=date(2026, 1, 1), heute=HEUTE)
    fahrer_fahrzeug_zuordnen(db, daten["f1"], daten["fz2"], ab=date(2026, 7, 1), heute=HEUTE)
    assert alt.gueltig_bis == date(2026, 6, 30)
    assert fahrzeug_von_fahrer_am(db, daten["f1"].id, date(2026, 6, 30)).id == daten["fz1"].id
    assert fahrzeug_von_fahrer_am(db, daten["f1"].id, date(2026, 7, 1)).id == daten["fz2"].id


def test_poolfahrzeug_mehrere_fahrer(db, daten):
    fahrer_fahrzeug_zuordnen(db, daten["f1"], daten["fz1"], ab=date(2026, 1, 1), heute=HEUTE)
    fahrer_fahrzeug_zuordnen(db, daten["f2"], daten["fz1"], ab=date(2026, 1, 1), heute=HEUTE)
    namen = [f.name for f in fahrer_von_fahrzeug_am(db, daten["fz1"].id, HEUTE)]
    assert namen == ["Test Eins", "Test Zwei"]


def test_fahrer_ueberlappung_beim_aendern(db, daten):
    a, _ = fahrer_fahrzeug_zuordnen(db, daten["f1"], daten["fz1"], ab=date(2026, 1, 1), bis=date(2026, 3, 31),
                                    heute=HEUTE)
    fahrer_fahrzeug_zuordnen(db, daten["f1"], daten["fz2"], ab=date(2026, 4, 1), heute=HEUTE)
    with pytest.raises(ZuordnungsFehler, match="Überschneidung"):
        fahrer_fahrzeug_aendern(db, a, ab=date(2026, 1, 1), bis=date(2026, 4, 1), fahrzeug_id=daten["fz1"].id,
                                heute=HEUTE)


def test_db_constraint_fahrer_ueberlappung(db, daten):
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=daten["f1"].id, fahrzeug_id=daten["fz1"].id,
                                   gueltig_ab=date(2026, 1, 1)))
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=daten["f1"].id, fahrzeug_id=daten["fz2"].id,
                                   gueltig_ab=date(2026, 2, 1)))
    with pytest.raises(IntegrityError):
        db.flush()


# ------------------------------------------------------------------ Zuordnung vom Fahrzeug aus

def _ff(db, fahrzeug_id):
    return {(z.fahrer.name, z.gueltig_ab, z.gueltig_bis) for z in db.scalars(
        select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrzeug_id == fahrzeug_id))}


def test_fahrerwechsel_vom_fahrzeug(angemeldet, db, daten):
    fz, alt, neu = daten["fz1"], daten["f1"], daten["f2"]
    fahrer_fahrzeug_zuordnen(db, alt, fz, ab=date(2026, 1, 1), heute=HEUTE)
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/fahrer", data={
        "fahrer_id": str(neu.id), "ab": "2026-11-01", "bisherige_austragen": "on"})
    assert antwort.status_code == 200
    assert "Test Zwei ist ab 01.11.2026 Fahrzeug S-TEST 1 zugeordnet" in antwort.text
    assert "Test Eins wurde zum 31.10.2026 aus dem Fahrzeug S-TEST 1 ausgetragen" in antwort.text
    assert _ff(db, fz.id) == {("Test Eins", date(2026, 1, 1), date(2026, 10, 31)), ("Test Zwei", date(2026, 11, 1), None)}


def test_poolfahrzeug_ohne_austragen(angemeldet, db, daten):
    fz, alt, neu = daten["fz1"], daten["f1"], daten["f2"]
    fahrer_fahrzeug_zuordnen(db, alt, fz, ab=date(2026, 1, 1), heute=HEUTE)
    angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/fahrer", data={"fahrer_id": str(neu.id), "ab": "2026-11-01"})
    assert _ff(db, fz.id) == {("Test Eins", date(2026, 1, 1), None), ("Test Zwei", date(2026, 11, 1), None)}
    assert [f.name for f in fahrer_von_fahrzeug_am(db, fz.id, date(2026, 11, 2))] == ["Test Eins", "Test Zwei"]


def test_austragen_nicht_moeglich(angemeldet, db, daten):
    """Eine andere Zuordnung beginnt erst am/nach dem Wechseldatum → Fehlermeldung, nichts geändert."""
    fz, alt, neu = daten["fz1"], daten["f1"], daten["f2"]
    fahrer_fahrzeug_zuordnen(db, alt, fz, ab=date(2026, 12, 1), heute=HEUTE)
    db.commit()  # wie im Betrieb bereits gespeichert (Rollback der Route betrifft nur die neue Änderung)
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{fz.id}/fahrer", data={
        "fahrer_id": str(neu.id), "ab": "2026-11-01", "bisherige_austragen": "on"})
    assert antwort.status_code == 422 and "ab 01.12.2026 zugeordnet" in antwort.text
    assert _ff(db, fz.id) == {("Test Eins", date(2026, 12, 1), None)}


def test_fahrer_fehlt(angemeldet, db, daten):
    antwort = angemeldet.post(f"/stammdaten/fahrzeuge/{daten['fz1'].id}/fahrer", data={"fahrer_id": "", "ab": "2026-11-01"})
    assert antwort.status_code == 422 and "Bitte eine Fahrer:in wählen" in antwort.text


def test_formular_auf_fahrzeugseite(angemeldet, daten):
    text = angemeldet.get(f"/stammdaten/fahrzeuge/{daten['fz1'].id}").text
    assert "Neue Fahrer:in-Zuordnung" in text and 'name="bisherige_austragen" checked' not in text
    assert 'name="neu_zuordnen" checked' in text  # Vorauswahl: neu zuordnen ja, bisherige austragen nein


def test_bearbeiten_neu_zuordnen_vorausgewaehlt(angemeldet, daten, db):
    from sqlalchemy import select
    from app.models import ZuordnungFahrerFahrzeug, ZuordnungKarte
    zf = db.scalar(select(ZuordnungFahrerFahrzeug))
    if zf:
        text = angemeldet.get(f"/stammdaten/zuordnungen/fahrer/{zf.id}").text
        assert text.split("Zuordnung löschen")[0].count('name="neu_zuordnen" checked') == 1  # nur beim Speichern
    zk = db.scalar(select(ZuordnungKarte))
    if zk:
        text = angemeldet.get(f"/stammdaten/zuordnungen/karte/{zk.id}").text
        assert text.split("Zuordnung löschen")[0].count('name="neu_zuordnen" checked') == 1
