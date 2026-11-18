"""EVSE-ID (eMI3) normieren und das Land eines Ladevorgangs bestimmen – ohne Datenbank, ohne externe Dienste.

EVSE-ID: <Land>*<Betreiber>*E<Ladepunkt>, z. B. „AT*MER*E000001*02“. Rechnungen schreiben sie unterschiedlich
(„ATMERE000001*02“, „at*mer*e000001*02“, mit Leerzeichen) – normiert wird auf Großbuchstaben mit „*“ als Trenner.
Gekürzte Angaben wie „AT*MER“ ergeben keine EVSE-ID, aber Betreiber-ID und Land.

Land (ISO 3166-1 alpha-2), der Reihe nach:
1. Präfix der EVSE-ID,
2. Länderangabe der Rechnung (rohdaten["land"]: Ländername oder ISO-Code, z. B. DCS-Teilrechnung je Land),
3. Standardland des MSP (nur MSP, die ausschließlich im Inland abrechnen).
"""
import re
from dataclasses import dataclass

# Ländernamen (deutsch) ↔ ISO-Code; deckt die vorkommenden und die übrigen europäischen Länder ab
LAENDER = {
    "AT": "Österreich", "DE": "Deutschland", "CH": "Schweiz", "LI": "Liechtenstein", "IT": "Italien",
    "SI": "Slowenien", "HR": "Kroatien", "HU": "Ungarn", "SK": "Slowakei", "CZ": "Tschechien", "PL": "Polen",
    "DK": "Dänemark", "SE": "Schweden", "NO": "Norwegen", "FI": "Finnland", "EE": "Estland", "LV": "Lettland",
    "LT": "Litauen", "NL": "Niederlande", "BE": "Belgien", "LU": "Luxemburg", "FR": "Frankreich", "ES": "Spanien",
    "PT": "Portugal", "IE": "Irland", "GB": "Vereinigtes Königreich", "RO": "Rumänien", "BG": "Bulgarien",
    "GR": "Griechenland", "RS": "Serbien", "BA": "Bosnien und Herzegowina", "ME": "Montenegro", "AL": "Albanien",
    "MK": "Nordmazedonien", "MT": "Malta", "CY": "Zypern", "IS": "Island",
}
_NAMEN = {name.lower(): code for code, name in LAENDER.items()} | {"tschechische republik": "CZ", "england": "GB"}

# Parser, deren „Ladepunkt“ keine EVSE-ID ist (Wallbox-Seriennummer), und MSP, die nur im Inland abrechnen

RE_EVSE = re.compile(r"^([A-Z]{2})\*?([A-Z0-9]{3})\*?(E\*?[A-Z0-9][A-Z0-9*]*)$")
RE_BETREIBER = re.compile(r"^([A-Z]{2})\*([A-Z0-9]{3})(?:\*.*)?$")


@dataclass(frozen=True)
class Ortsdaten:
    evse_id: str | None = None
    betreiber_id: str | None = None  # z. B. „AT*MER“
    land: str | None = None  # ISO-Code


def evse_zerlegen(roh: str | None) -> Ortsdaten:
    """EVSE-ID normieren; liefert Betreiber und Land auch bei gekürzten Angaben („AT*MER“)."""
    text = re.sub(r"[\s\-_.:/]+", "", (roh or "").upper()).strip("*")
    text = re.sub(r"\*+", "*", text)
    if m := RE_EVSE.match(text):
        land, betreiber, rest = m.groups()
        if land in LAENDER:
            return Ortsdaten(f"{land}*{betreiber}*{rest.rstrip('*')}", f"{land}*{betreiber}", land)
    if (m := RE_BETREIBER.match(text)) and m.group(1) in LAENDER:
        return Ortsdaten(None, f"{m.group(1)}*{m.group(2)}", m.group(1))
    return Ortsdaten()


def cpo_land(code: str | None) -> str | None:
    """Land aus einer CPO-Kennung („AT*MER“ → „AT“); None bei Namen ohne Kennung (z. B. eigene Ladestationen)."""
    m = RE_BETREIBER.match(code or "")
    return m.group(1) if m and m.group(1) in LAENDER else None


def land_aus_text(text: str | None) -> str | None:
    """Ländername („Österreich“) oder ISO-Code („AT“) → ISO-Code."""
    text = (text or "").strip()
    if text.upper() in LAENDER:
        return text.upper()
    return _NAMEN.get(text.lower())


def ermitteln(parser_key: str | None, ladepunkt: str | None, rohdaten: dict | None) -> Ortsdaten:
    """EVSE-ID, Betreiber und Land eines Ladevorgangs aus Ladepunkt, Rechnungsangabe und den Eigenschaften des
    Parsers (liefert_evse, standardland – der Kern kennt keine konkreten Anbieter)."""
    from app.parsers import PARSER  # spät importiert: Parser nutzen ihrerseits Dienste
    parser = PARSER.get(parser_key or "")
    evse = evse_zerlegen(ladepunkt) if parser is None or parser.liefert_evse else Ortsdaten()
    land = (evse.land or land_aus_text((rohdaten or {}).get("land"))
            or (parser.standardland if parser else None))
    return Ortsdaten(evse.evse_id, evse.betreiber_id, land)


def landname(code: str | None) -> str:
    return LAENDER.get(code or "", code or "unbekannt")
