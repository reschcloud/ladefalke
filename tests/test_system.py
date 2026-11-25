"""Einstellungen → System: Geodaten selbst laden (services/geodaten.py) und Hinweis auf neue Releases
(services/aktualisierung.py). Ohne Netz: Download und GitHub-Abfrage werden ersetzt; Geodaten erfunden
(tests/fixtures/geonames_beispiel.txt)."""
import json
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import GeoPlz
from app.services import aktualisierung, geodaten
from app.services.geokodierung import einlesen

GEO = Path(__file__).parent / "fixtures" / "geonames_beispiel.txt"
JETZT = datetime(2026, 11, 24, 12, 0)


@pytest.fixture(autouse=True)
def ohne_gemerktes_release():
    aktualisierung._neueste = None
    yield
    aktualisierung._neueste = None


def _zip(ziel: Path) -> None:
    with zipfile.ZipFile(ziel, "w") as archiv:
        archiv.write(GEO, "allCountries.txt")
        archiv.writestr("readme.txt", "erfunden")


# ------------------------------------------------------------------ Geodaten

@pytest.mark.parametrize("st, faellig", [
    ({}, True),  # noch nie
    ({"zeit": "2026-11-24T08:00:00", "ok": True, "anzahl": 5, "version": "v1"}, False),
    ({"zeit": "2026-11-24T08:00:00", "ok": True, "anzahl": 5, "version": "v0"}, True),  # nach einem Update
    ({"zeit": "2026-10-20T08:00:00", "ok": True, "anzahl": 5, "version": "v1"}, True),  # älter als 30 Tage
    ({"zeit": "2026-11-24T08:00:00", "ok": True, "anzahl": 0, "version": "v1"}, True),  # Tabelle leer
    ({"zeit": "2026-11-24T08:00:00", "ok": False, "anzahl": 0}, False),  # Fehlschlag: erst morgen wieder
    ({"zeit": "2026-11-23T08:00:00", "ok": False, "anzahl": 0}, True),
])
def test_geodaten_faellig(st, faellig):
    assert geodaten.faellig(st, JETZT, "v1") is faellig


def test_einlesen_aus_zip(db, tmp_path):
    _zip(tmp_path / "allCountries.zip")
    assert einlesen(db, tmp_path / "allCountries.zip") == 13


def test_unbrauchbare_datei_laesst_bestand(db, tmp_path):
    einlesen(db, GEO)
    (tmp_path / "leer.txt").write_text("US\t10001\tNew York\t\t\t\t\t\t\t40.7\t-74.0\n")
    with pytest.raises(ValueError):
        einlesen(db, tmp_path / "leer.txt")
    assert db.scalar(select(func.count(GeoPlz.id))) == 13


def test_aktualisieren_per_download(db, monkeypatch):
    monkeypatch.setattr(geodaten, "herunterladen", _zip)
    text = geodaten.aktualisieren(db, "automatisch")
    assert text.startswith("13 Postleitzahlen")
    st = geodaten.status(db)
    assert st["ok"] and st["quelle"] == "automatisch" and st["anzahl"] == 13 and st["version"]
    assert not geodaten.faellig(st, datetime.now(), st["version"])


def test_aktualisieren_rueckfall_auf_lokale_datei(db, monkeypatch, tmp_path):
    def kein_netz(_ziel):
        raise OSError("kein Netz")
    shutil.copy(GEO, tmp_path / "allCountries.txt")
    monkeypatch.setattr(geodaten, "herunterladen", kein_netz)
    monkeypatch.setattr(geodaten, "lokale_datei", lambda: tmp_path / "allCountries.txt")
    geodaten.aktualisieren(db)
    st = geodaten.status(db)
    assert st["ok"] and st["quelle"] == "datei" and st["anzahl"] == 13


def test_aktualisieren_fehler_behaelt_bestand(db, monkeypatch):
    einlesen(db, GEO)
    db.commit()  # bisheriger, gespeicherter Stand

    def kein_netz(_ziel):
        raise OSError("kein Netz")
    monkeypatch.setattr(geodaten, "herunterladen", kein_netz)
    monkeypatch.setattr(geodaten, "lokale_datei", lambda: None)
    with pytest.raises(OSError):
        geodaten.aktualisieren(db)
    st = geodaten.status(db)
    assert not st["ok"] and "kein Netz" in st["text"] and st["anzahl"] == 13
    assert not geodaten.status_laeuft()  # Sperre wieder frei


# ------------------------------------------------------------------ Updates

def test_neueste_aus_tags():
    tags = [{"name": "v1.6.1"}, {"name": "v1.10.0"}, {"name": "v1.9.3"}, {"name": "test"}, {"name": "v2.0.0-rc1"}]
    assert aktualisierung.neueste_aus(tags) == "1.10.0"
    assert aktualisierung.neueste_aus([]) is None


def test_abfragen_nur_releases_mit_image(monkeypatch):
    """Ein frisch getaggtes Release zählt erst, wenn die Pipeline das Image veröffentlicht hat."""
    import io
    import urllib.error
    tags = [{"name": "v1.12.0"}, {"name": "v1.11.0"}, {"name": "v1.10.0"}, {"name": "v1.9.0"}]
    images = {"1.11.0", "1.10.0"}  # 1.12.0: Pipeline läuft noch
    gefragt = []

    def holen(url, kopf=None, methode="GET"):
        if url == aktualisierung.URL:
            return io.BytesIO(json.dumps(tags).encode())
        if "/token?" in url:
            return io.BytesIO(b'{"token": "anonym"}')
        version = url.rsplit("/", 1)[1]
        gefragt.append(version)
        assert methode == "HEAD" and kopf["Authorization"] == "Bearer anonym"
        if version in images:
            return io.BytesIO(b"")
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(aktualisierung, "_holen", holen)
    assert aktualisierung.abfragen() == "1.11.0"
    images.add("1.12.0")
    assert aktualisierung.abfragen() == "1.12.0"
    images.clear()
    gefragt.clear()
    assert aktualisierung.abfragen() is None  # keins der neuesten drei gebaut
    assert gefragt == ["1.12.0", "1.11.0", "1.10.0"]


def test_verfuegbar(monkeypatch):
    monkeypatch.setattr(aktualisierung, "laufende", lambda: "1.6.1")
    assert aktualisierung.verfuegbar("1.7.0") == "1.7.0"
    assert aktualisierung.verfuegbar("1.6.1") is None
    assert aktualisierung.verfuegbar("1.5.9") is None
    monkeypatch.setattr(aktualisierung, "laufende", lambda: None)  # selbst gebaut: kein Hinweis
    assert aktualisierung.verfuegbar("9.0.0") is None


def test_hinweis_in_der_oberflaeche(angemeldet, monkeypatch):
    monkeypatch.setattr(aktualisierung, "laufende", lambda: "1.6.1")
    monkeypatch.setattr(aktualisierung, "abfragen", lambda: "1.7.0")
    antwort = angemeldet.post("/einstellungen/update/pruefen", follow_redirects=True)
    assert "Version 1.7.0 ist verfügbar" in antwort.text
    assert "Update auf 1.7.0 verfügbar" in angemeldet.get("/").text
    seite = angemeldet.get("/einstellungen").text
    assert 'id="system"' in seite and "sh setup.sh" in seite
    monkeypatch.setattr(aktualisierung, "abfragen", lambda: "1.6.1")
    assert "neuesten Stand" in angemeldet.post("/einstellungen/update/pruefen", follow_redirects=True).text
    assert "Update auf" not in angemeldet.get("/").text


def test_update_pruefung_fehler(angemeldet, monkeypatch):
    def kein_netz():
        raise OSError("kein Netz")
    monkeypatch.setattr(aktualisierung, "abfragen", kein_netz)
    assert "Abfrage fehlgeschlagen" in angemeldet.post("/einstellungen/update/pruefen", follow_redirects=True).text


def test_automatische_pruefung_einmal_taeglich(db, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(aktualisierung, "laufende", lambda: "1.6.1")
    monkeypatch.setattr(aktualisierung, "status", lambda _db: {"zeit": (datetime.now() - timedelta(hours=2)).isoformat()})
    monkeypatch.setattr(aktualisierung, "pruefen", lambda _db: aufrufe.append(1))
    aktualisierung.automatisch_pruefen()
    assert aufrufe == []
