"""Parser: Wien Energie – Servicerechnung E-Mobilität (PDF), SPEC 4.2.

Aufbau: Seite 1 Übersicht je Vertrag (Bruttobeträge), danach „Rechnungsbetrag“ und
Nettobetrag, dann Details je Vertrag mit den Abschnitten „Wien Energie-Ladestationen“,
„Ladestationen anderer Betreiber (Roaming)“ und „Gebühren und Entgelte“.

- Je Ladepunkt-Kopfzeile ein Ladevorgang; mehrere Zeilen darunter sind Tarifzeitfenster
  desselben Vorgangs (z. B. 18:26–21:59, 22:00–23:59, 00:00–06:53).
- Zeittarife ohne kWh: kWh werden mit den Vorgaben geschätzt (services/schaetzung.py);
  die Schätzbasis geht mit, damit der Import mit den Werten aus den Ladedaten neu schätzen kann.
- Alle Beträge in der Rechnung sind brutto; netto = brutto / (1 + USt), Rundungsreste
  werden so verteilt, dass die Summe exakt dem Nettobetrag der Rechnung entspricht.
- „Ladekarte:“ nennt die Kartennummer („Ladekarte: 100001“) oder – in älteren Rechnungen (bis etwa 2023) – die
  Vertragsnummer („Ladekarte: AT-VIE-C00000001-A“); die Kennung gilt dann als Vertragsnummer.
"""
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.formatierung import decimal_lesen
from app.models import KennungTyp
from app.parsers.basis import (Ergebnis, Gebuehr, Parser, ParserFehler, Rechnung, Vorgang, auf_summe_verteilen, cent,
                               ist_pdf, kwh_runden, pdf_text)
from app.services.schaetzung import Schaetzbasis, kwh_schaetzen

BETRAG = r"-?\d[\d.]*,\d{2}"
RE_VERTRAG = re.compile(r"AT-VIE-[A-Z0-9]+-[A-Z0-9]")
RE_ZEILE_BETRAG = re.compile(rf"^(.*\D)\s+({BETRAG})$")
RE_GEBUEHR = re.compile(rf"^(.+?)\s+({BETRAG})$")  # letzter Betrag = Brutto (davor ggf. Menge/Preis)
RE_ABSCHNITT = re.compile(rf"^(Wien Energie-Ladestationen|Ladestationen anderer Betreiber \(Roaming\)"
                          rf"|Gebühren und Entgelte)(?:\s+({BETRAG}))?$")
RE_SEGMENT = re.compile(r"^(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})\s+(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})\s+(.+?)\s+"
                        rf"(\d[\d.]*,\d+)\s+(-?\d[\d.]*,\d+)\s+({BETRAG})$")
RE_LADEPUNKT = re.compile(r"[A-Z]{2}\*[A-Z0-9]{3}\*[^,]+")
RE_LEISTUNG = re.compile(r",(AC|DC)\s?(\d+(?:,\d+)?)\s*-\s*Ladekarte")
RE_KARTE = re.compile(r"Ladekarte:\s*(\S+)")  # Kartennummer oder (älter) Vertragsnummer
RE_ZEITRAUM = re.compile(r"(\d{1,2}\.\d{1,2}\.\d{4})\s*-\s*(\d{1,2}\.\d{1,2}\.\d{4})")


def _zahl(text: str) -> Decimal:
    return decimal_lesen(text)


def _datum(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%Y").date()


def _zeit(text: str) -> datetime:
    return datetime.strptime(text, "%d.%m.%Y %H:%M")


def _erstes(muster: str, text: str) -> str | None:
    m = re.search(muster, text)
    return m.group(1).strip() if m else None


class WienEnergieParser(Parser):
    key = "wien_energie"
    name = "Wien Energie – Servicerechnung E-Mobilität (PDF)"
    msp_name = "Wien Energie"
    standardland = "AT"

    def erkennen(self, pfad: Path) -> bool:
        if not ist_pdf(pfad):
            return False
        text = pdf_text(pfad, nur_erste_seite=True)
        return "Wien Energie GmbH" in text and "E-Mobilität" in text

    def parsen(self, pfad: Path) -> Ergebnis:
        return parsen_text(pdf_text(pfad))


def parsen_text(text: str) -> Ergebnis:
    warnungen: list[str] = []
    rechnung = Rechnung(nummer=_erstes(r"E-Mobilität\s*Nr\.\s*(\d+)", text))
    if d := _erstes(r"Nr\.\s*\d+\s*vom\s*(\d{2}\.\d{2}\.\d{4})", text):
        rechnung.datum = _datum(d)
    if n := _erstes(rf"Nettobetrag\s*({BETRAG})\s*EUR", text):
        rechnung.summe_netto = _zahl(n)
    ust = _zahl(_erstes(r"\+\s*(\d+(?:,\d+)?)\s*%\s*USt", text) or "20") / 100
    brutto_gesamt = _erstes(rf"Rechnungsbetrag \(inkl\. USt\.\)\s*({BETRAG})", text)
    if brutto_gesamt is None:
        warnungen.append("„Rechnungsbetrag“ nicht gefunden – das PDF wirkt unvollständig.")
    if rechnung.summe_netto is None:
        warnungen.append("Nettobetrag der Rechnung nicht gefunden.")

    uebersicht: dict[str, Decimal] = defaultdict(Decimal)  # Vertrag → Brutto laut Seite 1
    sitzungen: list[dict] = []
    gebuehren: list[dict] = []
    abschnitt_summen: dict[tuple[str, str], Decimal] = defaultdict(Decimal)  # (Vertrag, Abschnitt) → Details

    phase, vertrag, abschnitt, kopf, sitzung, offen = "uebersicht", None, None, None, None, None
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        if phase == "uebersicht":
            if m := RE_VERTRAG.search(zeile):
                vertrag = m.group(0)
                uebersicht[vertrag] += 0
            elif zeile.startswith("Rechnungsbetrag"):
                phase, vertrag = "details", None
            elif vertrag and (m := RE_ZEILE_BETRAG.match(zeile)):
                uebersicht[vertrag] += _zahl(m.group(2))
            continue

        if offen is not None:  # umgebrochene Ladepunkt-Kopfzeile fortsetzen
            zeile, offen = f"{offen} {zeile}", None

        if zeile.startswith("Vertrag") and (m := RE_VERTRAG.search(zeile)):
            vertrag, abschnitt, kopf, sitzung = m.group(0), None, None, None
            continue
        if vertrag is None:
            continue
        if m := RE_ABSCHNITT.match(zeile):
            if m.group(2) is None:
                abschnitt, kopf, sitzung = m.group(1), None, None
            else:  # Abschnittssumme: gegen Details prüfen
                ist = abschnitt_summen[(vertrag, m.group(1))]
                if ist != _zahl(m.group(2)):
                    warnungen.append(f"Vertrag {vertrag}, {m.group(1)}: Positionen {ist} ≠ Summe {m.group(2)} EUR.")
            continue
        if zeile.startswith(("Ladebeginn", "Produkt")):
            continue  # Tabellenkopf (auch nach Seitenumbruch)

        if abschnitt == "Gebühren und Entgelte":
            if m := RE_GEBUEHR.match(zeile):
                brutto = _zahl(m.group(2))
                bezeichnung = re.sub(rf"(\s+{BETRAG})+$", "", zeile)
                zr = RE_ZEITRAUM.search(bezeichnung)
                gebuehren.append({"vertrag": vertrag, "bezeichnung": bezeichnung, "brutto": brutto,
                                  "von": _datum(zr.group(1)) if zr else None,
                                  "bis": _datum(zr.group(2)) if zr else None})
                abschnitt_summen[(vertrag, abschnitt)] += brutto
            continue

        if m := RE_SEGMENT.match(zeile):
            if kopf is None:
                warnungen.append(f"Vertrag {vertrag}: Ladezeile ohne Ladepunkt übersprungen: {zeile}")
                continue
            if sitzung is None:
                sitzung = {**kopf, "vertrag": vertrag, "start": _zeit(m.group(1)), "ende": _zeit(m.group(2)),
                           "stunden": Decimal(0), "kwh_ist": Decimal(0), "brutto": Decimal(0), "abschnitte": []}
                sitzungen.append(sitzung)
            einheit, basis = m.group(3).lower(), _zahl(m.group(4))
            if "kwh" in einheit:
                sitzung["kwh_ist"] += basis
            elif "min" in einheit:
                sitzung["stunden"] += basis / 60
            else:
                sitzung["stunden"] += basis
            brutto = _zahl(m.group(6))
            sitzung["brutto"] += brutto
            sitzung["ende"] = max(sitzung["ende"], _zeit(m.group(2)))
            sitzung["abschnitte"].append(zeile)
            abschnitt_summen[(vertrag, abschnitt)] += brutto
            continue

        if RE_LADEPUNKT.search(zeile) and "," in zeile:
            if not RE_KARTE.search(zeile):
                offen = zeile  # Kopfzeile in die nächste Zeile umgebrochen
                continue
            lp = RE_LADEPUNKT.search(zeile)
            leistung = RE_LEISTUNG.search(zeile)
            kopf = {"standort": zeile[:lp.start()].rstrip(", ") or None, "ladepunkt": lp.group(0),
                    "karte": RE_KARTE.search(zeile).group(1),
                    "art": leistung.group(1) if leistung else "AC",
                    "stromart": leistung.group(1) if leistung else None,
                    "nennleistung": _zahl(leistung.group(2)) if leistung else None,
                    "kopfzeile": zeile}
            sitzung = None

    # ---------------------------------------------------------------- Prüfungen
    details_je_vertrag: dict[str, Decimal] = defaultdict(Decimal)
    for (v, _), betrag in abschnitt_summen.items():
        details_je_vertrag[v] += betrag
    for v, soll in uebersicht.items():
        if details_je_vertrag.get(v, Decimal(0)) != soll:
            warnungen.append(f"Vertrag {v}: Details {details_je_vertrag.get(v, Decimal(0))} ≠ "
                             f"Übersicht {soll} EUR (brutto).")
    if brutto_gesamt and sum(uebersicht.values(), Decimal(0)) != _zahl(brutto_gesamt):
        warnungen.append(f"Summe der Verträge {sum(uebersicht.values(), Decimal(0))} ≠ "
                         f"Rechnungsbetrag {brutto_gesamt} EUR (brutto).")

    # ---------------------------------------------------------------- Netto
    brutto_liste = [s["brutto"] for s in sitzungen] + [g["brutto"] for g in gebuehren]
    roh_netto = [b / (1 + ust) for b in brutto_liste]
    netto = auf_summe_verteilen(roh_netto, rechnung.summe_netto) if rechnung.summe_netto is not None else None
    if netto is None:
        if rechnung.summe_netto is not None:
            warnungen.append("Positionen passen nicht zum Nettobetrag – Netto je Position einzeln gerundet.")
        netto = [cent(n) for n in roh_netto]

    # ---------------------------------------------------------------- Ergebnis
    karten_je_vertrag: dict[str, set[str]] = defaultdict(set)
    for s in sitzungen:
        karten_je_vertrag[s["vertrag"]].add(s["karte"])

    ergebnis = Ergebnis(rechnung=rechnung, warnungen=warnungen)
    for s, n in zip(sitzungen, netto):
        basis = None
        if s["kwh_ist"] > 0:
            kwh = kwh_runden(s["kwh_ist"])
        else:
            basis = Schaetzbasis(s["stunden"], s["art"], s["nennleistung"])
            kwh = kwh_schaetzen(basis)
        rohdaten = {"vertrag": s["vertrag"], "kopfzeile": s["kopfzeile"], "abschnitte": s["abschnitte"],
                    "brutto": str(s["brutto"])}
        ergebnis.vorgaenge.append(Vorgang(
            kartenkennung=s["karte"], vertragsnummer=s["vertrag"],
            kennung_typ=KennungTyp.vertragsnummer if RE_VERTRAG.fullmatch(s["karte"]) else KennungTyp.kartennummer,
            start=s["start"], ende=s["ende"], kwh=kwh, kwh_geschaetzt=basis is not None, kosten_netto=n,
            standort=s["standort"], ladepunkt=s["ladepunkt"], kartenbezeichnung=f"Wien Energie {s['vertrag']}",
            stromart=s["stromart"], schaetzbasis=basis,
            rohdaten={**rohdaten, **(basis.als_rohdaten() if basis else {})}))
    for g, n in zip(gebuehren, netto[len(sitzungen):]):
        karten = karten_je_vertrag.get(g["vertrag"], set())
        ergebnis.gebuehren.append(Gebuehr(
            bezeichnung=g["bezeichnung"], betrag_netto=n, vertragsnummer=g["vertrag"],
            kartenkennung=next(iter(karten)) if len(karten) == 1 else None,
            zeitraum_von=g["von"], zeitraum_bis=g["bis"]))

    zeitraeume = [(g.zeitraum_von, g.zeitraum_bis) for g in ergebnis.gebuehren if g.zeitraum_von]
    if zeitraeume:
        rechnung.zeitraum_von, rechnung.zeitraum_bis = min(z[0] for z in zeitraeume), max(z[1] for z in zeitraeume)
    elif ergebnis.vorgaenge:
        rechnung.zeitraum_von = min(v.ende for v in ergebnis.vorgaenge).date()
        rechnung.zeitraum_bis = max(v.ende for v in ergebnis.vorgaenge).date()
    if not sitzungen and not gebuehren:
        raise ParserFehler("Keine Ladevorgänge oder Gebühren gefunden – ist das eine Wien-Energie-Rechnung?")
    return ergebnis
