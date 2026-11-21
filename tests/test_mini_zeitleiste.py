"""Zuordnungs-Abschnitte der Detailseiten: Mini-Zeitleiste und chronologische Tabelle mit Lücken."""
from datetime import date

from sqlalchemy import select

from app.models import Fahrer, Fahrzeug, KennungTyp, Ladekarte, Msp, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services.mini_zeitleiste import Eintrag, berechnen

D = date
HEUTE = D(2026, 11, 11)


def test_berechnen_luecken_und_tabelle():
    eintraege = [Eintrag(D(2026, 7, 1), None, "Bernd"), Eintrag(D(2026, 2, 1), D(2026, 5, 31), "Anna")]
    mini = berechnen(eintraege, HEUTE, leasing_beginn=D(2026, 1, 1), luecken_art="warnung")
    assert [(l.von, l.bis) for l in mini.luecken] == [(D(2026, 1, 1), D(2026, 1, 31)), (D(2026, 6, 1), D(2026, 6, 30))]
    # Tabelle chronologisch: Lücke, Anna, Lücke, Bernd
    assert [z.eintrag.name if z.eintrag else "Lücke" for z in mini.zeilen] == ["Lücke", "Anna", "Lücke", "Bernd"]
    assert mini.leasing_beginn == 0 and mini.heute is not None and mini.von == D(2026, 1, 1)
    # ohne Lücken-Art (Person ohne Fahrzeug) keine Lücken; offene Lücke bis heute
    assert berechnen(eintraege, HEUTE).luecken == []
    offen = berechnen([Eintrag(D(2026, 1, 1), D(2026, 9, 30), "Anna")], HEUTE, luecken_art="warnung")
    assert [(l.von, l.bis) for l in offen.luecken] == [(D(2026, 10, 1), None)]
    # Pool: gleichzeitige Zuordnungen auf eigenen Spuren
    pool = berechnen([Eintrag(D(2026, 1, 1), None, "A"), Eintrag(D(2026, 3, 1), None, "B")], HEUTE)
    assert pool.spuren == 2 and [a.spur for a in pool.abschnitte] == [0, 1]


def test_detailseiten(angemeldet, db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    fz = Fahrzeug(kennzeichen="MZ-1", leasing_beginn=D(2026, 1, 1))
    f = Fahrer(name="Mini Test")
    k = Ladekarte(msp_id=msp.id, kennung="MZ-K", kennung_typ=KennungTyp.kartennummer)
    db.add_all([fz, f, k])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 3, 1),
                                        gueltig_bis=D(2026, 6, 30)),
                ZuordnungKarte(ladekarte_id=k.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 4, 1), gueltig_bis=D(2026, 8, 31))])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert 'class="mini-zl fzl"' in seite and 'class="mini-zl-leasing beginn"' in seite  # Zeitleiste des Fahrzeugs
    assert '<tr class="luecken-zeile warnung">' in seite and "symbol-warnung" in seite and "Lücke ohne Fahrer:in" in seite
    assert 'data-luecke-fuellen="neue-fahrer-zuordnung"' in seite and 'data-von="2026-01-01" data-bis="2026-02-28"' in seite
    assert 'id="neue-fahrer-zuordnung"' in seite and ">Lücke schließen</button>" in seite

    seite = angemeldet.get(f"/stammdaten/fahrer/{f.id}").text  # Person ohne Fahrzeug ist keine Lücke
    assert 'class="mini-zl"' in seite and "luecken-zeile" not in seite

    seite = angemeldet.get(f"/stammdaten/ladekarten/{k.id}").text  # Karte ohne Zuordnung: neutral
    assert '<tr class="luecken-zeile neutral">' in seite and 'data-luecke-fuellen="neue-karten-zuordnung"' in seite
    assert ">zuordnen</button>" in seite and "nicht zugeordnet" in seite


def test_symbole_und_kartenansicht(angemeldet, db):
    """Auto-/Personen-Symbole bei Karten-Zuordnungen, in der Auswahl und im Menü; Heatmap als dritte Kartenansicht."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    fz, f = Fahrzeug(kennzeichen="SY-1"), Fahrer(name="Symbol Test")
    k1 = Ladekarte(msp_id=msp.id, kennung="SY-A", kennung_typ=KennungTyp.kartennummer)
    k2 = Ladekarte(msp_id=msp.id, kennung="SY-P", kennung_typ=KennungTyp.kartennummer)
    db.add_all([fz, f, k1, k2])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=k1.id, fahrzeug_id=fz.id, gueltig_ab=D(2026, 1, 1)),
                ZuordnungKarte(ladekarte_id=k2.id, fahrer_id=f.id, gueltig_ab=D(2026, 1, 1))])
    db.flush()
    seite = angemeldet.get(f"/stammdaten/ladekarten/{k1.id}").text
    assert 'aria-label="Fahrzeug"><title>Fahrzeug</title>' in seite and ">SY-1</a>" in seite
    assert "🚗 SY-1" in seite and "👤 Symbol Test" in seite  # Auswahl „Zuordnen an“
    liste = angemeldet.get("/stammdaten/ladekarten").text
    zeile = liste.split(">SY-P</a>")[1].split("</tr>")[0]
    assert 'aria-label="Person"' in zeile and ">Symbol Test</a>" in zeile
    assert '<a href="/stammdaten/fahrzeuge"><svg class="symbol symbol-auto"' in liste  # Menü „Stammdaten“
    assert 'data-kartenfarbe="heat"' in angemeldet.get("/ladevorgaenge").text
