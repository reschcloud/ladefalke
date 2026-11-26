"""E-Mail: Adressen bei Fahrer:innen und Benutzer:innen, Mail-Einstellungen (Passwort verschlüsselt), Versand, Testmail."""
import smtplib

import pytest
from sqlalchemy import select

from app.config import einstellungen
from app.formatierung import email_lesen
from app.models import Benutzer, Einstellung, Fahrer
from app.services import app_einstellungen, geheim, mail
from tests.test_benutzer import rollen  # noqa: F401  (Fixture)

MAIL = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30",
        "mail_server": "smtp.example.net", "mail_port": "587", "mail_verbindung": "starttls",
        "mail_benutzer": "ladefalke@example.net", "mail_passwort": "streng-geheim", "mail_absender": "ladefalke@example.net",
        "mail_absender_name": "Ladefalke", "mail_antwort_an": ""}


class ScheinServer:
    """Ersetzt smtplib.SMTP: zeichnet Verbindung, Anmeldung und Nachrichten auf."""
    gesendet: list = []
    fehler: Exception | None = None

    def __init__(self, server, port, timeout=None, **_):
        self.server, self.port, self.tls, self.login_daten = server, port, False, None
        if ScheinServer.fehler:
            raise ScheinServer.fehler

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, benutzer, passwort):
        self.login_daten = (benutzer, passwort)

    def send_message(self, nachricht):
        ScheinServer.gesendet.append((self, nachricht))


@pytest.fixture
def schein(monkeypatch):
    ScheinServer.gesendet, ScheinServer.fehler = [], None
    monkeypatch.setattr(mail.smtplib, "SMTP", ScheinServer)
    return ScheinServer


def test_email_pruefen():
    assert email_lesen(" anna@example.com ") == "anna@example.com" and email_lesen("") is None
    for falsch in ("anna", "anna@", "anna@firma", "an na@example.com"):
        with pytest.raises(ValueError):
            email_lesen(falsch)


def test_email_bei_fahrer_und_benutzer(rollen, db):  # noqa: F811
    admin, operator, viewer = rollen["admin"], rollen["operator"], rollen["viewer"]
    antwort = operator.post("/stammdaten/fahrer/neu", data={"name": "Mail Test", "aktiv": "on", "email": "mail@test.example"})
    assert antwort.status_code == 200
    f = db.scalar(select(Fahrer).where(Fahrer.name == "Mail Test"))
    assert f.email == "mail@test.example"
    assert 'title="E-Mail: mail@test.example"><svg class="symbol symbol-email"' in admin.get("/stammdaten/fahrer").text
    assert operator.post(f"/stammdaten/fahrer/{f.id}", data={"name": "Mail Test", "aktiv": "on",
                                                             "email": "kaputt"}).status_code == 422
    # Benutzerverwaltung (Admin) und Mein Konto (Admin, Operator – nicht Viewer)
    olga = db.scalar(select(Benutzer).where(Benutzer.benutzername == "olga"))
    admin.post(f"/benutzer/{olga.id}", data={"name": "Olga", "rolle": "operator", "aktiv": "on", "email": "olga@example.com"})
    db.refresh(olga)
    assert olga.email == "olga@example.com"
    operator.post("/konto/email", data={"email": "olga.neu@example.com"})
    db.refresh(olga)
    assert olga.email == "olga.neu@example.com"
    assert viewer.post("/konto/email", data={"email": "v@example.com"}).status_code == 422
    assert 'disabled title="Unzureichende Berechtigungen – die Adresse pflegt ein Admin"' in viewer.get("/konto").text


def test_mail_einstellungen_passwort_verschluesselt(angemeldet, db, monkeypatch):
    angemeldet.post("/einstellungen", data=MAIL)
    gespeichert = db.get(Einstellung, "mail_passwort").wert
    assert gespeichert and "streng-geheim" not in gespeichert and geheim.entschluesseln(gespeichert) == "streng-geheim"
    seite = angemeldet.get("/einstellungen").text
    assert "streng-geheim" not in seite and gespeichert not in seite  # nie ausgeben
    assert "Gespeichert – leer lassen, um es zu behalten." in seite and "Bereit zum Senden über smtp.example.net:587" in seite
    angemeldet.post("/einstellungen", data={**MAIL, "mail_passwort": ""})  # leer = behalten
    assert geheim.entschluesseln(db.get(Einstellung, "mail_passwort").wert) == "streng-geheim"
    # anderer SECRET_KEY (z. B. nach Umzug): Passwort unlesbar → Hinweis
    monkeypatch.setattr(einstellungen(), "secret_key", "ein-anderer-schluessel")
    k = mail.konfiguration(db)
    assert k.passwort is None and k.passwort_unlesbar and "neu eingeben" in k.fehlt
    assert "lässt sich nicht lesen" in angemeldet.get("/einstellungen").text
    monkeypatch.undo()
    angemeldet.post("/einstellungen", data={**MAIL, "mail_passwort": "", "mail_passwort_entfernen": "on"})
    assert db.get(Einstellung, "mail_passwort").wert == ""
    assert angemeldet.post("/einstellungen", data={**MAIL, "mail_port": "99999"}).status_code == 422
    assert angemeldet.post("/einstellungen", data={**MAIL, "mail_absender": "kein-mail"}).status_code == 422


def test_senden_mit_anhang(angemeldet, db, schein):
    angemeldet.post("/einstellungen", data={**MAIL, "mail_antwort_an": "fuhrpark@example.net"})
    mail.senden(db, "anna@example.com", "Report", "Hallo", [("report.pdf", b"%PDF-1.4", "application/pdf")])
    [(verbindung, nachricht)] = schein.gesendet
    assert verbindung.tls and verbindung.login_daten == ("ladefalke@example.net", "streng-geheim")
    assert nachricht["To"] == "anna@example.com" and nachricht["From"] == "Ladefalke <ladefalke@example.net>"
    assert nachricht["Reply-To"] == "fuhrpark@example.net" and nachricht["Subject"] == "Report"
    [anhang] = list(nachricht.iter_attachments())
    assert anhang.get_filename() == "report.pdf" and anhang.get_content() == b"%PDF-1.4"


@pytest.mark.parametrize("fehler, text", [
    (smtplib.SMTPAuthenticationError(535, b"nein"), "Anmeldung am Mailserver abgelehnt"),
    (ConnectionRefusedError("abgelehnt"), "nicht erreichbar"),
    (smtplib.SMTPServerDisconnected("weg"), "Verbindung getrennt"),
])
def test_fehlermeldungen(angemeldet, db, schein, fehler, text):
    angemeldet.post("/einstellungen", data=MAIL)
    schein.fehler = fehler
    with pytest.raises(mail.MailFehler, match=text):
        mail.senden(db, "anna@example.com", "x", "y")


def test_testmail(rollen, db, schein):  # noqa: F811
    admin = rollen["admin"]
    assert "Kein Mailserver eingetragen" in admin.post("/einstellungen/mail/test", data={"an": "a@b.example"}).text
    admin.post("/einstellungen", data=MAIL)
    assert "Testmail an a@b.example gesendet" in admin.post("/einstellungen/mail/test", data={"an": "a@b.example"}).text
    assert schein.gesendet[0][1]["Subject"] == "Ladefalke – Testmail"
    assert rollen["operator"].post("/einstellungen/mail/test", data={"an": "a@b.example"}).status_code == 403
    assert app_einstellungen.laden(db).mail_server == "smtp.example.net"
