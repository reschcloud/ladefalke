"""Parser: DCS / BMW Charging (Digital Charging Solutions), PDF.

Eine PDF enthält je Land eine Teilrechnung (eigene Rechnungsnummer, Seitenmarke
„DocInfo>>…-INV_<Nr>-0-0<<DocInfo“), ggf. mit vorangestellter „Rechnungsübersicht“.
Je Teilrechnung: Positionen je Vertrag (Grundgebühr, IONITY Plus, „Summe Ladevorgänge“),
Netto-Gesamtbetrag und die „Übersicht der Ladevorgänge“ mit Einzelvorgängen.

- Karte = Vertragsnummer (AT*DCS*…): Roaming-Rechnungen nennen keine Kartennummer.
- Uhrzeiten: heißt die Spalte „Datum/Uhrzeit (UTC)“, stehen sie in UTC und werden in Europe/Vienna umgerechnet;
  ohne „(UTC)“ (neueres Format, Rechnungen ab Ende 2025) sind sie bereits Ortszeit. Ende = Start + Dauer.
- Beträge sind netto; die 4-stelligen Einzelbeträge werden centgenau so verteilt, dass
  sie je Teilrechnung zur Nettosumme (abzüglich Gebühren) passen.
- USt. je Teilrechnung: „+ 27% MwSt. HU … 8,13 EUR“ (Satz, Land, Betrag in EUR – auf die Posten verteilt) bzw.
  „Steuerschuldnerschaft des Leistungsempfängers“ (Reverse Charge).
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.formatierung import decimal_lesen
from app.models import KennungTyp
from app.parsers.basis import (REVERSE_CHARGE, Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang,
                               auf_summe_verteilen, cent, ist_pdf, kwh_runden, pdf_text, stromart_normieren, ust_setzen)

WIEN = ZoneInfo("Europe/Vienna")
ZAHL = r"-?\d[\d.]*,\d+"
RE_DOCINFO = re.compile(r"DocInfo>>.*?INV_([A-Z]{3}\d+)-")
RE_VERTRAG = re.compile(r"^([A-Z]{2}\*[A-Z0-9]{3}\*[A-Z0-9]+\*[A-Z0-9])\b\s*(.*)$")
RE_KARTE = re.compile(r"\b[A-Z]{2}-[A-Z]-\d+-\d\b")
RE_EUR = re.compile(rf"({ZAHL})\s*EUR\b")
RE_DATUM = re.compile(r"\d{2}\.\d{2}\.\d{4}")
KWH = r"\d+(?:[.,]\d+)*"  # „12,340“ (AT) oder „15.757“ (DE-Teilrechnung mit Dezimalpunkt)
RE_VORGANG = re.compile(rf"^(\d{{2}}\.\d{{2}}\.\d{{4}}) (\d{{2}}:\d{{2}})h (.*?)\s*({KWH}) kWh (\S+)\s*(.*)$")
RE_VORGANG_ANFANG = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}h ")
RE_DAUER = re.compile(r"(\d+):(\d{2}) hh:mm")
RE_ORT_ENDE = re.compile(r"\d+:\d{2} hh:mm|\(kWh\)")  # Ort endet vor der Dauer bzw. der Einheit des Produkts
RE_ID = re.compile(r"[A-Z0-9]+")


def kwh_lesen(text: str) -> Decimal:
    """kWh-Angabe mit Dezimalkomma („12,340“) oder – in manchen Länderrechnungen – Dezimalpunkt („15.757“).

    Ohne Komma ist ein einzelner Punkt der Dezimaltrenner: Tausenderpunkte kommen bei kWh je
    Ladevorgang nicht vor.
    """
    if "," in text:
        return decimal_lesen(text)
    if text.count(".") > 1:
        raise ValueError(f"„{text}“ ist keine gültige kWh-Angabe.")
    return Decimal(text)


def _datum(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def _zeitpunkt(tag: str, zeit: str) -> datetime:
    """Datum + Uhrzeit; fehlerhafte Uhrzeiten wie „24:57“ (kommen in DCS-Rechnungen vor) werden als Überlauf in den
    Folgetag gelesen: 28.12.2025 24:57 = 29.12.2025 00:57."""
    stunden, minuten = (int(x) for x in zeit.split(":"))
    return datetime.strptime(tag, "%d.%m.%Y") + timedelta(hours=stunden, minutes=minuten)


def _utc_nach_wien(tag: str, zeit: str) -> datetime:
    """UTC-Zeitstempel der Rechnung → Wiener Ortszeit (ohne Zeitzone)."""
    return _zeitpunkt(tag, zeit).replace(tzinfo=timezone.utc).astimezone(WIEN).replace(tzinfo=None)


def _zeiten_in_utc(teil: str) -> bool:
    """Ältere Rechnungen: „Datum/Uhrzeit (UTC)“; neuere nur „Datum/Uhrzeit“ – dann Ortszeit."""
    return "Datum/Uhrzeit (UTC)" in teil or not re.search(r"Datum/Uhrzeit(?! \(UTC\))", teil)


def _uhrzeit_ungueltig(zeit: str) -> bool:
    stunden, minuten = (int(x) for x in zeit.split(":"))
    return stunden > 23 or minuten > 59


def _seiten_je_rechnung(text: str) -> list[tuple[str, str]]:
    """[(Rechnungsnummer, Text)] in Reihenfolge; Übersichtsseiten werden übersprungen."""
    teile = re.split(r"(?m)^(?=.*DocInfo>>)", text)
    rechnungen: dict[str, list[str]] = {}
    for seite in teile:
        m = RE_DOCINFO.search(seite)
        if not m or "Rechnungsübersicht" in seite:
            continue
        rechnungen.setdefault(m.group(1), []).append(seite)
    if not rechnungen:  # ältere Layouts ohne Seitenmarke
        nr = re.search(r"Rechnungsnummer\s*\n\s*([A-Z]{3}\d+)", text)
        return [(nr.group(1) if nr else "", text)]
    return [(nr, "\n".join(seiten)) for nr, seiten in rechnungen.items()]


def _dauer(folgezeilen: list[str]) -> re.Match | None:
    """Ladedauer „11:07 hh:mm“: meist in Zeile 2 des Vorgangs, bei langen Vorgängen auch eine Zeile tiefer (unter
    „(kWh)“). Nur bis zum nächsten Vorgang suchen, damit nicht dessen Dauer gelesen wird."""
    for z in folgezeilen:
        if RE_VORGANG_ANFANG.match(z):
            return None
        if m := RE_DAUER.search(z):
            return m
    return None


def _ladepunkt_und_ort(zeile: str, folgezeile: str | None) -> tuple[str, str]:
    """Zeile 2 eines Vorgangs, z. B. „AT * MER * E000001 * 02 Musterort 0:06 hh:mm …“ → (EVSE, Ort)."""
    tokens = RE_ORT_ENDE.split(zeile, maxsplit=1)[0].split()
    teile, i = [], 0
    if tokens and RE_ID.fullmatch(tokens[0]):
        teile, i = [tokens[0]], 1
    while i + 1 < len(tokens) and tokens[i] == "*" and RE_ID.fullmatch(tokens[i + 1]):
        teile.append(tokens[i + 1])
        i += 2
    if i < len(tokens) and tokens[i] == "*":  # Ladepunkt-ID in die nächste Zeile umgebrochen
        i += 1
        if folgezeile and RE_ID.fullmatch(folgezeile.strip()):
            teile.append(folgezeile.strip())
    return "*".join(teile), " ".join(" ".join(tokens[i:]).replace("|", " ").split())


class DcsParser(Parser):
    key = "dcs"
    name = "DCS / BMW Charging (PDF)"
    msp_name = "DCS / BMW Charging"
    abrechnung_je_karte = True  # monatliche Rechnung je Ladekarte

    def erkennen(self, pfad: Path) -> bool:
        return ist_pdf(pfad) and "Digital Charging Solutions" in pdf_text(pfad, nur_erste_seite=True)

    def parsen(self, pfad: Path) -> Ergebnis:
        return parsen_text(pdf_text(pfad))


def parsen_text(text: str) -> Ergebnis:
    ergebnis = Ergebnis(rechnung=Rechnung())
    nummern, netto_gesamt, daten, zeitraeume = [], Decimal(0), [], []
    for nr, teil in _seiten_je_rechnung(text):
        land = (re.search(r"Ihre Rechnung für\s+(.+)", teil) or [None, "?"])[1].strip()
        nummern.append(nr)
        if m := re.search(r"Datum\s+(\d{2}\.\d{2}\.\d{4})", teil):
            daten.append(_datum(m.group(1)))
        zeitraum = None
        if m := re.search(r"Zeitraum vom\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", teil):
            zeitraum = (_datum(m.group(1)), _datum(m.group(2)))
            zeitraeume.append(zeitraum)
        vorgaenge, gebuehren, netto, summe_laden, warnungen = _teilrechnung(teil, nr, land, zeitraum)
        ergebnis.warnungen += warnungen
        if netto is None:
            ergebnis.warnungen.append(f"Rechnung {nr} ({land}): Nettobetrag nicht gefunden – PDF unvollständig?")
            netto = summe_laden + sum((g.betrag_netto for g in gebuehren), Decimal(0))
        netto_gesamt += netto

        # Einzelbeträge (4 Stellen) centgenau auf die Ladekosten der Teilrechnung verteilen
        ziel = netto - sum((g.betrag_netto for g in gebuehren), Decimal(0))
        roh = [v.kosten_netto for v in vorgaenge]
        verteilt = auf_summe_verteilen(roh, ziel) if vorgaenge else ([] if ziel == 0 else None)
        if verteilt is None:
            ergebnis.warnungen.append(f"Rechnung {nr} ({land}): Einzelvorgänge {cent(sum(roh, Decimal(0)))} EUR passen nicht "
                                      f"zu den Ladekosten {ziel} EUR.")
            verteilt = [cent(r) for r in roh]
        if summe_laden != ziel and vorgaenge:
            ergebnis.warnungen.append(f"Rechnung {nr} ({land}): Summe Ladevorgänge {summe_laden} EUR ≠ "
                                      f"Nettobetrag abzüglich Gebühren {ziel} EUR.")
        for v, betrag in zip(vorgaenge, verteilt):
            v.kosten_netto = betrag
        if warnung := _ust(teil, vorgaenge + gebuehren):
            ergebnis.warnungen.append(f"Rechnung {nr} ({land}): {warnung}")
        ergebnis.vorgaenge += vorgaenge
        ergebnis.gebuehren += gebuehren
        ergebnis.zeiten_utc |= bool(vorgaenge) and _zeiten_in_utc(teil)

    if not nummern or (not ergebnis.vorgaenge and not ergebnis.gebuehren):
        raise ParserFehler("Keine DCS-Rechnung mit Ladevorgängen oder Gebühren gefunden.")
    r = ergebnis.rechnung
    r.nummer = ", ".join(n for n in nummern if n)[:100] or None
    r.datum = max(daten) if daten else None
    r.summe_netto = netto_gesamt
    if zeitraeume:
        r.zeitraum_von, r.zeitraum_bis = min(z[0] for z in zeitraeume), max(z[1] for z in zeitraeume)
    return ergebnis


RE_MWST = re.compile(rf"^\+\s*(\d+(?:,\d+)?)\s*%\s*MwSt\.?\s*([A-Z]{{2}})\b.*?({ZAHL})\s*EUR\s*$", re.M)


def _ust(teil: str, posten: list) -> str | None:
    """USt. einer Teilrechnung auf ihre Posten übertragen (siehe Moduldoku); ohne Angabe bleibt sie offen
    (die App schätzt sie dann)."""
    if m := RE_MWST.search(teil):
        return ust_setzen(posten, decimal_lesen(m.group(1)), m.group(2), decimal_lesen(m.group(3)))
    if "Steuerschuldnerschaft des Leistungsempfängers" in teil:
        ust_setzen(posten, None, REVERSE_CHARGE)
    return None


def _teilrechnung(teil: str, nr: str, land: str, zeitraum):
    """Liefert (Vorgänge mit 4-stelligen Rohbeträgen, Gebühren, Nettobetrag, Summe Ladevorgänge, Warnungen)."""
    vorgaenge: list[Vorgang] = []
    gebuehren: list[Gebuehr] = []
    warnungen: list[str] = []
    vertraege: dict[str, str] = {}  # Vertrag → Kopfzeile (Kartennummer, Tarif, Vertragsname)
    netto, summe_laden = None, Decimal(0)
    in_utc = _zeiten_in_utc(teil)
    vertrag, abschnitt, offene_gebuehr = None, None, None
    zeilen = [z.strip() for z in teil.splitlines()]

    for i, z in enumerate(zeilen):
        if m := RE_VERTRAG.match(z):
            vertrag = m.group(1)
            vertraege.setdefault(vertrag, m.group(2))
            continue
        if z.startswith("Leistungsposition"):
            abschnitt = "positionen"
            continue
        if z.startswith("Übersicht der Ladevorgänge"):
            abschnitt = "vorgaenge"
            continue

        if abschnitt == "positionen":
            if z.startswith("Gesamtbetrag"):
                if netto is None and (betraege := RE_EUR.findall(z)):
                    netto = decimal_lesen(betraege[-1])  # erste Zeile: netto („*“ bzw. „(xx% MwSt.)“)
                abschnitt = None
                continue
            if offene_gebuehr and RE_DATUM.fullmatch(z):
                offene_gebuehr.zeitraum_bis = _datum(z)  # „vom 01.06.2026 -“ in die Folgezeile umgebrochen
                continue
            betraege = RE_EUR.findall(z)
            if not betraege or vertrag is None:
                continue
            betrag = decimal_lesen(betraege[-1])
            if z.startswith("Summe Ladevorgänge"):
                summe_laden += betrag
                continue
            daten = RE_DATUM.findall(z)
            bezeichnung = re.split(r"\s+vom\s+|\s+\d", z, maxsplit=1)[0]
            offene_gebuehr = Gebuehr(
                bezeichnung=f"{bezeichnung} ({land})" if land != "Österreich" else bezeichnung,
                betrag_netto=betrag, kartenkennung=vertrag, vertragsnummer=vertrag,
                kennung_typ=KennungTyp.vertragsnummer, kartenbezeichnung=f"DCS {vertraege.get(vertrag, '')}".strip(),
                zeitraum_von=_datum(daten[0]) if daten else (zeitraum[0] if zeitraum else None),
                zeitraum_bis=_datum(daten[1]) if len(daten) > 1 else None)
            gebuehren.append(offene_gebuehr)
            continue

        if abschnitt == "vorgaenge" and (m := RE_VORGANG.match(z)):
            if vertrag is None:
                warnungen.append(f"Rechnung {nr}: Ladevorgang ohne Vertrag übersprungen: {z}")
                continue
            folge = zeilen[i + 1] if i + 1 < len(zeilen) else ""
            dauer = _dauer(zeilen[i + 1:i + 4])
            evse, ort = _ladepunkt_und_ort(folge, zeilen[i + 2] if i + 2 < len(zeilen) else None)
            start = _utc_nach_wien(m.group(1), m.group(2)) if in_utc else _zeitpunkt(m.group(1), m.group(2))
            if _uhrzeit_ungueltig(m.group(2)):
                warnungen.append(f"Rechnung {nr}: ungültige Uhrzeit „{m.group(1)} {m.group(2)}“"
                                 f"{' (UTC)' if in_utc else ''} als {start:%d.%m.%Y %H:%M} Wiener Zeit gelesen "
                                 "(Überlauf in den Folgetag) – bitte prüfen.")
            ende = start + timedelta(hours=int(dauer.group(1)), minutes=int(dauer.group(2))) if dauer else start
            if not dauer:
                warnungen.append(f"Rechnung {nr}: Ladedauer nicht gefunden ({m.group(1)} {m.group(2)}h).")
            betraege = RE_EUR.findall(m.group(6))
            if not betraege:
                warnungen.append(f"Rechnung {nr}: Betrag nicht gefunden ({m.group(1)} {m.group(2)}h).")
                continue
            adresse = " ".join(m.group(3).replace("|", " ").split())
            kopf = vertraege.get(vertrag, "")
            karte = RE_KARTE.search(kopf)
            vorgaenge.append(Vorgang(
                kartenkennung=vertrag, kennung_typ=KennungTyp.vertragsnummer, vertragsnummer=vertrag,
                start=start, ende=ende, kwh=kwh_runden(kwh_lesen(m.group(4))),
                kosten_netto=decimal_lesen(betraege[-1]),  # 4 Stellen; wird je Teilrechnung verteilt
                standort=", ".join(x for x in (adresse, ort) if x) or None, ladepunkt=evse or None,
                kartenbezeichnung=f"DCS {kopf}".strip(),
                stromart=stromart_normieren(m.group(5)),
                rohdaten={"rechnung": nr, "land": land,
                          "zeit_utc" if in_utc else "zeit_ortszeit": f"{m.group(1)} {m.group(2)}",
                          "produkt": m.group(5), "betrag_roh": betraege[-1], "karte": karte.group(0) if karte else "",
                          "zeilen": [z, folge]}))
            continue
        if abschnitt == "vorgaenge" and RE_VORGANG_ANFANG.match(z):
            warnungen.append(f"Rechnung {nr}: Ladevorgang nicht lesbar und übersprungen: {z}")
    return vorgaenge, gebuehren, netto, summe_laden, warnungen
