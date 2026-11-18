"""Parser unabhängig vom Kern: automatisch gefunden, über den MSP deaktivierbar, MSP für neue Parser, fehlende Parser."""
import re
from pathlib import Path

from sqlalchemy import select

from app.models import Import, ImportStatus, Ladeart, Msp
from app.parsers import PARSER
from app.parsers.basis import Ergebnis, Parser
from app.services import evse, parser_verwaltung
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)

BEKANNT = {"dcs", "elli", "enbw", "hastobe_csv", "linz_ag", "salzburg_ag", "shell_recharge", "wien_energie"}


def _msp(db, key) -> Msp:
    return db.scalar(select(Msp).where(Msp.parser_key == key))


def test_parser_automatisch_gefunden_und_eigenschaften():
    assert set(PARSER) == BEKANNT  # alle Module in app/parsers/ mit Parser-Klasse, ohne feste Liste
    assert PARSER["dcs"].abrechnung_je_karte and not PARSER["elli"].abrechnung_je_karte
    assert not PARSER["hastobe_csv"].liefert_evse and PARSER["hastobe_csv"].standard_ladeart == "privat"
    # EVSE: Eigenschaften kommen vom Parser
    assert evse.ermitteln("hastobe_csv", "AT*ABC*E1", None).evse_id is None
    assert evse.ermitteln("hastobe_csv", None, None).land == "AT"
    assert evse.ermitteln("enbw", None, None).land is None and evse.ermitteln("dcs", "AT*ABC*E1", None).land == "AT"


def test_kern_kennt_keine_parser_schluessel():
    """Außerhalb von app/parsers/ stehen keine Parser-Schlüssel im Code (Kern unabhängig von den Parsern)."""
    muster = re.compile(r"""["'](%s)["']""" % "|".join(sorted(BEKANNT)))
    treffer = [f"{p}:{n}" for p in Path("app").rglob("*.py") if "parsers" not in p.parts
               for n, zeile in enumerate(p.read_text().splitlines(), 1) if muster.search(zeile)]
    assert treffer == []


def test_migration_uebernimmt_abrechnung_je_karte(db):
    assert _msp(db, "dcs").abrechnung_je_karte and not _msp(db, "wien_energie").abrechnung_je_karte
    assert all(m.parser_aktiv for m in db.scalars(select(Msp)))


def test_deaktivierter_parser_gilt_als_nicht_vorhanden(db, monkeypatch):
    _msp(db, "elli").parser_aktiv = False
    db.flush()
    assert "elli" not in parser_verwaltung.aktive(db) and "dcs" in parser_verwaltung.aktive(db)
    assert PARSER["elli"].name not in parser_verwaltung.formate(db)
    monkeypatch.setattr(parser_verwaltung, "passende_parser", lambda pfad: ["elli"])
    assert parser_verwaltung.erkennen(db, Path("x.pdf")) is None  # erkennt nichts mehr
    from app.services.importe import vorschau_berechnen
    imp = Import(dateiname="e.pdf", datei_pfad="e.pdf", datei_hash="e" * 64, parser_key="elli",
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.vorschau, warnungen=[])
    assert "ist deaktiviert" in vorschau_berechnen(db, imp).fehler


def test_msp_fuer_neuen_parser(db, monkeypatch):
    class NeuerParser(Parser):
        key, name, msp_name = "test_neu", "Testanbieter (PDF)", "Testanbieter"
        abrechnung_je_karte, standard_ladeart = True, "privat"

        def erkennen(self, pfad):
            return False

        def parsen(self, pfad):
            return Ergebnis()

    monkeypatch.setitem(parser_verwaltung.PARSER, "test_neu", NeuerParser())
    assert parser_verwaltung.abgleichen(db) == ["Testanbieter"]
    neu = _msp(db, "test_neu")
    assert neu.name == "Testanbieter" and neu.abrechnung_je_karte and neu.standard_ladeart == Ladeart.privat
    assert parser_verwaltung.abgleichen(db) == []  # nur einmal


def test_fehlender_parser_warnung_und_verknuepfung_entfernen(angemeldet, db):
    msp = Msp(name="Alter Anbieter", parser_key="weg_parser", standard_ladeart=Ladeart.oeffentlich)
    db.add(msp)
    db.flush()
    liste = angemeldet.get("/stammdaten/msp").text
    assert "auf einen nicht installierten Parser" in liste and "⚠ fehlt</span>" in liste
    seite = angemeldet.get(f"/stammdaten/msp/{msp.id}").text
    assert "ist nicht installiert" in seite and "Parser-Verknüpfung entfernen" in seite
    assert 'aria-disabled="true" title="Zuerst den Parser deaktivieren und speichern"' in seite
    # aktiv: Entfernen abgelehnt
    assert "zuerst deaktivieren" in angemeldet.post(f"/stammdaten/msp/{msp.id}/parser-entfernen").text
    # deaktivieren (Formular speichern, ohne Haken „Parser aktiv“), dann entfernen
    angemeldet.post(f"/stammdaten/msp/{msp.id}", data={"name": msp.name, "parser_key": "weg_parser",
                                                       "standard_ladeart": "oeffentlich"})
    db.refresh(msp)
    assert not msp.parser_aktiv
    assert "Verknüpfung" in angemeldet.post(f"/stammdaten/msp/{msp.id}/parser-entfernen").text
    db.refresh(msp)
    assert msp.parser_key is None and db.get(Msp, msp.id) is not None  # MSP bleibt
    # installierter Parser: Knopf sichtbar, aber ausgegraut mit Grund; Entfernen abgelehnt
    dcs = _msp(db, "dcs")
    seite = angemeldet.get(f"/stammdaten/msp/{dcs.id}").text
    knopf = seite.split('action="/stammdaten/msp/%d/parser-entfernen"' % dcs.id)[1].split("</button>")[0]
    assert 'aria-disabled="true" title="Der Parser ist installiert' in knopf
    assert "„Installiert“ heißt: Das Parser-Modul liegt in der App (app/parsers/)" in knopf
    assert "deaktivieren allein reicht dafür nicht" in knopf
    angemeldet.post(f"/stammdaten/msp/{dcs.id}/parser-entfernen")
    db.refresh(dcs)
    assert dcs.parser_key == "dcs"


def test_msp_aendern_nur_admin(rollen, db):  # noqa: F811
    operator = rollen["operator"]
    dcs = _msp(db, "dcs")
    seite = operator.get(f"/stammdaten/msp/{dcs.id}").text  # lesen ja
    assert 'class="formular" data-nur-admin' in seite
    assert operator.post("/stammdaten/msp/neu", data={"name": "X", "standard_ladeart": "oeffentlich"}).status_code == 403
    assert operator.post(f"/stammdaten/msp/{dcs.id}", data={"name": "Y", "standard_ladeart": "oeffentlich"}).status_code == 403
    assert rollen["admin"].post("/stammdaten/msp/neu", data={"name": "Neu", "standard_ladeart": "oeffentlich",
                                                             "parser_aktiv": "on"}).status_code == 200
    assert db.scalar(select(Msp).where(Msp.name == "Neu")) is not None
