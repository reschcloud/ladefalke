"""Admin-Zugang per Kommando – für die Erstinstallation und den Notfall (kein Admin mehr, Passwort vergessen).

    docker compose exec app python -m werkzeuge.admin [benutzername]
        Konto als aktiven Admin anlegen bzw. vorhandenes Konto zum aktiven Admin machen, mit neuem Zufallspasswort
        (wird einmal angezeigt). Ohne Angabe: „admin“.
    docker compose exec app python -m werkzeuge.admin [benutzername] --nur-wenn-leer
        nur, solange es noch gar keine Benutzer:innen gibt (setup.sh bei der Installation); sonst nichts, Rückgabe 3
    docker compose exec app python -m werkzeuge.admin --vorhanden
        Rückgabe 0, wenn es Benutzer:innen gibt, sonst 1 (setup.sh: ADMIN_PASSWORD aus .env entfernen)

Im Änderungsprotokoll steht „System“. Wer Zugriff auf den Server hat, kommt so immer an einen Admin – deshalb
braucht es kein Passwort in `.env`.
"""
import sys

from app.db import SessionLocal
from app.services import benutzer


def main(argumente: list[str]) -> int:
    if "-h" in argumente or "--hilfe" in argumente:
        print(__doc__)
        return 0
    with SessionLocal() as db:
        if "--vorhanden" in argumente:
            return 0 if benutzer.anzahl(db) else 1
        namen = [a for a in argumente if not a.startswith("--")]
        name = namen[0] if namen else "admin"
        if "--nur-wenn-leer" in argumente and benutzer.anzahl(db):
            print("Es gibt schon Benutzer:innen – nichts geändert.", file=sys.stderr)
            return 3
        b, passwort, neu = benutzer.admin_setzen(db, name)
        db.commit()
        print(f"{'Admin angelegt' if neu else 'Neues Passwort für'}: {b.benutzername}")
        print(f"Passwort: {passwort}")
        print("Bitte nach der Anmeldung unter „Mein Konto“ ändern.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
