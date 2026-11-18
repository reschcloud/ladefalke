"""Backup per Kommando: Sicherung erstellen oder wiederherstellen (auch zum Umziehen auf einen anderen Server).

    docker compose exec app python -m werkzeuge.backup erstellen
    docker compose exec app python -m werkzeuge.backup liste
    docker compose exec -it app python -m werkzeuge.backup wiederherstellen /data/backups/<datei> [--ueberschreiben]
    python -m werkzeuge.backup vor-update   (start.sh: Sicherung, wenn Migrationen anstehen)

Wiederherstellen ersetzt Datenbank und Originaldateien. Verschlüsselte Sicherungen: Passwort aus BACKUP_PASSWORT
oder Eingabe (dafür `-it`). Danach die App neu starten: docker compose restart app.
"""
import getpass
import sys
from pathlib import Path

from app.config import einstellungen
from app.db import SessionLocal
from app.services import backup


def main(argumente: list[str]) -> int:
    if not argumente or argumente[0] not in ("erstellen", "liste", "wiederherstellen", "vor-update"):
        print(__doc__)
        return 2
    try:
        if argumente[0] == "erstellen":
            with SessionLocal() as db:
                datei = backup.erstellen(db, "manuell")
            print(f"Sicherung erstellt: {datei}")
        elif argumente[0] == "vor-update":
            with SessionLocal() as db:
                _, meldung = backup.vor_update(db)
            print(meldung)
        elif argumente[0] == "liste":
            for s in backup.liste():
                print(f"{s.datum:%d.%m.%Y %H:%M}  {s.art:<12} {s.groesse / 1048576:7.1f} MB  "
                      f"{'verschlüsselt' if s.verschluesselt else 'offen':<13}  {s.datei}")
        else:
            if len(argumente) < 2:
                print("Bitte die Sicherungsdatei angeben.")
                return 2
            pfad = Path(argumente[1])
            passwort = None
            if backup.ist_verschluesselt(pfad):
                passwort = einstellungen().backup_passwort or getpass.getpass("Passwort der Sicherung: ")
            for zeile in backup.wiederherstellen(pfad, passwort, ueberschreiben="--ueberschreiben" in argumente):
                print(zeile)
    except backup.BackupFehler as e:
        print(f"Fehler: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
