"""Parser: Salzburg AG (CSV-Export „Stromladen_Beleg_<Nr>.csv“) – Teil des Salzburg-AG-Parsers (Schlüssel
„salzburg_ag“, siehe salzburg_ag.py).

Export der Ladeplattform (be.ENERGISED), eine Zeile je Ladevorgang:
- „Transaction ID“ = Transaktionsnummer des PDF-Ladenachweises (externe ID → keine Doppelten bei PDF und CSV)
- „Card number“ = Kartenkennung wie im PDF („K00001“)
- Zeiten in UTC (→ Wiener Zeit), „Consumption“ in Wh, „Amount Net“ netto (3 Nachkommastellen → Cent)
- AC/DC aus dem Tarif („Cost Rate“, z. B. „PREMIUM AC 072026“, „PREMIUM DC SNAP“); „Roaming PLUS“ ohne Angabe
- Rechnung: „Account Document Number“/„Account Document Date“, Summe netto „Invoice Amount Net“
Grundgebühren je Karte stehen nicht in der CSV: die Differenz zur Rechnungssumme wird als Gebühr ohne Kartenbezug
erfasst (mit Warnung). Erkannt wird die Datei an ihren Spalten und an „Salzburg AG“ in der Spalte „Entity“, damit
gleich aufgebaute Exporte anderer Anbieter nicht hier landen.
"""
import csv
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import KennungTyp
from app.parsers.basis import CENT, Ergebnis, Gebuehr, ParserFehler, Rechnung, Vorgang, kwh_runden

WIEN = ZoneInfo("Europe/Vienna")
PFLICHTSPALTEN = {"Transaction ID", "Start time (UTC)", "End time (UTC)", "Consumption", "Amount Net",
                  "Account Document Number", "Card number", "Entity"}
# nicht in die Rohdaten übernehmen (Kundendaten)
OHNE = {"Contact Customer Number", "Contact Name", "Contact Street", "Contact ZIP", "Contact City",
        "Contact Country Alpha2", "Card customer reference", "Card Comment", "Card Order ID"}


def _zeilen(pfad: Path) -> list[dict]:
    with open(pfad, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ist_salzburg_ag_csv(pfad: Path) -> bool:
    try:
        with open(pfad, encoding="utf-8-sig", newline="") as f:
            leser = csv.DictReader(f)
            if not PFLICHTSPALTEN <= set(leser.fieldnames or ()):
                return False
            erste = next(leser, None)
    except (UnicodeDecodeError, OSError, csv.Error):
        return False
    return erste is not None and "Salzburg AG" in (erste.get("Entity") or "")


def _zeit(text: str) -> datetime:
    """ISO-Zeitstempel in UTC → Wiener Ortszeit ohne Zeitzone (wie alle Zeiten in der Datenbank)."""
    return datetime.fromisoformat(text.strip()).astimezone(timezone.utc).astimezone(WIEN).replace(tzinfo=None)


def _betrag(text: str | None) -> Decimal:
    return Decimal((text or "0").strip() or "0")


def _stromart(tarif: str) -> str | None:
    if re.search(r"\bDC\b", tarif):
        return "DC"
    if re.search(r"\bAC\b", tarif):
        return "AC"
    return None


def _monat(tag: date) -> tuple[date, date]:
    von = tag.replace(day=1)
    return von, (von.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def parsen(pfad: Path) -> Ergebnis:
    zeilen = _zeilen(pfad)
    if not zeilen:
        raise ParserFehler("Die CSV-Datei enthält keine Ladevorgänge.")
    r = Rechnung()
    warnungen: list[str] = []
    ergebnis = Ergebnis(rechnung=r, warnungen=warnungen)
    erste = zeilen[0]
    r.nummer = (erste.get("Account Document Number") or "").strip() or None
    if datum := (erste.get("Account Document Date") or "").strip():
        r.datum = datetime.fromisoformat(datum).date()
    if (summe := (erste.get("Invoice Amount Net") or "").strip()):
        r.summe_netto = _betrag(summe).quantize(CENT, rounding=ROUND_HALF_UP)
    if len({z.get("Account Document Number") for z in zeilen}) > 1:
        warnungen.append("Die Datei enthält mehrere Belege – bitte je Beleg eine Datei importieren.")

    for z in zeilen:
        karte = (z.get("Card number") or "").strip() or None
        strasse, plz, ort = ((z.get(k) or "").strip() for k in
                             ("Charging Station Street", "Charging Station Zip", "Charging Station City"))
        tarif = (z.get("Cost Rate") or "").strip()
        ergebnis.vorgaenge.append(Vorgang(
            kartenkennung=karte, kennung_typ=KennungTyp.kartennummer,
            start=_zeit(z["Start time (UTC)"]), ende=_zeit(z["End time (UTC)"]),
            kwh=kwh_runden(_betrag(z.get("Consumption")) / 1000),
            kosten_netto=_betrag(z.get("Amount Net")).quantize(CENT, rounding=ROUND_HALF_UP),
            externe_id=(z.get("Transaction ID") or "").strip() or None,
            ladepunkt=(z.get("EvseID") or "").strip() or None,
            standort=", ".join(x for x in (strasse, f"{plz} {ort}".strip()) if x) or None,
            kartenbezeichnung=(z.get("Card name") or "").strip() or karte,
            stromart=_stromart(tarif),
            rohdaten={"rechnung": r.nummer, "land": (z.get("Charging Station Country Alpha2") or "").strip(),
                      "tarif": tarif, "csv": {k: v for k, v in z.items() if v and k not in OHNE}}))
        if (ust := (z.get("Amount VAT") or "").strip()) and (satz := (z.get("Tax Rate Energy") or "").strip()):
            v = ergebnis.vorgaenge[-1]
            v.ust_betrag, v.ust_satz = _betrag(ust).quantize(CENT, rounding=ROUND_HALF_UP), _betrag(satz)
            v.ust_land = (z.get("Charging Station Country Alpha2") or "").strip().upper()[:2] or None
        if not karte:
            warnungen.append(f"Ladevorgang {z.get('Transaction ID')}: Ladekarte nicht gefunden.")

    # Abrechnungsmonat aus den Ladevorgängen, sonst aus dem Belegdatum
    if ergebnis.vorgaenge:
        r.zeitraum_von = _monat(min(v.start for v in ergebnis.vorgaenge).date())[0]
        r.zeitraum_bis = _monat(max(v.ende for v in ergebnis.vorgaenge).date())[1]
    elif r.datum:
        r.zeitraum_von, r.zeitraum_bis = _monat(r.datum)

    # Grundgebühren u. Ä. fehlen in der CSV: Differenz zur Rechnungssumme als Gebühr ohne Kartenbezug
    if r.summe_netto is not None:
        rest = r.summe_netto - sum((v.kosten_netto for v in ergebnis.vorgaenge), Decimal(0))
        if rest:
            g = Gebuehr("Grundgebühren und sonstige Positionen (in der CSV nicht aufgeschlüsselt)",
                        rest, zeitraum_von=r.zeitraum_von, zeitraum_bis=r.zeitraum_bis)
            ergebnis.gebuehren.append(g)
            # USt. dazu: Rechnungs-USt. minus USt. der Ladevorgänge, Satz und Land wie bei den Ladevorgängen
            saetze = {(v.ust_satz, v.ust_land) for v in ergebnis.vorgaenge if v.ust_satz is not None}
            if (ust_gesamt := (erste.get("Invoice Amount VAT") or "").strip()) and len(saetze) == 1:
                g.ust_satz, g.ust_land = saetze.pop()
                g.ust_betrag = _betrag(ust_gesamt).quantize(CENT, rounding=ROUND_HALF_UP) - sum(
                    (v.ust_betrag or Decimal(0) for v in ergebnis.vorgaenge), Decimal(0))
            warnungen.append(f"{rest} EUR der Rechnungssumme sind in der CSV nicht aufgeschlüsselt (Grundgebühren je "
                             "Karte) und werden als Gebühr ohne Kartenbezug erfasst. Für die Zuordnung zu den Karten "
                             "besser das PDF statt der CSV importieren (beide zusammen: die Gebühren der zweiten "
                             "Datei werden als bereits importiert übersprungen).")
    return ergebnis
