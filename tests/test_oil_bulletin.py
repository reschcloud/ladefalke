"""Spritpreise aus dem EU Weekly Oil Bulletin: Monatsmittel je Land und Kraftstoff, Übernahme als Spritpreise (ab dem
1. des Monats), Download auf Knopfdruck bzw. wöchentlich, Hochladen der Datei. Testdatei mit erfundenen Werten."""
import io
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import openpyxl
import pytest
from sqlalchemy import select

from app.models import Spritpreis
from app.services import app_einstellungen, oil_bulletin


def _datei(wochen: list[tuple[date, float, float]], land: str = "AT") -> bytes:
    """Aufbau wie die Historien-Datei: Kopfzeile mit Spaltennamen, zwei Beschreibungszeilen, dann je Montag eine Zeile
    (neueste zuerst); Preise in € je 1.000 l."""
    mappe = openpyxl.Workbook()
    blatt = mappe.active
    blatt.title = "Prices with taxes"
    blatt.append(["Consumer prices …", "CTR", "EU_price_with_tax_euro95", "EU_price_with_tax_diesel", "CTR",
                  f"{land}_price_with_tax_euro95", f"{land}_price_with_tax_diesel"])
    blatt.append([None, None, "Euro-super 95", "Diesel", None, "Euro-super 95", "Diesel"])
    blatt.append(["Date", None, "1000 l", "1000 l", None, "1000 l", "1000 l"])
    for tag, benzin, diesel in sorted(wochen, reverse=True):
        blatt.append([datetime.combine(tag, datetime.min.time()), "EU_", 1700.0, 1800.0, f"{land}_", benzin, diesel])
    mappe.create_sheet("Prices wo taxes")
    puffer = io.BytesIO()
    mappe.save(puffer)
    return puffer.getvalue()


WOCHEN = [(date(2026, 9, 7), 1600.0, 1900.0), (date(2026, 9, 14), 1620.0, 1950.0),
          (date(2026, 10, 5), 1700.0, 2100.0), (date(2026, 10, 12), 1710.0, 2150.0),
          (date(2019, 6, 3), 1300.0, 1250.0)]  # vor 2020: nicht übernommen


def test_monatsmittel():
    mittel = oil_bulletin.monatsmittel(_datei(WOCHEN), "AT", "diesel")
    assert mittel[date(2026, 9, 1)] == D("1.925") and mittel[date(2026, 10, 1)] == D("2.125")
    assert oil_bulletin.monatsmittel(_datei(WOCHEN), "AT", "euro95")[date(2026, 9, 1)] == D("1.610")
    with pytest.raises(oil_bulletin.OilBulletinFehler, match="DE_price_with_tax_diesel"):
        oil_bulletin.monatsmittel(_datei(WOCHEN), "DE", "diesel")
    with pytest.raises(oil_bulletin.OilBulletinFehler, match="Excel"):
        oil_bulletin.monatsmittel(b"kein xlsx", "AT", "diesel")


def test_faellig():
    jetzt = datetime(2026, 11, 23, 12)
    assert oil_bulletin.faellig({}, jetzt)
    assert not oil_bulletin.faellig({"zeit": (jetzt - timedelta(days=6)).isoformat(), "ok": True}, jetzt)
    assert oil_bulletin.faellig({"zeit": (jetzt - timedelta(days=7)).isoformat(), "ok": True}, jetzt)
    assert oil_bulletin.faellig({"zeit": (jetzt - timedelta(days=1)).isoformat(), "ok": False}, jetzt)


def test_hochladen_und_laden(angemeldet, db, monkeypatch):
    db.add(Spritpreis(gueltig_ab=date(2026, 9, 15), preis=D("1.800")))  # händisch, Monatsmitte: bleibt
    db.add(Spritpreis(gueltig_ab=date(2026, 10, 1), preis=D("1.500")))  # händisch am Ersten: überschrieben
    db.flush()
    antwort = angemeldet.post("/einstellungen/oil-bulletin/hochladen",
                              files={"datei": ("hist.xlsx", _datei(WOCHEN), "application/octet-stream")})
    assert "Spritpreise aus der Datei: 2 Monatswerte übernommen (09/2026 – 10/2026)." in antwort.text
    preise = {s.gueltig_ab: (s.preis, s.quelle) for s in db.scalars(select(Spritpreis))}
    assert preise == {date(2026, 9, 1): (D("1.925"), "oil_bulletin"), date(2026, 9, 15): (D("1.800"), "manuell"),
                      date(2026, 10, 1): (D("2.125"), "oil_bulletin")}
    seite = angemeldet.get("/einstellungen").text
    assert "aus Datei übernommen" in seite and "01.09.2026 <small>Oil Bulletin</small>" in seite
    # Download: Benzin gewählt; Fehler landet in Meldung und Status
    app_einstellungen.speichern(db, {"sprit_art": "euro95"})
    monkeypatch.setattr(oil_bulletin, "herunterladen", lambda: _datei(WOCHEN))
    assert "2 Monatswerte übernommen" in angemeldet.post("/einstellungen/oil-bulletin/laden").text
    assert db.scalar(select(Spritpreis.preis).where(Spritpreis.gueltig_ab == date(2026, 9, 1))) == D("1.610")

    def kaputt():
        raise OSError("keine Verbindung")
    monkeypatch.setattr(oil_bulletin, "herunterladen", kaputt)
    assert "Download fehlgeschlagen (keine Verbindung)" in angemeldet.post("/einstellungen/oil-bulletin/laden").text
    st = oil_bulletin.status(db)
    assert st["ok"] is False and "keine Verbindung" in st["text"]
    assert "Bitte die Datei" in angemeldet.post("/einstellungen/oil-bulletin/hochladen", files={}).text


def test_automatisch(db, monkeypatch):
    """Taktgeber: nur mit Tankkosten-Schätzung und Haken; wöchentlich."""
    from contextlib import contextmanager
    from app import db as db_modul

    @contextmanager
    def sitzung():
        yield db
    monkeypatch.setattr(db_modul, "SessionLocal", sitzung)
    monkeypatch.setattr(db, "commit", db.flush)
    aufrufe = []
    monkeypatch.setattr(oil_bulletin, "herunterladen", lambda: aufrufe.append(1) or _datei(WOCHEN))
    app_einstellungen.speichern(db, {"feature_tank": "", "oil_bulletin_auto": "1"})
    oil_bulletin.automatisch_pruefen()
    assert aufrufe == []  # Schätzung aus
    app_einstellungen.speichern(db, {"feature_tank": "1"})
    oil_bulletin.automatisch_pruefen()
    oil_bulletin.automatisch_pruefen()  # gerade geladen: nicht fällig
    assert aufrufe == [1] and db.scalar(select(Spritpreis.preis).where(Spritpreis.gueltig_ab == date(2026, 10, 1)))
    app_einstellungen.speichern(db, {"oil_bulletin_auto": "", "oil_bulletin_status": ""})
    oil_bulletin.automatisch_pruefen()
    assert aufrufe == [1]  # abgeschaltet
