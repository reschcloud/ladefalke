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
