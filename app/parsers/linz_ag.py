"""Parser: LINZ AG Strommobil (PDF).

Positionen „Ladevorgang AC 22“ mit Standort, Start, Dauer in Minuten und Kartennummer, darunter
die Wertzeilen „20,922 kWh 6,80“ und ggf. „39 min 3,90“ (Infrastrukturbelegung). Beträge netto.
Die Grundgebühr steht ohne Kartennummer als Sammelposition („4 MON 25,00“) und wird centgenau
gleichmäßig auf die Karten dieser Rechnung aufgeteilt.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.formatierung import decimal_lesen
from app.models import KennungTyp
from app.parsers.basis import (Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang, ist_pdf, kwh_runden,
                               pdf_text, stromart_normieren, ust_setzen)

BETRAG = r"-?\d[\d.]*,\d{2}"
RE_POSITION = re.compile(r"^(\d{6})\s+(.*)$")
RE_WERT = re.compile(rf"^(-?\d[\d.]*(?:,\d+)?)\s+([A-Za-z]+)\s+({BETRAG})$")
RE_LADEN = re.compile(r"^(.*?)\s+-\s+(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}),\s*(\d+)\s*MIN\b", re.IGNORECASE)
RE_KARTE = re.compile(r"Kartennr\.\s*(\d+)")
RE_ZEITRAUM = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")


def _datum(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def _cent_aufteilen(betrag: Decimal, n: int) -> list[Decimal]:
    cents = int(betrag * 100)
    basis, rest = divmod(cents, n)
    return [Decimal(basis + (1 if i < rest else 0)) / 100 for i in range(n)]


class LinzAgParser(Parser):
    key = "linz_ag"
    name = "LINZ AG Strommobil (PDF)"
    msp_name = "LINZ AG"
    standardland = "AT"

    def erkennen(self, pfad: Path) -> bool:
        if not ist_pdf(pfad):
            return False
        text = pdf_text(pfad, nur_erste_seite=True)
        return "LINZ STROM" in text or "strommobil" in text.lower()

    def parsen(self, pfad: Path) -> Ergebnis:
        return parsen_text(pdf_text(pfad))


def parsen_text(text: str) -> Ergebnis:
    rechnung = Rechnung()
    if m := re.search(r"Rechnung Nr\.\s*(\d+)", text):
        rechnung.nummer = m.group(1)
    if m := re.search(r"Belegdatum\s+(\d{2}\.\d{2}\.\d{4})", text):
        rechnung.datum = _datum(m.group(1))
    warnungen = []

    # Tabellenzeilen sammeln: ab „in EUR“ bis Seitenende („||“-Zeilen) bzw. „Nettowert“
    tabelle, in_tabelle = [], False
    for z in (z.strip() for z in text.splitlines()):
        if z.startswith("in EUR"):
            in_tabelle = True
        elif m := re.match(rf"^Nettowert\s+({BETRAG})", z):
            rechnung.summe_netto = decimal_lesen(m.group(1))
            in_tabelle = False
        elif z.startswith("||"):
            in_tabelle = False
        elif in_tabelle and z:
            tabelle.append(z)
    if rechnung.summe_netto is None:
        warnungen.append("„Nettowert“ nicht gefunden – das PDF wirkt unvollständig.")

    positionen = []
    for z in tabelle:
        if m := RE_POSITION.match(z):
            positionen.append({"nr": m.group(1), "titel": m.group(2), "zeilen": []})
        elif positionen:
            positionen[-1]["zeilen"].append(z)

    ergebnis = Ergebnis(rechnung=rechnung, warnungen=warnungen)
    sammelgebuehren = []
    for pos in positionen:
        text_pos = " ".join(pos["zeilen"])
        wert, kwh = Decimal(0), Decimal(0)
        for z in pos["zeilen"]:
            if m := RE_WERT.match(z):
                wert += decimal_lesen(m.group(3))
                if m.group(2).lower() == "kwh":
                    kwh += decimal_lesen(m.group(1))
        if pos["titel"].startswith("Ladevorgang") and (m := RE_LADEN.search(text_pos)):
            karte = RE_KARTE.search(text_pos)
            start = datetime.strptime(m.group(2), "%d.%m.%Y %H:%M")
            art = pos["titel"].split()[1] if len(pos["titel"].split()) > 1 else ""
            ergebnis.vorgaenge.append(Vorgang(
                kartenkennung=karte.group(1) if karte else None, kennung_typ=KennungTyp.kartennummer,
                start=start, ende=start + timedelta(minutes=int(m.group(3))), kwh=kwh_runden(kwh),
                kosten_netto=wert, standort=" ".join(m.group(1).split()) or None,
                kartenbezeichnung=f"LINZ AG Karte {karte.group(1)}" if karte else None,
                stromart=stromart_normieren(art),
                rohdaten={"position": pos["nr"], "titel": pos["titel"], "art": art, "zeilen": pos["zeilen"]}))
            if not karte:
                warnungen.append(f"Position {pos['nr']}: Kartennummer nicht gefunden.")
            continue
        zr = RE_ZEITRAUM.search(text_pos)
        geb = {"bezeichnung": pos["titel"].split("  ")[0], "betrag": wert,
               "von": _datum(zr.group(1)) if zr else None, "bis": _datum(zr.group(2)) if zr else None}
        if karte := RE_KARTE.search(text_pos):
            ergebnis.gebuehren.append(Gebuehr(geb["bezeichnung"], wert, kartenkennung=karte.group(1),
                                              kartenbezeichnung=f"LINZ AG Karte {karte.group(1)}",
                                              zeitraum_von=geb["von"], zeitraum_bis=geb["bis"]))
        else:
            sammelgebuehren.append(geb)

    # Sammelpositionen ohne Karte (Grundgebühr) gleichmäßig auf die Karten der Rechnung aufteilen
    karten = list(dict.fromkeys(v.kartenkennung for v in ergebnis.vorgaenge if v.kartenkennung))
    for geb in sammelgebuehren:
        titel = re.sub(rf"\s+{BETRAG}.*$|\s+[\d,]+\s*/\s*MON.*$", "", geb["bezeichnung"]).strip()
        if not karten:
            ergebnis.gebuehren.append(Gebuehr(titel, geb["betrag"], zeitraum_von=geb["von"], zeitraum_bis=geb["bis"]))
            continue
        for karte, anteil in zip(karten, _cent_aufteilen(geb["betrag"], len(karten))):
            ergebnis.gebuehren.append(Gebuehr(f"{titel} (anteilig 1/{len(karten)})", anteil, kartenkennung=karte,
                                              kartenbezeichnung=f"LINZ AG Karte {karte}",
                                              zeitraum_von=geb["von"], zeitraum_bis=geb["bis"]))

    summe = sum((v.kosten_netto for v in ergebnis.vorgaenge), Decimal(0)) + \
        sum((g.betrag_netto for g in ergebnis.gebuehren), Decimal(0))
    if rechnung.summe_netto is not None and summe != rechnung.summe_netto:
        warnungen.append(f"Summe der Positionen {summe} EUR ≠ Nettowert {rechnung.summe_netto} EUR.")
    if not ergebnis.vorgaenge and not ergebnis.gebuehren:
        raise ParserFehler("Keine Positionen gefunden – ist das eine LINZ-AG-Rechnung?")
    zeitraeume = [(g.zeitraum_von, g.zeitraum_bis) for g in ergebnis.gebuehren if g.zeitraum_von]
    if zeitraeume:
        rechnung.zeitraum_von, rechnung.zeitraum_bis = min(z[0] for z in zeitraeume), max(z[1] for z in zeitraeume)
    elif ergebnis.vorgaenge:
        rechnung.zeitraum_von = min(v.ende for v in ergebnis.vorgaenge).date()
        rechnung.zeitraum_bis = max(v.ende for v in ergebnis.vorgaenge).date()
    # USt.: „Umsatzsteuer 20,00% v. 94,16 18,83“ (Inlandsrechnung)
    if m := re.search(rf"Umsatzsteuer\s+(\d+(?:,\d+)?)\s*%\s*v\.\s*{BETRAG}\s+({BETRAG})", text):
        if warnung := ust_setzen(ergebnis.vorgaenge + ergebnis.gebuehren, decimal_lesen(m.group(1)), "AT",
                                 decimal_lesen(m.group(2))):
            warnungen.append(warnung)
    return ergebnis
