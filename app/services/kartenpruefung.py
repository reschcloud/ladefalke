"""Ladekarten je Fahrzeug prüfen (Hinweis beim Fahrzeug, Aufgabe, Report „Offene Aufgaben“).

Zwei Arten von Befunden je aktivem Fahrzeug:
- „ausserhalb“ (immer geprüft): eine Zuordnung Ladekarte → Fahrzeug liegt ganz oder teilweise außerhalb des
  Leasingzeitraums (neue Zuordnungen verhindert services/zuordnung.leasing_pruefen; bestehende bzw. nach einer
  Änderung des Leasings entstehen hier als Aufgabe).
- „anzahl“ (abschaltbar, Einstellungen „Ladekarten je Fahrzeug“): Zeiträume, in denen dem Fahrzeug weniger oder mehr
  Ladekarten direkt zugeordnet sind als erlaubt (karten_min … karten_max; persönliche Karten zählen nicht).
  Geprüft wie die Lücken ohne Fahrer:in: ab Leasingbeginn (sonst ab der ersten Zuordnung) bis Leasing-Ende – auch
  künftig –, ohne Leasing-Ende bis heute.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import heute
from app.formatierung import datum, zeitraum
from app.models import Fahrzeug, ZuordnungFahrerFahrzeug, ZuordnungKarte
from app.services.app_einstellungen import AppEinstellungen

TAG = timedelta(days=1)


@dataclass(frozen=True)
class Befund:
    fahrzeug: Fahrzeug
    art: str  # „anzahl“ | „ausserhalb“
    von: date
    bis: date | None  # None = offen (ohne Ende)
    anzahl: int | None = None  # nur „anzahl“
    zuordnung: ZuordnungKarte | None = None  # nur „ausserhalb“
    offen: bool = False  # umfasst heute
    kuenftig: bool = False  # beginnt erst nach heute
    grenzen: tuple[int, int] = (1, 1)

    @property
    def text(self) -> str:
        bereich = (f"seit {datum(self.von)}" + (f" (bis {datum(self.bis)})" if self.bis else "")
                   if self.offen else zeitraum(self.von, self.bis))
        if self.art == "ausserhalb":
            k = self.zuordnung.ladekarte
            return f"Karte {k.bezeichnung or k.kennung} außerhalb des Leasings: {bereich}"
        return f"{self.anzahl_text}: {bereich}"

    @property
    def anzahl_text(self) -> str:
        if self.anzahl == 0:
            return "keine Ladekarte"
        return f"{self.anzahl} Ladekarte{'n' if self.anzahl != 1 else ''}"

    @property
    def stufe(self) -> str:
        """„rot“ (außerhalb des Leasings, aktuell keine Karte), sonst „gelb“."""
        return "rot" if self.art == "ausserhalb" or (self.offen and self.anzahl == 0) else "gelb"

    @property
    def erklaerung(self) -> str:
        if self.art == "ausserhalb":
            lb, le = self.fahrzeug.leasing_beginn, self.fahrzeug.leasing_ende
            return f"Leasing {zeitraum(lb, le) if lb else 'bis ' + datum(le)} – Zuordnung bitte anpassen"
        mn, mx = self.grenzen
        erlaubt = f"genau {mn}" if mn == mx else f"{mn} bis {mx}"
        return f"erlaubt: {erlaubt} Ladekarte{'n' if mx != 1 else ''} je Fahrzeug (Einstellungen)"


def _ausserhalb(fz: Fahrzeug, z: ZuordnungKarte, tag: date) -> list[Befund]:
    lb, le = fz.leasing_beginn, fz.leasing_ende
    teile = []
    if lb and z.gueltig_ab < lb:
        teile.append((z.gueltig_ab, min(z.gueltig_bis or lb - TAG, lb - TAG)))
    if le and (z.gueltig_bis is None or z.gueltig_bis > le):
        teile.append((max(z.gueltig_ab, le + TAG), z.gueltig_bis))
    return [Befund(fz, "ausserhalb", a, b, zuordnung=z, offen=a <= tag and (b is None or tag <= b),
                   kuenftig=a > tag) for a, b in teile]


def _anzahl(fz: Fahrzeug, karten: list[ZuordnungKarte], fahrer: list[ZuordnungFahrerFahrzeug], tag: date,
            mn: int, mx: int) -> list[Befund]:
    starts = [z.gueltig_ab for z in [*karten, *fahrer]]
    von = fz.leasing_beginn or (min(starts) if starts else None)
    if von is None:
        return []
    bis = fz.leasing_ende or tag
    if von > bis:
        return []
    # Anzahl je Tag über Wechselpunkte: +1 am Beginn, −1 am Tag nach dem Ende
    wechsel: dict[date, int] = {}
    for z in karten:
        a, b = max(z.gueltig_ab, von), min(z.gueltig_bis or bis, bis)
        if a <= b:
            wechsel[a] = wechsel.get(a, 0) + 1
            wechsel[b + TAG] = wechsel.get(b + TAG, 0) - 1
    punkte = sorted({von, bis + TAG, *[d for d in wechsel if von <= d <= bis + TAG]})
    abschnitte, anzahl = [], sum(n for d, n in wechsel.items() if d < von)
    for a, naechster in zip(punkte, punkte[1:]):
        anzahl += wechsel.get(a, 0)
        b = naechster - TAG
        if abschnitte and abschnitte[-1][2] == anzahl and abschnitte[-1][1] + TAG == a:
            abschnitte[-1] = (abschnitte[-1][0], b, anzahl)
        else:
            abschnitte.append((a, b, anzahl))
    befunde = []
    for a, b, n in abschnitte:
        if mn <= n <= mx:
            continue
        endlos = not fz.leasing_ende and b == tag
        befunde.append(Befund(fz, "anzahl", a, None if endlos else b, anzahl=n, offen=a <= tag <= b,
                              kuenftig=a > tag, grenzen=(mn, mx)))
    return befunde


def ermitteln(db: Session, e: AppEinstellungen, tag: date | None = None,
              fahrzeuge: list[Fahrzeug] | None = None) -> list[Befund]:
    """Befunde aller aktiven Fahrzeuge (bzw. der übergebenen), je Fahrzeug chronologisch."""
    tag = tag or heute()
    if fahrzeuge is None:
        fahrzeuge = db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv).order_by(Fahrzeug.kennzeichen)).all()
    ids = [fz.id for fz in fahrzeuge if fz.aktiv]
    karten: dict[int, list[ZuordnungKarte]] = {}
    for z in db.scalars(select(ZuordnungKarte).where(ZuordnungKarte.fahrzeug_id.in_(ids))
                        .options(selectinload(ZuordnungKarte.ladekarte)).order_by(ZuordnungKarte.gueltig_ab)):
        karten.setdefault(z.fahrzeug_id, []).append(z)
    fahrer: dict[int, list[ZuordnungFahrerFahrzeug]] = {}
    for z in db.scalars(select(ZuordnungFahrerFahrzeug).where(ZuordnungFahrerFahrzeug.fahrzeug_id.in_(ids))):
        fahrer.setdefault(z.fahrzeug_id, []).append(z)
    befunde = []
    for fz in fahrzeuge:
        if not fz.aktiv:
            continue
        eigene = [b for z in karten.get(fz.id, []) for b in _ausserhalb(fz, z, tag)]
        if e.feature_kartenpruefung:
            eigene += _anzahl(fz, karten.get(fz.id, []), fahrer.get(fz.id, []), tag, e.karten_min, e.karten_max)
        befunde += sorted(eigene, key=lambda b: (b.von, b.art))
    return befunde
