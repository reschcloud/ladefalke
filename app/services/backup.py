"""Backup: Sicherung von Daten, Einstellungen und Benutzer:innen samt Originaldateien – auch zum Umziehen.

Eine Sicherung ist eine Datei `ladefalke-backup-JJJJ-MM-TT_HHMMSS[-auto|-update].tar.gz` (verschlüsselt: `….tar.gz.enc`) in
BACKUP_DIR mit
- `datenbank.dump`: pg_dump der ganzen Datenbank (Daten, Einstellungen, Benutzer:innen mit Passwort-Hashes,
  eingespielte Postleitzahlen),
- `uploads/`: die hochgeladenen Originaldateien,
- `parser/`: Kopie der installierten Parser-Module (nur zur Kontrolle/Übernahme von Hand – beim Wiederherstellen
  wird kein Code aus der Sicherung geladen),
- `manifest.json`: Datum, Art, App-Version, Datenbankstand (Alembic), Parser mit Prüfsumme, Anzahlen.
Daneben liegt `<name>.json` mit dem Manifest unverschlüsselt (für die Liste; enthält nur Zahlen, keine Personendaten).

Verschlüsselung: ist BACKUP_PASSWORT gesetzt, wird das Archiv mit AES-256-GCM verschlüsselt (Schlüssel per scrypt).
Zeitplan (Einstellungen): aus / täglich / wöchentlich zu einer Uhrzeit; automatische Sicherungen über die Anzahl
„behalten“ hinaus werden gelöscht, manuelle bleiben. Stand des letzten automatischen Laufs in `status.json`.
Wiederherstellen nur per Kommando (werkzeuge/backup.py), nie über die Oberfläche.
"""
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import einstellungen
from app.models import Benutzer, Fahrer, Fahrzeug, Import, Ladekarte, Ladevorgang, Msp
from app.version import version

log = logging.getLogger(__name__)

PRAEFIX = "ladefalke-backup-"
MAGIC = b"LADEFALKE-BACKUP-1\n"  # Kopf verschlüsselter Sicherungen
PARSER_VERZEICHNIS = Path(__file__).resolve().parent.parent / "parsers"
ANZAHLEN = {"ladevorgaenge": Ladevorgang, "importe": Import, "ladekarten": Ladekarte, "fahrzeuge": Fahrzeug,
            "fahrer": Fahrer, "msp": Msp, "benutzer": Benutzer}


class BackupFehler(Exception):
    pass


def verzeichnis() -> Path:
    return Path(einstellungen().backup_dir)


def _jetzt() -> datetime:
    return datetime.now(ZoneInfo(einstellungen().tz)).replace(tzinfo=None)


# ------------------------------------------------------------------ Verschlüsselung

def _schluessel(passwort: str, salz: bytes) -> bytes:
    return Scrypt(salt=salz, length=32, n=2 ** 15, r=8, p=1).derive(passwort.encode())


def verschluesseln(daten: bytes, passwort: str) -> bytes:
    salz, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salz + nonce + AESGCM(_schluessel(passwort, salz)).encrypt(nonce, daten, MAGIC)


def entschluesseln(daten: bytes, passwort: str) -> bytes:
    if not daten.startswith(MAGIC):
        raise BackupFehler("Keine verschlüsselte Ladefalke-Sicherung.")
    kopf = len(MAGIC)
    salz, nonce, rest = daten[kopf:kopf + 16], daten[kopf + 16:kopf + 28], daten[kopf + 28:]
    try:
        return AESGCM(_schluessel(passwort, salz)).decrypt(nonce, rest, MAGIC)
    except InvalidTag:
        raise BackupFehler("Passwort falsch oder Datei beschädigt.") from None


def ist_verschluesselt(pfad: Path) -> bool:
    with open(pfad, "rb") as f:
        return f.read(len(MAGIC)) == MAGIC


# ------------------------------------------------------------------ Erstellen

def _pg(url: str) -> tuple[list[str], dict]:
    u = make_url(url)
    argumente = ["-h", u.host or "localhost", "-p", str(u.port or 5432), "-U", u.username or "", "-d", u.database or ""]
    return argumente, {**os.environ, "PGPASSWORD": u.password or ""}


def _pruefsumme(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


def installierte_parser() -> dict[str, str]:
    """Parser-Module (Dateiname → SHA-256) – alle .py-Dateien in app/parsers/."""
    return {p.name: _pruefsumme(p.read_bytes()) for p in sorted(PARSER_VERZEICHNIS.glob("*.py"))}


def _revision(db: Session) -> str | None:
    return db.scalar(text("SELECT version_num FROM alembic_version"))


def manifest(db: Session, art: str, verschluesselt: bool) -> dict:
    uploads = Path(einstellungen().upload_dir)
    return {
        "format": 1, "datum": _jetzt().isoformat(timespec="seconds"), "art": art, "app_version": version(),
        "datenbankstand": _revision(db), "verschluesselt": verschluesselt, "parser": installierte_parser(),
        "anzahlen": {name: db.scalar(select(func.count()).select_from(modell)) for name, modell in ANZAHLEN.items()}
        | {"originaldateien": sum(1 for p in uploads.iterdir() if p.is_file()) if uploads.is_dir() else 0},
    }


VOR_UPDATE = "vor Update"  # Art der Sicherung, die start.sh vor anstehenden Migrationen anlegt (nie aufgeräumt)


def vor_update(db: Session) -> tuple[Path | None, str]:
    """Beim Start vor `alembic upgrade head` (start.sh): stehen Migrationen an, zuerst eine Sicherung anlegen.

    Ergebnis (Datei oder None, Meldung). Leere Datenbank (Erstinstallation) und aktueller Stand: keine Sicherung.
    Ist der Datenbankstand der App unbekannt (Datenbank neuer als die App, z. B. nach einem Rückschritt auf ein
    älteres Release ohne Wiederherstellen), BackupFehler – migriert werden darf dann nicht."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    if not inspect(db.get_bind()).has_table("alembic_version") or (stand := _revision(db)) is None:
        return None, "Neue Datenbank – keine Sicherung vor dem Update nötig."
    skripte = ScriptDirectory.from_config(Config("alembic.ini"))
    if stand == skripte.get_current_head():
        return None, "Datenbank ist aktuell – keine Migration nötig."
    if stand not in {r.revision for r in skripte.walk_revisions()}:
        raise BackupFehler(f"Die Datenbank (Stand {stand}) ist neuer als diese App-Version. Für einen Rückschritt "
                           "die Sicherung von vor dem Update wiederherstellen (siehe README „Betrieb“).")
    ziel = skripte.get_current_head()
    # Schlägt die Migration fehl, startet Docker den Container immer wieder: dann nicht jedes Mal neu sichern
    if vorhanden := next((s for s in liste() if s.art == VOR_UPDATE and s.manifest.get("datenbankstand") == stand
                          and s.manifest.get("migration_ziel") == ziel), None):
        return None, f"Migration {stand} → {ziel}: Sicherung {vorhanden.name} von vor diesem Update ist schon da."
    datei = erstellen(db, VOR_UPDATE, zusatz={"migration_ziel": ziel})
    return datei, f"Migration steht an ({stand} → {ziel}): Sicherung {datei.name} erstellt."


def erstellen(db: Session, art: str = "manuell", passwort: str | None = None, zusatz: dict | None = None) -> Path:
    """Neue Sicherung in BACKUP_DIR; `passwort` None = BACKUP_PASSWORT aus der Konfiguration, "" = unverschlüsselt.
    `zusatz`: weitere Angaben fürs Manifest (z. B. Ziel der Migration bei VOR_UPDATE)."""
    passwort = einstellungen().backup_passwort if passwort is None else passwort
    ziel = verzeichnis()
    ziel.mkdir(parents=True, exist_ok=True)
    basis = PRAEFIX + _jetzt().strftime("%Y-%m-%d_%H%M%S") + {"automatisch": "-auto", VOR_UPDATE: "-update"}.get(art, "")
    name, n = basis, 1
    while any(ziel.glob(name + ".*")):  # zwei Sicherungen in derselben Sekunde
        n += 1
        name = f"{basis}-{n}"
    daten = manifest(db, art, bool(passwort)) | (zusatz or {})
    with tempfile.TemporaryDirectory(dir=ziel, prefix=".entsteht-") as tmp:
        tmp = Path(tmp)
        argumente, umgebung = _pg(einstellungen().database_url)
        try:
            subprocess.run(["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", str(tmp / "datenbank.dump"),
                            *argumente], env=umgebung, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise BackupFehler("pg_dump ist nicht installiert (App-Image neu bauen).") from None
        except subprocess.CalledProcessError as e:
            raise BackupFehler(f"pg_dump fehlgeschlagen: {e.stderr.strip()}") from None
        archiv = tmp / "archiv.tar.gz"
        with tarfile.open(archiv, "w:gz") as tar:
            tar.add(tmp / "datenbank.dump", arcname="datenbank.dump")
            uploads = Path(einstellungen().upload_dir)
            if uploads.is_dir():
                tar.add(uploads, arcname="uploads")
            for datei in sorted(PARSER_VERZEICHNIS.glob("*.py")):
                tar.add(datei, arcname=f"parser/{datei.name}")
            inhalt = json.dumps(daten, ensure_ascii=False, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime = len(inhalt), int(_jetzt().timestamp())
            tar.addfile(info, io.BytesIO(inhalt))
        endung = ".tar.gz.enc" if passwort else ".tar.gz"
        fertig = tmp / (name + endung)
        if passwort:
            fertig.write_bytes(verschluesseln(archiv.read_bytes(), passwort))
        else:
            archiv.rename(fertig)
        os.replace(fertig, ziel / fertig.name)
    daten |= {"datei": name + endung, "groesse": (ziel / (name + endung)).stat().st_size}
    (ziel / f"{name}.json").write_text(json.dumps(daten, ensure_ascii=False, indent=2))
    return ziel / (name + endung)


# ------------------------------------------------------------------ Liste, Löschen, Aufräumen

@dataclass
class Sicherung:
    name: str  # ohne Endung
    datei: Path
    datum: datetime
    art: str
    groesse: int
    verschluesselt: bool
    app_version: str = ""
    anzahlen: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def liste() -> list[Sicherung]:
    """Vorhandene Sicherungen, neueste zuerst."""
    ziel = verzeichnis()
    ergebnis = []
    for datei in ziel.glob(PRAEFIX + "*.tar.gz*") if ziel.is_dir() else []:
        name = datei.name.removesuffix(".enc").removesuffix(".tar.gz")
        try:
            m = json.loads((ziel / f"{name}.json").read_text())
        except (OSError, ValueError):
            m = {}
        try:
            datum = datetime.fromisoformat(m["datum"])
        except (KeyError, ValueError):
            datum = datetime.fromtimestamp(datei.stat().st_mtime)
        ergebnis.append(Sicherung(name, datei, datum, m.get("art", "manuell"), datei.stat().st_size,
                                  datei.name.endswith(".enc"), m.get("app_version", ""), m.get("anzahlen", {}), m))
    return sorted(ergebnis, key=lambda s: s.datum, reverse=True)


def finden(name: str) -> Sicherung | None:
    return next((s for s in liste() if s.name == name), None)


def loeschen(name: str) -> bool:
    s = finden(name)
    if s is None:
        return False
    s.datei.unlink(missing_ok=True)
    (verzeichnis() / f"{s.name}.json").unlink(missing_ok=True)
    return True


def aufraeumen(behalten: int) -> list[str]:
    """Automatische Sicherungen über die neuesten `behalten` hinaus löschen; manuelle bleiben."""
    automatische = [s for s in liste() if s.art == "automatisch"]
    weg = [s.name for s in automatische[max(behalten, 1):]]
    for name in weg:
        loeschen(name)
    return weg


# ------------------------------------------------------------------ Zeitplan

def faellig(zeitplan: str, wochentag: int, uhrzeit: str, letzter: datetime | None, jetzt: datetime) -> bool:
    """Ist seit dem letzten automatischen Lauf ein Termin vergangen? Ohne bisherigen Lauf: sofort (holt nach)."""
    if zeitplan not in ("taeglich", "woechentlich"):
        return False
    stunde, minute = (int(x) for x in uhrzeit.split(":"))
    termin = jetzt.replace(hour=stunde, minute=minute, second=0, microsecond=0)
    if zeitplan == "woechentlich":
        termin -= timedelta(days=(jetzt.weekday() - wochentag) % 7)
        if termin > jetzt:
            termin -= timedelta(days=7)
    elif termin > jetzt:
        termin -= timedelta(days=1)
    return letzter is None or letzter < termin


def status() -> dict:
    try:
        return json.loads((verzeichnis() / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def _status_schreiben(erfolg: bool, meldung: str) -> None:
    verzeichnis().mkdir(parents=True, exist_ok=True)
    (verzeichnis() / "status.json").write_text(json.dumps(
        {"letzter_lauf": _jetzt().isoformat(timespec="seconds"), "erfolg": erfolg, "meldung": meldung},
        ensure_ascii=False))


def automatisch_pruefen() -> None:
    """Vom Taktgeber der App minütlich aufgerufen: fällige automatische Sicherung erstellen und aufräumen."""
    from app.db import SessionLocal
    from app.services import app_einstellungen
    with SessionLocal() as db:
        e = app_einstellungen.laden(db)
        letzter = status().get("letzter_lauf")
        if not faellig(e.backup_zeitplan, e.backup_wochentag, e.backup_uhrzeit,
                       datetime.fromisoformat(letzter) if letzter else None, _jetzt()):
            return
        try:
            datei = erstellen(db, "automatisch")
            weg = aufraeumen(e.backup_behalten)
            protokollieren(db, "erstellt", datei.name, {"Art": [None, "automatisch"]} | (
                {"alte gelöscht": [None, ", ".join(weg)]} if weg else {}))
            db.commit()
            _status_schreiben(True, f"{datei.name} erstellt" + (f", {len(weg)} alte gelöscht" if weg else ""))
        except Exception as fehler:  # noqa: BLE001 – Fehler landen in der Statusanzeige
            log.exception("Automatische Sicherung fehlgeschlagen")
            _status_schreiben(False, str(fehler))


def protokollieren(db: Session, aktion: str, name: str, felder: dict | None = None) -> None:
    """Eintrag im Änderungsprotokoll (Sicherung erstellt/gelöscht)."""
    from app.services import protokoll
    protokoll.eintragen(db, aktion, "backup", name, felder=felder)


def _wiederherstellung_protokollieren(url: str, m: dict) -> None:
    """Nach dem Einspielen: Eintrag in der wiederhergestellten Datenbank und an Syslog (Warnung)."""
    from sqlalchemy.orm import Session as Sitzung
    from app.services import protokoll, syslog
    motor = create_engine(url)
    try:
        with Sitzung(motor) as db:
            syslog.laden(db)
            protokoll.eintragen(db, "wiederhergestellt", "backup", f"Sicherung vom {m['datum']} ({m['art']})",
                                felder={"App-Version": [None, m.get("app_version")]}, kontext_text="Kommandozeile")
            db.commit()
        syslog.warten(5)
    except Exception:  # noqa: BLE001 – die Wiederherstellung selbst ist gelungen
        log.exception("Wiederherstellung nicht protokolliert")
    finally:
        motor.dispose()


# ------------------------------------------------------------------ Wiederherstellen

def wiederherstellen(pfad: Path, passwort: str | None = None, ueberschreiben: bool = False,
                     datenbank_url: str | None = None) -> list[str]:
    """Spielt eine Sicherung ein: Datenbank und Originaldateien werden ersetzt. Liefert einen Bericht (Zeilen).
    Parser aus der Sicherung werden nur neben die Sicherung gelegt und verglichen, nie geladen."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    url = datenbank_url or einstellungen().database_url
    bericht = []
    roh = pfad.read_bytes()
    if roh.startswith(MAGIC):
        if not passwort:
            raise BackupFehler("Die Sicherung ist verschlüsselt – Passwort angeben (BACKUP_PASSWORT oder Eingabe).")
        roh = entschluesseln(roh, passwort)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "archiv.tar.gz").write_bytes(roh)
        with tarfile.open(tmp / "archiv.tar.gz") as tar:
            tar.extractall(tmp / "inhalt", filter="data")
        inhalt = tmp / "inhalt"
        try:
            m = json.loads((inhalt / "manifest.json").read_text())
        except (OSError, ValueError):
            raise BackupFehler("Kein gültiges Manifest – keine Ladefalke-Sicherung?") from None
        bericht.append(f"Sicherung vom {m['datum']} ({m['art']}), App-Version {m['app_version']}, "
                       f"Datenbankstand {m['datenbankstand']}.")

        # Datenbankstand: Sicherungen einer neueren App-Version werden abgelehnt
        skripte = ScriptDirectory.from_config(Config("alembic.ini"))
        bekannt = {r.revision for r in skripte.walk_revisions()}
        if m["datenbankstand"] not in bekannt:
            raise BackupFehler(f"Die Sicherung stammt von einer neueren App-Version (Datenbankstand "
                               f"{m['datenbankstand']}) – zuerst die App aktualisieren.")
        aelter = m["datenbankstand"] != skripte.get_current_head()
        if aelter and datenbank_url and datenbank_url != einstellungen().database_url:
            raise BackupFehler("Ältere Sicherungen lassen sich nur in die Datenbank der App einspielen.")

        # Ziel leer oder ausdrücklich überschreiben
        motor = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with motor.connect() as conn:
                vorhanden = "ladevorgang" in inspect(conn).get_table_names() and conn.scalar(
                    text("SELECT count(*) FROM ladevorgang")) > 0
                if vorhanden and not ueberschreiben:
                    raise BackupFehler("Die Datenbank enthält bereits Daten – zum Ersetzen --ueberschreiben angeben.")
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
        finally:
            motor.dispose()
        argumente, umgebung = _pg(url)
        try:
            subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
                            *argumente, str(inhalt / "datenbank.dump")],
                           env=umgebung, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise BackupFehler(f"pg_restore fehlgeschlagen: {e.stderr.strip()}") from None
        bericht.append("Datenbank eingespielt: " + ", ".join(f"{n} {k}" for k, n in m["anzahlen"].items()) + ".")
        if aelter:
            from alembic import command
            command.upgrade(Config("alembic.ini"), "head")
            bericht.append(f"Datenbank von Stand {m['datenbankstand']} auf {skripte.get_current_head()} angehoben.")

        # Originaldateien ersetzen
        uploads = Path(einstellungen().upload_dir)
        uploads.mkdir(parents=True, exist_ok=True)
        for alt in uploads.iterdir():
            if alt.is_file():
                alt.unlink()
        quelle = inhalt / "uploads"
        anzahl = 0
        for datei in quelle.iterdir() if quelle.is_dir() else []:
            if datei.is_file():
                shutil.copy2(datei, uploads / datei.name)
                anzahl += 1
        bericht.append(f"{anzahl} Originaldateien wiederhergestellt.")

        # Parser vergleichen und nur ablegen
        jetzt_installiert = installierte_parser()
        fehlen = sorted(set(m["parser"]) - set(jetzt_installiert))
        anders = sorted(n for n, s in m["parser"].items() if n in jetzt_installiert and jetzt_installiert[n] != s)
        if fehlen or anders:
            ablage = pfad.parent / f"{pfad.name.split('.tar.gz')[0]}_parser"
            ablage.mkdir(exist_ok=True)
            for n in fehlen + anders:
                shutil.copy2(inhalt / "parser" / n, ablage / n)
            if fehlen:
                bericht.append(f"Parser aus der Sicherung, die hier fehlen: {', '.join(fehlen)}.")
            if anders:
                bericht.append(f"Parser mit anderem Stand als hier: {', '.join(anders)}.")
            bericht.append(f"Kopien liegen in {ablage} – bei Bedarf von Hand nach app/parsers/ übernehmen und das "
                           "Image neu bauen (Code aus Sicherungen wird nie automatisch geladen).")
        else:
            bericht.append("Parser: gleicher Stand wie in der Sicherung.")
    _wiederherstellung_protokollieren(url, m)
    bericht.append("Fertig. Bitte die App neu starten (docker compose restart app).")
    return bericht
