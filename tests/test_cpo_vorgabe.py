"""CPO-Vorgabe je MSP: auch ohne öffentliche Kennung, beim Import und nachträglich; Rechnungswerte bleiben."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from app.services import cpo
from app.services.auswertung import auswerten, zeitraum_frei
from app.services import app_einstellungen
from app.services.evse import cpo_land
from app.services.importe import bestaetigen, hochladen

CSV = (Path(__file__).parent / "fixtures" / "hastobe_beispiel.csv").read_bytes()  # ohne EVSE-ID


def _msp(db, key):
    return db.scalar(select(Msp).where(Msp.parser_key == key))


def test_normieren_und_land():
    assert cpo.normieren(" at*xyz ") == "AT*XYZ"
    assert cpo.normieren("AT*XYZ*E123*1") == "AT*XYZ"            # aus einer ganzen EVSE-ID
    assert cpo.normieren("  Eigene   Ladestationen ") == "Eigene Ladestationen"
    assert cpo.normieren("") is None
    assert cpo_land("AT*XYZ") == "AT" and cpo_land("Eigene Ladestationen") is None


def test_vorgabe_beim_import(db):
    msp = _msp(db, "hastobe_csv")
    msp.cpo_standard = "Eigene Ladestationen"
    imp = hochladen(db, "oktober.csv", CSV)
    bestaetigen(db, imp, {})
    werte = {(v.betreiber_id, v.betreiber_quelle) for v in db.scalars(
        select(Ladevorgang).where(Ladevorgang.import_id == imp.id))}
    assert werte == {("Eigene Ladestationen", "vorgabe")}


def test_vorgabe_beim_msp_nachtraeglich(angemeldet, db):
    msp = _msp(db, "linz_ag")
    imp = Import(dateiname="c.pdf", datei_pfad="c.pdf", datei_hash="c" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add(imp)
    db.flush()
    vs = []
    for tag, (betreiber, quelle) in enumerate([(None, None), ("AT*ALT", "vorgabe"), ("AT*LAG", "rechnung")], start=1):
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=datetime(2026, 9, tag, 8), ende=datetime(2026, 9, tag, 9),
                        kwh=Decimal(30), kosten_netto=Decimal(15), betreiber_id=betreiber, betreiber_quelle=quelle,
                        ladeart=Ladeart.oeffentlich, zuordnung_quelle=ZuordnungQuelle.keine)
        db.add(v)
        vs.append(v)
    db.flush()

    # ohne Vorgabe: nur Hinweis
    text = angemeldet.get(f"/stammdaten/msp/{msp.id}").text
    assert "1 Ladevorgang dieses" in text and 'form="cpo-anwenden"' not in text

    # Vorgabe speichern (normiert) und anwenden: ersetzt fehlende und frühere Vorgaben, nicht die Rechnung
    angemeldet.post(f"/stammdaten/msp/{msp.id}", data={"name": msp.name, "parser_key": "linz_ag",
                                                       "standard_ladeart": "oeffentlich", "cpo_standard": "at*lin"})
    db.refresh(msp)
    assert msp.cpo_standard == "AT*LIN"
    text = angemeldet.get(f"/stammdaten/msp/{msp.id}").text
    # Hinweis und Knopf direkt unter dem CPO-Feld, noch vor „Speichern“
    assert text.index('name="cpo_standard"') < text.index("2 Ladevorgänge dieses") \
        < text.index('form="cpo-anwenden"') < text.index('<button type="submit">Speichern</button>')
    assert "<td>AT*LIN</td>" in angemeldet.get("/stammdaten/msp").text
    angemeldet.post(f"/stammdaten/msp/{msp.id}/cpo-anwenden")
    for v in vs:
        db.refresh(v)
    assert [(v.betreiber_id, v.betreiber_quelle) for v in vs] == [
        ("AT*LIN", "vorgabe"), ("AT*LIN", "vorgabe"), ("AT*LAG", "rechnung")]
    assert "Vorgabe des" in angemeldet.get(f"/ladevorgaenge/{vs[0].id}").text

    # Name ohne Kennung: in der Auswertung ohne Land
    msp.cpo_standard = "Eigene Ladestationen"
    cpo.vorgabe_anwenden(db, msp.cpo_standard, Ladevorgang.msp_id == msp.id)
    db.flush()
    a = auswerten(db, zeitraum_frei(datetime(2026, 9, 1).date(), datetime(2026, 9, 30).date()),
                  app_einstellungen.laden(db))
    assert dict(a.cpo)["Eigene Ladestationen"].anzahl == 2
    text = angemeldet.get("/auswertungen?art=frei&von=2026-09-01&bis=2026-09-30").text
    assert ">Eigene Ladestationen</a></td>\n        <td></td>" in text
