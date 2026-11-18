"""Versand der PDF-Reports per Mail (Seite „Reports“ und automatisch im Folgemonat), mit Versandprotokoll.

- Fahrer:innen-Report an die Person selbst (E-Mail-Adresse bei der Fahrer:in), nur wenn sie im Zeitraum Ladungen hat.
- Zusammenfassung an Benutzer:innen (manuell ausgewählt; automatisch an alle mit Abo und E-Mail-Adresse).
- Offene Aufgaben (Stand heute) an Benutzer:innen (manuell ausgewählt; automatisch nach eigenem Zeitplan an alle mit
  Abo und E-Mail-Adresse, je Tag höchstens einmal; auf Wunsch nur, wenn etwas offen ist).
- Manuell (Versanddialog der Reports-Seite, `report_senden`): Zusammenfassung, Karten und Preise je CPO,
  Zuordnungen am Stichtag, Flottenbilanz (auf Wunsch mit den Seiten als 4K-JPG) und Offene Aufgaben an ausgewählte Benutzer:innen und frei
  eingegebene Adressen; Fahrer:innen-Reports nur an die Person selbst, auf Wunsch mit Kopie an die Absender:in.
- Jede Mail ein Eintrag in `report_versand` (auch Fehler). Der automatische Versand schickt jedem Empfänger je Report
  und Zeitraum höchstens einmal (auch nach einem Fehler kein endloses Wiederholen – manuell geht es jederzeit).
- Mailtexte in Du-Form, Gruß „Dein Ladefalke“; der Zeitraum im Betreff.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import einstellungen
from app.formatierung import geld, zahl
from app.models import Benutzer, Fahrer, ReportVersand
from app.services import app_einstellungen, mail, reports
from app.services.auswertung import Zeitraum, auswerten, vormonat, zeitraum_mit

log = logging.getLogger(__name__)


@dataclass
class Ergebnis:
    gesendet: list[str] = field(default_factory=list)  # Namen
    ohne_adresse: list[str] = field(default_factory=list)
    fehler: list[tuple[str, str]] = field(default_factory=list)  # (Name, Meldung)

    def text(self, was: str) -> str:
        teile = [f"{was}: {len(self.gesendet)} gesendet"]
        if self.ohne_adresse:
            teile.append(f"{len(self.ohne_adresse)} ohne E-Mail-Adresse übersprungen ({', '.join(self.ohne_adresse)})")
        if self.fehler:
            teile.append(f"{len(self.fehler)} fehlgeschlagen ({'; '.join(f'{n}: {m}' for n, m in self.fehler)})")
        return ", ".join(teile) + "."


def _jetzt() -> datetime:
    return datetime.now(ZoneInfo(einstellungen().tz)).replace(tzinfo=None)


def _protokoll(db: Session, art: str, z: Zeitraum, name: str, an: str, erfolg: bool, fehler: str | None,
               ausgeloest: str, von: int | None, fahrer_id: int | None = None, benutzer_id: int | None = None) -> None:
    db.add(ReportVersand(art=art, zeitraum_art=z.art, zeitraum_von=z.von, zeitraum_bis=z.bis, zeitraum_label=z.label,
                         fahrer_id=fahrer_id, benutzer_id=benutzer_id, empfaenger_name=name, an=an,
                         gesendet_am=_jetzt(), erfolg=erfolg, fehler=fehler, ausgeloest=ausgeloest, ausgeloest_von=von))
    db.flush()


def _gruss(db: Session) -> str:
    """Grußformel unter den Mails (fest; der Absendername aus den Einstellungen steht im Absender)."""
    return "Dein Ladefalke"


def fahrer_text(person: Fahrer, z: Zeitraum, summe, gruss: str) -> tuple[str, str]:
    betreff = f"Dein Ladereport {z.label}"
    zahlen = (f"{summe.anzahl} Ladungen, {zahl(summe.kwh, 0)} kWh, {geld(summe.kosten)} netto"
              + (f", Ø {zahl(summe.preis, 2)} €/kWh" if summe.preis is not None else ""))
    text = (f"Hallo {person.name},\n\nanbei dein Ladereport für {z.label} ({z.von:%d.%m.%Y} – {z.bis:%d.%m.%Y}).\n"
            f"Kurz zusammengefasst: {zahlen}.\n\nDeinen Platz in der Topliste findest du im PDF.\n\nViele Grüße\n{gruss}")
    return betreff, text


def zusammenfassung_text(benutzer: Benutzer, z: Zeitraum, gesamt, gruss: str) -> tuple[str, str]:
    betreff = f"Ladefalke – Zusammenfassung {z.label}"
    text = (f"Hallo {benutzer.name},\n\nanbei die Zusammenfassung für {z.label} ({z.von:%d.%m.%Y} – {z.bis:%d.%m.%Y}): "
            f"{gesamt.anzahl} Ladungen, {zahl(gesamt.kwh, 0)} kWh, {geld(gesamt.kosten)} netto.\n\n"
            f"Du bekommst diese Mail, weil du Zusammenfassungen abonniert hast bzw. sie dir geschickt wurde "
            f"(abbestellen unter „Mein Konto“).\n\nViele Grüße\n{gruss}")
    return betreff, text


# ------------------------------------------------------------------ manueller Versand (Dialog auf der Reports-Seite)

TITEL = {"zusammenfassung": "Zusammenfassung", "karten_cpo": "Karten und Preise je CPO", "flottenbilanz": "Flottenbilanz",
         "aufgaben": "Offene Aufgaben", "zuordnungen": "Zuordnungen am Stichtag", "fahrer": "Ladereport"}
# Reports mit Abo je Benutzer:in (automatischer Versand, Vorauswahl im Dialog)
ABO = {"zusammenfassung": "zusammenfassung_abo", "aufgaben": "aufgaben_abo", "karten_cpo": "karten_cpo_abo",
       "flottenbilanz": "flottenbilanz_abo", "zuordnungen": "zuordnungen_abo"}


def adressen_lesen(text: str) -> tuple[list[str], list[str]]:
    """Frei eingegebene Adressen (Komma, Semikolon, Leerzeichen oder Zeilenumbruch); liefert (gültige, ungültige),
    ohne Doppelte."""
    import re
    from app.formatierung import email_lesen
    gueltig, ungueltig = [], []
    for teil in re.split(r"[\s,;]+", text or ""):
        if not teil:
            continue
        try:
            adresse = email_lesen(teil)
        except ValueError:
            ungueltig.append(teil)
            continue
        if adresse.lower() not in (a.lower() for a in gueltig):
            gueltig.append(adresse)
    return gueltig, ungueltig


def zuletzt_adressen(db: Session, anzahl: int = 8) -> list[str]:
    """Zuletzt frei eingegebene Adressen (erfolgreich, ohne Benutzer:in oder Fahrer:in) – Vorschläge im Dialog."""
    ergebnis: list[str] = []
    for an in db.scalars(select(ReportVersand.an).where(ReportVersand.benutzer_id.is_(None),
                                                        ReportVersand.fahrer_id.is_(None), ReportVersand.erfolg)
                         .order_by(ReportVersand.gesendet_am.desc()).limit(200)):
        if an.lower() not in (a.lower() for a in ergebnis):
            ergebnis.append(an)
        if len(ergebnis) >= anzahl:
            break
    return ergebnis


def _inhalt(db: Session, art: str, z) -> tuple[str, list[tuple[str, bytes, str]], str]:
    """(Betreffzeile, Anhänge, Satz zum Inhalt) eines Reports für den manuellen Versand."""
    if art == "zuordnungen":
        tag = z.von
        return (f"Ladefalke – Zuordnungen am {tag:%d.%m.%Y}",
                [(reports.zuordnungen_dateiname(tag), reports.zuordnungen(db, tag), "application/pdf")], "")
    if art == "aufgaben":
        from app.services import offen
        zahlen = aufgaben_zahlen(offen.fuer_report(db))
        satz = ("Offen sind: " + ", ".join(zahlen) + ".") if zahlen else "Derzeit ist nichts offen."
        return (f"Ladefalke – Offene Aufgaben ({z.label})",
                [(f"ladefalke-offene-aufgaben-{z.von:%Y-%m-%d}.pdf", reports.offene_aufgaben(db), "application/pdf")], satz)
    erzeugen = {"zusammenfassung": reports.zusammenfassung, "karten_cpo": reports.karten_cpo,
                "flottenbilanz": reports.flottenbilanz}[art]
    name = {"karten_cpo": "karten-cpo"}.get(art, art)
    pdf = erzeugen(db, z)
    anhaenge = [(reports.dateiname(z, name), pdf, "application/pdf")]
    satz = ""
    if art == "zusammenfassung":
        g = auswerten(db, z, app_einstellungen.laden(db)).gesamt
        satz = f"Kurz: {g.anzahl} Ladungen, {zahl(g.kwh, 0)} kWh, {geld(g.kosten)} netto."
    return f"Ladefalke – {TITEL[art]} {z.label}", anhaenge, satz


def report_senden(db: Session, art: str, z, benutzer_ids: list[int], adressen: list[str] = (), von: int | None = None,
                  mit_bildern: bool = False, ausgeloest: str = "manuell", nur_neue: bool = False) -> Ergebnis:
    """Ein Report (nicht Fahrer:innen) an Benutzer:innen und frei eingegebene Adressen – je Empfänger:in eine eigene
    Mail, das PDF (und ggf. die Bilder) nur einmal erzeugt. Ändert kein Abo. `nur_neue`: wer für diesen Report und
    Zeitraum schon automatisch etwas bekommen hat, wird übersprungen (automatischer Versand)."""
    if fehlt := mail.konfiguration(db).fehlt:
        raise mail.MailFehler(fehlt)
    schon = _bereits(db, art, z, "benutzer_id") if nur_neue else set()
    benutzer = [b for b in db.scalars(select(Benutzer).where(Benutzer.id.in_(benutzer_ids), Benutzer.aktiv)
                                      .order_by(Benutzer.name)) if b.id not in schon]
    ergebnis, gruss = Ergebnis(), _gruss(db)
    empfaenger: list[tuple[str, str, int | None, str]] = []  # (Name, Adresse, Benutzer-ID, Anrede)
    for b in benutzer:
        if b.email:
            empfaenger.append((b.name, b.email, b.id, f"Hallo {b.name},"))
        else:
            ergebnis.ohne_adresse.append(b.name)
    bekannt = {e[1].lower() for e in empfaenger}
    empfaenger += [(a, a, None, "Hallo,") for a in adressen if a.lower() not in bekannt]
    if not empfaenger:
        return ergebnis
    betreff, anhaenge, satz = _inhalt(db, art, z)
    if mit_bildern and art == "flottenbilanz":
        pdf = anhaenge[0][1]
        anhaenge += [(reports.dateiname(z, "flottenbilanz").removesuffix(".pdf") + f"-seite-{s}.jpg",
                      reports.als_jpg(pdf, s), "image/jpeg") for s in (1, 2)]
    for name, adresse, bid, anrede in empfaenger:
        was = (f"die Zuordnungen am {z.von:%d.%m.%Y}" if art == "zuordnungen" else
               f"{'die ' if art in ('zusammenfassung', 'flottenbilanz') else ''}{TITEL[art]} {z.label}")
        text = f"{anrede}\n\nanbei {was}.{(' ' + satz) if satz else ''}\n\nViele Grüße\n{gruss}"
        try:
            mail.senden(db, adresse, betreff, text, anhaenge)
        except mail.MailFehler as e:
            ergebnis.fehler.append((name, str(e)))
            _protokoll(db, art, z, name, adresse, False, str(e), ausgeloest, von, benutzer_id=bid)
        else:
            ergebnis.gesendet.append(name)
            _protokoll(db, art, z, name, adresse, True, None, ausgeloest, von, benutzer_id=bid)
    return ergebnis


def fahrer_senden(db: Session, z: Zeitraum, fahrer_ids: list[int] | None = None, ausgeloest: str = "manuell",
                  von: int | None = None, nur_neue: bool = False, kopie_an: Benutzer | None = None) -> Ergebnis:
    """Fahrer:innen-Reports senden – an `fahrer_ids` bzw. alle mit Ladungen im Zeitraum. `nur_neue`: wer für diesen
    Zeitraum schon einen (auch fehlgeschlagenen) automatischen Versand hatte, wird übersprungen. `kopie_an`: jeder
    gesendete Report zusätzlich als eigene Mail an diese Benutzer:in (nie an fremde Adressen – persönliche Daten)."""
    if fehlt := mail.konfiguration(db).fehlt:
        raise mail.MailFehler(fehlt)
    g = reports.grundlage(db, z)
    zeilen = {zl.fahrer.id: zl for zl in g.a.fahrer}
    ziele = [i for i in (fahrer_ids if fahrer_ids is not None else zeilen) if i in zeilen]
    schon = _bereits(db, "fahrer", z, "fahrer_id") if nur_neue else set()
    ergebnis, gruss = Ergebnis(), _gruss(db)
    for fid in ziele:
        if fid in schon:
            continue
        zl = zeilen[fid]
        person = zl.fahrer
        if not person.email:
            ergebnis.ohne_adresse.append(person.name)
            continue
        betreff, text = fahrer_text(person, z, zl.summe, gruss)
        try:
            pdf = reports.fahrer(db, z, fid, g)
            mail.senden(db, person.email, betreff, text,
                        [(reports.dateiname(z, "report", person.name), pdf, "application/pdf")])
        except mail.MailFehler as e:
            ergebnis.fehler.append((person.name, str(e)))
            _protokoll(db, "fahrer", z, person.name, person.email, False, str(e), ausgeloest, von, fahrer_id=fid)
        else:
            ergebnis.gesendet.append(person.name)
            _protokoll(db, "fahrer", z, person.name, person.email, True, None, ausgeloest, von, fahrer_id=fid)
            if kopie_an is not None and kopie_an.email:
                try:
                    mail.senden(db, kopie_an.email, f"Kopie: {betreff} ({person.name})", text,
                                [(reports.dateiname(z, "report", person.name), pdf, "application/pdf")])
                except mail.MailFehler as e:
                    ergebnis.fehler.append((f"Kopie ({person.name})", str(e)))
                    _protokoll(db, "fahrer", z, f"Kopie: {person.name}", kopie_an.email, False, str(e), ausgeloest,
                               von, benutzer_id=kopie_an.id)
                else:
                    _protokoll(db, "fahrer", z, f"Kopie: {person.name}", kopie_an.email, True, None, ausgeloest, von,
                               benutzer_id=kopie_an.id)
    return ergebnis


def zusammenfassung_senden(db: Session, z: Zeitraum, benutzer_ids: list[int], ausgeloest: str = "manuell",
                           von: int | None = None, nur_neue: bool = False) -> Ergebnis:
    if fehlt := mail.konfiguration(db).fehlt:
        raise mail.MailFehler(fehlt)
    empfaenger = db.scalars(select(Benutzer).where(Benutzer.id.in_(benutzer_ids), Benutzer.aktiv)
                            .order_by(Benutzer.name)).all()
    schon = _bereits(db, "zusammenfassung", z, "benutzer_id") if nur_neue else set()
    ergebnis, gruss = Ergebnis(), _gruss(db)
    pdf, gesamt = None, None
    for b in empfaenger:
        if b.id in schon:
            continue
        if not b.email:
            ergebnis.ohne_adresse.append(b.name)
            continue
        if pdf is None:  # erst erzeugen, wenn wirklich jemand etwas bekommt
            pdf = reports.zusammenfassung(db, z)
            gesamt = auswerten(db, z, app_einstellungen.laden(db)).gesamt
        betreff, text = zusammenfassung_text(b, z, gesamt, gruss)
        try:
            mail.senden(db, b.email, betreff, text, [(reports.dateiname(z, "zusammenfassung"), pdf, "application/pdf")])
        except mail.MailFehler as e:
            ergebnis.fehler.append((b.name, str(e)))
            _protokoll(db, "zusammenfassung", z, b.name, b.email, False, str(e), ausgeloest, von, benutzer_id=b.id)
        else:
            ergebnis.gesendet.append(b.name)
            _protokoll(db, "zusammenfassung", z, b.name, b.email, True, None, ausgeloest, von, benutzer_id=b.id)
    return ergebnis


# ------------------------------------------------------------------ Offene Aufgaben

def stand_heute(tag=None):
    """„Zeitraum“ für Protokoll und Versand der offenen Aufgaben: der Stichtag selbst."""
    from types import SimpleNamespace
    from app.config import heute
    tag = tag or heute()
    return SimpleNamespace(art="stand", von=tag, bis=tag, label=f"Stand {tag:%d.%m.%Y}")


def aufgaben_zahlen(daten: dict) -> list[str]:
    """Kurzfassung der offenen Punkte für den Mailtext („7 Fristen“, …), leer = nichts offen."""
    zn = daten["zahlen"]
    teile = [(len(daten["fristen"]), "Frist", "Fristen"), (zn.ohne_fahrzeug, "Ladevorgang ohne Fahrzeug", "Ladevorgänge ohne Fahrzeug"),
             (len(daten["luecken"]), "Lücke in der Zuordnung", "Lücken in der Zuordnung"),
             (zn.gebuehren_ohne_karte, "Gebühr ohne Karte", "Gebühren ohne Karte"),
             (zn.unplausibel, "unplausible AC/DC-Angabe", "unplausible AC/DC-Angaben"),
             (zn.ohne_stromart, "Ladevorgang ohne AC/DC-Angabe", "Ladevorgänge ohne AC/DC-Angabe"),
             (zn.ladeorte, "Ladeort ohne Verortung", "Ladeorte ohne Verortung"),
             (zn.vorschauen, "offene Import-Vorschau", "offene Import-Vorschauen")]
    return [f"{n} {eins if n == 1 else mehr}" for n, eins, mehr in teile if n]


def aufgaben_text(benutzer: Benutzer, stand, zahlen: list[str], gruss: str) -> tuple[str, str]:
    betreff = f"Ladefalke – Offene Aufgaben ({stand.label})"
    inhalt = ("Offen sind: " + ", ".join(zahlen) + ".") if zahlen else "Derzeit ist nichts offen."
    text = (f"Hallo {benutzer.name},\n\nanbei die offenen Aufgaben, {stand.label}. {inhalt}\n\n"
            f"Erledigen lässt sich alles unter „Aufgaben“ in der App. Du bekommst diese Mail, weil du die offenen "
            f"Aufgaben abonniert hast bzw. sie dir geschickt wurden (abbestellen unter „Mein Konto“).\n\nViele Grüße\n{gruss}")
    return betreff, text


def aufgaben_senden(db: Session, benutzer_ids: list[int], ausgeloest: str = "manuell", von: int | None = None,
                    nur_neue: bool = False, nur_wenn_offen: bool = False) -> Ergebnis:
    """Report „Offene Aufgaben“ (Stand heute) an Benutzer:innen. `nur_neue`: wer heute schon automatisch etwas
    bekommen hat, wird übersprungen; `nur_wenn_offen`: ohne offene Punkte nichts senden."""
    from app.services import offen
    if fehlt := mail.konfiguration(db).fehlt:
        raise mail.MailFehler(fehlt)
    stand = stand_heute()
    ergebnis = Ergebnis()
    daten = offen.fuer_report(db)
    zahlen = aufgaben_zahlen(daten)
    if nur_wenn_offen and not zahlen:
        return ergebnis
    empfaenger = db.scalars(select(Benutzer).where(Benutzer.id.in_(benutzer_ids), Benutzer.aktiv)
                            .order_by(Benutzer.name)).all()
    schon = _bereits(db, "aufgaben", stand, "benutzer_id") if nur_neue else set()
    gruss, pdf = _gruss(db), None
    for b in empfaenger:
        if b.id in schon:
            continue
        if not b.email:
            ergebnis.ohne_adresse.append(b.name)
            continue
        if pdf is None:
            pdf = reports.offene_aufgaben(db)
        betreff, text = aufgaben_text(b, stand, zahlen, gruss)
        try:
            mail.senden(db, b.email, betreff, text,
                        [(f"ladefalke-offene-aufgaben-{stand.von:%Y-%m-%d}.pdf", pdf, "application/pdf")])
        except mail.MailFehler as e:
            ergebnis.fehler.append((b.name, str(e)))
            _protokoll(db, "aufgaben", stand, b.name, b.email, False, str(e), ausgeloest, von, benutzer_id=b.id)
        else:
            ergebnis.gesendet.append(b.name)
            _protokoll(db, "aufgaben", stand, b.name, b.email, True, None, ausgeloest, von, benutzer_id=b.id)
    return ergebnis


def zeitplan_faellig(intervall: str, wochentag: int, tag: int, uhrzeit: str, jetzt: datetime) -> bool:
    """Ist heute ein Versandtag laut Zeitplan und die Uhrzeit erreicht? `tag` 0 = Monatsletzter."""
    import calendar
    if intervall == "aus":
        return False
    stunde, minute = (int(x) for x in uhrzeit.split(":"))
    if (jetzt.hour, jetzt.minute) < (stunde, minute):
        return False
    if intervall == "woechentlich":
        return jetzt.weekday() == wochentag
    if intervall == "monatlich":
        return jetzt.day == (tag or calendar.monthrange(jetzt.year, jetzt.month)[1])
    return True  # täglich


def aufgaben_faellig(e, jetzt: datetime) -> bool:
    return zeitplan_faellig(e.aufgaben_auto, e.aufgaben_auto_wochentag, e.aufgaben_auto_tag, e.aufgaben_auto_uhrzeit,
                            jetzt)


def zuordnungen_faellig(e, jetzt: datetime) -> bool:
    return zeitplan_faellig(e.zuordnungen_auto, e.zuordnungen_auto_wochentag, e.zuordnungen_auto_tag,
                            e.zuordnungen_auto_uhrzeit, jetzt)


def zuletzt_aufgaben(db: Session, art: str = "aufgaben") -> dict[int, ReportVersand]:
    """Letzter erfolgreicher Versand je Benutzer:in, unabhängig vom Zeitraum (offene Aufgaben, Zuordnungen)."""
    eintraege = db.scalars(select(ReportVersand).where(ReportVersand.art == art, ReportVersand.erfolg)
                           .order_by(ReportVersand.gesendet_am)).all()
    return {e.benutzer_id: e for e in eintraege if e.benutzer_id}


def _bereits(db: Session, art: str, z: Zeitraum, spalte: str) -> set[int]:
    """IDs, die für diesen Report und Zeitraum schon automatisch etwas bekommen haben (oder es versucht wurde)."""
    s = getattr(ReportVersand, spalte)
    return set(db.scalars(select(s).where(ReportVersand.art == art, ReportVersand.zeitraum_von == z.von,
                                         ReportVersand.zeitraum_bis == z.bis, ReportVersand.ausgeloest == "automatisch",
                                         s.is_not(None))))


def zuletzt(db: Session, z: Zeitraum) -> dict[tuple[str, int], ReportVersand]:
    """Letzter erfolgreicher Versand je (art, fahrer_id/benutzer_id) für diesen Zeitraum – für die Reports-Seite."""
    eintraege = db.scalars(select(ReportVersand).where(
        ReportVersand.zeitraum_von == z.von, ReportVersand.zeitraum_bis == z.bis, ReportVersand.erfolg)
        .order_by(ReportVersand.gesendet_am)).all()
    # Fahrer:innen-Reports nach Fahrer-ID (Kopien an Benutzer:innen zählen dort nicht), die übrigen nach Benutzer-ID
    return {(e.art, e.fahrer_id if e.art == "fahrer" else e.benutzer_id): e for e in eintraege
            if (e.fahrer_id if e.art == "fahrer" else e.benutzer_id)}


def protokoll(db: Session, anzahl: int = 30) -> list[ReportVersand]:
    return db.scalars(select(ReportVersand).order_by(ReportVersand.gesendet_am.desc(), ReportVersand.id.desc())
                      .limit(anzahl)).all()


# ------------------------------------------------------------------ automatisch

def faellige_zeitraeume(jetzt: datetime, laengere: bool) -> list[Zeitraum]:
    """Vormonat; mit `laengere` zusätzlich Quartal, Halbjahr und Jahr, die mit dem Vormonat enden."""
    vm = vormonat(jetzt.date())
    ergebnis = [vm]
    if laengere:
        for art in ("quartal", "halbjahr", "jahr"):
            z = zeitraum_mit(vm.von, art)
            if z.bis == vm.bis:
                ergebnis.append(z)
    return ergebnis


def automatisch_faellig(e, jetzt: datetime) -> bool:
    if not e.report_auto:
        return False
    stunde, minute = (int(x) for x in e.report_auto_uhrzeit.split(":"))
    return (jetzt.day, jetzt.hour, jetzt.minute) >= (e.report_auto_tag, stunde, minute)


def automatisch_pruefen() -> None:
    """Vom Taktgeber minütlich: offene Aufgaben nach ihrem Zeitplan; ist der Versandtermin im laufenden Monat erreicht,
    die Reports der fälligen Zeiträume an alle schicken, die sie noch nicht automatisch bekommen haben."""
    from app.db import SessionLocal
    jetzt = _jetzt()
    with SessionLocal() as db:
        e = app_einstellungen.laden(db)
        if mail.konfiguration(db).fehlt:
            return
        if aufgaben_faellig(e, jetzt):
            try:
                abo = list(db.scalars(select(Benutzer.id).where(Benutzer.aufgaben_abo, Benutzer.aktiv)))
                if abo:
                    erg = aufgaben_senden(db, abo, ausgeloest="automatisch", nur_neue=True,
                                          nur_wenn_offen=e.aufgaben_auto_nur_offen)
                    if erg.gesendet or erg.fehler:
                        log.info(erg.text("Offene Aufgaben"))
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("Automatischer Versand der offenen Aufgaben fehlgeschlagen")
        if zuordnungen_faellig(e, jetzt):
            try:
                abo = list(db.scalars(select(Benutzer.id).where(Benutzer.zuordnungen_abo, Benutzer.aktiv)))
                if abo:
                    erg = report_senden(db, "zuordnungen", stand_heute(jetzt.date()), abo, ausgeloest="automatisch",
                                        nur_neue=True)
                    if erg.gesendet or erg.fehler:
                        log.info(erg.text("Zuordnungen am Stichtag"))
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("Automatischer Versand der Zuordnungen fehlgeschlagen")
        if not automatisch_faellig(e, jetzt):
            return
        for z in faellige_zeitraeume(jetzt, e.report_auto_laengere):
            try:
                if e.report_auto_fahrer:
                    erg = fahrer_senden(db, z, ausgeloest="automatisch", nur_neue=True)
                    if erg.gesendet or erg.fehler:
                        log.info(erg.text(f"Fahrer:innen-Reports {z.label}"))
                if e.report_auto_zusammenfassung:
                    abo = list(db.scalars(select(Benutzer.id).where(Benutzer.zusammenfassung_abo, Benutzer.aktiv)))
                    if abo:
                        erg = zusammenfassung_senden(db, z, abo, ausgeloest="automatisch", nur_neue=True)
                        if erg.gesendet or erg.fehler:
                            log.info(erg.text(f"Zusammenfassung {z.label}"))
                for art, an in (("karten_cpo", e.report_auto_karten_cpo), ("flottenbilanz", e.report_auto_flottenbilanz)):
                    if not an:
                        continue
                    abo = list(db.scalars(select(Benutzer.id).where(getattr(Benutzer, ABO[art]), Benutzer.aktiv)))
                    if abo:
                        erg = report_senden(db, art, z, abo, ausgeloest="automatisch", nur_neue=True)
                        if erg.gesendet or erg.fehler:
                            log.info(erg.text(f"{TITEL[art]} {z.label}"))
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("Automatischer Report-Versand für %s fehlgeschlagen", z.label)
