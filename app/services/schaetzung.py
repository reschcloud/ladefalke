"""kWh-Schätzung für Zeittarife ohne Energieangabe (z. B. Wien Energie).

geschätzte kWh = Standzeit × Leistung, höchstens die Obergrenze je Vorgang – getrennt nach AC und DC.
- Leistung: Σ kWh / Σ angesteckte Stunden aus den gemessenen Vorgängen (Ladeprofil), begrenzt auf die
  Nennleistung des Ladepunkts.
- Obergrenze: 95. Perzentil der gemessenen kWh je Vorgang („mehr lädt das Auto praktisch nie auf einmal“).
- Grundlage: eigene Daten des Fahrzeugs ab MINDESTANZAHL Vorgängen, sonst der Flottenschnitt (ebenfalls ab
  MINDESTANZAHL), sonst die Vorgaben aus den Einstellungen (Standard AC 11 kW, DC 90 kW, 50 kWh).
  Ohne bekanntes Fahrzeug: Flottenschnitt.
Die Werte berechnet services/ladeprofil.py (`schaetzgrundlage`); geschätzte kWh fließen dort nicht ein.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MINDESTANZAHL = 5
# Standardwerte der Vorgaben (in den Einstellungen änderbar; hier für Aufrufe ohne Datenbank, z. B. im Parser)
VORGABE_KW = {"AC": Decimal(11), "DC": Decimal(90)}
VORGABE_OBERGRENZE_KWH = Decimal(50)
QUELLEN = {"fahrzeug": "eigene Daten", "flotte": "Flottenschnitt", "vorgabe": "Vorgabe"}


@dataclass(frozen=True)
class Schaetzwert:
    """Grundlage der Schätzung für eine Stromart."""
    kw: Decimal  # kWh je angesteckter Stunde
    obergrenze_kwh: Decimal  # je Vorgang
    quelle: str = "vorgabe"  # Schlüssel aus QUELLEN
    anzahl: int = 0  # Vorgänge, aus denen die Werte stammen

    @property
    def quelle_text(self) -> str:
        text = QUELLEN[self.quelle]
        return f"{text} ({self.anzahl} Vorgänge)" if self.anzahl else text


def vorgabe(art: str) -> Schaetzwert:
    return Schaetzwert(VORGABE_KW[art], VORGABE_OBERGRENZE_KWH)


@dataclass
class Schaetzbasis:
    stunden: Decimal
    art: str  # "AC" oder "DC"
    nennleistung_kw: Decimal | None = None  # des Ladepunkts

    def als_rohdaten(self) -> dict:
        return {"standzeit_std": str(self.stunden), "art": self.art,
                "nennleistung_kw": str(self.nennleistung_kw) if self.nennleistung_kw else ""}


def basis_aus_rohdaten(rohdaten: dict | None) -> Schaetzbasis | None:
    """Schätzgrundlage eines gespeicherten Ladevorgangs (Felder aus `Schaetzbasis.als_rohdaten`)."""
    if not rohdaten or not rohdaten.get("standzeit_std") or rohdaten.get("art") not in ("AC", "DC"):
        return None
    try:
        nenn = rohdaten.get("nennleistung_kw")
        return Schaetzbasis(Decimal(rohdaten["standzeit_std"]), rohdaten["art"], Decimal(nenn) if nenn else None)
    except InvalidOperation:
        return None


def kwh_schaetzen(basis: Schaetzbasis, werte: dict[str, Schaetzwert] | None = None) -> Decimal:
    """`werte` je Stromart (aus `Schaetzgrundlage.fuer`); ohne Angabe gelten die Vorgaben."""
    w = (werte or {}).get(basis.art) or vorgabe(basis.art)
    kw = min(w.kw, basis.nennleistung_kw) if basis.nennleistung_kw else w.kw
    # Hier bewusst ohne app.parsers.basis (Zirkelimport: der Parser nutzt dieses Modul)
    return min(basis.stunden * kw, w.obergrenze_kwh).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
