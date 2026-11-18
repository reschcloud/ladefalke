"""Benutzer und Rollen.

Rollen: admin (alles), operator (alles außer Einstellungen, MSP/Parser ändern und Benutzerverwaltung), viewer (nur lesen).
Die Einstellungen sehen alle Rollen.
Passwörter als scrypt-Hash (Standardbibliothek). Gibt es noch keinen Benutzer, wird beim ersten Anmeldeversuch der
Admin aus `.env` (ADMIN_USER/ADMIN_PASSWORD) angelegt – danach zählt nur die Benutzertabelle.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import einstellungen
from app.models import Benutzer

ROLLEN = {"admin": "Admin", "operator": "Operator", "viewer": "Viewer"}
ROLLEN_TEXT = {"admin": "alles, auch Einstellungen ändern und Benutzer:innen verwalten",
               "operator": "alles außer Einstellungen und MSP ändern sowie Benutzerverwaltung",
               "viewer": "nur lesen (inkl. Exporte und Originaldokumente)"}
THEMES = {"auto": "Auto", "hell": "Hell", "dunkel": "Dunkel"}  # Auto = wie das Betriebssystem
MINDESTLAENGE = 10
_N, _R, _P = 2 ** 14, 8, 1


def passwort_hash(passwort: str) -> str:
    salz = secrets.token_bytes(16)
    wert = hashlib.scrypt(passwort.encode(), salt=salz, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salz.hex()}${wert.hex()}"


def passwort_pruefen(passwort: str, gespeichert: str) -> bool:
    try:
        verfahren, n, r, p, salz, wert = gespeichert.split("$")
        if verfahren != "scrypt":
            return False
        neu = hashlib.scrypt(passwort.encode(), salt=bytes.fromhex(salz), n=int(n), r=int(r), p=int(p),
                             dklen=len(bytes.fromhex(wert)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(neu, bytes.fromhex(wert))


_ATTRAPPE = passwort_hash(secrets.token_hex(8))  # gleiche Rechenzeit, wenn es den Benutzer nicht gibt


def finden(db: Session, benutzername: str) -> Benutzer | None:
    return db.scalar(select(Benutzer).where(func.lower(Benutzer.benutzername) == benutzername.strip().lower()))


def einrichten(db: Session) -> None:
    """Ersten Admin aus `.env` anlegen, solange es keinen Benutzer gibt."""
    if db.scalar(select(func.count()).select_from(Benutzer)):
        return
    e = einstellungen()
    db.add(Benutzer(benutzername=e.admin_user, name=e.admin_user, rolle="admin",
                    passwort_hash=passwort_hash(e.admin_password)))
    db.flush()


def anmelden(db: Session, benutzername: str, passwort: str) -> Benutzer | None:
    """Aktiver Benutzer bei richtigem Passwort, sonst None."""
    einrichten(db)
    b = finden(db, benutzername)
    if b is None:
        passwort_pruefen(passwort, _ATTRAPPE)
        return None
    if not passwort_pruefen(passwort, b.passwort_hash) or not b.aktiv:
        return None
    b.letzte_anmeldung = datetime.now(timezone.utc)
    return b


def passwort_fehler(passwort: str, wiederholung: str) -> str | None:
    if len(passwort) < MINDESTLAENGE:
        return f"Das Passwort muss mindestens {MINDESTLAENGE} Zeichen haben."
    if passwort != wiederholung:
        return "Die beiden Passwörter stimmen nicht überein."
    return None


def aktive_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Benutzer).where(Benutzer.rolle == "admin", Benutzer.aktiv)) or 0


def darf(b: Benutzer | None, recht: str) -> bool:
    """Rechte: „lesen“ (alle), „schreiben“ (admin, operator), „verwalten“ (nur admin: Einstellungen, Benutzer)."""
    if b is None or not b.aktiv:
        return False
    return {"lesen": True, "schreiben": b.rolle in ("admin", "operator"), "verwalten": b.rolle == "admin"}[recht]
