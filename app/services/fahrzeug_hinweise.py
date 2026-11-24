"""Hinweise für die Listen (Spalte „Hinweise“): alles, was beim Fahrzeug bzw. bei der Person zu klären ist.

Fahrer:innen (`fuer_fahrer`): Führerscheinkontrolle (nur mit Feature) überfällig (rot), bald fällig (gelb), fehlt
(grau); E-Mail-Adresse fehlt (grau).
Fahrzeuge (`ermitteln`):

- Leasing: keine Leasingdaten, Leasing-Ende fehlt, endet bald (Hinweisfrist aus den Einstellungen), abgelaufen
- Vignette und Streckenmaut FLEX (nur mit Feature und wenn beim Fahrzeug nötig): Gültigkeit fehlt (grau), noch
  nicht gültig oder abgelaufen (rot), läuft bald ab (gelb)
- Zuordnung: aktuell ohne Fahrer:in, Lücken in der Fahrer:innen-Zuordnung (services/luecken.py); Zeiträume
  „ohne Fahrer:in“ (bewusst niemandem zugeordnet) sind kein Hinweis
- Ladevorgänge: unplausible AC/DC-Angaben, Vorgänge ohne AC/DC-Angabe
- Ladekarten (services/kartenpruefung.py): Anzahl der Fahrzeugkarten außerhalb der Grenzen, Zuordnung außerhalb des
  Leasings
- Fahrer:innen, Vignette, Streckenmaut FLEX außerhalb des Leasings (services/leasingpruefung.py, nur Warnung)
Stufen: „rot“ (dringend), „gelb“ (klären), „grau“ (fehlende Angabe). Ohne Hinweis zeigt die Liste einen grünen Haken.
Nur für aktive Fahrzeuge.
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.formatierung import datum, spanne
from app.config import heute
from app.models import Fahrzeug, FahrzeugOhneFahrer, Ladevorgang, ZuordnungFahrerFahrzeug
from app.services import kartenpruefung, leasingpruefung, luecken, stromart
from app.services.app_einstellungen import AppEinstellungen
from app.services.fristen import MAUT, einstufen, maut_aktiv, maut_frist, naechste_kontrolle
from app.services.ladeprofil import unplausibel_sql
from app.services.zuordnung import gueltig_am

STUFEN = ("rot", "gelb", "grau")


@dataclass(frozen=True)
class Hinweis:
    stufe: str  # rot | gelb | grau
    text: str
    link: str
    titel: str = ""


def _anzahl_je_fahrzeug(db: Session, bedingung) -> dict[int, int]:
    return dict(db.execute(select(Ladevorgang.fahrzeug_id, func.count()).where(
        Ladevorgang.fahrzeug_id.is_not(None), bedingung).group_by(Ladevorgang.fahrzeug_id)).all())


def ermitteln(db: Session, fahrzeuge: list[Fahrzeug], e: AppEinstellungen) -> dict[int, list[Hinweis]]:
    """Hinweise je Fahrzeug-ID (nur aktive Fahrzeuge; leere Liste = alles in Ordnung), dringende zuerst."""
    luecken_je = {}
    for l in luecken.ermitteln(db):
        luecken_je.setdefault(l.fahrzeug.id, []).append(l)
    heute_zugeordnet = set(db.scalars(select(ZuordnungFahrerFahrzeug.fahrzeug_id).where(
        gueltig_am(ZuordnungFahrerFahrzeug, heute()))))
    heute_zugeordnet |= set(db.scalars(select(FahrzeugOhneFahrer.fahrzeug_id).where(  # bewusst ohne Fahrer:in
        gueltig_am(FahrzeugOhneFahrer, heute()))))
    karten_befunde: dict[int, list] = {}
    for b in kartenpruefung.ermitteln(db, e, fahrzeuge=fahrzeuge):
        karten_befunde.setdefault(b.fahrzeug.id, []).append(b)
    leasing_warnungen = leasingpruefung.ermitteln(db, e, [fz for fz in fahrzeuge if fz.aktiv])
    unplausibel = _anzahl_je_fahrzeug(db, unplausibel_sql(e))
    ohne_stromart = _anzahl_je_fahrzeug(db, stromart.ohne_angabe())

    def daten_hinweise(fz: Fahrzeug, h: list[Hinweis]) -> None:
        """Hinweise zu den Ladevorgängen – gelten auch für Ersatz-/Mietfahrzeuge."""
        if n := unplausibel.get(fz.id):
            h.append(Hinweis("gelb", f"{n} unplausible AC/DC", f"/ladevorgaenge?fahrzeug_id={fz.id}&unplausibel=1",
                             "AC/DC passt nicht zur Leistung über den Vorgang"))
        if n := ohne_stromart.get(fz.id):
            h.append(Hinweis("grau", f"{n} ohne AC/DC", f"/ladevorgaenge?fahrzeug_id={fz.id}&stromart=-",
                             "Ladevorgänge ohne AC/DC-Angabe (unter „Aufgaben“ setzbar)"))

    ergebnis = {}
    for fz in fahrzeuge:
        if not fz.aktiv:
            continue
        seite = f"/stammdaten/fahrzeuge/{fz.id}"
        h: list[Hinweis] = []
        if fz.ersatzfahrzeug:  # keine Prüfungen der Dienstwagen (Leasing, Lücken, Karten, Maut)
            h.append(Hinweis("grau", "Ersatz-/Mietfahrzeug", seite,
                             "Wird zugeordnet und ausgewertet, aber nicht wie ein Dienstwagen geprüft"))
            daten_hinweise(fz, h)
            ergebnis[fz.id] = sorted(h, key=lambda x: STUFEN.index(x.stufe))
            continue
        # Leasing
        if not fz.leasing_beginn and not fz.leasing_ende:
            h.append(Hinweis("grau", "keine Leasingdaten", seite, "Leasingbeginn und -ende beim Fahrzeug eintragen"))
        elif not fz.leasing_ende:
            h.append(Hinweis("grau", "Leasing-Ende fehlt", seite))
        else:
            stufe = einstufen(fz.leasing_ende, e.leasing_vorwarnung_tage)
            if stufe == "ueberfaellig":
                h.append(Hinweis("rot", f"Leasing abgelaufen ({datum(fz.leasing_ende)})", seite))
            elif stufe == "bald":
                h.append(Hinweis("gelb", f"Leasing endet {datum(fz.leasing_ende)}", seite,
                                 f"innerhalb der Hinweisfrist von {e.leasing_vorwarnung_tage} Tagen"))
        # Vignette und Streckenmaut FLEX (nur mit Feature und wenn beim Fahrzeug nötig)
        for art, name in MAUT.items():
            if maut_aktiv(e, art) and (f := maut_frist(fz, art, e)) is not None:
                h.append(_maut_hinweis(f, name, seite))
        # Fahrer:innen-Zuordnung
        eigene = luecken_je.get(fz.id, [])
        offen = next((l for l in eigene if l.offen), None)
        if offen:
            h.append(Hinweis("rot", f"ohne Fahrer:in seit {datum(offen.von)}", seite,
                             "Aktuell ist keine Fahrer:in zugeordnet" + ("" if offen.endlos else f" (Lücke bis {datum(offen.bis)})")))
        elif fz.id not in heute_zugeordnet:  # ohne Bezugszeitraum (weder Leasing noch je eine Zuordnung)
            h.append(Hinweis("rot", "ohne Fahrer:in", seite, "Aktuell ist keine Fahrer:in zugeordnet"))
        frueher = [l for l in eigene if not l.offen]
        if frueher:
            betroffen = sum(l.ladevorgaenge for l in frueher)
            h.append(Hinweis("gelb", f"{len(frueher)} Lücke{'n' if len(frueher) != 1 else ''} in der Zuordnung",
                             "/stammdaten/zuordnungen/zeitleiste?ansicht=alles",
                             "; ".join(f"{datum(l.von)} – {datum(l.bis)}" for l in frueher)
                             + (f" – {betroffen} Ladevorgänge ohne Person" if betroffen else "")))
        # Ladekarten: aktueller Befund einzeln, übrige Zeiträume zusammengefasst; außerhalb des Leasings
        befunde = karten_befunde.get(fz.id, [])
        karten_seite = f"{seite}#ladekarten"
        if aktuell := next((b for b in befunde if b.art == "anzahl" and b.offen), None):
            h.append(Hinweis(aktuell.stufe, aktuell.anzahl_text, karten_seite, f"{aktuell.text} – {aktuell.erklaerung}"))
        if andere := [b for b in befunde if b.art == "anzahl" and not b.offen]:
            h.append(Hinweis("gelb", f"{len(andere)} Zeitr{'äume' if len(andere) != 1 else 'aum'}: Kartenanzahl",
                             karten_seite, "; ".join(b.text for b in andere) + f" – {andere[0].erklaerung}"))
        if ausserhalb := [b for b in befunde if b.art == "ausserhalb"]:
            h.append(Hinweis("rot", "Karte außerhalb des Leasings", karten_seite, "; ".join(b.text for b in ausserhalb)))
        # Fahrer:innen, Vignette, Streckenmaut FLEX außerhalb des Leasings – je Art ein Hinweis
        warnungen = leasing_warnungen.get(fz.id, [])
        for art, text, ziel in (("fahrer", "Fahrer:in außerhalb des Leasings", f"{seite}#zeitleiste"),
                                *((a, f"{n} außerhalb des Leasings", seite) for a, n in MAUT.items())):
            if eigene := [w for w in warnungen if w.art == art]:
                h.append(Hinweis("gelb", text, ziel, "; ".join(w.text for w in eigene)))
        daten_hinweise(fz, h)
        ergebnis[fz.id] = sorted(h, key=lambda x: STUFEN.index(x.stufe))
    return ergebnis


def _maut_hinweis(f, name: str, seite: str) -> Hinweis:
    if f.zustand == "fehlt":
        return Hinweis("grau", f"{name}: Gültigkeit fehlt", seite, "Gültig ab und bis beim Fahrzeug eintragen")
    if f.zustand == "noch_nicht":
        return Hinweis("rot", f"{name} erst ab {datum(f.ab)}", seite, "Aktuell keine gültige " + name)
    if f.zustand == "abgelaufen":
        return Hinweis("rot", f"{name} abgelaufen ({datum(f.faellig)})", seite)
    return Hinweis("gelb", f"{name} läuft ab {datum(f.faellig)}", seite, f"in {spanne(f.tage)}")


def fuer_fahrer(fahrer: list, e: AppEinstellungen) -> dict[int, tuple[list[Hinweis], str]]:
    """Je aktiver Person: (Hinweise, Tooltip für den grünen Haken)."""
    ergebnis = {}
    for f in fahrer:
        if not f.aktiv:
            continue
        seite = f"/stammdaten/fahrer/{f.id}"
        naechste = naechste_kontrolle(f, e) if e.feature_fuehrerschein else None
        stufe = einstufen(naechste, e.fuehrerschein_vorwarnung_tage) if e.feature_fuehrerschein else ""
        h = []
        if stufe == "fehlt":
            h.append(Hinweis("grau", "Führerscheinkontrolle fehlt", seite, "Datum der letzten Kontrolle eintragen"))
        elif stufe == "ueberfaellig":
            h.append(Hinweis("rot", f"Führerscheinkontrolle überfällig ({datum(naechste)})", seite))
        elif stufe == "bald":
            h.append(Hinweis("gelb", f"Führerscheinkontrolle fällig {datum(naechste)}", seite,
                             f"innerhalb der Hinweisfrist von {e.fuehrerschein_vorwarnung_tage} Tagen"))
        if not f.email:
            h.append(Hinweis("grau", "E-Mail fehlt", seite, "E-Mail-Adresse eintragen – für den Versand des eigenen Reports"))
        ergebnis[f.id] = (h, f"Nächste Führerscheinkontrolle am {datum(naechste)}" if naechste else "")
    return ergebnis
