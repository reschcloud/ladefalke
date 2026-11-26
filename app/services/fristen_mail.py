"""Benachrichtigung per Mail bei Fristen (Leasing-Ende, Führerscheinkontrolle, Vignette, Streckenmaut FLEX).

Wer unter „Mein Konto“ „Bei Fristen benachrichtigen“ gewählt hat, bekommt eine Mail, sobald eine Frist in die
Hinweisfrist fällt (bzw. eine Angabe fehlt oder noch nicht gilt – Stufe „warnung“) und noch einmal, wenn sie
tatsächlich abläuft (Stufe „ablauf“). Die betroffenen Fahrzeuge und Personen stehen im Mailtext; angehängt ist der
Report „Offene Aufgaben“. Je Person, Frist und Stufe höchstens eine Mail (Tabelle frist_meldung); ändert sich das
Datum (z. B. Leasing verlängert, Kontrolle eingetragen), beginnt die Meldekette von vorn.

Geprüft wird vom Taktgeber einmal täglich ab 7 Uhr (Ortszeit), nur mit eingerichtetem Mailserver.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatierung import datum, spanne
from app.models import Benutzer, FristMeldung
from app.services import app_einstellungen, fristen, mail, reports
from app.services.fristen import Frist

log = logging.getLogger(__name__)

AB_STUNDE = 7
TITEL = {"leasing": "Leasing-Ende", "fuehrerschein": "Führerscheinkontrolle", "vignette": "Vignette",
         "streckenmaut": "Streckenmaut FLEX"}
GEPRUEFT = "fristen_mail_geprueft"  # Einstellung: Tag der letzten Prüfung (ISO)


def stufe(f: Frist) -> str:
    return "ablauf" if f.zustand == "abgelaufen" else "warnung"


def zeile(f: Frist) -> str:
    """„Leasing-Ende W-123: endet am 31.12.2026 (in 3 Monaten)“ u. Ä."""
    titel = f"{TITEL.get(f.art, f.art)} {f.name}"
    z = f.zustand
    if z == "fehlt":
        return f"{titel}: {'keine Kontrolle eingetragen' if f.art == 'fuehrerschein' else 'Gültigkeit nicht eingetragen'}"
    if z == "noch_nicht":
        return f"{titel}: noch nicht gültig (ab {datum(f.ab)})"
    if z == "abgelaufen":
        return f"{titel}: {'überfällig seit' if f.art == 'fuehrerschein' else 'abgelaufen am'} {datum(f.faellig)}"
    return f"{titel}: {'fällig am' if f.art == 'fuehrerschein' else 'läuft ab am'} {datum(f.faellig)} (in {spanne(f.tage)})"


def _schon(db: Session, benutzer_id: int) -> set[tuple]:
    return {(m.art, m.objekt, m.faellig, m.stufe)
            for m in db.scalars(select(FristMeldung).where(FristMeldung.benutzer_id == benutzer_id))}


def neue_meldungen(db: Session, benutzer_id: int, liste: list[Frist]) -> list[Frist]:
    """Fristen, deren aktuelle Stufe dieser Person noch nicht gemeldet wurde."""
    schon = _schon(db, benutzer_id)
    return [f for f in liste if (f.art, f.link, f.faellig, stufe(f)) not in schon]


def mail_text(benutzer: Benutzer, neu: list[Frist], alle: int, gruss: str) -> tuple[str, str]:
    abgelaufen = [f for f in neu if stufe(f) == "ablauf"]
    bald = [f for f in neu if stufe(f) == "warnung"]
    teile = []
    if abgelaufen:
        teile.append(f"{len(abgelaufen)} abgelaufen")
    if bald:
        teile.append(f"{len(bald)} in der Hinweisfrist")
    betreff = "Ladefalke – Fristen: " + ", ".join(teile)
    absaetze = [f"Hallo {benutzer.name},", ""]
    if abgelaufen:
        absaetze += ["Abgelaufen:"] + [f"– {zeile(f)}" for f in abgelaufen] + [""]
    if bald:
        absaetze += ["Hinweisfrist erreicht:"] + [f"– {zeile(f)}" for f in bald] + [""]
    absaetze += [f"Insgesamt sind derzeit {alle} Frist{'en' if alle != 1 else ''} offen – erledigen lässt sich alles "
                 "unter „Aufgaben“ in der App; anbei der Report „Offene Aufgaben“.",
                 "",
                 "Du bekommst diese Mail, weil du unter „Mein Konto“ die Benachrichtigung bei Fristen gewählt hast.",
                 "", "Viele Grüße", gruss]
    return betreff, "\n".join(absaetze)


def senden(db: Session, jetzt: datetime) -> list[str]:
    """Neue Fristmeldungen an alle mit Abo schicken; liefert die Namen der Empfänger:innen."""
    from app.services.versand import _gruss, _protokoll, stand_heute
    if mail.konfiguration(db).fehlt:
        return []
    e = app_einstellungen.laden(db)
    alle = fristen.ermitteln(db, e, jetzt.date())
    if not alle:
        return []
    stand, pdf, gesendet = stand_heute(jetzt.date()), None, []
    for b in db.scalars(select(Benutzer).where(Benutzer.fristen_abo, Benutzer.aktiv, Benutzer.email.is_not(None))
                        .order_by(Benutzer.name)):
        neu = neue_meldungen(db, b.id, alle)
        if not neu:
            continue
        if pdf is None:
            pdf = reports.offene_aufgaben(db)
        betreff, text = mail_text(b, neu, len(alle), _gruss(db))
        try:
            mail.senden(db, b.email, betreff, text,
                        [(f"ladefalke-offene-aufgaben-{stand.von:%Y-%m-%d}.pdf", pdf, "application/pdf")])
        except mail.MailFehler as fehler:
            _protokoll(db, "fristen", stand, b.name, b.email, False, str(fehler), "automatisch", None, benutzer_id=b.id)
            continue  # nächster Versuch bei der nächsten Prüfung
        _protokoll(db, "fristen", stand, b.name, b.email, True, None, "automatisch", None, benutzer_id=b.id)
        for f in neu:
            db.add(FristMeldung(benutzer_id=b.id, art=f.art, objekt=f.link, faellig=f.faellig, stufe=stufe(f),
                                gesendet_am=jetzt))
        gesendet.append(b.name)
    db.flush()
    return gesendet


def faellig(zuletzt: str, jetzt: datetime) -> bool:
    """Einmal täglich ab AB_STUNDE Uhr."""
    return jetzt.hour >= AB_STUNDE and zuletzt != jetzt.date().isoformat()


def automatisch_pruefen() -> None:
    """Vom Taktgeber minütlich aufgerufen."""
    from app.db import SessionLocal
    from app.models import Einstellung
    from app.config import jetzt_lokal
    jetzt = jetzt_lokal()
    with SessionLocal() as db:
        eintrag = db.get(Einstellung, GEPRUEFT)
        if not faellig(eintrag.wert if eintrag else "", jetzt):
            return
        try:
            namen = senden(db, jetzt)
            app_einstellungen.speichern(db, {GEPRUEFT: jetzt.date().isoformat()})
            db.commit()
            if namen:
                log.info("Fristen-Benachrichtigung an %s", ", ".join(namen))
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("Fristen-Benachrichtigung fehlgeschlagen")
