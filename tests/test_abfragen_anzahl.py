"""Tempo: Die Zahl der SQL-Abfragen wächst nicht mit der Zahl der Ladevorgänge (kein N+1) – Aufgaben, Report
„Offene Aufgaben“ und die Vollständigkeitsprüfung (Report „Zusammenfassung“) laden die Zuordnungen einmal vor. Dazu
Menüzahlen, Teil-Antwort der Ladevorgangsliste, Cache-Kopf statischer Dateien und Index auf dem Ladebeginn."""
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


def test_menue_zahlen_gemerkt_bis_zur_aenderung(angemeldet, db, karte_ohne_zuordnung, monkeypatch):
    """Die Zahlen im Menü werden nicht bei jeder Anfrage neu gezählt, nach einer gespeicherten Änderung aber schon."""
    monkeypatch.setattr(offen, "MENUE_GUELTIG", 60)  # in den übrigen Tests aus (conftest)
    angemeldet.get("/stammdaten/msp")
    with abfragen_zaehlen(db) as erneut:
        angemeldet.get("/stammdaten/msp")
    with abfragen_zaehlen(db) as frisch:
        offen.zaehlen(db), offen.bestand(db)
    assert len(erneut) < 10 and len(frisch) > 5  # die Menüzähler kommen beim zweiten Mal aus dem Speicher
    vorher = offen.menue_zahlen(db)[0].ohne_fahrzeug
    karte_ohne_zuordnung(2)  # Commit: Datenstand ändert sich
    assert offen.menue_zahlen(db)[0].ohne_fahrzeug == vorher + 2


def test_ladevorgaenge_nur_liste(angemeldet, karte_ohne_zuordnung):
    """Kartenausschnitt und Sortierkopf („X-Teil: liste“) bekommen nur die Liste samt Sortierfeld."""
    karte_ohne_zuordnung(3)
    ganz = angemeldet.get("/ladevorgaenge?sortierung=kwh-ab")
    teil = angemeldet.get("/ladevorgaenge?sortierung=kwh-ab", headers={"X-Teil": "liste"})
    assert teil.status_code == 200 and "X-Teil" in teil.headers["vary"] and "X-Teil" in ganz.headers["vary"]
    assert '<div id="liste">' in teil.text and 'id="karte"' not in teil.text and "<html" not in teil.text
    assert '<input type="hidden" id="sortierung-feld" name="sortierung" value="kwh-ab">' in teil.text
    assert len(teil.text) * 3 < len(ganz.text)
    liste = ganz.text.split('<div id="liste">')[1].split("</div>{# /liste")[0][:2000]
    assert liste.split("<tbody>")[0] in teil.text  # gleicher Kopf wie in der ganzen Seite


def test_statische_dateien_mit_version_dauerhaft(client):
    mit = client.get("/static/app.css?v=1")
    ohne = client.get("/static/app.css")
    assert mit.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "immutable" not in ohne.headers.get("cache-control", "")


def test_index_auf_ladebeginn(db):
    from sqlalchemy import inspect
    assert "ix_ladevorgang_start" in {i["name"] for i in inspect(db.connection()).get_indexes("ladevorgang")}
