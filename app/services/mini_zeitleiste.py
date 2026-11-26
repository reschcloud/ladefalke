"""Mini-Zeitleiste und chronologische Tabelle für die Zuordnungs-Abschnitte der Detailseiten.

Zeigt die Zuordnungen eines Objekts (Fahrer:innen eines Fahrzeugs, Fahrzeuge einer Person, Ziele einer Ladekarte,
Karten eines Fahrzeugs bzw. einer Person) als Abschnitte auf einer Achse – von Leasingbeginn bzw. erster Zuordnung bis
heute plus 3 Monate –, mit Heute-Linie und beim Fahrzeug Leasing-Markierungen. Lücken:
- „warnung“: Fahrzeug ohne Fahrer:in (wie in Zeitleiste und Aufgaben, ab Leasingbeginn bis Leasing-Ende – auch in
  der Zukunft –, ohne Leasing-Ende bis heute)
- „neutral“: Ladekarte ohne Zuordnung (kein Fehler, nur zur Orientierung)
- None: keine Lücken (Person ohne Fahrzeug ist keine Lücke; Karten am Fahrzeug sind optional)
Die Tabelle listet Zuordnungen und Lücken chronologisch (alt → neu).
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.formatierung import datum
from app.services.luecken import _zeitraeume as zeitraeume_ohne


@dataclass
class Eintrag:
    ab: date
    bis: date | None
    name: str
    link: str | None = None  # zum Objekt (Person, Fahrzeug, Karte)
    zusatz: str = ""  # z. B. MSP
    bearbeiten: str | None = None  # Link zum Bearbeiten der Zuordnung (bzw. zur Seite, auf der sie geändert wird)
    art: str = ""  # Symbol vor dem Namen: „auto“, „person“ oder „karte“ (_makros.symbol)
    stil: str = ""  # zusätzliche CSS-Klasse, z. B. „ohne“ für Zeiträume „ohne Fahrer:in“ (grau)
    bearbeiten_text: str = "bearbeiten"  # z. B. „bei Karte ändern →“, wenn die Zuordnung woanders geändert wird

    @property
    def zeitraum(self) -> str:
        return f"{datum(self.ab)} – {datum(self.bis) if self.bis else 'offen'}"


@dataclass
class Abschnitt:
    links: float
    breite: float
    spur: int = 0
    eintrag: Eintrag | None = None  # None = Lücke
    von: date | None = None
    bis: date | None = None


@dataclass
class Zeile:
    """Tabellenzeile: Zuordnung oder Lücke."""
    ab: date
    eintrag: Eintrag | None = None
    bis: date | None = None  # nur Lücke; None = bis heute (offen)

    @property
    def zeitraum(self) -> str:
        if self.eintrag:
            return self.eintrag.zeitraum
        return f"{datum(self.ab)} – {datum(self.bis) if self.bis else 'heute'}"


@dataclass
class Mini:
    von: date
    bis: date
    luecken_art: str | None
    abschnitte: list[Abschnitt] = field(default_factory=list)
    luecken: list[Abschnitt] = field(default_factory=list)
    achse: list[tuple[str, float]] = field(default_factory=list)  # (Beschriftung, links %)
    spuren: int = 1
    heute: float | None = None
    leasing_beginn: float | None = None
    leasing_ende: float | None = None
    ausserhalb: list[tuple[float, float]] = field(default_factory=list)
    zeilen: list[Zeile] = field(default_factory=list)
    leasing_beginn_datum: date | None = None
    leasing_ende_datum: date | None = None

    @property
    def ende_links(self) -> str:
        """Beschriftung am linken Ende: Leasingbeginn, wenn die Leiste dort beginnt, sonst das Startdatum."""
        if self.leasing_beginn_datum and self.leasing_beginn is not None and self.leasing_beginn < 4:
            return f"Leasingbeginn {datum(self.leasing_beginn_datum)}"
        return datum(self.von)

    @property
    def ende_rechts(self) -> str:
        if self.leasing_ende_datum and self.leasing_ende is not None and self.leasing_ende > 96:
            return f"Leasing-Ende {datum(self.leasing_ende_datum)}"
        return datum(self.bis)


def monatserster(tag: date, verschiebung: int = 0) -> date:
    """Erster Tag des Monats von `tag`, um `verschiebung` Monate verschoben (auch von services/zeitleiste.py)."""
    index = tag.year * 12 + tag.month - 1 + verschiebung
    return date(index // 12, index % 12 + 1, 1)


def bereich(eintraege: list[Eintrag], heute: date, leasing_beginn: date | None = None,
            leasing_ende: date | None = None) -> tuple[date, date]:
    """Achse: ab Monatsanfang von Leasingbeginn bzw. erster Zuordnung bis heute + 3 Monate bzw. letztem Ende."""
    starts = [d for d in [leasing_beginn] + [e.ab for e in eintraege] if d]
    von = monatserster(min(starts) if starts else monatserster(heute, -11))
    enden = [monatserster(heute, 3) - timedelta(days=1)] + [e.bis for e in eintraege if e.bis] \
        + ([leasing_ende] if leasing_ende else [])
    return von, monatserster(max(enden), 1) - timedelta(days=1)


def berechnen(eintraege: list[Eintrag], heute: date, leasing_beginn: date | None = None,
              leasing_ende: date | None = None, luecken_art: str | None = None,
              achse: tuple[date, date] | None = None) -> Mini:
    """`achse`: gemeinsamer Bereich mehrerer Leisten (Zeitleiste des Fahrzeugs: Fahrer:innen und Karten)."""
    eintraege = sorted(eintraege, key=lambda e: (e.ab, e.bis or date.max))
    von, bis = achse or bereich(eintraege, heute, leasing_beginn, leasing_ende)
    tage = (bis - von).days + 1

    def lage(a: date, b: date) -> tuple[float, float]:
        a, b = max(a, von), min(b, bis)
        return (a - von).days / tage * 100, ((b - a).days + 1) / tage * 100

    mini = Mini(von, bis, luecken_art, leasing_beginn_datum=leasing_beginn, leasing_ende_datum=leasing_ende)
    # Abschnitte, gleichzeitige Zuordnungen (Pool) auf eigenen Spuren
    spur_ende: list[date] = []
    for e in eintraege:
        ende = e.bis or bis
        spur = next((i for i, x in enumerate(spur_ende) if x < e.ab), len(spur_ende))
        if spur == len(spur_ende):
            spur_ende.append(ende)
        else:
            spur_ende[spur] = ende
        mini.abschnitte.append(Abschnitt(*lage(e.ab, ende), spur=spur, eintrag=e, von=e.ab, bis=e.bis))
    mini.spuren = max(1, len(spur_ende))
    # Lücken (siehe Moduldoku)
    luecken: list[tuple[date, date]] = []
    if luecken_art and (leasing_beginn or eintraege):
        start = leasing_beginn or eintraege[0].ab
        ende = leasing_ende or heute
        if start <= ende:
            luecken = zeitraeume_ohne(start, ende, [(e.ab, e.bis) for e in eintraege])
    for a, b in luecken:
        offen = b == heute and not leasing_ende  # ohne Leasing-Ende: „bis heute“
        mini.luecken.append(Abschnitt(*lage(a, b), von=a, bis=None if offen else b))
    # Tabelle chronologisch: Zuordnungen und Lücken
    mini.zeilen = sorted([Zeile(e.ab, eintrag=e) for e in eintraege]
                         + [Zeile(l.von, bis=l.bis) for l in mini.luecken], key=lambda z: (z.ab, z.eintrag is None))
    # Achse: bis 24 Monate Quartale, darüber Jahre
    monate = (bis.year - von.year) * 12 + bis.month - von.month + 1
    m = von
    while m <= bis:
        if (monate > 24 and m.month == 1) or (monate <= 24 and m.month in (1, 4, 7, 10)):
            text = str(m.year) if m.month == 1 else f"{('', 'Jan', '', '', 'Apr', '', '', 'Jul', '', '', 'Okt')[m.month]}"
            mini.achse.append((text, lage(m, m)[0]))
        m = monatserster(m, 1)
    mini.heute = lage(heute, heute)[0] if von <= heute <= bis else None
    if leasing_beginn and von <= leasing_beginn <= bis:
        mini.leasing_beginn = lage(leasing_beginn, leasing_beginn)[0]
    if leasing_ende and von <= leasing_ende <= bis:
        mini.leasing_ende = sum(lage(leasing_ende, leasing_ende))
    if leasing_beginn and leasing_beginn > von:
        mini.ausserhalb.append(lage(von, leasing_beginn - timedelta(days=1)))
    if leasing_ende and leasing_ende < bis:
        mini.ausserhalb.append(lage(leasing_ende + timedelta(days=1), bis))
    return mini


@dataclass
class Markierung:
    """Hervorgehobener Zeitraum über einer Leiste (z. B. falsche Kartenanzahl), Lage in % der Achse."""
    links: float
    breite: float
    stufe: str  # „rot“ | „gelb“
    text: str


def markierungen(mini: Mini, zeitraeume: list[tuple[date, date | None, str, str]]) -> list[Markierung]:
    """(von, bis oder None = bis Achsenende, Stufe, Text) → Markierungen auf der Achse von `mini`."""
    tage = (mini.bis - mini.von).days + 1
    ergebnis = []
    for a, b, stufe, text in zeitraeume:
        a, b = max(a, mini.von), min(b or mini.bis, mini.bis)
        if a <= b:
            ergebnis.append(Markierung((a - mini.von).days / tage * 100, ((b - a).days + 1) / tage * 100, stufe, text))
    return ergebnis
