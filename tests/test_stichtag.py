"""Stichtag Ladeende/Ladebeginn (Einstellung) gilt in der ganzen App: Zuordnung, Person, Filter, Zeitraum."""
import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services import app_einstellungen
from app.services.auswertung import auswerten, zeitraum

EINSTELLUNGEN = {"stichtag": "beginn", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
                 "plausibel_ac_max_kw": "12", "plausibel_dc_min_kw": "10,0", "plausibel_dc_max_kw": "1.000",  # wie angezeigt
                 "schaetzung_ac_kw": "11", "schaetzung_dc_kw": "90", "schaetzung_obergrenze_kwh": "50"}


def _anzahl(text):
    return int(re.search(r"<strong>(\d+)</strong> Vorgänge", text).group(1))


def test_umstellen_auf_ladebeginn(angemeldet, db):
    """Ladung 30.9. 22:00 – 1.10. 02:00; die Karte wechselt am 1.10. von Fahrzeug A (Anna) zu Fahrzeug B (Bernd)."""
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    anna, bernd = Fahrer(name="Anna Stichtag"), Fahrer(name="Bernd Stichtag")
    fz_a, fz_b = Fahrzeug(kennzeichen="ST A"), Fahrzeug(kennzeichen="ST B")
    karte = Ladekarte(msp_id=msp.id, kennung="ST1", kennung_typ=KennungTyp.vertragsnummer)
    imp = Import(dateiname="s.pdf", datei_pfad="s.pdf", datei_hash="t" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add_all([anna, bernd, fz_a, fz_b, karte, imp])
    db.flush()
    db.add_all([
        ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=fz_a.id, gueltig_ab=date(2026, 1, 1),
                       gueltig_bis=date(2026, 9, 30)),
        ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=fz_b.id, gueltig_ab=date(2026, 10, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz_a.id, gueltig_ab=date(2026, 1, 1)),
        ZuordnungFahrerFahrzeug(fahrer_id=bernd.id, fahrzeug_id=fz_b.id, gueltig_ab=date(2026, 1, 1)),
    ])
    v = Ladevorgang(import_id=imp.id, msp_id=msp.id, ladekarte_id=karte.id, start=datetime(2026, 9, 30, 22),
                    ende=datetime(2026, 10, 1, 2), kwh=Decimal(30), kosten_netto=Decimal(15),
                    ladeart=Ladeart.oeffentlich, fahrzeug_id=fz_b.id, zuordnung_quelle=ZuordnungQuelle.automatisch)
    db.add(v)
    db.flush()

    # Standard Ladeende: Oktober, Fahrzeug B, Bernd
    assert _anzahl(angemeldet.get("/ladevorgaenge?von=2026-10-01&bis=2026-10-31").text) == 1
    assert _anzahl(angemeldet.get(f"/ladevorgaenge?fahrer={bernd.id}").text) == 1
    assert auswerten(db, zeitraum("monat", 2026, 10), app_einstellungen.laden(db)).gesamt.anzahl == 1

    # Umstellen auf Ladebeginn: Neuzuordnung → Fahrzeug A, Anna, September
    text = angemeldet.post("/einstellungen", data=EINSTELLUNGEN).text
    assert "Stichtag jetzt Ladebeginn: 1 Ladevorgänge neu zugeordnet." in text
    db.refresh(v)
    assert v.fahrzeug_id == fz_a.id
    assert _anzahl(angemeldet.get("/ladevorgaenge?von=2026-09-01&bis=2026-09-30").text) == 1
    assert _anzahl(angemeldet.get("/ladevorgaenge?von=2026-10-01&bis=2026-10-31").text) == 0
    assert _anzahl(angemeldet.get(f"/ladevorgaenge?fahrer={anna.id}").text) == 1
    assert _anzahl(angemeldet.get(f"/ladevorgaenge?fahrer={bernd.id}").text) == 0
    liste = angemeldet.get("/ladevorgaenge").text
    assert ">Ladebeginn<span class=\"sortpfeil\"" in liste and "darunter das Ladeende" in liste
    assert f'<td><a href="/ladevorgaenge/{v.id}">30.09.2026 22:00</a></td>' in liste
    # daneben die Dauer, darunter das Ladeende (anderer Tag: mit Datum)
    assert '<td class="dauer">4:00 h<br><small>bis\n  01.10.2026 02:00</small></td>' in liste
    assert "<h1>Ladevorgang 30.09.2026 22:00</h1>" in angemeldet.get(f"/ladevorgaenge/{v.id}").text
    assert "<th>Ladebeginn</th><th>Dauer</th>" in angemeldet.get(f"/stammdaten/fahrzeuge/{fz_a.id}").text
    assert auswerten(db, zeitraum("monat", 2026, 9), app_einstellungen.laden(db)).gesamt.anzahl == 1

    # Manuelle Zuordnung bleibt beim Zurückstellen
    v.fahrzeug_id, v.zuordnung_quelle = fz_b.id, ZuordnungQuelle.manuell
    db.flush()
    angemeldet.post("/einstellungen", data={**EINSTELLUNGEN, "stichtag": "ende"})
    db.refresh(v)
    assert (v.fahrzeug_id, v.zuordnung_quelle) == (fz_b.id, ZuordnungQuelle.manuell)
