"""Vollständigkeit der Daten je Monat (Dashboard)."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (Fahrer, Fahrzeug, Gebuehr, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladevorgang,
                        Msp, ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.services import app_einstellungen
from app.services.auswertung import zeitraum
from app.services.vollstaendigkeit import pruefen

D = Decimal
HEUTE = date(2026, 11, 5)


@pytest.fixture
def daten(db):
    """DCS (je Karte): K1 und K2 ab August, K3 erst ab Oktober; im September nur K1 mit Vorgang, K2 mit Gebühr.
    Wien Energie: Eintrag nur im August. Fahrzeug A (Anna) lädt im September über K1, Fahrzeug B (Bernd) nicht."""
    dcs = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    we = db.scalar(select(Msp).where(Msp.parser_key == "wien_energie"))
    imp = Import(dateiname="v.pdf", datei_pfad="v.pdf", datei_hash="v" * 64, msp_id=dcs.id, ladeart=Ladeart.oeffentlich,
                 status=ImportStatus.abgeschlossen)
    imp_we = Import(dateiname="w.pdf", datei_pfad="w.pdf", datei_hash="w" * 64, msp_id=we.id,
                    ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen,
                    abrechnungszeitraum_von=date(2026, 8, 1), abrechnungszeitraum_bis=date(2026, 8, 31))
    k1, k2, k3 = (Ladekarte(msp_id=dcs.id, kennung=f"VK{i}", kennung_typ=KennungTyp.kartennummer) for i in (1, 2, 3))
    kw = Ladekarte(msp_id=we.id, kennung="VKW", kennung_typ=KennungTyp.kartennummer)
    a, b = Fahrzeug(kennzeichen="V-A 1"), Fahrzeug(kennzeichen="V-B 1")
    anna, bernd = Fahrer(name="Anna V"), Fahrer(name="Bernd V")
    db.add_all([imp, imp_we, k1, k2, k3, kw, a, b, anna, bernd])
    db.flush()
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=a.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=bernd.id, fahrzeug_id=b.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungKarte(ladekarte_id=k1.id, fahrer_id=anna.id, gueltig_ab=date(2026, 1, 1))])

    def lv(karte, monat, fz=None, msp=dcs, i=imp):
        db.add(Ladevorgang(import_id=i.id, msp_id=msp.id, ladekarte_id=karte.id, start=datetime(2026, monat, 10, 8),
                           ende=datetime(2026, monat, 10, 9), kwh=D(10), kosten_netto=D(5),
                           ladeart=Ladeart.oeffentlich, fahrzeug_id=fz.id if fz else None,
                           zuordnung_quelle=ZuordnungQuelle.automatisch if fz else ZuordnungQuelle.keine))

    lv(k1, 8, a)
    lv(k2, 8, a)
    lv(k1, 9, a)
    lv(k3, 10, a)
    lv(k1, 10)  # ohne Fahrzeug
    db.add(Gebuehr(import_id=imp.id, ladekarte_id=k2.id, bezeichnung="Grundgebühr", betrag_netto=D(1),
                   zeitraum_von=date(2026, 9, 1)))
    lv(kw, 8, None, we, imp_we)
    db.flush()
    return {"k1": k1, "k2": k2, "k3": k3, "we": we, "dcs": dcs, "b": b, "bernd": bernd}


def _pruefen(db, z):
    return pruefen(db, z, app_einstellungen.laden(db), heute=HEUTE)


def test_monat_mit_luecken_und_hinweisen(db, daten):
    st = _pruefen(db, zeitraum("monat", 2026, 9)).monate[0]
    assert st.status == "fehlt"
    luecken = {l.msp.name: l for l in st.luecken}
    assert set(luecken) == {daten["we"].name}  # DCS: K1 Vorgang, K2 Gebühr, K3 erst ab Oktober erwartet
    assert luecken[daten["we"].name].karten == []
    assert st.fahrzeuge_ohne == [daten["b"]] and st.fahrer_ohne == [daten["bernd"]]  # Anna lädt über K1


def test_je_karte(db, daten):
    st = _pruefen(db, zeitraum("monat", 2026, 10)).monate[0]
    dcs = next(l for l in st.luecken if l.msp.id == daten["dcs"].id)
    assert dcs.karten == [daten["k2"]] and dcs.karten_erwartet == 3  # K1 und K3 haben Vorgänge
    assert st.ohne_fahrzeug == 1


def test_laufender_monat_offen_und_zukunft_fehlt(db, daten):
    v = _pruefen(db, zeitraum("quartal", 2026, 4))  # Okt, Nov (laufend); Dez liegt in der Zukunft
    assert [m.label for m in v.monate] == ["10/2026", "11/2026"]
    assert v.monate[1].status == "offen" and not v.monate[1].luecken
    assert v.status == "fehlt" and len(v.unvollstaendig) == 1
    assert _pruefen(db, zeitraum("monat", 2026, 11)).status == "offen"
    assert _pruefen(db, zeitraum("monat", 2027, 1)).monate == []


def test_vor_dem_ersten_eintrag_nichts_erwartet(db, daten):
    assert _pruefen(db, zeitraum("monat", 2026, 7)).monate[0].luecken == []


def test_dashboard_zeigt_vollstaendigkeit(angemeldet, db, daten):
    text = angemeldet.get("/?art=monat&jahr=2026&nr=10").text
    assert "Daten unvollständig" in text and "1 von 3 Karten ohne Eintrag" in text
    assert f'<a href="/stammdaten/ladekarten/{daten["k2"].id}">VK2</a>' in text
    text = angemeldet.get("/?art=quartal&jahr=2026&nr=3").text
    assert "in 1 von 3 Monaten" in text  # Juli ok, August ok, September fehlt (Wien Energie)
