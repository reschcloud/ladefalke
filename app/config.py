"""Konfiguration aus Umgebungsvariablen bzw. `.env`."""
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Einstellungen(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # Nur für die Erstinstallation, beide optional: gibt es noch keine Benutzer:innen, wird damit beim ersten
    # Anmeldeversuch der erste Admin angelegt. Ohne Passwort: „Ersten Admin anlegen“ im Browser bzw.
    # `python -m werkzeuge.admin` (siehe README „Admin-Zugang“). Danach aus .env entfernen (setup.sh tut das).
    admin_user: str = "admin"
    admin_password: str = ""
    secret_key: str
    tz: str = "Europe/Vienna"
    upload_dir: str = "/data/uploads"
    geo_dir: str = "/data/geo"  # GeoNames-Postleitzahlen (allCountries.txt), siehe README
    backup_dir: str = "/data/backups"
    # gesetzt: alle Sicherungen damit verschlüsselt (AES-256-GCM, Schlüssel per scrypt); verloren = Sicherung wertlos
    backup_passwort: str = ""
    # Sitzungs-Cookie nur über HTTPS senden: Standard ja (eigenes TLS oder Reverse Proxy mit HTTPS);
    # nur für reinen HTTP-Testbetrieb ohne Proxy auf false setzen
    cookie_secure: bool = True
    # Quelltext dieser Installation (AGPL-3.0, Abschnitt 13: Nutzer:innen über das Netz bekommen den Quelltext
    # angeboten) – wer eine veränderte Version betreibt, trägt hier die Adresse des eigenen Quelltexts ein
    quelltext_url: str = "https://github.com/reschcloud/ladefalke"


@lru_cache
def einstellungen() -> Einstellungen:
    return Einstellungen()


def heute() -> date:
    """Heutiges Datum in der konfigurierten Zeitzone."""
    return datetime.now(ZoneInfo(einstellungen().tz)).date()


def jetzt_lokal() -> datetime:
    """Jetzt in der konfigurierten Zeitzone, ohne Zeitzonenangabe (wie die gespeicherten lokalen Zeitpunkte)."""
    return datetime.now(ZoneInfo(einstellungen().tz)).replace(tzinfo=None)
