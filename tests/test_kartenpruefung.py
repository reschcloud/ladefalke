"""Ladekarten je Fahrzeug: nur innerhalb des Leasings zuordnen, Anzahl der Fahrzeugkarten prüfen (Hinweis, Aufgabe,
Zeitleiste der Fahrzeugseite, Report), Einstellungen."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Fahrzeug, KennungTyp, Ladekarte, Msp, ZuordnungKarte
from app.services import app_einstellungen, kartenpruefung
from app.services.zuordnung import ZuordnungsFehler, karte_zuordnen

D = date
HEUTE = D(2026, 11, 20)


@pytest.fixture
def fz_karten(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    fz = Fahrzeug(kennzeichen="KP-L1", leasing_beginn=D(2026, 1, 1), leasing_ende=D(2027, 12, 31))
    k1, k2 = (Ladekarte(msp=msp, kennung=k, kennung_typ=KennungTyp.kartennummer) for k in ("KP-1", "KP-2"))
    db.add_all([fz, k1, k2])
    db.flush()
    return fz, k1, k2


def _befunde(db, fz, tag=HEUTE, **einstellungen):
    if einstellungen:
        app_einstellungen.speichern(db, einstellungen)
    return [(b.art, b.von, b.bis, b.anzahl) for b in
            kartenpruefung.ermitteln(db, app_einstellungen.laden(db), tag, fahrzeuge=[fz])]


def test_nur_innerhalb_des_leasings(db, fz_karten):
    fz, k1, k2 = fz_karten
    with pytest.raises(ZuordnungsFehler, match="vor dem Leasingbeginn"):
        karte_zuordnen(db, k1, ab=D(2025, 12, 1), fahrzeug_id=fz.id, heute=HEUTE)
    with pytest.raises(ZuordnungsFehler, match="nach dem Leasing-Ende"):
        karte_zuordnen(db, k1, ab=D(2026, 1, 1), bis=D(2028, 1, 31), fahrzeug_id=fz.id, heute=HEUTE)
    # Offenes Ende: auf das Leasing-Ende begrenzt, mit Hinweis
    z, hinweise = karte_zuordnen(db, k1, ab=D(2026, 1, 1), fahrzeug_id=fz.id, heute=HEUTE)
    assert z.gueltig_bis == D(2027, 12, 31) and any("endet mit dem Leasing" in h for h in hinweise)
    # Personen ohne Leasing-Grenze
    from app.models import Fahrer
    p = Fahrer(name="KP Person")
    db.add(p)
    db.flush()
    z2, _ = karte_zuordnen(db, k2, ab=D(2020, 1, 1), fahrer_id=p.id, heute=HEUTE)
    assert z2.gueltig_bis is None
    # Wechsel einer offen zugeordneten Karte auf ein Fahrzeug: die bisherige Zuordnung endet wie bisher
    z3, _ = karte_zuordnen(db, k2, ab=D(2026, 6, 1), fahrzeug_id=fz.id, heute=HEUTE)
    db.refresh(z2)
    assert z2.gueltig_bis == D(2026, 5, 31) and z3.gueltig_bis == D(2027, 12, 31)


def test_anzahl_der_karten(db, fz_karten):
    fz, k1, k2 = fz_karten
    karte_zuordnen(db, k1, ab=D(2026, 3, 1), bis=D(2026, 9, 30), fahrzeug_id=fz.id, heute=HEUTE)
    karte_zuordnen(db, k2, ab=D(2026, 9, 1), fahrzeug_id=fz.id, heute=HEUTE)  # bis Leasing-Ende
    assert _befunde(db, fz) == [
        ("anzahl", D(2026, 1, 1), D(2026, 2, 28), 0),   # vor der ersten Karte
        ("anzahl", D(2026, 9, 1), D(2026, 9, 30), 2),   # zwei Karten gleichzeitig
    ]
    # Grenzen 0 bis 2: alles in Ordnung; Prüfung aus: ebenso
    assert _befunde(db, fz, karten_min="0", karten_max="2") == []
    assert _befunde(db, fz, karten_min="1", karten_max="1", feature_kartenpruefung="") == []


def test_kuenftig_und_aktuell(db, fz_karten):
    fz, k1, _ = fz_karten
    karte_zuordnen(db, k1, ab=D(2026, 1, 1), bis=D(2026, 12, 31), fahrzeug_id=fz.id, heute=HEUTE)
    [b] = kartenpruefung.ermitteln(db, app_einstellungen.laden(db), HEUTE, fahrzeuge=[fz])
    assert (b.von, b.bis, b.anzahl, b.kuenftig, b.offen) == (D(2027, 1, 1), D(2027, 12, 31), 0, True, False)
    [b] = kartenpruefung.ermitteln(db, app_einstellungen.laden(db), D(2027, 3, 1), fahrzeuge=[fz])
    assert b.offen and b.stufe == "rot" and b.text == "keine Ladekarte: seit 01.01.2027 (bis 31.12.2027)"


def test_bestehende_zuordnung_ausserhalb_des_leasings(db, fz_karten):
    """Vor der Regel angelegt bzw. Leasing danach geändert: wird zur Aufgabe."""
    fz, k1, _ = fz_karten
    db.add(ZuordnungKarte(ladekarte_id=k1.id, fahrzeug_id=fz.id, gueltig_ab=D(2025, 10, 1), gueltig_bis=None))
    db.flush()
    befunde = _befunde(db, fz)
    assert ("ausserhalb", D(2025, 10, 1), D(2025, 12, 31), None) in befunde
    assert ("ausserhalb", D(2028, 1, 1), None, None) in befunde
    assert all(b.stufe == "rot" for b in kartenpruefung.ermitteln(db, app_einstellungen.laden(db), HEUTE, [fz])
               if b.art == "ausserhalb")


def test_oberflaeche(angemeldet, db, fz_karten):
    fz, k1, k2 = fz_karten
    karte_zuordnen(db, k1, ab=D(2026, 1, 1), fahrzeug_id=fz.id, heute=HEUTE)
    karte_zuordnen(db, k2, ab=D(2026, 1, 1), bis=D(2026, 3, 31), fahrzeug_id=fz.id, heute=HEUTE)
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'class="mini-zl fzl"' in seite and ">Fahrer:in</span>" in seite and ">Ladekarten</span>" in seite
    assert 'class="mini-zl-markierung gelb"' in seite and "2 Ladekarten: 01.01.2026 – 31.03.2026" in seite
    assert 'id="ladekarten"' in seite
    aufgaben = angemeldet.get("/aufgaben").text
    assert 'id="karten-je-fahrzeug"' in aufgaben and "KP-L1" in aufgaben and "2 Ladekarten" in aufgaben
    # Zuordnung außerhalb des Leasings per Formular: abgewiesen
    antwort = angemeldet.post(f"/stammdaten/ladekarten/{k2.id}/zuordnung",
                              data={"ziel": f"fahrzeug:{fz.id}", "ab": "2025-06-01", "bis": "2025-06-30"})
    assert antwort.status_code == 422 and "vor dem Leasingbeginn" in antwort.text


def test_einstellungen(angemeldet, db):
    seite = angemeldet.get("/einstellungen").text
    assert 'name="feature_kartenpruefung"' in seite and 'name="karten_min"' in seite
    e = app_einstellungen.laden(db)
    assert (e.feature_kartenpruefung, e.karten_min, e.karten_max) == (True, 1, 1)  # Standard: genau 1 Karte
