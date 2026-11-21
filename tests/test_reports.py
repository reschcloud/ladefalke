"""PDF-Reports: Zusammenfassung und Fahrer:innen-Report (nur eigene Daten), Download-Routen."""
import io

import pypdfium2 as pdfium

from app.services import reports
from app.services.auswertung import zeitraum
from tests.test_cpo_personen import daten  # noqa: F401  (Fixture: Anna, Bernd, Cora mit Ladungen im 09/2026)

SEP = "art=monat&jahr=2026&nr=9"


def _text(pdf: bytes) -> tuple[str, int]:
    dok = pdfium.PdfDocument(io.BytesIO(pdf))
    text = "\n".join(seite.get_textpage().get_text_range() for seite in dok)
    return text, len(dok)


def test_zusammenfassung(db, daten):
    pdf = reports.zusammenfassung(db, zeitraum("monat", 2026, 9))
    assert pdf.startswith(b"%PDF")
    text, seiten = _text(pdf)
    assert "Zusammenfassung 09/2026" in text and "Seite 1 von" in text and "Ladefalke" in text
    for erwartet in ("Verteilung", "Toplisten", "Pro Fahrer:in", "Pro Fahrzeug", "Pro CPO", "Anna CPO", "CPO-1"):
        assert erwartet in text, erwartet


def test_fahrer_report_nur_eigene_daten(db, daten):
    text, _ = _text(reports.fahrer(db, zeitraum("monat", 2026, 9), daten["anna"].id))
    assert "Dein Ladereport 09/2026" in text and "Anna CPO" in text
    assert "Nach Ladekarte" in text and "persönlich" in text and "AT*TST" in text  # eigene Karten und CPO
    for fremd in ("Bernd", "Cora", "Score"):  # keine anderen Personen, keine Topliste
        assert fremd not in text, fremd
    assert "Flottenschnitt" in text and "Platz 1 von 1" in text  # Aggregate: Vergleich und eigener Rang
    assert "Ladedauer" in text and "keine DC-Ladung" in text  # Testdaten ohne Stromart
    assert "Keine öffentlichen DC-Ladungen im Zeitraum" in text
    leer, _ = _text(reports.fahrer(db, zeitraum("monat", 2026, 1), daten["anna"].id))
    assert "keine Ladungen von dir erfasst" in leer


def test_reports_seite_und_downloads(angemeldet, daten):
    anna = daten["anna"].id
    seite = angemeldet.get(f"/reports?{SEP}&fahrer={anna}").text
    assert 'aria-current="page">Reports</a>' in seite and "Zusammenfassung" in seite and "Fahrer:innen-Reports" in seite
    assert f'href="/reports/fahrer/{anna}.pdf?{SEP.replace("&", "&amp;")}"' in seite
    assert f'<tr id="fahrer-{anna}" class="hervorgehoben">' in seite and "Alle als ZIP" in seite
    assert "fahrer=" in seite.split('class="zeitraumwahl"')[1].split("</nav>")[0]  # Zeitraumwechsel behält die Person
    antwort = angemeldet.get(f"/reports/zusammenfassung.pdf?{SEP}")
    assert antwort.status_code == 200 and antwort.headers["content-type"] == "application/pdf"
    assert 'filename="ladefalke-zusammenfassung-09-2026.pdf"' in antwort.headers["content-disposition"]
    antwort = angemeldet.get(f"/reports/fahrer/{anna}.pdf?{SEP}")
    assert antwort.status_code == 200 and "ladefalke-report-anna-cpo-09-2026.pdf" in antwort.headers["content-disposition"]
    assert angemeldet.get(f"/reports/fahrer/999999.pdf?{SEP}").status_code == 404
    antwort = angemeldet.get(f"/reports/fahrer.zip?{SEP}")
    assert antwort.status_code == 200 and antwort.headers["content-type"] == "application/zip"
    import zipfile
    with zipfile.ZipFile(io.BytesIO(antwort.content)) as archiv:
        assert archiv.namelist() == ["ladefalke-report-anna-cpo-09-2026.pdf"]
    # Auswertungen und Fahrer:innen-Seite verweisen auf die Reports-Seite
    assert f'href="/reports?{SEP.replace("&", "&amp;")}"' in angemeldet.get(f"/auswertungen?{SEP}").text
    assert f'/reports?{SEP.replace("&", "&amp;")}&amp;fahrer={anna}' in angemeldet.get(f"/auswertungen/fahrer/{anna}?{SEP}").text


def test_kartenbild_und_diagramme(angemeldet, db, daten):
    from PIL import Image
    from app.services import diagramme, kartenbild
    png = kartenbild.bild([(48.208, 16.372, 10), (47.07, 15.44, 2)], breite=800, hoehe=480)
    assert png.startswith(b"\x89PNG") and Image.open(io.BytesIO(png)).size == (800, 480)
    assert kartenbild.bild([]) is None and kartenbild.data_uri(None) is None
    v = diagramme.aus_summen([(f"M{i}", type("S", (), {"kwh": i + 1, "kosten": i})()) for i in range(7)])
    assert [r.name for r in v.reihen] == ["M6", "M5", "M4", "M3", "M2", "Weitere"] and v.reihensumme(5, 0) == 3
    text, _ = _text(reports.fahrer(db, zeitraum("monat", 2026, 9), daten["anna"].id))
    assert "kWh nach Anbieter (MSP)" in text and "kWh nach Betreiber (CPO, öffentlich)" in text
    assert "Deine öffentlichen Ladungen" in text and "Heatmap" not in text
    text, _ = _text(reports.zusammenfassung(db, zeitraum("monat", 2026, 9)))
    assert "Öffentliches Laden" in text
    # Fahrer:innen-Seite: Links auf den Report (Vormonat und laufendes Jahr)
    seite = angemeldet.get(f"/stammdaten/fahrer/{daten['anna'].id}").text
    assert f'href="/reports?fahrer={daten["anna"].id}#fahrer-{daten["anna"].id}"' in seite


def test_ladedauer_dc(db, daten):
    from sqlalchemy import select
    from app.models import Ladeart, Ladevorgang
    for v in db.scalars(select(Ladevorgang).where(Ladevorgang.betreiber_id == "AT*TST",
                                                  Ladevorgang.ladeart == Ladeart.oeffentlich)):
        v.stromart = "DC"  # alle 1 h angesteckt
    db.flush()
    dc = reports._ladedauer_dc(db, zeitraum("monat", 2026, 9), "ende", daten["anna"].id)
    assert dc["eigen"] == 60 and dc["anzahl"] == 2 and dc["flotte"] == 60  # Anna: 2 DC; Pool-Ladung zählt nur zur Flotte
    text, _ = _text(reports.fahrer(db, zeitraum("monat", 2026, 9), daten["anna"].id))
    assert "1:00 h" in text and "2 DC-Ladungen" in text
    # Histogramme öffentliches DC-Laden: im Fahrer:innen-Report nur die eigenen Ladungen, in der Zusammenfassung alle
    assert "Dein öffentliches DC-Laden" in text and "Median 60 min · 2 Ladungen" in text
    gesamt, _ = _text(reports.zusammenfassung(db, zeitraum("monat", 2026, 9)))
    assert "Öffentliches DC-Laden" in gesamt and "Uhrzeit des Ladebeginns" in gesamt
    assert "Median 60 min · 2 Ladungen" not in gesamt  # Flotte: mehr Ladungen als Anna



def test_ladedauer_dc_im_web(angemeldet, db, daten):
    """Ø Ladedauer DC auch als Kachel in Dashboard, Auswertungen und den Detail-Auswertungen."""
    from sqlalchemy import select
    from app.models import Ladeart, Ladevorgang
    from app.services import kennzahlen
    for v in db.scalars(select(Ladevorgang).where(Ladevorgang.betreiber_id == "AT*TST",
                                                  Ladevorgang.ladeart == Ladeart.oeffentlich)):
        v.stromart = "DC"
    db.flush()
    fz = db.scalar(select(Ladevorgang.fahrzeug_id).where(Ladevorgang.stromart == "DC").limit(1))
    dc = kennzahlen.ladedauer_dc(db, zeitraum("monat", 2026, 9), "ende", fahrzeug_id=fz)
    assert dc.eigen == 60 and dc.flotte == 60 and dc.anzahl >= 1
    for url in (f"/?{SEP}", f"/auswertungen?{SEP}", f"/auswertungen/fahrer/{daten['anna'].id}?{SEP}",
                f"/auswertungen/fahrzeug/{fz}?{SEP}"):
        seite = angemeldet.get(url).text
        assert "Ø Ladedauer DC" in seite and "1:00 h" in seite, url
    assert "ggü. Ø 06/2026 – 08/2026" not in angemeldet.get(f"/?{SEP}").text  # ohne DC-Daten vorher kein Vergleich


def test_tlp_kennzeichnung(angemeldet, db, daten):
    """TLP-Kennzeichnung global in den Einstellungen; erscheint in der Fußzeile jeder PDF-Seite."""
    text, _ = _text(reports.zusammenfassung(db, zeitraum("monat", 2026, 9)))
    assert "TLP:" not in text  # Vorgabe: keine
    daten_form = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
                  "report_tlp": "AMBER+STRICT"}
    angemeldet.post("/einstellungen", data=daten_form)
    text, seiten = _text(reports.fahrer(db, zeitraum("monat", 2026, 9), daten["anna"].id))
    assert text.count("TLP:AMBER+STRICT") == seiten
    seite = angemeldet.get("/reports?art=monat&jahr=2026&nr=9").text
    assert 'class="tlp tlp-amber-strict"' in seite and ">TLP:AMBER+STRICT</span>" in seite
    assert angemeldet.post("/einstellungen", data={**daten_form, "report_tlp": "LILA"}).status_code == 422


def test_download_signal(angemeldet, db):
    """Mit „download_kennung“ setzt der Download ein Cookie mit der Kennung (Lade-Animation endet), sonst nicht."""
    antwort = angemeldet.get(f"/reports/zusammenfassung.pdf?{SEP}&download_kennung=abc123")
    assert antwort.status_code == 200 and "ladefalke_download=abc123" in antwort.headers.get("set-cookie", "")
    ohne = angemeldet.get(f"/reports/zusammenfassung.pdf?{SEP}")
    assert "ladefalke_download" not in ohne.headers.get("set-cookie", "")
    boese = angemeldet.get(f"/reports/zusammenfassung.pdf?{SEP}&download_kennung=a;b")
    assert "ladefalke_download" not in boese.headers.get("set-cookie", "")


def test_kachel_reihenfolge(angemeldet, db, daten):
    """Überall gleich: Ladevorgänge, kWh, Kosten, Preis, Ladedauer DC, Anteil öffentlich."""
    reihe = ["Ladevorgänge", "kWh", "Kosten netto", "Ø €/kWh", "Ø Ladedauer DC", "öffentlich (kWh)"]

    def in_reihenfolge(text, titel):
        pos, ab = [], 0
        for t in titel:
            ab = text.index(t, ab)
            pos.append(ab)
        return pos == sorted(pos)

    for url in (f"/?{SEP}", f"/auswertungen?{SEP}", f"/auswertungen/fahrer/{daten['anna'].id}?{SEP}"):
        seite = angemeldet.get(url).text
        kacheln = seite.split('<div class="kacheln kennzahlen"')[1].split("</div>\n</div>")[0]
        assert in_reihenfolge(kacheln, reihe), url
    seite = angemeldet.get(f"/stammdaten/fahrer/{daten['anna'].id}").text
    assert in_reihenfolge(seite.split('class="kacheln kacheln-klein"')[1], reihe)
    text, _ = _text(reports.zusammenfassung(db, zeitraum("monat", 2026, 9)))
    assert in_reihenfolge(text, ["Ladevorgänge", "kWh", "Kosten netto", "Ø €/kWh", "Ø Ladedauer DC", "öffentlich (kWh)"])


def test_offene_aufgaben(angemeldet, db, daten):
    """Report „Offene Aufgaben“: Stand heute; Abschnitte nur mit offenen Punkten; Download für alle Rollen."""
    from datetime import timedelta
    from app.config import heute
    from app.models import Fahrzeug
    from app.services import app_einstellungen
    app_einstellungen.speichern(db, {"feature_vignette": "1"})
    db.add(Fahrzeug(kennzeichen="AUF-1", leasing_ende=heute() + timedelta(days=5)))
    db.flush()
    assert "Offene Aufgaben" in angemeldet.get(f"/reports?{SEP}").text
    antwort = angemeldet.get("/reports/offene-aufgaben.pdf")
    assert antwort.status_code == 200 and antwort.content.startswith(b"%PDF")
    assert f"ladefalke-offene-aufgaben-{heute():%Y-%m-%d}.pdf" in antwort.headers["content-disposition"]
    text, _ = _text(antwort.content)
    assert f"Stand {heute():%d.%m.%Y}" in text and "Fristen" in text and "AUF-1" in text
    assert "Vignette" in text and "Gültigkeit fehlt" in text  # AUF-1 braucht eine Vignette, hat aber keine Daten
    assert "Ladevorgänge ohne Fahrzeug" in text
