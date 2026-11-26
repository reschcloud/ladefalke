"""USt. für bereits importierte Rechnungen nachtragen (SPEC Abschnitt 6 „Kosten und Umsatzsteuer“, Schritt 3).

Liest die gespeicherten Originaldateien öffentlicher Importe erneut mit ihrem Parser und überträgt Satz, Land und
Betrag der USt. auf die vorhandenen Ladevorgänge und Gebühren desselben Imports – zugeordnet über die externe ID,
sonst über Beginn, Ende und Nettobetrag (Gebühren: Bezeichnung, Karte und Nettobetrag). Vorhandene USt.-Angaben
bleiben unverändert; danach werden die tatsächlichen Kosten neu berechnet.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatierung import anzahl as anzahl_text
from app.models import Gebuehr, Import, ImportStatus, Ladeart, Ladevorgang
from app.parsers import PARSER
from app.services import kosten

log = logging.getLogger(__name__)


@dataclass
class Bericht:
    importe: int = 0
    vorgaenge: int = 0
    gebuehren: int = 0
    fehler: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        t = (f"USt. nachgetragen: {anzahl_text(self.vorgaenge, 'Ladevorgang')} und {anzahl_text(self.gebuehren, 'Gebühr')} "
             f"aus {anzahl_text(self.importe, 'Rechnung', dativ=True)}.")
        if self.fehler:
            t += f" Nicht lesbar: {len(self.fehler)} ({', '.join(self.fehler[:5])}{' …' if len(self.fehler) > 5 else ''})."
        return t


def _uebernehmen(ziel, quelle) -> bool:
    if quelle.ust_land is None and quelle.ust_betrag is None:
        return False
    ziel.ust_betrag, ziel.ust_satz, ziel.ust_land = quelle.ust_betrag, quelle.ust_satz, quelle.ust_land
    return True


def nachtragen(db: Session) -> Bericht:
    from app.services.importe import datei
    bericht = Bericht()
    importe = db.scalars(select(Import).where(Import.status == ImportStatus.abgeschlossen,
                                              Import.ladeart == Ladeart.oeffentlich,
                                              Import.parser_key.is_not(None))).all()
    for imp in importe:
        offen_v = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id, Ladevorgang.ust_land.is_(None),
                                                       Ladevorgang.ust_betrag.is_(None))).all()
        offen_g = db.scalars(select(Gebuehr).where(Gebuehr.import_id == imp.id, Gebuehr.ust_land.is_(None),
                                                   Gebuehr.ust_betrag.is_(None))).all()
        parser = PARSER.get(imp.parser_key)
        pfad = datei(imp)
        if not (offen_v or offen_g) or parser is None or not pfad.exists():
            continue
        try:
            ergebnis = parser.parsen_datei(pfad, imp.dateiname)
        except Exception as e:  # noqa: BLE001 – eine unlesbare Datei soll den Rest nicht aufhalten
            log.warning("USt. nachtragen: Import %s nicht lesbar: %s", imp.id, e)
            bericht.fehler.append(imp.dateiname)
            continue
        bericht.importe += 1
        nach_id, nach_zeit = {}, defaultdict(list)
        for v in ergebnis.vorgaenge:
            if v.externe_id:
                nach_id[v.externe_id] = v
            nach_zeit[(v.start, v.ende, v.kosten_netto)].append(v)
        vergeben: set[int] = set()  # jeder Vorgang der Datei höchstens einmal
        for lv in sorted(offen_v, key=lambda x: x.externe_id is None):  # erst die mit externer ID
            quelle = nach_id.get(lv.externe_id) if lv.externe_id else None
            if quelle is None:
                quelle = next((k for k in nach_zeit.get((lv.start, lv.ende, lv.kosten_netto), [])
                               if id(k) not in vergeben and not (k.externe_id and lv.externe_id)), None)
            if quelle is None or id(quelle) in vergeben:
                continue
            vergeben.add(id(quelle))
            if _uebernehmen(lv, quelle):
                bericht.vorgaenge += 1
        gebuehren = defaultdict(list)
        for g in ergebnis.gebuehren:
            gebuehren[(g.bezeichnung[:300], g.betrag_netto)].append(g)
        for g in offen_g:
            if (kandidaten := gebuehren.get((g.bezeichnung, g.betrag_netto))) and _uebernehmen(g, kandidaten.pop(0)):
                bericht.gebuehren += 1
    db.flush()
    kosten.neu_berechnen(db)
    return bericht


def beim_start() -> None:
    """Einmal nach dem Update (Migration 0031) im Hintergrund nachtragen; danach nur noch auf Knopfdruck."""
    import threading

    def ausfuehren() -> None:
        from datetime import date
        from app.db import SessionLocal
        from app.services import app_einstellungen
        try:
            with SessionLocal() as db:
                bericht = nachtragen(db)
                app_einstellungen.speichern(db, {"ust_nachgetragen": date.today().isoformat()})
                db.commit()
                log.info(bericht.text)
        except Exception:  # noqa: BLE001
            log.exception("USt. nachtragen fehlgeschlagen")

    from app.db import SessionLocal
    from app.models import Einstellung
    with SessionLocal() as db:
        if db.get(Einstellung, "ust_nachgetragen") is not None:
            return
        offen = db.scalar(select(Ladevorgang.id).where(Ladevorgang.ladeart == Ladeart.oeffentlich,
                                                       Ladevorgang.ust_land.is_(None),
                                                       Ladevorgang.ust_betrag.is_(None)).limit(1))
        if offen is None:  # nichts nachzutragen (z. B. neue Installation)
            return
    threading.Thread(target=ausfuehren, name="ust-nachtrag", daemon=True).start()
