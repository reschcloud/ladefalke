from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.formatierung import monat, monat_lesen
from app.models import Pauschale


@pytest.mark.parametrize("text, erwartet", [
    ("2026-09", date(2026, 9, 1)), ("09/2026", date(2026, 9, 1)), ("9.2026", date(2026, 9, 1)), ("", None)])
def test_monat_lesen(text, erwartet):
    assert monat_lesen(text) == erwartet


def test_monat_lesen_ungueltig():
    with pytest.raises(ValueError, match="kein gültiger Monat"):
        monat_lesen("13/2026")


def test_monat_anzeigen():
    assert monat(date(2026, 9, 1)) == "09/2026"


def test_pauschale_crud(angemeldet, db):
    daten = {"monat": "2026-09", "bezeichnung": "Wallbox Teststraße", "kwh": "412,5", "betrag_netto": "49,17",
             "ladeart": "privat", "notiz": "eSolution Home"}
    antwort = angemeldet.post("/import/pauschalen/neu", data=daten)
    assert antwort.status_code == 200
    assert "Pauschale „Wallbox Teststraße“ für 09/2026 angelegt" in antwort.text
    assert "412,500" in antwort.text and "49,17 €" in antwort.text
    p = db.scalar(select(Pauschale).where(Pauschale.bezeichnung == "Wallbox Teststraße"))
    assert (p.monat, p.kwh, p.betrag_netto, p.ladeart.value) == (date(2026, 9, 1), Decimal("412.500"),
                                                                 Decimal("49.17"), "privat")

    # gleicher Monat + Bezeichnung → abgewiesen
    antwort = angemeldet.post("/import/pauschalen/neu", data=daten)
    assert antwort.status_code == 422
    assert "bereits eine Pauschale" in antwort.text

    assert "Folgemonat" not in angemeldet.get("/import").text  # Vorlage-Knopf entfernt

    antwort = angemeldet.post(f"/import/pauschalen/{p.id}", data={**daten, "kwh": "400", "betrag_netto": "-1"})
    assert antwort.status_code == 422
    angemeldet.post(f"/import/pauschalen/{p.id}", data={**daten, "kwh": "400"})
    db.refresh(p)
    assert p.kwh == Decimal("400.000")

    angemeldet.post(f"/import/pauschalen/{p.id}/loeschen")
    assert db.get(Pauschale, p.id) is None


def test_jahresfilter(angemeldet, db):
    db.add_all([Pauschale(monat=date(2025, 12, 1), bezeichnung="Alt", kwh=Decimal(1), betrag_netto=Decimal(1),
                          ladeart="privat"),
                Pauschale(monat=date(2026, 1, 1), bezeichnung="Neu", kwh=Decimal(2), betrag_netto=Decimal(2),
                          ladeart="privat")])
    db.flush()
    antwort = angemeldet.get("/import?pauschalen_jahr=2025")
    assert "Alt" in antwort.text and ">Neu<" not in antwort.text


def test_teil_der_import_seite(angemeldet, db):
    """Pauschalen stehen als Abschnitt auf der Import-Seite; kein eigener Menüpunkt, alte Adresse leitet um."""
    db.add(Pauschale(monat=date(2025, 5, 1), bezeichnung="Wallbox Test", kwh=Decimal(10), betrag_netto=Decimal(5),
                     ladeart="privat"))
    db.flush()
    seite = angemeldet.get("/import?pauschalen_jahr=2025").text
    assert '<article id="pauschalen">' in seite and "Wallbox Test" in seite
    assert 'href="/pauschalen"' not in seite  # Menüpunkt entfällt
    alt = angemeldet.get("/pauschalen?jahr=2025", follow_redirects=False)
    assert alt.status_code == 301 and alt.headers["location"] == "/import?pauschalen_jahr=2025#pauschalen"
    antwort = angemeldet.post("/import/pauschalen/neu", data={"monat": "2025-06", "bezeichnung": "Wallbox Test",
                                                              "kwh": "1", "betrag_netto": "1", "ladeart": "privat"},
                              follow_redirects=False)
    assert "/import?pauschalen_jahr=2025#pauschalen" in (antwort.headers.get("location", "")
                                                         + antwort.headers.get("hx-redirect", "") + antwort.text)
