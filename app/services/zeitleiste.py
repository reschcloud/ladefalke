"""Zeitleiste der Zuordnungen Fahrer:in ↔ Fahrzeug (Gantt-Ansicht), in beide Richtungen:

- nach="fahrzeug": je Fahrzeug eine Zeile, die Fahrer:innen als Balken;
- nach="fahrer":   je Person eine Zeile, die Fahrzeuge als Balken.

Zeitraum: Kalenderjahr oder rollierend (11 Monate zurück bis 2 Monate voraus, je ganze Monate).
Gleichzeitige Zuordnungen in einer Zeile (Poolfahrzeug) liegen in eigenen Spuren.
Lücke = Tage ohne Zuordnung ab der ersten Zuordnung der Zeile (davor war Fahrzeug/Person vermutlich nicht dabei).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.formatierung import datum
from app.models import Fahrer, Fahrzeug, FahrzeugOhneFahrer, ZuordnungFahrerFahrzeug
from app.services.luecken import _zeitraeume as zeitraeume_ohne

MONATSNAMEN = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
RICHTUNGEN = ("fahrzeug", "fahrer")


def _monatserster(tag: date, verschiebung: int = 0) -> date:
    index = tag.year * 12 + tag.month - 1 + verschiebung
    return date(index // 12, index % 12 + 1, 1)


def bereich_rollierend(heute: date) -> tuple[date, date]:
    return _monatserster(heute, -11), _monatserster(heute, 3) - timedelta(days=1)


def bereich_jahr(jahr: int) -> tuple[date, date]:
    return date(jahr, 1, 1), date(jahr, 12, 31)


def bereich_5jahre(heute: date) -> tuple[date, date]:
    """Letzte 5 Jahre (60 Monate) plus 3 Monate Vorschau, wie bei den letzten 12 Monaten."""
    return _monatserster(heute, -59), _monatserster(heute, 3) - timedelta(days=1)


def bereich_alles(db: Session, heute: date) -> tuple[date, date]:
    """Vom frühesten Zuordnungs- bzw. Leasingbeginn bis heute plus 3 Monate (oder bis zur spätesten Zuordnung)."""
    erstes = min((d for d in (db.scalar(select(func.min(ZuordnungFahrerFahrzeug.gueltig_ab))),
                             db.scalar(select(func.min(Fahrzeug.leasing_beginn)))) if d), default=None)
    if erstes is None:
        return bereich_rollierend(heute)
    letztes = db.scalar(select(func.max(ZuordnungFahrerFahrzeug.gueltig_bis)))
    bis = max(_monatserster(heute, 3) - timedelta(days=1), letztes or heute)
    return erstes.replace(day=1), _monatserster(bis, 1) - timedelta(days=1)


@dataclass
class Abschnitt:
    """Balken (Zuordnung) oder Lücke, mit Lage in Prozent der Zeitachse."""
    von: date
    bis: date | None  # None = offen (über den Bereich hinaus)
    links: float
    breite: float
    spur: int = 0
    zuordnung: ZuordnungFahrerFahrzeug | None = None
    gegenueber: Fahrer | Fahrzeug | None = None  # was der Balken zeigt (Person bzw. Fahrzeug)
    ohne: FahrzeugOhneFahrer | None = None  # Zeitraum „ohne Fahrer:in“ (bewusst niemandem zugeordnet)

    @property
    def beschriftung(self) -> str:
        if isinstance(self.gegenueber, Fahrzeug):  # Personen-Ansicht: Kennzeichen und Modell
            fz = self.gegenueber
            return f"{fz.kennzeichen} · {fz.modell}" if fz.modell else fz.kennzeichen
        return self.gegenueber.name if self.gegenueber else ""

    @property
    def text(self) -> str:
        zeitraum = f"{datum(self.von)} – {datum(self.bis) if self.bis else 'offen'}"
        if self.ohne is not None:
            return f"ohne Fahrer:in: {zeitraum}"
        if self.zuordnung is None:
            return f"Lücke: {zeitraum}"
        return f"{self.beschriftung}: {zeitraum}"


@dataclass
class Zeile:
    objekt: Fahrer | Fahrzeug
    balken: list[Abschnitt] = field(default_factory=list)
    luecken: list[Abschnitt] = field(default_factory=list)
    ohne: list[Abschnitt] = field(default_factory=list)  # Zeiträume „ohne Fahrer:in“ (nur Fahrzeug-Zeilen)
    spuren: int = 1
    nie_zugeordnet: bool = False
    # Fahrzeug-Zeilen: Leasing-Markierungen (Lage in %, None = nicht im Bereich) und Zeit außerhalb des Leasings
    leasing_beginn: float | None = None
    leasing_ende: float | None = None
    ausserhalb: list[tuple[float, float]] = field(default_factory=list)  # (links %, breite %)

    @property
    def ist_fahrzeug(self) -> bool:
        return isinstance(self.objekt, Fahrzeug)

    @property
    def name(self) -> str:
        return self.objekt.kennzeichen if self.ist_fahrzeug else self.objekt.name

    @property
    def zusatz(self) -> str | None:
        return self.objekt.modell if self.ist_fahrzeug else None

    @property
    def link(self) -> str:
        return f"/stammdaten/{'fahrzeuge' if self.ist_fahrzeug else 'fahrer'}/{self.objekt.id}"


@dataclass
class Zeitleiste:
    von: date
    bis: date
    nach: str  # "fahrzeug" | "fahrer"
    zeilen: list[Zeile]
    monate: list[tuple[str, float, float]]  # (Beschriftung, links %, breite %)
    heute: float | None  # Lage der Heute-Linie in %, None außerhalb

    @property
    def mit_luecken(self) -> int:
        return sum(1 for z in self.zeilen if z.luecken or z.nie_zugeordnet)

    @property
    def balken_ids(self) -> list[int]:
        """IDs der in Balken gezeigten Objekte (für die Hover-Hervorhebung)."""
        return sorted({b.gegenueber.id for z in self.zeilen for b in z.balken})


def berechnen(db: Session, von: date, bis: date, heute: date, status: str = "aktiv",
              nach: str = "fahrzeug") -> Zeitleiste:
    """`status`: aktiv | inaktiv | alle (wie die Statusfilter der Stammdaten)."""
    tage = (bis - von).days + 1
    nach_fahrzeug = nach != "fahrer"

    def lage(a: date, b: date) -> tuple[float, float]:
        return (a - von).days / tage * 100, ((b - a).days + 1) / tage * 100

    modell = Fahrzeug if nach_fahrzeug else Fahrer
    q = select(modell).order_by(Fahrzeug.kennzeichen if nach_fahrzeug else Fahrer.name)
    if status == "aktiv":
        q = q.where(modell.aktiv)
    elif status == "inaktiv":
        q = q.where(~modell.aktiv)
    zuordnungen: dict[int, list[ZuordnungFahrerFahrzeug]] = {}
    for z in db.scalars(select(ZuordnungFahrerFahrzeug)
                        .options(selectinload(ZuordnungFahrerFahrzeug.fahrer),
                                 selectinload(ZuordnungFahrerFahrzeug.fahrzeug))
                        .order_by(ZuordnungFahrerFahrzeug.gueltig_ab)):
        zuordnungen.setdefault(z.fahrzeug_id if nach_fahrzeug else z.fahrer_id, []).append(z)
    ohne_je: dict[int, list[FahrzeugOhneFahrer]] = {}
    if nach_fahrzeug:
        for o in db.scalars(select(FahrzeugOhneFahrer).order_by(FahrzeugOhneFahrer.gueltig_ab)):
            ohne_je.setdefault(o.fahrzeug_id, []).append(o)

    zeilen = []
    for objekt in db.scalars(q):
        alle = zuordnungen.get(objekt.id, [])
        ohne = ohne_je.get(objekt.id, [])
        abgedeckt = [(z.gueltig_ab, z.gueltig_bis) for z in [*alle, *ohne]]  # Fahrer:innen und „ohne Fahrer:in“
        # Lücken nur bei Fahrzeugen ohne Fahrer:in – eine Person ohne Fahrzeug ist keine Lücke
        zeile = Zeile(objekt, nie_zugeordnet=nach_fahrzeug and not abgedeckt)
        for o in ohne:
            a, b = max(o.gueltig_ab, von), min(o.gueltig_bis or bis, bis)
            if a <= b:
                zeile.ohne.append(Abschnitt(o.gueltig_ab, o.gueltig_bis, *lage(a, b), ohne=o))
        # Balken im Bereich, Spuren für gleichzeitige Zuordnungen
        spur_ende: list[date] = []
        for z in alle:
            a, b = max(z.gueltig_ab, von), min(z.gueltig_bis or bis, bis)
            if a > b:
                continue
            spur = next((i for i, ende in enumerate(spur_ende) if ende < a), len(spur_ende))
            if spur == len(spur_ende):
                spur_ende.append(b)
            else:
                spur_ende[spur] = b
            zeile.balken.append(Abschnitt(z.gueltig_ab, z.gueltig_bis, *lage(a, b), spur=spur, zuordnung=z,
                                          gegenueber=z.fahrer if nach_fahrzeug else z.fahrzeug))
        zeile.spuren = max(1, len(spur_ende))
        fz = objekt if nach_fahrzeug else None
        if fz is not None and (fz.leasing_beginn or fz.leasing_ende):
            # Leasing sichtbar machen; Lücke = jeder Tag im Leasing (im Bereich) ohne Zuordnung
            lb, le = fz.leasing_beginn, fz.leasing_ende
            if lb and von <= lb <= bis:
                zeile.leasing_beginn = lage(lb, lb)[0]
            if le and von <= le <= bis:
                zeile.leasing_ende = sum(lage(le, le))
            if lb and lb > von:
                zeile.ausserhalb.append(lage(von, min(lb - timedelta(days=1), bis)))
            if le and le < bis:
                zeile.ausserhalb.append(lage(max(le + timedelta(days=1), von), bis))
            start = max(von, lb or min((ab for ab, _ in abgedeckt), default=von))
            ende = min(bis, le) if le else bis
            if start <= ende:
                for a, b in zeitraeume_ohne(start, ende, abgedeckt):
                    offen = b == bis and not le
                    zeile.luecken.append(Abschnitt(a, None if offen else b, *lage(a, b)))
            zeile.nie_zugeordnet = False  # mit Leasing zeigt die Lücke den Zeitraum genau
        # Lücken ohne Leasingdaten: nicht abgedeckte Tage ab der ersten Zuordnung
        elif abgedeckt and nach_fahrzeug:
            cursor = max(von, min(ab for ab, _ in abgedeckt))
            for a, b in sorted((max(ab, von), min(bis_ or bis, bis)) for ab, bis_ in abgedeckt):
                if b < cursor:
                    continue
                if a > cursor:
                    zeile.luecken.append(Abschnitt(cursor, a - timedelta(days=1), *lage(cursor, a - timedelta(days=1))))
                cursor = max(cursor, b + timedelta(days=1))
            if cursor <= bis:
                zeile.luecken.append(Abschnitt(cursor, None, *lage(cursor, bis)))
        zeilen.append(zeile)

    # Achse: bis 24 Monate je Monat beschriftet, darüber je Jahr (sonst unlesbar)
    monate, m = [], von.replace(day=1)
    if (bis.year - von.year) * 12 + bis.month - von.month < 24:
        while m <= bis:
            ende = _monatserster(m, 1) - timedelta(days=1)
            links, breite = lage(max(m, von), min(ende, bis))
            monate.append((f"{MONATSNAMEN[m.month - 1]} {m:%y}", links, breite))
            m = _monatserster(m, 1)
    else:
        for jahr in range(von.year, bis.year + 1):
            links, breite = lage(max(date(jahr, 1, 1), von), min(date(jahr, 12, 31), bis))
            monate.append((str(jahr) if breite >= 3 else "", links, breite))  # angeschnittenes Jahr ohne Text
    heute_lage = (heute - von).days / tage * 100 + 0.5 / tage * 100 if von <= heute <= bis else None
    return Zeitleiste(von, bis, "fahrzeug" if nach_fahrzeug else "fahrer", zeilen, monate, heute_lage)
