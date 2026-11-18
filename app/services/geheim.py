"""Geheimnisse in der Datenbank (z. B. Passwort des Mailservers) verschlüsselt ablegen.

Schlüssel aus SECRET_KEY (.env) abgeleitet; Fernet (AES-128-CBC + HMAC). Mit anderem SECRET_KEY (z. B. nach einem
Umzug ohne die alte .env) lässt sich ein gespeichertes Geheimnis nicht mehr lesen → `entschluesseln` liefert None,
die Oberfläche bittet um Neueingabe.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import einstellungen


def _fernet() -> Fernet:
    schluessel = hashlib.sha256(b"ladefalke-geheim:" + einstellungen().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(schluessel))


def verschluesseln(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def entschluesseln(token: str) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
