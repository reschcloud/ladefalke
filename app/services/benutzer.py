"""Benutzer und Rollen.

Rollen: admin (alles), operator (alles außer Einstellungen, MSP/Parser ändern und Benutzerverwaltung), viewer (nur lesen).
Die Einstellungen sehen alle Rollen.
Passwörter als scrypt-Hash (Standardbibliothek). Erster Admin, solange es noch keine Benutzer:innen gibt: mit
ADMIN_USER/ADMIN_PASSWORD aus `.env` beim ersten Anmeldeversuch, sonst über „Ersten Admin anlegen“ im Browser
(`ersteinrichtung_offen`). Notfall (kein Admin mehr, Passwort vergessen): `python -m werkzeuge.admin` (`admin_setzen`).
Danach zählt nur die Benutzertabelle; die Angaben in `.env` werden nicht mehr gebraucht.
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
# vorausgewählter Zeitraum ohne Angabe in der Adresse; jeweils der Zeitraum, der den Vormonat enthält
ZEITRAEUME = {"monat": "Monat", "quartal": "Quartal", "halbjahr": "Halbjahr", "jahr": "Jahr", "alles": "Alles"}
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


def anzahl(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Benutzer)) or 0


def einrichten(db: Session) -> None:
    """Ersten Admin aus `.env` anlegen, solange es keinen Benutzer gibt (nur mit ADMIN_PASSWORD)."""
    e = einstellungen()
    if not e.admin_password or anzahl(db):
        return
    db.add(Benutzer(benutzername=e.admin_user, name=e.admin_user, rolle="admin",
                    passwort_hash=passwort_hash(e.admin_password)))
    db.flush()


def ersteinrichtung_offen(db: Session) -> bool:
    """Noch keine Benutzer:innen und kein ADMIN_PASSWORD: der erste Admin wird im Browser angelegt."""
    return not einstellungen().admin_password and not anzahl(db)


def env_admin_ueberfluessig(db: Session) -> bool:
    """ADMIN_PASSWORD steht noch in der Umgebung (.env), obwohl es schon Benutzer:innen gibt – Hinweis zum Entfernen."""
    return bool(einstellungen().admin_password) and anzahl(db) > 0


def admin_setzen(db: Session, benutzername: str, passwort: str | None = None) -> tuple[Benutzer, str, bool]:
    """Notfall-Zugang: Admin anlegen bzw. vorhandenes Konto zum aktiven Admin mit neuem Passwort machen.
    Ohne `passwort` ein zufälliges. Liefert (Benutzer, Passwort, neu angelegt)."""
    passwort = passwort or secrets.token_urlsafe(12)
    b = finden(db, benutzername)
    neu = b is None
    if neu:
        b = Benutzer(benutzername=benutzername.strip(), name=benutzername.strip(), rolle="admin",
                     passwort_hash=passwort_hash(passwort))
        db.add(b)
    else:
        b.rolle, b.aktiv, b.passwort_hash = "admin", True, passwort_hash(passwort)
    db.flush()
    return b, passwort, neu


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
