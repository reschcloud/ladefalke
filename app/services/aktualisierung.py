"""Hinweis auf neue Releases: einmal täglich die Versions-Tags (vX.Y.Z) auf GitHub abfragen (Einstellung
„update_pruefen“, Standard an; es werden keine Daten gesendet). Ist ein neueres Release als das laufende da, zeigt
die Oberfläche neben der Version „Update verfügbar“ und unter Einstellungen → System, wie man aktualisiert
(`sh setup.sh` im Installationsordner). Selbst gebaute Stände ohne Release-Version bekommen keinen Hinweis.
"""
import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Einstellung
from app.version import version

log = logging.getLogger(__name__)

REPO = "reschcloud/ladefalke"
URL = f"https://api.github.com/repos/{REPO}/tags?per_page=100"
RELEASES = f"https://github.com/{REPO}/releases"
CHANGELOG = f"https://github.com/{REPO}/blob/main/CHANGELOG.md"
STATUS = "update_status"
_neueste: str | None = None  # zuletzt gefundenes Release (für die Anzeige in jeder Seite, ohne Datenbankzugriff)


def _schluessel(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def laufende() -> str | None:
    """Release der laufenden App („1.6.1“) oder None (selbst gebaut ohne Tag)."""
    treffer = re.match(r"v(\d+\.\d+\.\d+)\b", version())
    return treffer.group(1) if treffer else None


def neueste_aus(tags: list[dict]) -> str | None:
    versionen = [t["name"][1:] for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t.get("name", ""))]
    return max(versionen, key=_schluessel) if versionen else None


def abfragen() -> str | None:
    anfrage = urllib.request.Request(URL, headers={"User-Agent": "Ladefalke (Update-Pruefung)",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:  # noqa: S310 – feste https-Adresse
        return neueste_aus(json.loads(antwort.read(2 * 1024 * 1024)))


def status(db: Session) -> dict:
    e = db.get(Einstellung, STATUS)
    try:
        return json.loads(e.wert) if e and e.wert else {}
    except ValueError:
        return {}


def verfuegbar(neueste: str | None = None) -> str | None:
    """Neueres Release als das laufende („1.7.0“) oder None."""
    neueste = neueste or _neueste
    jetzt = laufende()
    if not neueste or not jetzt:
        return None
    return neueste if _schluessel(neueste) > _schluessel(jetzt) else None


def pruefen(db: Session) -> str | None:
    """Jetzt nachsehen; Ergebnis im Status. Liefert das verfügbare neuere Release oder None."""
    global _neueste
    from app.services import app_einstellungen
    try:
        neueste = abfragen()
        st = {"zeit": datetime.now().isoformat(timespec="seconds"), "ok": True, "neueste": neueste}
    except Exception as e:  # noqa: BLE001
        st = {**status(db), "zeit": datetime.now().isoformat(timespec="seconds"), "ok": False,
              "fehler": f"{type(e).__name__}: {e}"}
    if st.get("neueste"):
        _neueste = st["neueste"]
    app_einstellungen.speichern(db, {STATUS: json.dumps(st, ensure_ascii=False)})
    db.commit()
    return verfuegbar()


def beim_start() -> None:
    """Letztes Ergebnis laden, damit der Hinweis nach einem Neustart sofort wieder erscheint."""
    global _neueste
    from app.db import SessionLocal
    try:
        with SessionLocal() as db:
            _neueste = status(db).get("neueste") or None
    except Exception:  # noqa: BLE001 – ohne Datenbank (z. B. Tests) kein Hinweis
        _neueste = None


def automatisch_pruefen() -> None:
    """Vom Taktgeber minütlich aufgerufen: einmal täglich nachsehen (wenn eingeschaltet)."""
    from app.db import SessionLocal
    from app.services import app_einstellungen
    with SessionLocal() as db:
        if not app_einstellungen.laden(db).update_pruefen or not laufende():
            return
        st = status(db)
        if st.get("zeit") and datetime.now() - datetime.fromisoformat(st["zeit"]) < timedelta(days=1):
            return
        if neu := pruefen(db):
            log.info("Update verfügbar: v%s (installiert: v%s)", neu, laufende())
