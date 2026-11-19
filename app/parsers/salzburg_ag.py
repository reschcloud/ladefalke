"""Parser: Salzburg AG (PDF oder CSV-Export „Stromladen_Beleg“; CSV siehe salzburg_ag_csv.py).

Ein Dokument kann mehrere Länderrechnungen enthalten (je „Dokument-Nr.“), davor ggf. ein
Deckblatt „Rechnungsübersicht“ (wird übersprungen). Je Rechnung: Positionen je Karte
(„1 K00001 / K00001“ mit Grundgebühr und Ladekosten) und der „Ladenachweis“ mit Einzelvorgängen
(Start, Ende, kWh, Betrag netto, EVSE-ID, Transaktionsnummer „# 100000001“ als externe ID).

Älteres Format (Rechnungen bis etwa 2024): „Belegnummer“ statt „Dokument-Nr.“, Beträge mit nachgestelltem „EUR“
(„2,50 EUR“ statt „EUR 2,50“), Transaktionsnummer mit Session („# 100000001 / Session: 200000001“), Land nur in der
Position („Gesamtkosten für Ihre Ladevorgänge (AT)“).
"""
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.formatierung import decimal_lesen
from app.models import KennungTyp
from app.parsers import salzburg_ag_csv
from app.parsers.basis import Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang, ist_pdf, kwh_runden, pdf_text

BETRAG = r"-?[\d.]+,\d{2}"
# Betrag mit vorangestelltem („EUR 2,50“, aktuelles Format) oder nachgestelltem „EUR“ („2,50 EUR“, älteres Format)
EUR_BETRAG = rf"(?:EUR\s*{BETRAG}|{BETRAG}\s*EUR)"
EUR_BETRAG_ENDE = rf"(?:EUR\s*(?P<betrag_a>{BETRAG})|(?P<betrag_b>{BETRAG})\s*EUR)\s*$"
RE_DOKUMENT = re.compile(r"(?:Dokument-Nr\.|Belegnummer)\s*(\d+)")
RE_KARTE_POS = re.compile(r"^\d+\s+(\S+)\s*/\s*(.+)$")
RE_UNTERPOS = re.compile(rf"^\d+\.\d+\s+(.*?)\s+[\d,]+\s+{EUR_BETRAG}.*?{EUR_BETRAG_ENDE}")
RE_KARTE_NACHWEIS = re.compile(r"^([A-Z0-9]+)\s+-\s+([A-Z0-9]+)$")
RE_VORGANG = re.compile(r"^\d+\s+(\d{2}\.\d{2}\.\d{4} \d{1,2}:\d{2})\s+(.*?)\s+(\d{2}:\d{2}:\d{2})\s+"
                        rf"(\d[\d.]*,\d+)\s*kWh\b.*?{EUR_BETRAG_ENDE}")
RE_NETTO = re.compile(rf"Nettobetrag:\s*(?:EUR\s*({BETRAG})|({BETRAG})\s*EUR)")
RE_TRANSAKTION = re.compile(r"^#\s*(\d+)(?:\s*/\s*Session:\s*(\d+))?$")
RE_ENDE = re.compile(r"^(\d{2}\.\d{2}\.\d{4} \d{1,2}:\d{2})\s*(.*)$")
# Seitenkopf: von „Salzburg AG, … Dokument-Nr.“ bis „Seite …“ (dazwischen Datum, Rechnungsempfänger mit Adresse,
# Vertragskonto) – darf bei Seitenumbruch nicht in Standortangaben landen; danach die Tabellenköpfe
RE_KOPF_ANFANG = re.compile(r"^Salzburg AG, .*(?:Dokument-Nr\.|Belegnummer)")
RE_TABELLENKOPF = re.compile(r"^(Pos\. |Ladenachweis)")


def _kopfzeilen(zeilen: list[str]) -> set[int]:
    """Indizes der Seitenkopf-Zeilen (ohne festen Empfängernamen – der steht je Kunde anders da)."""
    kopf, im_kopf = set(), False
    for i, z in enumerate(zeilen):
        if RE_KOPF_ANFANG.match(z):
            im_kopf = True
        if im_kopf:
            kopf.add(i)
            im_kopf = not z.startswith("Seite ")
    return kopf


def _zeit(text: str) -> datetime:
    return datetime.strptime(text, "%d.%m.%Y %H:%M")


def _datum(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


class SalzburgAgParser(Parser):
    key = "salzburg_ag"
    name = "Salzburg AG (PDF oder CSV)"
    msp_name = "Salzburg AG"
    standardland = "AT"

    def erkennen(self, pfad: Path) -> bool:
        if not ist_pdf(pfad):
            return salzburg_ag_csv.ist_salzburg_ag_csv(pfad)
        text = pdf_text(pfad, nur_erste_seite=True)
        return "Salzburg AG" in text and ("Dokument-Nr" in text or "Belegnummer" in text)

    def parsen(self, pfad: Path) -> Ergebnis:
        if not ist_pdf(pfad) and salzburg_ag_csv.ist_salzburg_ag_csv(pfad):
            return salzburg_ag_csv.parsen(pfad)
        return parsen_text(pdf_text(pfad))


def _dokumente(text: str) -> list[tuple[str, list[str]]]:
    """Zeilen je Dokument-Nr. (Seitenkopf) in Reihenfolge; Deckblätter „Rechnungsübersicht“ entfallen."""
    dokumente: dict[str, list[str]] = {}
    aktuell = None
    for z in (z.strip() for z in text.splitlines()):
        if m := RE_DOKUMENT.search(z):
            aktuell = m.group(1)
            dokumente.setdefault(aktuell, [])
        if aktuell:
            dokumente[aktuell].append(z)
    return [(nr, zeilen) for nr, zeilen in dokumente.items() if not any("Rechnungsübersicht" in z for z in zeilen)]


def parsen_text(text: str) -> Ergebnis:
    ergebnis = Ergebnis(rechnung=Rechnung())
    nummern, netto_gesamt, daten, zeitraeume = [], Decimal(0), [], []

    for nr, zeilen in _dokumente(text):
        dok = "\n".join(zeilen)
        nummern.append(nr)
        land = ((re.search(rf"Rechnung\s+(\S+)\s+{nr}", dok) or [None, ""])[1]
                or (re.search(r"Ladevorg\S*\s+\(([A-Z]{2})\)", dok) or [None, ""])[1])  # älteres Format: „(AT)“
        if m := re.search(r"Datum\s+(\d{2}\.\d{2}\.\d{4})", dok):
            daten.append(_datum(m.group(1)))
        zeitraum = None
        if m := re.search(r"Zeitraum\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", dok):
            zeitraum = (_datum(m.group(1)), _datum(m.group(2)))
            zeitraeume.append(zeitraum)
        if m := RE_NETTO.search(dok):
            netto = decimal_lesen(m.group(1) or m.group(2))
            netto_gesamt += netto
        else:
            netto = None
            ergebnis.warnungen.append(f"Rechnung {nr}: „Nettobetrag“ nicht gefunden – PDF unvollständig?")

        ladekosten_pos: dict[str, Decimal] = defaultdict(Decimal)
        ladekosten_nachweis: dict[str, Decimal] = defaultdict(Decimal)
        bezeichnungen: dict[str, str] = {}
        karte, abschnitt, vorgang = None, "positionen", None
        summe = Decimal(0)

        kopf = _kopfzeilen(zeilen)
        for i, z in enumerate(zeilen):
            if z.startswith("Ladenachweis"):
                abschnitt, karte = "nachweis", None
                continue
            if abschnitt == "positionen":
                if m := RE_KARTE_POS.match(z):
                    karte = m.group(1)
                    bezeichnungen[karte] = m.group(2).strip()
                elif karte and (m := RE_UNTERPOS.match(z)):
                    betrag = decimal_lesen(m["betrag_a"] or m["betrag_b"])
                    summe += betrag
                    if "Ladevorg" in m.group(1):
                        ladekosten_pos[karte] += betrag
                    else:
                        ergebnis.gebuehren.append(Gebuehr(
                            m.group(1), betrag, kartenkennung=karte,
                            kartenbezeichnung=f"Salzburg AG {bezeichnungen.get(karte, karte)}",
                            zeitraum_von=zeitraum[0] if zeitraum else None,
                            zeitraum_bis=zeitraum[1] if zeitraum else None))
                continue

            # Ladenachweis
            if m := RE_KARTE_NACHWEIS.match(z):
                karte, vorgang = m.group(1), None
                continue
            if karte and (m := RE_VORGANG.match(z)):
                start = _zeit(m.group(1))
                betrag = decimal_lesen(m["betrag_a"] or m["betrag_b"])
                ladekosten_nachweis[karte] += betrag
                vorgang = Vorgang(
                    kartenkennung=karte, kennung_typ=KennungTyp.kartennummer, start=start, ende=start,
                    kwh=kwh_runden(decimal_lesen(m.group(4))), kosten_netto=betrag, standort=m.group(2),
                    kartenbezeichnung=f"Salzburg AG {bezeichnungen.get(karte, karte)}",
                    rohdaten={"rechnung": nr, "land": land, "dauer": m.group(3), "zeilen": [z]})
                vorgang.rohdaten["_wartet_auf_ende"] = True
                ergebnis.vorgaenge.append(vorgang)
                continue
            if vorgang is None or i in kopf or RE_TABELLENKOPF.match(z):
                continue
            vorgang.rohdaten["zeilen"].append(z)
            if vorgang.rohdaten.pop("_wartet_auf_ende", False) and (m := RE_ENDE.match(z)):
                vorgang.ende = _zeit(m.group(1))
                if m.group(2):
                    vorgang.standort = f"{vorgang.standort} {m.group(2)}"
            elif m := re.match(r"^EVSE-ID:\s*(\S+)", z):
                vorgang.ladepunkt = m.group(1)
            elif m := RE_TRANSAKTION.match(z):
                vorgang.externe_id = m.group(1)
                if m.group(2):
                    vorgang.rohdaten["session"] = m.group(2)
                vorgang = None  # Transaktionsnummer schließt den Vorgang ab
            else:
                vorgang.standort = f"{vorgang.standort}, {z}"  # Adresszeilen

        for k in set(ladekosten_pos) | set(ladekosten_nachweis):
            if ladekosten_pos[k] != ladekosten_nachweis[k]:
                ergebnis.warnungen.append(f"Rechnung {nr}, Karte {k}: Ladekosten {ladekosten_pos[k]} EUR ≠ "
                                          f"Summe Ladenachweis {ladekosten_nachweis[k]} EUR.")
        if netto is not None and summe != netto:
            ergebnis.warnungen.append(f"Rechnung {nr}: Summe der Positionen {summe} EUR ≠ Nettobetrag {netto} EUR.")

    if not nummern or (not ergebnis.vorgaenge and not ergebnis.gebuehren):
        raise ParserFehler("Keine Salzburg-AG-Rechnung mit Positionen gefunden.")
    for v in ergebnis.vorgaenge:
        if v.ende == v.start:
            ergebnis.warnungen.append(f"Ladevorgang {v.start:%d.%m.%Y %H:%M}: Ladeende nicht gefunden.")
        if not v.externe_id:
            ergebnis.warnungen.append(f"Ladevorgang {v.start:%d.%m.%Y %H:%M}: Transaktionsnummer nicht gefunden.")
        v.standort = (v.standort or "")[:300] or None
    r = ergebnis.rechnung
    r.nummer = ", ".join(nummern)[:100]
    r.datum = max(daten) if daten else None
    r.summe_netto = netto_gesamt
    if zeitraeume:
        r.zeitraum_von, r.zeitraum_bis = min(z[0] for z in zeitraeume), max(z[1] for z in zeitraeume)
    return ergebnis
