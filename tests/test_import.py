"""Import-Ablauf: Hochladen, Vorschau, Bestätigen, Duplikate, Fahrzeugauflösung, Löschen."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import (Fahrer, Fahrzeug, Gebuehr, ImportStatus, KennungTyp, Ladekarte, Ladevorgang, Msp,
                        ZuordnungFahrerFahrzeug, ZuordnungKarte, ZuordnungQuelle)
from app.parsers.wien_energie import WienEnergieParser, parsen_text
from app.services.importe import (ImportFehler, KartenEntscheidung, bestaetigen, datei, hochladen, loeschen,
                                  vorschau_berechnen)

FIXTURES = Path(__file__).parent / "fixtures"
CSV = (FIXTURES / "hastobe_beispiel.csv").read_bytes()


def _msp(db, key):
    return db.scalar(select(Msp).where(Msp.parser_key == key))


def _anzahl(db, model, *bedingungen):
    return db.scalar(select(func.count()).select_from(model).where(*bedingungen))


@pytest.fixture
def max_mit_fahrzeug(db):
    """Karte „Muster, Max“ ist personengebunden; die Person fährt ab 1.1.2026 das Fahrzeug W-TEST 1."""
    karte = Ladekarte(msp=_msp(db, "hastobe_csv"), kennung="Muster, Max", kennung_typ=KennungTyp.kartenlabel)
    fahrer, fz = Fahrer(name="Max Muster"), Fahrzeug(kennzeichen="W-TEST 1")
    db.add_all([karte, fahrer, fz])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=karte.id, fahrer_id=fahrer.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=fahrer.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1))])
    db.flush()
    return karte, fz


def test_hochladen_erkennt_parser_und_msp(db):
    imp = hochladen(db, "oktober.csv", CSV)
    assert imp.parser_key == "hastobe_csv"
    assert imp.msp.parser_key == "hastobe_csv"
    assert imp.ladeart.value == "privat"
    assert imp.strompreis_netto_kwh == Decimal("0.30")
    assert imp.status == ImportStatus.vorschau
    assert datei(imp).read_bytes() == CSV
    # gleiche Datei nochmal → dieselbe Vorschau
    assert hochladen(db, "kopie.csv", CSV).id == imp.id


def test_vorschau(db, max_mit_fahrzeug):
    imp = hochladen(db, "oktober.csv", CSV)
    vs = vorschau_berechnen(db, imp)
    assert vs.fehler is None
    assert len(vs.neue) == 6
    assert vs.vorgaenge[0].kosten == Decimal("20.13")  # 67,088 kWh × 0,30 = 20,1264
    assert vs.summe_kwh == Decimal("166.689")
    assert vs.summe_kosten == sum(x.kosten for x in vs.vorgaenge)
    bekannt = [k for k in vs.karten if k.karte]
    assert [k.kennung for k in bekannt] == ["Muster, Max"]
    assert [fz.kennzeichen for _, fz in bekannt[0].ziele.values()] == ["W-TEST 1"]
    assert bekannt[0].preis == bekannt[0].kosten / bekannt[0].kwh
    assert vs.vorgaenge[0].fahrer == ["Max Muster"]  # personengebundene Karte → Person der Karte
    assert vs.vorgaenge[0].preis == Decimal("20.13") / Decimal("67.088")
    assert {k.kennung: k.gast for k in vs.unbekannte_karten} == {
        "Beispiel, Erika": False, "GAST / Gast Anna": True, "GAST / Gästechip X": True}


def test_fahrer_zum_zeitpunkt_des_ladevorgangs(db):
    """Fahrzeuggebundene Karte, Fahrerwechsel am 5.10.: jeder Vorgang zeigt, wer an seinem Ladeende fuhr."""
    karte = Ladekarte(msp=_msp(db, "hastobe_csv"), kennung="Muster, Max", kennung_typ=KennungTyp.kartenlabel)
    fz, alt, neu = Fahrzeug(kennzeichen="W-WECHSEL 1"), Fahrer(name="Alte Fahrerin"), Fahrer(name="Neuer Fahrer")
    db.add_all([karte, fz, alt, neu])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=alt.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1),
                                        gueltig_bis=date(2026, 10, 4)),
                ZuordnungFahrerFahrzeug(fahrer_id=neu.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 10, 5))])
    db.flush()
    vs = vorschau_berechnen(db, hochladen(db, "oktober.csv", CSV))
    fahrer = {x.v.externe_id: x.fahrer for x in vs.vorgaenge if x.fahrzeug}
    assert fahrer == {"900001": ["Alte Fahrerin"], "900005": ["Neuer Fahrer"]}  # Ladeende 1.10. bzw. 7.10.


def test_vorschau_seite_mit_fahrer_und_preis(angemeldet, db, max_mit_fahrzeug):
    antwort = angemeldet.post("/import", files={"datei_upload": ("oktober.csv", CSV, "text/csv")})
    assert "W-TEST 1</a> <small>(Max Muster)</small>" in antwort.text  # Ladevorgänge: Fahrer:in beim Laden
    karte, fz = max_mit_fahrzeug
    zuordnung = antwort.text.split("Zuordnung (Vorschlag)")[1]
    assert f'Max Muster</a> →' in zuordnung and f'href="/stammdaten/fahrzeuge/{fz.id}">W-TEST 1</a>' in zuordnung  # Karten
    assert antwort.text.count("(Max Muster)") == 2  # nur die zwei Vorgänge, nicht die Kartentabelle
    assert "0,3000" in antwort.text  # Ø €/kWh = Strompreis bei der has.to.be-CSV
    assert "Ø €/kWh" in antwort.text


def test_bestaetigen(db, max_mit_fahrzeug):
    karte_max, fz = max_mit_fahrzeug
    imp = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, imp, {"Beispiel, Erika": KartenEntscheidung(anlegen=False)})
    assert imp.status == ImportStatus.abgeschlossen
    assert imp.importiert_am is not None
    assert imp.abrechnungszeitraum_von == date(2026, 10, 1)

    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)).all()
    assert len(vorgaenge) == 6
    assert all(v.ladeart.value == "privat" for v in vorgaenge)
    max_v = [v for v in vorgaenge if v.ladekarte_id == karte_max.id]
    assert len(max_v) == 2 and all(v.fahrzeug_id == fz.id for v in max_v)
    assert all(v.zuordnung_quelle == ZuordnungQuelle.automatisch for v in max_v)
    # ignorierte Karte: ohne Karte importiert, Rohkennung bleibt
    erika = [v for v in vorgaenge if v.roh_kartenkennung == "Beispiel, Erika"]
    assert len(erika) == 2 and all(v.ladekarte_id is None and v.fahrzeug_id is None for v in erika)
    assert all(v.zuordnung_quelle == ZuordnungQuelle.keine for v in erika)
    # Gastkarten neu angelegt, keinem Fahrzeug zugeordnet
    gaeste = db.scalars(select(Ladekarte).where(Ladekarte.kennung.like("GAST / %"))).all()
    assert len(gaeste) == 2 and all(k.ist_gastkarte for k in gaeste)
    assert _anzahl(db, Ladekarte, Ladekarte.kennung == "Beispiel, Erika") == 0
    assert vorgaenge[0].rohdaten["Id"] in {"900001", "900002", "900003", "900004", "900005", "900006"}

    with pytest.raises(ImportFehler, match="bereits abgeschlossen"):
        bestaetigen(db, imp, {})
    with pytest.raises(ImportFehler, match="bereits importiert"):
        hochladen(db, "nochmal.csv", CSV)


def test_ueberlappende_dateien_werden_dedupliziert(db):
    erster = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, erster, {})
    # Folgedatei: enthält Vorgang 900006 erneut plus einen neuen
    text = CSV.decode("utf-8-sig").splitlines()
    neu = text[6].replace("900006", "900007").replace("2026-10-31", "2026-11-02")
    zweite_datei = ("\r\n".join([text[0], text[6], neu]) + "\r\n").encode("utf-8-sig")
    zweiter = hochladen(db, "november.csv", zweite_datei)
    vs = vorschau_berechnen(db, zweiter)
    assert [x.duplikat for x in vs.vorgaenge] == [True, False]
    assert any("bereits importiert" in w for w in vs.warnungen)
    bestaetigen(db, zweiter, {})
    assert _anzahl(db, Ladevorgang, Ladevorgang.import_id == zweiter.id) == 1


def test_ohne_strompreis_nicht_bestaetigen(db):
    imp = hochladen(db, "oktober.csv", CSV)
    imp.strompreis_netto_kwh = None
    assert any("Strompreis" in w for w in vorschau_berechnen(db, imp).warnungen)
    with pytest.raises(ImportFehler, match="Strompreis"):
        bestaetigen(db, imp, {})


def test_loeschen_macht_import_rueckgaengig(db):
    imp = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, imp, {})
    import_id, pfad = imp.id, datei(imp)
    assert loeschen(db, imp) == pfad
    assert _anzahl(db, Ladevorgang, Ladevorgang.import_id == import_id) == 0
    # neu angelegte Karten bleiben als Stammdaten erhalten; Datei kann erneut importiert werden
    assert hochladen(db, "oktober.csv", CSV).status == ImportStatus.vorschau


def test_wien_energie_gebuehren_ueber_vertragsnummer(db, monkeypatch):
    """Gebühr eines Vertrags ohne Ladevorgang wird über die Vertragsnummer der Karte zugeordnet."""
    text = (FIXTURES / "wien_energie_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(WienEnergieParser, "parsen", lambda self, pfad: parsen_text(text))
    we = _msp(db, "wien_energie")
    karte_b = Ladekarte(msp=we, kennung="100002", kennung_typ=KennungTyp.kartennummer,
                        vertragsnummer="AT-VIE-C00000002-B")
    db.add(karte_b)
    db.flush()

    imp = hochladen(db, "rechnung.pdf", b"%PDF-1.4 Testinhalt")
    imp.parser_key, imp.msp_id = "wien_energie", we.id  # Erkennung braucht ein echtes PDF
    vs = vorschau_berechnen(db, imp)
    assert not [w for w in vs.warnungen if "Rechnungssumme" in w]  # Plausibilität passt
    bestaetigen(db, imp, {})

    gebuehren = db.scalars(select(Gebuehr).where(Gebuehr.import_id == imp.id).order_by(Gebuehr.id)).all()
    assert [g.betrag_netto for g in gebuehren] == [Decimal("8.25")] * 3
    assert gebuehren[1].ladekarte_id == karte_b.id
    neue = db.scalars(select(Ladekarte).where(Ladekarte.msp_id == we.id, Ladekarte.kennung == "100001")).one()
    assert neue.vertragsnummer == "AT-VIE-C00000001-A"
    assert neue.bezeichnung == "Wien Energie AT-VIE-C00000001-A"
    assert gebuehren[0].ladekarte_id == neue.id
    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)).all()
    assert len(vorgaenge) == 4 and sum(1 for v in vorgaenge if v.kwh_geschaetzt) == 3
    assert sum(v.kosten_netto for v in vorgaenge) + sum(g.betrag_netto for g in gebuehren) == Decimal("71.47")
    assert all(v.ladeart.value == "oeffentlich" for v in vorgaenge)


# ------------------------------------------------------------------ Oberfläche

def test_oberflaeche_ablauf(angemeldet, db):
    antwort = angemeldet.post("/import", files={"datei_upload": ("oktober.csv", CSV, "text/csv")})
    assert antwort.status_code == 200
    assert "Vorschau: oktober.csv" in antwort.text
    assert "GAST / Gast Anna" in antwort.text
    import_id = int(str(antwort.url).rstrip("/").rsplit("/", 1)[1])

    # Strompreis ändern → Kosten neu berechnet
    antwort = angemeldet.post(f"/import/{import_id}/einstellungen", data={
        "parser_key": "hastobe_csv", "msp_id": str(_msp(db, "hastobe_csv").id), "ladeart": "privat",
        "strompreis": "0,25"})
    assert "16,77 €" in antwort.text  # 67,088 kWh × 0,25

    # Bestätigen: erste unbekannte Karte ignorieren, Rest anlegen
    antwort = angemeldet.post(f"/import/{import_id}/bestaetigen", data={
        "karte_1_kennung": "Beispiel, Erika", "karte_1_aktion": "ignorieren"})
    assert antwort.status_code == 200 and str(antwort.url).endswith("/import#abschluss")  # eine Datei: zurück zur Import-Seite
    assert "oktober.csv übernommen: 6 Ladevorgänge" in antwort.text
    abschluss = antwort.text.split('id="abschluss"')[1].split("</article>")[0]
    assert "✓ Import abgeschlossen" in abschluss and "1 Datei übernommen" in abschluss
    assert "<strong>6</strong> Ladevorgänge" in abschluss and "Jetzt zuordnen (Aufgaben)" in abschluss
    assert 'aria-current="step"' in antwort.text
    assert 'class="neu-importiert"' in antwort.text
    assert "Import löschen" in angemeldet.get(f"/import/{import_id}").text
    assert 'data-ausblenden="20"' in abschluss.split(">")[0] + ">"  # blendet sich nach 20 s aus
    # nur einmal: beim Neuladen ist der Abschluss weg; Ausblenden-Link schadet nicht
    assert 'id="abschluss"' not in angemeldet.get("/import").text
    assert 'id="abschluss"' not in angemeldet.get("/import?abschluss=aus").text

    assert angemeldet.get("/import").status_code == 200
    antwort = angemeldet.get(f"/ladevorgaenge?import_id={import_id}&fahrzeug_id=ohne&von=2026-10-01&bis=2026-10-31")
    assert antwort.status_code == 200
    assert "<strong>6</strong> Vorgänge" in antwort.text
    assert angemeldet.get(f"/import/{import_id}/datei").content == CSV
    antwort = angemeldet.get(f"/import/{import_id}/datei?anzeigen=1")  # im Browser öffnen statt herunterladen
    assert antwort.content == CSV and antwort.headers["content-disposition"].startswith("inline")

    antwort = angemeldet.post("/import", files={"datei_upload": ("oktober.csv", CSV, "text/csv")})
    assert antwort.status_code == 422
    assert "bereits importiert" in antwort.text

    antwort = angemeldet.post(f"/import/{import_id}/loeschen")
    assert "Import gelöscht" in antwort.text
    assert _anzahl(db, Ladevorgang, Ladevorgang.import_id == import_id) == 0


def test_unbekanntes_format(angemeldet):
    antwort = angemeldet.post("/import", files={"datei_upload": ("x.txt", b"irgendwas", "text/plain")})
    assert antwort.status_code == 200
    assert "Dateiformat nicht erkannt" in antwort.text
    assert antwort.text.count("Dateiformat nicht erkannt") == 1


def test_dublette_trotz_geaenderter_schaetzung(db, monkeypatch):
    """Ohne externe ID wird über Karte, Start und Ende erkannt – geschätzte kWh dürfen sich unterscheiden."""
    from app.services.aufloesung import neu_zuordnen_karte
    text = (FIXTURES / "wien_energie_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(WienEnergieParser, "parsen", lambda self, pfad: parsen_text(text))
    we = _msp(db, "wien_energie")
    erster = hochladen(db, "a.pdf", b"%PDF-1.4 a")
    erster.parser_key, erster.msp_id = "wien_energie", we.id
    bestaetigen(db, erster, {})
    karte = db.scalars(select(Ladekarte).where(Ladekarte.msp_id == we.id, Ladekarte.kennung == "100001")).one()
    fz = Fahrzeug(kennzeichen="W-DUP 1")
    db.add(fz)
    db.flush()
    db.add(ZuordnungKarte(ladekarte_id=karte.id, fahrzeug_id=fz.id, gueltig_ab=date(2026, 1, 1)))
    assert neu_zuordnen_karte(db, karte.id, date(2026, 1, 1), None) == 3  # Fahrzeug neu ermittelt

    zweiter = hochladen(db, "b.pdf", b"%PDF-1.4 b")  # andere Datei, gleicher Inhalt
    zweiter.parser_key, zweiter.msp_id = "wien_energie", we.id
    vs = vorschau_berechnen(db, zweiter)
    assert all(x.duplikat for x in vs.vorgaenge)
    assert any("bereits mit der Datei" in w for w in vs.warnungen)  # gleiche Rechnungsnummer


# ------------------------------------------------------------------ Mehrfach-Upload und roter Punkt

def _zweite_csv() -> bytes:
    """Andere Datei (anderer Hash) mit einem neuen Vorgang."""
    text = CSV.decode("utf-8-sig").splitlines()
    neu = text[6].replace("900006", "900099").replace("2026-10-31", "2026-11-02")
    return ("\r\n".join([text[0], neu]) + "\r\n").encode("utf-8-sig")


def _id_aus(antwort) -> int:
    return int(str(antwort.url).rstrip("/").rsplit("/", 1)[1])


def test_mehrfach_upload_und_der_reihe_nach(angemeldet, db):
    antwort = angemeldet.get("/")
    assert 'class="punkt"' not in antwort.text  # nichts offen
    assert 'class="import-ablage"' in antwort.text  # Dateien auf den Menüpunkt „Import“ ziehen
    assert 'id="datei-liste"' in angemeldet.get("/import").text

    antwort = angemeldet.post("/import", files=[
        ("datei_upload", ("oktober.csv", CSV, "text/csv")),
        ("datei_upload", ("november.csv", _zweite_csv(), "text/csv")),
        ("datei_upload", ("oktober-kopie.csv", CSV, "text/csv")),  # gleiche Datei nochmal
        ("datei_upload", ("notiz.txt", b"irgendwas", "text/plain")),
    ])
    assert str(antwort.url).endswith("/import")
    text = antwort.text
    assert "oktober.csv: Vorschau angelegt (has.to.be CSV (Wallboxen))" in text  # ohne Datenbanknummer
    assert "oktober-kopie.csv: liegt bereits als offene Vorschau vor" in text
    assert "notiz.txt: Vorschau angelegt" in text and "Format nicht erkannt" in text
    assert "Offene Vorschauen (3)" in text
    assert text.count('class="punkt"') == 1  # nur „Import“, noch nichts importiert
    erste = int(text.split("Der Reihe nach prüfen")[0].rsplit('href="/import/', 1)[1].split('"')[0])
    assert "3 Dateien offen" in text  # Schritt 2 der Schrittanzeige

    antwort = angemeldet.get(f"/import/{erste}")
    assert "Datei 1 von 3" in antwort.text and "später zuordnen</span>" in antwort.text
    assert "noch ohne Zuordnung</strong>" in antwort.text and "danach weiter mit der nächsten Datei" in antwort.text
    assert f'href="/import/{erste}/datei?anzeigen=1" target="_blank"' in antwort.text  # Originaldatei, neuer Tab

    # Bestätigen → automatisch zur nächsten offenen Vorschau
    antwort = angemeldet.post(f"/import/{erste}/bestaetigen", data={})
    zweite = _id_aus(antwort)
    assert zweite != erste
    assert "übernommen" in antwort.text and "Weiter mit der nächsten Datei" in antwort.text
    assert "Datei 2 von 3" in antwort.text  # Zählung über den ganzen Durchgang
    assert antwort.text.count('class="punkt"') == 2  # jetzt auch „Nicht zugeordnet“ (Karten ohne Zuordnung)

    # Verwerfen → zur letzten offenen Vorschau
    antwort = angemeldet.post(f"/import/{zweite}/loeschen")
    dritte = _id_aus(antwort)
    assert dritte not in (erste, zweite) and "verworfen" in antwort.text
    assert "Datei 3 von 3" in antwort.text  # Format nicht erkannt: Fehlermeldung statt Bestätigen-Block

    # Letzte verwerfen → zurück zur Import-Seite, kein Punkt mehr bei „Import“
    antwort = angemeldet.post(f"/import/{dritte}/loeschen")
    assert str(antwort.url).endswith("/import#abschluss")
    assert "Offene Vorschauen" not in antwort.text
    abschluss = antwort.text.split('id="abschluss"')[1].split("</article>")[0]
    assert "1 Datei übernommen, 2 verworfen" in abschluss and "oktober-kopie.csv" not in abschluss  # Duplikat: keine eigene Vorschau
    assert antwort.text.count('class="punkt"') == 1  # nur noch „Nicht zugeordnet“


def test_mehrfach_upload_mit_bereits_importierter_datei(angemeldet, db):
    bestaetigen(db, hochladen(db, "oktober.csv", CSV), {})
    antwort = angemeldet.post("/import", files=[
        ("datei_upload", ("oktober.csv", CSV, "text/csv")),
        ("datei_upload", ("november.csv", _zweite_csv(), "text/csv"))])
    assert "oktober.csv: Diese Datei wurde bereits importiert" in antwort.text
    assert "november.csv: Vorschau angelegt" in antwort.text


def test_upload_ohne_datei(angemeldet):
    antwort = angemeldet.post("/import", files=[("datei_upload", ("", b"", "application/octet-stream"))])
    assert antwort.status_code == 422 and "mindestens eine Datei" in antwort.text


def test_vorschau_zeigt_person_und_fahrzeug_je_karte(db, monkeypatch):
    """Personenkarte: Vorschlag „Person → Fahrzeug“; Person ohne Fahrzeug am Ladetag: Person ohne Fahrzeug."""
    text = (FIXTURES / "wien_energie_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(WienEnergieParser, "parsen", lambda self, pfad: parsen_text(text))
    we = _msp(db, "wien_energie")
    mit_fz = Ladekarte(msp_id=we.id, kennung="100001", kennung_typ=KennungTyp.vertragsnummer)
    ohne_fz = Ladekarte(msp_id=we.id, kennung="100003", kennung_typ=KennungTyp.vertragsnummer)
    anna, bernd, fz = Fahrer(name="Anna Vorschau"), Fahrer(name="Bernd Vorschau"), Fahrzeug(kennzeichen="W-VOR 1")
    db.add_all([mit_fz, ohne_fz, anna, bernd, fz])
    db.flush()
    db.add_all([ZuordnungKarte(ladekarte_id=mit_fz.id, fahrer_id=anna.id, gueltig_ab=date(2020, 1, 1)),
                ZuordnungKarte(ladekarte_id=ohne_fz.id, fahrer_id=bernd.id, gueltig_ab=date(2020, 1, 1)),
                ZuordnungFahrerFahrzeug(fahrer_id=anna.id, fahrzeug_id=fz.id, gueltig_ab=date(2020, 1, 1))])
    db.flush()
    imp = hochladen(db, "v.pdf", b"%PDF-1.4 vorschau")
    imp.parser_key, imp.msp_id = "wien_energie", we.id
    karten = {k.kennung: k for k in vorschau_berechnen(db, imp).karten}
    assert list(karten["100001"].ziele.values()) == [(anna, fz)]
    assert list(karten["100003"].ziele.values()) == [(bernd, None)]


def test_bestaetigen_speichert_evse_und_land(db, monkeypatch):
    text = (FIXTURES / "wien_energie_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(WienEnergieParser, "parsen", lambda self, pfad: parsen_text(text))
    we = _msp(db, "wien_energie")
    imp = hochladen(db, "e.pdf", b"%PDF-1.4 evse")
    imp.parser_key, imp.msp_id = "wien_energie", we.id
    bestaetigen(db, imp, {})
    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)).all()
    assert vorgaenge and all(v.land == "AT" for v in vorgaenge)
    assert all(v.evse_id and v.evse_id.startswith("AT*") and v.betreiber_id == v.evse_id[:6] for v in vorgaenge)


def test_bestaetigen_ordnet_ladeorte_zu(db, monkeypatch):
    text = (FIXTURES / "wien_energie_beispiel.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(WienEnergieParser, "parsen", lambda self, pfad: parsen_text(text))
    we = _msp(db, "wien_energie")
    imp = hochladen(db, "o.pdf", b"%PDF-1.4 ladeorte")
    imp.parser_key, imp.msp_id = "wien_energie", we.id
    bestaetigen(db, imp, {})
    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)).all()
    assert all(v.ladeort is not None and v.ladeort.plz for v in vorgaenge)
    # zwei Ladepunkte an derselben Adresse („2000 Teststadt, Autobahn A1 KM 1“) → ein Ladeort
    assert len({v.ladeort_id for v in vorgaenge}) < len(vorgaenge)


def test_letzte_datei_hinweis(angemeldet):
    """Bei der einzigen bzw. letzten offenen Vorschau sagt der Bestätigen-Block, dass danach Schluss ist."""
    antwort = angemeldet.post("/import", files={"datei_upload": ("oktober.csv", CSV, "text/csv")})
    assert "letzte Datei – danach ist der Import abgeschlossen" in antwort.text
    assert "Datei 1 von 1" not in antwort.text  # Zähler nur bei mehreren Dateien
