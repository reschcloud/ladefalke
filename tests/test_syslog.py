"""Syslog: Format (RFC 5424/3164), Rahmen, Versand über UDP/TCP/TLS an einen lokalen Empfänger, nach dem Commit,
App-Meldungen, Einstellungen und Testmeldung."""
import logging
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Fahrer
from app.services import syslog


def _konfig(**anders) -> syslog.Konfiguration:
    werte = dict(server="127.0.0.1", port=514, protokoll="udp", format="5424", facility="local0", hostname="fuhrpark",
                 ca="", aenderungen=True, app_meldungen=True, werte=True)
    return syslog.Konfiguration(**(werte | anders))


class Empfaenger:
    """Lokaler Syslog-Empfänger (UDP, TCP oder TLS) in einem Thread; sammelt die rohen Bytes."""

    def __init__(self, art="udp", tls_kontext=None):
        self.daten, self.fertig = b"", threading.Event()
        typ = socket.SOCK_DGRAM if art == "udp" else socket.SOCK_STREAM
        self.sock = socket.socket(socket.AF_INET, typ)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        if art != "udp":
            self.sock.listen(1)
        self.sock.settimeout(5)
        threading.Thread(target=self._lauf, args=(art, tls_kontext), daemon=True).start()

    def _lauf(self, art, tls_kontext):
        try:
            if art == "udp":
                self.daten, _ = self.sock.recvfrom(9000)
            else:
                conn, _ = self.sock.accept()
                if tls_kontext:
                    conn = tls_kontext.wrap_socket(conn, server_side=True)
                conn.settimeout(5)
                while chunk := conn.recv(9000):
                    self.daten += chunk
                conn.close()
        except (OSError, ssl.SSLError):
            pass
        finally:
            self.fertig.set()

    def warten(self):
        self.fertig.wait(5)
        self.sock.close()
        return self.daten


def test_format_5424_mit_strukturierten_daten():
    zeit = datetime(2026, 11, 13, 9, 30, 5, tzinfo=timezone.utc)
    roh = syslog.meldung(_konfig(), syslog.HINWEIS, "admin: Fahrer:in „A“ angelegt", "aenderung",
                         {"benutzer": 'ad"min', "id": 3, "leer": None}, zeit).decode()
    assert roh.startswith("<133>1 2026-11-13T10:30:05.000+01:00 fuhrpark ladefalke ")  # local0.notice, Wien
    assert ' aenderung [ladefalke@32473 benutzer="ad\\"min" id="3"] admin: Fahrer:in „A“ angelegt' in roh
    assert syslog._rahmen(_konfig(protokoll="tcp"), b"abc") == b"3 abc"  # octet counting
    assert syslog._rahmen(_konfig(protokoll="udp"), b"abc") == b"abc"


def test_format_3164():
    zeit = datetime(2026, 11, 3, 9, 30, 5, tzinfo=timezone.utc)
    roh = syslog.meldung(_konfig(format="3164", facility="local3"), syslog.WARNUNG, "Zeile\nzwei", zeit=zeit).decode()
    assert roh.startswith("<156>Nov  3 10:30:05 fuhrpark ladefalke[") and roh.endswith("]: Zeile zwei")
    assert syslog._rahmen(_konfig(format="3164", protokoll="tcp"), b"x") == b"x\n"


def test_eintrag_text_mit_und_ohne_werte():
    werte = {"benutzer_name": "Anna", "aktion": "geaendert", "objekt_typ": "fahrer", "bezeichnung": "Paul",
             "felder": {"Name": ["Paul", "Paula"], "E-Mail": [None, "p@x.example"]}, "kontext": "Import Nr. 3"}
    assert syslog.eintrag_text(werte, True) == ("Anna: Fahrer:in „Paul“ geändert: Name „Paul“ → „Paula“; "
                                                "E-Mail „–“ → „p@x.example“ [Import Nr. 3]")
    assert syslog.eintrag_text(werte, False) == "Anna: Fahrer:in „Paul“ geändert: Name, E-Mail [Import Nr. 3]"


@pytest.fixture
def konfig_setzen(monkeypatch):
    def setzen(k):
        monkeypatch.setattr(syslog, "_konfig", k)
    yield setzen


def test_nach_commit_per_udp(angemeldet, db, konfig_setzen):
    e = Empfaenger("udp")
    konfig_setzen(_konfig(port=e.port, werte=False))
    angemeldet.post("/stammdaten/fahrer/neu", data={"name": "Udo Udp", "aktiv": "on"})
    roh = e.warten().decode()
    assert "<133>1 " in roh and 'benutzer="admin" aktion="angelegt" objekt="fahrer"' in roh
    assert roh.endswith("admin: Fahrer:in „Udo Udp“ angelegt: Name, aktiv")  # ohne Werte


def test_rollback_sendet_nichts(db, konfig_setzen):
    gesendet = []
    konfig_setzen(_konfig())
    syslog_senden = syslog.eintrag_senden
    try:
        syslog.eintrag_senden = gesendet.append
        db.begin_nested()
        db.add(Fahrer(name="Rolf Rollback"))
        db.flush()
        db.rollback()
    finally:
        syslog.eintrag_senden = syslog_senden
    assert gesendet == []


def test_testmeldung_tcp(konfig_setzen):
    e = Empfaenger("tcp")
    syslog.testen(_konfig(protokoll="tcp", port=e.port), "Anna")
    roh = e.warten()
    laenge, _, rest = roh.partition(b" ")
    assert int(laenge) == len(rest) and rest.endswith("Testmeldung von Ladefalke (ausgelöst von Anna).".encode())


def test_nicht_erreichbar():
    frei = socket.socket()
    frei.bind(("127.0.0.1", 0))
    port = frei.getsockname()[1]
    frei.close()
    with pytest.raises(syslog.SyslogFehler, match="nicht erreichbar"):
        syslog.testen(_konfig(protokoll="tcp", port=port), "Anna")
    with pytest.raises(syslog.SyslogFehler, match="Kein Syslog-Server"):
        syslog.testen(_konfig(server=""), "Anna")


def _zertifikat(tmp_path):
    """Selbst signiertes Zertifikat für 127.0.0.1 (CA = Zertifikat selbst)."""
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    schluessel = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "syslog-test")])
    jetzt = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(schluessel.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(jetzt - timedelta(days=1))
            .not_valid_after(jetzt + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(schluessel, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "cert.pem").write_bytes(pem)
    (tmp_path / "key.pem").write_bytes(schluessel.private_bytes(serialization.Encoding.PEM,
                                                                serialization.PrivateFormat.PKCS8,
                                                                serialization.NoEncryption()))
    kontext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    kontext.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    return pem.decode(), kontext


def test_tls_mit_eigener_ca(tmp_path):
    pem, kontext = _zertifikat(tmp_path)
    e = Empfaenger("tls", kontext)
    syslog.testen(_konfig(protokoll="tls", port=e.port, ca=pem), "Anna")
    assert b"Testmeldung von Ladefalke" in e.warten()
    e = Empfaenger("tls", kontext)  # ohne die CA: Zertifikat nicht vertrauenswürdig
    with pytest.raises(syslog.SyslogFehler, match="nicht vertrauenswürdig"):
        syslog.testen(_konfig(protokoll="tls", port=e.port), "Anna")
    e.warten()


def test_app_meldungen(konfig_setzen):
    eingereiht = []
    konfig_setzen(_konfig())
    handler = syslog.AppHandler()
    original = syslog._sender.einreihen
    syslog._sender.einreihen = eingereiht.append
    try:
        handler.emit(logging.LogRecord("app.services.backup", logging.ERROR, __file__, 1, "Sicherung kaputt", None, None))
        konfig_setzen(_konfig(app_meldungen=False))
        handler.emit(logging.LogRecord("app.services.backup", logging.ERROR, __file__, 1, "nicht senden", None, None))
    finally:
        syslog._sender.einreihen = original
    [roh] = eingereiht
    assert roh.startswith(b"<131>1 ") and b'modul="app.services.backup"' in roh and roh.endswith(b"Sicherung kaputt")


def test_einstellungen_speichern_und_pruefen(angemeldet, db):
    basis = {"stichtag": "ende", "topliste_mindestmenge": "20", "strompreis_vorbelegung": "0,30"}
    antwort = angemeldet.post("/einstellungen", data=basis | {"syslog_server": "syslog firma", "syslog_port": "99999",
                                                              "syslog_ca": "kein Zertifikat"})
    assert antwort.status_code == 422
    for text in ("nur den Servernamen", "Port zwischen 1 und 65535", "PEM-Format"):
        assert text in antwort.text
    angemeldet.post("/einstellungen", data=basis | {"syslog_server": "127.0.0.1", "syslog_port": "6514",
                                                    "syslog_protokoll": "tls", "syslog_aenderungen": "on",
                                                    "syslog_werte": "on"})
    k = syslog._konfig
    assert k.server == "127.0.0.1" and k.port == 6514 and k.protokoll == "tls" and k.aenderungen and not k.app_meldungen
    seite = angemeldet.get("/einstellungen").text
    assert 'id="protokoll"' in seite and "Syslog (rsyslog)" in seite
    # Test ohne erreichbaren Server: Fehlermeldung, kein Absturz
    antwort = angemeldet.post("/einstellungen/syslog/test", follow_redirects=True)
    assert "Syslog-Testmeldung fehlgeschlagen" in antwort.text
    angemeldet.post("/einstellungen", data=basis)  # wieder aus (Server leer)
    assert not syslog._konfig.aktiv


def test_rechte_testmeldung(rollen):  # noqa: F811
    assert rollen["operator"].post("/einstellungen/syslog/test").status_code == 403


from tests.test_benutzer import rollen  # noqa: E402, F401, F811  (Fixture)
