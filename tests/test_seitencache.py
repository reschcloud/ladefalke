"""Seiten-Cache (services/seitencache.py): rechenintensive Seiten werden je Benutzer:in und Adresse gemerkt und bei
jeder gespeicherten Änderung ungültig; Seiten mit Meldungen nie aus dem Cache."""
from app.services import seitencache


def _treffer(antwort) -> bool:
    return antwort.headers.get("X-Seitencache") == "treffer"


def test_zweiter_aufruf_aus_dem_cache(angemeldet):
    seitencache.ungueltig_machen()  # die Anmeldung leitet schon auf „/“ weiter
    for pfad in ("/", "/auswertungen", "/reports", "/?art=jahr"):
        erste = angemeldet.get(pfad)
        assert erste.status_code == 200 and not _treffer(erste), pfad
        zweite = angemeldet.get(pfad)
        assert _treffer(zweite), pfad
        assert zweite.text == erste.text


def test_andere_adresse_eigener_eintrag(angemeldet):
    angemeldet.get("/?art=jahr")
    assert not _treffer(angemeldet.get("/?art=quartal"))


def test_aenderung_macht_ungueltig(angemeldet):
    angemeldet.get("/")
    assert _treffer(angemeldet.get("/"))
    stand = seitencache.datenstand()
    # eine gespeicherte Änderung (Einstellungen) – danach wird neu gerechnet
    antwort = angemeldet.post("/einstellungen/update/pruefen", follow_redirects=False)
    assert antwort.status_code == 303
    assert seitencache.datenstand() > stand
    assert not _treffer(angemeldet.get("/einstellungen"))  # (nicht gecacht; holt die Meldung ab)
    assert not _treffer(angemeldet.get("/"))


def test_meldung_nie_aus_dem_cache(angemeldet, monkeypatch):
    angemeldet.get("/")
    assert _treffer(angemeldet.get("/"))
    # eine Aktion ohne Datenänderung, die eine Meldung hinterlässt: die nächste Seite muss sie zeigen
    from app.services import aktualisierung
    monkeypatch.setattr(seitencache, "ungueltig_machen", lambda: None)  # Datenstand bleibt gleich
    monkeypatch.setattr(aktualisierung, "abfragen", lambda: None)
    angemeldet.post("/einstellungen/update/pruefen", follow_redirects=False)
    seite = angemeldet.get("/")
    assert not _treffer(seite)
    assert "neuesten Stand" in seite.text or "fehlgeschlagen" in seite.text
    nachher = angemeldet.get("/")  # die Seite mit Meldung wurde nicht gemerkt – es gilt die von vorher
    assert _treffer(nachher) and "neuesten Stand" not in nachher.text


def test_je_benutzer_getrennt(angemeldet, db):
    from sqlalchemy import select
    from app.models import Benutzer
    angemeldet.get("/")
    admin = db.scalar(select(Benutzer).where(Benutzer.benutzername == "admin"))
    schluessel = [k for k in seitencache._eintraege]
    assert schluessel and all(k[0] == admin.id for k in schluessel)


def test_harter_reload_rechnet_neu(angemeldet):
    seitencache.ungueltig_machen()
    angemeldet.get("/")
    assert _treffer(angemeldet.get("/"))
    assert _treffer(angemeldet.get("/", headers={"Cache-Control": "max-age=0"}))  # normaler Reload (F5)
    assert not _treffer(angemeldet.get("/", headers={"Cache-Control": "no-cache", "Pragma": "no-cache"}))
    assert not _treffer(angemeldet.get("/", headers={"Pragma": "no-cache"}))
    assert _treffer(angemeldet.get("/"))  # das frische Ergebnis ist wieder gemerkt
