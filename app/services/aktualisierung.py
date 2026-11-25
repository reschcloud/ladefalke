"""Hinweis auf neue Releases: einmal täglich die Versions-Tags (vX.Y.Z) auf GitHub abfragen (Einstellung
„update_pruefen“, Standard an; es werden keine Daten gesendet). Ist ein neueres Release als das laufende da, zeigt
die Oberfläche neben der Version „Update verfügbar“ und unter Einstellungen → System, wie man aktualisiert
(`sh setup.sh` im Installationsordner). Selbst gebaute Stände ohne Release-Version bekommen keinen Hinweis.

Als Release zählt ein Tag erst, wenn die Release-Pipeline das Image auf ghcr.io veröffentlicht hat (direkt nach
dem Taggen laufen noch die Tests) – sonst gäbe es den Hinweis, bevor `setup.sh` das Image laden kann.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Einstellung
from app.version import version

log = logging.getLogger(__name__)

REPO = "reschcloud/ladefalke"
URL = f"https://api.github.com/repos/{REPO}/tags?per_page=100"
REGISTRY = "https://ghcr.io"
IMAGE = "reschcloud/ladefalke"  # ghcr.io/reschcloud/ladefalke (compose.yaml)
MANIFEST_TYPEN = ", ".join([  # Image-Index (mehrere Plattformen) oder einzelnes Manifest, OCI bzw. Docker
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])
HOECHSTENS_PRUEFEN = 3  # so viele der neuesten Tags auf ein Image prüfen
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


def versionen_aus(tags: list[dict]) -> list[str]:
    """Release-Versionen aus den Tags, neueste zuerst."""
    versionen = {t["name"][1:] for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t.get("name", ""))}
    return sorted(versionen, key=_schluessel, reverse=True)


def neueste_aus(tags: list[dict]) -> str | None:
    versionen = versionen_aus(tags)
    return versionen[0] if versionen else None


def _holen(url: str, kopf: dict | None = None, methode: str = "GET"):
    anfrage = urllib.request.Request(url, method=methode, headers={"User-Agent": "Ladefalke (Update-Pruefung)",
                                                                   **(kopf or {})})
    return urllib.request.urlopen(anfrage, timeout=30)  # noqa: S310 – feste https-Adressen


def image_vorhanden(version: str, token: str) -> bool:
    """Gibt es ghcr.io/…/ladefalke:<version>? 404 = (noch) nicht gebaut; andere Fehler gehen nach oben."""
    try:
        with _holen(f"{REGISTRY}/v2/{IMAGE}/manifests/{version}",
                    {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPEN}, "HEAD"):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def _token() -> str:
    """Anonymes Lese-Token der Registry (öffentliches Image)."""
    with _holen(f"{REGISTRY}/token?scope=repository:{IMAGE}:pull") as antwort:
        return json.loads(antwort.read(64 * 1024))["token"]


def abfragen() -> str | None:
    """Neuestes Release mit fertigem Image (höchstens die drei neuesten Tags werden geprüft)."""
    with _holen(URL, {"Accept": "application/vnd.github+json"}) as antwort:
        versionen = versionen_aus(json.loads(antwort.read(2 * 1024 * 1024)))
    if not versionen:
        return None
    token = _token()
    return next((v for v in versionen[:HOECHSTENS_PRUEFEN] if image_vorhanden(v, token)), None)


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
