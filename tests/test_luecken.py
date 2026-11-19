"""Lücken in der Fahrer:innen-Zuordnung, Reihenfolge auf der Fahrzeugseite, „Gültig ab = Leasingbeginn“."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungFahrerFahrzeug,
                        ZuordnungQuelle)
from app.services import luecken
from app.services.luecken import _zeitraeume

D = date


def test_zeitraeume():
    assert _zeitraeume(D(2026, 1, 1), D(2026, 1, 31), [(D(2026, 1, 1), D(2026, 1, 10)), (D(2026, 1, 15), None)]) == [
        (D(2026, 1, 11), D(2026, 1, 14))]
    # Pool: überlappende Zuordnungen decken gemeinsam ab; offenes Ende am Schluss
    assert _zeitraeume(D(2026, 1, 1), D(2026, 1, 31), [(D(2026, 1, 1), D(2026, 1, 20)), (D(2026, 1, 5), D(2026, 1, 25))]) \
        == [(D(2026, 1, 26), D(2026, 1, 31))]
    assert _zeitraeume(D(2026, 1, 1), D(2026, 1, 31), []) == [(D(2026, 1, 1), D(2026, 1, 31))]
    assert _zeitraeume(D(2026, 1, 1), D(2026, 1, 31), [(D(2025, 12, 1), None)]) == []


def test_luecken_und_aufgaben(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="l.pdf", datei_pfad="l.pdf", datei_hash="l" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    fz = Fahrzeug(kennzeichen="LU-1", leasing_beginn=D(2026, 1, 1))
    ohne = Fahrzeug(kennzeichen="LU-2")  # weder Leasing noch Zuordnung: nicht geprüft
    anna, bernd = Fahrer(name="Anna Lücke"), Fahrer(name="Bernd Lücke")
    db.add_all([imp, fz, ohne, anna, bernd])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 2, 1),
                                        gueltig_bis=D(2026, 5, 31)),
                ZuordnungFahrerFahrzeug(fahrer_id=bernd.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 7, 1),
                                        gueltig_bis=D(2026, 8, 31)),
                Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 6, 10, 8), ende=datetime(2026, 6, 10, 9),
                            kwh=Decimal(10), kosten_netto=Decimal(5), ladeart=Ladeart.oeffentlich, fahrzeug_id=fz.id,
                            zuordnung_quelle=ZuordnungQuelle.automatisch)])
    db.flush()
    gefunden = [(l.von, l.bis, l.offen, l.ladevorgaenge) for l in luecken.ermitteln(db, D(2026, 10, 15))
                if l.fahrzeug.id == fz.id]
    assert gefunden == [(D(2026, 1, 1), D(2026, 1, 31), False, 0), (D(2026, 6, 1), D(2026, 6, 30), False, 1),
                        (D(2026, 9, 1), D(2026, 10, 15), True, 0)]
    assert not [l for l in luecken.ermitteln(db, D(2026, 10, 15)) if l.fahrzeug.id == ohne.id]

    seite = angemeldet.get("/aufgaben").text
    assert "Lücken in der Fahrer:innen-Zuordnung" in seite and ">LU-1</a>" in seite
    assert "01.06.2026 – 30.06.2026 ohne Fahrer:in" in seite and "1 Ladevorgang</a> ohne Fahrer:in" in seite

    # Fahrzeugseite: Fahrer:innen von alt nach neu, „Gültig ab = Leasingbeginn“
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert seite.index("Anna Lücke") < seite.index("Bernd Lücke")
    assert 'data-leasing=\'{"*": ["2026-01-01", null]}\'' in seite and 'data-leasing-uebernehmen="beginn"' in seite
    # Fahrer:in-Seite: Leasingbeginn je Fahrzeug für den Knopf
    seite = angemeldet.get(f"/stammdaten/fahrer/{anna.id}").text
    assert f'"{fz.id}": ["2026-01-01", null]' in seite and 'data-leasing-feld="fahrzeug_id"' in seite



def test_ladekarte_zuordnung_leasing_und_vorauswahl(angemeldet, db):
    from app.models import KennungTyp, Ladekarte, Msp
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    fz = Fahrzeug(kennzeichen="LK-1", leasing_beginn=D(2025, 3, 1), leasing_ende=D(2028, 2, 29))
    k = Ladekarte(msp_id=msp.id, kennung="LK-K", kennung_typ=KennungTyp.kartennummer)
    db.add_all([fz, k])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/ladekarten/{k.id}").text
    assert f'"fahrzeug:{fz.id}": ["2025-03-01", "2028-02-29"]' in seite and 'data-leasing-feld="ziel"' in seite
    assert 'data-leasing-uebernehmen="ende"' in seite
    assert 'name="neu_zuordnen" checked' in seite  # Vorauswahl: bereits importierte neu zuordnen


def test_luecken_bis_leasing_ende_auch_kuenftig(db):
    """Mit Leasing-Ende wird der ganze Leasingzeitraum geprüft – auch künftige Zeiträume ohne Fahrer:in."""
    from app.services import mini_zeitleiste
    tag = D(2026, 11, 19)
    fz = Fahrzeug(kennzeichen="LU-K1", leasing_beginn=D(2026, 1, 1), leasing_ende=D(2028, 12, 31))
    ohne_ende = Fahrzeug(kennzeichen="LU-K2", leasing_beginn=D(2026, 1, 1))
    anna, bernd = Fahrer(name="Anna Künftig"), Fahrer(name="Bernd Künftig")
    db.add_all([fz, ohne_ende, anna, bernd])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 1, 1),
                                        gueltig_bis=D(2026, 12, 31)),
                ZuordnungFahrerFahrzeug(fahrer_id=bernd.id, fahrzeug_id=ohne_ende.id, gueltig_ab=D(2026, 1, 1),
                                        gueltig_bis=D(2026, 6, 30))])
    db.flush()
    alle = {l.fahrzeug.kennzeichen: l for l in luecken.ermitteln(db, tag)}
    k = alle["LU-K1"]
    assert (k.von, k.bis, k.offen, k.kuenftig, k.endlos) == (D(2027, 1, 1), D(2028, 12, 31), False, True, False)
    e = alle["LU-K2"]  # ohne Leasing-Ende: bis heute, offen und ohne bekanntes Ende
    assert (e.von, e.bis, e.offen, e.endlos) == (D(2026, 7, 1), tag, True, True)
    assert e.text == "seit 01.07.2026 ohne Fahrer:in"
    # Aktuelle Lücke mit bekanntem Ende (Leasing-Ende)
    db.get(ZuordnungFahrerFahrzeug, db.scalar(select(ZuordnungFahrerFahrzeug.id).where(
        ZuordnungFahrerFahrzeug.fahrzeug_id == fz.id))).gueltig_bis = D(2026, 6, 30)
    db.flush()
    k = {l.fahrzeug.kennzeichen: l for l in luecken.ermitteln(db, tag)}["LU-K1"]
    assert k.offen and not k.endlos and k.text == "seit 01.07.2026 ohne Fahrer:in (bis 31.12.2028)"
    # Mini-Zeitleiste der Fahrzeugseite: Lücke bis zum Leasing-Ende, nicht „bis heute“
    mini = mini_zeitleiste.berechnen([mini_zeitleiste.Eintrag(D(2026, 1, 1), D(2026, 12, 31), "Anna")], tag,
                                     D(2026, 1, 1), D(2028, 12, 31), "warnung")
    assert [(l.von, l.bis) for l in mini.luecken] == [(D(2027, 1, 1), D(2028, 12, 31))]
    mini = mini_zeitleiste.berechnen([mini_zeitleiste.Eintrag(D(2026, 1, 1), D(2026, 6, 30), "Bernd")], tag,
                                     D(2026, 1, 1), None, "warnung")
    assert [(l.von, l.bis) for l in mini.luecken] == [(D(2026, 7, 1), None)]  # None = bis heute
