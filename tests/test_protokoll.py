"""Änderungsprotokoll: angelegt/geändert/gelöscht mit Feldern, Sammeleinträge, sensible Felder, System, Anmeldungen,
Seite „Protokoll“ und Verlauf (nur Admins), Aufbewahrung."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import Aenderung, Benutzer, Fahrer, Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from app.services import protokoll
from app.services.aufloesung import neu_zuordnen
from app.services.importe import bestaetigen, hochladen
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)

CSV = (Path(__file__).parent / "fixtures" / "hastobe_beispiel.csv").read_bytes()


def _eintraege(db, **bed):
    abfrage = select(Aenderung).order_by(Aenderung.id)
    for feld, wert in bed.items():
        abfrage = abfrage.where(getattr(Aenderung, feld) == wert)
    return db.scalars(abfrage).all()


def test_angelegt_geaendert_geloescht(angemeldet, db):
    angemeldet.post("/stammdaten/fahrer/neu", data={"name": "Paula Probe", "aktiv": "on", "email": "p@example.com"})
    person = db.scalar(select(Fahrer).where(Fahrer.name == "Paula Probe"))
    [neu] = _eintraege(db, objekt_typ="fahrer", aktion="angelegt")
    assert neu.benutzer_name == "admin" and neu.objekt_id == person.id and neu.bezeichnung == "Paula Probe"
    assert neu.felder["Name"] == [None, "Paula Probe"] and neu.felder["E-Mail"] == [None, "p@example.com"]

    angemeldet.post(f"/stammdaten/fahrer/{person.id}", data={"name": "Paula Muster", "aktiv": "on", "email": "p@example.com"})
    [geaendert] = _eintraege(db, objekt_typ="fahrer", aktion="geaendert")
    assert geaendert.felder == {"Name": ["Paula Probe", "Paula Muster"]}  # nur was sich geändert hat

    angemeldet.post(f"/stammdaten/fahrer/{person.id}/loeschen")
    [weg] = _eintraege(db, objekt_typ="fahrer", aktion="geloescht")
    assert weg.bezeichnung == "Paula Muster" and weg.felder["Name"] == ["Paula Muster", None]


def test_verweise_mit_namen_und_system(db):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    protokoll.setzen(db, None)
    from app.models import KennungTyp, Ladekarte
    db.add(Ladekarte(msp_id=msp.id, kennung="AT-TEST-1", kennung_typ=KennungTyp.kartennummer, bezeichnung="Testkarte"))
    db.flush()
    [e] = _eintraege(db, objekt_typ="ladekarte")
    assert e.benutzer_name == "System" and e.benutzer_id is None
    assert e.felder["MSP"] == [None, msp.name] and e.bezeichnung == f"Testkarte ({msp.name})"


def test_einstellungen_ein_eintrag_ohne_geheimnisse(angemeldet, db):
    angemeldet.post("/einstellungen", data={"stichtag": "ende", "topliste_mindestmenge": "25",
                                            "strompreis_vorbelegung": "0,30", "mail_passwort": "geheim!"})
    [e] = _eintraege(db, objekt_typ="einstellung")
    assert e.felder["topliste mindestmenge"] == ["20", "25"]
    assert e.felder["mail passwort"] == [None, "geändert"] and "geheim!" not in str(e.felder)
    assert not any("plausibel" in f for f in e.felder)  # unveränderte Werte erscheinen nicht


def test_passwort_nur_als_geaendert_und_warnung(angemeldet, db):
    angemeldet.post("/konto/passwort", data={"aktuell": "test-passwort", "neu": "neues-passwort-1",
                                             "wiederholung": "neues-passwort-1"})
    [e] = _eintraege(db, objekt_typ="benutzer", aktion="geaendert")
    assert e.felder == {"Passwort": [None, "geändert"]} and protokoll.ist_warnung(e)


def test_anmeldungen(client, db):
    client.post("/login", data={"benutzer": "admin", "passwort": "falsch", "weiter_zu": "/"})
    client.post("/login", data={"benutzer": "admin", "passwort": "test-passwort", "weiter_zu": "/"})
    fehl, ok = _eintraege(db, objekt_typ="anmeldung")
    assert fehl.aktion == "anmeldung_fehlgeschlagen" and fehl.benutzer_name == "admin" and protokoll.ist_warnung(fehl)
    assert ok.aktion == "anmeldung" and ok.benutzer_id is not None and ok.kontext.startswith("von ")
    # letzte Anmeldung und Theme sind kein eigener Eintrag
    assert not _eintraege(db, objekt_typ="benutzer", aktion="geaendert")


def test_import_als_sammeleintrag(angemeldet, db):
    imp = hochladen(db, "oktober.csv", CSV)
    vs = bestaetigen(db, imp, {})
    db.commit()
    [e] = _eintraege(db, objekt_typ="import")
    assert e.aktion == "importiert" and e.objekt_id == imp.id and e.felder["Ladevorgänge"] == [None, str(len(vs.neue))]
    assert not _eintraege(db, objekt_typ="ladevorgang") and not _eintraege(db, objekt_typ="ladeort")
    karten = _eintraege(db, objekt_typ="ladekarte")  # neue Karten einzeln, mit dem Import als Zusammenhang
    assert karten and all(k.kontext == "Import oktober.csv" for k in karten)
    # Löschen des Imports: ein Eintrag mit der Anzahl
    angemeldet.post(f"/import/{imp.id}/loeschen")
    [weg] = _eintraege(db, objekt_typ="import", aktion="geloescht")
    assert weg.felder["Ladevorgänge"] == [str(len(vs.neue)), None]


def _vorgaenge(db, n):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "linz_ag"))
    imp = Import(dateiname="x.pdf", datei_pfad="x.pdf", datei_hash="d" * 64, msp_id=msp.id, ladeart=Ladeart.oeffentlich,
                 status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    vs = [Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, 1 + i % 28, 8),
                      ende=datetime(2026, 9, 1 + i % 28, 9), kwh=Decimal(10), kosten_netto=Decimal(5),
                      ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.automatisch) for i in range(n)]
    db.add_all(vs)
    db.flush()
    return vs


def test_korrektur_einzeln_masse_als_sammeleintrag(db):
    vs = _vorgaenge(db, 12)
    vs[0].land = "DE"
    db.flush()
    [e] = _eintraege(db, objekt_typ="ladevorgang")
    assert e.objekt_id == vs[0].id and e.felder == {"Land": [None, "DE"]}
    for v in vs:
        v.stromart = "AC"
    db.flush()
    sammel = _eintraege(db, objekt_typ="ladevorgang")[-1]
    assert sammel.objekt_id is None and sammel.bezeichnung == "12 Ladevorgänge" and "Stromart" in sammel.felder["Felder"][1]


def test_neuzuordnung_ein_eintrag(db):
    _vorgaenge(db, 3)
    assert neu_zuordnen(db) == 3  # ohne Karte: automatisch → keine
    [e] = _eintraege(db, objekt_typ="ladevorgang")
    assert e.bezeichnung == "3 Ladevorgänge automatisch neu zugeordnet"


def test_seite_und_verlauf_nur_admin(rollen, db):  # noqa: F811
    admin, operator = rollen["admin"], rollen["operator"]
    assert operator.get("/protokoll").status_code == 403
    operator.post("/stammdaten/fahrer/neu", data={"name": "Otto Operator", "aktiv": "on"})
    seite = admin.get("/protokoll?typ=fahrer").text
    assert "Otto Operator" in seite and "Olga" in seite
    assert "Otto Operator" not in admin.get("/protokoll?typ=msp").text
    olga = db.scalar(select(Benutzer).where(Benutzer.benutzername == "olga"))
    assert "Otto Operator" in admin.get(f"/protokoll?benutzer={olga.id}&q=otto").text
    assert "Otto Operator" not in admin.get("/protokoll?benutzer=system").text
    person = db.scalar(select(Fahrer).where(Fahrer.name == "Otto Operator"))
    assert "verlauf-abschnitt" in admin.get(f"/stammdaten/fahrer/{person.id}").text
    assert "verlauf-abschnitt" not in operator.get(f"/stammdaten/fahrer/{person.id}").text
    assert 'href="/protokoll"' in admin.get("/").text and "Protokoll 🔒" in operator.get("/").text


def test_aufraeumen(db):
    protokoll.eintragen(db, "erstellt", "backup", "alt")
    protokoll.eintragen(db, "erstellt", "backup", "neu")
    db.flush()
    alt = _eintraege(db, bezeichnung="alt")[0]
    alt.zeitpunkt = datetime.now(timezone.utc) - timedelta(days=4 * 365)
    db.flush()
    assert protokoll.aufraeumen(db, 3) == 1
    assert [e.bezeichnung for e in _eintraege(db, objekt_typ="backup")] == ["neu"]
