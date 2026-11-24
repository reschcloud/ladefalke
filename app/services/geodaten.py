"""Geodaten automatisch aktuell halten: GeoNames-Postleitzahlen (CC-BY 4.0) für die Verortung der Ladeorte.

Der Taktgeber der App prüft minütlich, ob ein Abgleich fällig ist (Einstellung „geodaten_auto“, Standard an):
- noch keine Postleitzahlen in der Datenbank (erster Start),
- die App-Version hat sich seit dem letzten Abgleich geändert (nach einem Update),
- der letzte erfolgreiche Abgleich ist 30 Tage alt; nach einem Fehlschlag neuer Versuch nach einem Tag.
Quelle: Download von download.geonames.org (es werden keine Daten gesendet); geht der nicht, eine Datei
allCountries.zip bzw. allCountries.txt in GEO_DIR (data/geo, z. B. für Server ohne Internet). Danach werden alle
Ladeorte neu verortet. Schlägt etwas fehl, bleibt der bisherige Stand erhalten.
Die Kartengrundlagen (Grenzen, Orte, Relief) liegen fertig im Image (app/static/geo) und brauchen nichts.
"""
import json
import logging
import shutil
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import einstellungen as konfiguration
from app.models import Einstellung, GeoPlz
from app.services import geokodierung
from app.version import version

log = logging.getLogger(__name__)

URL = "https://download.geonames.org/export/zip/allCountries.zip"
MAX_BYTES = 200 * 1024 * 1024  # Schutz vor unerwartet großen Antworten (die Datei hat rund 20 MB)
STATUS = "geodaten_status"
_sperre = threading.Lock()  # nie zwei Abgleiche gleichzeitig (Taktgeber und Knopf)


def status(db: Session) -> dict:
    e = db.get(Einstellung, STATUS)
    try:
        st = json.loads(e.wert) if e and e.wert else {}
    except ValueError:
        st = {}
    st["anzahl"] = db.scalar(select(func.count(GeoPlz.id))) or 0
    st["laeuft"] = _sperre.locked()
    return st


def status_laeuft() -> bool:
    return _sperre.locked()


def _status_schreiben(db: Session, ok: bool, text: str, quelle: str) -> None:
    from app.services import app_einstellungen
    app_einstellungen.speichern(db, {STATUS: json.dumps(
        {"zeit": datetime.now().isoformat(timespec="seconds"), "ok": ok, "text": text, "quelle": quelle,
         "version": version()}, ensure_ascii=False)})


def faellig(st: dict, jetzt: datetime, app_version: str) -> bool:
    """Noch nie gelaufen; nach einem Fehlschlag einen Tag später; sonst bei leerer Tabelle, nach einem Update oder
    nach 30 Tagen."""
    if not st.get("zeit"):
        return True
    alter = jetzt - datetime.fromisoformat(st["zeit"])
    if not st.get("ok"):
        return alter >= timedelta(days=1)
    return not st.get("anzahl") or st.get("version") != app_version or alter >= timedelta(days=30)


def herunterladen(ziel: Path) -> None:
    anfrage = urllib.request.Request(URL, headers={"User-Agent": "Ladefalke (Postleitzahlen, GeoNames)"})
    with urllib.request.urlopen(anfrage, timeout=120) as antwort, open(ziel, "wb") as datei:  # noqa: S310 – feste https-Adresse
        geschrieben = 0
        while block := antwort.read(1024 * 1024):
            geschrieben += len(block)
            if geschrieben > MAX_BYTES:
                raise ValueError("Die heruntergeladene Datei ist zu groß.")
            datei.write(block)


def lokale_datei() -> Path | None:
    ordner = Path(konfiguration().geo_dir)
    return next((p for p in (ordner / "allCountries.zip", ordner / "allCountries.txt") if p.is_file()), None)


def _einspielen(db: Session, pfad: Path, quelle: str) -> str:
    anzahl = geokodierung.einlesen(db, pfad)
    verortet = geokodierung.alle_geokodieren(db)
    genau = sum(n for g, n in verortet.items() if g in ("plz", "ort", "manuell"))
    text = f"{anzahl:,} Postleitzahlen".replace(",", ".") + f", {genau} von {sum(verortet.values())} Ladeorten verortet"
    _status_schreiben(db, True, text, quelle)
    db.commit()
    return text


def aktualisieren(db: Session, quelle: str = "download") -> str:
    """Postleitzahlen laden (Download, sonst lokale Datei) und alle Ladeorte neu verorten; liefert den Ergebnistext.
    Wirft bei Fehlern (Status dann mit Fehlertext); der bisherige Stand bleibt."""
    if not _sperre.acquire(blocking=False):
        raise RuntimeError("Der Abgleich läuft bereits.")
    ordner = Path(tempfile.mkdtemp(prefix="ladefalke-geo-"))
    try:
        try:
            herunterladen(ordner / "allCountries.zip")
            return _einspielen(db, ordner / "allCountries.zip", quelle)
        except Exception as e:  # noqa: BLE001 – Rückfall auf die lokale Datei
            db.rollback()
            fehler = e
        if (pfad := lokale_datei()) is not None:
            try:
                return _einspielen(db, pfad, "datei")
            except Exception as e:  # noqa: BLE001
                db.rollback()
                fehler = e
        text = str(fehler) if isinstance(fehler, ValueError) else f"{type(fehler).__name__}: {fehler}"
        _status_schreiben(db, False, text, quelle)
        db.commit()
        raise fehler
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
        _sperre.release()


def im_hintergrund() -> None:
    """Abgleich in einem eigenen Thread (Knopf „Jetzt aktualisieren“ – dauert etwa eine Minute)."""
    def lauf():
        from app.db import SessionLocal
        with SessionLocal() as db:
            try:
                log.info("Geodaten: %s", aktualisieren(db, "manuell"))
            except Exception:  # noqa: BLE001 – Status zeigt den Fehler
                log.exception("Geodaten: Abgleich fehlgeschlagen")
    threading.Thread(target=lauf, name="geodaten", daemon=True).start()


def automatisch_pruefen() -> None:
    """Vom Taktgeber der App minütlich aufgerufen: fälligen Abgleich ausführen (wenn eingeschaltet)."""
    from app.db import SessionLocal
    from app.services import app_einstellungen
    with SessionLocal() as db:
        if not app_einstellungen.laden(db).geodaten_auto or _sperre.locked():
            return
        if not faellig(status(db), datetime.now(), version()):
            return
        try:
            log.info("Geodaten: %s", aktualisieren(db, "automatisch"))
        except Exception:  # noqa: BLE001 – Status zeigt den Fehler, nächster Versuch morgen
            log.warning("Geodaten: Abgleich fehlgeschlagen (nächster Versuch morgen)", exc_info=True)
