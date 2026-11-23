"""Spritpreise aus dem EU Weekly Oil Bulletin (Europäische Kommission, Generaldirektion Energie).

Die Historien-Datei (XLSX, ab 2005) enthält im Blatt „Prices with taxes“ je Montag die Verbraucherpreise inklusive
Steuern je Land und Kraftstoff, Spalten wie „AT_price_with_tax_diesel“ bzw. „AT_price_with_tax_euro95“, in € je
1.000 l. Übernommen werden Monatsmittel für das Land des Unternehmens und die gewählte Kraftstoffart – je Monat ein
Spritpreis „gültig ab dem 1.“ (Quelle „oil_bulletin“), ab dem ersten Monat mit Ladungen (spätestens ab 2020).
Händisch eingetragene Spritpreise an anderen Tagen bleiben; einer am Monatsersten wird überschrieben.

Laden: automatisch einmal pro Woche (Einstellung, abschaltbar; einzige Verbindung der App nach außen außer Mail und
Syslog), auf Knopfdruck oder per Hochladen der Datei.
"""
import io
import json
import logging
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Einstellung, Ladevorgang, Spritpreis

log = logging.getLogger(__name__)

URL = "https://energy.ec.europa.eu/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en"
SEITE = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
ARTEN = {"diesel": "Diesel", "euro95": "Benzin (Euro-Super 95)"}
BLATT = "Prices with taxes"
MAX_BYTES = 30 * 1024 * 1024
FRUEHESTENS = date(2020, 1, 1)
QUELLE = "oil_bulletin"
STATUS = "oil_bulletin_status"  # Einstellung: JSON {zeit, ok, text}


class OilBulletinFehler(ValueError):
    pass


@dataclass
class Ergebnis:
    monate: int
    von: date | None
    bis: date | None

    @property
    def text(self) -> str:
        if not self.monate:
            return "Keine Monatswerte übernommen."
        return f"{self.monate} Monatswerte übernommen ({self.von:%m/%Y} – {self.bis:%m/%Y})."


def monatsmittel(inhalt: bytes, land: str, art: str) -> dict[date, Decimal]:
    """Monatsmittel (€/l) der Wochenwerte für Land und Kraftstoff aus der XLSX-Datei."""
    import openpyxl
    if len(inhalt) > MAX_BYTES:
        raise OilBulletinFehler("Die Datei ist zu groß.")
    try:
        mappe = openpyxl.load_workbook(io.BytesIO(inhalt), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 – kein gültiges XLSX
        raise OilBulletinFehler("Keine gültige Excel-Datei des Oil Bulletin.") from e
    if BLATT not in mappe.sheetnames:
        raise OilBulletinFehler(f"Blatt „{BLATT}“ fehlt – ist das die Historien-Datei des Oil Bulletin?")
    spalte_name = f"{land}_price_with_tax_{art}"
    spalte = None
    werte: dict[date, list[Decimal]] = defaultdict(list)
    for zeile in mappe[BLATT].iter_rows(values_only=True):
        if spalte is None:
            if spalte_name in zeile:
                spalte = zeile.index(spalte_name)
            continue
        tag, wert = (zeile[0] if zeile else None), (zeile[spalte] if len(zeile) > spalte else None)
        if isinstance(tag, datetime) and isinstance(wert, (int, float)) and wert > 0:
            werte[tag.date().replace(day=1)].append(Decimal(str(wert)) / 1000)
    mappe.close()
    if spalte is None:
        raise OilBulletinFehler(f"Spalte „{spalte_name}“ nicht gefunden (Land oder Kraftstoff nicht enthalten).")
    return {m: (sum(w, Decimal(0)) / len(w)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            for m, w in sorted(werte.items())}


def uebernehmen(db: Session, inhalt: bytes) -> Ergebnis:
    """Monatsmittel als Spritpreise speichern (siehe Moduldoku)."""
    from app.services import app_einstellungen
    e = app_einstellungen.laden(db)
    mittel = monatsmittel(inhalt, e.firmenland, e.sprit_art)
    erste = db.scalar(select(func.min(Ladevorgang.ende)))
    ab = max(FRUEHESTENS, erste.date().replace(day=1)) if erste else FRUEHESTENS
    mittel = {m: p for m, p in mittel.items() if m >= ab}
    vorhanden = {s.gueltig_ab: s for s in db.scalars(select(Spritpreis).where(Spritpreis.gueltig_ab.in_(mittel)))}
    for monat, preis in mittel.items():
        if (s := vorhanden.get(monat)) is not None:
            s.preis, s.quelle = preis, QUELLE
        else:
            db.add(Spritpreis(gueltig_ab=monat, preis=preis, quelle=QUELLE))
    db.flush()
    return Ergebnis(len(mittel), min(mittel) if mittel else None, max(mittel) if mittel else None)


def herunterladen() -> bytes:
    anfrage = urllib.request.Request(URL, headers={"User-Agent": "Ladefalke (Spritpreise, Weekly Oil Bulletin)"})
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:  # noqa: S310 – feste https-Adresse
        inhalt = antwort.read(MAX_BYTES + 1)
    if len(inhalt) > MAX_BYTES:
        raise OilBulletinFehler("Die heruntergeladene Datei ist zu groß.")
    return inhalt


def status(db: Session) -> dict:
    e = db.get(Einstellung, STATUS)
    try:
        return json.loads(e.wert) if e and e.wert else {}
    except ValueError:
        return {}


def status_schreiben(db: Session, ok: bool, text: str, quelle: str) -> None:
    from app.services import app_einstellungen
    app_einstellungen.speichern(db, {STATUS: json.dumps(
        {"zeit": datetime.now().isoformat(timespec="seconds"), "ok": ok, "text": text, "quelle": quelle},
        ensure_ascii=False)})


def laden_und_uebernehmen(db: Session, quelle: str = "download", inhalt: bytes | None = None) -> Ergebnis:
    """Herunterladen (bzw. `inhalt` einer hochgeladenen Datei) und übernehmen; Status in den Einstellungen."""
    try:
        ergebnis = uebernehmen(db, inhalt if inhalt is not None else herunterladen())
    except Exception as e:
        db.rollback()
        text = str(e) if isinstance(e, OilBulletinFehler) else f"{type(e).__name__}: {e}"
        status_schreiben(db, False, text, quelle)
        db.commit()
        raise
    status_schreiben(db, True, ergebnis.text, quelle)
    db.commit()
    return ergebnis


def faellig(st: dict, jetzt: datetime) -> bool:
    """Wöchentlich nach einem Erfolg, täglich nach einem Fehlschlag."""
    if not st.get("zeit"):
        return True
    zuletzt = datetime.fromisoformat(st["zeit"])
    return jetzt - zuletzt >= (timedelta(days=7) if st.get("ok") else timedelta(days=1))


def automatisch_pruefen() -> None:
    """Vom Taktgeber der App minütlich aufgerufen: fälligen Download ausführen (wenn eingeschaltet)."""
    from app.db import SessionLocal
    from app.services import app_einstellungen
    with SessionLocal() as db:
        e = app_einstellungen.laden(db)
        if not (e.feature_tank and e.oil_bulletin_auto) or not faellig(status(db), datetime.now()):
            return
        try:
            ergebnis = laden_und_uebernehmen(db, "automatisch")
            log.info("Oil Bulletin: %s", ergebnis.text)
        except Exception:  # noqa: BLE001 – Status zeigt den Fehler, nächster Versuch morgen
            log.warning("Oil Bulletin: automatischer Download fehlgeschlagen", exc_info=True)
