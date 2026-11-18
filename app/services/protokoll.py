"""Änderungsprotokoll: wer hat was wann geändert (Seite „Protokoll“, Verlauf auf Detailseiten, Syslog).

Mitgeschrieben wird zentral beim Speichern (SQLAlchemy-Ereignis `after_flush`), damit keine Änderung durchrutscht
und neue Funktionen automatisch dabei sind:
- Stammdaten (MSP, Ladekarten, Fahrer:innen, Fahrzeuge, Zuordnungen, Ladeorte, Pauschalen, Benutzer:innen) und
  Einstellungen: angelegt / geändert / gelöscht mit den Feldern alt → neu (als Anzeigetext; Verweise mit Namen).
- Ladevorgänge und Gebühren nur bei Änderungen (manuelle Korrekturen); angelegt bzw. gelöscht werden sie über
  Importe, die je einen Sammeleintrag schreiben (services/importe.py). Ändert ein Vorgang viele Vorgänge auf einmal
  (z. B. Neuzuordnung), entsteht ebenfalls ein Sammeleintrag statt hunderter Zeilen.
- Sensible Felder (Passwort, Mail-Passwort, Syslog-CA) nur als „geändert“, ohne Wert.
- Wer: die angemeldete Person der Anfrage (`setzen`, aus auth.login_erforderlich, an der DB-Sitzung);
  ohne Anmeldung (Taktgeber, Kommandozeile, Start) „System“.
Einträge gehen nach dem Commit zusätzlich an den Syslog-Server, falls eingerichtet (services/syslog.py).
Aufbewahrung: Einstellung „protokoll_aufbewahrung_jahre“, ältere Einträge löscht der Taktgeber.
"""
import enum
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Text, cast, delete, event, func, insert, or_, select
from sqlalchemy.orm import Session, attributes, object_session

from app import formatierung
from app.models import (Aenderung, Benutzer, Einstellung, Fahrer, Fahrzeug, Gebuehr, Import, Ladekarte, Ladeort,
                        Ladevorgang, Msp, Pauschale, ZuordnungFahrerFahrzeug, ZuordnungKarte)

SYSTEM = "System"
_WER, _STILL, _KONTEXT, _NEU = "protokoll_wer", "protokoll_still", "protokoll_kontext", "protokoll_neu"
MASSE = 10  # mehr gleichartige Änderungen in einem Schritt → ein Sammeleintrag

# Objektart: Klasse → (Schlüssel, Anzeigename, Mehrzahl)
TYPEN = {
    Msp: ("msp", "MSP", "MSP"), Ladekarte: ("ladekarte", "Ladekarte", "Ladekarten"),
    Fahrer: ("fahrer", "Fahrer:in", "Fahrer:innen"), Fahrzeug: ("fahrzeug", "Fahrzeug", "Fahrzeuge"),
    ZuordnungKarte: ("zuordnung_karte", "Zuordnung Ladekarte", "Zuordnungen Ladekarte"),
    ZuordnungFahrerFahrzeug: ("zuordnung_fahrer", "Zuordnung Fahrer:in", "Zuordnungen Fahrer:in"),
    Ladeort: ("ladeort", "Ladeort", "Ladeorte"), Pauschale: ("pauschale", "Pauschale", "Pauschalen"),
    Benutzer: ("benutzer", "Benutzer:in", "Benutzer:innen"), Einstellung: ("einstellung", "Einstellungen", "Einstellungen"),
    Ladevorgang: ("ladevorgang", "Ladevorgang", "Ladevorgänge"), Gebuehr: ("gebuehr", "Gebühr", "Gebühren"),
}
# weitere Objektarten nur aus ausdrücklichen Einträgen
TYP_NAMEN = {schluessel: name for schluessel, name, _ in TYPEN.values()} | {
    "import": "Import", "anmeldung": "Anmeldung", "backup": "Backup", "syslog": "Syslog"}
AKTIONEN = {"angelegt": "angelegt", "geaendert": "geändert", "geloescht": "gelöscht", "importiert": "importiert",
            "anmeldung": "angemeldet", "anmeldung_fehlgeschlagen": "Anmeldung fehlgeschlagen",
            "erstellt": "erstellt", "wiederhergestellt": "wiederhergestellt"}
# Ladevorgänge und Gebühren entstehen und verschwinden nur mit Importen (Sammeleintrag dort)
NUR_AENDERUNGEN = (Ladevorgang, Gebuehr)
IGNORIERT = {"id", "rohdaten", "letzte_anmeldung", "theme", "menue", "angelegt_am", "schluessel", "plz_norm", "name_norm"}
GEHEIM = {"passwort_hash"}
GEHEIME_EINSTELLUNGEN = {"mail_passwort", "syslog_ca"}
FELDNAMEN = {
    "name": "Name", "bezeichnung": "Bezeichnung", "kennung": "Kennung", "kennung_typ": "Kennungstyp",
    "vertragsnummer": "Vertragsnummer", "ist_gastkarte": "Gastkarte", "aktiv": "aktiv", "notiz": "Notiz",
    "msp_id": "MSP", "fahrer_id": "Fahrer:in", "fahrzeug_id": "Fahrzeug", "ladekarte_id": "Ladekarte",
    "ladeort_id": "Ladeort", "gueltig_ab": "Gültig ab", "gueltig_bis": "Gültig bis", "kennzeichen": "Kennzeichen",
    "modell": "Modell", "leasing_beginn": "Leasingbeginn", "leasing_ende": "Leasing-Ende",
    "fuehrerschein_kontrolle": "Führerscheinkontrolle", "vignette_noetig": "Vignette nötig", "vignette_ab": "Vignette ab",
    "vignette_bis": "Vignette bis", "streckenmaut_noetig": "Streckenmaut FLEX nötig",
    "streckenmaut_ab": "Streckenmaut FLEX ab", "streckenmaut_bis": "Streckenmaut FLEX bis", "email": "E-Mail", "rolle": "Rolle",
    "passwort_hash": "Passwort", "benutzername": "Benutzername", "zusammenfassung_abo": "Abo Zusammenfassung",
    "aufgaben_abo": "Abo Offene Aufgaben", "karten_cpo_abo": "Abo Karten und Preise je CPO",
    "flottenbilanz_abo": "Abo Flottenbilanz", "zuordnungen_abo": "Abo Zuordnungen am Stichtag",
    "parser_key": "Parser", "parser_aktiv": "Parser aktiv", "standard_ladeart": "Standard-Ladeart",
    "stromart_standard": "Stromart-Vorgabe", "cpo_standard": "CPO-Vorgabe", "abrechnung_je_karte": "Abrechnung je Karte",
    "stromart": "Stromart", "stromart_quelle": "Stromart-Quelle", "land": "Land", "profil_ausgenommen":
    "aus Ladeprofil ausgenommen", "zuordnung_quelle": "Zuordnung", "kwh": "kWh", "kwh_geschaetzt": "kWh geschätzt",
    "kosten_netto": "Kosten netto", "betrag_netto": "Betrag netto", "betreiber_id": "CPO", "betreiber_quelle":
    "CPO-Quelle", "evse_id": "EVSE-ID", "adresse": "Adresse", "plz": "PLZ", "ort": "Ort", "plz_manuell": "PLZ (manuell)",
    "ort_manuell": "Ort (manuell)", "lat": "Breite", "lon": "Länge", "genauigkeit": "Verortung", "monat": "Monat",
    "ladeart": "Ladeart", "start": "Start", "ende": "Ende", "standort": "Standort", "ladepunkt": "Ladepunkt",
    "zeitraum_von": "Zeitraum von", "zeitraum_bis": "Zeitraum bis", "roh_kartenkennung": "Kartenkennung (Datei)",
}
VERWEISE = {"msp_id": Msp, "fahrer_id": Fahrer, "fahrzeug_id": Fahrzeug, "ladekarte_id": Ladekarte, "ladeort_id": Ladeort}


# ------------------------------------------------------------------ wer, Kontext, Sammeln

def setzen(db: Session, benutzer: Benutzer | None) -> None:
    """Wer ändert in dieser Sitzung (None = System)."""
    db.info[_WER] = (benutzer.id, benutzer.name) if benutzer is not None else None


def _wer(db: Session) -> tuple[int | None, str]:
    return db.info.get(_WER) or (None, SYSTEM)


@contextmanager
def kontext(db: Session, text: str):
    """Zusammenhang für alle Einträge im Block, z. B. „Import datei.csv“."""
    vorher = db.info.get(_KONTEXT)
    db.info[_KONTEXT] = text[:300]
    try:
        yield
    finally:
        db.info[_KONTEXT] = vorher


@contextmanager
def still(db: Session, *klassen):
    """Im Block keine Einzeleinträge für diese Klassen (der Aufrufer schreibt einen Sammeleintrag)."""
    db.flush()
    vorher = db.info.get(_STILL, frozenset())
    db.info[_STILL] = vorher | set(klassen)
    try:
        yield
        db.flush()
    finally:
        db.info[_STILL] = vorher


def eintragen(db: Session, aktion: str, objekt_typ: str, bezeichnung: str, objekt_id: int | None = None,
              felder: dict | None = None, kontext_text: str | None = None,
              wer: tuple[int | None, str] | None = None) -> None:
    """Ausdrücklicher Eintrag (Import, Anmeldung, Backup, Sammeleinträge)."""
    benutzer_id, name = wer or _wer(db)
    werte = dict(zeitpunkt=datetime.now(timezone.utc), benutzer_id=benutzer_id, benutzer_name=name[:200],
                 aktion=aktion, objekt_typ=objekt_typ, objekt_id=objekt_id, bezeichnung=bezeichnung[:300],
                 felder=felder or {}, kontext=(kontext_text or db.info.get(_KONTEXT) or None))
    db.add(Aenderung(**werte))
    db.info.setdefault(_NEU, []).append(werte)


# ------------------------------------------------------------------ Darstellung

def _text(wert) -> str | None:
    if wert is None or wert == "":
        return None
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    if isinstance(wert, enum.Enum):
        return getattr(wert, "label", wert.value)
    if isinstance(wert, datetime):
        return formatierung.datumzeit(wert)
    if isinstance(wert, date):
        return formatierung.datum(wert)
    if isinstance(wert, Decimal):
        return format(wert.normalize(), "f").replace(".", ",")
    return str(wert)[:500]


def _bezug(obj, name: str):
    """Verknüpftes Objekt – bei gerade angelegten Objekten ist die Beziehung oft noch leer, dann über die ID."""
    wert = getattr(obj, name, None)
    if wert is None and (fk := getattr(obj, f"{name}_id", None)) is not None and (s := object_session(obj)) is not None:
        wert = s.get(obj.__mapper__.relationships[name].mapper.class_, fk)
    return wert


def bezeichnung(obj) -> str:
    """Lesbarer Name, wird mitgespeichert (bleibt nach Umbenennen oder Löschen verständlich)."""
    try:
        match obj:
            case Msp():
                return obj.name
            case Ladekarte():
                msp = _bezug(obj, "msp")
                return f"{obj.bezeichnung or obj.kennung} ({msp.name if msp else '–'})"
            case Fahrer():
                return obj.name
            case Fahrzeug():
                return obj.kennzeichen + (f" ({obj.modell})" if obj.modell else "")
            case ZuordnungKarte():
                fahrer, fahrzeug = _bezug(obj, "fahrer"), _bezug(obj, "fahrzeug")
                ziel = f"Fahrer:in {fahrer.name}" if fahrer else f"Fahrzeug {fahrzeug.kennzeichen}" if fahrzeug else "–"
                return (f"{bezeichnung(_bezug(obj, 'ladekarte'))} → {ziel}, "
                        f"{formatierung.zeitraum(obj.gueltig_ab, obj.gueltig_bis)}")
            case ZuordnungFahrerFahrzeug():
                return (f"{_bezug(obj, 'fahrer').name} → {_bezug(obj, 'fahrzeug').kennzeichen}, "
                        f"{formatierung.zeitraum(obj.gueltig_ab, obj.gueltig_bis)}")
            case Ladeort():
                return obj.adresse + (f" ({obj.land})" if obj.land else "")
            case Pauschale():
                return f"{obj.bezeichnung} {formatierung.monat(obj.monat)}"
            case Benutzer():
                return f"{obj.name} ({obj.benutzername})"
            case Ladevorgang():
                msp = _bezug(obj, "msp")
                return (f"{formatierung.datumzeit(obj.start)}, {_text(obj.kwh)} kWh, "
                        f"{formatierung.geld(obj.kosten_netto)} ({msp.name if msp else '–'})")
            case Gebuehr():
                return f"{obj.bezeichnung}, {formatierung.geld(obj.betrag_netto)}"
            case Import():
                return obj.dateiname
    except Exception:  # noqa: BLE001 – z. B. Verweis schon gelöscht
        pass
    return "–"


def _feldwert(db: Session, feld: str, wert) -> str | None:
    if feld in VERWEISE and wert is not None:
        ziel = db.get(VERWEISE[feld], wert)
        return bezeichnung(ziel) if ziel is not None else "(gelöscht)"
    return _text(wert)


def _spalten(obj) -> list[str]:
    return [s.key for s in obj.__mapper__.column_attrs if s.key not in IGNORIERT]


def _felder(db: Session, obj, aktion: str) -> dict:
    """{Anzeigename: [alt, neu]} – angelegt: alle gesetzten Felder; gelöscht: alle bisherigen Werte."""
    felder = {}
    for feld in _spalten(obj):
        if aktion == "geaendert":
            h = attributes.get_history(obj, feld)
            if not h.added and not h.deleted:
                continue
            alt, neu = (h.deleted[0] if h.deleted else None), (h.added[0] if h.added else None)
            if alt == neu:
                continue
        else:
            wert = getattr(obj, feld, None)
            if wert is None or wert == "":
                continue
            alt, neu = (None, wert) if aktion == "angelegt" else (wert, None)
        name = FELDNAMEN.get(feld, feld)
        if feld in GEHEIM:
            felder[name] = [None, "geändert"] if aktion == "geaendert" else [None, "gesetzt"] if aktion == "angelegt" else [None, None]
            continue
        felder[name] = [_feldwert(db, feld, alt), _feldwert(db, feld, neu)]
    return felder


def einstellung_name(schluessel: str) -> str:
    return schluessel.replace("_", " ")


# ------------------------------------------------------------------ Mitschreiben beim Speichern

@event.listens_for(Session, "after_flush")
def _mitschreiben(session: Session, _kontext) -> None:
    still = session.info.get(_STILL, frozenset())
    einzel: list[tuple] = []  # (aktion, obj)
    for aktion, menge in (("angelegt", session.new), ("geaendert", session.dirty), ("geloescht", session.deleted)):
        for obj in menge:
            klasse = type(obj)
            if klasse not in TYPEN or klasse in still:
                continue
            if aktion != "geaendert" and klasse in NUR_AENDERUNGEN:
                continue
            if aktion == "geaendert" and not session.is_modified(obj, include_collections=False):
                continue
            einzel.append((aktion, obj))
    if not einzel:
        return
    zeilen = []
    # Einstellungen: alle in einem Eintrag
    einst = [(a, o) for a, o in einzel if isinstance(o, Einstellung)]
    if einst:
        from app.services.app_einstellungen import STANDARD
        felder = {}
        for aktion, o in einst:
            if aktion == "geaendert":
                h = attributes.get_history(o, "wert")
                alt = h.deleted[0] if h.deleted else o.wert
            else:
                alt = STANDARD.get(o.schluessel, "")
            neu = o.wert if aktion != "geloescht" else STANDARD.get(o.schluessel, "")
            if (alt or "") == (neu or ""):
                continue
            if o.schluessel in GEHEIME_EINSTELLUNGEN:
                felder[einstellung_name(o.schluessel)] = [None, "geändert" if neu else "entfernt"]
            else:
                felder[einstellung_name(o.schluessel)] = [alt or None, neu or None]
        if felder:
            zeilen.append(("geaendert", "einstellung", None, "Einstellungen", felder))
    # Massenänderungen je Klasse zusammenfassen
    je_klasse: dict[type, list] = {}
    for aktion, o in einzel:
        if not isinstance(o, Einstellung):
            je_klasse.setdefault((type(o), aktion), []).append(o)
    for (klasse, aktion), objekte in je_klasse.items():
        schluessel, _, mehrzahl = TYPEN[klasse]
        if len(objekte) > MASSE:
            geaendert = sorted({f for o in objekte for f in _felder(session, o, aktion)}) if aktion == "geaendert" else []
            zeilen.append((aktion, schluessel, None, f"{len(objekte)} {mehrzahl}",
                           {"Felder": [None, ", ".join(geaendert)]} if geaendert else {}))
            continue
        for o in objekte:
            felder = _felder(session, o, aktion)
            if aktion == "geaendert" and not felder:
                continue
            zeilen.append((aktion, schluessel, o.id, bezeichnung(o), felder))
    if not zeilen:
        return
    benutzer_id, name = _wer(session)
    jetzt, kontext_text = datetime.now(timezone.utc), session.info.get(_KONTEXT)
    werte = [dict(zeitpunkt=jetzt, benutzer_id=benutzer_id, benutzer_name=name[:200], aktion=a, objekt_typ=t,
                  objekt_id=i, bezeichnung=b[:300], felder=f, kontext=kontext_text) for a, t, i, b, f in zeilen]
    session.connection().execute(insert(Aenderung), werte)
    session.info.setdefault(_NEU, []).extend(werte)


@event.listens_for(Session, "after_commit")
def _nach_commit(session: Session) -> None:
    neu = session.info.pop(_NEU, None)
    if neu:
        from app.services import syslog
        for werte in neu:
            syslog.eintrag_senden(werte)


@event.listens_for(Session, "after_rollback")
def _nach_rollback(session: Session) -> None:
    session.info.pop(_NEU, None)


# ------------------------------------------------------------------ Lesen, Aufräumen

def ist_warnung(e) -> bool:
    """Wichtige Einträge (Syslog „warning“, in der Liste hervorgehoben)."""
    aktion, typ, felder = (e["aktion"], e["objekt_typ"], e["felder"]) if isinstance(e, dict) else (e.aktion, e.objekt_typ, e.felder)
    return (aktion in ("anmeldung_fehlgeschlagen", "wiederhergestellt")
            or (typ == "benutzer" and (aktion == "geloescht" or (aktion == "geaendert" and (
                "Rolle" in (felder or {}) or "Passwort" in (felder or {}))))))



def suchen(db: Session, von: date | None = None, bis: date | None = None, benutzer: str = "", typ: str = "",
           text: str = "", seite: int = 1, je_seite: int = 50) -> tuple[list[Aenderung], int]:
    """Einträge (neueste zuerst) und Gesamtzahl. benutzer: ID oder „system“."""
    bed = []
    if von:
        bed.append(Aenderung.zeitpunkt >= datetime.combine(von, datetime.min.time()).astimezone())
    if bis:
        bed.append(Aenderung.zeitpunkt < datetime.combine(bis + timedelta(days=1), datetime.min.time()).astimezone())
    if benutzer == "system":
        bed.append(Aenderung.benutzer_name == SYSTEM)
    elif benutzer.isdigit():
        bed.append(Aenderung.benutzer_id == int(benutzer))
    if typ:
        bed.append(Aenderung.objekt_typ == typ)
    if text.strip():
        muster = f"%{text.strip()}%"
        bed.append(or_(Aenderung.bezeichnung.ilike(muster), Aenderung.kontext.ilike(muster),
                       Aenderung.benutzer_name.ilike(muster), cast(Aenderung.felder, Text).ilike(muster)))
    anzahl = db.scalar(select(func.count()).select_from(Aenderung).where(*bed)) or 0
    eintraege = db.scalars(select(Aenderung).where(*bed).order_by(Aenderung.zeitpunkt.desc(), Aenderung.id.desc())
                           .offset((max(seite, 1) - 1) * je_seite).limit(je_seite)).all()
    return list(eintraege), anzahl


def fuer_objekt(db: Session, typ: str, objekt_id: int, anzahl: int = 30) -> list[Aenderung]:
    return list(db.scalars(select(Aenderung).where(Aenderung.objekt_typ == typ, Aenderung.objekt_id == objekt_id)
                           .order_by(Aenderung.zeitpunkt.desc(), Aenderung.id.desc()).limit(anzahl)))


def verlauf(request, db: Session, typ: str, objekt_id: int | None) -> list[Aenderung] | None:
    """Für Detailseiten: Verlauf des Objekts, nur für Admins (sonst None = Abschnitt ausblenden)."""
    b = getattr(request.state, "benutzer", None)
    if objekt_id is None or b is None or b.rolle != "admin":
        return None
    return fuer_objekt(db, typ, objekt_id)


def aufraeumen(db: Session, jahre: int) -> int:
    """Einträge älter als `jahre` löschen; liefert die Anzahl."""
    grenze = datetime.now(timezone.utc) - timedelta(days=round(365.25 * jahre))
    return db.execute(delete(Aenderung).where(Aenderung.zeitpunkt < grenze)).rowcount or 0


_zuletzt_aufgeraeumt: datetime | None = None


def automatisch_aufraeumen() -> None:
    """Vom Taktgeber: höchstens stündlich alte Einträge löschen."""
    global _zuletzt_aufgeraeumt
    jetzt = datetime.now(timezone.utc)
    if _zuletzt_aufgeraeumt and jetzt - _zuletzt_aufgeraeumt < timedelta(hours=1):
        return
    _zuletzt_aufgeraeumt = jetzt
    from app.db import SessionLocal
    from app.services import app_einstellungen
    with SessionLocal() as db:
        if aufraeumen(db, app_einstellungen.laden(db).protokoll_aufbewahrung_jahre):
            db.commit()
