"""Zahl der SQL-Abfragen wächst nicht mit der Zahl der Ladevorgänge (kein N+1): Aufgaben, Report „Offene Aufgaben“
und die Vollständigkeitsprüfung (Report „Zusammenfassung“) laden die Zuordnungen einmal vor."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.models import Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp, ZuordnungQuelle
from app.services import app_einstellungen, offen, vollstaendigkeit
from app.services.auswertung import zeitraum


@contextmanager
def abfragen_zaehlen(db):
    liste = []
    zaehlen = lambda *a: liste.append(1)  # noqa: E731
    event.listen(db.connection(), "before_cursor_execute", zaehlen)
    try:
        yield liste
    finally:
        event.remove(db.connection(), "before_cursor_execute", zaehlen)


@pytest.fixture
def karte_ohne_zuordnung(db):
    """Karte ohne Zuordnung, dazu eine Funktion, die Ladevorgänge an weiteren Tagen anlegt (ohne Fahrzeug)."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="n.pdf", datei_pfad="n.pdf", datei_hash="n" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    karte = Ladekarte(kennung="AT-TST-N1", msp_id=msp.id, kennung_typ=KennungTyp.kartennummer)
    db.add_all([imp, karte])
    db.flush()
    tage = iter(range(60))

    def vorgaenge(n):
        for _ in range(n):
            t = datetime(2026, 9, 1, 8) + timedelta(days=next(tage) % 30, hours=next(tage) % 5)
            db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, start=t, ende=t + timedelta(hours=1),
                               kwh=Decimal(10), kosten_netto=Decimal(5), ladeart=Ladeart.oeffentlich, land="AT",
                               zuordnung_quelle=ZuordnungQuelle.keine))
        db.commit()
    return vorgaenge


def test_aufgaben_ohne_n_plus_1(angemeldet, db, karte_ohne_zuordnung):
    karte_ohne_zuordnung(3)
    angemeldet.get("/aufgaben")  # einmal vorab (Einstellungen, Caches)
    with abfragen_zaehlen(db) as wenige:
        assert angemeldet.get("/aufgaben").status_code == 200
    karte_ohne_zuordnung(15)
    with abfragen_zaehlen(db) as viele:
        angemeldet.get("/aufgaben")
    assert len(viele) == len(wenige)
    with abfragen_zaehlen(db) as report_wenige:
        offen.fuer_report(db)
    karte_ohne_zuordnung(10)
    with abfragen_zaehlen(db) as report_viele:
        offen.fuer_report(db)
    assert len(report_viele) == len(report_wenige)


def test_vollstaendigkeit_ohne_n_plus_1(db, karte_ohne_zuordnung):
    einst = app_einstellungen.laden(db)
    z = zeitraum("monat", 2026, 9)
    karte_ohne_zuordnung(3)
    with abfragen_zaehlen(db) as wenige:
        vollstaendigkeit.pruefen(db, z, einst, heute=date(2026, 11, 26))
    karte_ohne_zuordnung(15)
    with abfragen_zaehlen(db) as viele:
        vollstaendigkeit.pruefen(db, z, einst, heute=date(2026, 11, 26))
    assert len(viele) == len(wenige)


def test_aufgaben_unplausibel_begrenzt(angemeldet, db, karte_ohne_zuordnung, monkeypatch):
    """Die Liste der unplausiblen Vorgänge hat wie die übrigen eine Obergrenze; die Kachel zählt alle."""
    from app.routers import zu_klaeren
    karte_ohne_zuordnung(3)
    for v in db.scalars(select(Ladevorgang).where(Ladevorgang.import_id.isnot(None))):
        v.stromart, v.kwh, v.kwh_geschaetzt = "AC", Decimal(40), False  # 40 kW in einer Stunde: AC unplausibel
    db.commit()
    monkeypatch.setattr(zu_klaeren, "MAX_ZEILEN", 2)
    seite = angemeldet.get("/aufgaben").text
    tabelle = seite.split('id="unplausibel"')[1].split("</table>")[0]
    assert tabelle.count("<tr>") == 1 + 2  # Kopf und zwei Vorgänge
    assert "Nur die neuesten 2 von 3 angezeigt" in seite
