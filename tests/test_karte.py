"""Heatmap im Dashboard: öffentliche Ladevorgänge je verortetem Ladeort (Raumbezug Phase 4)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Import, ImportStatus, Ladeart, Ladeort, Ladevorgang, Msp, ZuordnungQuelle
from app.services import app_einstellungen
from app.services.auswertung import zeitraum
from app.services.karte import berechnen

D = Decimal


def test_karte_nur_oeffentlich_und_verortet(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    imp = Import(dateiname="k.pdf", datei_pfad="k.pdf", datei_hash="k" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    genau = Ladeort(schluessel="AT|k1", land="AT", adresse="1010 Wien, Testgasse 1", lat=D("48.2"), lon=D("16.37"),
                    genauigkeit="plz", plz="1010", ort="Wien")
    land = Ladeort(schluessel="AT|k2", land="AT", adresse="Irgendwo", lat=D("47.6"), lon=D("14.1"), genauigkeit="land")
    db.add_all([imp, genau, land])
    db.flush()

    def lv(tag, ort, ladeart=Ladeart.oeffentlich, monat=9):
        db.add(Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, monat, tag, 8),
                           ende=datetime(2026, monat, tag, 9), kwh=D(10), kosten_netto=D(5), ladeart=ladeart,
                           ladeort_id=ort.id if ort else None, zuordnung_quelle=ZuordnungQuelle.keine))

    lv(1, genau)
    lv(2, genau)
    lv(3, genau, Ladeart.privat)  # privat: nicht auf der Karte
    lv(4, land)                    # nur Landesmitte → ohne genauen Ort
    lv(5, None)                    # ohne Ladeort → ohne genauen Ort
    lv(6, genau, monat=8)          # anderer Monat
    db.flush()
    k = berechnen(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db))
    assert [(p.adresse, p.anzahl) for p in k.punkte] == [("1010 Wien, Testgasse 1", 2)]
    assert k.ohne_ort == 2 and k.anzahl == 2
    assert k.als_json() == [[48.2, 16.37, 2]]

    # Dashboard: reine Heatmap ohne Liste, Link auf die Karte bei den Ladevorgängen
    text = angemeldet.get("/?art=monat&jahr=2026&nr=9").text
    assert 'data-modus="statisch"' in text and "[[48.2, 16.37, 2]]" in text
    assert "2 ohne genauen Ort</a>" in text and "Häufigste Ladeorte" not in text
    assert "/ladevorgaenge?art=monat&amp;jahr=2026&amp;nr=9&amp;ladeart=oeffentlich" in text

    # Ladevorgänge: Karte mit allen Ladungen der Filter, je Ladung die Angaben für Schild und Tooltip (keine Adresse)
    basis = "/ladevorgaenge?von=2026-09-01&bis=2026-09-30"
    text = angemeldet.get(f"{basis}&ladeart=oeffentlich").text
    assert "<strong>4</strong> Vorgänge" in text and 'data-modus="interaktiv"' in text
    daten = text.split('id="karte-daten">')[1].split("</script>")[0]
    assert daten.count('"Wien"') == 2 and "Testgasse" not in daten  # zwei verortete öffentliche Ladungen
    assert ', 48.2, 16.37, "AT", "Wien", null, 10.0, 5.0, false, "02.09.26", "", "ohne Person", "", 60]' in daten
    assert "2 ohne genauen Ort</a> (stehen immer in der Liste)" in text
    assert "Vergleichswert für „Farbe nach Preis“" in text and "ohne Angabe 0,50 €/kWh" in text  # Ø je Stromart unter der Karte
    assert 'name="ladeart" value="oeffentlich" checked' in text
    assert angemeldet.get(basis).text.split('id="karte-daten">')[1].split("</script>")[0].count('"Wien"') == 3

    # Liste folgt dem Kartenausschnitt; Ladungen ohne genauen Ort stehen immer drin; Karte zeigt weiter alles
    text = angemeldet.get(f"{basis}&ladeart=oeffentlich&bereich=47,10,47.5,11").text  # Ausschnitt ohne Wien
    assert "Im Kartenausschnitt:</small> <strong>2</strong> Vorgänge" in text  # nur die zwei ohne genauen Ort
    assert text.split('id="karte-daten">')[1].split("</script>")[0].count('"Wien"') == 2
    assert 'data-bereich="47,10,47.5,11"' in text and "bereich=47%2C10%2C47.5%2C11" in text  # Export wie die Liste
    text = angemeldet.get(f"{basis}&ladeart=oeffentlich&bereich=48,16,48.5,17").text
    assert "<strong>4</strong> Vorgänge" in text
    assert "<strong>4</strong> Vorgänge" in angemeldet.get(f"{basis}&ladeart=oeffentlich&bereich=kaputt").text
    csv = angemeldet.get("/ladevorgaenge/export.csv?von=2026-09-01&bis=2026-09-30&bereich=47,10,47.5,11").text
    assert len(csv.strip().splitlines()) == 1 + 2

    # Ladeort-Filter (Links aus den Auswertungen), mehrfach; „Filter aufheben“
    text = angemeldet.get(f"{basis}&ladeart=oeffentlich&ladeort_id={genau.id}").text
    assert "<strong>2</strong> Vorgänge" in text and "Nur 1 Ladeort" in text
    text = angemeldet.get(f"{basis}&ladeort_id={genau.id}&ladeort_id={land.id}").text
    assert "<strong>4</strong> Vorgänge" in text and "Nur 2 Ladeorte" in text
    assert "<strong>4</strong> Vorgänge" in angemeldet.get(f"/ladevorgaenge?ladeort_id={genau.id}").text
    assert "<strong>1</strong> Vorgänge" in angemeldet.get(f"{basis}&ladeart=privat").text

    # Auswertungen: Häufigste Ladeorte (öffentlich, auch ohne genaue Verortung), mit Export
    from app.services.auswertung import auswerten
    a = auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db), mit_personen=False)
    assert [(o.adresse, s.anzahl, s.kwh) for o, s in a.ladeorte] == [("1010 Wien, Testgasse 1", 2, 20), ("Irgendwo", 1, 10)]
    text = angemeldet.get("/auswertungen?art=monat&jahr=2026&nr=9").text
    assert "Häufigste Ladeorte" in text and f'ladeort_id={genau.id}&amp;art=monat&amp;jahr=2026&amp;nr=9' in text
    assert "Alle 2 Ladeorte" not in text  # erst ab mehr als 10
    csv = angemeldet.get("/auswertungen/export/ladeorte.csv?art=monat&jahr=2026&nr=9").text
    assert "1010 Wien, Testgasse 1" in csv and "Irgendwo" in csv
