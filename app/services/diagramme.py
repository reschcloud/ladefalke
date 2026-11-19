"""Dashboard-Diagramme: Verteilung von kWh und Ladekosten auf die Fahrer:innen, kWh aller Ladungen je MSP und je CPO.

- Monat: Tortendiagramme; sonst gestapelte Säulen mit einer Säule je Monat.
- Zurechnung wie in der Auswertung pro Fahrer:in (nur Ladekosten, Ladung zählt für die Person zum Ladezeitpunkt;
  Poolladungen ohne eindeutige Person, Gäste, Gebühren und Pauschalen sind nicht enthalten).
- Die ANZAHL_FARBEN Personen mit den meisten kWh im Zeitraum bekommen eine eigene Farbe (für kWh und Kosten
  dieselbe), alle übrigen sind „Weitere“ – höchstens sechs Segmente je Torte. Die Tabelle zeigt alle Personen.
Geometrie (SVG-Koordinaten) als float; die Werte selbst bleiben Decimal.
"""
import math
from datetime import time
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatierung import geld, zahl
from app.models import Ladeart, Ladevorgang, Msp
from app.services.app_einstellungen import AppEinstellungen
from app.services.aufloesung import stichtag_spalte
from app.services.auswertung import MONATSNAMEN, Auswertung, Zeitraum, auswerten, zeitbedingung, zeitraum_mit

ANZAHL_FARBEN = 5
NULL = Decimal(0)


@dataclass
class Reihe:
    name: str
    slot: int  # 1–ANZAHL_FARBEN, 0 = „Weitere“
    fahrer_id: int | None = None  # Personen-Diagramme: Link zur Auswertung der Person
    betreiber: str | None = None  # CPO-Diagramm: Link zu den Ladevorgängen dieses Betreibers
    msp_id: int | None = None  # MSP-Diagramm: Link zu den Ladevorgängen dieses MSP
    # nur „Weitere“: die zusammengefassten Einträge (Name, kWh, Kosten), meiste kWh zuerst – für den Tooltip
    enthalten: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)

    def enthalten_text(self, feld: int, einheit: str, hoechstens: int = 15) -> str:
        """Tooltip-Zeilen „Name: Wert“ (feld 0 = kWh, 1 = Kosten; Format wie im Diagramm), nach Wert absteigend;
        lange Listen gekürzt."""
        def fmt(x: Decimal) -> str:
            return geld(x) if einheit == "€" else f"{zahl(x, 0)} {einheit}"
        eintraege = sorted(self.enthalten, key=lambda e: (-e[1 + feld], e[0]))
        zeilen = [f"{name}: {fmt(werte[feld])}" for name, *werte in eintraege[:hoechstens]]
        if len(eintraege) > hoechstens:
            zeilen.append(f"… und {len(eintraege) - hoechstens} weitere")
        return "\n".join(zeilen)


@dataclass
class Personenzeile:
    """Tabellenansicht: eine Person mit Summen im Zeitraum."""
    name: str
    fahrer_id: int
    kwh: Decimal
    kosten: Decimal
    reihe: Reihe


@dataclass
class Verteilung:
    reihen: list[Reihe]
    spalten: list[tuple[Zeitraum, dict[int, tuple[Decimal, Decimal]]]]  # (Monat, {Reihenindex: (kWh, Kosten)})
    personen: list[Personenzeile] = field(default_factory=list)
    torte: bool = False  # Monatsansicht

    def summe(self, feld: int) -> Decimal:
        return sum((w[feld] for _, werte in self.spalten for w in werte.values()), NULL)

    def reihensumme(self, index: int, feld: int) -> Decimal:
        return sum((werte.get(index, (NULL, NULL))[feld] for _, werte in self.spalten), NULL)

    @property
    def leer(self) -> bool:
        return not self.reihen


def monate(z: Zeitraum) -> list[Zeitraum]:
    if z.art == "monat":
        return [z]
    ergebnis, m = [], zeitraum_mit(z.von, "monat")
    while m.von <= z.bis:
        ergebnis.append(m)
        m = m.verschoben(1)
    return ergebnis


def je_monat(db: Session, a: Auswertung, einst: AppEinstellungen) -> list[tuple[Zeitraum, Auswertung]]:
    """Auswertungen je Monat des Zeitraums; in der Monatsansicht wird `a` wiederverwendet."""
    return [(m, a if m == a.zeitraum else auswerten(db, m, einst)) for m in monate(a.zeitraum)]


def _aufbauen(monate: list[tuple[Zeitraum, Auswertung]], eintraege, reihe_neu) -> tuple[Verteilung, list, dict]:
    """Gemeinsamer Aufbau: `eintraege(auswertung)` liefert (Schlüssel, kWh, Kosten); die ANZAHL_FARBEN Schlüssel
    mit den meisten kWh bekommen über `reihe_neu(schluessel, slot)` eine eigene Reihe, der Rest ist „Weitere“."""
    gesamt: dict = {}
    for _, am in monate:
        for schluessel, kwh, kosten in eintraege(am):
            summe = gesamt.setdefault(schluessel, [NULL, NULL])
            summe[0] += kwh
            summe[1] += kosten
    rangfolge = sorted(((k, kwh, kosten) for k, (kwh, kosten) in gesamt.items() if kwh > 0),
                       key=lambda e: (-e[1], str(e[0])))
    reihen = [reihe_neu(k, i + 1) for i, (k, _, _) in enumerate(rangfolge[:ANZAHL_FARBEN])]
    index = {k: i for i, (k, _, _) in enumerate(rangfolge[:ANZAHL_FARBEN])}
    if len(rangfolge) > ANZAHL_FARBEN:
        reihen.append(Reihe("Weitere", 0, enthalten=[(reihe_neu(k, 0).name, kwh, kosten)
                                                       for k, kwh, kosten in rangfolge[ANZAHL_FARBEN:]]))
    spalten = []
    for m, am in monate:
        werte: dict[int, tuple[Decimal, Decimal]] = {}
        for schluessel, kwh, kosten in eintraege(am):
            if schluessel not in gesamt or gesamt[schluessel][0] <= 0:
                continue
            i = index.get(schluessel, len(reihen) - 1)
            alt_kwh, alt_kosten = werte.get(i, (NULL, NULL))
            werte[i] = (alt_kwh + kwh, alt_kosten + kosten)
        spalten.append((m, werte))
    return Verteilung(reihen, spalten, torte=monate[0][0].art == "monat" and len(monate) == 1), rangfolge, index


def berechnen(db: Session, a: Auswertung, einst: AppEinstellungen,
              monate: list[tuple[Zeitraum, Auswertung]] | None = None) -> Verteilung:
    """Verteilung von kWh und Ladekosten auf die Fahrer:innen."""
    monate = monate or je_monat(db, a, einst)
    personen = {z.fahrer.id: z.fahrer for _, am in monate for z in am.fahrer}
    v, rangfolge, index = _aufbauen(
        monate, lambda am: [(z.fahrer.id, z.summe.kwh, z.summe.kosten) for z in am.fahrer],
        lambda fid, slot: Reihe(personen[fid].name, slot, fahrer_id=fid))
    weitere = v.reihen[-1] if v.reihen and v.reihen[-1].slot == 0 else None
    v.personen = [Personenzeile(personen[fid].name, fid, kwh, kosten, v.reihen[index[fid]] if fid in index else weitere)
                  for fid, kwh, kosten in sorted(rangfolge, key=lambda e: (-e[1], personen[e[0]].name))]
    return v


def aus_summen(eintraege: list[tuple[str, "Summe"]]) -> Verteilung:
    """Verteilung (eine Torte) aus fertigen Summen, z. B. kWh einer Person je MSP oder CPO: die ANZAHL_FARBEN mit den
    meisten kWh eigene Farbe, der Rest „Weitere“."""
    rangfolge = sorted(((n, s) for n, s in eintraege if s.kwh > 0), key=lambda e: (-e[1].kwh, e[0]))
    reihen = [Reihe(n, i + 1) for i, (n, _) in enumerate(rangfolge[:ANZAHL_FARBEN])]
    werte = {i: (s.kwh, s.kosten) for i, (_, s) in enumerate(rangfolge[:ANZAHL_FARBEN])}
    if rest := rangfolge[ANZAHL_FARBEN:]:
        reihen.append(Reihe("Weitere", 0, enthalten=[(n, s.kwh, s.kosten) for n, s in rest]))
        werte[len(reihen) - 1] = (sum((s.kwh for _, s in rest), NULL), sum((s.kosten for _, s in rest), NULL))
    return Verteilung(reihen, [(None, werte)], torte=True)


def cpo_verteilung(monate: list[tuple[Zeitraum, Auswertung]]) -> Verteilung:
    """kWh aller Ladungen (öffentlich und privat) je CPO: aus der EVSE-ID oder Vorgabe des MSP, sonst „ohne Kennung“."""
    def eintraege(am: Auswertung):
        return [(c or "", s.kwh, s.kosten) for c, s in am.cpo]

    return _aufbauen(monate, eintraege, lambda c, slot: Reihe(c or "ohne Kennung", slot, betreiber=c or None))[0]


def msp_verteilung(db: Session, monate: list[tuple[Zeitraum, Auswertung]]) -> Verteilung:
    """kWh aller Ladungen (öffentlich und privat) je MSP; Gebühren haben keine kWh und zählen nicht."""
    ids = dict(db.execute(select(Msp.name, Msp.id)).all())

    def eintraege(am: Auswertung):
        return [(name, s.kwh, s.kosten) for name, s in am.msp]

    return _aufbauen(monate, eintraege, lambda name, slot: Reihe(name, slot, msp_id=ids.get(name)))[0]


# ------------------------------------------------------------------ Geometrie

@dataclass
class Segment:
    reihe: Reihe
    wert: Decimal
    anteil: float
    pfad: str = ""  # SVG-Pfad (Torte); leer = voller Kreis
    x: float = 0
    y: float = 0
    breite: float = 0
    hoehe: float = 0


def torte(v: Verteilung, feld: int, r: float = 90, mitte: float = 100) -> list[Segment]:
    """Tortenstücke im Uhrzeigersinn ab 12 Uhr, in Reihenfolge der Reihen („Weitere“ zuletzt)."""
    gesamt = v.summe(feld)
    if not gesamt:
        return []
    segmente, winkel = [], -math.pi / 2
    for i, reihe in enumerate(v.reihen):
        wert = v.reihensumme(i, feld)
        if wert <= 0:
            continue
        anteil = float(wert / gesamt)
        ende = winkel + anteil * 2 * math.pi
        if anteil >= 0.9999:
            pfad = ""
        else:
            x1, y1 = mitte + r * math.cos(winkel), mitte + r * math.sin(winkel)
            x2, y2 = mitte + r * math.cos(ende), mitte + r * math.sin(ende)
            gross = 1 if anteil > 0.5 else 0
            pfad = f"M{mitte},{mitte} L{x1:.2f},{y1:.2f} A{r},{r} 0 {gross} 1 {x2:.2f},{y2:.2f} Z"
        segmente.append(Segment(reihe, wert, anteil, pfad))
        winkel = ende
    return segmente


def _schritt(maximum: float, ziel: int = 4) -> float:
    """„Schöne“ Schrittweite (1, 2, 5 × 10^n) für etwa `ziel` Teilstriche."""
    if maximum <= 0:
        return 1
    roh = maximum / ziel
    basis = 10 ** math.floor(math.log10(roh))
    return next(f * basis for f in (1, 2, 5, 10) if f * basis >= roh)


@dataclass
class Saeulen:
    breite: float
    hoehe: float
    links: float  # Platz für die Achsenbeschriftung
    unten: float
    teilstriche: list[tuple[float, float]]  # (y, Wert)
    spalten: list[tuple[str, str, float, list[Segment], Decimal]]  # (kurz, Monat, x-Mitte, Segmente, Summe)
    band: float = 40.0  # Breite je Spalte (Trefferfläche für den Tooltip)


def monatsbeschriftungen(monate: list[Zeitraum]) -> list[str]:
    """Achsenbeschriftung der Monatssäulen, ausgedünnt nach Länge (sonst überlagern sich die Texte):
    bis 12 Monate jeder Monat („Jan“); bis 24 Quartalsanfänge, der Januar als Jahr („2025“, „Apr“, …); darüber nur
    die Jahre am Januar, der erste Monat mit seinem Jahr, wenn bis zum nächsten Januar Platz ist. Leer = ohne Text."""
    if len(monate) <= 12:
        return [MONATSNAMEN[m.nr - 1][:3] for m in monate]
    if len(monate) <= 24:
        return [str(m.jahr) if m.nr == 1 else MONATSNAMEN[m.nr - 1][:3] if m.nr in (4, 7, 10) else "" for m in monate]
    texte = [str(m.jahr) if m.nr == 1 else "" for m in monate]
    if monate[0].nr != 1 and 13 - monate[0].nr >= 6:  # Jahr am Anfang nur mit Abstand zum nächsten Januar
        texte[0] = str(monate[0].jahr)
    return texte


def saeulen(v: Verteilung, feld: int, breite: float = 420, hoehe: float = 240) -> Saeulen:  # drei Diagramme je Zeile
    links, oben, unten = 56.0, 10.0, 26.0
    flaeche = hoehe - oben - unten
    summen = [sum((w[feld] for w in werte.values()), NULL) for _, werte in v.spalten]
    schritt = _schritt(float(max(summen, default=NULL)))
    maximum = schritt * max(1, math.ceil(float(max(summen, default=NULL)) / schritt))
    band = (breite - links) / max(1, len(v.spalten))
    dicke = min(24.0, band * 0.6)
    ergebnis = []
    beschriftungen = monatsbeschriftungen([monat for monat, _ in v.spalten])
    for (monat, werte), summe, kurz in zip(v.spalten, summen, beschriftungen):
        mitte = links + band * (len(ergebnis) + 0.5)
        y, segmente = oben + flaeche, []
        for i, reihe in enumerate(v.reihen):  # unten beginnend in Reihenfolge der Legende
            wert = werte.get(i, (NULL, NULL))[feld]
            if wert <= 0:
                continue
            h = float(wert) / maximum * flaeche
            y -= h
            segmente.append(Segment(reihe, wert, float(wert / summe) if summe else 0, x=mitte - dicke / 2, y=y,
                                    breite=dicke, hoehe=h))
        ergebnis.append((kurz, monat.label, mitte, segmente, summe))
    teilstriche = [(oben + flaeche - k * schritt / maximum * flaeche, k * schritt)
                   for k in range(int(round(maximum / schritt)) + 1)]
    return Saeulen(breite, hoehe, links, unten, teilstriche, ergebnis, band)


# ------------------------------------------------------------------ Histogramme öffentliches DC-Laden (Auswertungen)

DAUER_KLASSE = 10  # Minuten je Säule
DAUER_GRENZE = 90  # ab hier eine gemeinsame Säule „90+“


@dataclass
class Histogramm:
    """Säulen (kurz = Achsenbeschriftung, leer = keine; text = Tooltip/Tabelle; anzahl) samt SVG-Geometrie."""
    klassen: list[tuple[str, str, int]]
    breite: float = 520
    hoehe: float = 220
    links: float = 40.0
    oben: float = 10.0
    unten: float = 26.0

    @property
    def anzahl(self) -> int:
        return sum(n for _, _, n in self.klassen)

    def geometrie(self) -> tuple[list[tuple[float, int]], list[tuple[str, str, int, float, float, float, float]]]:
        """(Teilstriche [(y, Wert)], Säulen [(kurz, text, anzahl, x, y, breite, höhe)])."""
        flaeche = self.hoehe - self.oben - self.unten
        groesste = max((n for _, _, n in self.klassen), default=0)
        schritt = max(1, _schritt(groesste))  # ganze Ladevorgänge
        maximum = schritt * max(1, math.ceil(groesste / schritt))
        band = (self.breite - self.links) / max(1, len(self.klassen))
        saeulen = []
        for i, (kurz, text, n) in enumerate(self.klassen):
            h = n / maximum * flaeche
            saeulen.append((kurz, text, n, self.links + band * i, self.oben + flaeche - h, band, h))
        teilstriche = [(self.oben + flaeche - k * schritt / maximum * flaeche, k * schritt)
                       for k in range(int(round(maximum / schritt)) + 1)]
        return teilstriche, saeulen


@dataclass
class DcOeffentlich:
    dauer: Histogramm
    stunde: Histogramm
    median_minuten: int | None
    ohne_uhrzeit: int = 0  # Beginn genau 00:00:00 – nur bei der Dauer, nicht bei der Uhrzeit


def dc_oeffentlich(db: Session, z: Zeitraum, stichtag: str, *bedingungen) -> DcOeffentlich:
    """Öffentliche DC-Ladungen mit kWh im Zeitraum (Stichtag wie die Auswertung): Verteilung der Dauer
    (angesteckt, Start bis Ende) in Klassen zu DAUER_KLASSE Minuten und der Uhrzeit des Ladebeginns (Ortszeit;
    ohne Ladungen, deren Beginn genau 00:00:00 ist – dort ist die Uhrzeit unbekannt)."""
    zeiten = db.execute(select(Ladevorgang.start, Ladevorgang.ende).where(
        Ladevorgang.stromart == "DC", Ladevorgang.ladeart == Ladeart.oeffentlich, Ladevorgang.kwh > 0,
        Ladevorgang.ende >= Ladevorgang.start, zeitbedingung(stichtag_spalte(stichtag), z), *bedingungen)).all()
    minuten = sorted((ende - start).total_seconds() / 60 for start, ende in zeiten)
    anzahl_klassen = DAUER_GRENZE // DAUER_KLASSE + 1
    dauer = [0] * anzahl_klassen
    for m in minuten:
        dauer[min(int(m // DAUER_KLASSE), anzahl_klassen - 1)] += 1
    # Beginn genau 00:00:00 = Uhrzeit unbekannt (z. B. Elli: Rechnung nur mit Datum, Ende = Datum + Dauer) –
    # die Dauer stimmt, die Uhrzeit nicht
    stunden, ohne_uhrzeit = [0] * 24, 0
    for start, _ in zeiten:
        if start.time() == time(0):
            ohne_uhrzeit += 1
        else:
            stunden[start.hour] += 1
    klassen_dauer = [(f"{i * DAUER_KLASSE}–{(i + 1) * DAUER_KLASSE}", f"{i * DAUER_KLASSE}–{(i + 1) * DAUER_KLASSE} min", n)
                     for i, n in enumerate(dauer[:-1])]
    klassen_dauer.append((f"{DAUER_GRENZE}+", f"{DAUER_GRENZE} min und länger", dauer[-1]))
    klassen_stunde = [(f"{h}" if h % 3 == 0 else "", f"{h:02d}–{(h + 1) % 24:02d} Uhr", n)
                      for h, n in enumerate(stunden)]
    median = None
    if minuten:
        mitte = len(minuten) // 2
        median = round(minuten[mitte] if len(minuten) % 2 else (minuten[mitte - 1] + minuten[mitte]) / 2)
    return DcOeffentlich(Histogramm(klassen_dauer), Histogramm(klassen_stunde), median, ohne_uhrzeit)
