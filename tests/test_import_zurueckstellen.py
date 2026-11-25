"""Import zurückstellen: nicht lesbare Rechnungen auf die Merkliste der Import-Seite (später von Hand erfassen)."""
from pathlib import Path

from sqlalchemy import select

from app.models import Import, ImportStatus
from app.services.importe import datei

CSV = (Path(__file__).parent / "fixtures" / "hastobe_beispiel.csv").read_bytes()
UNLESBAR = b"%PDF-1.4\nerfundene Rechnung ohne bekanntes Format\n"


def _hochladen(client, *dateien):
    return client.post("/import", files=[("datei_upload", d) for d in dateien])


def _import(db, name):
    db.expire_all()
    return db.scalar(select(Import).where(Import.dateiname == name))


def test_zurueckstellen_und_merkliste(angemeldet, db):
    _hochladen(angemeldet, ("unlesbar.pdf", UNLESBAR, "application/pdf"), ("oktober.csv", CSV, "text/csv"))
    unlesbar, oktober = _import(db, "unlesbar.pdf"), _import(db, "oktober.csv")
    seite = angemeldet.get(f"/import/{unlesbar.id}").text
    assert f'action="/import/{unlesbar.id}/zurueckstellen"' in seite

    antwort = angemeldet.post(f"/import/{unlesbar.id}/zurueckstellen", data={"notiz": "  als Pauschale eintragen "},
                              follow_redirects=False)
    assert antwort.headers["location"] == f"/import/{oktober.id}"  # weiter mit der nächsten Datei
    unlesbar = _import(db, "unlesbar.pdf")
    assert unlesbar.status == ImportStatus.zurueckgestellt and unlesbar.notiz == "als Pauschale eintragen"
    assert unlesbar.zurueckgestellt_am is not None and datei(unlesbar).exists()

    # Navigation zählt die zurückgestellte Datei als erledigt mit: Datei 2 von 2
    assert "Datei 2 von 2" in angemeldet.get(f"/import/{oktober.id}").text
    liste = angemeldet.post(f"/import/{oktober.id}/loeschen").text  # letzte Datei → Abschluss (nur einmal)
    assert "1 zurückgestellt" in liste
    assert 'id="zurueckgestellt"' in liste and "unlesbar.pdf" in liste and "als Pauschale eintragen" in liste
    assert "Offene Vorschauen" not in liste  # zählt nicht als offene Vorschau

    # Detailadresse führt zur Merkliste
    antwort = angemeldet.get(f"/import/{unlesbar.id}", follow_redirects=False)
    assert antwort.headers["location"] == "/import#zurueckgestellt"


def test_erneut_hochgeladen_gilt_als_bekannt(angemeldet, db):
    _hochladen(angemeldet, ("unlesbar.pdf", UNLESBAR, "application/pdf"))
    imp = _import(db, "unlesbar.pdf")
    angemeldet.post(f"/import/{imp.id}/zurueckstellen", data={"notiz": ""})
    assert _import(db, "unlesbar.pdf").notiz is None

    seite = _hochladen(angemeldet, ("nochmal.pdf", UNLESBAR, "application/pdf"), ("oktober.csv", CSV, "text/csv")).text
    assert _import(db, "nochmal.pdf") is None  # keine neue Vorschau
    assert "bereits bekannt" in seite and "zurückgestellt" in seite


def test_wieder_pruefen_und_erledigt(angemeldet, db):
    _hochladen(angemeldet, ("unlesbar.pdf", UNLESBAR, "application/pdf"))
    imp = _import(db, "unlesbar.pdf")
    angemeldet.post(f"/import/{imp.id}/zurueckstellen", data={"notiz": "später"})

    antwort = angemeldet.post(f"/import/{imp.id}/wieder-pruefen", follow_redirects=False)
    assert antwort.headers["location"] == f"/import/{imp.id}"
    imp = _import(db, "unlesbar.pdf")
    assert imp.status == ImportStatus.vorschau and imp.notiz is None and imp.zurueckgestellt_am is None

    angemeldet.post(f"/import/{imp.id}/zurueckstellen", data={"notiz": "später"})
    pfad = datei(_import(db, "unlesbar.pdf"))
    antwort = angemeldet.post(f"/import/{imp.id}/loeschen", follow_redirects=False)  # „Erledigt“
    assert antwort.headers["location"] == "/import#zurueckgestellt"
    assert _import(db, "unlesbar.pdf") is None and not pfad.exists()
    assert 'id="zurueckgestellt"' not in angemeldet.get("/import").text


def test_nur_offene_vorschauen_zurueckstellen(angemeldet, db):
    _hochladen(angemeldet, ("unlesbar.pdf", UNLESBAR, "application/pdf"))
    imp = _import(db, "unlesbar.pdf")
    angemeldet.post(f"/import/{imp.id}/zurueckstellen")
    angemeldet.post(f"/import/{imp.id}/zurueckstellen")  # doppelt: Fehlermeldung, Zustand bleibt
    assert _import(db, "unlesbar.pdf").status == ImportStatus.zurueckgestellt
    angemeldet.post(f"/import/{imp.id}/wieder-pruefen")
    angemeldet.post(f"/import/{imp.id}/wieder-pruefen")
    assert _import(db, "unlesbar.pdf").status == ImportStatus.vorschau
