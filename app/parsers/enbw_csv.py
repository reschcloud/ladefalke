"""Parser: EnBW mobility+ (CSV-Export einer Rechnung) – Teil des EnBW-Parsers (Schlüssel „enbw“, siehe enbw.py).

Eine Zeile je Position, Spalte „Positionstyp“:
- „Ladevorgang“: Ladestart/-ende (Ortszeit), Lademedium (Ladekarte), Ladepunkt (EVSE-ID), Stromtyp,
  Transaktions ID (externe ID – dieselbe wie im PDF, daher keine Doppelten bei PDF und CSV), Energie (kWh, nur
  zwei Nachkommastellen; das PDF hat drei), Energie- und Blockiergebühr netto.
- „Monatliche Grundgebühr je Nutzer“: Grundgebühr der Karte (Identifikation RFID).
- Grundgebühr ohne Nutzer und Kartenbestellungen: Gebühren ohne Kartenbezug.
Beträge haben vier Nachkommastellen; auf Cent gerundet wie im PDF (kaufmännisch auf die gerade Ziffer:
12,0250 → 12,02). Der Abrechnungsmonat steht nicht in der Datei: aus dem Dateinamen
(„Rechnung_2026_09_DE_….csv“), sonst aus den Ladevorgängen, sonst Vormonat des Rechnungsdatums (mit Warnung).
Fahrer- und Fahrzeugangaben der Datei werden nicht übernommen (die Zuordnung läuft über die Stammdaten).
"""
import csv
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from app.formatierung import decimal_lesen
from app.models import KennungTyp
from app.parsers.basis import CENT, Ergebnis, Gebuehr, ParserFehler, Rechnung, Vorgang, kwh_runden, stromart_normieren

KOPF = ("Rechnungsnummer", "Rechnungsdatum", "Rechnungstyp")
PFLICHTSPALTEN = {"Rechnungsnummer", "Positionstyp", "Lademedium", "Transaktions ID", "Energie (kWh)"}
# nicht in die Rohdaten übernehmen (Personen- und Kundendaten)
OHNE = {"Kundennummer", "Umsatzsteuer-ID", "Fahrer Bezeichnung", "Fahrer Nachname", "Fahrer Vorname",
        "Personalnummer", "Fahrzeug Kennzeichen", "Fahrzeug Kilometerstand", "Fahrzeug Kilometerstand Datum"}


def ist_enbw_csv(pfad: Path) -> bool:
    try:
        with open(pfad, encoding="utf-8-sig") as f:
            spalten = set(f.readline().strip().split(";"))
    except (UnicodeDecodeError, OSError):
        return False
    return PFLICHTSPALTEN <= spalten


def _betrag(text: str | None) -> Decimal:
    return decimal_lesen(text) if (text or "").strip() else Decimal(0)


def _cent(betrag: Decimal) -> Decimal:
    return betrag.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _datum(text: str) -> date:
    return datetime.strptime(text.strip(), "%d.%m.%Y").date()


def _zeit(datum_text: str, uhrzeit: str) -> datetime:
    return datetime.strptime(f"{datum_text.strip()} {uhrzeit.strip()}", "%d.%m.%Y %H:%M:%S")


def _monat(jahr: int, mon: int) -> tuple[date, date]:
    von = date(jahr, mon, 1)
    return von, (von.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def parsen(pfad: Path, dateiname: str | None = None) -> Ergebnis:
    with open(pfad, encoding="utf-8-sig", newline="") as f:
        zeilen = list(csv.DictReader(f, delimiter=";"))
    if not zeilen:
        raise ParserFehler("Die CSV-Datei enthält keine Positionen.")
    r = Rechnung()
    warnungen: list[str] = []
    ergebnis = Ergebnis(rechnung=r, warnungen=warnungen)

    nummern = sorted({z["Rechnungsnummer"].strip() for z in zeilen if z.get("Rechnungsnummer")})
    r.nummer = nummern[0] if nummern else None
    if len(nummern) > 1:
        warnungen.append(f"Die Datei enthält mehrere Rechnungen ({', '.join(nummern)}) – bitte je Rechnung "
                         "eine Datei importieren.")
    if datum := next((z["Rechnungsdatum"] for z in zeilen if z.get("Rechnungsdatum")), None):
        r.datum = _datum(datum)

    gebuehren_ohne_zeitraum: list[Gebuehr] = []
    for z in zeilen:
        typ = (z.get("Positionstyp") or "").strip()
        land = (z.get("Leistungserbringungsland") or "").strip()
        if typ == "Ladevorgang":
            karte = (z.get("Lademedium") or "").strip() or None
            strasse, plz, ort = ((z.get(k) or "").strip() for k in ("Straße", "PLZ", "Ort"))
            ergebnis.vorgaenge.append(Vorgang(
                kartenkennung=karte, kennung_typ=KennungTyp.kartennummer,
                start=_zeit(z["Datum Ladestart"], z["Uhrzeit Ladestart"]),
                ende=_zeit(z["Datum Ladeende"], z["Uhrzeit Ladeende"]),
                kwh=kwh_runden(_betrag(z.get("Energie (kWh)"))),
                kosten_netto=_cent(_betrag(z.get("Energiekosten Netto (Euro)"))
                                   + _betrag(z.get("Blockiergebühr Netto (Euro)"))),
                externe_id=(z.get("Transaktions ID") or "").strip() or None,
                ladepunkt=(z.get("Ladepunkt") or "").strip() or None,
                standort=", ".join(x for x in (strasse, f"{plz} {ort}".strip()) if x) or None,
                kartenbezeichnung=f"EnBW {karte}" if karte else None,
                stromart=stromart_normieren(z.get("Stromtyp")),
                rohdaten={"rechnung": r.nummer, "land": land, "csv": {k: v for k, v in z.items()
                                                                      if v and k not in OHNE}}))
            if not karte:
                warnungen.append(f"Ladevorgang {z['Datum Ladestart']} {z['Uhrzeit Ladestart']}: "
                                 "Ladekarte nicht gefunden.")
            continue
        # Gebühren: je Nutzer (Karte), allgemein, Kartenbestellung
        je_nutzer = _betrag(z.get("Grundgebühr je Nutzer Netto (Euro)"))
        allgemein = _betrag(z.get("Grundgebühr Netto (Euro)"))
        karten = _betrag(z.get("Kartenkosten Netto (Euro)"))
        if je_nutzer:
            karte = (z.get("Identifikation Grundgebühr je Nutzer (RFID)") or "").strip() or None
            gebuehren_ohne_zeitraum.append(Gebuehr(
                "Monatliche Grundgebühr", _cent(je_nutzer), kartenkennung=karte,
                kartenbezeichnung=f"EnBW {karte}" if karte else None))
        if allgemein:
            gebuehren_ohne_zeitraum.append(Gebuehr("Monatliche Grundgebühr", _cent(allgemein)))
        if karten:
            bestellt = (z.get("Bestellte Karten") or "").strip()
            gebuehren_ohne_zeitraum.append(Gebuehr(f"Ladekartenbestellung{f' ({bestellt})' if bestellt else ''}",
                                                   _cent(karten)))
        if not (je_nutzer or allgemein or karten):
            sonst = _betrag(z.get("Energiekosten Netto (Euro)")) + _betrag(z.get("Blockiergebühr Netto (Euro)"))
            if sonst:
                gebuehren_ohne_zeitraum.append(Gebuehr(typ or "Sonstige Position", _cent(sonst)))
            warnungen.append(f"Unbekannte Position „{typ or '–'}“"
                             + (f" mit {_cent(sonst)} € als Gebühr erfasst – bitte prüfen." if sonst
                                else " ohne Betrag übersprungen."))

    # Abrechnungsmonat: Dateiname, sonst Ladevorgänge, sonst Vormonat des Rechnungsdatums
    if dateiname and (m := re.search(r"(?<!\d)(20\d{2})_(0[1-9]|1[0-2])(?!\d)", dateiname)):
        r.zeitraum_von, r.zeitraum_bis = _monat(int(m.group(1)), int(m.group(2)))
    elif ergebnis.vorgaenge:
        erster = min(v.start for v in ergebnis.vorgaenge)
        letzter = max(v.ende for v in ergebnis.vorgaenge)
        r.zeitraum_von, r.zeitraum_bis = _monat(erster.year, erster.month)[0], _monat(letzter.year, letzter.month)[1]
    elif r.datum:
        vormonat = r.datum.replace(day=1) - timedelta(days=1)
        r.zeitraum_von, r.zeitraum_bis = _monat(vormonat.year, vormonat.month)
        warnungen.append(f"Abrechnungsmonat nicht erkennbar – Gebühren zählen für {vormonat:%m/%Y} (Vormonat des "
                         "Rechnungsdatums). Bitte prüfen; der Dateiname „Rechnung_JJJJ_MM_…“ legt den Monat fest.")
    for g in gebuehren_ohne_zeitraum:
        g.zeitraum_von, g.zeitraum_bis = r.zeitraum_von, r.zeitraum_bis
        ergebnis.gebuehren.append(g)

    # keine Gesamtsumme in der Datei: Summe der Positionen
    r.summe_netto = sum((v.kosten_netto for v in ergebnis.vorgaenge), Decimal(0)) + \
        sum((g.betrag_netto for g in ergebnis.gebuehren), Decimal(0))
    if not ergebnis.vorgaenge and not ergebnis.gebuehren:
        raise ParserFehler("Keine Ladevorgänge oder Gebühren gefunden – ist das ein EnBW-CSV-Export?")
    return ergebnis
