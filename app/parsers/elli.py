"""Parser: Elli / Cupra Charging – „Übersicht über die Ladevorgänge“ (PDF, keine Rechnung).

- Text über PDFium: pdfplumber verliert bei diesem PDF alle Texte in normaler Schrift.
- Je Ladevorgang: Datum, Betreiber, Adresse, Ladepunkt-ID, Dauer, kWh, Bruttopreis.
  Keine Uhrzeit im Dokument → Start = Datum 00:00, Ende = Start + Dauer (Ladeende stimmt; der Ladebeginn ist nur der Tag).
- Karte = Name des Kontoinhabers (das Dokument enthält keine Kartennummer).
- Netto: Bruttopreis je Vorgang mit dem USt-Satz seines Landes (aus der Ladepunkt-ID), centgenau
  abgestimmt auf Summe − ausgewiesene Mehrwertsteuer. Die Beschriftung „EUR(19%)“ im Dokument ist
  ungenau; der ausgewiesene Betrag entspricht der Rechnung je Land.
- Übersichtszahlen mit Dezimalpunkt („86.82 EUR“), Tabelle mit Dezimalkomma („36,05 EUR“).
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.models import KennungTyp
from app.parsers.basis import (Ergebnis, Parser, ParserFehler, Rechnung, Vorgang, auf_summe_verteilen, cent,
                               ist_pdf, kwh_runden, pdf_text_pdfium, zahl_flexibel)

MONATE = {m: i for i, m in enumerate(["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                                      "September", "Oktober", "November", "Dezember"], 1)}
# USt-Sätze für Ladungen im Ausland (Prozent); fehlt ein Land, gilt der Satz laut Dokument (mit Warnung)
UST_JE_LAND = {"AT": 20, "DE": 19, "IT": 22, "SI": 22, "HU": 27, "CZ": 21, "SK": 23, "HR": 25, "CH": Decimal("8.1"),
               "FR": 20, "NL": 21, "BE": 21, "LU": 17, "DK": 25, "PL": 23, "ES": 21}

ZAHL = r"-?\d[\d.,]*"
RE_DATUM = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
RE_WERTE = re.compile(rf"^(\d{{1,2}}):(\d{{2}}):(\d{{2}})\s+({ZAHL})\s+({ZAHL})\s+([A-Z]{{3}})$")
RE_LADEPUNKT = re.compile(r"^([A-Z]{2})\*[A-Z0-9]{3}\*\S+$")
RE_MWST = re.compile(rf"EUR\((\d+(?:[.,]\d+)?)%\)\s+({ZAHL})\s*EUR")


def _land(ladepunkt: str | None, adresse: str | None) -> str | None:
    if ladepunkt and (m := RE_LADEPUNKT.match(ladepunkt)):
        return m.group(1)
    if adresse and (m := re.search(r",\s*([A-Z]{2})$", adresse)):
        return m.group(1)
    return None


class ElliParser(Parser):
    key = "elli"
    name = "Elli / Cupra Charging (PDF)"
    msp_name = "Elli / Cupra Charging"

    def erkennen(self, pfad: Path) -> bool:
        if not ist_pdf(pfad):
            return False
        text = pdf_text_pdfium(pfad)
        return "Elli Mobility GmbH" in text and "Übersicht über die Ladevorgänge" in text

    def parsen(self, pfad: Path) -> Ergebnis:
        return parsen_text(pdf_text_pdfium(pfad))


def parsen_text(text: str) -> Ergebnis:
    zeilen = [z.strip().replace(" ", "") for z in text.splitlines()]
    zeilen = [z for z in zeilen if z]
    warnungen: list[str] = []
    r = Rechnung()

    # Kontoinhaber:in = erste Zeile (vor der Firmenanschrift) → Kennung der Karte
    inhaber = zeilen[0] if zeilen else ""
    if m := re.search(r"Übersicht über die Ladevorgänge\s*\|\s*(\S+)\s+(\d{4})", text):
        if m.group(1) in MONATE:
            r.zeitraum_von = date(int(m.group(2)), MONATE[m.group(1)], 1)
            r.zeitraum_bis = (r.zeitraum_von.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    summe = zahl_flexibel(m.group(1)) if (m := re.search(rf"^Summe\s+({ZAHL})\s*EUR", text, re.M)) else None
    mwst_zeilen = RE_MWST.findall(text)
    mwst = sum((zahl_flexibel(b) for _, b in mwst_zeilen), Decimal(0)) if mwst_zeilen else None
    satz_dokument = zahl_flexibel(mwst_zeilen[0][0]) if mwst_zeilen else Decimal(19)
    if summe is None:
        warnungen.append("„Summe“ nicht gefunden – das PDF wirkt unvollständig.")
    elif mwst is not None:
        r.summe_netto = summe - mwst

    # Ladevorgänge: Block ab Datumszeile bis zur Wertezeile (Dauer kWh Preis Währung)
    rohe = []
    block = None
    for z in zeilen:
        if RE_DATUM.match(z):
            block = {"datum": z, "zeilen": []}
            continue
        if block is None:
            continue
        if m := RE_WERTE.match(z):
            block["werte"] = m
            rohe.append(block)
            block = None
        else:
            block["zeilen"].append(z)

    ergebnis = Ergebnis(rechnung=r, warnungen=warnungen)
    roh_netto = []
    for b in rohe:
        m = b["werte"]
        if m.group(6) != "EUR":
            warnungen.append(f"Ladevorgang {b['datum']}: Preis in {m.group(6)} – übersprungen.")
            continue
        ladepunkt = next((z for z in b["zeilen"] if RE_LADEPUNKT.match(z)), None)
        uebrige = [z for z in b["zeilen"] if z != ladepunkt]
        betreiber, adresse = (uebrige + [None, None])[:2]
        land = _land(ladepunkt, adresse)
        satz = UST_JE_LAND.get(land) if land else None
        if satz is None:
            warnungen.append(f"Ladevorgang {b['datum']}: USt-Satz für Land {land or '?'} unbekannt – "
                             f"{satz_dokument} % laut Dokument verwendet.")
            satz = satz_dokument
        brutto = zahl_flexibel(m.group(5))
        start = datetime.strptime(b["datum"], "%d.%m.%Y")
        dauer = timedelta(hours=int(m.group(1)), minutes=int(m.group(2)), seconds=int(m.group(3)))
        roh_netto.append(brutto / (1 + Decimal(satz) / 100))
        ergebnis.vorgaenge.append(Vorgang(
            kartenkennung=inhaber or None, kennung_typ=KennungTyp.kartenlabel, start=start, ende=start + dauer,
            kwh=kwh_runden(zahl_flexibel(m.group(4))), kosten_netto=None,
            standort=", ".join(x for x in (betreiber, adresse) if x) or None, ladepunkt=ladepunkt,
            kartenbezeichnung=f"Elli {inhaber}".strip(),
            rohdaten={"datum": b["datum"], "betreiber": betreiber, "adresse": adresse, "land": land,
                      "ust_satz": str(satz), "brutto": str(brutto), "dauer": f"{m.group(1)}:{m.group(2)}:{m.group(3)}",
                      "uhrzeit_unbekannt": True}))

    if not ergebnis.vorgaenge:
        raise ParserFehler("Keine Ladevorgänge gefunden – ist das eine Elli-Übersicht?")

    # Netto centgenau auf Summe − ausgewiesene USt abstimmen
    netto = auf_summe_verteilen(roh_netto, r.summe_netto) if r.summe_netto is not None else None
    if netto is None:
        if r.summe_netto is not None:
            warnungen.append(f"Netto je Land ({cent(sum(roh_netto, Decimal(0)))} EUR) passt nicht zu Summe − "
                             f"Mehrwertsteuer laut Dokument ({r.summe_netto} EUR).")
        netto = [cent(n) for n in roh_netto]
        r.summe_netto = sum(netto, Decimal(0)) if r.summe_netto is None else r.summe_netto
    for v, n in zip(ergebnis.vorgaenge, netto):
        v.kosten_netto = n

    # Kontrollen gegen die echten Textwerte des Dokuments
    brutto_summe = sum((Decimal(v.rohdaten["brutto"]) for v in ergebnis.vorgaenge), Decimal(0))
    if summe is not None and brutto_summe != summe:
        warnungen.append(f"Summe der Bruttopreise {brutto_summe} EUR ≠ Summe laut Dokument {summe} EUR.")
    if m := re.search(rf"Ladevorgänge insgesamt\s+(\d+)", text):
        if int(m.group(1)) != len(ergebnis.vorgaenge):
            warnungen.append(f"{len(ergebnis.vorgaenge)} Ladevorgänge gelesen, laut Übersicht {m.group(1)}.")
    if m := re.search(rf"Geladene Energie\s+insgesamt\s+({ZAHL})\s*kWh", text):
        if kwh_runden(zahl_flexibel(m.group(1))) != sum((v.kwh for v in ergebnis.vorgaenge), Decimal(0)):
            warnungen.append(f"Summe der kWh passt nicht zur Übersicht ({m.group(1)} kWh).")
    return ergebnis
