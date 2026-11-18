"""Parser Shell Recharge: anonymisiertes Fixture (Wortpositionen wie im echten PDF, erfundene Werte) und – falls
vorhanden – die echten Beispielrechnungen aus samples/ (nur Summen und Anzahlen geprüft)."""
import json
from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.models import KennungTyp
from app.parsers import PARSER, passende_parser, shell_recharge
from app.services import evse
from tests.beispiele import beispiel

FIXTURE = Path(__file__).parent / "fixtures" / "shell_recharge_beispiel.json"
SAMPLES = Path(__file__).parent.parent / "samples"


def _fixture():
    daten = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return shell_recharge.parsen_daten(daten["text"], daten["seiten"])


def test_fixture():
    e = _fixture()
    r = e.rechnung
    assert e.warnungen == []
    assert (r.nummer, r.datum, r.summe_netto) == ("25F000001", date(2025, 4, 5), D("15.64"))
    assert (r.zeitraum_von, r.zeitraum_bis) == (date(2025, 3, 27), date(2025, 4, 15))
    a, b, c = e.vorgaenge
    # kWh-Tarif mit umbrochener Adresse und Transaktionskosten (Zeitstempel = Ende)
    assert a.kartenkennung == "AT-TNM-C00000001-1" and a.kennung_typ == KennungTyp.vertragsnummer
    assert a.kartenbezeichnung == "Shell Recharge AT-TNM-C00000001-1"
    assert (a.start, a.ende, a.kwh, a.kosten_netto) == (datetime(2025, 3, 27, 10, 14), datetime(2025, 3, 27, 10, 18),
                                                         D("6.286"), D("3.75"))
    assert a.standort == "Teststraße 28, Musterort am See" and a.ladepunkt == "AT*IOY*E000001"
    assert a.rohdaten["adresse"] == "Teststraße 28 Musterort am See"  # wie in der Rechnung
    assert a.rohdaten["betreiber_name"] == "Ionity" and a.rohdaten["zusatz"][0]["bezeichnung"] == "Tr. kosten"
    assert a.rohdaten["betrag_laden"] == "3.46" and a.stromart is None and not a.kwh_geschaetzt
    # Minutentarif ohne Transaktionskosten: Ende = Start + Minuten; umbrochene EVSE-ID zusammengesetzt
    assert b.ladepunkt == "AT*SMA*EAT0000001*001AC" and b.rohdaten["betreiber_name"] == "Smatrics"
    assert (b.kwh, b.kosten_netto, b.ende) == (D("8.028"), D("2.60"), datetime(2025, 4, 8, 17, 26, 21))
    # zweite Karte auf der nächsten Seite, Betreiber mit Leerzeichen
    assert c.kartenkennung == "AT-TNM-C00000002-2" and c.rohdaten["betreiber_name"] == "Beispiel Laden GmbH"
    assert (c.kosten_netto, c.ende) == (D("9.29"), datetime(2025, 4, 15, 12, 40))
    assert evse.ermitteln("shell_recharge", c.ladepunkt, c.rohdaten).land == "DE"
    # Ort hinter der Hausnummer abgetrennt → die Verortung findet ihn über den Namen
    from app.services.ladeorte import analysieren
    assert b.standort == "Beispielgasse 5, Teststadt" and analysieren(b.standort, "AT").ort == "Teststadt"
    assert shell_recharge._adresse("Hauptplatz Testdorf") == "Hauptplatz Testdorf"  # ohne Hausnummer unverändert


def test_pruefsummen_warnen():
    daten = json.loads(FIXTURE.read_text(encoding="utf-8"))
    daten["text"] = daten["text"].replace("15.64", "15.65")
    e = shell_recharge.parsen_daten(daten["text"], daten["seiten"])
    assert any("Rechnungssumme netto 15.65" in w for w in e.warnungen)


def test_parser_registriert():
    p = PARSER["shell_recharge"]
    assert p.msp_name == "Shell Recharge" and p.liefert_evse and p.standardland is None


@pytest.mark.parametrize("pruefsumme, summe, kwh", [("7bd1612884b4", D("3.75"), D("6.286")),
                                                    ("55b646dd903d", D("2.89"), D("8.028"))])
def test_echte_beispiele(pruefsumme, summe, kwh):
    pfad = beispiel(pruefsumme)
    assert passende_parser(pfad) == ["shell_recharge"]
    e = PARSER["shell_recharge"].parsen(pfad)
    assert e.warnungen == [] and e.rechnung.summe_netto == summe and len(e.vorgaenge) == 1
    [v] = e.vorgaenge
    assert v.kwh == kwh and v.kosten_netto == summe and v.ende > v.start and v.ladepunkt


def test_import_ende_zu_ende(db):
    """Hochladen → Vorschau → Übernehmen mit einer echten Rechnung (MSP wird beim Start angelegt)."""
    from sqlalchemy import select
    from app.models import Ladekarte, Ladevorgang, Msp
    from app.services import parser_verwaltung
    from app.services.importe import bestaetigen, hochladen, vorschau_berechnen
    pfad = beispiel("55b646dd903d")
    parser_verwaltung.abgleichen(db)
    msp = db.scalar(select(Msp).where(Msp.parser_key == "shell_recharge"))
    assert msp is not None and msp.name == "Shell Recharge"
    imp = hochladen(db, pfad.name, pfad.read_bytes())
    assert imp.parser_key == "shell_recharge" and imp.msp_id == msp.id
    vs = vorschau_berechnen(db, imp)
    assert not vs.fehler and len(vs.neue) == 1 and len(vs.unbekannte_karten) == 1
    bestaetigen(db, imp, {})
    [v] = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)).all()
    assert v.betreiber_id == "AT*SMA" and v.land == "AT" and v.kosten_netto == D("2.89")
    assert v.ladeort is not None and v.ladeort.ort  # Ort erkannt (Koordinaten nur mit Ortsverzeichnis)
    karte = db.get(Ladekarte, v.ladekarte_id)
    assert karte.kennung_typ == KennungTyp.vertragsnummer and karte.bezeichnung.startswith("Shell Recharge ")
