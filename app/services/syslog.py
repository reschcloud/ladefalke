"""Weiterleitung an einen Syslog-Server (rsyslog): Einträge des Änderungsprotokolls und Meldungen der App.

- Transport UDP, TCP oder TCP mit TLS (RFC 5425); Format RFC 5424 (mit strukturierten Daten) oder RFC 3164.
  Über TCP/TLS mit Längenangabe vor jeder Meldung (octet counting, RFC 6587) bei RFC 5424, sonst zeilenweise –
  rsyslog (imtcp) erkennt beides.
- Gesendet wird im Hintergrund über eine Warteschlange: Ein langsamer oder nicht erreichbarer Server bremst die App
  nicht. Die Datenbank bleibt die Hauptquelle; was nicht zugestellt werden kann, fehlt nur im Syslog
  (letzter Fehler in den Einstellungen sichtbar).
- Die App verbindet sich nur, wenn ein Server eingetragen ist.
Einstellungen unter Einstellungen → Protokoll; `laden()` nach dem Speichern und beim Start.
"""
import logging
import os
import queue
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import einstellungen

PROTOKOLLE = {"udp": "UDP", "tcp": "TCP", "tls": "TCP mit TLS"}
FORMATE = {"5424": "RFC 5424 (empfohlen)", "3164": "RFC 3164 (BSD, älter)"}
FACILITIES = {**{f"local{i}": 16 + i for i in range(8)}, "user": 1}
APP_NAME = "ladefalke"
SD_ID = "ladefalke@32473"  # 32473: Beispiel-Unternehmensnummer für eigene Kennungen (RFC 5612)
# Schweregrade (RFC 5424)
FEHLER, WARNUNG, HINWEIS, INFO = 3, 4, 5, 6
MAX_LAENGE = 8000
# eigener Logger außerhalb von „app“, damit Fehler beim Senden nicht wieder an Syslog gehen
log = logging.getLogger("ladefalke.syslog")


class SyslogFehler(Exception):
    pass


@dataclass(frozen=True)
class Konfiguration:
    server: str
    port: int
    protokoll: str
    format: str
    facility: str
    hostname: str
    ca: str
    aenderungen: bool
    app_meldungen: bool
    werte: bool

    @property
    def aktiv(self) -> bool:
        return bool(self.server)


_konfig: Konfiguration | None = None
_status = {"fehler": None, "zeit": None, "gesendet": 0, "verworfen": 0}


def konfiguration(e) -> Konfiguration:
    return Konfiguration(server=e.syslog_server, port=e.syslog_port, protokoll=e.syslog_protokoll, format=e.syslog_format,
                         facility=e.syslog_facility, hostname=e.syslog_hostname or _hostname(), ca=e.syslog_ca,
                         aenderungen=e.syslog_aenderungen, app_meldungen=e.syslog_app, werte=e.syslog_werte)


def laden(db: Session) -> Konfiguration:
    """Einstellungen übernehmen (beim Start und nach dem Speichern); offene Verbindung neu aufbauen."""
    global _konfig
    from app.services import app_einstellungen
    _konfig = konfiguration(app_einstellungen.laden(db))
    _sender.neu_verbinden()
    return _konfig


def status() -> dict:
    return dict(_status)


def _hostname() -> str:
    return socket.gethostname() or "-"


# ------------------------------------------------------------------ Meldungen bauen

def _sd_wert(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def meldung(k: Konfiguration, schwere: int, text: str, msgid: str = "-", sd: dict | None = None,
            zeit: datetime | None = None) -> bytes:
    """Eine Syslog-Zeile (ohne Rahmen) im gewählten Format."""
    pri = FACILITIES.get(k.facility, 16) * 8 + schwere
    zeit = (zeit or datetime.now(timezone.utc)).astimezone(ZoneInfo(einstellungen().tz))
    host = (k.hostname or "-").replace(" ", "_")[:255]
    text = " ".join(text.split())  # eine Zeile
    if k.format == "3164":
        kopf = f"<{pri}>{zeit.strftime('%b')} {zeit.day:>2} {zeit:%H:%M:%S} {host} {APP_NAME}[{os.getpid()}]: "
        roh = (kopf + text).encode("utf-8")
    else:
        daten = "-"
        if sd:
            daten = f"[{SD_ID} " + " ".join(f'{n}="{_sd_wert(w)}"' for n, w in sd.items() if w not in (None, "")) + "]"
        kopf = f"<{pri}>1 {zeit.isoformat(timespec='milliseconds')} {host} {APP_NAME} {os.getpid()} {msgid} {daten} "
        roh = kopf.encode("utf-8") + text.encode("utf-8")
    return roh[:MAX_LAENGE]


def _rahmen(k: Konfiguration, roh: bytes) -> bytes:
    if k.protokoll == "udp":
        return roh
    if k.format == "3164":
        return roh + b"\n"
    return f"{len(roh)} ".encode() + roh  # octet counting


def eintrag_text(werte: dict, mit_werten: bool) -> str:
    """„admin: Ladekarte „1234 (Wien Energie)“ geändert: Notiz „alt“ → „neu“ [Import datei.csv]“"""
    from app.services.protokoll import AKTIONEN, TYP_NAMEN
    teile = [f"{werte['benutzer_name']}: {TYP_NAMEN.get(werte['objekt_typ'], werte['objekt_typ'])} "
             f"„{werte['bezeichnung']}“ {AKTIONEN.get(werte['aktion'], werte['aktion'])}"]
    felder = werte.get("felder") or {}
    if felder:
        if mit_werten:
            teile.append(": " + "; ".join(f"{f} „{a or '–'}“ → „{n or '–'}“" if a is not None or n is not None else f
                                          for f, (a, n) in felder.items()))
        else:
            teile.append(": " + ", ".join(felder))
    if werte.get("kontext"):
        teile.append(f" [{werte['kontext']}]")
    return "".join(teile)


def eintrag_senden(werte: dict) -> None:
    """Protokolleintrag (nach dem Commit) an Syslog – falls eingerichtet und gewünscht."""
    k = _konfig
    if k is None or not k.aktiv or not k.aenderungen:
        return
    from app.services.protokoll import ist_warnung
    sd = {"benutzer": werte["benutzer_name"], "aktion": werte["aktion"], "objekt": werte["objekt_typ"],
          "id": werte.get("objekt_id"), "felder": ",".join(werte.get("felder") or {}), "kontext": werte.get("kontext")}
    msgid = "anmeldung" if werte["objekt_typ"] == "anmeldung" else "aenderung"
    _sender.einreihen(meldung(k, WARNUNG if ist_warnung(werte) else HINWEIS, eintrag_text(werte, k.werte), msgid, sd,
                              werte.get("zeitpunkt")))


class AppHandler(logging.Handler):
    """Meldungen der App (Logger „app.*“: Fehler, Backups, Versand) an Syslog, falls gewünscht."""

    def emit(self, record: logging.LogRecord) -> None:
        k = _konfig
        if k is None or not k.aktiv or not k.app_meldungen:
            return
        try:
            schwere = FEHLER if record.levelno >= logging.ERROR else WARNUNG if record.levelno >= logging.WARNING else INFO
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            _sender.einreihen(meldung(k, schwere, text, "app", {"modul": record.name}))
        except Exception:  # noqa: BLE001
            self.handleError(record)


# ------------------------------------------------------------------ Verbindung und Versand

def _verbinden(k: Konfiguration, zeitlimit: float = 5) -> socket.socket:
    try:
        if k.protokoll == "udp":
            s = socket.socket(socket.AF_INET6 if ":" in k.server else socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(zeitlimit)
            s.connect((k.server, k.port))
            return s
        s = socket.create_connection((k.server, k.port), timeout=zeitlimit)
        if k.protokoll == "tls":
            kontext = ssl.create_default_context(cadata=k.ca or None) if k.ca else ssl.create_default_context()
            try:
                s = kontext.wrap_socket(s, server_hostname=k.server)
            except Exception:
                s.close()
                raise
        return s
    except ssl.SSLCertVerificationError as e:
        raise SyslogFehler(f"Zertifikat des Servers nicht vertrauenswürdig ({e.verify_message}) – ggf. die CA "
                           "eintragen.") from None
    except ssl.SSLError as e:
        raise SyslogFehler(f"TLS-Verbindung fehlgeschlagen ({e.reason or e}).") from None
    except socket.gaierror:
        raise SyslogFehler(f"Server „{k.server}“ nicht gefunden.") from None
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError) as e:
        raise SyslogFehler(f"Server {k.server}:{k.port} nicht erreichbar ({e.strerror or e}).") from None


class _Sender:
    """Hintergrund-Thread mit Warteschlange; hält die Verbindung offen und baut sie bei Fehlern neu auf."""

    def __init__(self) -> None:
        self.schlange: queue.Queue = queue.Queue(maxsize=2000)
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.sperre = threading.Lock()
        self.neu = False

    def einreihen(self, roh: bytes) -> None:
        with self.sperre:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._lauf, name="syslog", daemon=True)
                self.thread.start()
        try:
            self.schlange.put_nowait(roh)
        except queue.Full:
            _status["verworfen"] += 1

    def neu_verbinden(self) -> None:
        self.neu = True

    def warten(self, sekunden: float = 5) -> None:
        """Bis die Schlange leer ist (z. B. vor dem Ende eines Kommandozeilen-Werkzeugs)."""
        ende = datetime.now().timestamp() + sekunden
        while self.schlange.unfinished_tasks and datetime.now().timestamp() < ende:
            threading.Event().wait(.05)

    def _schliessen(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _lauf(self) -> None:
        while True:
            roh = self.schlange.get()
            try:
                k = _konfig
                if k is None or not k.aktiv:
                    continue
                if self.neu:
                    self.neu = False
                    self._schliessen()
                for versuch in (1, 2):  # bei abgebrochener Verbindung einmal neu verbinden
                    try:
                        if self.sock is None:
                            self.sock = _verbinden(k)
                        self.sock.sendall(_rahmen(k, roh))
                        _status.update(gesendet=_status["gesendet"] + 1)
                        _status.update(fehler=None)
                        break
                    except (SyslogFehler, OSError) as e:
                        self._schliessen()
                        if versuch == 2:
                            _status.update(fehler=str(e), zeit=datetime.now(timezone.utc),
                                           verworfen=_status["verworfen"] + 1)
                            log.warning("Syslog: Meldung nicht zugestellt: %s", e)
            except Exception:  # noqa: BLE001 – der Thread darf nicht sterben
                log.exception("Syslog: unerwarteter Fehler")
            finally:
                self.schlange.task_done()


_sender = _Sender()


def warten(sekunden: float = 5) -> None:
    _sender.warten(sekunden)


def testen(k: Konfiguration, wer: str) -> None:
    """Testmeldung sofort und direkt senden (Fehler als SyslogFehler)."""
    if not k.aktiv:
        raise SyslogFehler("Kein Syslog-Server eingetragen.")
    s = _verbinden(k)
    try:
        s.sendall(_rahmen(k, meldung(k, HINWEIS, f"Testmeldung von Ladefalke (ausgelöst von {wer}).", "test",
                                     {"benutzer": wer})))
    except OSError as e:
        raise SyslogFehler(f"Senden fehlgeschlagen ({e.strerror or e}).") from None
    finally:
        s.close()


def einrichten() -> None:
    """Beim Start: App-Meldungen (Logger „app“, ab INFO) an den Syslog-Handler hängen und Einstellungen laden."""
    app_log = logging.getLogger("app")
    if not any(isinstance(h, AppHandler) for h in app_log.handlers):
        app_log.addHandler(AppHandler(level=logging.INFO))
    if app_log.level == logging.NOTSET or app_log.level > logging.INFO:
        app_log.setLevel(logging.INFO)
    from app.db import SessionLocal
    try:
        with SessionLocal() as db:
            laden(db)
    except Exception:  # noqa: BLE001 – ohne Datenbank startet die App trotzdem
        log.exception("Syslog-Einstellungen nicht lesbar")
