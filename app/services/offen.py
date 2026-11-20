"""Offene Punkte für Menüleiste (roter Punkt) und Dashboard."""
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Fahrer, Fahrzeug, Gebuehr, Import, ImportStatus, Ladekarte, Ladeort, Ladevorgang, Msp
from app.services.app_einstellungen import laden as einstellungen_laden
from app.services.geokodierung import verzeichnis_vorhanden
from app.services import fristen, kartenpruefung, stromart
from app.services.ladeprofil import unplausibel_sql


def ohne_fahrzeug_bedingungen():
    """Ladevorgänge ohne Fahrzeug, außer Gastkarten (die bleiben bewusst unzugeordnet)."""
    return [Ladevorgang.fahrzeug_id.is_(None),
            or_(Ladevorgang.ladekarte_id.is_(None),
                Ladevorgang.ladekarte.has(Ladekarte.ist_gastkarte == false()))]


@dataclass(frozen=True)
class OffenePunkte:
    vorschauen: int
    ohne_fahrzeug: int
    gebuehren_ohne_karte: int
    unplausibel: int = 0  # Ladevorgänge mit unplausibler AC/DC-Angabe
    ladeorte: int = 0  # Ladeorte ohne Koordinaten bzw. nur auf das Land verortet
    ohne_stromart: int = 0  # Ladevorgänge ohne AC/DC-Angabe
    fristen: int = 0  # Leasing, Führerscheinkontrolle, Vignette, Streckenmaut FLEX (services/fristen.py)
    karten: int = 0  # Ladekarten je Fahrzeug: Anzahl bzw. außerhalb des Leasings (services/kartenpruefung.py)

    @property
    def zu_klaeren(self) -> int:
        return (self.ohne_fahrzeug + self.gebuehren_ohne_karte + self.unplausibel + self.ladeorte + self.ohne_stromart
                + self.fristen + self.karten)

    @property
    def beschreibung(self) -> str:
        """Tooltip des roten Punkts: nur Kategorien mit offenen Punkten, in der Reihenfolge der Aufgabenseite."""
        teile = [(self.fristen, "Fristen"), (self.karten, "Ladekarten je Fahrzeug"),
                 (self.ohne_fahrzeug, "Ladevorgänge ohne Fahrzeug"),
                 (self.gebuehren_ohne_karte, "Gebühren ohne Ladekarte"), (self.unplausibel, "unplausible AC/DC-Angaben"),
                 (self.ohne_stromart, "Ladevorgänge ohne AC/DC-Angabe"), (self.ladeorte, "Ladeorte ohne Koordinaten")]
        return ", ".join(f"{n} {text}" for n, text in teile if n)


def zaehlen(db: Session) -> OffenePunkte:
    def anzahl(model, *bedingungen) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*bedingungen))

    e = einstellungen_laden(db)
    return OffenePunkte(
        vorschauen=anzahl(Import, Import.status == ImportStatus.vorschau),
        ohne_fahrzeug=anzahl(Ladevorgang, *ohne_fahrzeug_bedingungen()),
        gebuehren_ohne_karte=anzahl(Gebuehr, Gebuehr.ladekarte_id.is_(None)),
        unplausibel=anzahl(Ladevorgang, unplausibel_sql(e)),
        ladeorte=anzahl(Ladeort, *ladeorte_offen_bedingungen()) if verzeichnis_vorhanden(db) else 0,
        ohne_stromart=anzahl(Ladevorgang, stromart.ohne_angabe()),
        fristen=len(fristen.ermitteln(db, e)),
        karten=len(kartenpruefung.ermitteln(db, e)))


def ladeorte_offen_bedingungen():
    """Ladeorte, die nicht auf der Karte erscheinen können: ohne Koordinaten oder nur Landesmitte."""
    return [or_(Ladeort.genauigkeit.is_(None), Ladeort.genauigkeit == "land")]


def bestand(db: Session) -> dict[str, int]:
    """Anzahlen im Menü unter „Stammdaten“: aktive Einträge (MSP: alle)."""
    def anzahl(model, *bedingungen) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*bedingungen))
    return {"msp": anzahl(Msp), "ladekarten": anzahl(Ladekarte, Ladekarte.aktiv),
            "fahrzeuge": anzahl(Fahrzeug, Fahrzeug.aktiv), "fahrer": anzahl(Fahrer, Fahrer.aktiv)}


def offene_punkte_laden(request: Request, db: Session = Depends(get_db)) -> None:
    """Dependency für alle angemeldeten Seiten: Zahlen für die Menüleiste, den Stichtag (Ladeende/Ladebeginn,
    für Beschriftungen) und das Theme in request.state ablegen."""
    request.state.offen = zaehlen(db)
    request.state.bestand = bestand(db)
    e = einstellungen_laden(db)
    request.state.stichtag = e.stichtag
    request.state.theme = request.state.benutzer.theme  # je Benutzer; base.html: data-theme am <html>


# ------------------------------------------------------------------ Report „Offene Aufgaben“ (services/reports.py)

def fuer_report(db: Session, max_zeilen: int = 30) -> dict:
    """Alles, was unter „Aufgaben“ offen ist, als Daten für den PDF-Report (Stand heute)."""
    from app.models import Msp
    from app.services import app_einstellungen, kartenanzeige, luecken
    from app.services.aufloesung import stichtag_spalte
    from app.services.ladeprofil import unplausibel_grund
    from sqlalchemy.orm import selectinload
    e = app_einstellungen.laden(db)
    tag = stichtag_spalte(e.stichtag)
    gruppen = db.execute(
        select(Ladevorgang.ladekarte_id, Ladevorgang.roh_kartenkennung, Msp.name, func.count(), func.sum(Ladevorgang.kwh),
               func.sum(Ladevorgang.kosten_netto), func.min(tag), func.max(tag))
        .join(Msp, Msp.id == Ladevorgang.msp_id).where(*ohne_fahrzeug_bedingungen())
        .group_by(Ladevorgang.ladekarte_id, Ladevorgang.roh_kartenkennung, Msp.name)
        .order_by(func.count().desc())).all()
    karten = {k.id: k for k in db.scalars(select(Ladekarte).where(Ladekarte.id.in_([g[0] for g in gruppen if g[0]]))
                                          .options(selectinload(Ladekarte.msp)))}
    unplausibel = db.scalars(select(Ladevorgang).where(unplausibel_sql(e))
                             .options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.msp),
                                      selectinload(Ladevorgang.fahrzeug)).order_by(tag.desc())).all()
    kartenanzeige.fuer_vorgaenge(db, unplausibel[:max_zeilen], e.stichtag)
    ladeorte = db.execute(
        select(Ladeort, func.count(Ladevorgang.id)).join(Ladevorgang, Ladevorgang.ladeort_id == Ladeort.id)
        .where(*ladeorte_offen_bedingungen()).group_by(Ladeort.id)
        .order_by(func.count(Ladevorgang.id).desc(), Ladeort.adresse)).all() if verzeichnis_vorhanden(db) else []
    return {
        "zahlen": zaehlen(db), "einst": e, "max_zeilen": max_zeilen,
        "fristen": fristen.ermitteln(db, e), "luecken": luecken.ermitteln(db),
        "karten_befunde": kartenpruefung.ermitteln(db, e),
        "gruppen": gruppen, "karten": karten,
        "gebuehren": db.scalars(select(Gebuehr).where(Gebuehr.ladekarte_id.is_(None))
                                .options(selectinload(Gebuehr.import_).selectinload(Import.msp))
                                .order_by(Gebuehr.id.desc())).all(),
        "unplausibel": [(v, unplausibel_grund(v, e)) for v in unplausibel],
        "ohne_stromart": db.execute(select(Msp.name, func.count(Ladevorgang.id))
                                    .join(Ladevorgang, Ladevorgang.msp_id == Msp.id).where(stromart.ohne_angabe())
                                    .group_by(Msp.name).order_by(func.count(Ladevorgang.id).desc())).all(),
        "ladeorte": ladeorte,
        "vorschauen": db.scalars(select(Import).where(Import.status == ImportStatus.vorschau).order_by(Import.id)).all(),
    }
