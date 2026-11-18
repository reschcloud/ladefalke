"""Deutsche Darstellung und Eingabe von Datum und Zahlen (TT.MM.JJJJ, Dezimalkomma)."""
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def datum(wert: date | datetime | None) -> str:
    return wert.strftime("%d.%m.%Y") if wert else ""


def datumzeit(wert: datetime | None) -> str:
    return wert.strftime("%d.%m.%Y %H:%M") if wert else ""


def zahl(wert: Decimal | int | float | None, stellen: int = 2) -> str:
    """1234.5 -> '1.234,50' – kaufmännisch gerundet (72,5 -> 73; Pythons Formatierung allein rundet 72,5 -> 72)."""
    if wert is None:
        return ""
    gerundet = Decimal(wert).quantize(Decimal(1).scaleb(-stellen), rounding=ROUND_HALF_UP)
    text = f"{gerundet:,.{stellen}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def dauer(start: datetime | None, ende: datetime | None) -> str:
    """Ladedauer (angesteckt): „45 min“, „1:05 h“, ab einem Tag „2 T 3 h“; ohne Zeiten „–“."""
    if start is None or ende is None or ende < start:
        return "–"
    minuten = round((ende - start).total_seconds() / 60)
    if minuten < 60:
        return f"{minuten} min"
    if minuten < 24 * 60:
        return f"{minuten // 60}:{minuten % 60:02d} h"
    return f"{minuten // 1440} T {round(minuten % 1440 / 60)} h"


def minuten(wert: int | None) -> str:
    """Dauer in Minuten: 45 → „45 min“, 65 → „1:05 h“; None → „–“."""
    if wert is None:
        return "–"
    return f"{wert // 60}:{wert % 60:02d} h" if wert >= 60 else f"{wert} min"


def monat(wert: date | None) -> str:
    """date(2026, 9, 1) -> '09/2026'"""
    return wert.strftime("%m/%Y") if wert else ""


def prozent(anteil: Decimal | None, stellen: int = 0) -> str:
    """Anteil 0–1 -> '45 %'"""
    return f"{zahl(Decimal(anteil) * 100, stellen)} %" if anteil is not None else "–"


def geld(wert: Decimal | None) -> str:
    return f"{zahl(wert, 2)} €" if wert is not None else ""


def zeitraum(ab: date, bis: date | None) -> str:
    return f"{datum(ab)} – {datum(bis) if bis else 'offen'}"


def decimal_lesen(text: str | None) -> Decimal | None:
    """'1.234,56' oder '0,30' oder '0.30' oder '1.000' (Tausenderpunkt) -> Decimal. Leer -> None.

    Ohne Komma ist ein Punkt der Dezimalpunkt – außer die Zahl sieht wie mit Tausenderpunkten geschrieben aus
    (1–3 Ziffern ohne führende 0, dann Gruppen zu je drei: „1.000“, „12.500“). So lassen sich die angezeigten
    Werte (deutsch formatiert, z. B. „1.000“) unverändert wieder speichern."""
    text = (text or "").strip().replace(" ", "").replace("€", "")
    if not text:
        return None
    if "," in text or re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", text):
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"„{text}“ ist keine gültige Zahl.") from None


def datum_lesen(text: str | None) -> date | None:
    """'2026-11-04' (HTML-Datumsfeld) oder '04.11.2026' -> date. Leer -> None."""
    text = (text or "").strip()
    if not text:
        return None
    for muster in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, muster).date()
        except ValueError:
            pass
    raise ValueError(f"„{text}“ ist kein gültiges Datum (TT.MM.JJJJ).")


def monat_lesen(text: str | None) -> date | None:
    """'2026-09' (HTML-Monatsfeld), '09/2026' oder '09.2026' -> Monatserster. Leer -> None."""
    text = (text or "").strip()
    if not text:
        return None
    for muster in ("%Y-%m", "%m/%Y", "%m.%Y"):
        try:
            return datetime.strptime(text, muster).date().replace(day=1)
        except ValueError:
            pass
    raise ValueError(f"„{text}“ ist kein gültiger Monat (MM/JJJJ).")


EMAIL_MUSTER = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def email_lesen(text: str) -> str | None:
    """E-Mail-Adresse aus einer Eingabe (Leerzeichen entfernt); leer → None; ungültig → ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > 254 or not EMAIL_MUSTER.fullmatch(text):
        raise ValueError("Bitte eine gültige E-Mail-Adresse angeben (z. B. name@example.com).")
    return text


def flagge(land: str | None) -> str:
    """Landesflagge als Emoji aus dem ISO-Code („AT“ → 🇦🇹); leer bei unbekanntem Code. Nur für die Oberfläche."""
    if not land or len(land) != 2 or not land.isascii() or not land.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(z) - ord("A")) for z in land.upper())
