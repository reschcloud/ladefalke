"""Parser: EnBW mobility+ (PDF oder CSV-Export; CSV siehe enbw_csv.py).

Eine Rechnung je Land und Monat. „Ihre Einzelladevorgänge im Detail“ liefert je Vorgang
Ladestart/-ende, Ladepunkt-ID, Transaktions-ID (externe ID), Ladekarte und den Gesamtbetrag netto;
die genaue Energiemenge steht unter „Details: Ladekosten für 37,003 kWh …“.
Grundgebühren stehen je Karte in der „Übersicht Monatliche Grundgebühr“. Beträge netto,
ausländische Umsatzsteuer wird ignoriert.
Firmen- und Privatkonto haben dasselbe Format; Unterschiede: Privatrechnungen beschriften die Zeiten mit „Start:“,
„Ende:“, „Dauer:“ (statt „Ladestart:“ usw.), wiederholen den Tabellenkopf je Vorgang und haben meist keine
Grundgebühr. Abgebrochene Vorgänge ohne Energie und Kosten (0 kWh, 0 €) werden nicht übernommen (Hinweis).
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.formatierung import decimal_lesen
from app.parsers import enbw_csv
from app.models import KennungTyp
from app.parsers.basis import (Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang, ist_pdf, kwh_runden,
                               pdf_text, stromart_normieren)

MONATE = {m: i for i, m in enumerate(["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                                      "September", "Oktober", "November", "Dezember"], 1)}
BETRAG = r"-?[\d.]+,\d{2}"
RE_KOPF = re.compile(r"^(\d+)\s+(\d{2}\.\d{2}\.\d{4})\s+(.*)$")
RE_GEBUEHR = re.compile(rf"^\d+\s+(?!\d{{2}}\.\d{{2}}\.\d{{4}})(.*?)\s({BETRAG})\s*€\s+({BETRAG})\s*€\s*$")
RE_KARTE = re.compile(r"\(([A-Z0-9]+(?:-[A-Z0-9]+)+)\)")
RE_ZEIT = r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(?::\d{2})?)"


def _zeit(text: str) -> datetime:
    return datetime.strptime(text, "%d.%m.%Y %H:%M:%S" if text.count(":") == 2 else "%d.%m.%Y %H:%M")


class EnbwParser(Parser):
    key = "enbw"
    name = "EnBW mobility+ (PDF oder CSV)"
    msp_name = "EnBW mobility+"

    def erkennen(self, pfad: Path) -> bool:
        if enbw_csv.ist_enbw_csv(pfad):
            return True
        return ist_pdf(pfad) and "EnBW Energie Baden-Württemberg" in pdf_text(pfad, nur_erste_seite=True)

    def parsen(self, pfad: Path) -> Ergebnis:
        if not ist_pdf(pfad) and enbw_csv.ist_enbw_csv(pfad):
            return enbw_csv.parsen(pfad)
        return parsen_text(pdf_text(pfad))

    def parsen_datei(self, pfad: Path, dateiname: str | None = None) -> Ergebnis:
        """CSV: Abrechnungsmonat aus dem Dateinamen; PDF wie `parsen`."""
        if not ist_pdf(pfad) and enbw_csv.ist_enbw_csv(pfad):
            return enbw_csv.parsen(pfad, dateiname)
        return self.parsen(pfad)


def parsen_text(text: str) -> Ergebnis:
    r = Rechnung()
    warnungen: list[str] = []
    # Nummer „180 000 000 001“ steht je nach PDF-Version in der Folgezeile oder am Ende einer anderen Zeile
    if m := re.search(r"(?<!\d)(?<!\d )(\d{3} \d{3} \d{3} \d{3})(?!\d)", text):
        r.nummer = m.group(1).replace(" ", "")
    land = ""
    if m := re.search(r"für\s+(.+?)\s+im\s+(\S+)\s+(\d{4})", text):
        land = m.group(1)
        if m.group(2) in MONATE:
            r.zeitraum_von = date(int(m.group(3)), MONATE[m.group(2)], 1)
            r.zeitraum_bis = (r.zeitraum_von.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if m := re.search(r"Rechnung\s+(\d{1,2})\.\s*(\S+)\s+(\d{4})", text):
        if m.group(2) in MONATE:
            r.datum = date(int(m.group(3)), MONATE[m.group(2)], int(m.group(1)))
    if m := re.search(rf"^Summe\s*\d?\s+({BETRAG})\s*€", text, flags=re.M):
        r.summe_netto = decimal_lesen(m.group(1))
    else:
        warnungen.append("Nettosumme nicht gefunden – das PDF wirkt unvollständig.")

    zeilen = [z.strip() for z in text.splitlines()]
    ergebnis = Ergebnis(rechnung=r, warnungen=warnungen)

    # Grundgebühren je Karte (Übersicht); Zeilen der Einzelladevorgänge beginnen mit einem Datum
    for z in zeilen:
        if (m := RE_GEBUEHR.match(z)) and (karte := RE_KARTE.search(m.group(1))):
            ergebnis.gebuehren.append(Gebuehr(
                "Monatliche Grundgebühr" if "Grundgebühr" in text else m.group(1), decimal_lesen(m.group(2)),
                kartenkennung=karte.group(1), kartenbezeichnung=f"EnBW {karte.group(1)}",
                zeitraum_von=r.zeitraum_von, zeitraum_bis=r.zeitraum_bis))

    # Einzelladevorgänge: Blöcke ab „<Nr> <Datum> …“
    bloecke, block = [], None
    for z in zeilen:
        if RE_KOPF.match(z) and "€" in z:
            block = [z]
            bloecke.append(block)
        elif z.startswith(("Nr Datum", "Ihre bestellten", "Bestelldatum", "Übersicht")):
            block = None
        elif block is not None:
            block.append(z)
    for b in bloecke:
        inhalt = "\n".join(b)
        gesamt = re.search(rf"Gesamt\s+({BETRAG})\s*€", inhalt)
        start = re.search(rf"(?:Ladestart|Start):\s*{RE_ZEIT}", inhalt)  # Firmen- bzw. Privatkonto
        ende = re.search(rf"(?:Ladeende|Ende):\s*{RE_ZEIT}", inhalt)
        if not (gesamt and start):
            warnungen.append(f"Ladevorgang ohne Gesamtbetrag oder Ladestart übersprungen: {b[0]}")
            continue
        kwh_details = [decimal_lesen(k) for zeile in b if zeile.startswith("Details")
                       for k in re.findall(r"(\d[\d.]*,\d+)\s*kWh\b", zeile)]
        kwh_menge = [decimal_lesen(k) for zeile in b if not zeile.startswith("Details")
                     for k in re.findall(r"(\d[\d.]*,\d+)\s*kWh\b", zeile)]
        kwh = kwh_runden(sum(kwh_details or kwh_menge, Decimal(0)))
        if kwh == 0 and decimal_lesen(gesamt.group(1)) == 0:
            warnungen.append(f"Abgebrochener Ladevorgang vom {start.group(1)} ohne Energie und Kosten nicht übernommen.")
            continue
        karte = re.search(r"Ladekarte\s*\(([^)]+)\)", inhalt)
        kopf = RE_KOPF.match(b[0])
        strasse = re.split(r"\s+(?:AC|DC)\s+", kopf.group(3))[0]
        gesamtzeile = next((z for z in b[1:] if re.search(rf"\bGesamt\s+{BETRAG}", z)), "")
        ort = re.split(r"\s*\bGesamt\s+", gesamtzeile)[0]
        ergebnis.vorgaenge.append(Vorgang(
            kartenkennung=karte.group(1) if karte else None, kennung_typ=KennungTyp.kartennummer,
            start=_zeit(start.group(1)), ende=_zeit(ende.group(1)) if ende else _zeit(start.group(1)),
            kwh=kwh, kosten_netto=decimal_lesen(gesamt.group(1)),
            externe_id=(re.search(r"Transaktions-ID:\s*(\S+)", inhalt) or [None, None])[1],
            ladepunkt=(re.search(r"Ladepunkt-ID:\s*(\S+)", inhalt) or [None, None])[1],
            standort=", ".join(x for x in (strasse, ort) if x) or None,
            kartenbezeichnung=f"EnBW {karte.group(1)}" if karte else None,
            stromart=stromart_normieren((re.search(r"\s(AC|DC)\s", kopf.group(3)) or [None, None])[1]),
            rohdaten={"rechnung": r.nummer, "land": land, "zeilen": b}))
        if not karte:
            warnungen.append(f"Ladevorgang {start.group(1)}: Ladekarte nicht gefunden.")

    summe = sum((v.kosten_netto for v in ergebnis.vorgaenge), Decimal(0)) + \
        sum((g.betrag_netto for g in ergebnis.gebuehren), Decimal(0))
    if r.summe_netto is not None:
        rest = r.summe_netto - summe
        if rest != 0:  # z. B. Ladekartenbestellung ohne Kartenbezug
            ergebnis.gebuehren.append(Gebuehr("Sonstige Positionen ohne Kartenbezug", rest,
                                              zeitraum_von=r.zeitraum_von, zeitraum_bis=r.zeitraum_bis))
            warnungen.append(f"{rest} EUR ohne Kartenbezug als Gebühr erfasst – bitte prüfen.")
    if not ergebnis.vorgaenge and not ergebnis.gebuehren:
        raise ParserFehler("Keine Ladevorgänge oder Gebühren gefunden – ist das eine EnBW-Rechnung?")
    return ergebnis
