"""Auswertungen (SPEC Abschnitt 6) für einen beliebigen Zeitraum – rein lesend.

- Ladevorgänge zählen über den Stichtag (Einstellung: Ladeende oder Ladebeginn) zum Zeitraum;
  0-kWh-Vorgänge zählen nicht als Ladevorgang, ihre Kosten aber schon.
- Gebühren zählen über den Leistungsbeginn (ohne Leistungszeitraum: Rechnungsdatum). Ihr Fahrzeug wird
  über die Zuordnung der Karte am Ende des Leistungszeitraums ermittelt (bei jeder Auswertung neu).
- Pauschalen zählen über ihren Monat (Monatserster liegt im Zeitraum), keinem Fahrzeug zugeordnet.
- Ø €/kWh ist immer Σ Kosten / Σ kWh (gewichtet), nie ein Mittel von Mittelwerten.
- Pro Fahrer:in und Topliste: Ladevorgänge zählen für die Person, die zum Ladezeitpunkt gefahren ist
  (personengebundene Karte → deren Person, sonst Fahrer:in des Fahrzeugs; Stichtag laut Einstellungen). Nur
  Ladekosten, keine Gebühren. Vorgänge mit mehreren möglichen Personen (Poolfahrzeug) zählen hier nicht.
- Pro CPO nach Fahrer:in und Ladekarte (`cpo_personen`): nur öffentliche Ladungen, nur Ladekosten; Person wie oben,
  dazu Sammelzeilen für Poolfahrzeuge (Person nicht eindeutig), Ladungen ohne ermittelbare Person und Gastkarten.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import heute
from app.formatierung import datum, monat
from app.models import (Fahrer, Fahrzeug, Gebuehr, Import, Ladeart, Ladekarte, Ladeort, Ladevorgang, Pauschale,
                        ZuordnungFahrerFahrzeug)
from app.services.app_einstellungen import AppEinstellungen
from app.services.aufloesung import fahrer_beim_laden, fahrzeug_fuer_karte, stichtag, stichtag_art, stichtag_spalte
from app.services.zuordnung import fahrer_von_fahrzeug_am, karten_zuordnung_am

NULL = Decimal(0)

# ------------------------------------------------------------------ Zeiträume

ARTEN = {"monat": "Monat", "quartal": "Quartal", "halbjahr": "Halbjahr", "jahr": "Jahr", "alles": "Alles", "frei": "Frei"}
MONATE_JE_ART = {"monat": 1, "quartal": 3, "halbjahr": 6, "jahr": 12}
MONATSNAMEN = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember"]
# Auswahl des Teilzeitraums je Art (Nr. → Text); "jahr" hat nur die Jahresauswahl
TEILE = {"monat": dict(enumerate(MONATSNAMEN, start=1)),
         "quartal": {n: f"Q{n}" for n in range(1, 5)},
         "halbjahr": {1: "1. Halbjahr", 2: "2. Halbjahr"}}


def _monatsende(jahr: int, mon: int) -> date:
    return (date(jahr, mon, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


@dataclass(frozen=True, order=True)
class Zeitraum:
    art: str
    von: date
    bis: date  # inklusive
    jahr: int = 0
    nr: int = 1  # Monat 1–12, Quartal 1–4, Halbjahr 1–2, Jahr 1

    @property
    def label(self) -> str:
        if self.art == "monat":
            return monat(self.von)
        if self.art == "quartal":
            return f"Q{self.nr} {self.jahr}"
        if self.art == "halbjahr":
            return f"{self.nr}. Halbjahr {self.jahr}"
        if self.art == "jahr":
            return str(self.jahr)
        if self.art == "alles":
            return f"Alles ({monat(self.von)} – {monat(self.bis)})"
        return f"{datum(self.von)} – {datum(self.bis)}"

    def verschoben(self, schritte: int) -> "Zeitraum":
        if self.art == "alles":  # kein Davor und Danach
            return self
        if self.art == "frei":
            laenge = (self.bis - self.von).days + 1
            return zeitraum_frei(self.von + timedelta(days=laenge * schritte),
                                 self.bis + timedelta(days=laenge * schritte))
        je_jahr = 12 // MONATE_JE_ART[self.art]
        index = self.jahr * je_jahr + (self.nr - 1) + schritte
        return zeitraum(self.art, index // je_jahr, index % je_jahr + 1)

    @property
    def tage_bisher(self) -> int:
        """Tage des Zeitraums bis einschließlich heute (laufende Zeiträume zählen nur die vergangenen Tage);
        0, wenn der Zeitraum noch nicht begonnen hat – Grundlage für „Ø Ladevorgänge pro Tag“."""
        return max(0, (min(self.bis, heute()) - self.von).days + 1)

    def pro_tag(self, anzahl: int) -> Decimal | None:
        return Decimal(anzahl) / self.tage_bisher if self.tage_bisher else None

    @property
    def monate_bisher(self) -> Decimal:
        """Monate des Zeitraums bis heute: volle Monate zählen 1, der laufende anteilig nach vergangenen Tagen."""
        ende = min(self.bis, heute())
        summe, m = Decimal(0), self.von.replace(day=1)
        while m <= ende:
            letzter = _monatsende(m.year, m.month)
            von, bis = max(m, self.von), min(letzter, ende)
            if von <= bis:
                summe += Decimal((bis - von).days + 1) / letzter.day
            m = letzter + timedelta(days=1)
        return summe

    def schnitt(self, s: "Summe", fahrzeuge: int = 1) -> "Schnitt | None":
        """Ø je Monat (und je Fahrzeug, wenn `fahrzeuge` > 1) für die Kacheln."""
        monate = self.monate_bisher
        if not monate or fahrzeuge < 1:
            return None
        teiler = monate * fahrzeuge
        je = "pro Monat und Auto" if fahrzeuge > 1 else "pro Monat"
        return Schnitt(s.kosten / teiler, s.kwh / teiler, Decimal(s.anzahl) / teiler, je,
                       f"Ø über {monate.quantize(Decimal('0.1'))} Monate" + (f" und {fahrzeuge} Fahrzeuge mit Ladungen "
                       "(nur Kosten der Fahrzeuge, ohne Gäste, Pauschalen und nicht zugeordnete Ladungen)"
                       if fahrzeuge > 1 else ""))

    def als_parameter(self) -> dict[str, str]:
        if self.art == "alles":
            return {"art": "alles"}
        if self.art == "frei":
            return {"art": "frei", "von": self.von.isoformat(), "bis": self.bis.isoformat()}
        return {"art": self.art, "jahr": str(self.jahr), "nr": str(self.nr)}


@dataclass
class Schnitt:
    """Durchschnitt je Monat bzw. je Monat und Fahrzeug (Kacheln „Ø … pro Monat und Auto“)."""
    kosten: Decimal
    kwh: Decimal
    anzahl: Decimal
    text: str  # „pro Monat und Auto“ bzw. „pro Monat“
    titel: str  # Tooltip: worüber gemittelt wurde


def zeitraum(art: str, jahr: int, nr: int = 1) -> Zeitraum:
    n = MONATE_JE_ART[art]
    erster = (nr - 1) * n + 1
    return Zeitraum(art, date(jahr, erster, 1), _monatsende(jahr, erster + n - 1), jahr, nr)


def zeitraum_frei(von: date, bis: date) -> Zeitraum:
    if bis < von:
        von, bis = bis, von
    return Zeitraum("frei", von, bis, von.year, 1)


def zeitraum_mit(tag: date, art: str) -> Zeitraum:
    """Zeitraum der gegebenen Art, der den Tag enthält."""
    if art == "frei":
        art = "monat"
    return zeitraum(art, tag.year, (tag.month - 1) // MONATE_JE_ART[art] + 1)


def vormonat(tag: date | None = None) -> Zeitraum:
    return zeitraum_mit((tag or heute()).replace(day=1) - timedelta(days=1), "monat")


def art_wechseln(z: Zeitraum, art: str, standard: Zeitraum) -> Zeitraum:
    """Zeitraum der neuen Art beim Umschalten (z. B. Jahr → Monat): enthält der bisherige Zeitraum den
    Standardmonat (Vormonat), der Zeitraum mit diesem; liegt er davor, mit seinem letzten Tag; danach mit dem ersten."""
    if z.von <= standard.von <= z.bis:
        tag = standard.von
    elif z.bis < standard.von:
        tag = z.bis
    else:
        tag = z.von
    return zeitraum_mit(tag, art)


def zeitraum_alles(db: Session) -> Zeitraum:
    """„Alles“: vom Monat der ersten bis zum Monat der letzten Ladung (Stichtag laut Einstellungen);
    ohne Ladevorgänge der Vormonat."""
    spalte = stichtag_spalte(stichtag_art(db))
    erstes, letztes = db.execute(select(func.min(spalte), func.max(spalte))).one()
    if erstes is None:
        v = vormonat()
        return Zeitraum("alles", v.von, v.bis, v.jahr, 1)
    return Zeitraum("alles", erstes.date().replace(day=1), _monatsende(letztes.year, letztes.month), erstes.year, 1)


def datenjahre(db: Session, z: Zeitraum) -> list[int]:
    """Jahre für die Auswahl: vom ersten Ladevorgang bis heute (und jedenfalls das gewählte Jahr)."""
    erstes = db.scalar(select(func.min(Ladevorgang.ende)))
    von = min(erstes.year if erstes else heute().year, z.jahr)
    return list(range(max(heute().year, z.jahr), von - 1, -1))


def zeitraum_aus_parametern(art: str, jahr: str, nr: str, von: str, bis: str, standard: Zeitraum,
                            db: Session | None = None) -> Zeitraum:
    """Zeitraum aus URL-Parametern; ungültige Angaben ergeben den Standardzeitraum. „alles“ braucht `db`."""
    if art == "alles" and db is not None:
        return zeitraum_alles(db)
    try:
        if art == "frei":
            return zeitraum_frei(date.fromisoformat(von), date.fromisoformat(bis))
        if art in MONATE_JE_ART:
            if not jahr and not nr:  # nichts gewählt (z. B. Aufruf ohne Parameter): Standardzeitraum
                return standard if art == standard.art else zeitraum_mit(standard.von, art)
            j = int(jahr) if jahr else standard.jahr
            if art == "jahr":
                return zeitraum("jahr", j, 1)
            n = int(nr) if nr else 1
            if 1 <= n <= 12 // MONATE_JE_ART[art]:
                return zeitraum(art, j, n)
            return zeitraum_mit(standard.von.replace(year=j), art)
    except ValueError:
        pass
    return standard


# ------------------------------------------------------------------ Summen

def _preis(kosten: Decimal, kwh: Decimal) -> Decimal | None:
    return kosten / kwh if kwh else None


def _anteil(teil: Decimal, ganzes: Decimal) -> Decimal | None:
    return teil / ganzes if ganzes else None


@dataclass
class Summe:
    anzahl: int = 0  # Ladevorgänge mit kWh > 0
    kwh: Decimal = NULL
    kwh_geschaetzt: Decimal = NULL
    kosten: Decimal = NULL  # inklusive Gebühren und Pauschalen
    gebuehren: Decimal = NULL
    kwh_oeffentlich: Decimal = NULL
    kosten_oeffentlich: Decimal = NULL
    kwh_privat: Decimal = NULL
    kosten_privat: Decimal = NULL

    def _verbuchen(self, kwh: Decimal, kosten: Decimal, ladeart: Ladeart) -> None:
        self.kwh += kwh
        self.kosten += kosten
        if ladeart == Ladeart.privat:
            self.kwh_privat += kwh
            self.kosten_privat += kosten
        else:
            self.kwh_oeffentlich += kwh
            self.kosten_oeffentlich += kosten

    def vorgang(self, kwh: Decimal, kosten: Decimal, ladeart: Ladeart, geschaetzt: bool) -> None:
        if kwh > 0:
            self.anzahl += 1
        if geschaetzt:
            self.kwh_geschaetzt += kwh
        self._verbuchen(kwh, kosten, ladeart)

    def gebuehr(self, betrag: Decimal, ladeart: Ladeart) -> None:
        self.gebuehren += betrag
        self._verbuchen(NULL, betrag, ladeart)

    def pauschale(self, kwh: Decimal, betrag: Decimal, ladeart: Ladeart) -> None:
        self._verbuchen(kwh, betrag, ladeart)

    def __add__(self, other: "Summe") -> "Summe":
        return Summe(**{f: getattr(self, f) + getattr(other, f) for f in self.__dataclass_fields__})

    @property
    def leer(self) -> bool:
        return not self.kwh and not self.kosten and not self.anzahl

    @property
    def preis(self) -> Decimal | None:
        return _preis(self.kosten, self.kwh)

    @property
    def preis_oeffentlich(self) -> Decimal | None:
        return _preis(self.kosten_oeffentlich, self.kwh_oeffentlich)

    @property
    def preis_privat(self) -> Decimal | None:
        return _preis(self.kosten_privat, self.kwh_privat)

    @property
    def anteil_oeffentlich_kwh(self) -> Decimal | None:
        return _anteil(self.kwh_oeffentlich, self.kwh)

    @property
    def anteil_oeffentlich_kosten(self) -> Decimal | None:
        return _anteil(self.kosten_oeffentlich, self.kosten)

    @property
    def anteil_geschaetzt(self) -> Decimal | None:
        return _anteil(self.kwh_geschaetzt, self.kwh)


# ------------------------------------------------------------------ Ergebnis

@dataclass
class FahrzeugZeile:
    fahrzeug: Fahrzeug
    summe: Summe = field(default_factory=Summe)
    msp: dict[str, Summe] = field(default_factory=lambda: defaultdict(Summe))
    standorte: dict[str, Summe] = field(default_factory=lambda: defaultdict(Summe))
    fahrer_im_zeitraum: list[str] = field(default_factory=list)
    fahrer_aktuell: list[str] = field(default_factory=list)


@dataclass
class FahrerZeile:
    """Ladevorgänge einer Person (nur Ladekosten, ohne Gebühren)."""
    fahrer: Fahrer
    summe: Summe = field(default_factory=Summe)
    summe_oeffentlich: Summe = field(default_factory=Summe)  # nur öffentliche Ladungen (zweite Topliste)
    fahrzeuge: dict[str, Summe] = field(default_factory=lambda: defaultdict(Summe))  # Kennzeichen → Summe
    msp: dict[str, Summe] = field(default_factory=lambda: defaultdict(Summe))
    standorte: dict[str, Summe] = field(default_factory=lambda: defaultdict(Summe))
    karten: dict[str, Summe] = field(default_factory=dict)  # „MSP · persönlich / Kennzeichen“ → Summe (am Ende gefüllt)
    karten_roh: dict = field(default_factory=lambda: defaultdict(Summe))  # Ladekarte (oder None) → Summe
    cpo: dict[str, Summe] = field(default_factory=lambda: defaultdict(Summe))  # nur öffentliche Ladungen


@dataclass
class KartenZeile:
    """Nutzung einer Ladekarte im Zeitraum (Kosten inkl. kartenbezogener Gebühren)."""
    karte: Ladekarte
    summe: Summe = field(default_factory=Summe)
    zugeordnet: str = "–"  # Zuordnung am Ende des Zeitraums (bzw. heute)


# Sparpotenzial je CPO: Maßstab ist der günstigste MSP je Stromart, wenn er dort mindestens so viele kWh hat
SPAR_MINDEST_KWH = Decimal(20)
STROMARTEN_VERGLEICH = {"AC": "AC", "DC": "DC", None: "ohne Angabe"}


@dataclass
class CpoPosten:
    """Öffentliche Ladungen einer Person mit einer Karte (bzw. einem MSP) und Stromart bei einem CPO."""
    karte: Ladekarte | None
    msp: str
    stromart: str | None  # AC | DC | None (unbekannt)
    summe: Summe = field(default_factory=Summe)
    massstab: tuple[str, Decimal] | None = None  # günstigster MSP dieser Stromart beim CPO: (Name, Ø €/kWh)

    @property
    def ersparnis(self) -> Decimal:
        """Was der günstigste MSP für dieselben kWh weniger gekostet hätte (0, wenn diese Karte selbst der Maßstab ist
        oder nicht teurer war)."""
        if self.massstab is None or self.msp == self.massstab[0] or not self.summe.kwh:
            return Decimal(0)
        return max(Decimal(0), (self.summe.kosten - self.summe.kwh * self.massstab[1]).quantize(Decimal("0.01")))


@dataclass
class CpoPerson:
    """Öffentliche Ladungen einer Person (oder Sammelzeile) bei einem CPO, aufgeteilt nach Ladekarte."""
    name: str
    art: str = "person"  # „person“, oder Sammelzeile: „pool“, „ohne“, „gaeste“
    fahrer: Fahrer | None = None
    summe: Summe = field(default_factory=Summe)
    karten: dict[int | None, tuple[Ladekarte | None, Summe]] = field(default_factory=dict)
    posten: dict[tuple, CpoPosten] = field(default_factory=dict)  # (Karte, MSP, Stromart) → Posten

    def buchen(self, karte: Ladekarte | None, werte: tuple, msp: str = "", stromart: str | None = None) -> None:
        self.summe.vorgang(*werte)
        self.karten.setdefault(karte.id if karte else None, (karte, Summe()))[1].vorgang(*werte)
        stromart = stromart if stromart in ("AC", "DC") else None
        self.posten.setdefault((karte.id if karte else None, msp, stromart),
                               CpoPosten(karte, msp, stromart)).summe.vorgang(*werte)

    @property
    def ersparnis(self) -> Decimal:
        return sum((p.ersparnis for p in self.posten.values()), Decimal(0))

    def ersparnis_karte(self, karte: Ladekarte | None) -> Decimal:
        kid = karte.id if karte else None
        return sum((p.ersparnis for (k, _, _), p in self.posten.items() if k == kid), Decimal(0))

    def posten_karte(self, karte: Ladekarte | None) -> list[CpoPosten]:
        """Posten einer Karte je Stromart (AC, DC, ohne Angabe)."""
        kid = karte.id if karte else None
        return sorted((p for (k, _, _), p in self.posten.items() if k == kid),
                      key=lambda p: list(STROMARTEN_VERGLEICH).index(p.stromart))

    @property
    def karten_liste(self) -> list[tuple[Ladekarte | None, Summe]]:
        """Karten nach Kosten, teuerste zuerst."""
        return sorted(self.karten.values(), key=lambda e: (-e[1].kosten, -e[1].kwh))



@dataclass
class CpoDetail:
    """Öffentliche Ladungen bei einem CPO: Personen (höchste Kosten zuerst, Sammelzeilen am Ende) und MSP."""
    code: str | None
    personen: list[CpoPerson] = field(default_factory=list)
    msp: list[tuple[str, Summe]] = field(default_factory=list)  # günstigster Ø €/kWh zuerst
    # je Stromart (AC, DC, None): MSP mit Summe, günstigster zuerst; Maßstab = günstigster mit Mindestmenge
    msp_stromart: dict[str | None, list[tuple[str, Summe]]] = field(default_factory=dict)
    massstab: dict[str | None, tuple[str, Decimal]] = field(default_factory=dict)

    @property
    def ersparnis(self) -> Decimal:
        return sum((p.ersparnis for p in self.personen), Decimal(0))

    def massstab_berechnen(self) -> None:
        for art, liste in self.msp_stromart.items():
            kandidaten = [(name, s.preis) for name, s in liste if s.preis is not None and s.kwh >= SPAR_MINDEST_KWH]
            if kandidaten:
                self.massstab[art] = min(kandidaten, key=lambda e: e[1])
        for person in self.personen:
            for posten in person.posten.values():
                posten.massstab = self.massstab.get(posten.stromart)

    @property
    def summe(self) -> Summe:
        return sum((p.summe for p in self.personen), Summe())

    @property
    def nur_personen(self) -> list[CpoPerson]:
        return [p for p in self.personen if p.art == "person"]


@dataclass
class TopEintrag:
    fahrer: Fahrer
    kwh: Decimal
    kosten: Decimal
    preis: Decimal
    score: Decimal
    anteil_geschaetzt: Decimal | None


@dataclass
class Topliste:
    guenstigste: list[TopEintrag]
    teuerste: list[TopEintrag]
    p_flotte: Decimal | None  # Ø €/kWh aller Fahrer:innen im Zeitraum
    m: Decimal | None
    m_aus_einstellung: bool
    mindestmenge: Decimal
    teilnehmer: int
    nicht_eindeutig: Summe = field(default_factory=Summe)  # Poolfahrzeug mit mehreren Fahrer:innen
    rangfolge: list[TopEintrag] = field(default_factory=list)  # alle Teilnehmenden, günstigster Score zuerst

    def rang(self, fahrer_id: int) -> int | None:
        """Platz der Person (1 = günstigste); None, wenn sie nicht teilnimmt (unter der Mindestmenge)."""
        return next((i for i, e in enumerate(self.rangfolge, start=1) if e.fahrer.id == fahrer_id), None)


@dataclass
class Auswertung:
    zeitraum: Zeitraum
    stichtag: str
    fahrzeuge: list[FahrzeugZeile]
    msp: list[tuple[str, Summe]]
    fahrzeuge_summe: Summe
    nicht_zugeordnet: Summe  # Ladevorgänge ohne Fahrzeug (ohne Gäste)
    gaeste: Summe  # Ladevorgänge und Gebühren von Gastkarten
    gebuehren_nicht_zuordenbar: Summe
    pauschalen: Summe
    topliste: Topliste
    topliste_oeffentlich: Topliste | None = None  # wie `topliste`, aber nur öffentliche Ladungen
    fahrer: list[FahrerZeile] = field(default_factory=list)
    fahrer_nicht_eindeutig: Summe = field(default_factory=Summe)
    karten: list[KartenZeile] = field(default_factory=list)  # meist genutzte zuerst
    laender: list[tuple[str | None, Summe]] = field(default_factory=list)  # nur Ladevorgänge, meiste kWh zuerst
    cpo: list[tuple[str | None, Summe]] = field(default_factory=list)  # Betreiber-ID (z. B. „AT*MER“), wie laender
    # nur öffentliche Ladevorgänge mit Ladeort, häufigste zuerst (wie die Liste neben der Karte im Dashboard)
    ladeorte: list[tuple[Ladeort, Summe]] = field(default_factory=list)
    cpo_personen: dict[str | None, CpoDetail] = field(default_factory=dict)  # nur öffentlich, siehe Moduldoku
    karten_anzeige: dict = field(default_factory=dict)  # Karten-ID → kartenanzeige.Anzeige (nur mit Personen)

    def sparpotenziale(self) -> list[tuple[CpoDetail, CpoPerson, CpoPosten]]:
        """Alle Posten mit Ersparnis über alle CPO, größte zuerst."""
        liste = [(d, p, x) for d in self.cpo_personen.values() for p in d.personen for x in p.posten.values()
                 if x.ersparnis > 0]
        return sorted(liste, key=lambda e: (-e[2].ersparnis, e[1].name.lower()))

    @property
    def ersparnis_gesamt(self) -> Decimal:
        return sum((d.ersparnis for d in self.cpo_personen.values()), Decimal(0))

    def karte(self, karte):
        """Anzeige (Person/Fahrzeug + MSP) einer Karte im Zeitraum, None ohne Karte."""
        return self.karten_anzeige.get(karte.id) if karte is not None else None

    def karten_meist_genutzt(self, anzahl: int = 10) -> list[KartenZeile]:
        return [k for k in self.karten if k.summe.anzahl > 0][:anzahl]

    def karten_wenigst_genutzt(self, anzahl: int = 10) -> list[KartenZeile]:
        """Am wenigsten genutzte zuerst (auch ungenutzte); ohne Überschneidung mit den meist genutzten."""
        meist = {id(k) for k in self.karten_meist_genutzt(anzahl)}
        return [k for k in reversed(self.karten) if id(k) not in meist][:anzahl]

    @property
    def karten_ungenutzt(self) -> list[KartenZeile]:
        return [k for k in self.karten if k.summe.anzahl == 0]

    @property
    def gesamt(self) -> Summe:
        return (self.fahrzeuge_summe + self.nicht_zugeordnet + self.gaeste + self.gebuehren_nicht_zuordenbar
                + self.pauschalen)

    def fahrzeug(self, fahrzeug_id: int) -> FahrzeugZeile | None:
        return next((z for z in self.fahrzeuge if z.fahrzeug.id == fahrzeug_id), None)

    def fahrer_zeile(self, fahrer_id: int) -> FahrerZeile | None:
        return next((z for z in self.fahrer if z.fahrer.id == fahrer_id), None)

    @property
    def schnitt_je_auto(self) -> Schnitt | None:
        """Kosten, kWh und Ladevorgänge der Fahrzeuge je Monat und Fahrzeug mit Ladungen im Zeitraum."""
        mit_ladungen = [z for z in self.fahrzeuge if z.summe.anzahl > 0]
        if not mit_ladungen:
            return None
        return self.zeitraum.schnitt(sum((z.summe for z in mit_ladungen), Summe()), len(mit_ladungen))

    @property
    def fahrer_summe(self) -> Summe:
        return sum((z.summe for z in self.fahrer), Summe())


def topliste_berechnen(zeilen: list[FahrerZeile], m_einstellung: Decimal | None,
                       mindestmenge: Decimal, anzahl: int = 3, nur_oeffentlich: bool = False) -> Topliste:
    """Bayesscher Preis je Fahrer:in: score = (Kosten + m × P_Schnitt) / (kWh + m) (SPEC 6, Topliste).

    `nur_oeffentlich`: nur öffentliche Ladungen bewerten (Schnitt, Median und Mindestmenge ebenfalls).
    """
    def summe(z: FahrerZeile) -> Summe:
        return z.summe_oeffentlich if nur_oeffentlich else z.summe

    kwh_summe = sum((summe(z).kwh for z in zeilen), NULL)
    p_flotte = _preis(sum((summe(z).kosten for z in zeilen), NULL), kwh_summe)
    kwh_werte = [summe(z).kwh for z in zeilen if summe(z).kwh > 0]
    m = m_einstellung if m_einstellung is not None else (Decimal(median(kwh_werte)) if kwh_werte else None)
    leer = Topliste([], [], p_flotte, m, m_einstellung is not None, mindestmenge, 0)
    if p_flotte is None or m is None:
        return leer
    eintraege = []
    for z in zeilen:
        s = summe(z)
        if s.kwh < mindestmenge or s.kwh <= 0:
            continue
        score = (s.kosten + m * p_flotte) / (s.kwh + m) if s.kwh + m else s.preis
        eintraege.append(TopEintrag(z.fahrer, s.kwh, s.kosten, s.preis, score, s.anteil_geschaetzt))
    eintraege.sort(key=lambda e: (e.score, e.fahrer.name))
    guenstigste = eintraege[:anzahl]
    teuerste = sorted(eintraege[anzahl:], key=lambda e: (-e.score, e.fahrer.name))[:anzahl]
    return Topliste(guenstigste, teuerste, p_flotte, m, m_einstellung is not None, mindestmenge, len(eintraege),
                    rangfolge=eintraege)


def zeitbedingung(spalte, z: Zeitraum):
    return and_(spalte >= datetime.combine(z.von, datetime.min.time()),
                spalte < datetime.combine(z.bis + timedelta(days=1), datetime.min.time()))


def gebuehr_stichtag(g: Gebuehr) -> date | None:
    """Zeitraum-Zuordnung einer Gebühr: Leistungsbeginn, sonst Rechnungsdatum bzw. Abrechnungszeitraum."""
    imp = g.import_
    return (g.zeitraum_von or g.zeitraum_bis or imp.rechnungsdatum or imp.abrechnungszeitraum_von
            or (imp.importiert_am.date() if imp.importiert_am else None))


def auswerten(db: Session, z: Zeitraum, einst: AppEinstellungen, mit_personen: bool = True) -> Auswertung:
    """`mit_personen=False`: ohne Zurechnung zu Fahrer:innen (schneller, z. B. für Vergleichszeiträume)."""
    zeilen: dict[int, FahrzeugZeile] = {}
    personen: dict[int, FahrerZeile] = {}
    personen_cache: dict[tuple, list[Fahrer]] = {}
    msp: dict[str, Summe] = defaultdict(Summe)
    laender: dict[str | None, Summe] = defaultdict(Summe)
    cpo: dict[str | None, Summe] = defaultdict(Summe)
    ladeorte: dict[int, tuple[Ladeort, Summe]] = {}
    nicht_zugeordnet, gaeste, gebuehren_ohne, pauschalen = Summe(), Summe(), Summe(), Summe()
    nicht_eindeutig, nicht_eindeutig_oeffentlich = Summe(), Summe()
    karten_nutzung: dict[int, KartenZeile] = {}
    cpo_gruppen: dict[str | None, dict] = defaultdict(dict)  # CPO → Schlüssel → CpoPerson
    cpo_msp: dict[str | None, dict[str, Summe]] = defaultdict(lambda: defaultdict(Summe))
    cpo_msp_art: dict[str | None, dict] = defaultdict(lambda: defaultdict(lambda: defaultdict(Summe)))
    SAMMEL = {"pool": "Poolfahrzeug (Person nicht eindeutig)", "ohne": "ohne Fahrer:in", "gaeste": "Gäste"}

    def cpo_buchen(v: Ladevorgang, werte: tuple, art: str, fahrer: Fahrer | None = None) -> None:
        if v.ladeart != Ladeart.oeffentlich:
            return
        schl = fahrer.id if fahrer else art
        gruppe = cpo_gruppen[v.betreiber_id].setdefault(
            schl, CpoPerson(fahrer.name if fahrer else SAMMEL[art], art, fahrer))
        gruppe.buchen(v.ladekarte, werte, v.msp.name, v.stromart)
        cpo_msp[v.betreiber_id][v.msp.name].vorgang(*werte)
        cpo_msp_art[v.betreiber_id][v.stromart if v.stromart in ("AC", "DC") else None][v.msp.name].vorgang(*werte)

    def karten_zeile(karte: Ladekarte) -> KartenZeile:
        return karten_nutzung.setdefault(karte.id, KartenZeile(karte))

    def zeile(fz: Fahrzeug) -> FahrzeugZeile:
        if fz.id not in zeilen:
            zeilen[fz.id] = FahrzeugZeile(fz)
        return zeilen[fz.id]

    # Ladevorgänge
    spalte = Ladevorgang.start if einst.stichtag == "beginn" else Ladevorgang.ende
    for v in db.scalars(select(Ladevorgang).where(zeitbedingung(spalte, z))
                        .options(selectinload(Ladevorgang.fahrzeug), selectinload(Ladevorgang.msp),
                                 selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.ladeort))):
        werte = (v.kwh, v.kosten_netto, v.ladeart, v.kwh_geschaetzt)
        msp[v.msp.name].vorgang(*werte)
        laender[v.land].vorgang(*werte)
        cpo[v.betreiber_id].vorgang(*werte)
        if v.ladeort is not None and v.ladeart == Ladeart.oeffentlich:
            ladeorte.setdefault(v.ladeort.id, (v.ladeort, Summe()))[1].vorgang(*werte)
        if v.ladekarte is not None:
            karten_zeile(v.ladekarte).summe.vorgang(*werte)
        if v.fahrzeug is not None:
            fz = zeile(v.fahrzeug)
            fz.summe.vorgang(*werte)
            fz.msp[v.msp.name].vorgang(*werte)
            fz.standorte[v.standort or "unbekannt"].vorgang(*werte)
        elif v.ladekarte is not None and v.ladekarte.ist_gastkarte:
            gaeste.vorgang(*werte)
            if mit_personen:
                cpo_buchen(v, werte, "gaeste")
            continue
        else:
            nicht_zugeordnet.vorgang(*werte)
        if not mit_personen:
            continue

        # Person zum Ladezeitpunkt (Stichtag laut Einstellungen, wie bei der Fahrzeugzuordnung)
        schluessel = (v.ladekarte_id, v.fahrzeug_id, stichtag(v.start, v.ende, einst.stichtag))
        if schluessel not in personen_cache:
            personen_cache[schluessel] = fahrer_beim_laden(db, v.ladekarte, v.fahrzeug, schluessel[2])
        fahrer = personen_cache[schluessel]
        oeffentlich = v.ladeart == Ladeart.oeffentlich
        if len(fahrer) > 1:
            cpo_buchen(v, werte, "pool")
        elif fahrer:
            cpo_buchen(v, werte, "person", fahrer[0])
        else:
            cpo_buchen(v, werte, "ohne")
        if len(fahrer) > 1:
            nicht_eindeutig.vorgang(*werte)
            if oeffentlich:
                nicht_eindeutig_oeffentlich.vorgang(*werte)
        elif fahrer:
            pz = personen.setdefault(fahrer[0].id, FahrerZeile(fahrer[0]))
            pz.summe.vorgang(*werte)
            if oeffentlich:
                pz.summe_oeffentlich.vorgang(*werte)
            pz.fahrzeuge[v.fahrzeug.kennzeichen if v.fahrzeug else "ohne Fahrzeug"].vorgang(*werte)
            pz.msp[v.msp.name].vorgang(*werte)
            pz.standorte[v.standort or "unbekannt"].vorgang(*werte)
            pz.karten_roh[v.ladekarte].vorgang(*werte)
            if oeffentlich:
                pz.cpo[v.betreiber_id or "unbekannt"].vorgang(*werte)

    # Gebühren (Zeitraum über Leistungsbeginn; Fahrzeug am Ende des Leistungszeitraums)
    for g in db.scalars(select(Gebuehr).join(Import).where(
            or_(and_(Gebuehr.zeitraum_von.is_not(None), Gebuehr.zeitraum_von <= z.bis),
                Gebuehr.zeitraum_von.is_(None)))
            .options(selectinload(Gebuehr.import_).selectinload(Import.msp), selectinload(Gebuehr.ladekarte))):
        tag = gebuehr_stichtag(g)
        if tag is None or not z.von <= tag <= z.bis:
            continue
        ladeart = g.import_.ladeart
        if g.import_.msp:
            msp[g.import_.msp.name].gebuehr(g.betrag_netto, ladeart)
        karte = g.ladekarte
        if karte is not None:
            karten_zeile(karte).summe.gebuehr(g.betrag_netto, ladeart)
        if karte is not None and karte.ist_gastkarte:
            gaeste.gebuehr(g.betrag_netto, ladeart)
            continue
        fz = fahrzeug_fuer_karte(db, karte, g.zeitraum_bis or tag) if karte else None
        if fz is None:
            gebuehren_ohne.gebuehr(g.betrag_netto, ladeart)
        else:
            zeile(fz).summe.gebuehr(g.betrag_netto, ladeart)
            if g.import_.msp:
                zeile(fz).msp[g.import_.msp.name].gebuehr(g.betrag_netto, ladeart)

    # Pauschalen
    for p in db.scalars(select(Pauschale).where(Pauschale.monat >= z.von, Pauschale.monat <= z.bis)):
        pauschalen.pauschale(p.kwh, p.betrag_netto, p.ladeart)

    # Fahrer:innen je Fahrzeug
    stichtag_heute = heute()
    for fz_id, fz_zeile in (zeilen.items() if mit_personen else ()):
        perioden = db.scalars(select(ZuordnungFahrerFahrzeug).where(
            ZuordnungFahrerFahrzeug.fahrzeug_id == fz_id, ZuordnungFahrerFahrzeug.gueltig_ab <= z.bis,
            or_(ZuordnungFahrerFahrzeug.gueltig_bis.is_(None), ZuordnungFahrerFahrzeug.gueltig_bis >= z.von))
            .options(selectinload(ZuordnungFahrerFahrzeug.fahrer))
            .order_by(ZuordnungFahrerFahrzeug.gueltig_ab)).all()
        fz_zeile.fahrer_im_zeitraum = list(dict.fromkeys(p.fahrer.name for p in perioden))
        fz_zeile.fahrer_aktuell = [f.name for f in fahrer_von_fahrzeug_am(db, fz_id, stichtag_heute)]

    # Ladekarten-Nutzung: alle aktiven Karten (auch ungenutzte) plus genutzte inaktive
    for karte in db.scalars(select(Ladekarte).where(Ladekarte.aktiv).options(selectinload(Ladekarte.msp))):
        karten_zeile(karte)
    stand = min(z.bis, heute())
    for kz in karten_nutzung.values():
        if kz.karte.ist_gastkarte:
            kz.zugeordnet = "Gäste"
        elif zo := karten_zuordnung_am(db, kz.karte.id, stand):
            kz.zugeordnet = zo.ziel_name
    # Anzeigenamen der Karten im Zeitraum (wer sie hatte) – für Tabellen, Reports und CPO-Details
    karten_anzeige = {}
    if mit_personen:
        from app.services import kartenanzeige
        alle_karten = {kz.karte for kz in karten_nutzung.values()}
        alle_karten |= {k for g in cpo_gruppen.values() for p in g.values() for k, _ in p.karten.values() if k}
        alle_karten |= {k for pz in personen.values() for k in pz.karten_roh if k}
        karten_anzeige = kartenanzeige.fuer_zeitraum(db, alle_karten, z.von, z.bis, einst.stichtag)
        for pz in personen.values():
            for k, s in sorted(pz.karten_roh.items(), key=lambda e: -e[1].kosten):
                if k is None:
                    pz.karten["ohne Karte"] = s
                else:
                    a = karten_anzeige[k.id]
                    pz.karten[f"{a.msp} · {a.relativ(pz.fahrer.id)}{' …' + a.zusatz if a.zusatz else ''}"] = s
    karten_zeilen = sorted(karten_nutzung.values(),
                           key=lambda k: (-k.summe.anzahl, -k.summe.kwh, k.karte.kennung.lower()))

    fahrzeug_zeilen = sorted(zeilen.values(), key=lambda r: r.fahrzeug.kennzeichen)
    fahrer_zeilen = sorted(personen.values(), key=lambda r: r.fahrer.name.lower())
    topliste = topliste_berechnen(fahrer_zeilen, einst.topliste_m, einst.topliste_mindestmenge)
    topliste.nicht_eindeutig = nicht_eindeutig
    topliste_oeffentlich = topliste_berechnen(fahrer_zeilen, einst.topliste_m, einst.topliste_mindestmenge,
                                              nur_oeffentlich=True)
    topliste_oeffentlich.nicht_eindeutig = nicht_eindeutig_oeffentlich
    nach_preis = lambda e: (e[1].preis is None, e[1].preis or 0, e[0].lower())  # noqa: E731
    cpo_details = {}
    for code, gruppen in cpo_gruppen.items():
        d = CpoDetail(code, sorted(gruppen.values(), key=lambda g: (g.art != "person", -g.summe.kosten, g.name.lower())),
                      sorted(cpo_msp[code].items(), key=nach_preis),
                      {art: sorted(m.items(), key=nach_preis)
                       for art, m in sorted(cpo_msp_art[code].items(), key=lambda e: list(STROMARTEN_VERGLEICH).index(e[0]))})
        d.massstab_berechnen()
        cpo_details[code] = d
    return Auswertung(
        zeitraum=z, stichtag=einst.stichtag, fahrzeuge=fahrzeug_zeilen,
        msp=sorted(msp.items(), key=lambda e: e[0].lower()),
        fahrzeuge_summe=sum((r.summe for r in fahrzeug_zeilen), Summe()),
        nicht_zugeordnet=nicht_zugeordnet, gaeste=gaeste, gebuehren_nicht_zuordenbar=gebuehren_ohne,
        pauschalen=pauschalen, topliste=topliste, topliste_oeffentlich=topliste_oeffentlich, fahrer=fahrer_zeilen,
        karten=karten_zeilen, fahrer_nicht_eindeutig=nicht_eindeutig,
        laender=sorted(laender.items(), key=lambda e: (-e[1].kwh, e[0] or "~")),
        cpo=sorted(cpo.items(), key=lambda e: (e[0] is None, -e[1].kwh, e[0] or "")),
        ladeorte=sorted(ladeorte.values(), key=lambda e: (-e[1].anzahl, -e[1].kwh, e[0].adresse)),
        cpo_personen=cpo_details,
        karten_anzeige=karten_anzeige)


# ------------------------------------------------------------------ Vergleich mit Vorperioden (Dashboard)

@dataclass
class Vergleich:
    """Abweichung der Kennzahlen vom Durchschnitt der Vorperioden (relativ, 0,12 = +12 %)."""
    basis: str  # z. B. „07/2026 – 09/2026“
    perioden: int  # Anzahl Vorperioden mit Daten
    referenz: Summe  # Summe über die Vorperioden
    kwh: Decimal | None
    kosten: Decimal | None
    preis: Decimal | None
    anzahl: Decimal | None
    oeffentlich_pp: Decimal | None  # Anteil öffentlich (kWh): Differenz in Prozentpunkten

    @property
    def kwh_schnitt(self) -> Decimal:
        return self.referenz.kwh / self.perioden

    @property
    def kosten_schnitt(self) -> Decimal:
        return self.referenz.kosten / self.perioden

    @property
    def anzahl_schnitt(self) -> Decimal:
        return Decimal(self.referenz.anzahl) / self.perioden


def _relativ(wert: Decimal, referenz: Decimal | None) -> Decimal | None:
    return (Decimal(wert) - referenz) / referenz if referenz else None


def vergleichen(aktuell: Summe, vorher: list[tuple[Zeitraum, Summe]]) -> Vergleich | None:
    """Vergleich mit dem Durchschnitt der Vorperioden; Perioden ohne Daten zählen nicht mit."""
    mit_daten = [(z, s) for z, s in vorher if not s.leer]
    if not mit_daten:
        return None
    n = len(mit_daten)
    ref = sum((s for _, s in mit_daten), Summe())
    zeitraeume = sorted(z for z, _ in mit_daten)
    basis = zeitraeume[0].label if n == 1 else f"{zeitraeume[0].label} – {zeitraeume[-1].label}"
    oeff_akt, oeff_ref = aktuell.anteil_oeffentlich_kwh, ref.anteil_oeffentlich_kwh
    return Vergleich(
        basis=basis, perioden=n, referenz=ref,
        kwh=_relativ(aktuell.kwh, ref.kwh / n), kosten=_relativ(aktuell.kosten, ref.kosten / n),
        preis=_relativ(aktuell.preis, ref.preis) if aktuell.preis is not None else None,
        anzahl=_relativ(Decimal(aktuell.anzahl), Decimal(ref.anzahl) / n),
        oeffentlich_pp=(oeff_akt - oeff_ref) * 100 if oeff_akt is not None and oeff_ref is not None else None)
