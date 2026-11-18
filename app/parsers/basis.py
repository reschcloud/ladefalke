"""Gemeinsame Schnittstelle aller Parser.

Ein Parser liest eine Datei und liefert ein `Ergebnis` – ohne Datenbankzugriff.
Kartenauflösung, Kostenberechnung (Strompreis) und Speichern übernimmt der Import-Service.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from app.models import KennungTyp

if TYPE_CHECKING:
    from app.services.schaetzung import Schaetzbasis

CENT = Decimal("0.01")
WH = Decimal("0.001")


def stromart_normieren(text: str | None) -> str | None:
    """„AC“, „DC“, „HPC“ (High Power Charging = DC) → "AC"/"DC"; alles andere → None (keine Angabe)."""
    t = (text or "").strip().upper()
    return "AC" if t == "AC" else "DC" if t in ("DC", "HPC") else None


def cent(betrag: Decimal) -> Decimal:
    return betrag.quantize(CENT, rounding=ROUND_HALF_UP)


def kwh_runden(kwh: Decimal) -> Decimal:
    return kwh.quantize(WH, rounding=ROUND_HALF_UP)


def auf_summe_verteilen(werte: list[Decimal], summe: Decimal) -> list[Decimal] | None:
    """Werte auf Cent runden, sodass ihre Summe exakt `summe` ergibt (Verfahren des größten Rests).

    Liefert None, wenn die Abweichung größer ist, als sich durch Rundung erklären lässt.
    """
    abgerundet = [w.quantize(CENT, rounding=ROUND_FLOOR) for w in werte]
    rest = int((summe - sum(abgerundet, Decimal(0))) / CENT)
    if not 0 <= rest <= len(werte):
        return None
    reihenfolge = sorted(range(len(werte)), key=lambda i: werte[i] - abgerundet[i], reverse=True)
    for i in reihenfolge[:rest]:
        abgerundet[i] += CENT
    return abgerundet


@dataclass
class Vorgang:
    kartenkennung: str | None
    kennung_typ: KennungTyp
    start: datetime
    ende: datetime
    kwh: Decimal
    kwh_geschaetzt: bool = False
    kosten_netto: Decimal | None = None  # None: Kosten = kWh × Strompreis des Imports
    externe_id: str | None = None
    vertragsnummer: str | None = None
    standort: str | None = None
    ladepunkt: str | None = None
    kartenbezeichnung: str | None = None  # Vorschlag, falls die Karte neu angelegt wird
    gastkarte: bool = False  # Vorschlag, falls die Karte neu angelegt wird
    stromart: str | None = None  # "AC" / "DC" – nur wenn die Rechnung es ausdrücklich angibt
    schaetzbasis: "Schaetzbasis | None" = None  # nur bei geschätzten kWh; Import schätzt mit Fahrzeugwerten neu
    rohdaten: dict = field(default_factory=dict)  # nur JSON-taugliche Werte


@dataclass
class Gebuehr:
    bezeichnung: str
    betrag_netto: Decimal
    kartenkennung: str | None = None
    vertragsnummer: str | None = None
    zeitraum_von: date | None = None
    zeitraum_bis: date | None = None
    kennung_typ: KennungTyp = KennungTyp.kartennummer  # falls die Karte neu angelegt wird
    kartenbezeichnung: str | None = None  # Vorschlag, falls die Karte neu angelegt wird


@dataclass
class Rechnung:
    nummer: str | None = None
    datum: date | None = None
    zeitraum_von: date | None = None
    zeitraum_bis: date | None = None
    summe_netto: Decimal | None = None


@dataclass
class Ergebnis:
    rechnung: Rechnung
    vorgaenge: list[Vorgang] = field(default_factory=list)
    gebuehren: list[Gebuehr] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)


class Parser(ABC):
    """Ein Rechnungsformat. Der Kern kennt keine konkreten Parser: alles Anbieterspezifische steht hier.
    Neue Parser: Modul in app/parsers/ mit einer Unterklasse – wird beim Start automatisch gefunden (__init__.py),
    der zugehörige MSP wird angelegt (services/parser_verwaltung.py)."""
    key: str  # entspricht msp.parser_key
    name: str
    # Eigenschaften des Formats (neutrale Vorgaben; Parser überschreiben nur, was abweicht)
    liefert_evse: bool = True  # False: Ladepunkt-Angabe ist keine EVSE-ID (z. B. Seriennummer eigener Wallboxen)
    standardland: str | None = None  # ISO-Code, wenn die Rechnung kein Land nennt (Inlandsanbieter)
    abrechnung_je_karte: bool = False  # Vorbelegung des MSP: monatliche Rechnung je Ladekarte (Vollständigkeit)
    msp_name: str | None = None  # Name des automatisch angelegten MSP (sonst `name`)
    standard_ladeart: str = "oeffentlich"  # Vorbelegung des MSP: „oeffentlich“ oder „privat“

    @abstractmethod
    def erkennen(self, pfad: Path) -> bool:
        """True, wenn die Datei zu diesem Parser passt (Inhalt, nicht nur Dateiname)."""

    @abstractmethod
    def parsen(self, pfad: Path) -> Ergebnis:
        ...

    def parsen_datei(self, pfad: Path, dateiname: str | None = None) -> Ergebnis:
        """Wie `parsen`, mit dem ursprünglichen Dateinamen (gespeichert wird die Datei unter ihrem Hash) – für
        Formate, die Angaben nur im Dateinamen haben (z. B. Abrechnungsmonat der EnBW-CSV)."""
        return self.parsen(pfad)


class ParserFehler(ValueError):
    pass


def pdf_text(pfad: Path, nur_erste_seite: bool = False) -> str:
    import pdfplumber

    with pdfplumber.open(pfad) as pdf:
        seiten = pdf.pages[:1] if nur_erste_seite else pdf.pages
        return "\n".join(s.extract_text() or "" for s in seiten)


def ist_pdf(pfad: Path) -> bool:
    with open(pfad, "rb") as f:
        return f.read(5) == b"%PDF-"


def pdf_text_pdfium(pfad: Path) -> str:
    """Text aller Seiten über PDFium – liest auch Schriften, bei denen pdfplumber Text verliert (z. B. Elli)."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(pfad)
    try:
        return "\n".join(seite.get_textpage().get_text_range() for seite in pdf).replace("\r\n", "\n")
    finally:
        pdf.close()


def zahl_flexibel(text: str) -> Decimal:
    """Zahl mit Dezimalkomma („36,05“, „1.234,5“) oder Dezimalpunkt („86.82“) – Dokumente mischen beides."""
    text = text.strip()
    if "," in text:
        return Decimal(text.replace(".", "").replace(",", "."))
    if text.count(".") > 1:
        raise ValueError(f"„{text}“ ist keine eindeutige Zahl.")
    return Decimal(text)
