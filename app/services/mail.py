"""Mail-Versand über SMTP – Zugangsdaten aus den Einstellungen (Gruppe „E-Mail“, nur Admin).

Das Passwort liegt verschlüsselt in der Datenbank (services/geheim.py). `senden` verschickt eine Nachricht mit
optionalen Anhängen (z. B. PDF-Reports) und meldet Fehler als `MailFehler` mit verständlichem deutschem Text.
Nur Pythons eigenes smtplib, keine weitere Abhängigkeit.
"""
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from sqlalchemy.orm import Session

from app.services import app_einstellungen, geheim

VERBINDUNGEN = {"starttls": "STARTTLS (meist Port 587)", "ssl": "SSL/TLS (meist Port 465)",
                "keine": "unverschlüsselt (nicht empfohlen)"}
ZEITLIMIT = 20  # Sekunden


class MailFehler(Exception):
    pass


@dataclass
class Konfiguration:
    server: str
    port: int
    verbindung: str
    benutzer: str
    passwort: str | None  # None: keins gespeichert bzw. nicht lesbar (anderer SECRET_KEY)
    passwort_unlesbar: bool
    absender: str
    absender_name: str
    antwort_an: str

    @property
    def fehlt(self) -> str | None:
        """Was noch fehlt, damit gesendet werden kann (oder None)."""
        if not self.server:
            return "Kein Mailserver eingetragen (Einstellungen → E-Mail)."
        if not self.absender:
            return "Keine Absenderadresse eingetragen (Einstellungen → E-Mail)."
        if self.passwort_unlesbar:
            return "Das gespeicherte Passwort lässt sich nicht lesen (anderer SECRET_KEY) – bitte neu eingeben."
        if self.benutzer and not self.passwort:
            return "Benutzername ohne Passwort – bitte das Passwort eintragen."
        return None


def konfiguration(db: Session) -> Konfiguration:
    e = app_einstellungen.laden(db)
    passwort = geheim.entschluesseln(e.mail_passwort) if e.mail_passwort else None
    return Konfiguration(e.mail_server, e.mail_port, e.mail_verbindung, e.mail_benutzer, passwort,
                         bool(e.mail_passwort) and passwort is None, e.mail_absender, e.mail_absender_name,
                         e.mail_antwort_an)


def senden(db: Session, an: str | list[str], betreff: str, text: str,
           anhaenge: list[tuple[str, bytes, str]] = ()) -> None:
    """Nachricht senden; `anhaenge`: (Dateiname, Inhalt, MIME-Typ wie „application/pdf“)."""
    k = konfiguration(db)
    if fehlt := k.fehlt:
        raise MailFehler(fehlt)
    empfaenger = [an] if isinstance(an, str) else list(an)
    nachricht = EmailMessage()
    nachricht["From"] = formataddr((k.absender_name, k.absender)) if k.absender_name else k.absender
    nachricht["To"] = ", ".join(empfaenger)
    if k.antwort_an:
        nachricht["Reply-To"] = k.antwort_an
    nachricht["Subject"] = betreff
    nachricht["Date"] = formatdate(localtime=True)
    nachricht["Message-ID"] = make_msgid(domain=k.absender.rsplit("@", 1)[-1])
    nachricht.set_content(text)
    for name, inhalt, mime in anhaenge:
        haupt, _, unter = mime.partition("/")
        nachricht.add_attachment(inhalt, maintype=haupt, subtype=unter or "octet-stream", filename=name)

    kontext = ssl.create_default_context()
    try:
        if k.verbindung == "ssl":
            verbindung = smtplib.SMTP_SSL(k.server, k.port, timeout=ZEITLIMIT, context=kontext)
        else:
            verbindung = smtplib.SMTP(k.server, k.port, timeout=ZEITLIMIT)
        with verbindung as s:
            if k.verbindung == "starttls":
                s.starttls(context=kontext)
            if k.benutzer:
                s.login(k.benutzer, k.passwort or "")
            s.send_message(nachricht)
    except smtplib.SMTPAuthenticationError:
        raise MailFehler("Anmeldung am Mailserver abgelehnt – Benutzername und Passwort prüfen.") from None
    except smtplib.SMTPRecipientsRefused as e:
        raise MailFehler(f"Empfänger abgelehnt: {', '.join(e.recipients)}.") from None
    except smtplib.SMTPSenderRefused as e:
        raise MailFehler(f"Absender {e.sender} abgelehnt – darf dieses Konto mit dieser Adresse senden?") from None
    except smtplib.SMTPNotSupportedError:
        raise MailFehler("Der Server unterstützt die gewählte Verbindungsart nicht (STARTTLS/SSL prüfen).") from None
    except smtplib.SMTPServerDisconnected:
        raise MailFehler("Der Mailserver hat die Verbindung getrennt – Verbindungsart und Port prüfen.") from None
    except smtplib.SMTPException as e:  # vor OSError: SMTPException ist eine Unterklasse davon
        raise MailFehler(f"Der Mailserver hat abgelehnt: {e}") from None
    except ssl.SSLError as e:
        raise MailFehler(f"TLS-Fehler ({e.reason or e}) – Verbindungsart und Port prüfen.") from None
    except OSError as e:  # Name unbekannt, Verbindung abgelehnt, Zeitüberschreitung
        raise MailFehler(f"Mailserver {k.server}:{k.port} nicht erreichbar ({e}).") from None
