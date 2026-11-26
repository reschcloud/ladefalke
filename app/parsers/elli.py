"""Parser: Elli / Cupra Charging – „Übersicht über die Ladevorgänge“ (PDF, keine Rechnung).

- Text über PDFium: pdfplumber verliert bei diesem PDF alle Texte in normaler Schrift.
- Je Ladevorgang: Datum, Betreiber, Adresse, Ladepunkt-ID, Dauer, kWh, Bruttopreis.
  Seit etwa Anfang 2026 mit Uhrzeit des Ladebeginns („27.01.2026, 06:58:29“) → Start = Datum und Uhrzeit.
  Ältere Übersichten nur mit Datum → Start = Datum 00:00, Ende = Start + Dauer (nur der Tag stimmt).
- Monat im Kopf auch österreichisch („Jänner 2026“).
- Karte = Name des Kontoinhabers (das Dokument enthält keine Kartennummer).
- Netto: Bruttopreis je Vorgang mit dem USt-Satz seines Landes (aus der Ladepunkt-ID), centgenau
  abgestimmt auf Summe − ausgewiesene Mehrwertsteuer. Die Beschriftung „EUR(19%)“ im Dokument ist
  ungenau; der ausgewiesene Betrag entspricht der Rechnung je Land.
- Übersichtszahlen mit Dezimalpunkt („86.82 EUR“), Tabelle mit Dezimalkomma („36,05 EUR“).

Älteres Format (bis etwa 2023, „Easy Charging“, Volkswagen Group Charging GmbH): eine echte Rechnung je Land mit
Rechnungsnummer, Zeitraum, Belegdatum und „Gesamtbetrag netto“; in der Anlage „Übersicht der Ladevorgänge“ je
Vorgang Beginn mit Uhrzeit, EVSE-ID mit Station, AC/DC, eine eindeutige ID (→ externe ID), kWh, Dauer und
Bruttopreis. Karte = Name aus der Anrede („Guten Tag …,“), wie im neuen Format der Kontoinhaber. Eine
„Grundgebühr <Tarif>“ mit eigenem Zeitraum wird zur Gebühr der Karte; Rechnungen nur mit Grundgebühr (ohne
Ladevorgänge und ohne Anlage) kommen ebenfalls vor.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.models import KennungTyp
from app.services.evse import land_aus_text
from app.parsers.basis import (Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang, auf_summe_verteilen, cent,
                               ist_pdf, kwh_runden, pdf_text_pdfium, zahl_flexibel)

MONATE = {m: i for i, m in enumerate(["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                                      "September", "Oktober", "November", "Dezember"], 1)} | {"Jänner": 1}
# USt-Sätze für Ladungen im Ausland (Prozent); fehlt ein Land, gilt der Satz laut Dokument (mit Warnung)
UST_JE_LAND = {"AT": 20, "DE": 19, "IT": 22, "SI": 22, "HU": 27, "CZ": 21, "SK": 23, "HR": 25, "CH": Decimal("8.1"),
               "FR": 20, "NL": 21, "BE": 21, "LU": 17, "DK": 25, "PL": 23, "ES": 21}

ZAHL = r"-?\d[\d.,]*"
RE_DATUM = re.compile(r"^(\d{2}\.\d{2}\.\d{4})(?:,\s*(\d{1,2}:\d{2}:\d{2}))?$")  # Uhrzeit nur im neueren Format
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
        return ("Elli Mobility GmbH" in text and "Übersicht über die Ladevorgänge" in text) or _ist_rechnung(text)

    def parsen(self, pfad: Path) -> Ergebnis:
        return parsen_text(pdf_text_pdfium(pfad))


def parsen_text(text: str) -> Ergebnis:
    if _ist_rechnung(text):
        return _rechnung_parsen(text)
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
        if m := RE_DATUM.match(z):
            block = {"datum": m.group(1), "uhrzeit": m.group(2), "zeilen": []}
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
        start = (datetime.strptime(f"{b['datum']} {b['uhrzeit']}", "%d.%m.%Y %H:%M:%S") if b["uhrzeit"]
                 else datetime.strptime(b["datum"], "%d.%m.%Y"))
        dauer = timedelta(hours=int(m.group(1)), minutes=int(m.group(2)), seconds=int(m.group(3)))
        roh_netto.append(brutto / (1 + Decimal(satz) / 100))
        ergebnis.vorgaenge.append(Vorgang(
            kartenkennung=inhaber or None, kennung_typ=KennungTyp.kartenlabel, start=start, ende=start + dauer,
            kwh=kwh_runden(zahl_flexibel(m.group(4))), kosten_netto=None,
            standort=", ".join(x for x in (betreiber, adresse) if x) or None, ladepunkt=ladepunkt,
            kartenbezeichnung=f"Elli {inhaber}".strip(),
            rohdaten={"datum": b["datum"], "betreiber": betreiber, "adresse": adresse, "land": land,
                      "ust_satz": str(satz), "brutto": str(brutto), "dauer": f"{m.group(1)}:{m.group(2)}:{m.group(3)}",
                      "uhrzeit_unbekannt": b["uhrzeit"] is None}))

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
    _ust_aus_brutto(ergebnis.vorgaenge)

    # Kontrollen gegen die echten Textwerte des Dokuments
    brutto_summe = sum((Decimal(v.rohdaten["brutto"]) for v in ergebnis.vorgaenge), Decimal(0))
    if summe is not None and brutto_summe != summe:
        warnungen.append(f"Summe der Bruttopreise {brutto_summe} EUR ≠ Summe laut Dokument {summe} EUR.")
    if m := re.search(r"Ladevorgänge insgesamt\s+(\d+)", text):
        if int(m.group(1)) != len(ergebnis.vorgaenge):
            warnungen.append(f"{len(ergebnis.vorgaenge)} Ladevorgänge gelesen, laut Übersicht {m.group(1)}.")
    if m := re.search(rf"Geladene Energie\s+insgesamt\s+({ZAHL})\s*kWh", text):
        # Übersicht auf 2 Stellen gerundet („51.54“), Vorgänge mit 3 (Summe 51,541)
        if not _gleich_gerundet(m.group(1), sum((v.kwh for v in ergebnis.vorgaenge), Decimal(0))):
            warnungen.append(f"Summe der kWh passt nicht zur Übersicht ({m.group(1)} kWh).")
    return ergebnis


def _ust_aus_brutto(vorgaenge: list[Vorgang]) -> None:
    """USt. je Vorgang = brutto − netto, Satz und Land wie zur Netto-Berechnung (Land des Ladepunkts)."""
    for v in vorgaenge:
        if v.kosten_netto is not None and "brutto" in v.rohdaten:
            v.ust_betrag = Decimal(v.rohdaten["brutto"]) - v.kosten_netto
            v.ust_satz = Decimal(v.rohdaten["ust_satz"])
            v.ust_land = v.rohdaten.get("land") or None


# ------------------------------------------------------------------ älteres Format: Rechnung „Easy Charging“

RE_VORGANG_START = re.compile(r"^(\d{2}\.\d{2}\.\d{4}),\s*(\d{1,2}:\d{2}:\d{2})\b")
RE_VORGANG_WERTE = re.compile(rf"({ZAHL})\s+(\d{{1,2}}):(\d{{2}}):(\d{{2}})\s+({ZAHL})\s*EUR")
RE_EVSE = re.compile(r"EVSEID:\s*(\S+)\s*\((.*)\)\s*(AC|DC)?")
RE_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
RE_KOPFDATEN = re.compile(r"^(\d+)\s+(\d+)\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}\.\d{2}\.\d{4})\s*$",
                         re.M)
RE_BLOCKENDE = re.compile(r"^(Volkswagen Group Charging|Gesamtbetrag|Summe Ladevorgänge|\* Die Abrechnung)")
RE_GRUNDGEBUEHR = re.compile(rf"(Grundgebühr[^\n]*?)\s*\n?\s*(\d{{2}}\.\d{{2}}\.\d{{4}})\s*-\s*(\d{{2}}\.\d{{2}}\.\d{{4}})"
                             rf"\s+({ZAHL})\s*EUR\s+({ZAHL})\s*%\s+({ZAHL})\s*EUR")
RE_LAND_POSITION = re.compile(rf"Ladevorgänge in\s+\S+\s+(\d+)\s+({ZAHL})\s*kWh\s+({ZAHL})\s*EUR")


def _ist_rechnung(text: str) -> bool:
    return ("Easy Charging" in text and "Rechnungsnummer" in text and "Kundennummer" in text
            and ("Übersicht der Ladevorgänge" in text or "Grundgebühr" in text))


def _gleich_gerundet(dokument: str, wert: Decimal) -> bool:
    """Vergleich mit so vielen Nachkommastellen, wie das Dokument angibt („48,69“ ↔ 48,693)."""
    stellen = len(re.split(r"[.,]", dokument)[1]) if re.search(r"[.,]", dokument) else 0
    return zahl_flexibel(dokument) == wert.quantize(Decimal(1).scaleb(-stellen))


def _datum(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def _rechnung_parsen(text: str) -> Ergebnis:
    text = text.replace("\xa0", " ")
    warnungen: list[str] = []
    r = Rechnung()
    if m := RE_KOPFDATEN.search(text):
        r.nummer, r.zeitraum_von, r.zeitraum_bis, r.datum = m.group(2), _datum(m.group(3)), _datum(m.group(4)), \
            _datum(m.group(5))
    else:
        warnungen.append("Rechnungsnummer, Zeitraum und Belegdatum nicht gefunden.")
    if m := re.search(rf"Gesamtbetrag netto\s+({ZAHL})\s*EUR", text):
        r.summe_netto = zahl_flexibel(m.group(1))
    else:
        warnungen.append("„Gesamtbetrag netto“ nicht gefunden – das PDF wirkt unvollständig.")
    brutto_gesamt = zahl_flexibel(m.group(1)) if (m := re.search(rf"Gesamtbetrag brutto\s+({ZAHL})\s*EUR", text)) else None
    satz_dokument = zahl_flexibel(m.group(1)) if (m := re.search(r"USt\.\s*(\d+(?:,\d+)?)\s*%", text)) else Decimal(20)
    land_rechnung = (re.search(r"Rechnung für\s+(\S+)", text) or [None, None])[1]
    inhaber = (re.search(r"Guten Tag\s+([^,\n]+),", text) or [None, ""])[1].strip()

    # Vorgänge: Block ab „TT.MM.JJJJ, hh:mm:ss“ bis zum nächsten Beginn bzw. Seitenfuß (Reihenfolge im Block egal)
    bloecke, block = [], None
    for z in (z.strip() for z in text.splitlines()):
        if m := RE_VORGANG_START.match(z):
            block = {"start": datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d.%m.%Y %H:%M:%S"),
                     "text": z[m.end():].strip()}
            bloecke.append(block)
        elif block is not None:
            if RE_BLOCKENDE.match(z):
                block = None
            else:
                block["text"] += " " + z

    ergebnis = Ergebnis(rechnung=r, warnungen=warnungen)
    for m in RE_GRUNDGEBUEHR.finditer(text):  # „Grundgebühr Power“ mit Zeitraum, netto, USt-Satz, brutto
        ergebnis.gebuehren.append(Gebuehr(
            re.sub(r"\s+", " ", m.group(1)).strip(), zahl_flexibel(m.group(4)), kartenkennung=inhaber or None,
            zeitraum_von=_datum(m.group(2)), zeitraum_bis=_datum(m.group(3)), kennung_typ=KennungTyp.kartenlabel,
            kartenbezeichnung=f"Elli {inhaber}".strip(),
            ust_betrag=zahl_flexibel(m.group(6)) - zahl_flexibel(m.group(4)), ust_satz=zahl_flexibel(m.group(5)),
            ust_land=land_aus_text(land_rechnung)))
    gebuehren_netto = sum((g.betrag_netto for g in ergebnis.gebuehren), Decimal(0))
    roh_netto = []
    for b in bloecke:
        werte = RE_VORGANG_WERTE.search(b["text"])
        if werte is None:
            warnungen.append(f"Ladevorgang {b['start']:%d.%m.%Y %H:%M}: kWh, Dauer oder Preis nicht gefunden – übersprungen.")
            continue
        evse = RE_EVSE.search(RE_VORGANG_WERTE.sub("", b["text"]))
        ladepunkt = evse.group(1) if evse else None
        station = re.sub(r"\s+", " ", evse.group(2)).strip() if evse else None
        land = _land(ladepunkt, None) or land_aus_text(land_rechnung)
        satz = UST_JE_LAND.get(land) if land else None
        if satz is None:
            warnungen.append(f"Ladevorgang {b['start']:%d.%m.%Y %H:%M}: USt-Satz für Land {land or '?'} unbekannt – "
                             f"{satz_dokument} % laut Dokument verwendet.")
            satz = satz_dokument
        brutto = zahl_flexibel(werte.group(5))
        dauer = timedelta(hours=int(werte.group(2)), minutes=int(werte.group(3)), seconds=int(werte.group(4)))
        roh_netto.append(brutto / (1 + Decimal(satz) / 100))
        kennung = RE_ID.search(b["text"])
        ergebnis.vorgaenge.append(Vorgang(
            kartenkennung=inhaber or None, kennung_typ=KennungTyp.kartenlabel, start=b["start"],
            ende=b["start"] + dauer, kwh=kwh_runden(zahl_flexibel(werte.group(1))), kosten_netto=None,
            standort=station, ladepunkt=ladepunkt, externe_id=kennung.group(0) if kennung else None,
            stromart=evse.group(3) if evse and evse.group(3) else None,
            kartenbezeichnung=f"Elli {inhaber}".strip(),
            rohdaten={"rechnung": r.nummer, "land": land, "ust_satz": str(satz), "brutto": str(brutto),
                      "dauer": f"{werte.group(2)}:{werte.group(3)}:{werte.group(4)}"}))

    if not ergebnis.vorgaenge and not ergebnis.gebuehren:
        raise ParserFehler("Weder Ladevorgänge noch Grundgebühr gefunden – ist das eine Easy-Charging-Rechnung?")

    # Netto der Ladevorgänge: Positionen „Ladevorgänge in …“, sonst Gesamtbetrag netto ohne Grundgebühren;
    # darauf centgenau verteilen
    positionen = RE_LAND_POSITION.findall(text)
    ziel = (sum((zahl_flexibel(p[2]) for p in positionen), Decimal(0)) if positionen
            else r.summe_netto - gebuehren_netto if r.summe_netto is not None else None)
    netto = auf_summe_verteilen(roh_netto, ziel) if ziel is not None and roh_netto else None
    if netto is None and roh_netto:
        if ziel is not None:
            warnungen.append(f"Netto je Land ({cent(sum(roh_netto, Decimal(0)))} EUR) passt nicht zu den Ladevorgängen "
                             f"netto laut Rechnung ({ziel} EUR).")
        netto = [cent(n) for n in roh_netto]
    for v, n in zip(ergebnis.vorgaenge, netto or []):
        v.kosten_netto = n
    _ust_aus_brutto(ergebnis.vorgaenge)
    if r.summe_netto is None:
        r.summe_netto = sum((v.kosten_netto for v in ergebnis.vorgaenge), gebuehren_netto)

    # Kontrollen gegen die Rechnung: Bruttosumme der Ladevorgänge, Anzahl und kWh der Positionen „Ladevorgänge in …“
    brutto_summe = sum((Decimal(v.rohdaten["brutto"]) for v in ergebnis.vorgaenge), Decimal(0))
    if m := re.search(rf"Summe Ladevorgänge\s+({ZAHL})\s*EUR", text):
        brutto_soll, wozu = zahl_flexibel(m.group(1)), "Summe Ladevorgänge"
    elif brutto_gesamt is not None and not ergebnis.gebuehren:
        brutto_soll, wozu = brutto_gesamt, "Gesamtbetrag brutto"
    else:
        brutto_soll = None
    if brutto_soll is not None and brutto_summe != brutto_soll:
        warnungen.append(f"Summe der Bruttopreise {brutto_summe} EUR ≠ {wozu} {brutto_soll} EUR.")
    if positionen:
        anzahl = sum(int(p[0]) for p in positionen)
        if anzahl != len(ergebnis.vorgaenge):
            warnungen.append(f"{len(ergebnis.vorgaenge)} Ladevorgänge gelesen, laut Rechnung {anzahl}.")
        kwh = sum((v.kwh for v in ergebnis.vorgaenge), Decimal(0))
        if len(positionen) == 1 and not _gleich_gerundet(positionen[0][1], kwh):
            warnungen.append(f"Summe der kWh passt nicht zur Rechnung ({positionen[0][1]} kWh).")
    summe = sum((v.kosten_netto or Decimal(0) for v in ergebnis.vorgaenge), gebuehren_netto)
    if summe != r.summe_netto:
        warnungen.append(f"Ladevorgänge und Grundgebühr ({summe} EUR) ≠ Gesamtbetrag netto ({r.summe_netto} EUR).")
    return ergebnis
