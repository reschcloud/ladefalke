"""Erster Admin ohne Passwort in .env: „Ersten Admin anlegen“ im Browser, Notfall-Zugang per Kommando
(werkzeuge/admin.py → benutzer.admin_setzen) und Hinweis, wenn ADMIN_PASSWORD noch in .env steht."""
import pytest
from sqlalchemy import select

from app.config import einstellungen
from app.models import Benutzer
from app.services import benutzer


@pytest.fixture
def ohne_env_admin(monkeypatch):
    monkeypatch.setattr(einstellungen(), "admin_password", "")


def test_login_fuehrt_zur_ersteinrichtung(client, ohne_env_admin):
    antwort = client.get("/login", follow_redirects=False)
    assert antwort.status_code == 303 and antwort.headers["location"] == "/einrichten"
    assert "Ersten Admin anlegen" in client.get("/einrichten").text
    # ohne Konto führt jede Seite über die Anmeldung zur Ersteinrichtung
    assert client.get("/").url.path == "/einrichten"


def test_ersteinrichtung_prueft_und_legt_an(client, db, ohne_env_admin):
    kurz = client.post("/einrichten", data={"benutzername": "chefin", "passwort": "kurz", "wiederholung": "kurz"})
    assert kurz.status_code == 422 and "mindestens" in kurz.text
    assert benutzer.anzahl(db) == 0
    antwort = client.post("/einrichten", data={"benutzername": "chefin", "name": "Chefin Test",
                                               "passwort": "ein-langes-passwort", "wiederholung": "ein-langes-passwort"})
    assert antwort.url.path == "/" and antwort.status_code == 200  # gleich angemeldet
    b = db.scalar(select(Benutzer).where(Benutzer.benutzername == "chefin"))
    assert b.rolle == "admin" and b.aktiv and b.name == "Chefin Test"
    # danach ist die Seite zu – kein zweiter „erster Admin“
    assert client.get("/einrichten", follow_redirects=False).headers["location"] == "/login"
    zweiter = client.post("/einrichten", data={"benutzername": "x", "passwort": "ein-langes-passwort",
                                               "wiederholung": "ein-langes-passwort"}, follow_redirects=False)
    assert zweiter.headers["location"] == "/login" and benutzer.anzahl(db) == 1


def test_mit_env_passwort_keine_ersteinrichtung(client):
    assert client.get("/einrichten", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/login").status_code == 200


def test_notfall_admin_anlegen_und_zuruecksetzen(db, ohne_env_admin):
    b, passwort, neu = benutzer.admin_setzen(db, "notfall")
    assert neu and b.rolle == "admin" and len(passwort) >= benutzer.MINDESTLAENGE
    assert benutzer.anmelden(db, "notfall", passwort) is not None
    # vorhandenes, deaktiviertes Konto ohne Admin-Rechte wird wieder aktiver Admin mit neuem Passwort
    b.rolle, b.aktiv = "viewer", False
    b2, neues, neu2 = benutzer.admin_setzen(db, "Notfall")
    assert b2.id == b.id and not neu2 and b2.rolle == "admin" and b2.aktiv and neues != passwort
    assert benutzer.anmelden(db, "notfall", passwort) is None and benutzer.anmelden(db, "notfall", neues) is not None


def test_hinweis_env_passwort_ueberfluessig(angemeldet, monkeypatch):
    assert "steht noch <code>ADMIN_PASSWORD</code>" in angemeldet.get("/einstellungen").text  # Testumgebung setzt es
    monkeypatch.setattr(einstellungen(), "admin_password", "")
    assert "steht noch <code>ADMIN_PASSWORD</code>" not in angemeldet.get("/einstellungen").text
