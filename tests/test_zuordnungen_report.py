"""Report „Zuordnungen am Stichtag“: Inhalt (persönliche und Fahrzeugkarten, Pool, Spalten je MSP, Abschnitte),
PDF quer, Versand mit Stichtag, Zeitpläne mit Monatsletztem, Abos und automatischer Versand an Abonnent:innen."""
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models import (Benutzer, Fahrer, Fahrzeug, KennungTyp, Ladekarte, Msp, ReportVersand,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte)
from app.services import app_einstellungen, stichtag_zuordnungen, versand
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)
from tests.test_mail import ScheinServer, schein  # noqa: F401  (Fixture)
from tests.test_versand import mailbereit  # noqa: F401  (Fixture)

TAG = date(2026, 11, 30)
LANG = "Firmenkarte Vertrieb Region Süd mit sehr langem Vertragsnamen"


@pytest.fixture
def flotte(db):
    msp = {k: db.scalar(select(Msp).where(Msp.parser_key == k)) for k in ("wien_energie", "dcs")}
    anna, bert, cora = Fahrer(name="Anna Zett"), Fahrer(name="Bert Zett"), Fahrer(name="Cora Zett")
    alt = Fahrer(name="Otto Inaktiv", aktiv=False)
    pool, frei = Fahrzeug(kennzeichen="W-POOL 1", modell="Pool"), Fahrzeug(kennzeichen="W-FREI 2", modell="Frei")

    def karte(m, kennung, **mehr):
        return Ladekarte(msp=msp[m], kennung=kennung, kennung_typ=KennungTyp.kartennummer, **mehr)

    k = {"anna": karte("wien_energie", "ZK-1", bezeichnung=LANG, vertragsnummer="V-4711"),
         "pool": karte("dcs", "ZK-2"), "cora": karte("dcs", "ZK-3"), "frei": karte("wien_energie", "ZK-4"),
         "ohne": karte("wien_energie", "ZK-5"), "gast": karte("dcs", "ZK-6", ist_gastkarte=True),
         "abgelaufen": karte("wien_energie", "ZK-7")}
    db.add_all([anna, bert, cora, alt, pool, frei, *k.values()])
    db.flush()
    ab = date(2026, 1, 1)
    db.add_all([ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=pool.id, gueltig_ab=ab),
                ZuordnungFahrerFahrzeug(fahrer_id=bert.id, fahrzeug_id=pool.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k["anna"].id, fahrer_id=anna.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k["pool"].id, fahrzeug_id=pool.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k["cora"].id, fahrer_id=cora.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k["frei"].id, fahrzeug_id=frei.id, gueltig_ab=ab),
                ZuordnungKarte(ladekarte_id=k["abgelaufen"].id, fahrer_id=bert.id, gueltig_ab=ab,
                               gueltig_bis=date(2026, 6, 30))])
    db.flush()
    return {"msp": msp, "karten": k}


def _karten(zeile) -> dict[str, list[tuple[str, str | None]]]:
    return {m: [(e.karte.kennung, e.ueber) for e in liste] for m, liste in zeile.karten.items()}


def test_berechnung(db, flotte):
    d = stichtag_zuordnungen.berechnen(db, TAG)
    we, dcs = flotte["msp"]["wien_energie"].id, flotte["msp"]["dcs"].id
    assert [m.id for m in d.msp] == sorted([we, dcs], key=lambda i: db.get(Msp, i).name)
    zeilen = {z.name: z for z in d.personen}
    assert "Otto Inaktiv" not in zeilen  # nur aktive
    assert zeilen["Anna Zett"].zusatz == ["W-POOL 1"]
    assert _karten(zeilen["Anna Zett"]) == {we: [("ZK-1", None)], dcs: [("ZK-2", "W-POOL 1")]}
    assert _karten(zeilen["Bert Zett"]) == {dcs: [("ZK-2", "W-POOL 1")]}  # Pool; abgelaufene Karte fehlt
    assert _karten(zeilen["Cora Zett"]) == {dcs: [("ZK-3", None)]} and zeilen["Cora Zett"].zusatz == []
    [frei] = [z for z in d.fahrzeuge_ohne if z.name == "W-FREI 2"]
    assert _karten(frei) == {we: [("ZK-4", None)]} and frei.zusatz == ["Frei"]
    [ohne] = d.karten_ohne
    assert {k for liste in _karten(ohne).values() for k, _ in liste} >= {"ZK-5", "ZK-6", "ZK-7"}
    # vor Beginn der Zuordnungen: alles ohne Zuordnung
    vorher = stichtag_zuordnungen.berechnen(db, date(2025, 12, 31))
    assert all(z.leer for z in vorher.personen)


def test_pdf(angemeldet, db, flotte):
    import pypdfium2
    seite = angemeldet.get("/reports").text
    assert 'id="zuordnungen"' in seite and 'action="/reports/zuordnungen.pdf"' in seite
    antwort = angemeldet.get("/reports/zuordnungen.pdf?stichtag=2026-11-30")
    assert antwort.status_code == 200 and "ladefalke-zuordnungen-2026-11-30.pdf" in antwort.headers["content-disposition"]
    pdf = pypdfium2.PdfDocument(antwort.content)
    breite, hoehe = pdf[0].get_size()
    assert breite > hoehe  # A4 quer
    text = "".join(s.get_textpage().get_text_range() for s in pdf).replace("\r\n", " ").replace("\n", " ")
    for teil in ("Zuordnungen am 30.11.2026", "Anna Zett", "W-POOL 1", "über W-POOL 1", "Vertrag V-4711",
                 "Fahrzeuge ohne Fahrer:in", "W-FREI 2", "Karten ohne Zuordnung", "Gast"):
        assert teil in text, teil
    assert "Vertragsnamen" in text  # langer Name umgebrochen, nicht abgeschnitten
    assert "Otto Inaktiv" not in text
    assert angemeldet.get("/reports/zuordnungen.pdf?stichtag=kaputt").status_code == 200  # dann heute


def test_versand_mit_stichtag(rollen, db, flotte, mailbereit, schein):  # noqa: F811
    antwort = rollen["admin"].post("/reports/zuordnungen/senden", data={"stichtag": "2026-11-30",
                                                                         "adressen": "pruefung@example.org"})
    assert "Zuordnungen am 30.11.2026: 1 gesendet" in antwort.text
    [(_, nachricht)] = schein.gesendet
    assert nachricht["Subject"] == "Ladefalke – Zuordnungen am 30.11.2026"
    [anhang] = list(nachricht.iter_attachments())
    assert anhang.get_filename() == "ladefalke-zuordnungen-2026-11-30.pdf"
    assert "anbei die Zuordnungen am 30.11.2026." in nachricht.get_body(("plain",)).get_content()


def test_zeitplan_monatsletzter():
    f = versand.zeitplan_faellig
    assert f("monatlich", 0, 0, "07:00", datetime(2026, 11, 30, 7, 0))
    assert not f("monatlich", 0, 0, "07:00", datetime(2026, 11, 29, 9, 0))
    assert f("monatlich", 0, 0, "07:00", datetime(2027, 2, 28, 8, 0)) and not f("monatlich", 0, 0, "07:00", datetime(2027, 2, 28, 6, 0))
    assert f("monatlich", 0, 15, "07:00", datetime(2026, 11, 15, 7, 1))
    assert f("woechentlich", 0, 1, "07:00", datetime(2026, 11, 16, 7, 0)) and not f("aus", 0, 1, "00:00", datetime(2026, 11, 16, 9))


def test_einstellungen(angemeldet, db):
    basis = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    seite = angemeldet.get("/einstellungen").text
    assert 'name="zuordnungen_auto_an"' in seite and '<option value="0" selected>Monatsletzten</option>' in seite
    angemeldet.post("/einstellungen", data=basis | {
        "zuordnungen_auto_an": "on", "zuordnungen_auto": "monatlich", "zuordnungen_auto_tag": "0",
        "zuordnungen_auto_uhrzeit": "06:30", "aufgaben_auto_an": "on", "aufgaben_auto": "monatlich",
        "aufgaben_auto_tag": "0", "report_auto_karten_cpo": "on", "report_auto_flottenbilanz": "on"})
    e = app_einstellungen.laden(db)
    assert (e.zuordnungen_auto, e.zuordnungen_auto_tag, e.zuordnungen_auto_uhrzeit) == ("monatlich", 0, "06:30")
    assert (e.aufgaben_auto, e.aufgaben_auto_tag) == ("monatlich", 0)
    assert e.report_auto_karten_cpo and e.report_auto_flottenbilanz
    antwort = angemeldet.post("/einstellungen", data=basis | {"zuordnungen_auto_an": "on", "zuordnungen_auto_tag": "31"})
    assert antwort.status_code == 422 and "oder den Monatsletzten" in antwort.text
    assert "monatlich, am Monatsletzten 07:00" in angemeldet.get("/reports").text  # Offene Aufgaben


def test_abos_und_automatischer_versand(rollen, db, flotte, mailbereit, schein):  # noqa: F811
    viewer = rollen["viewer"]
    viktor = db.scalar(select(Benutzer).where(Benutzer.benutzername == "viktor"))
    viktor.email = "v@example.com"
    db.flush()
    for art in ("zuordnungen", "karten_cpo", "flottenbilanz"):
        viewer.post(f"/konto/report-abo/{art}", data={"abo": "on"})
    db.refresh(viktor)
    assert viktor.zuordnungen_abo and viktor.karten_cpo_abo and viktor.flottenbilanz_abo
    assert viewer.post("/konto/report-abo/unsinn", data={"abo": "on"}).status_code == 404
    seite = rollen["admin"].get("/reports").text
    eintrag = seite.split('id="dialog-zuordnungen"')[1].split(f'value="{viktor.id}"')[1].split("</li>")[0]
    assert "checked" in eintrag and "· Abo" in eintrag  # Vorauswahl folgt dem Abo
    # wie im Taktgeber: einmal je Tag
    stand = versand.stand_heute(TAG)
    assert versand.report_senden(db, "zuordnungen", stand, [viktor.id], ausgeloest="automatisch", nur_neue=True).gesendet == ["Viktor"]
    assert versand.report_senden(db, "zuordnungen", stand, [viktor.id], ausgeloest="automatisch", nur_neue=True).gesendet == []
    [eintrag] = db.scalars(select(ReportVersand).where(ReportVersand.art == "zuordnungen")).all()
    assert eintrag.ausgeloest == "automatisch" and eintrag.benutzer_id == viktor.id
