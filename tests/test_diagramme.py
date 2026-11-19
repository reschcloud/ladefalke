"""Dashboard-Diagramme: Verteilung von kWh und Kosten auf Fahrer:innen."""
from decimal import Decimal

from app.services.auswertung import zeitraum
from app.services.diagramme import ANZAHL_FARBEN, Personenzeile, Reihe, Verteilung, saeulen, torte

D = Decimal


def _verteilung(werte_je_monat, monate=("09",)):
    """werte_je_monat: je Monat {Reihenindex: (kWh, Kosten)}; Reihen A, B und „Weitere“."""
    reihen = [Reihe("A", 1, 1), Reihe("B", 2, 2), Reihe("Weitere", 0)]
    spalten = [(zeitraum("monat", 2026, int(m)), w) for m, w in zip(monate, werte_je_monat)]
    return Verteilung(reihen, spalten, [Personenzeile("A", 1, D(1), D(1), reihen[0])], torte=len(spalten) == 1)


def test_torte_anteile_und_reihenfolge():
    v = _verteilung([{0: (D(50), D(10)), 1: (D(30), D(20)), 2: (D(20), D(10))}])
    stuecke = torte(v, 0)
    assert [s.reihe.name for s in stuecke] == ["A", "B", "Weitere"]
    assert [round(s.anteil, 2) for s in stuecke] == [0.5, 0.3, 0.2]
    assert stuecke[0].pfad.startswith("M100,100 L100.00,10.00")  # Beginn bei 12 Uhr
    assert [round(s.anteil, 2) for s in torte(v, 1)] == [0.25, 0.5, 0.25]  # Kosten
    einzeln = torte(_verteilung([{0: (D(5), D(1))}]), 0)
    assert len(einzeln) == 1 and einzeln[0].pfad == ""  # 100 %: voller Kreis


def test_saeulen_gestapelt_und_achse():
    v = _verteilung([{0: (D(100), D(1)), 1: (D(50), D(1))}, {2: (D(40), D(1))}, {}], monate=("07", "08", "09"))
    g = saeulen(v, 0)
    assert [kurz for kurz, *_ in g.spalten] == ["Jul", "Aug", "Sep"]
    juli = g.spalten[0][3]
    assert [s.reihe.name for s in juli] == ["A", "B"] and juli[1].y < juli[0].y  # B über A gestapelt
    assert g.spalten[2][3] == []  # Monat ohne Daten: leere Säule
    assert [w for _, w in g.teilstriche] == [0, 50, 100, 150]  # schöne Schritte bis zum Maximum
    assert all(s.breite <= 24 for _, _, _, segmente, _ in g.spalten for s in segmente)


def test_weitere_ab_sechster_person(db):
    """Mehr Personen als Farben: die mit den meisten kWh bekommen Farben, der Rest ist „Weitere“."""
    from app.services import app_einstellungen
    from types import SimpleNamespace
    from app.services.auswertung import FahrerZeile, Summe
    from app.services.diagramme import berechnen
    from app.models import Fahrer

    personen = [FahrerZeile(Fahrer(id=900 + i, name=f"P{i}"), Summe(kwh=D(10 * (i + 1)), kosten=D(i + 1)))
                for i in range(ANZAHL_FARBEN + 2)]
    a = SimpleNamespace(zeitraum=zeitraum("monat", 2026, 9), fahrer=personen)  # Monat: keine weitere Auswertung
    v = berechnen(db, a, app_einstellungen.laden(db))
    assert [r.name for r in v.reihen] == ["P6", "P5", "P4", "P3", "P2", "Weitere"]
    assert v.reihensumme(5, 0) == D(30)  # P0 (10) + P1 (20)
    assert len(v.personen) == ANZAHL_FARBEN + 2 and v.personen[-1].reihe.name == "Weitere"
    assert v.reihen[-1].enthalten_text(0, "kWh") == "P1: 20 kWh\nP0: 10 kWh"  # Tooltip „Weitere“



def test_cpo_verteilung_alle_ladungen():
    """kWh je CPO aus der Auswertung je Monat; öffentliche und private kWh, ohne Kennung als eigene Reihe."""
    from types import SimpleNamespace
    from app.services.auswertung import Summe
    from app.services.diagramme import cpo_verteilung

    def summe(oeffentlich, privat=0):
        return Summe(kwh=D(oeffentlich + privat), kwh_oeffentlich=D(oeffentlich), kosten_oeffentlich=D(1))

    monate = [(zeitraum("monat", 2026, 8), SimpleNamespace(cpo=[("AT*MER", summe(40)), (None, summe(5, 500))])),
              (zeitraum("monat", 2026, 9), SimpleNamespace(cpo=[("DE*ION", summe(60)), ("AT*MER", summe(10))]))]
    v = cpo_verteilung(monate)
    assert [(r.name, r.betreiber) for r in v.reihen] == [("ohne Kennung", None), ("DE*ION", "DE*ION"),
                                                        ("AT*MER", "AT*MER")]
    assert v.reihensumme(0, 0) == D(505)  # Wallbox (privat, 500 kWh) zählt mit
    assert not v.torte  # zwei Monate → Säulen


def test_msp_verteilung(db):
    """kWh je MSP aus der Auswertung je Monat; Reihen verlinken über die MSP-ID."""
    from types import SimpleNamespace
    from sqlalchemy import select
    from app.models import Msp
    from app.services.auswertung import Summe
    from app.services.diagramme import msp_verteilung

    dcs = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    monate = [(zeitraum("monat", 2026, 9), SimpleNamespace(msp=[
        (dcs.name, Summe(kwh=D(40), kosten=D(20))), ("Unbekannt GmbH", Summe(kwh=D(60), kosten=D(30))),
        ("Nur Gebühren", Summe(kwh=D(0), kosten=D(5)))]))]
    v = msp_verteilung(db, monate)
    assert [(r.name, r.msp_id) for r in v.reihen] == [("Unbekannt GmbH", None), (dcs.name, dcs.id)]
    assert v.torte and v.summe(0) == D(100)


def test_weitere_tooltip_im_dashboard(angemeldet, db):
    """Dashboard: „Weitere“ in Legende und Tortenstück nennt die zusammengefassten Personen samt Wert."""
    from datetime import datetime
    from sqlalchemy import select
    from app.models import (Fahrer, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp,
                            ZuordnungKarte, ZuordnungQuelle)
    msp = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="w.pdf", datei_pfad="w.pdf", datei_hash="w" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    for i in range(ANZAHL_FARBEN + 2):  # 7 Personen: P0 und P1 (wenigste kWh) landen in „Weitere“
        f = Fahrer(name=f"Weitere-Test P{i}")
        k = Ladekarte(msp=msp, kennung=f"WT-{i}", kennung_typ=KennungTyp.kartennummer)
        db.add_all([f, k])
        db.flush()
        db.add(ZuordnungKarte(ladekarte_id=k.id, fahrer_id=f.id, gueltig_ab=datetime(2026, 1, 1).date()))
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=k.id, start=datetime(2026, 9, 5, 10),
                           ende=datetime(2026, 9, 5, 11), kwh=D(10 * (i + 1)), kosten_netto=D(i + 1),
                           ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    seite = angemeldet.get("/?art=monat&jahr=2026&nr=9").text
    assert 'class="weitere" title="Weitere-Test P1: 20 kWh\nWeitere-Test P0: 10 kWh">Weitere <small>(2)</small>' in seite
    assert "\nWeitere-Test P1: 20 kWh\nWeitere-Test P0: 10 kWh</title>" in seite  # Tortenstück kWh
    assert "\nWeitere-Test P1: 2,00 €\nWeitere-Test P0: 1,00 €</title>" in seite  # Tortenstück Kosten


def test_dc_oeffentlich_histogramme(db, angemeldet):
    """Nur öffentliche DC-Ladungen mit kWh: Dauer in 10-Minuten-Klassen (ab 90 eine Klasse), Stunde des Beginns."""
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from app.models import Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
    from app.services.diagramme import dc_oeffentlich

    msp = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    imp = Import(dateiname="h.pdf", datei_pfad="h.pdf", datei_hash="h" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()

    def lv(start, minuten, stromart="DC", ladeart=Ladeart.oeffentlich, kwh="20"):
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, roh_kartenkennung="X", start=start,
                           ende=start + timedelta(minutes=minuten), kwh=D(kwh), kosten_netto=D("5"),
                           ladeart=ladeart, stromart=stromart, zuordnung_quelle=ZuordnungQuelle.automatisch))

    tag = datetime(2031, 3, 10)
    lv(tag.replace(hour=7, minute=55), 25)   # 20–30 min, 7 Uhr
    lv(tag.replace(hour=7, minute=10), 28)   # 20–30 min, 7 Uhr
    lv(tag.replace(hour=18), 9)              # 0–10 min
    lv(tag.replace(hour=23, minute=30), 140)  # 90+, 23 Uhr (Ende am Folgetag)
    lv(tag.replace(hour=12), 30, stromart="AC")             # nicht DC
    lv(tag.replace(hour=12), 30, ladeart=Ladeart.privat)    # nicht öffentlich
    lv(tag.replace(hour=12), 30, kwh="0")                   # ohne kWh
    lv(tag, 33)  # Beginn genau 00:00:00 (Uhrzeit unbekannt, z. B. Elli): zählt für die Dauer, nicht für die Uhrzeit
    db.flush()

    d = dc_oeffentlich(db, zeitraum("monat", 2031, 3), "beginn")
    assert d.dauer.anzahl == 5 and d.stunde.anzahl == 4 and d.ohne_uhrzeit == 1
    assert d.stunde.klassen[0][2] == 0
    assert [n for *_, n in d.dauer.klassen] == [1, 0, 2, 1, 0, 0, 0, 0, 0, 1]
    assert d.dauer.klassen[-1][0] == "90+"
    assert d.stunde.klassen[7][2] == 2 and d.stunde.klassen[18][2] == 1 and d.stunde.klassen[23][2] == 1
    assert d.median_minuten == 28  # 9, 25, 28, 33, 140
    teilstriche, saeulen = d.dauer.geometrie()
    assert [w for _, w in teilstriche] == [0, 1, 2] and len(saeulen) == 10

    seite = angemeldet.get("/auswertungen?art=monat&jahr=2031&nr=3").text
    assert "Öffentliches DC-Laden" in seite and "Median 28 min · 5 Ladevorgänge" in seite
    assert "1 ohne Uhrzeit nicht enthalten" in seite
    assert "07–08 Uhr: 2 Ladevorgänge" in seite
    leer = angemeldet.get("/auswertungen?art=monat&jahr=2031&nr=4").text
    assert "keine öffentlichen DC-Ladungen" in leer


def test_monatsbeschriftungen_ausgeduennt():
    """Lange Zeiträume: nicht jeder Monat beschriftet, sonst überlagern sich die Texte."""
    from app.services.diagramme import monatsbeschriftungen

    def monate(jahr, monat, anzahl):
        return [zeitraum("monat", jahr + (monat - 1 + i) // 12, (monat - 1 + i) % 12 + 1) for i in range(anzahl)]

    assert monatsbeschriftungen(monate(2026, 1, 12)) == ["Jän", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep",
                                                         "Okt", "Nov", "Dez"]
    texte = monatsbeschriftungen(monate(2025, 1, 24))
    assert [t for t in texte if t] == ["2025", "Apr", "Jul", "Okt", "2026", "Apr", "Jul", "Okt"]
    texte = monatsbeschriftungen(monate(2023, 3, 44))  # März 2023 – Oktober 2026
    assert [t for t in texte if t] == ["2023", "2024", "2025", "2026"] and texte[0] == "2023"
    assert monatsbeschriftungen(monate(2023, 10, 30))[0] == ""  # Oktober: zu nah am nächsten Januar


def test_saeulen_trefferflaeche_je_spalte():
    v = _verteilung([{0: (D(10), D(1))}] * 36, monate=[f"{m:02d}" for m in range(1, 13)] * 3)
    g = saeulen(v, 0)
    assert round(g.band * len(g.spalten) + g.links, 6) == g.breite  # Flächen überlappen nicht
