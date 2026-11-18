"""Pro CPO nach Fahrer:in und Ladekarte: nur öffentliche Ladungen, Person beim Laden, Kartenvergleich; Seite und Vorschau."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services import app_einstellungen
from app.services.auswertung import auswerten, zeitraum

D = Decimal
SEP = "art=monat&jahr=2026&nr=9"


@pytest.fixture
def daten(db):
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    dcs = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    st = db.scalar(select(Msp).where(Msp.parser_key == "hastobe_csv"))
    imp = {m: Import(dateiname=f"{n}.pdf", datei_pfad=f"{n}.pdf", datei_hash=n * 64, msp_id=m.id, ladeart=art,
                     status=ImportStatus.abgeschlossen)
           for m, n, art in ((we, "a", Ladeart.oeffentlich), (dcs, "b", Ladeart.oeffentlich), (st, "c", Ladeart.privat))}
    fz, pool = Fahrzeug(kennzeichen="CPO-1"), Fahrzeug(kennzeichen="CPO-POOL")
    anna, bernd, cora = Fahrer(name="Anna CPO"), Fahrer(name="Bernd CPO"), Fahrer(name="Cora CPO")
    k_person = Ladekarte(msp=dcs, kennung="CPO-DCS", kennung_typ=KennungTyp.kartennummer)  # Annas eigene Karte
    k_fz = Ladekarte(msp=we, kennung="CPO-WE", kennung_typ=KennungTyp.kartennummer)  # Karte am Fahrzeug
    k_pool = Ladekarte(msp=we, kennung="CPO-WE-POOL", kennung_typ=KennungTyp.kartennummer)
    db.add_all([*imp.values(), fz, pool, anna, bernd, cora, k_person, k_fz, k_pool])
    db.flush()
    ab = date(2026, 1, 1)
    db.add_all([ZuordnungKarte(ladekarte_id=k_person.id, fahrer_id=anna.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k_fz.id, fahrzeug_id=fz.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k_pool.id, fahrzeug_id=pool.id, gueltig_ab=ab),
                ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=ab),
                ZuordnungFahrerFahrzeug(fahrer_id=bernd.id, fahrzeug_id=pool.id, gueltig_ab=ab),
                ZuordnungFahrerFahrzeug(fahrer_id=cora.id, fahrzeug_id=pool.id, gueltig_ab=ab)])

    def lv(msp, tag, kwh, kosten, karte, fahrzeug, cpo):
        db.add(Ladevorgang(import_id=imp[msp].id, msp_id=msp.id, ladekarte_id=karte.id if karte else None,
                           start=datetime(2026, 9, tag, 10), ende=datetime(2026, 9, tag, 11), kwh=D(kwh),
                           kosten_netto=D(kosten), ladeart=imp[msp].ladeart, betreiber_id=cpo,
                           fahrzeug_id=fahrzeug.id if fahrzeug else None,
                           zuordnung_quelle=ZuordnungQuelle.automatisch if fahrzeug else ZuordnungQuelle.keine))

    lv(dcs, 2, "10", "7.00", k_person, fz, "AT*TST")  # Anna mit eigener Karte: 0,70 €/kWh
    lv(we, 3, "10", "4.00", k_fz, fz, "AT*TST")  # Anna mit Fahrzeugkarte: 0,40 €/kWh
    lv(we, 4, "5", "2.00", k_pool, pool, "AT*TST")  # Poolfahrzeug: Person nicht eindeutig
    lv(st, 5, "20", "6.00", None, fz, "AT*TST")  # privat: zählt nicht
    lv(we, 6, "8", "3.20", k_fz, fz, "AT*XYZ")  # anderer CPO
    lv(we, 7, "4", "2.00", k_fz, fz, None)  # ohne CPO
    db.flush()
    return {"anna": anna, "k_person": k_person, "k_fz": k_fz}


def test_aufschluesselung(db, daten):
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    d = a.cpo_personen["AT*TST"]
    assert [(p.name, p.art, p.summe.kosten) for p in d.personen] == [
        ("Anna CPO", "person", D("11.00")), ("Poolfahrzeug (Person nicht eindeutig)", "pool", D("2.00"))]
    assert d.summe.kosten == D("13.00") and d.summe.kwh == D(25)  # privat nicht enthalten
    anna = d.nur_personen[0]
    assert [(k.kennung, s.kosten) for k, s in anna.karten_liste] == [("CPO-DCS", D("7.00")), ("CPO-WE", D("4.00"))]
    assert [(n, s.preis) for n, s in d.msp] == [("Wien Energie", D("0.4")), ("DCS / BMW Charging", D("0.7"))]
    assert [p.name for p in a.cpo_personen["AT*XYZ"].personen] == ["Anna CPO"]
    assert a.cpo_personen[None].summe.kosten == D("2.00")


def test_detailseite_und_export(angemeldet, daten):
    seite = angemeldet.get(f"/auswertungen/cpo/AT*TST?{SEP}").text
    assert "<code>AT*TST</code>" in seite and "Anna CPO" in seite and "Poolfahrzeug" in seite
    assert ">persönlich</a>" in seite and "teurer" not in seite  # Annas eigene Karte, aus ihrer Sicht
    # unter 20 kWh je Stromart kein Maßstab (tests/test_sparpotenzial.py): kein „günstigster“, sondern „wenig Daten“
    assert "günstigster</span>" not in seite and "wenig Daten" in seite and f"ladekarte_id={daten['k_fz'].id}" in seite
    assert f"betreiber=AT%2ATST&amp;ladeart=oeffentlich&amp;art=monat&amp;jahr=2026&amp;nr=9&amp;fahrer={daten['anna'].id}" in seite
    leer = angemeldet.get(f"/auswertungen/cpo/-?{SEP}")
    assert leer.status_code == 200 and "unbekannt" in leer.text
    csv = angemeldet.get(f"/auswertungen/export/cpo_personen.csv?{SEP}&cpo=AT*TST").content.decode("utf-8-sig")
    zeilen = csv.splitlines()
    assert zeilen[0].startswith("Fahrer:in;Ladekarte") and "Anna CPO;CPO-DCS;" in zeilen[1] and "7,00" in zeilen[1]


def test_vorschau_in_uebersicht(angemeldet, daten):
    seite = angemeldet.get(f"/auswertungen?{SEP}").text
    teil = seite.split('id="cpo"')[1].split("</section>")[0]
    assert 'href="/auswertungen/cpo/AT*TST?' in teil and "data-aufklappen" in teil
    assert 'class="cpo-vorschau" hidden' in teil and "Anna CPO" in teil and 'class="karten-chip"' in teil
    assert '>0,55 €/kWh</strong>' in teil and 'role="button" class="outline klein">Details zu AT*TST</a>' in teil
    assert 'href="/auswertungen/cpo/-?' in teil  # unbekannter CPO


def test_kwh_preis_spalten(angemeldet, daten):
    """Ø €/kWh in der Ladevorgangsliste und in der aufklappbaren Tabelle am Dashboard."""
    liste = angemeldet.get("/ladevorgaenge?von=2026-09-01&bis=2026-09-30&betreiber=AT*TST&ladeart=oeffentlich").text
    assert 'title="Nach Preis je kWh sortieren (Vorgänge ohne kWh am Ende)">€/kWh<' in liste
    assert '<td class="rechts">0,70</td>' in liste and '<td class="rechts">0,40</td>' in liste
    dashboard = angemeldet.get("/?art=monat&jahr=2026&nr=9").text
    tabelle = dashboard.split('class="diagramm-tabelle"')[1].split("</details>")[0]
    assert "Ø €/kWh<span class=\"sortpfeil\"" in tabelle and "Anna CPO" in tabelle and ">0,43</td>" in tabelle  # alle Ladungen: 22,20 € / 52 kWh


def test_sortierung_ladevorgaenge(angemeldet, daten):
    """Liste serverseitig nach Ø €/kWh (auch kWh, Kosten, Datum) sortierbar; Kopf kehrt die Richtung um."""
    basis = "/ladevorgaenge?von=2026-09-01&bis=2026-09-30"
    ab = angemeldet.get(basis + "&sortierung=preis-ab").text
    auf = angemeldet.get(basis + "&sortierung=preis-auf").text
    assert 'aria-sort="descending"' in ab and 'aria-sort="ascending"' in auf
    assert "sortierung=preis-auf" in ab  # Klick kehrt um
    assert ab.index(">0,70</td>") < ab.index(">0,40</td>") and auf.index(">0,30</td>") < auf.index(">0,70</td>")
    assert '<input type="hidden" name="sortierung" value="preis-ab">' in ab
    assert "sortierung=preis-ab" in ab.split('href="/ladevorgaenge/export.csv')[1].split('"')[0]
    standard = angemeldet.get(basis).text
    assert "aria-sort" in standard and "sortierung=zeit-auf" in standard  # Standard: Datum absteigend
    assert angemeldet.get(basis + "&sortierung=unsinn").status_code == 200
    dashboard = angemeldet.get("/?art=monat&jahr=2026&nr=9").text
    assert 'data-sortierbar' in dashboard and 'data-sort="zahl"' in dashboard


def test_zeitraum_und_preisfilter_ladevorgaenge(angemeldet, daten):
    """Ladevorgänge: Zeitraumzeile wie im Dashboard (Standard „Alles“), Bereichsregler Ø €/kWh mit Histogramm."""
    alles = angemeldet.get("/ladevorgaenge").text
    assert 'class="zeitraumwahl"' in alles and 'aria-current="true">Alles</a>' in alles and 'type="date"' not in alles
    sep = angemeldet.get("/ladevorgaenge?art=monat&jahr=2026&nr=9&betreiber=AT*TST").text
    assert '<option value="9" selected>' in sep and '<input type="hidden" name="art" value="monat">' in sep
    # Zeitraum-Links und -Auswahl behalten die übrigen Filter
    assert "art=quartal&amp;jahr=2026&amp;nr=3&amp;betreiber=AT%2ATST" in sep or "art=quartal&jahr=2026&nr=3&betreiber=AT%2ATST" in sep
    assert '<input type="hidden" name="betreiber" value="AT*TST">' in sep
    okt = angemeldet.get("/ladevorgaenge?art=monat&jahr=2026&nr=10").text
    assert "Keine Ladevorgänge gefunden." in okt
    # alter Link mit von/bis: fester Zeitraum
    frei = angemeldet.get("/ladevorgaenge?von=2026-09-01&bis=2026-09-15").text
    assert "01.09.2026 – 15.09.2026" in frei
    # Preisfilter: 0,40–0,50 €/kWh → nur die Vorgänge zu 0,40 und 0,50 (nicht 0,30 / 0,70)
    teil = angemeldet.get("/ladevorgaenge?preis_min=0.40&preis_max=0.50").text
    tabelle = teil.split("<tbody>")[1].split("</tbody>")[0]
    assert ">0,40</td>" in tabelle and ">0,50</td>" in tabelle and ">0,70</td>" not in tabelle and ">0,30</td>" not in tabelle
    assert 'class="preisregler gesetzt"' in teil and "data-preis-weg" in teil
    assert '<input type="hidden" name="preis_min" value="0.40">' in teil
    # Histogramm ohne den Preisfilter: alle Preise bis 0,70 → Säulen bis 0,72
    assert 'data-max="0.72"' in teil and teil.count('data-ab="') == 36
    export = angemeldet.get("/ladevorgaenge/export.csv?preis_min=0.40&preis_max=0.50").content.decode("utf-8-sig")
    assert len(export.strip().splitlines()) == 1 + 4  # Kopf + vier Vorgänge (3 × 0,40; 0,50)


def test_histogramm_bis_hoechstpreis(db, daten):
    """Skala bis zum teuersten Vorgang; nur extreme Ausreißer (> 2 × 99. Perzentil) werden gekappt."""
    from app.routers.ladevorgaenge import preis_histogramm
    h = preis_histogramm(db, [], None, None)
    assert h["max"] == D("0.72") and not h["gekappt"]  # teuerster: 0,70
    imp = db.scalars(select(Import)).first()
    for i in range(150):  # viele normale Preise, ein extremer Ausreißer
        db.add(Ladevorgang(import_id=imp.id, msp_id=imp.msp_id, start=datetime(2026, 8, 1, 8), ende=datetime(2026, 8, 1, 9),
                           kwh=D(10), kosten_netto=D("5.00"), ladeart=Ladeart.oeffentlich,
                           zuordnung_quelle=ZuordnungQuelle.keine))
    db.add(Ladevorgang(import_id=imp.id, msp_id=imp.msp_id, start=datetime(2026, 8, 2, 8), ende=datetime(2026, 8, 2, 9),
                       kwh=D(1), kosten_netto=D("9.00"), ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine))
    db.flush()
    h = preis_histogramm(db, [], None, None)
    assert h["gekappt"] and h["max"] < D(9) and sum(n for _, _, n in h["saeulen"]) == 157


def test_flagge_in_ladevorgangsliste(angemeldet, db, daten):
    """Land als Flagge mit Ländername im Tooltip statt als Code."""
    v = db.scalars(select(Ladevorgang).where(Ladevorgang.betreiber_id == "AT*XYZ")).one()
    v.land = "AT"
    db.flush()
    liste = angemeldet.get("/ladevorgaenge").text
    assert '<span class="flagge" title="Österreich (AT)" role="img"' in liste and "🇦🇹</span>" in liste
