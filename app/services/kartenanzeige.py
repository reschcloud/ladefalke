"""Anzeigename einer Ladekarte: wer sie im Zeitraum (bzw. am Ladetag) hatte, statt der langen Kennung.

- Person, der die Karte zugeordnet war; bei Fahrzeugkarten das Kennzeichen mit der Person, die das Fahrzeug in
  dieser Zeit gefahren ist; Gastkarten „Gäste“; ohne Zuordnung die (gekürzte) Bezeichnung bzw. Kennung.
- Mehrere Zuordnungen im Zeitraum: vorne die mit den meisten Ladevorgängen, die anderen als „+n“ (Namen im Tooltip).
- Der MSP steht als Etikett daneben (Makro `karte` in _makros.html bzw. reports/_basis.html), die Kennung im
  Tooltip. Haben zwei angezeigte Karten denselben Namen beim selben MSP, kommt das Ende der Kennung dazu („…123-4“).
- Innerhalb einer Person (Fahrer:innen-Report, CPO-Details) wäre der eigene Name doppelt: `relativ()` zeigt dann
  „persönlich“ bzw. das Kennzeichen.
Stammdaten-Seiten (Kartenliste, Zuordnungen, Import-Vorschau) zeigen weiterhin die Kennung – dort geht es ums
Identifizieren der Karte.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Ladekarte, Ladevorgang, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services.aufloesung import stichtag

MAX_LAENGE = 32


@dataclass
class Anzeige:
    karte: Ladekarte
    name: str  # Hauptname
    art: str  # person | fahrzeug | gast | ohne
    msp: str
    kennzeichen: str | None = None  # bei Fahrzeugkarten
    personen: list[str] = field(default_factory=list)  # Person(en) der Hauptzuordnung
    fahrer_id: int | None = None  # bei persönlicher Karte
    weitere: list[str] = field(default_factory=list)  # weitere Zuordnungen im Zeitraum
    zusatz: str | None = None  # Ende der Kennung, falls der Name sonst mehrdeutig ist

    @property
    def tooltip(self) -> str:
        teile = [f"{self.msp}: {self.karte.kennung}"]
        if self.karte.bezeichnung and self.karte.bezeichnung != self.karte.kennung:
            teile.append(self.karte.bezeichnung)
        if self.weitere:
            teile.append("im Zeitraum auch: " + ", ".join(self.weitere))
        if not self.karte.aktiv:
            teile.append("inaktiv")
        return " · ".join(teile)

    @property
    def text(self) -> str:
        """Als reiner Text (Exporte, Tabellen-Schlüssel): „Name …123-4 (MSP)“."""
        return f"{self.name}{' …' + self.zusatz if self.zusatz else ''} ({self.msp})"

    def relativ(self, fahrer_id: int | None) -> str:
        """Name aus Sicht einer Person: die eigene persönliche Karte heißt „persönlich“, Fahrzeugkarten das Kennzeichen."""
        if fahrer_id is not None and self.art == "person" and self.fahrer_id == fahrer_id:
            return "persönlich"
        if self.art == "fahrzeug" and fahrer_id is not None:
            return self.kennzeichen or self.name
        return self.name


def _kurz(text: str) -> str:
    return text if len(text) <= MAX_LAENGE else text[:MAX_LAENGE - 1].rstrip() + "…"


def _ende(kennung: str) -> str:
    return kennung[-5:].lstrip("-_ ") if len(kennung) > 5 else kennung


kurz = _ende  # Ende der Kennung, z. B. für Beschriftungen „Karte …123-4“


def _ohne(karte: Ladekarte) -> Anzeige:
    if karte.ist_gastkarte:
        return Anzeige(karte, "Gäste", "gast", karte.msp.name)
    return Anzeige(karte, _kurz(karte.bezeichnung or karte.kennung), "ohne", karte.msp.name)


def _ueberlappt(ab: date, bis: date | None, von: date, bis_z: date) -> bool:
    return ab <= bis_z and (bis is None or bis >= von)


class _Daten:
    """Zuordnungen der Karten und der beteiligten Fahrzeuge, einmal geladen."""

    def __init__(self, db: Session, karten_ids: set[int], von: date, bis: date):
        self.karten: dict[int, list[ZuordnungKarte]] = defaultdict(list)
        self.fahrzeuge: dict[int, list[ZuordnungFahrerFahrzeug]] = defaultdict(list)
        if not karten_ids:
            return
        for z in db.scalars(select(ZuordnungKarte).where(
                ZuordnungKarte.ladekarte_id.in_(karten_ids), ZuordnungKarte.gueltig_ab <= bis,
                or_(ZuordnungKarte.gueltig_bis.is_(None), ZuordnungKarte.gueltig_bis >= von))
                .options(selectinload(ZuordnungKarte.fahrer), selectinload(ZuordnungKarte.fahrzeug))
                .order_by(ZuordnungKarte.gueltig_ab)):
            self.karten[z.ladekarte_id].append(z)
        fz_ids = {z.fahrzeug_id for zs in self.karten.values() for z in zs if z.fahrzeug_id}
        if fz_ids:
            for z in db.scalars(select(ZuordnungFahrerFahrzeug).where(
                    ZuordnungFahrerFahrzeug.fahrzeug_id.in_(fz_ids), ZuordnungFahrerFahrzeug.gueltig_ab <= bis,
                    or_(ZuordnungFahrerFahrzeug.gueltig_bis.is_(None), ZuordnungFahrerFahrzeug.gueltig_bis >= von))
                    .options(selectinload(ZuordnungFahrerFahrzeug.fahrer)).order_by(ZuordnungFahrerFahrzeug.gueltig_ab)):
                self.fahrzeuge[z.fahrzeug_id].append(z)

    def fahrer_des_fahrzeugs(self, fahrzeug_id: int, von: date, bis: date) -> list[str]:
        return list(dict.fromkeys(z.fahrer.name for z in self.fahrzeuge.get(fahrzeug_id, [])
                                  if _ueberlappt(z.gueltig_ab, z.gueltig_bis, von, bis)))

    def anzeige(self, karte: Ladekarte, von: date, bis: date, gewichte: Counter | None = None) -> Anzeige:
        """Anzeige für [von, bis]; `gewichte`: Ladevorgänge je Zuordnungs-ID (Hauptzuordnung = die meisten)."""
        if karte.ist_gastkarte:
            return _ohne(karte)
        zs = [z for z in self.karten.get(karte.id, []) if _ueberlappt(z.gueltig_ab, z.gueltig_bis, von, bis)]
        if not zs:
            return _ohne(karte)
        gewichte = gewichte or Counter()
        haupt = max(zs, key=lambda z: (gewichte[z.id], z.gueltig_ab))  # Gleichstand: die spätere
        beschriftungen = []
        for z in zs:
            if z.fahrer_id:
                beschriftungen.append((z, z.fahrer.name, [z.fahrer.name]))
            else:
                a = max(z.gueltig_ab, von)
                b = min(z.gueltig_bis or bis, bis)
                personen = self.fahrer_des_fahrzeugs(z.fahrzeug_id, a, b)
                text = z.fahrzeug.kennzeichen + (f" ({', '.join(personen[:2])}{' …' if len(personen) > 2 else ''})"
                                                 if personen else "")
                beschriftungen.append((z, text, personen))
        name = next(t for z, t, _ in beschriftungen if z is haupt)
        personen = next(p for z, _, p in beschriftungen if z is haupt)
        weitere = list(dict.fromkeys(t for z, t, _ in beschriftungen if z is not haupt and t != name))
        return Anzeige(karte, name, "person" if haupt.fahrer_id else "fahrzeug", karte.msp.name,
                       kennzeichen=haupt.fahrzeug.kennzeichen if haupt.fahrzeug_id else None, personen=personen,
                       fahrer_id=haupt.fahrer_id, weitere=weitere)


def eindeutig_machen(anzeigen) -> None:
    """Gleicher Name beim selben MSP: das Ende der Kennung dazu."""
    anzeigen = list(anzeigen)
    zaehler = Counter((a.name, a.msp) for a in anzeigen)
    for a in anzeigen:
        if zaehler[(a.name, a.msp)] > 1:
            a.zusatz = _ende(a.karte.kennung)


def fuer_zeitraum(db: Session, karten, von: date, bis: date) -> dict[int, Anzeige]:
    """Anzeige je Karten-ID für einen Zeitraum; Hauptzuordnung = die mit den meisten Ladevorgängen darin."""
    karten = {k.id: k for k in karten if k is not None}
    daten = _Daten(db, set(karten), von, bis)
    gewichte: dict[int, Counter] = defaultdict(Counter)
    mehrfach = [kid for kid, zs in daten.karten.items() if len(zs) > 1]
    if mehrfach:
        for kid, start, ende in db.execute(select(Ladevorgang.ladekarte_id, Ladevorgang.start, Ladevorgang.ende)
                                           .where(Ladevorgang.ladekarte_id.in_(mehrfach))):
            tag = stichtag(start)
            if von <= tag <= bis:
                for z in daten.karten[kid]:
                    if _ueberlappt(z.gueltig_ab, z.gueltig_bis, tag, tag):
                        gewichte[kid][z.id] += 1
    ergebnis = {kid: daten.anzeige(k, von, bis, gewichte[kid]) for kid, k in karten.items()}
    eindeutig_machen(ergebnis.values())
    return ergebnis


def fuer_vorgaenge(db: Session, vorgaenge) -> dict[int, Anzeige]:
    """Anzeige je Ladevorgang (Zuordnung am Ladetag, Stichtag Ladebeginn); setzt zusätzlich `v.karte_anzeige`."""
    vorgaenge = [v for v in vorgaenge if v.ladekarte is not None]
    if not vorgaenge:
        return {}
    tage = {v.id: stichtag(v.start) for v in vorgaenge}
    daten = _Daten(db, {v.ladekarte_id for v in vorgaenge}, min(tage.values()), max(tage.values()))
    ergebnis = {}
    for v in vorgaenge:
        a = daten.anzeige(v.ladekarte, tage[v.id], tage[v.id])
        ergebnis[v.id] = a
        v.karte_anzeige = a
    # mehrdeutig nur, wenn verschiedene Karten denselben Namen beim selben MSP tragen
    je_name: dict[tuple, set[int]] = defaultdict(set)
    for a in ergebnis.values():
        je_name[(a.name, a.msp)].add(a.karte.id)
    for a in ergebnis.values():
        if len(je_name[(a.name, a.msp)]) > 1:
            a.zusatz = _ende(a.karte.kennung)
    return ergebnis


def zuordenbare_karten(db):
    """Aktive Ladekarten ohne Gastkarten, für „Karte zuordnen →“ auf Fahrzeug- und Personenseite."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models import Ladekarte
    return db.scalars(select(Ladekarte).where(Ladekarte.aktiv, Ladekarte.ist_gastkarte.is_(False))
                      .options(selectinload(Ladekarte.msp)).order_by(Ladekarte.kennung)).all()
