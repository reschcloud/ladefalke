"""Benutzer und Rollen: admin (alles), operator (alles außer Einstellungen/MSP/Benutzer), viewer (nur lesen)."""
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Benutzer
from app.services import benutzer as benutzer_service

PASSWORT = "sehr-geheim-123"


def _anlegen(angemeldet, benutzername, rolle):
    antwort = angemeldet.post("/benutzer/neu", data={"benutzername": benutzername, "name": benutzername.title(),
                                                      "rolle": rolle, "passwort": PASSWORT, "wiederholung": PASSWORT})
    assert antwort.status_code == 200, antwort.text


def _client(benutzername, passwort=PASSWORT):
    c = TestClient(app)
    antwort = c.post("/login", data={"benutzer": benutzername, "passwort": passwort, "weiter_zu": "/"})
    assert antwort.status_code == 200 and "Benutzername oder Passwort falsch" not in antwort.text
    return c


@pytest.fixture
def rollen(angemeldet):
    _anlegen(angemeldet, "olga", "operator")
    _anlegen(angemeldet, "viktor", "viewer")
    return {"admin": angemeldet, "operator": _client("olga"), "viewer": _client("Viktor")}  # Name ohne Groß/klein


def test_passwort_hash():
    h = benutzer_service.passwort_hash("abc-def-ghi")
    assert h.startswith("scrypt$") and benutzer_service.passwort_pruefen("abc-def-ghi", h)
    assert not benutzer_service.passwort_pruefen("falsch", h) and not benutzer_service.passwort_pruefen("x", "kaputt")


def test_erster_admin_aus_env(angemeldet, db):
    admin = db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin"))
    assert admin.rolle == "admin" and admin.letzte_anmeldung is not None
    assert admin.passwort_hash != "test-passwort"  # nur als Hash gespeichert


def _alle_routen(routen):
    for r in routen:  # eingebundene Router sind je nach FastAPI-Version verpackt (_IncludedRouter)
        if hasattr(r, "original_router"):
            yield from _alle_routen(r.original_router.routes)
        else:
            yield r


def _post_routen():
    """Alle ändernden Routen der App mit Platzhaltern für Pfadparameter (ohne Anmeldung, Abmelden und
    Ersteinrichtung – die liegen vor der Anmeldung; /einrichten ist zu, sobald es Konten gibt)."""
    for r in _alle_routen(app.routes):
        if isinstance(r, APIRoute) and "POST" in r.methods and r.path not in ("/login", "/logout", "/einrichten") \
                and not r.path.startswith("/konto/"):
            yield re.sub(r"\{[^}]+\}", "1", r.path)


def test_viewer_darf_nichts_aendern(rollen):
    viewer = rollen["viewer"]
    routen = list(_post_routen())
    assert len(routen) > 40  # sonst ist die Suche kaputt
    for pfad in routen:
        antwort = viewer.post(pfad, data={}, follow_redirects=False)
        assert antwort.status_code == 403, pfad
    assert viewer.post("/stammdaten/fahrer/neu", data={"name": "X"}, headers={"HX-Request": "true"},
                       follow_redirects=False).headers["HX-Redirect"] == "/keine-berechtigung"
    # Lesen geht, auch Exporte; Menü ohne Einstellungen, Seite als nur lesbar markiert
    for pfad in ("/", "/ladevorgaenge", "/aufgaben", "/import", "/stammdaten/fahrzeuge", "/auswertungen",
                 "/ladevorgaenge/export.csv", "/konto"):
        assert viewer.get(pfad).status_code == 200, pfad
    seite = viewer.get("/stammdaten/fahrer").text
    assert '<body hx-boost="true" class="nur-lesen menue-links">' in seite and 'href="/einstellungen"' in seite
    assert viewer.get("/benutzer").status_code == 403
    einst = viewer.get("/einstellungen")  # lesen ja, ändern nein
    assert einst.status_code == 200 and "Nur lesen – Einstellungen ändern kann ein Admin." in einst.text


def test_operator_alles_ausser_verwaltung(rollen, db):
    operator = rollen["operator"]
    assert operator.post("/stammdaten/fahrer/neu", data={"name": "Neu Operator", "aktiv": "on"}).status_code == 200
    assert db.scalar(select(Benutzer).where(Benutzer.benutzername == "olga")).rolle == "operator"
    einst = operator.get("/einstellungen").text  # sehen ja, Felder gesperrt
    assert 'class="einstellungen einstellungen-nur-lesen"' in einst and 'disabled title="Unzureichende Berechtigungen"' in einst
    for pfad in ("/benutzer", "/benutzer/neu"):
        assert operator.get(pfad).status_code == 403, pfad
    assert operator.post("/einstellungen", data={"stichtag": "beginn"}).status_code == 403
    assert operator.post("/benutzer/neu", data={"benutzername": "x"}).status_code == 403
    seite = operator.get("/").text
    assert 'href="/benutzer"' not in seite and 'class="nur-lesen"' not in seite and 'class="als-admin"' not in seite
    admin_seite = rollen["admin"].get("/einstellungen").text
    assert 'href="/benutzer"' in admin_seite and '<body hx-boost="true" class="als-admin menue-links">' in admin_seite  # rote Leiste
    assert "einstellungen-nur-lesen" not in admin_seite and 'disabled title="Unzureichende Berechtigungen"' not in admin_seite


def test_letzter_admin_und_eigenes_konto(rollen, db):
    admin = rollen["admin"]
    ich = db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin"))
    antwort = admin.post(f"/benutzer/{ich.id}", data={"name": "Admin", "rolle": "operator", "aktiv": "on"})
    assert antwort.status_code == 422 and "letzte aktive Admin" in antwort.text
    antwort = admin.post(f"/benutzer/{ich.id}", data={"name": "Admin", "rolle": "admin"})
    assert antwort.status_code == 422 and "eigene Konto" in antwort.text
    # zweiter Admin → jetzt darf der erste herabgestuft werden
    olga = db.scalar(select(Benutzer).where(Benutzer.benutzername == "olga"))
    admin.post(f"/benutzer/{olga.id}", data={"name": "Olga", "rolle": "admin", "aktiv": "on"})
    antwort = admin.post(f"/benutzer/{ich.id}", data={"name": "Admin", "rolle": "operator", "aktiv": "on"},
                         follow_redirects=False)
    assert antwort.status_code == 303 and antwort.headers["location"] == "/"  # Verwaltung nicht mehr erlaubt
    db.refresh(ich)
    assert ich.rolle == "operator"
    assert admin.get("/benutzer").status_code == 403  # Rolle wirkt sofort


def test_deaktivieren_und_passwoerter(rollen, db):
    admin, viewer = rollen["admin"], rollen["viewer"]
    viktor = db.scalar(select(Benutzer).where(Benutzer.benutzername == "viktor"))
    # eigenes Passwort ändern (auch als Viewer erlaubt)
    antwort = viewer.post("/konto/passwort", data={"aktuell": "falsch", "neu": "x" * 10, "wiederholung": "x" * 10})
    assert antwort.status_code == 422 and "bisherige Passwort" in antwort.text
    antwort = viewer.post("/konto/passwort", data={"aktuell": PASSWORT, "neu": "kurz", "wiederholung": "kurz"})
    assert antwort.status_code == 422 and "mindestens 10 Zeichen" in antwort.text
    assert viewer.post("/konto/passwort", data={"aktuell": PASSWORT, "neu": "neues-passwort",
                                                "wiederholung": "neues-passwort"}).status_code == 200
    _client("viktor", "neues-passwort")
    # Admin setzt zurück
    admin.post(f"/benutzer/{viktor.id}/passwort", data={"passwort": "zurueckgesetzt", "wiederholung": "zurueckgesetzt"})
    _client("viktor", "zurueckgesetzt")
    # Deaktivieren: laufende Sitzung endet, Anmeldung scheitert
    admin.post(f"/benutzer/{viktor.id}", data={"name": "Viktor", "rolle": "viewer"})
    assert viewer.get("/", follow_redirects=False).status_code == 303
    c = TestClient(app)
    assert c.post("/login", data={"benutzer": "viktor", "passwort": "zurueckgesetzt"}).status_code == 422
    # doppelter Benutzername
    antwort = admin.post("/benutzer/neu", data={"benutzername": "VIKTOR", "name": "V", "rolle": "viewer",
                                                 "passwort": PASSWORT, "wiederholung": PASSWORT})
    assert antwort.status_code == 422 and "gibt es bereits" in antwort.text


def test_viewer_sieht_keine_eingabebereiche(rollen, db):
    """Reine Eingabebereiche (neue Zuordnung, Korrekturen) sind als data-schreiben markiert und für Viewer per CSS
    ausgeblendet; die Seite selbst ist lesbar."""
    from app.models import Fahrzeug
    rollen["admin"].post("/stammdaten/fahrzeuge/neu", data={"kennzeichen": "V-1", "aktiv": "on"})
    fz = db.scalar(select(Fahrzeug).where(Fahrzeug.kennzeichen == "V-1"))
    seite = rollen["viewer"].get(f"/stammdaten/fahrzeuge/{fz.id}").text
    assert '<article data-schreiben>' in seite and seite.count("</svg>V-1</h1>") == 1  # Titel mit Fahrzeug-Symbol


def test_loeschen_nur_deaktiviert(rollen, db):
    admin = rollen["admin"]
    viktor = db.scalar(select(Benutzer).where(Benutzer.benutzername == "viktor"))
    assert "bitte zuerst deaktivieren" in admin.post(f"/benutzer/{viktor.id}/loeschen").text
    assert db.get(Benutzer, viktor.id) is not None
    assert "/loeschen" not in admin.get(f"/benutzer/{viktor.id}").text.split("<h1")[1]  # kein Knopf, solange aktiv
    admin.post(f"/benutzer/{viktor.id}", data={"name": "Viktor", "rolle": "viewer"})  # deaktivieren
    assert f'action="/benutzer/{viktor.id}/loeschen"' in admin.get(f"/benutzer/{viktor.id}").text
    assert "gelöscht" in admin.post(f"/benutzer/{viktor.id}/loeschen").text
    db.expire_all()
    assert db.get(Benutzer, viktor.id) is None
    ich = db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin"))
    assert "eigene Konto" in admin.post(f"/benutzer/{ich.id}/loeschen").text


def test_gesperrtes_sichtbar_statt_versteckt(rollen):
    """Was eine Rolle nicht darf, bleibt sichtbar und ist gekennzeichnet (statt ausgeblendet)."""
    viewer, operator = rollen["viewer"], rollen["operator"]
    seite = viewer.get("/stammdaten/fahrzeuge").text
    assert 'role="button" data-schreiben>Neues Fahrzeug</a>' in seite  # Link da, wird per Skript gesperrt
    assert "display: none" not in viewer.get("/static/app.css").text.split("Rollen */")[1].split("Einstellungen für")[0]
    for c in (viewer, operator):  # Benutzerverwaltung im Menü sichtbar, aber gesperrt
        assert 'title="Benutzerverwaltung nur für Admins">Benutzer:innen <svg class="symbol symbol-schloss"' in c.get("/").text
    einst = operator.get("/einstellungen").text
    assert 'class="ausgegraut" aria-disabled="true" title="Unzureichende Berechtigungen">Speichern</button>' in einst


def test_benutzermenue_ohne_rolle(angemeldet):
    """Rechts oben nur der Name; die Rolle steht im Tooltip."""
    seite = angemeldet.get("/").text
    kopf = seite.split('benutzermenue">')[1].split("</summary>")[0]
    assert 'class="rolle"' not in kopf and "(Admin)" in kopf


def test_menue_immer_links(angemeldet, db):
    """Menüleiste immer als Seitenleiste links (kein Umschalter mehr); Footer-Inhalt in der Leiste, Anzahlen bei Stammdaten."""
    seite = angemeldet.get("/stammdaten/fahrer").text
    assert "menue-links" in seite.split("<body hx-boost")[1].split(">")[0] and 'class="seiten-fuss"' in seite
    assert "Seitenleiste: Stammdaten aufgeklappt" in seite and "benutzermenue" in seite  # Benutzermenü fest offen
    assert 'class="menue-anzahl"' in seite
    assert "/konto/menue" not in angemeldet.get("/konto").text
    assert angemeldet.post("/konto/menue", data={"menue": "oben"}).status_code in (404, 405)


def test_menue_knopf_fuer_schmale_fenster(angemeldet):
    seite = angemeldet.get("/").text
    assert 'class="menue-knopf outline secondary"' in seite and 'aria-controls="hauptmenue"' in seite
    assert '<ul id="hauptmenue">' in seite and 'classList.toggle("menue-kompakt"' in seite
