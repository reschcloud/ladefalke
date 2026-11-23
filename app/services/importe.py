"""Import-Ablauf (SPEC Abschnitt 4): Hochladen → Vorschau → Bestätigen; Löschen macht alles rückgängig.

Die Vorschau wird bei jedem Aufruf neu aus der gespeicherten Datei berechnet (Parser sind
deterministisch); erst `bestaetigen` schreibt Ladevorgänge, Gebühren und neue Ladekarten.
"""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import einstellungen
from app.formatierung import datum, geld
from app.models import (Fahrer, Fahrzeug, Gebuehr, Import, ImportStatus, KennungTyp, Ladeart, Ladekarte, Ladeort, Ladevorgang,
                        Msp, ZuordnungQuelle)
from app.parsers import PARSER
from app.parsers import basis
from app.services import app_einstellungen, protokoll
from app.services.aufloesung import fahrer_beim_laden, fahrzeug_fuer_karte, stichtag, stichtag_art
from app.services import evse
from app.services.geokodierung import geokodieren
from app.services.ladeorte import ladeort_fuer
from app.services.ladeprofil import schaetzgrundlage
from app.services.schaetzung import kwh_schaetzen
from app.services.zuordnung import karten_zuordnung_am

class ImportFehler(ValueError):
    pass


def upload_verzeichnis() -> Path:
    return Path(einstellungen().upload_dir)


def datei(imp: Import) -> Path:
    return upload_verzeichnis() / imp.datei_pfad


# ------------------------------------------------------------------ Hochladen

def _strompreis_vorbelegung(msp: Msp | None, parser_key: str | None, ladeart: Ladeart) -> Decimal | None:
    """Strompreis für Dateien ohne Kosten (Parser liefert_kosten = False) bzw. private Importe: Vorgabe des MSP."""
    from app.parsers import PARSER
    parser = PARSER.get(parser_key or "")
    if msp is None or not (ladeart == Ladeart.privat or (parser is not None and not parser.liefert_kosten)):
        return None
    return msp.strompreis


def hochladen(db: Session, dateiname: str, inhalt: bytes) -> Import:
    if not inhalt:
        raise ImportFehler("Die Datei ist leer.")
    datei_hash = hashlib.sha256(inhalt).hexdigest()
    if fertig := db.scalar(select(Import).where(Import.datei_hash == datei_hash,
                                                Import.status == ImportStatus.abgeschlossen)):
        raise ImportFehler(f"Diese Datei wurde bereits importiert ({fertig.bezeichnung} "
                           f"vom {datum(fertig.importiert_am)}).")
    if offen := db.scalar(select(Import).where(Import.datei_hash == datei_hash,
                                               Import.status == ImportStatus.vorschau)):
        return offen  # gleiche Datei erneut hochgeladen – bestehende Vorschau weiterverwenden

    verzeichnis = upload_verzeichnis()
    verzeichnis.mkdir(parents=True, exist_ok=True)
    name = datei_hash + Path(dateiname).suffix.lower()
    if not (verzeichnis / name).exists():
        (verzeichnis / name).write_bytes(inhalt)

    from app.services import parser_verwaltung
    parser_key = parser_verwaltung.erkennen(db, verzeichnis / name)
    msp = db.scalar(select(Msp).where(Msp.parser_key == parser_key)) if parser_key else None
    ladeart = msp.standard_ladeart if msp else Ladeart.oeffentlich
    imp = Import(dateiname=Path(dateiname).name[:300], datei_pfad=name, datei_hash=datei_hash,
                 parser_key=parser_key, msp_id=msp.id if msp else None, ladeart=ladeart,
                 strompreis_netto_kwh=_strompreis_vorbelegung(msp, parser_key, ladeart),
                 status=ImportStatus.vorschau, warnungen=[])
    db.add(imp)
    db.flush()
    return imp


# ------------------------------------------------------------------ Vorschau

@dataclass
class KartenInfo:
    kennung: str
    kennung_typ: KennungTyp
    vertragsnummer: str | None
    bezeichnung: str | None
    gast: bool
    karte: Ladekarte | None
    anzahl: int = 0
    kwh: Decimal = Decimal(0)
    kosten: Decimal = Decimal(0)
    # Vorschlag je Zuordnung: (Fahrer-ID oder None, Fahrzeug-ID oder None) → (Fahrer:in, Fahrzeug); bei einer
    # Personenkarte das Fahrzeug, das die Person am Ladetag fuhr (None = keines)
    ziele: dict = field(default_factory=dict)

    @property
    def preis(self) -> Decimal | None:
        return self.kosten / self.kwh if self.kwh else None


@dataclass
class VorgangVorschau:
    v: basis.Vorgang
    kwh: Decimal  # bei Schätzung bereits mit den Werten des Fahrzeugs
    kosten: Decimal
    karte: Ladekarte | None
    fahrzeug: Fahrzeug | None
    duplikat: bool
    fahrer: list[str] = field(default_factory=list)  # Fahrer:in(nen) am Stichtag des Ladevorgangs

    @property
    def preis(self) -> Decimal | None:
        return self.kosten / self.kwh if self.kwh else None


@dataclass
class GebuehrVorschau:
    g: basis.Gebuehr
    karte: Ladekarte | None
    duplikat: bool = False  # Rechnung schon importiert (z. B. als PDF, jetzt als CSV) – wird übersprungen


@dataclass
class Vorschau:
    ergebnis: basis.Ergebnis | None = None
    fehler: str | None = None
    vorgaenge: list[VorgangVorschau] = field(default_factory=list)
    gebuehren: list[GebuehrVorschau] = field(default_factory=list)
    karten: list[KartenInfo] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)

    @property
    def neue(self) -> list[VorgangVorschau]:
        return [x for x in self.vorgaenge if not x.duplikat]

    @property
    def summe_kwh(self) -> Decimal:
        return sum((x.kwh for x in self.neue), Decimal(0))

    @property
    def summe_kosten(self) -> Decimal:
        return sum((x.kosten for x in self.neue), Decimal(0))

    @property
    def neue_gebuehren(self) -> list[GebuehrVorschau]:
        return [x for x in self.gebuehren if not x.duplikat]

    @property
    def summe_gebuehren(self) -> Decimal:
        return sum((x.g.betrag_netto for x in self.neue_gebuehren), Decimal(0))

    @property
    def unbekannte_karten(self) -> list[KartenInfo]:
        return [k for k in self.karten if k.karte is None]


def _karte_finden(db: Session, msp_id: int | None, kennung: str | None, vertragsnummer: str | None):
    """Karte über die Kennung; nur ohne Kennung (z. B. Gebühr eines Vertrags) über die Vertragsnummer."""
    if msp_id is None:
        return None
    if kennung:
        return db.scalar(select(Ladekarte).where(Ladekarte.msp_id == msp_id, Ladekarte.kennung == kennung))
    if vertragsnummer:
        return db.scalar(select(Ladekarte).where(Ladekarte.msp_id == msp_id,
                                                 Ladekarte.vertragsnummer == vertragsnummer))
    return None


def _ist_duplikat(db: Session, msp_id: int | None, v: basis.Vorgang, karte: Ladekarte | None) -> bool:
    if msp_id is None:
        return False
    q = select(Ladevorgang.id).where(Ladevorgang.msp_id == msp_id)
    if v.externe_id:
        q = q.where(Ladevorgang.externe_id == v.externe_id)
    else:
        # ohne kWh: geschätzte kWh hängen vom Fahrzeug ab und können sich später ändern
        q = q.where(Ladevorgang.start == v.start, Ladevorgang.ende == v.ende)
        q = q.where(Ladevorgang.ladekarte_id == karte.id if karte else
                    Ladevorgang.roh_kartenkennung == v.kartenkennung)
    return db.scalar(q.limit(1)) is not None


def vorschau_berechnen(db: Session, imp: Import) -> Vorschau:
    from app.services import parser_verwaltung
    if imp.parser_key and imp.parser_key in PARSER and imp.parser_key not in parser_verwaltung.aktive(db):
        return Vorschau(fehler=f"Der Parser „{PARSER[imp.parser_key].name}“ ist deaktiviert (Stammdaten → MSP). "
                               f"Derzeit unterstützt: {parser_verwaltung.formate(db)}.")
    if not imp.parser_key or imp.parser_key not in PARSER:
        return Vorschau(fehler=f"Dateiformat nicht erkannt. Derzeit unterstützt: {parser_verwaltung.formate(db)}.")
    try:
        ergebnis = PARSER[imp.parser_key].parsen_datei(datei(imp), imp.dateiname)
    except basis.ParserFehler as e:
        return Vorschau(fehler=f"Die Datei konnte nicht gelesen werden: {e}")
    except Exception as e:  # z. B. falscher Parser für diese Datei
        return Vorschau(fehler=f"Die Datei passt nicht zum gewählten Parser ({type(e).__name__}: {e}).")
    art = stichtag_art(db)

    vs = Vorschau(ergebnis=ergebnis, warnungen=list(ergebnis.warnungen))
    if imp.msp_id is None:
        vs.warnungen.append("Kein MSP gewählt – Karten können nicht erkannt werden.")
    preis = imp.strompreis_netto_kwh
    karten: dict[str, KartenInfo] = {}
    # (Karte, Tag) → (Fahrzeug, Fahrer:innen beim Laden, Person der Karte oder None)
    fahrzeug_cache: dict[tuple, tuple[Fahrzeug | None, list[str], Fahrer | None]] = {}
    gesehen: set[str] = set()
    ohne_preis = 0
    grundlage = schaetzgrundlage(db)

    for v in ergebnis.vorgaenge:
        info = None
        if v.kartenkennung:
            info = karten.get(v.kartenkennung)
            if info is None:
                info = KartenInfo(kennung=v.kartenkennung, kennung_typ=v.kennung_typ,
                                  vertragsnummer=v.vertragsnummer, bezeichnung=v.kartenbezeichnung,
                                  gast=v.gastkarte, karte=_karte_finden(db, imp.msp_id, v.kartenkennung, None))
                karten[v.kartenkennung] = info
        karte = info.karte if info else None

        if v.kosten_netto is not None:
            kosten = v.kosten_netto
        elif preis is not None:
            kosten = basis.cent(v.kwh * preis)
        else:
            kosten, ohne_preis = Decimal(0), ohne_preis + 1

        tag = stichtag(v.start, v.ende, art)
        schluessel = (karte.id if karte else None, tag)
        if schluessel not in fahrzeug_cache:
            fz = fahrzeug_fuer_karte(db, karte, tag)
            z = karten_zuordnung_am(db, karte.id, tag) if karte and not karte.ist_gastkarte else None
            fahrzeug_cache[schluessel] = (fz, [f.name for f in fahrer_beim_laden(db, karte, fz, tag)],
                                          z.fahrer if z else None)
        fahrzeug, fahrer, kartenperson = fahrzeug_cache[schluessel]
        kwh = kwh_schaetzen(v.schaetzbasis, grundlage.fuer(fahrzeug.id if fahrzeug else None)) if v.schaetzbasis else v.kwh

        duplikat = (v.externe_id in gesehen) if v.externe_id else False
        duplikat = duplikat or _ist_duplikat(db, imp.msp_id, v, karte)
        if v.externe_id:
            gesehen.add(v.externe_id)
        vs.vorgaenge.append(VorgangVorschau(v=v, kwh=kwh, kosten=kosten, karte=karte, fahrzeug=fahrzeug,
                                            duplikat=duplikat, fahrer=fahrer))
        if info and not duplikat:
            info.anzahl += 1
            info.kwh += kwh
            info.kosten += kosten
            if fahrzeug or kartenperson:
                info.ziele[(kartenperson.id if kartenperson else None, fahrzeug.id if fahrzeug else None)] = \
                    (kartenperson, fahrzeug)

    r = ergebnis.rechnung
    frueher = db.scalar(select(Import).where(
        Import.msp_id == imp.msp_id, Import.rechnungsnummer == r.nummer, Import.status == ImportStatus.abgeschlossen,
        Import.id != imp.id)) if r.nummer and imp.msp_id else None
    for g in ergebnis.gebuehren:
        if g.kartenkennung and g.kartenkennung not in karten:
            # Karte nur mit Gebühr (z. B. Grundgebühr ohne Ladevorgang) – ebenfalls zum Anlegen anbieten
            karten[g.kartenkennung] = KartenInfo(
                kennung=g.kartenkennung, kennung_typ=g.kennung_typ, vertragsnummer=g.vertragsnummer,
                bezeichnung=g.kartenbezeichnung, gast=False,
                karte=_karte_finden(db, imp.msp_id, g.kartenkennung, None))
        karte = (karten[g.kartenkennung].karte if g.kartenkennung
                 else _karte_finden(db, imp.msp_id, None, g.vertragsnummer))
        vs.gebuehren.append(GebuehrVorschau(g=g, karte=karte, duplikat=frueher is not None))
    vs.karten = sorted(karten.values(), key=lambda k: (k.karte is not None, k.kennung.lower()))

    # ---------------------------------------------------------------- Plausibilität
    if ohne_preis:
        vs.warnungen.append(f"{ohne_preis} Vorgänge ohne Kosten – bitte Strompreis angeben.")
    if duplikate := sum(1 for x in vs.vorgaenge if x.duplikat):
        vs.warnungen.append(f"{duplikate} Vorgänge wurden bereits importiert und werden übersprungen.")
    if r.summe_netto is not None:  # alle Positionen der Datei, auch bereits importierte
        positionen = sum((x.kosten for x in vs.vorgaenge), Decimal(0)) + \
            sum((x.g.betrag_netto for x in vs.gebuehren), Decimal(0))
        if abs(positionen - r.summe_netto) > Decimal("0.01"):
            vs.warnungen.append(f"Summe der Positionen {positionen} € ≠ Rechnungssumme netto {r.summe_netto} €.")
    if frueher is not None:
        vs.warnungen.append(f"Rechnung {r.nummer} wurde bereits mit der Datei {frueher.dateiname} importiert (andere "
                            "Datei, z. B. PDF statt CSV): ihre Gebühren werden übersprungen, Ladevorgänge nur "
                            "übernommen, soweit sie noch fehlen.")
    return vs


# ------------------------------------------------------------------ Bestätigen

@dataclass
class KartenEntscheidung:
    anlegen: bool = True
    bezeichnung: str | None = None
    gast: bool = False


def bestaetigen(db: Session, imp: Import, entscheidungen: dict[str, KartenEntscheidung],
                stromart_vorgabe: str | None = None) -> Vorschau:
    """Wie `_bestaetigen`; im Änderungsprotokoll ein Sammeleintrag für den Import (neue Ladekarten einzeln, mit dem
    Import als Zusammenhang; neue Ladeorte nur gezählt)."""
    with protokoll.kontext(db, f"Import {imp.dateiname}"), protokoll.still(db, Ladeort):
        vs = _bestaetigen(db, imp, entscheidungen, stromart_vorgabe)
    msp = db.get(Msp, imp.msp_id)
    felder = {"MSP": [None, msp.name if msp else None], "Ladevorgänge": [None, str(len(vs.neue))],
              "Gebühren": [None, str(len(vs.neue_gebuehren))]}
    if imp.rechnungsnummer:
        felder["Rechnung"] = [None, imp.rechnungsnummer]
    if imp.rechnungssumme_netto is not None:
        felder["Summe netto"] = [None, geld(imp.rechnungssumme_netto)]
    protokoll.eintragen(db, "importiert", "import", imp.dateiname, imp.id, felder)
    return vs


def _bestaetigen(db: Session, imp: Import, entscheidungen: dict[str, KartenEntscheidung],
                 stromart_vorgabe: str | None = None) -> Vorschau:
    """Schreibt den Import. `entscheidungen`: je unbekannter Kartenkennung anlegen oder ignorieren.
    `stromart_vorgabe`: AC/DC für Vorgänge, deren Rechnung keine Stromart angibt (vorbelegt mit der Vorgabe des MSP)."""
    if imp.status != ImportStatus.vorschau:
        raise ImportFehler("Dieser Import ist bereits abgeschlossen.")
    if imp.msp_id is None:
        raise ImportFehler("Bitte einen MSP wählen.")
    if db.scalar(select(Import.id).where(Import.datei_hash == imp.datei_hash,
                                         Import.status == ImportStatus.abgeschlossen)):
        raise ImportFehler("Diese Datei wurde inzwischen bereits importiert.")
    vs = vorschau_berechnen(db, imp)
    if vs.fehler:
        raise ImportFehler(vs.fehler)
    if imp.strompreis_netto_kwh is None and any(x.v.kosten_netto is None for x in vs.vorgaenge):
        raise ImportFehler("Bitte einen Strompreis angeben – die Datei enthält keine Kosten.")

    for info in vs.unbekannte_karten:
        e = entscheidungen.get(info.kennung, KartenEntscheidung(gast=info.gast))
        if e.anlegen:
            info.karte = Ladekarte(msp_id=imp.msp_id, kennung=info.kennung, kennung_typ=info.kennung_typ,
                                   vertragsnummer=info.vertragsnummer,
                                   bezeichnung=(e.bezeichnung or info.bezeichnung or "")[:200] or None,
                                   ist_gastkarte=e.gast)
            db.add(info.karte)
    db.flush()
    karten = {info.kennung: info.karte for info in vs.karten}
    grundlage = schaetzgrundlage(db)  # nach dem Commit wird mit den neuen Daten ohnehin neu geschätzt
    art = stichtag_art(db)
    ladeorte: dict = {}
    msp = db.get(Msp, imp.msp_id)
    cpo_vorgabe = msp.cpo_standard if msp else None  # für Vorgänge, deren Datei keinen CPO liefert

    for x in vs.neue:
        v = x.v
        karte = karten.get(v.kartenkennung) if v.kartenkennung else None
        fahrzeug = fahrzeug_fuer_karte(db, karte, stichtag(v.start, v.ende, art))
        kwh = kwh_schaetzen(v.schaetzbasis, grundlage.fuer(fahrzeug.id if fahrzeug else None)) if v.schaetzbasis else v.kwh
        ort = evse.ermitteln(imp.parser_key, v.ladepunkt, v.rohdaten)
        db.add(Ladevorgang(
            import_id=imp.id, msp_id=imp.msp_id, externe_id=v.externe_id,
            ladekarte_id=karte.id if karte else None, roh_kartenkennung=v.kartenkennung,
            start=v.start, ende=v.ende, kwh=kwh, kwh_geschaetzt=v.kwh_geschaetzt, kosten_netto=x.kosten,
            standort=(v.standort or "")[:300] or None, ladepunkt=(v.ladepunkt or "")[:100] or None,
            evse_id=ort.evse_id, betreiber_id=ort.betreiber_id or cpo_vorgabe, land=ort.land,
            betreiber_quelle="rechnung" if ort.betreiber_id else "vorgabe" if cpo_vorgabe else None,
            ladeort=ladeort_fuer(db, v.standort, ort.land, ladeorte),
            ladeart=imp.ladeart, stromart=v.stromart or stromart_vorgabe,
            stromart_quelle="rechnung" if v.stromart else "vorgabe" if stromart_vorgabe else None,
            fahrzeug_id=fahrzeug.id if fahrzeug else None,
            zuordnung_quelle=ZuordnungQuelle.automatisch if fahrzeug else ZuordnungQuelle.keine,
            rohdaten=v.rohdaten, ust_betrag=v.ust_betrag, ust_satz=v.ust_satz, ust_land=v.ust_land))
    for x in vs.neue_gebuehren:
        g = x.g
        karte = x.karte or (karten.get(g.kartenkennung) if g.kartenkennung else None)
        db.add(Gebuehr(import_id=imp.id, ladekarte_id=karte.id if karte else None,
                       roh_kartenkennung=g.kartenkennung or g.vertragsnummer, zeitraum_von=g.zeitraum_von,
                       zeitraum_bis=g.zeitraum_bis, bezeichnung=g.bezeichnung[:300], betrag_netto=g.betrag_netto,
                       ust_betrag=g.ust_betrag, ust_satz=g.ust_satz, ust_land=g.ust_land))

    for ladeort in ladeorte.values():  # neue Ladeorte verorten (bekannte haben ihre Koordinaten schon)
        if ladeort is not None and ladeort.genauigkeit is None:
            geokodieren(db, ladeort)

    r = vs.ergebnis.rechnung
    imp.rechnungsnummer, imp.rechnungsdatum, imp.rechnungssumme_netto = r.nummer, r.datum, r.summe_netto
    imp.abrechnungszeitraum_von, imp.abrechnungszeitraum_bis = r.zeitraum_von, r.zeitraum_bis
    imp.warnungen = vs.warnungen
    imp.status = ImportStatus.abgeschlossen
    if imp.strompreis_netto_kwh is not None and imp.msp_id and (msp := db.get(Msp, imp.msp_id)) is not None:
        msp.strompreis = imp.strompreis_netto_kwh  # zuletzt verwendeter Preis = Vorbelegung beim nächsten Import
    imp.importiert_am = datetime.now(timezone.utc)
    db.flush()
    return vs


# ------------------------------------------------------------------ Löschen

def loeschen(db: Session, imp: Import) -> Path | None:
    """Löscht Import samt Vorgängen und Gebühren. Liefert die Datei, die nach dem Commit entfernt werden kann."""
    pfad = imp.datei_pfad
    if imp.status == ImportStatus.abgeschlossen:  # verworfene Vorschauen haben nichts geändert
        anzahl = db.scalar(select(func.count()).select_from(Ladevorgang).where(Ladevorgang.import_id == imp.id))
        protokoll.eintragen(db, "geloescht", "import", imp.dateiname, imp.id,
                            {"Ladevorgänge": [str(anzahl), None], "Gebühren": [str(len(imp.gebuehren)), None]})
    db.delete(imp)
    db.flush()
    if db.scalar(select(Import.id).where(Import.datei_pfad == pfad).limit(1)) is None:
        return upload_verzeichnis() / pfad
    return None


# ------------------------------------------------------------------ Offene Vorschauen der Reihe nach abarbeiten

def offene_vorschauen(db: Session) -> list[int]:
    """IDs aller offenen Vorschauen in Upload-Reihenfolge."""
    return list(db.scalars(select(Import.id).where(Import.status == ImportStatus.vorschau).order_by(Import.id)))


def naechste_vorschau(db: Session, nach_id: int) -> int | None:
    """Nächste offene Vorschau nach `nach_id`; am Ende wieder von vorn, sonst None."""
    offen = [i for i in offene_vorschauen(db) if i != nach_id]
    return next((i for i in offen if i > nach_id), offen[0] if offen else None)
