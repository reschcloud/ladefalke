"""Zeitleiste (Gantt) der Fahrzeug-Zuordnungen."""
from datetime import date

import pytest

from app.models import Fahrer, Fahrzeug, ZuordnungFahrerFahrzeug
from app.services.zeitleiste import bereich_jahr, bereich_rollierend, berechnen

HEUTE = date(2026, 11, 5)


@pytest.fixture
def daten(db):
    fz_wechsel, fz_pool = Fahrzeug(kennzeichen="ZL 1", modell="Testmodell A"), Fahrzeug(kennzeichen="ZL 2")
    fz_neu = Fahrzeug(kennzeichen="ZL 3")
    a, b, c = Fahrer(name="Anna"), Fahrer(name="Bernd"), Fahrer(name="Clara")
    db.add_all([fz_wechsel, fz_pool, fz_neu, a, b, c])
    db.flush()
    db.add_all([
        # ZL 1: Anna Jan–Mär, Lücke April, Bernd ab Mai
        ZuordnungFahrerFahrzeug(fahrer_id=a.id, fahrzeug_id=fz_wechsel.id, gueltig_ab=date(2026, 1, 1),
                                gueltig_bis=date(2026, 3, 31)),
        ZuordnungFahrerFahrzeug(fahrer_id=b.id, fahrzeug_id=fz_wechsel.id, gueltig_ab=date(2026, 5, 1)),
        # ZL 2: Poolfahrzeug, Clara ab Feb, Anna zusätzlich ab Juni (bis Ende September) → 2 Spuren
        ZuordnungFahrerFahrzeug(fahrer_id=c.id, fahrzeug_id=fz_pool.id, gueltig_ab=date(2026, 2, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=a.id, fahrzeug_id=fz_pool.id, gueltig_ab=date(2026, 6, 1),
                                gueltig_bis=date(2026, 9, 30)),
    ])
    db.flush()
    return {"wechsel": fz_wechsel, "pool": fz_pool, "neu": fz_neu}


def test_bereiche():
    assert bereich_rollierend(HEUTE) == (date(2025, 12, 1), date(2027, 1, 31))  # 11 Monate zurück, 2 voraus
    assert bereich_jahr(2026) == (date(2026, 1, 1), date(2026, 12, 31))


def test_jahr(db, daten):
    zl = berechnen(db, *bereich_jahr(2026), HEUTE)
    zeilen = {z.name: z for z in zl.zeilen}
    assert len(zl.monate) == 12 and zl.monate[0][0] == "Jan 26"
    assert zl.heute is not None and 84 < zl.heute < 85  # 5. November

    w = zeilen["ZL 1"]
    assert [b.beschriftung for b in w.balken] == ["Anna", "Bernd"] and w.spuren == 1
    assert [(l.von, l.bis) for l in w.luecken] == [(date(2026, 4, 1), date(2026, 4, 30))]
    assert w.balken[0].links == 0 and round(w.balken[0].breite, 2) == round(90 / 365 * 100, 2)
    assert w.luecken[0].text == "Lücke: 01.04.2026 – 30.04.2026"

    p = zeilen["ZL 2"]
    assert p.spuren == 2 and [b.spur for b in p.balken] == [0, 1]
    assert [(l.von, l.bis) for l in p.luecken] == []  # Jänner liegt vor der ersten Zuordnung → keine Lücke

    assert zeilen["ZL 3"].nie_zugeordnet and zeilen["ZL 3"].balken == []
    assert zl.mit_luecken == 2


def test_luecke_am_ende_und_beschnitt(db, daten):
    """Rollierend bis Jänner 2027: Bernd offen → keine Lücke; Zuordnungen vor dem Bereich werden beschnitten."""
    zl = berechnen(db, *bereich_rollierend(HEUTE), HEUTE)
    w = next(z for z in zl.zeilen if z.name == "ZL 1")
    assert w.balken[0].links > 0  # Anna ab 1.1.2026, Bereich ab 1.12.2025
    assert [(l.von, l.bis) for l in w.luecken] == [(date(2026, 4, 1), date(2026, 4, 30))]
    p = next(z for z in zl.zeilen if z.name == "ZL 2")
    assert all(l.bis is not None for l in p.luecken)


def test_inaktive_ausblenden(db, daten):
    daten["neu"].aktiv = False
    db.flush()
    assert "ZL 3" not in [z.name for z in berechnen(db, *bereich_jahr(2026), HEUTE).zeilen]
    assert "ZL 3" in [z.name for z in berechnen(db, *bereich_jahr(2026), HEUTE, status="alle").zeilen]


def test_seite_standard_kalenderjahr(angemeldet, db, daten):
    from app.config import heute
    text = angemeldet.get("/stammdaten/zuordnungen/zeitleiste").text
    assert f'<strong class="zeitraum-label">{heute().year}</strong>' in text  # Standard: laufendes Jahr
    assert "Person hervorheben" not in text
    text = angemeldet.get("/stammdaten/zuordnungen/zeitleiste?jahr=2026").text
    assert "Zeitleiste Fahrzeuge · Fahrer:innen" in text and "2 Fahrzeuge mit Lücken" in text
    assert 'title="Bernd: 01.05.2026 – offen"' in text and "symbol-warnung" in text and "noch nie zugeordnet</span>" in text
    assert "/stammdaten/zuordnungen/fahrer/" in text and "zurueck=/stammdaten/zuordnungen/zeitleiste%3Fstatus%3Daktiv%26nach%3Dfahrzeug" in text
    assert "Letzte 12 Monate" in text
    assert angemeldet.get("/stammdaten/zuordnungen/zeitleiste?ansicht=12monate").status_code == 200
    assert 'href="/stammdaten/zuordnungen/zeitleiste"' in angemeldet.get("/").text  # Menüeintrag


def test_umgedreht_nach_personen(db, daten):
    zl = berechnen(db, *bereich_jahr(2026), HEUTE, nach="fahrer")
    zeilen = {z.name: z for z in zl.zeilen}
    anna = zeilen["Anna"]
    assert [b.beschriftung for b in anna.balken] == ["ZL 1 · Testmodell A", "ZL 2"]  # Fahrzeug (+ Modell) als Balken
    # Personen ohne Fahrzeug sind keine Lücke – Lücken gibt es nur bei Fahrzeugen ohne Fahrer:in
    assert all(z.luecken == [] and not z.nie_zugeordnet for z in zl.zeilen) and zl.mit_luecken == 0
    assert zeilen["Clara"].spuren == 1
    assert not anna.ist_fahrzeug and anna.link.startswith("/stammdaten/fahrer/")
    assert zl.nach == "fahrer"


def test_seite_umgedreht(angemeldet, db, daten):
    text = angemeldet.get("/stammdaten/zuordnungen/zeitleiste?jahr=2026&nach=fahrer").text
    assert "Zeitleiste Fahrer:innen · Fahrzeuge" in text
    assert 'title="ZL 1 · Testmodell A: 01.01.2026 – 31.03.2026"' in text and "mit Lücken</strong>" not in text
    assert "<span>ZL 1 · Testmodell A</span>" in text  # nur Anna
    assert "nach=fahrer" in text.split('aria-label="Vorjahr"')[0]  # Blättern behält die Richtung


def test_hover_hervorhebung(angemeldet, db, daten):
    for nach in ("fahrzeug", "fahrer"):
        text = angemeldet.get(f"/stammdaten/zuordnungen/zeitleiste?jahr=2026&nach={nach}").text
        zl = berechnen(db, *bereich_jahr(2026), HEUTE, nach=nach)
        for i in zl.balken_ids:
            assert f'.zl-balken[data-id="{i}"]:is(:hover, :focus-visible)) .zl-balken[data-id="{i}"]' in text
            assert f'data-id="{i}"' in text


def test_zeitleiste_5jahre_und_alles(angemeldet, db):
    """Weitere Zeiträume; ab 24 Monaten ist die Achse nach Jahren beschriftet."""
    from datetime import date
    from app.models import Fahrer, Fahrzeug, ZuordnungFahrerFahrzeug
    from app.services.zeitleiste import bereich_5jahre, bereich_alles
    assert bereich_5jahre(date(2026, 11, 11)) == (date(2021, 12, 1), date(2027, 1, 31))  # wie 12 Monate: bis Ende Jänner
    fz, f = Fahrzeug(kennzeichen="ZL-ALT", leasing_beginn=date(2019, 3, 15)), Fahrer(name="Zeitleiste Alt")
    db.add_all([fz, f])
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=fz.id, gueltig_ab=date(2020, 5, 1)))
    db.flush()
    von, bis = bereich_alles(db, date(2026, 11, 11))
    assert von == date(2019, 3, 1) and bis == date(2027, 1, 31)
    text = angemeldet.get("/stammdaten/zuordnungen/zeitleiste?ansicht=alles").text
    assert 'aria-current="true">Alles</a>' in text and '>2019</span>' in text and ">Jän 26<" not in text
    text = angemeldet.get("/stammdaten/zuordnungen/zeitleiste?ansicht=5jahre").text
    assert 'aria-current="true">Letzte 5 Jahre</a>' in text and '>2022</span>' in text
    assert "Lücken in der Fahrer:innen-Zuordnung" in angemeldet.get("/aufgaben").text  # 2019–2020 ohne Fahrer:in


def test_zeitleiste_leasing(angemeldet, db):
    """Leasingbeginn/-ende sichtbar; jeder Tag im Leasing ohne Zuordnung ist eine Lücke, auch vor der ersten."""
    from datetime import date
    from app.models import Fahrer, Fahrzeug, ZuordnungFahrerFahrzeug
    fz = Fahrzeug(kennzeichen="ZL-LEAS", leasing_beginn=date(2026, 2, 1), leasing_ende=date(2026, 10, 31))
    f = Fahrer(name="Leasing Test")
    db.add_all([fz, f])
    db.flush()
    db.add(ZuordnungFahrerFahrzeug(fahrer_id=f.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 4, 1),
                                   gueltig_bis=date(2026, 8, 31)))
    db.flush()
    zl = berechnen(db, *bereich_jahr(2026), date(2026, 11, 11))
    zeile = next(z for z in zl.zeilen if z.objekt.id == fz.id)
    assert [(l.von, l.bis) for l in zeile.luecken] == [(date(2026, 2, 1), date(2026, 3, 31)),
                                                        (date(2026, 9, 1), date(2026, 10, 31))]
    assert zeile.leasing_beginn is not None and zeile.leasing_ende is not None and len(zeile.ausserhalb) == 2
    text = angemeldet.get("/stammdaten/zuordnungen/zeitleiste?jahr=2026").text
    assert 'title="Leasingbeginn 01.02.2026"' in text and 'title="Leasing-Ende 31.10.2026"' in text
    assert "außerhalb des Leasings" in text
