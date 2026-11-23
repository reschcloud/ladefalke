"""Parser: has.to.be-CSV der eigenen Wallboxen (Ladesoftware has.to.be, privat), SPEC 4.1.

UTF-8 (ggf. mit BOM), `;`-getrennt, Dezimalkomma. MeterTotal in Wh.
Kosten stehen nicht in der Datei (TotalCosts = 0) und werden beim Import aus dem Strompreis berechnet.
Die Datei enthält auch Vorgänge, die im Vormonat starten (Abrechnung nach Ladeende).
"""
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.formatierung import decimal_lesen
from app.models import KennungTyp
from app.parsers.basis import Ergebnis, Parser, ParserFehler, Rechnung, Vorgang, kwh_runden

PFLICHTSPALTEN = {"Id", "StartLocalTime", "EndLocalTime", "MeterTotal", "CardLabel"}
ZEITFORMAT = "%Y-%m-%d %H:%M:%S"


def kartenkennung(label: str, referenz: str) -> tuple[str, bool]:
    """Kennung der Karte und ob es eine Gastkarte ist.

    Normale Karten über CardLabel (CardCustomerReference ist dort nur „Zutrittschip“ o. ä.),
    Gastkarten über CardLabel + CardCustomerReference, z. B. „GAST / Vorname Nachname“.
    """
    label, referenz = label.strip(), referenz.strip()
    gast = label.upper() == "GAST"
    if gast and referenz:
        return f"{label} / {referenz}", True
    return label, gast


class HastobeCsvParser(Parser):
    key = "hastobe_csv"
    name = "has.to.be CSV (Wallboxen)"
    msp_name = "Eigene Wallboxen (has.to.be)"
    liefert_evse = False  # Ladepunkt-Angabe ist die Seriennummer der Wallbox
    liefert_kosten = False  # nur kWh: Kosten = kWh × Strompreis (beim Import, Vorbelegung beim MSP)
    standardland = "AT"
    standard_ladeart = "privat"

    def _zeilen(self, pfad: Path) -> list[dict]:
        with open(pfad, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, delimiter=";"))

    def erkennen(self, pfad: Path) -> bool:
        try:
            with open(pfad, encoding="utf-8-sig") as f:
                kopf = f.readline()
        except (UnicodeDecodeError, OSError):
            return False
        return PFLICHTSPALTEN <= set(kopf.strip().split(";"))

    def parsen(self, pfad: Path) -> Ergebnis:
        zeilen = self._zeilen(pfad)
        if not zeilen or not PFLICHTSPALTEN <= set(zeilen[0]):
            raise ParserFehler("Die Datei hat nicht die erwarteten Spalten einer has.to.be-CSV.")
        ergebnis = Ergebnis(rechnung=Rechnung())
        mit_kosten = 0
        for nr, z in enumerate(zeilen, start=2):
            try:
                kennung, gast = kartenkennung(z["CardLabel"], z.get("CardCustomerReference", ""))
                wh = decimal_lesen(z["MeterTotal"]) or Decimal(0)
                v = Vorgang(
                    kartenkennung=kennung or None,
                    kennung_typ=KennungTyp.kartenlabel,
                    start=datetime.strptime(z["StartLocalTime"].strip(), ZEITFORMAT),
                    ende=datetime.strptime(z["EndLocalTime"].strip(), ZEITFORMAT),
                    kwh=kwh_runden(wh / 1000),
                    externe_id=z["Id"].strip(),
                    standort=_standort(z),
                    ladepunkt=(z.get("ChargePointSerialnumber") or "").strip() or None,
                    kartenbezeichnung=z["CardLabel"].strip() or None,
                    gastkarte=gast,
                    rohdaten=dict(z),
                )
            except (KeyError, ValueError) as e:
                raise ParserFehler(f"Zeile {nr}: {e}") from None
            if decimal_lesen(z.get("TotalCosts")):
                mit_kosten += 1
            ergebnis.vorgaenge.append(v)

        if ergebnis.vorgaenge:
            ergebnis.rechnung.zeitraum_von = min(v.ende for v in ergebnis.vorgaenge).date()
            ergebnis.rechnung.zeitraum_bis = max(v.ende for v in ergebnis.vorgaenge).date()
        if mit_kosten:
            ergebnis.warnungen.append(f"{mit_kosten} Vorgänge haben TotalCosts ≠ 0 – ignoriert, "
                                      "Kosten werden aus dem Strompreis berechnet.")
        if leer := sum(1 for v in ergebnis.vorgaenge if v.kwh == 0):
            ergebnis.warnungen.append(f"{leer} Vorgänge mit 0 kWh – werden importiert, "
                                      "in Auswertungen aber nicht als Ladevorgang gezählt.")
        return ergebnis


def _standort(z: dict) -> str | None:
    adresse = " ".join(x for x in (z.get("ChargePointZip", "").strip(), z.get("ChargePointCity", "").strip()) if x)
    adresse = ", ".join(x for x in (z.get("ChargePointStreet", "").strip(), adresse) if x)
    label = z.get("ChargePointLabel", "").strip()
    if label and adresse:
        return f"{label} ({adresse})"
    return label or adresse or None
