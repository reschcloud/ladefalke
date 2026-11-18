"""AC/DC-Angabe je Ladevorgang: Herkunft und Vorgabe je MSP.

Herkunft (`Ladevorgang.stromart_quelle`): „rechnung“ (vom Parser), „vorgabe“ (Standard des MSP, beim Import oder
nachträglich angewendet), „manuell“ (auf der Detailseite gesetzt, auch „keine Angabe“). Gesetzte Werte zählen wie
Rechnungsangaben (Ladeprofil, Schätzgrundlage, Plausibilität). Ohne Angabe ist ein Vorgang, solange weder Rechnung
noch Vorgabe noch eine manuelle Entscheidung etwas festlegt – solche Vorgänge erscheinen unter „Aufgaben“.
"""
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import Ladevorgang

STROMARTEN = ("AC", "DC")
QUELLEN = {"rechnung": "laut Rechnung", "vorgabe": "Vorgabe des MSP", "manuell": "manuell gesetzt"}


def wert(text: str | None) -> str | None:
    """Formularwert → „AC“, „DC“ oder None (unbekannt)."""
    return text if text in STROMARTEN else None


def ohne_angabe():
    """SQL-Bedingung: keine AC/DC-Angabe und keine manuelle Entscheidung („keine Angabe“ bewusst gesetzt)."""
    return and_(Ladevorgang.stromart.is_(None), Ladevorgang.stromart_quelle.is_(None))


def vorschlag(kw, e) -> tuple[str | None, str]:
    """Vorschlag nach Ø-Leistung (kWh / angesteckte Dauer) über die Plausibilitätsgrenzen der Einstellungen `e`:
    ab der AC-Grenze DC (als AC unplausibel), unter der DC-Grenze AC (als DC unplausibel), dazwischen keiner.
    Nur ein Vorschlag – übernommen wird er erst durch ausdrückliches Setzen. Liefert (Stromart, Begründung)."""
    if kw is None:
        return None, "keine Leistung (geschätzte kWh oder keine Dauer)"
    if kw >= e.plausibel_ac_max_kw:
        return "DC", f"Ø {kw:.1f} kW ≥ {e.plausibel_ac_max_kw} kW – als AC unplausibel".replace(".", ",")
    if kw < e.plausibel_dc_min_kw:
        return "AC", f"Ø {kw:.1f} kW < {e.plausibel_dc_min_kw} kW – als DC unplausibel".replace(".", ",")
    return None, f"Ø {kw:.1f} kW zwischen den Grenzen – kein sicherer Vorschlag".replace(".", ",")


def manuell_setzen(v: Ladevorgang, art: str | None) -> None:
    """AC/DC (oder bewusst keine Angabe) manuell setzen; die Angabe der Rechnung bleibt in den Rohdaten erhalten."""
    if v.stromart_quelle != "manuell":  # beim ersten manuellen Setzen sichern (bei Vorgabe gab es keine)
        v.rohdaten = {**(v.rohdaten or {}), "stromart_rechnung": v.stromart if v.stromart_quelle != "vorgabe" else None}
    v.stromart, v.stromart_quelle = wert(art), "manuell"


def vorgabe_anwenden(db: Session, art: str | None, *bedingungen) -> int:
    """Stromart `art` für alle Vorgänge ohne Angabe setzen, die `bedingungen` erfüllen. Liefert die Anzahl.
    Einzeln über die Objekte, damit die Neuschätzung beim Commit ausgelöst wird (services/neuschaetzung.py)."""
    if art not in STROMARTEN:
        return 0
    anzahl = 0
    for v in db.scalars(select(Ladevorgang).where(ohne_angabe(), *bedingungen)):
        v.stromart, v.stromart_quelle = art, "vorgabe"
        anzahl += 1
    return anzahl
