"""Klarheit der Oberfläche: Einstellungen in Gruppen mit Standardwerten, Fristen in Listen, Detailkorrekturen."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.config import heute
from app.services import app_einstellungen
from app.models import Fahrer, Fahrzeug, Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle


def test_einstellungen_gruppen_und_standard(angemeldet):
    seite = angemeldet.get("/einstellungen").text
    for anker in ("allgemein", "datenqualitaet", "auswertungen", "fristen"):  # Import in „Allgemein“
        assert f'href="#{anker}"' in seite and f'id="{anker}"' in seite
    assert 'data-standard="12,0"' in seite and "Empfehlung: 12,0 kW" in seite and '<span class="einheit">kW</span>' in seite
    assert "Standard:" not in seite and "Auf Standard zurücksetzen" not in seite  # Empfehlung statt Standard, keine Zurücksetzen-Knöpfe
    fristen = seite.split('id="fristen"')[1].split('id="email"')[0]
    assert "Empfehlung" not in fristen and "data-standard" not in fristen  # Fristen: eigener Bedarf, keine Empfehlung
    assert 'class="einstellung geaendert"' not in seite  # alles Standard
    assert 'data-speichern class="ausgegraut" aria-disabled="true"' in seite and 'form="einstellungen-form"' in seite  # Stichtag gehört zum Formular

    angemeldet.post("/einstellungen", data={"stichtag": "ende", "topliste_mindestmenge": "20",
                                            "strompreis_vorbelegung": "0,30", "plausibel_ac_max_kw": "14"})
    seite = angemeldet.get("/einstellungen").text
    feld = seite.split('id="e-plausibel_ac_max_kw"')[0].rsplit("<div", 2)[1]
    assert 'class="einstellung geaendert"' in feld


def test_fristen_in_listen_und_status_spalte(angemeldet, db):
    app_einstellungen.speichern(db, {"feature_fuehrerschein": "1"})  # Feature einschalten (Standard: aus)
    tag = heute()
    db.add_all([Fahrzeug(kennzeichen="LI-1", leasing_ende=tag + timedelta(days=10)),
                Fahrer(name="Liste Ohne"), Fahrer(name="Liste Alt", fuehrerschein_kontrolle=tag - timedelta(days=400))])
    db.flush()
    fahrzeuge = angemeldet.get("/stammdaten/fahrzeuge").text
    assert "<th>Hinweise" in fahrzeuge and f'class="hinweis gelb" title="innerhalb der Hinweisfrist von 90 Tagen">' \
        f'Leasing endet {(tag + timedelta(days=10)).strftime("%d.%m.%Y")}</a>' in fahrzeuge
    assert "<th>Status</th>" not in fahrzeuge and "<th>Status</th>" in angemeldet.get("/stammdaten/fahrzeuge?status=alle").text
    fahrer = angemeldet.get("/stammdaten/fahrer").text
    assert "<th>Hinweise" in fahrer  # wie bei den Fahrzeugen
    assert 'class="hinweis grau" title="Datum der letzten Kontrolle eintragen">Führerscheinkontrolle fehlt</a>' in fahrer
    assert 'class="hinweis rot">Führerscheinkontrolle überfällig (' in fahrer
    assert "<th>Status</th>" not in angemeldet.get("/stammdaten/ladekarten").text


def test_detail_ohne_ladepunkt_kein_none(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="n.pdf", datei_pfad="n.pdf", datei_hash="n" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, 1, 8), ende=datetime(2026, 9, 1, 9),
                    kwh=Decimal(10), kosten_netto=Decimal(5), betreiber_id="AT*TST", betreiber_quelle="rechnung",
                    ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine)
    db.add(v)
    db.flush()
    seite = angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert "None" not in seite.split("<dt>EVSE-ID</dt>")[1].split("</dd>")[0]
    # „Fahrzeug ändern“ steht vor „Land und Ladeort“
    assert seite.index("Fahrzeug ändern") < seite.index("Land und Ladeort")


def test_aufgaben_alles_erledigt(angemeldet, db):
    for f in db.scalars(select(Fahrer)):  # Fristen außen vor lassen
        f.aktiv = False
    db.flush()
    seite = angemeldet.get("/aufgaben").text
    if 'class="kachel aufgabe offen"' not in seite:
        assert "Alles erledigt" in seite
    assert '<div class="kachel aufgabe erledigt">' in seite  # erledigte Kacheln sind keine Links


def test_maps_link_auf_detailseite(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="m.pdf", datei_pfad="m.pdf", datei_hash="m" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, 1, 8), ende=datetime(2026, 9, 1, 9),
                    kwh=Decimal(10), kosten_netto=Decimal(5), standort="Teststraße 1, 1010 Wien", land="AT",
                    ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine)
    db.add(v)
    db.flush()
    seite = angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert "https://www.google.com/maps/search/?api=1&amp;query=Teststra%C3%9Fe%201%2C%201010%20Wien%2C%20%C3%96sterreich" in seite
    assert 'target="_blank" rel="noopener noreferrer"' in seite
    assert "https://maps.apple.com/?q=Teststra%C3%9Fe%201%2C%201010%20Wien%2C%20%C3%96sterreich" in seite  # auch Apple Maps
    # kleine Kacheln: kWh, Kosten, €/kWh, Dauer, Ø kW
    kacheln = seite.split('class="kacheln kacheln-klein"')[1].split("</div>\n</div>")[0]
    for text in ("10,000", "5,00 €", "0,50", "1:00 h", "Dauer (angesteckt)", "Ø kW", "10,0"):
        assert text in kacheln, text


def test_dauer_format_und_spalte(angemeldet, db):
    from app.formatierung import dauer
    assert dauer(datetime(2026, 9, 1, 8), datetime(2026, 9, 1, 8, 45)) == "45 min"
    assert dauer(datetime(2026, 9, 1, 8), datetime(2026, 9, 1, 9, 5)) == "1:05 h"
    assert dauer(datetime(2026, 9, 1, 8), datetime(2026, 9, 3, 11)) == "2 T 3 h"
    assert dauer(datetime(2026, 9, 1, 8), None) == "–"
    test_maps_link_auf_detailseite(angemeldet, db)
    seite = angemeldet.get("/ladevorgaenge?art=monat&jahr=2026&nr=9").text
    assert "Dauer<span" in seite and 'sortierung=dauer-ab' in seite
    assert '<td class="dauer">1:00 h<br><small>ab\n  08:00</small></td>' in seite  # Ladebeginn klein darunter
    assert angemeldet.get("/ladevorgaenge?art=monat&jahr=2026&nr=9&sortierung=dauer-auf").status_code == 200


def test_originaldokument_link_auf_detailseite(angemeldet, db):
    v = db.scalar(select(Ladevorgang).order_by(Ladevorgang.id.desc())) or None
    if v is None:  # eigener Vorgang, falls die Testdaten leer sind
        test_maps_link_auf_detailseite(angemeldet, db)
        v = db.scalar(select(Ladevorgang).order_by(Ladevorgang.id.desc()))
    seite = angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert f'href="/import/{v.import_id}/datei?anzeigen=1" target="_blank"' in seite and "Originaldokument ↗</a>" in seite



def test_fahrzeug_hinweise(angemeldet, db):
    """Spalte „Hinweise“: alle Probleme je Fahrzeug, grüner Haken, wenn alles passt."""
    from datetime import date
    from app.models import ZuordnungFahrerFahrzeug
    from app.services import app_einstellungen, fahrzeug_hinweise
    tag = heute()
    ok = Fahrzeug(kennzeichen="HW-OK", leasing_beginn=date(2020, 1, 1), leasing_ende=tag + timedelta(days=900))
    leer = Fahrzeug(kennzeichen="HW-LEER")
    alt = Fahrzeug(kennzeichen="HW-ALT", leasing_beginn=date(2020, 1, 1), leasing_ende=tag - timedelta(days=1))
    f = Fahrer(name="Hinweis Test")
    db.add_all([ok, leer, alt, f])
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=ok.id, gueltig_ab=date(2020, 1, 1)))
    from app.models import KennungTyp, Ladekarte, Msp, ZuordnungKarte
    karte = Ladekarte(msp=db.scalar(select(Msp).where(Msp.parser_key == "dcs")), kennung="HW-K",
                      kennung_typ=KennungTyp.kartennummer)
    db.add(karte)
    db.flush()
    db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=ok.id, gueltig_ab=date(2020, 1, 1),
                          gueltig_bis=ok.leasing_ende))  # genau 1 Karte im ganzen Leasing
    db.flush()
    h = fahrzeug_hinweise.ermitteln(db, [ok, leer, alt], app_einstellungen.laden(db))
    assert h[ok.id] == []
    assert [x.text for x in h[leer.id]] == ["ohne Fahrer:in", "keine Leasingdaten"]  # dringende zuerst
    assert [x.stufe for x in h[alt.id]][0] == "rot" and h[alt.id][0].text.startswith("Leasing abgelaufen")
    assert any(x.text.startswith("1 Lücke") or x.text.startswith("ohne Fahrer:in") for x in h[alt.id])
    seite = angemeldet.get("/stammdaten/fahrzeuge").text
    zeile = seite.split(">HW-OK</a>")[1].split("</tr>")[0]
    assert '<span class="alles-ok" title="Keine offenen Punkte"><svg class="symbol symbol-ok"' in zeile


def test_fahrer_liste_ladekarten_msp(angemeldet, db):
    from datetime import date
    from app.models import KennungTyp, Ladekarte, ZuordnungFahrerFahrzeug, ZuordnungKarte
    dcs, enbw = (db.scalar(select(Msp).where(Msp.parser_key == k)) for k in ("dcs", "enbw"))
    f, fz = Fahrer(name="Karten Test"), Fahrzeug(kennzeichen="KT-1")
    k1 = Ladekarte(msp_id=dcs.id, kennung="KT-P", kennung_typ=KennungTyp.kartennummer)
    k2 = Ladekarte(msp_id=enbw.id, kennung="KT-F", kennung_typ=KennungTyp.kartennummer)
    db.add_all([f, fz, k1, k2])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungKarte(ladekarte_id=k1.id, fahrer_id=f.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungKarte(ladekarte_id=k2.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1))])
    db.flush()
    zeile = angemeldet.get("/stammdaten/fahrer").text.split(">Karten Test</a>")[1].split("</tr>")[0]
    assert f'<span class="msp-marke" title="persönliche Ladekarte">{dcs.name}</span>' in zeile
    assert f'<span class="msp-marke fahrzeug" title="über Fahrzeug KT-1">{enbw.name}</span>' in zeile


def test_fahrer_hinweise_haken(angemeldet, db):
    app_einstellungen.speichern(db, {"feature_fuehrerschein": "1"})  # Feature einschalten (Standard: aus)
    f = Fahrer(name="Hinweis Ok", fuehrerschein_kontrolle=heute(), email="ok@example.com")
    ohne = Fahrer(name="Hinweis Ohne Mail", fuehrerschein_kontrolle=heute())
    db.add_all([f, ohne])
    db.flush()
    seite = angemeldet.get("/stammdaten/fahrer").text
    zeile = seite.split(">Hinweis Ok</a>")[1].split("</tr>")[0]
    assert '<span class="alles-ok" title="Nächste Führerscheinkontrolle am' in zeile
    zeile = seite.split(">Hinweis Ohne Mail</a>")[1].split("</tr>")[0]
    assert '>E-Mail fehlt</a>' in zeile and "alles-ok" not in zeile


def test_export_ohne_beschriftung(angemeldet):
    """Export-Knöpfe ohne „Export:“ – Erklärung im Tooltip; bei den Ladevorgängen rechts über der Tabelle."""
    seite = angemeldet.get("/ladevorgaenge").text
    assert "Export:" not in seite and 'class="kopf tabellen-kopf"' in seite
    assert 'title="Alle Ladevorgänge der Filter als CSV exportieren' in seite
    seite = angemeldet.get("/auswertungen?art=monat&jahr=2026&nr=9").text
    assert "Export:" not in seite and 'title="Die Tabelle als Excel-Datei (XLSX) exportieren"' in seite


def test_lade_animation(angemeldet):
    """Lade-Animation (E-Auto an der Ladesäule) auf jeder Seite, gesteuert über htmx-Anfragen."""
    seite = angemeldet.get("/").text
    assert 'class="lade-anzeige" role="status"' in seite and 'class="lade-auto"' in seite and 'class="strom"' in seite
    assert 'addEventListener("htmx:beforeRequest"' in seite and 'classList.add("laedt")' in seite


def test_logo(angemeldet, client):
    """Falken-Logo in der Menüleiste, auf der Anmeldeseite und als Favicon; in der Lade-Animation wacht der Falke."""
    seite = angemeldet.get("/").text
    marke = seite.split('class="marke"')[1].split("</a>")[0]
    assert '<svg class="falke"' in marke and 'class="strom"' not in marke and "<strong>Ladefalke</strong><small>resch.cloud</small>" in marke
    animation = seite.split('class="lade-anzeige"')[1].split("</div>")[0]
    assert 'class="falke-wacht"' in animation and 'class="strom"' in animation
    assert 'rel="icon" href="/static/favicon.svg' in seite
    assert seite.count('id="falke-zeichen"') == 1 and '<use href="#falke-zeichen"/>' in marke  # Zeichnung nur einmal je Seite
    login = client.get("/login").text
    assert 'class="login-logo"><svg class="falke"' in login
    assert client.get("/static/favicon.svg").headers["content-type"].startswith("image/svg+xml")


def test_oberflaeche_ohne_anbieternamen(angemeldet):
    """Die Kern-App nennt in festen Texten keine Anbieter/Parser; die Liste der Formate kommt aus den Parsern."""
    from pathlib import Path
    from app.parsers import PARSER
    verboten = ("Wien Energie", "has.to.be", "DCS", "LINZ AG", "Salzburg AG", "Elli", "EnBW", "eSolution")
    for vorlage in Path("app/templates").rglob("*.html"):
        text = vorlage.read_text()
        assert not [w for w in verboten if w in text], vorlage
    import_seite = angemeldet.get("/import").text
    assert all(p.name in import_seite for p in PARSER.values())  # Upload-Hinweis aus den installierten Parsern
    einstellungen = angemeldet.get("/einstellungen").text
    assert "kWh-Schätzung – Ersatzwerte" in einstellungen and "Die Werte hier gelten nur" in einstellungen


def test_footer(angemeldet, client):
    """Footer auf jeder Seite: Marke, Link zu resch.cloud, Version (Commit-Stand und Build-Datum)."""
    from app.version import version
    for seite in (angemeldet.get("/").text, angemeldet.get("/einstellungen").text):
        fuss = seite.split('<footer class="fuss">')[1].split("</footer>")[0]
        assert 'href="https://resch.cloud" target="_blank" rel="noopener"' in fuss and f"Version {version()}" in fuss
        assert 'href="https://github.com/reschcloud/ladefalke"' in fuss and "Quelltext" in fuss  # AGPL-3.0 § 13
    assert version() and version() != "unbekannt"
