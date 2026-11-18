"""Einmalige Korrektur der DCS-Zeiten aus Rechnungen ohne „(UTC)“ im Spaltenkopf (werkzeuge/dcs_zeiten_korrigieren)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Import, ImportStatus, Ladeart, Ladevorgang, Msp, ZuordnungQuelle
from werkzeuge import dcs_zeiten_korrigieren as werkzeug


def test_korrektur(db, monkeypatch):
    msp = db.scalar(select(Msp).where(Msp.parser_key == "dcs"))
    alt = Import(dateiname="alt.pdf", datei_pfad="alt.pdf", datei_hash="a" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    neu = Import(dateiname="neu.pdf", datei_pfad="neu.pdf", datei_hash="b" * 64, msp_id=msp.id,
                 ladeart=Ladeart.oeffentlich, status=ImportStatus.abgeschlossen)
    db.add_all([alt, neu])
    db.flush()

    def vorgang(imp, start, roh):  # so, wie der alte Parser es gespeichert hat: Rechnungszeit als UTC umgerechnet
        v = Ladevorgang(import_id=imp.id, msp_id=msp.id, start=start, ende=start.replace(minute=start.minute + 30),
                        kwh=Decimal(10), kosten_netto=Decimal(5), ladeart=Ladeart.oeffentlich,
                        zuordnung_quelle=ZuordnungQuelle.keine, rohdaten={"zeit_utc": roh})
        db.add(v)
        return v

    v_alt = vorgang(alt, datetime(2025, 6, 2, 15, 9), "02.06.2025 13:09")  # (UTC)-Rechnung: richtig umgerechnet
    v_neu = vorgang(neu, datetime(2025, 12, 2, 14, 9), "02.12.2025 13:09")  # ohne (UTC): 1 h zu spät
    v_sommer = vorgang(neu, datetime(2025, 7, 1, 2, 10), "01.07.2025 00:10")  # Sommer: 2 h, Tag wechselt zurück
    db.flush()
    texte = {"alt.pdf": "Datum/Uhrzeit (UTC) Adresse Menge", "neu.pdf": "Datum/Uhrzeit Adresse Menge"}
    monkeypatch.setattr(werkzeug, "datei", lambda imp: imp.datei_pfad)
    monkeypatch.setattr(werkzeug, "pdf_text", lambda pfad: texte[pfad])

    geaendert = werkzeug.korrigieren(db, ausfuehren=True)
    for v in (v_alt, v_neu, v_sommer):
        db.refresh(v)
    assert sorted(geaendert) == sorted([v_neu.id, v_sommer.id])
    assert (v_alt.start, v_neu.start, v_neu.ende) == (datetime(2025, 6, 2, 15, 9), datetime(2025, 12, 2, 13, 9),
                                                      datetime(2025, 12, 2, 13, 39))
    assert v_sommer.start == datetime(2025, 7, 1, 0, 10)
    assert "zeit_ortszeit" in v_neu.rohdaten and "zeit_utc" not in v_neu.rohdaten and "zeit_utc" in v_alt.rohdaten
    assert werkzeug.korrigieren(db, ausfuehren=True) == []  # zweiter Lauf: nichts mehr zu tun
