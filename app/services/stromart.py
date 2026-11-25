"""AC/DC-Angabe je Ladevorgang: Herkunft, automatische Bestimmung und Vorgabe je MSP.

Herkunft (`Ladevorgang.stromart_quelle`) in der Reihenfolge des Vorrangs:
1. „rechnung“ – vom Parser,
2. „manuell“ – auf der Detailseite oder unter „Aufgaben“ gesetzt (auch bewusst „keine Angabe“),
3. „cpo“ – laut CPO-Liste (Einstellungen; Betreiber bietet nur AC oder nur DC an, z. B. Ionity = DC),
4. „leistung“ – aus der Ø-Leistung (kWh / angesteckte Dauer) über die Plausibilitätsgrenzen: ab der AC-Grenze DC,
   unter der DC-Grenze AC; nicht bei geschätzten kWh oder ohne Dauer,
5. „vorgabe“ – Vorgabe des MSP (beim Import oder nachträglich unter „Aufgaben“).
3 und 4 werden beim Import bestimmt und rückwirkend neu bestimmt (`neu_bestimmen`), wenn sich CPO-Liste oder Grenzen
ändern; 1 und 2 bleiben dabei immer unangetastet. Eine von 3/4 verdrängte MSP-Vorgabe bleibt in den Rohdaten
(`stromart_vorgabe`) und gilt wieder, wenn keine Regel mehr greift. Gesetzte Werte zählen wie Rechnungsangaben
(Ladeprofil, Schätzgrundlage, Plausibilität). Ohne Angabe erscheinen Vorgänge unter „Aufgaben“.
"""
import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import CpoStromart, Ladevorgang

STROMARTEN = ("AC", "DC")
QUELLEN = {"rechnung": "laut Rechnung", "manuell": "manuell gesetzt", "cpo": "laut CPO-Liste",
           "leistung": "aus Ø-Leistung", "vorgabe": "Vorgabe des MSP"}
AUTOMATISCH = ("cpo", "leistung", "vorgabe")  # darf neu bestimmt werden


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
    if v.stromart_quelle != "manuell":  # beim ersten manuellen Setzen sichern (automatisch Bestimmtes zählt nicht)
        original = v.stromart if v.stromart_quelle not in AUTOMATISCH else None
        v.rohdaten = {**(v.rohdaten or {}), "stromart_rechnung": original}
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


# ------------------------------------------------------------------ automatisch: CPO-Liste und Ø-Leistung

def cpo_schluessel(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


RE_KENNUNG = re.compile(r"^[A-Za-z]{2}\*([A-Za-z0-9]{3})$")
RE_OHNE_LAND = re.compile(r"^\*?([A-Za-z0-9]{3})$")


def kennung_normieren(text: str) -> str | None:
    """Eingabe der CPO-Liste → „AT*IOY“ (Kennung mit Land), „IOY“ (ohne Land: gilt in allen Ländern) oder der Text."""
    from app.services.cpo import normieren
    text = " ".join((text or "").split())
    if m := RE_OHNE_LAND.match(text):
        return m.group(1).upper()
    return normieren(text)


def cpo_eintrag(regeln: dict[str, CpoStromart], betreiber_id: str | None) -> CpoStromart | None:
    """Eintrag der CPO-Liste für einen Betreiber: genaue Kennung bzw. Name, sonst die Kennung ohne Land („IOY“)."""
    if not betreiber_id:
        return None
    if eintrag := regeln.get(cpo_schluessel(betreiber_id)):
        return eintrag
    if m := RE_KENNUNG.match(betreiber_id.strip()):
        return regeln.get(cpo_schluessel(m.group(1)))
    return None


def cpo_regeln(db: Session) -> dict[str, CpoStromart]:
    """CPO-Liste als Nachschlagetabelle: Kennung bzw. Name (Groß-/Kleinschreibung egal) → Eintrag."""
    regeln = {}
    for eintrag in db.scalars(select(CpoStromart).order_by(CpoStromart.name)):
        regeln.setdefault(cpo_schluessel(eintrag.name), eintrag)
        for kennung in eintrag.kennungen or []:
            regeln[cpo_schluessel(kennung)] = eintrag
    return regeln


def leistung_kw(kwh, geschaetzt: bool, start: datetime | None, ende: datetime | None) -> Decimal | None:
    """Ø-Leistung in kW (kWh / angesteckte Dauer); None bei geschätzten kWh, ohne kWh oder ohne Dauer."""
    if geschaetzt or not kwh or kwh <= 0 or start is None or ende is None or ende <= start:
        return None
    return Decimal(kwh) / (Decimal((ende - start).total_seconds()) / 3600)


def bestimmen(betreiber_id: str | None, kw: Decimal | None, regeln: dict[str, CpoStromart], e) \
        -> tuple[str, str, str] | None:
    """Stromart ohne Rechnungsangabe: zuerst CPO-Liste, dann Ø-Leistung. Liefert (Stromart, Quelle, Begründung)
    oder None, wenn keine Regel greift (dann gilt die Vorgabe des MSP, falls es eine gibt)."""
    if eintrag := cpo_eintrag(regeln, betreiber_id):
        return eintrag.stromart, "cpo", f"CPO-Liste: {eintrag.name}"
    art, grund = vorschlag(kw, e)
    if art:
        return art, "leistung", grund
    return None


def neu_bestimmbar():
    """SQL-Bedingung: Stromart darf automatisch (neu) bestimmt werden – ohne Angabe oder automatisch gesetzt."""
    return or_(ohne_angabe(), Ladevorgang.stromart_quelle.in_(AUTOMATISCH))


def neu_bestimmen(db: Session, *bedingungen) -> int:
    """Stromart aller Vorgänge ohne Rechnungsangabe und ohne manuelle Entscheidung neu bestimmen (CPO-Liste, dann
    Ø-Leistung, sonst die bisherige Vorgabe des MSP). Liefert die Anzahl geänderter Vorgänge. Einzeln über die Objekte,
    damit Neuschätzung und Protokoll (Sammeleintrag) beim Commit greifen."""
    from app.services.app_einstellungen import laden
    e, regeln = laden(db), cpo_regeln(db)
    anzahl = 0
    for v in db.scalars(select(Ladevorgang).where(
            neu_bestimmbar(), *bedingungen)):
        roh = dict(v.rohdaten or {})
        if v.stromart_quelle == "vorgabe":  # Vorgabe des MSP merken – sie gilt wieder, wenn keine Regel greift
            roh["stromart_vorgabe"] = v.stromart
        r = bestimmen(v.betreiber_id, leistung_kw(v.kwh, v.kwh_geschaetzt, v.start, v.ende), regeln, e)
        if r:
            neu = r[:2]
        elif roh.get("stromart_vorgabe") in STROMARTEN:
            neu = (roh["stromart_vorgabe"], "vorgabe")
        else:
            neu = (None, None)
        if (v.stromart, v.stromart_quelle) != neu:
            v.stromart, v.stromart_quelle = neu
            anzahl += 1
        if roh != (v.rohdaten or {}):
            v.rohdaten = roh
    return anzahl


def betroffen(db: Session) -> int:
    """Anzahl der Vorgänge, deren Stromart automatisch bestimmt wird oder werden könnte (für den Knopf)."""
    return db.scalar(select(func.count()).select_from(Ladevorgang).where(neu_bestimmbar())) or 0
