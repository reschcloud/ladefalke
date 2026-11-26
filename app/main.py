"""Ladefalke – Der elektrische Fuhrpark im Blick (Ladekosten-Auswertung) – FastAPI-Anwendung."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.config import einstellungen
from app.db import get_db
from app.services import aktualisierung, aufloesung, backup, fristen_mail, geodaten, kosten, neuschaetzung, offen, oil_bulletin, parser_verwaltung, protokoll, syslog, ust_nachtrag, versand  # noqa: F401  (kosten, neuschaetzung, protokoll: Ereignisse beim Speichern)
from app.routers import (auswertungen, benutzer, dashboard, einstellungen as einstellungen_router, fahrer, fahrzeuge,
                         importe, konto, ladekarten, ladevorgaenge, msp, pauschalen, protokoll as protokoll_router, reports,
                         zu_klaeren, zuordnungen)

async def taktgeber() -> None:
    """Hintergrund: jede Minute prüfen, ob eine automatische Sicherung, ein Report-Versand, ein Abgleich (Spritpreise,
    Geodaten, neue Releases) oder das Löschen alter Protokolleinträge fällig ist (Thread)."""
    while True:
        await asyncio.sleep(60)
        for aufgabe, name in ((backup.automatisch_pruefen, "Sicherung"), (versand.automatisch_pruefen, "Report-Versand"),
                              (oil_bulletin.automatisch_pruefen, "Spritpreis-Abruf"),
                              (geodaten.automatisch_pruefen, "Geodaten-Aktualisierung"),
                              (aktualisierung.automatisch_pruefen, "Update-Prüfung"),
                              (fristen_mail.automatisch_pruefen, "Fristen-Benachrichtigung"),
                              (protokoll.automatisch_aufraeumen, "Protokoll-Bereinigung")):
            try:
                await asyncio.to_thread(aufgabe)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("Taktgeber: automatische %s fehlgeschlagen", name)


@asynccontextmanager
async def lebenszyklus(_app: FastAPI):
    syslog.einrichten()  # Syslog-Einstellungen laden, App-Meldungen anhängen
    parser_verwaltung.beim_start()  # MSP für neu installierte Parser anlegen
    aufloesung.stichtag_festlegen()  # Stichtag fest Ladebeginn (früher wählbar): einmal neu zuordnen
    kosten.beim_start()  # tatsächliche Kosten nachtragen (nach Migration 0031)
    aktualisierung.beim_start()  # zuletzt gefundenes Release für den Hinweis „Update verfügbar“
    ust_nachtrag.beim_start()  # USt. aus den Originalrechnungen nachtragen (einmal, im Hintergrund)
    takt = asyncio.create_task(taktgeber())
    yield
    takt.cancel()


app = FastAPI(title="Ladefalke", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lebenszyklus)
app.add_middleware(SessionMiddleware, secret_key=einstellungen().secret_key,
                   session_cookie="ladekosten_session", max_age=12 * 3600, same_site="lax",
                   https_only=einstellungen().cookie_secure)
# Stufe 6 statt 9: gleiche Größe bei den großen Kartendaten (details.json, basiskarte.js), halbe Rechenzeit
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
@app.middleware("http")
async def download_signal(request, call_next):
    """Downloads mit „download_kennung“ (PDF-Reports, base.html): Cookie mit der Kennung setzen, sobald die Datei
    fertig ist – das Skript blendet dann die Lade-Animation aus. Nur Buchstaben und Ziffern."""
    antwort = await call_next(request)
    kennung = request.query_params.get("download_kennung", "")
    if kennung.isalnum() and len(kennung) <= 16 and "attachment" in antwort.headers.get("content-disposition", ""):
        antwort.set_cookie("ladefalke_download", kennung, max_age=60, path="/", samesite="lax", httponly=False,
                           secure=einstellungen().cookie_secure)
    return antwort


app.add_exception_handler(auth.NichtAngemeldet, auth.nicht_angemeldet_behandeln)
app.add_exception_handler(auth.KeineBerechtigung, auth.keine_berechtigung_behandeln)
class StatischeDateien(StaticFiles):
    """Mit Versionsparameter (`?v=…`, base.html: static_version) ändert sich der Inhalt unter der Adresse nie –
    der Browser darf die Datei dauerhaft behalten und fragt nicht einmal nach. Ohne `v` wie bisher (Nachfrage)."""
    async def get_response(self, path: str, scope):
        antwort = await super().get_response(path, scope)
        if antwort.status_code == 200 and "v" in parse_qs(scope.get("query_string", b"").decode()):
            antwort.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return antwort


app.mount("/static", StatischeDateien(directory=Path(__file__).parent / "static"), name="static")



@app.get("/gesund", include_in_schema=False)
def gesund(db: Session = Depends(get_db)) -> dict:
    """Healthcheck (compose.yaml, werkzeuge/gesund.py): App antwortet und erreicht die Datenbank. Ohne Anmeldung,
    verrät nichts außer dem Status."""
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


app.include_router(auth.router)
# Reihenfolge: erst Anmeldung und Rolle prüfen (Viewer: nur lesen), dann offene Punkte für die Menüleiste zählen
geschuetzt = [Depends(auth.login_erforderlich), Depends(offen.offene_punkte_laden)]
nur_admin = [Depends(auth.login_erforderlich), Depends(auth.admin_erforderlich), Depends(offen.offene_punkte_laden)]
# Pauschalen vor den Importen: /import/pauschalen/… darf nicht als /import/{import_id} gelesen werden
for modul in (dashboard, pauschalen, importe, ladevorgaenge, zu_klaeren, auswertungen, reports, msp, ladekarten, fahrzeuge,
              fahrer, zuordnungen, konto, einstellungen_router):  # Einstellungen: lesen alle, speichern nur Admin
    app.include_router(modul.router, dependencies=geschuetzt)
app.include_router(benutzer.router, dependencies=nur_admin)  # Benutzerverwaltung nur für Admins
app.include_router(protokoll_router.router, dependencies=nur_admin)  # Änderungsprotokoll nur für Admins
app.include_router(zu_klaeren.alt, dependencies=geschuetzt)
app.include_router(pauschalen.alt, dependencies=geschuetzt)
