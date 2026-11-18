"""Ladevorgangsliste: Mehrfachauswahl in den Filtern und Person beim Laden."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)


def test_mehrfachauswahl_und_person(angemeldet, db):
    dcs = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="m.pdf", datei_pfad="m.pdf", datei_hash="m" * 64, msp_id=dcs.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    anna, bernd, clara = Fahrer(name="Anna Test"), Fahrer(name="Bernd Test"), Fahrer(name="Clara Test")
    fz_eins, fz_pool = Fahrzeug(kennzeichen="LV 1"), Fahrzeug(kennzeichen="LV POOL")
    k_person = Ladekarte(msp_id=dcs.id, kennung="P1", kennung_typ="vertragsnummer")
    k_fahrzeug = Ladekarte(msp_id=dcs.id, kennung="F1", kennung_typ="vertragsnummer")
    k_gast = Ladekarte(msp_id=we.id, kennung="G1", kennung_typ="kartennummer", ist_gastkarte=True)
    db.add_all([imp, anna, bernd, clara, fz_eins, fz_pool, k_person, k_fahrzeug, k_gast])
    db.flush()
    db.add_all([
        ZuordnungFahrerFahrzeug(fahrer_id=bernd.id, fahrzeug_id=fz_pool.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=clara.id, fahrzeug_id=fz_pool.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz_eins.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungKarte(ladekarte_id=k_person.id, fahrer_id=bernd.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungKarte(ladekarte_id=k_fahrzeug.id, fahrzeug_id=fz_pool.id, gueltig_ab=date(2026, 1, 1)),
    ])

    def lv(tag, msp, karte, fz):
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, tag, 8),
                           ende=datetime(2026, 9, tag, 9), kwh=Decimal(10), kosten_netto=Decimal(5),
                           ladeart=Ladeart.oeffentlich, ladekarte_id=karte.id if karte else None,
                           fahrzeug_id=fz.id if fz else None, zuordnung_quelle=ZuordnungQuelle.automatisch))
    lv(1, dcs, k_person, fz_pool)     # personengebundene Karte am Poolfahrzeug → Bernd
    lv(2, dcs, k_fahrzeug, fz_pool)   # Fahrzeugkarte am Poolfahrzeug → Pool
    lv(3, we, k_gast, None)           # Gäste
    lv(4, we, None, fz_eins)          # Fahrer:in des Fahrzeugs → Anna
    lv(5, we, None, None)             # unbekannt
    db.flush()

    basis = "/ladevorgaenge?von=2026-09-01&bis=2026-09-30"
    text = angemeldet.get(basis).text
    assert "<strong>5</strong> Vorgänge" in text
    assert f'<a href="/stammdaten/fahrer/{bernd.id}">Bernd Test</a>' in text
    assert f'<a href="/stammdaten/fahrer/{anna.id}">Anna Test</a>' in text
    assert 'personengebundene Karte: Bernd Test, Clara Test">Pool</abbr>' in text and "<td>Gäste</td>" in text

    # Mehrfachauswahl: zwei MSP = alle; Fahrzeug „ohne“ + eines
    assert "<strong>5</strong> Vorgänge" in angemeldet.get(f"{basis}&msp_id={dcs.id}&msp_id={we.id}").text
    text = angemeldet.get(f"{basis}&fahrzeug_id=ohne&fahrzeug_id={fz_eins.id}").text
    assert "<strong>3</strong> Vorgänge" in text and "ohne Fahrzeug, LV 1</span></summary>" in text
    assert "<strong>2</strong> Vorgänge" in angemeldet.get(f"{basis}&msp_id={dcs.id}").text

    # Filter Fahrer:in – gleiche Regel wie die Spalte Person; Pool zählt bei jeder möglichen Person
    def anzahl(parameter):
        return int(re.search(r"<strong>(\d+)</strong> Vorgänge", angemeldet.get(f"{basis}&{parameter}").text).group(1))
    assert anzahl(f"fahrer={bernd.id}") == 2      # personengebundene Karte + Pool
    assert anzahl(f"fahrer={clara.id}") == 1      # nur Pool (die personengebundene Karte gehört Bernd)
    assert anzahl(f"fahrer={anna.id}") == 1       # Fahrer:in des Fahrzeugs
    assert anzahl("fahrer=gaeste") == 1 and anzahl("fahrer=ohne") == 1
    assert anzahl(f"fahrer={anna.id}&fahrer=gaeste") == 2
    assert 'name="fahrer" value="gaeste"' in angemeldet.get(basis).text

    # Freie Suche über alle Felder; mehrere Wörter: alle müssen vorkommen
    assert anzahl("suche=bernd") == 2               # Person beim Laden (personengebundene Karte + Pool)
    assert anzahl("suche=Wien+Energie") == 3        # MSP-Name, zwei Wörter
    assert anzahl("suche=p1") == 1                  # Kennung der Ladekarte aus den Stammdaten
    assert anzahl("suche=pool+bernd") == 2          # Kennzeichen „LV POOL“ und Person
    assert anzahl("suche=pool+anna") == 0
    assert anzahl("suche=100%25") == 0              # Sonderzeichen werden nicht als Platzhalter verstanden

    # Export mit Spalte Person
    csv = angemeldet.get(f"/ladevorgaenge/export.csv?von=2026-09-01&bis=2026-09-30&msp_id={dcs.id}").text
    assert "Person" in csv and "Bernd Test" in csv and "Pool: Bernd Test, Clara Test" in csv

    # Detailseite
    v = db.scalar(select(Ladevorgang).where(Ladevorgang.ladekarte_id == k_person.id))
    assert "Bernd Test</a>" in angemeldet.get(f"/ladevorgaenge/{v.id}").text


def test_liste_zeigt_nur_die_neuesten_50(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="z.pdf", datei_pfad="z.pdf", datei_hash="z" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    for i in range(55):
        start = datetime(2026, 8, 1, 8) + timedelta(hours=i)
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=start, ende=start + timedelta(hours=1),
                           kwh=Decimal(10), kosten_netto=Decimal(5), ladeart=Ladeart.oeffentlich,
                           zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    text = angemeldet.get("/ladevorgaenge?von=2026-08-01&bis=2026-08-31").text
    assert "<strong>55</strong> Vorgänge" in text
    assert "Es werden nur die neuesten 50 von 55 Vorgängen angezeigt – bitte Filter eingrenzen." in text
    assert len(re.findall(r'<a href="/ladevorgaenge/\d+"', text)) == 50
    assert len(angemeldet.get("/ladevorgaenge/export.csv?von=2026-08-01&bis=2026-08-31").text.strip().splitlines()) == 56


def test_filter_stromart(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="s.pdf", datei_pfad="s.pdf", datei_hash="s" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    for tag, art in enumerate(["AC", "DC", "DC", None], start=1):
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 7, tag, 8),
                           ende=datetime(2026, 7, tag, 9), kwh=Decimal(10), kosten_netto=Decimal(5), stromart=art,
                           ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    basis = "/ladevorgaenge?von=2026-07-01&bis=2026-07-31"
    text = angemeldet.get(f"{basis}&stromart=DC").text
    assert "<strong>2</strong> Vorgänge" in text and 'name="stromart" value="DC" checked' in text
    assert "<strong>2</strong> Vorgänge" in angemeldet.get(f"{basis}&stromart=AC&stromart=-").text  # AC oder keine Angabe
    assert "<strong>4</strong> Vorgänge" in angemeldet.get(f"{basis}&stromart=unsinn").text  # ungültig = alle
    csv = angemeldet.get("/ladevorgaenge/export.csv?von=2026-07-01&bis=2026-07-31&stromart=DC").text
    assert len(csv.strip().splitlines()) == 1 + 2


def test_gesetzte_filter_hervorgehoben(angemeldet, db):
    """Gesetzte Filter-Dropdowns sind markiert und haben ein „×“ zum Aufheben; nicht gesetzte nicht."""
    msp_id = db.scalar(select(Msp.id).where(Msp.parser_key == "dcs"))
    text = angemeldet.get(f"/ladevorgaenge?msp_id={msp_id}").text
    feld = text.split('id="mf-msp_id"')[0].rsplit("<div", 1)[1]
    assert 'class="mehrfach-feld gesetzt"' in feld
    assert 'data-filter-weg' in text.split('id="mf-msp_id"')[1].split("</span>")[0]
    assert text.count('class="filter-weg"') == 1  # nur der gesetzte Filter
    assert 'class="mehrfach-feld gesetzt"' not in angemeldet.get("/ladevorgaenge").text
