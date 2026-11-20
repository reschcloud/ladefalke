"""Warnungen beim Fahrzeug: Fahrer:innen, Vignette, Streckenmaut FLEX außerhalb des Leasings."""
from datetime import date

from app.models import Fahrer, Fahrzeug, ZuordnungFahrerFahrzeug
from app.services import app_einstellungen, fahrzeug_hinweise, leasingpruefung

D = date


def _fahrzeug(db, **leasing):
    fz = Fahrzeug(kennzeichen=f"LP-{len(leasing)}{leasing.get('leasing_ende', '')}", aktiv=True, **leasing)
    db.add(fz)
    db.flush()
    return fz


def _zuordnen(db, fz, name, ab, bis=None):
    f = Fahrer(name=name)
    db.add(f)
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=fz.id, gueltig_ab=ab, gueltig_bis=bis))
    db.flush()


def test_fahrer_ausserhalb(db):
    e = app_einstellungen.laden(db)
    tag = D(2026, 11, 20)
    fz = _fahrzeug(db, leasing_beginn=D(2026, 1, 1), leasing_ende=D(2027, 12, 31))
    _zuordnen(db, fz, "LP Früh", D(2025, 11, 1), D(2026, 3, 31))     # beginnt vor dem Leasing
    _zuordnen(db, fz, "LP Offen", D(2026, 4, 1))                      # offen, Leasing läuft noch: normal
    w = leasingpruefung.ermitteln(db, e, [fz], tag)[fz.id]
    assert [(x.art, x.von, x.bis) for x in w] == [("fahrer", D(2025, 11, 1), D(2025, 12, 31))]
    assert w[0].text == "LP Früh außerhalb des Leasings: 01.11.2025 – 31.12.2025"
    # Nach dem Leasing-Ende ist die offene Zuordnung außerhalb; ein eingetragenes Ende danach sofort
    w = leasingpruefung.ermitteln(db, e, [fz], D(2028, 2, 1))[fz.id]
    assert ("fahrer", D(2028, 1, 1), None) in [(x.art, x.von, x.bis) for x in w]
    fz2 = _fahrzeug(db, leasing_ende=D(2026, 6, 30))
    _zuordnen(db, fz2, "LP Lang", D(2026, 1, 1), D(2026, 9, 30))
    assert [(x.von, x.bis) for x in leasingpruefung.ermitteln(db, e, [fz2], tag)[fz2.id]] == [
        (D(2026, 7, 1), D(2026, 9, 30))]
    # Ohne Leasingdaten keine Prüfung
    ohne = _fahrzeug(db)
    _zuordnen(db, ohne, "LP Ohne", D(2020, 1, 1))
    assert ohne.id not in leasingpruefung.ermitteln(db, e, [ohne], tag)


def test_vignette_und_streckenmaut(db):
    app_einstellungen.speichern(db, {"feature_vignette": "1", "feature_streckenmaut": "1"})
    e = app_einstellungen.laden(db)
    fz = _fahrzeug(db, leasing_beginn=D(2026, 1, 1), leasing_ende=D(2027, 12, 31))
    fz.vignette_ab, fz.vignette_bis = D(2027, 12, 1), D(2029, 1, 31)  # über das Ende hinaus: normal
    fz.streckenmaut_noetig, fz.streckenmaut_ab, fz.streckenmaut_bis = True, D(2025, 1, 1), D(2025, 12, 31)  # falsches Jahr
    db.flush()
    w = leasingpruefung.ermitteln(db, e, [fz])[fz.id]
    assert [x.art for x in w] == ["streckenmaut"]
    assert w[0].text == "Streckenmaut FLEX komplett außerhalb des Leasings: 01.01.2025 – 31.12.2025"
    fz.vignette_ab, fz.vignette_bis = D(2028, 12, 1), D(2030, 1, 31)  # komplett nach dem Leasing
    db.flush()
    assert {x.art for x in leasingpruefung.ermitteln(db, e, [fz])[fz.id]} == {"vignette", "streckenmaut"}
    # Feature aus: keine Prüfung
    app_einstellungen.speichern(db, {"feature_vignette": "", "feature_streckenmaut": ""})
    assert fz.id not in leasingpruefung.ermitteln(db, app_einstellungen.laden(db), [fz])


def test_hinweise_und_zeitleiste(angemeldet, db):
    fz = _fahrzeug(db, leasing_beginn=D(2026, 1, 1), leasing_ende=D(2027, 12, 31))
    _zuordnen(db, fz, "LP Früh", D(2025, 11, 1), D(2026, 3, 31))
    texte = [h.text for h in fahrzeug_hinweise.ermitteln(db, [fz], app_einstellungen.laden(db))[fz.id]]
    assert "Fahrer:in außerhalb des Leasings" in texte
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert "LP Früh außerhalb des Leasings: 01.11.2025 – 31.12.2025" in seite  # Markierung und Liste der Zeitleiste
    assert seite.count('class="mini-zl-markierung gelb"') >= 1
