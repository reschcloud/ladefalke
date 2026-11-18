"""Report „Zuordnungen am Stichtag“: wer an einem Tag welche Ladekarten und welches Fahrzeug hatte.

- Je aktive Fahrer:in eine Zeile (auch ohne Karte): Fahrzeug(e) am Stichtag und alle Karten – persönliche und die des
  Fahrzeugs, das sie an dem Tag hatte (bei Poolfahrzeugen bei jeder Fahrer:in) –, je MSP eine Spalte.
- Dazu Fahrzeuge ohne Fahrer:in (aktiv, am Stichtag niemandem zugeordnet) mit ihren Karten und aktive Karten ohne
  Zuordnung (Gastkarten gekennzeichnet).
- Spalten: nur MSP mit mindestens einer Karte im Report, nach Name.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Fahrer, Fahrzeug, Ladekarte, Msp, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services.zuordnung import gueltig_am


@dataclass
class KarteEintrag:
    karte: Ladekarte
    ueber: str | None = None  # Kennzeichen, wenn die Karte über das Fahrzeug zugeordnet ist


@dataclass
class Zeile:
    name: str
    zusatz: list[str] = field(default_factory=list)  # Kennzeichen (bei Personen) bzw. Modell (bei Fahrzeugen)
    karten: dict[int, list[KarteEintrag]] = field(default_factory=dict)  # msp_id → Karten

    def hinzu(self, eintrag: KarteEintrag) -> None:
        self.karten.setdefault(eintrag.karte.msp_id, []).append(eintrag)

    @property
    def leer(self) -> bool:
        return not self.karten


@dataclass
class Daten:
    stichtag: date
    msp: list[Msp]
    personen: list[Zeile]
    fahrzeuge_ohne: list[Zeile]
    karten_ohne: list[Zeile]  # eine Zeile mit allen Karten ohne Zuordnung

    @property
    def anzahl_karten(self) -> int:
        return len({e.karte.id for z in self.personen + self.fahrzeuge_ohne + self.karten_ohne
                    for liste in z.karten.values() for e in liste})


def _sortiert(karten: list[KarteEintrag]) -> list[KarteEintrag]:
    return sorted(karten, key=lambda e: (e.ueber is not None, (e.karte.bezeichnung or e.karte.kennung).lower()))


def berechnen(db: Session, stichtag: date) -> Daten:
    fz_zuordnungen = db.scalars(select(ZuordnungFahrerFahrzeug).where(gueltig_am(ZuordnungFahrerFahrzeug, stichtag))
                                .options(selectinload(ZuordnungFahrerFahrzeug.fahrzeug))).all()
    karten_zuordnungen = db.scalars(select(ZuordnungKarte).where(gueltig_am(ZuordnungKarte, stichtag))
                                    .options(selectinload(ZuordnungKarte.ladekarte))).all()
    fahrzeuge_je_fahrer: dict[int, list[Fahrzeug]] = {}
    fahrer_je_fahrzeug: dict[int, list[int]] = {}
    for z in fz_zuordnungen:
        fahrzeuge_je_fahrer.setdefault(z.fahrer_id, []).append(z.fahrzeug)
        fahrer_je_fahrzeug.setdefault(z.fahrzeug_id, []).append(z.fahrer_id)
    karten_je_fahrer: dict[int, list[Ladekarte]] = {}
    karten_je_fahrzeug: dict[int, list[Ladekarte]] = {}
    zugeordnet: set[int] = set()
    for z in karten_zuordnungen:
        zugeordnet.add(z.ladekarte_id)
        ziel = karten_je_fahrer if z.fahrer_id else karten_je_fahrzeug
        ziel.setdefault(z.fahrer_id or z.fahrzeug_id, []).append(z.ladekarte)

    personen = []
    for f in db.scalars(select(Fahrer).where(Fahrer.aktiv).order_by(Fahrer.name)):
        fahrzeuge = sorted(fahrzeuge_je_fahrer.get(f.id, []), key=lambda fz: fz.kennzeichen)
        zeile = Zeile(f.name, [fz.kennzeichen for fz in fahrzeuge])
        for k in karten_je_fahrer.get(f.id, []):
            zeile.hinzu(KarteEintrag(k))
        for fz in fahrzeuge:
            for k in karten_je_fahrzeug.get(fz.id, []):
                zeile.hinzu(KarteEintrag(k, fz.kennzeichen))
        personen.append(zeile)

    fahrzeuge_ohne = []
    for fz in db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv).order_by(Fahrzeug.kennzeichen)):
        if fz.id in fahrer_je_fahrzeug:
            continue
        zeile = Zeile(fz.kennzeichen, [fz.modell] if fz.modell else [])
        for k in karten_je_fahrzeug.get(fz.id, []):
            zeile.hinzu(KarteEintrag(k))
        fahrzeuge_ohne.append(zeile)

    ohne = Zeile("Ohne Zuordnung")
    for k in db.scalars(select(Ladekarte).where(Ladekarte.aktiv).order_by(Ladekarte.kennung)):
        if k.id not in zugeordnet:
            ohne.hinzu(KarteEintrag(k))

    for z in personen + fahrzeuge_ohne + [ohne]:
        z.karten = {m: _sortiert(liste) for m, liste in z.karten.items()}
    msp_ids = {m for z in personen + fahrzeuge_ohne + [ohne] for m in z.karten}
    msp = db.scalars(select(Msp).where(Msp.id.in_(msp_ids)).order_by(Msp.name)).all() if msp_ids else []
    return Daten(stichtag, list(msp), personen, fahrzeuge_ohne, [ohne] if not ohne.leer else [])
