"""Ladevorgänge: filterbare Liste und Detailseite; bearbeitbar ist nur das Fahrzeug."""
import json
import re
from datetime import timedelta
from urllib.parse import urlencode
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.services import kartenanzeige, protokoll, zuordnung
from app.formatierung import datum_lesen
from app.models import Fahrer, Fahrzeug, Import, Ladeart, Ladekarte, Ladeort, Ladevorgang, Msp, ZuordnungQuelle
from app.routers.auswertungen import datei_antwort, zeitraum_navigation
from app.services.auswertung import Zeitraum, vormonat, zeitraum_alles, zeitraum_aus_parametern
from app.services.aufloesung import (fahrzeug_setzen, person_text, personen_beim_laden, personen_filter,
                                     stichtag_art, stichtag_spalte, vorgang_aktualisieren)
from app.services import app_einstellungen, karte as kartendaten, kosten as kosten_dienst, stromart as stromart_dienst
from app.services.app_einstellungen import AppEinstellungen
from app.services.evse import LAENDER, landname
from app.services.geokodierung import geokodieren, plz_setzen, verortung_text
from app.services.ladeorte import PLZ_MUSTER, ladeort_fuer
from app.services.export import Spalte, Tabelle
from app.services.ladeprofil import leistung, unplausibel_grund, unplausibel_sql
from app.web import art_vorgabe, meldung, render, weiter

router = APIRouter(prefix="/ladevorgaenge")

MAX_ZEILEN = 50  # Liste; die Karte und der Export zeigen alle
# Sortierung der Liste (Klick auf den Spaltenkopf): Spalte → erste Richtung beim Anklicken; Standard: neueste zuerst
SORTIERBAR = {"zeit": "ab", "dauer": "ab", "kwh": "ab", "kosten": "ab", "preis": "ab"}


ZEITRAUM_SCHLUESSEL = ("art", "jahr", "nr", "von", "bis")
PREIS_STUFE = Decimal("0.02")  # Breite der Histogramm-Säulen und Raster des Preisfilters (€/kWh)


def zeitraum_lesen(db: Session, art: str, jahr: str, nr: str, von: str, bis: str) -> Zeitraum:
    """Zeitraum wie im Dashboard (Monat/Quartal/Halbjahr/Jahr/Alles), Standard „Alles“. Nur `von`/`bis` (z. B. Links
    aus den Aufgaben) ergeben einen festen Zeitraum, der sich mit ◀ ▶ verschieben lässt."""
    if not art and (von or bis):
        alles = zeitraum_alles(db)
        art, von, bis = "frei", von or alles.von.isoformat(), bis or alles.bis.isoformat()
    return zeitraum_aus_parametern(art or "alles", jahr, nr, von, bis, standard=vormonat(), db=db)


def zeitraum_werte(z: Zeitraum) -> dict[str, str]:
    """URL-Parameter des Zeitraums; „Alles“ ist der Standard und braucht keine."""
    return {} if z.art == "alles" else z.als_parameter()


def preis_lesen(text: str) -> Decimal | None:
    try:
        wert = Decimal((text or "").replace(",", ".").strip())
    except ArithmeticError:
        return None
    return wert if wert.is_finite() and wert >= 0 else None


def preis_bedingung(preis_min: Decimal | None, preis_max: Decimal | None):
    """Ø €/kWh je Vorgang im Bereich (inklusive); Vorgänge ohne kWh fallen bei gesetztem Filter heraus."""
    if preis_min is None and preis_max is None:
        return None
    preis = Ladevorgang.kosten / func.nullif(Ladevorgang.kwh, 0)
    teile = [Ladevorgang.kwh > 0]
    if preis_min is not None:
        teile.append(preis >= preis_min)
    if preis_max is not None:
        teile.append(preis <= preis_max)
    return and_(*teile)


def preis_histogramm(db: Session, bedingungen, preis_min: Decimal | None, preis_max: Decimal | None) -> dict:
    """Verteilung der Ø €/kWh (alle übrigen Filter, ohne den Preisfilter) für den Bereichsregler: Säulen zu je
    PREIS_STUFE von 0 bis zum höchsten Preis. Nur einzelne Ausreißer über dem Doppelten des 99. Perzentils werden
    gekappt (landen in der letzten Säule, Anzeige „+“). Säulenhöhe ∝ Wurzel der Anzahl, damit neben einer sehr
    häufigen Preisstufe (z. B. Wallbox) auch seltene sichtbar bleiben."""
    preise = sorted(p for (p,) in db.execute(select(Ladevorgang.kosten / Ladevorgang.kwh)
                                               .where(*bedingungen, Ladevorgang.kwh > 0)) if p is not None)
    if not preise:
        return {"saeulen": [], "max": Decimal(0), "stufe": PREIS_STUFE, "von": preis_min, "bis": preis_max,
                "gekappt": False}
    p99 = preise[min(len(preise) - 1, int(len(preise) * 0.99))]
    gekappt = preise[-1] > 2 * p99
    obergrenze = 2 * p99 if gekappt else preise[-1]
    anzahl = int(obergrenze / PREIS_STUFE) + 1  # letzte Säule enthält die Obergrenze
    zaehler = [0] * anzahl
    for p in preise:
        zaehler[min(anzahl - 1, int(p / PREIS_STUFE))] += 1
    hoechste = max(zaehler)
    return {"saeulen": [(PREIS_STUFE * i, round((n / hoechste) ** 0.5 * 100), n) for i, n in enumerate(zaehler)],
            "max": PREIS_STUFE * anzahl, "stufe": PREIS_STUFE, "von": preis_min, "bis": preis_max,
            "gekappt": gekappt}


def sortierung_lesen(text: str) -> tuple[str, str]:
    """„preis-auf“ → („preis“, „auf“); Unbekanntes → Standard („zeit“, „ab“)."""
    spalte, _, richtung = (text or "").partition("-")
    if spalte not in SORTIERBAR or richtung not in ("auf", "ab"):
        return "zeit", "ab"
    return spalte, richtung


def mehrfach_bedingung(spalte, werte: list[str], ohne: str = "-", zahlen: bool = False):
    """Mehrfachauswahl → SQL-Bedingung (ODER); `ohne` steht für „kein Wert“. None = keine Einschränkung."""
    werte = [w for w in werte if w]
    rest = [w for w in werte if w != ohne]
    if zahlen:
        rest = [int(w) for w in rest if w.isdigit()]
    teile = ([spalte.in_(rest)] if rest else []) + ([spalte.is_(None)] if ohne in werte else [])
    return or_(*teile) if teile else None


def suche_bedingung(text: str, art: str = "ende"):
    """Freie Suche: jedes Wort muss in mindestens einem Feld vorkommen (Teiltreffer, Groß-/Kleinschreibung egal).
    Felder: Kartenkennung (Rechnung und Stammdaten), Kartenbezeichnung, Standort, Ort/Adresse des Ladeorts, EVSE-ID,
    CPO, MSP, Kennzeichen und Modell, Fahrer:in (Person beim Laden), externe ID, AC/DC, Land (Code und Name)."""
    teile = []
    for wort in text.split():
        def enthaelt(spalte, wort=wort):
            return spalte.icontains(wort, autoescape=True)
        laender = [code for code in LAENDER if wort.lower() in landname(code).lower() or wort.upper() == code]
        teile.append(or_(
            enthaelt(Ladevorgang.roh_kartenkennung), enthaelt(Ladevorgang.standort), enthaelt(Ladevorgang.evse_id),
            enthaelt(Ladevorgang.betreiber_id), enthaelt(Ladevorgang.externe_id), enthaelt(Ladevorgang.stromart),
            Ladevorgang.ladekarte.has(or_(enthaelt(Ladekarte.kennung), enthaelt(Ladekarte.bezeichnung))),
            Ladevorgang.ladeort.has(or_(enthaelt(Ladeort.ort), enthaelt(Ladeort.adresse))),
            Ladevorgang.msp.has(enthaelt(Msp.name)),
            Ladevorgang.fahrzeug.has(or_(enthaelt(Fahrzeug.kennzeichen), enthaelt(Fahrzeug.modell))),
            personen_filter(select(Fahrer.id).where(enthaelt(Fahrer.name)).scalar_subquery(), art=art),
            Ladevorgang.land.in_(laender)))
    return teile


def _filter(von: str, bis: str, msp_id: list[str], fahrzeug_id: list[str], suche: str, nur_geschaetzt: str,
            import_id: str, ladekarte_id: str = "", unplausibel: str = "",
            einst: AppEinstellungen | None = None, land: list[str] = (), betreiber: list[str] = (),
            ladeort_id: list[str] = (), ladeart: list[str] = (),
            stromart: list[str] = (), fahrer: list[str] = ()) -> tuple[dict, list, str | None]:
    """(Filterwerte für das Formular, SQL-Bedingungen, Fehlermeldung). MSP, Land, CPO, Fahrzeug, Art und Ladeort
    (aus der Karte): Mehrfachauswahl."""
    art = einst.stichtag if einst is not None else "ende"  # Stichtag für Datumsfilter und Person beim Laden
    msp_id, fahrzeug_id, land, betreiber, ladeort_id = (
        [w for w in x if w] for x in (msp_id, fahrzeug_id, land, betreiber, ladeort_id))
    ladeart = [w for w in ladeart if w in Ladeart.__members__]
    stromart = [w for w in stromart if w in ("AC", "DC", "-")]  # „-“ = keine Angabe
    fahrer = [w for w in fahrer if w.isdigit() or w in ("gaeste", "ohne")]
    filter_werte = {"von": von, "bis": bis, "msp_id": msp_id, "fahrzeug_id": fahrzeug_id, "suche": suche,
                    "nur_geschaetzt": nur_geschaetzt, "import_id": import_id, "ladekarte_id": ladekarte_id,
                    "unplausibel": unplausibel, "land": land, "betreiber": betreiber,
                    "ladeort_id": ladeort_id, "ladeart": ladeart, "stromart": stromart,
                    "fahrer": fahrer}
    bedingungen, fehler = [], None
    try:
        if d := datum_lesen(von):
            bedingungen.append(stichtag_spalte(art) >= d)
        if d := datum_lesen(bis):
            bedingungen.append(stichtag_spalte(art) < d + timedelta(days=1))  # Datum des Stichtags
    except ValueError as e:
        fehler = str(e)
    for b in (mehrfach_bedingung(Ladevorgang.msp_id, msp_id, zahlen=True),
              mehrfach_bedingung(Ladevorgang.betreiber_id, betreiber),
              mehrfach_bedingung(Ladevorgang.land, land),
              mehrfach_bedingung(Ladevorgang.fahrzeug_id, fahrzeug_id, ohne="ohne", zahlen=True),
              mehrfach_bedingung(Ladevorgang.ladeort_id, ladeort_id, zahlen=True),
              mehrfach_bedingung(Ladevorgang.stromart, stromart)):
        if b is not None:
            bedingungen.append(b)
    if ladeart:
        bedingungen.append(Ladevorgang.ladeart.in_([Ladeart(w) for w in ladeart]))
    if (b := personen_filter([int(w) for w in fahrer if w.isdigit()], "gaeste" in fahrer, "ohne" in fahrer,
                             art)) is not None:
        bedingungen.append(b)
    bedingungen += suche_bedingung(suche, art)
    if nur_geschaetzt:
        bedingungen.append(Ladevorgang.kwh_geschaetzt)
    if import_id.isdigit():
        bedingungen.append(Ladevorgang.import_id == int(import_id))
    if ladekarte_id.isdigit():
        bedingungen.append(Ladevorgang.ladekarte_id == int(ladekarte_id))
    if unplausibel and einst is not None:
        bedingungen.append(unplausibel_sql(einst))
    return filter_werte, bedingungen, fehler


def _abfrage(bedingungen, art: str = "ende", sortierung: str = ""):
    spalte, richtung = sortierung_lesen(sortierung)
    ausdruck = {"zeit": stichtag_spalte(art), "dauer": Ladevorgang.ende - Ladevorgang.start, "kwh": Ladevorgang.kwh, "kosten": Ladevorgang.kosten,
                "preis": Ladevorgang.kosten / func.nullif(Ladevorgang.kwh, 0)}[spalte]
    reihenfolge = (ausdruck.desc() if richtung == "ab" else ausdruck.asc()).nulls_last()  # 0 kWh: kein Preis → ans Ende
    return (select(Ladevorgang).where(*bedingungen)
            .options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.fahrzeug),
                     selectinload(Ladevorgang.msp))
            .order_by(reihenfolge, stichtag_spalte(art).desc(), Ladevorgang.id.desc()))


@router.get("")
@zuordnung.vorgeladen_fuer_route
def liste(request: Request, von: str = "", bis: str = "", msp_id: list[str] = Query([]),
          fahrzeug_id: list[str] = Query([]), suche: str = "", nur_geschaetzt: str = "", import_id: str = "",
          ladekarte_id: str = "", unplausibel: str = "", land: list[str] = Query([]),
          betreiber: list[str] = Query([]), ladeort_id: list[str] = Query([]), ladeart: list[str] = Query([]),
          stromart: list[str] = Query([]), fahrer: list[str] = Query([]), bereich: str = "", sortierung: str = "",
          art: str = "", jahr: str = "", nr: str = "", preis_min: str = "", preis_max: str = "",
          db: Session = Depends(get_db)):
    """Zeitraum wie im Dashboard (`art`/`jahr`/`nr`; `von`/`bis` als fester Zeitraum). Ohne jeden Parameter (Aufruf
    aus dem Menü) gilt der vorausgewählte Zeitraum aus „Mein Konto“; mit Filtern (z. B. Links von Detailseiten)
    ohne Zeitraum „Alles“.
    `preis_min`/`preis_max`: Bereich Ø €/kWh je Vorgang (Regler mit Histogramm).
    `sortierung`: „spalte-auf|ab“ (SORTIERBAR), Standard neueste zuerst.
    `bereich`: Kartenausschnitt „süd,west,nord,ost“ – schränkt nur die Liste ein (die Karte schickt ihn beim
    Verschieben und Zoomen); die Filter oben gelten für Karte und Liste."""
    einst = app_einstellungen.laden(db)
    if not request.query_params:  # Aufruf ohne Filter (Menü): vorausgewählter Zeitraum aus „Mein Konto“
        art = art_vorgabe(request)
    z = zeitraum_lesen(db, art, jahr, nr, von, bis)
    datum_von, datum_bis = ("", "") if z.art == "alles" else (z.von.isoformat(), z.bis.isoformat())
    filter_werte, bedingungen, fehler = _filter(datum_von, datum_bis, msp_id, fahrzeug_id, suche, nur_geschaetzt,
                                                import_id, ladekarte_id, unplausibel, einst, land, betreiber,
                                                ladeort_id, ladeart, stromart, fahrer)
    pmin, pmax = preis_lesen(preis_min), preis_lesen(preis_max)
    histogramm = preis_histogramm(db, bedingungen, pmin, pmax)  # ohne den Preisfilter selbst
    if (b := preis_bedingung(pmin, pmax)) is not None:
        bedingungen.append(b)
    del filter_werte["von"], filter_werte["bis"]
    filter_werte.update(zeitraum_werte(z))
    filter_werte.update({"preis_min": str(pmin) if pmin is not None else "",
                         "preis_max": str(pmax) if pmax is not None else ""})
    ohne_ladeorte = urlencode({k: w for k, w in filter_werte.items() if k != "ladeort_id" and w}, doseq=True)
    bedingungen_liste = list(bedingungen)
    if bereich and (b := kartendaten.im_bereich(bereich)) is not None:
        bedingungen_liste.append(b)
    else:
        bereich = ""
    summen = db.execute(select(func.count(), func.coalesce(func.sum(Ladevorgang.kwh), 0),
                               func.coalesce(func.sum(Ladevorgang.kosten), 0))
                        .where(*bedingungen_liste)).one()
    sortierung = "-".join(sortierung_lesen(sortierung)) if sortierung else ""
    vorgaenge = db.scalars(_abfrage(bedingungen_liste, einst.stichtag, sortierung).limit(MAX_ZEILEN)).all()
    kartenanzeige.fuer_vorgaenge(db, vorgaenge, einst.stichtag)  # Karte als Person/Fahrzeug am Ladetag
    sort_spalte, sort_richtung = sortierung_lesen(sortierung)

    def sortier_url(spalte: str) -> str:
        """Adresse für den Spaltenkopf: gleiche Spalte → Richtung umkehren, sonst deren erste Richtung."""
        richtung = ("auf" if sort_richtung == "ab" else "ab") if spalte == sort_spalte else SORTIERBAR[spalte]
        wert = "" if (spalte, richtung) == ("zeit", "ab") else f"{spalte}-{richtung}"
        return "/ladevorgaenge?" + urlencode({k: w for k, w in {**filter_werte, "bereich": bereich,
                                                                "sortierung": wert}.items() if w}, doseq=True)
    anzahl, kwh, kosten = summen
    # Zeitraumzeile wie im Dashboard: Links und Auswahl behalten alle übrigen Filter
    uebrige = {k: w for k, w in {**filter_werte, "sortierung": sortierung}.items()
               if k not in ZEITRAUM_SCHLUESSEL and w}
    # Für „← Ladevorgänge“ im Detail: zurück zur zuletzt angesehenen (gefilterten) Liste
    request.session["ladevorgaenge_liste"] = "/ladevorgaenge" + (f"?{request.url.query}" if request.url.query else "")
    return render(request, "ladevorgaenge.html", {
        "z": z, **zeitraum_navigation(db, z, "/ladevorgaenge", urlencode(uebrige, doseq=True)),
        "zeitraum_versteckt": [(k, x) for k, w in uebrige.items() for x in (w if isinstance(w, list) else [w])],
        "zeitraum_felder": zeitraum_werte(z), "histogramm": histogramm,
        "vorgaenge": vorgaenge, "anzahl": anzahl, "summe_kwh": Decimal(kwh), "summe_kosten": Decimal(kosten),
        "durchschnitt": (Decimal(kosten) / Decimal(kwh)) if kwh else None, "max_zeilen": MAX_ZEILEN,
        "f": filter_werte, "fehler": fehler, "bereich": bereich,
        "filter_karte": db.get(Ladekarte, int(ladekarte_id)) if ladekarte_id.isdigit() else None,
        "filter_import": db.get(Import, int(import_id)) if import_id.isdigit() else None,
        "export_parameter": urlencode({**filter_werte, "bereich": bereich, "sortierung": sortierung}, doseq=True),
        "sortierung": sortierung, "sort_spalte": sort_spalte, "sort_richtung": sort_richtung, "sortier_url": sortier_url,
        "personen": personen_beim_laden(db, vorgaenge),
        "ladungen": (ladungen := kartendaten.ladungen(db, bedingungen)),
        "preis_schnitt": kartendaten.preis_schnitt(ladungen),
        "ohne_ort": kartendaten.ohne_ort_anzahl(db, bedingungen),
        "ohne_ladeorte": ohne_ladeorte,  # aktuelle Filter ohne Ladeort-Auswahl („Filter aufheben“)
        # aktuelle Filter ohne einen davon – für „Filter aufheben“ bei Filtern ohne eigenes Feld
        "ohne_filter": lambda name: urlencode({k: w for k, w in filter_werte.items() if k != name and w}, doseq=True),
        "gruende": {v.id: g for v in vorgaenge if (g := unplausibel_grund(v, einst))},
        "optionen": _optionen(db)})


def _optionen(db: Session) -> dict[str, list[tuple[str, str]]]:
    """Auswahlwerte (Wert, Text) für die Mehrfachauswahl-Filter."""
    betreiber = db.scalars(select(Ladevorgang.betreiber_id).where(Ladevorgang.betreiber_id.is_not(None))
                           .distinct().order_by(Ladevorgang.betreiber_id)).all()
    laender = sorted(db.scalars(select(Ladevorgang.land).where(Ladevorgang.land.is_not(None)).distinct()), key=landname)
    return {
        "msp": [(str(x.id), x.name) for x in db.scalars(select(Msp).order_by(Msp.name))],
        "land": [(x, landname(x)) for x in laender] + [("-", "unbekannt")],
        "betreiber": [(x, x) for x in betreiber] + [("-", "ohne Kennung")],
        "ladeart": [(a.value, a.label) for a in Ladeart],
        "stromart": [("AC", "AC"), ("DC", "DC"), ("-", "keine Angabe")],
        "fahrer": [(str(x.id), x.name) for x in db.scalars(select(Fahrer).order_by(Fahrer.name))]
                  + [("gaeste", "Gäste"), ("ohne", "ohne Person")],
        "fahrzeug": [("ohne", "ohne Fahrzeug")]
                    + [(str(x.id), x.kennzeichen) for x in db.scalars(select(Fahrzeug).order_by(Fahrzeug.kennzeichen))],
    }


@router.get("/export.{format_}")
@zuordnung.vorgeladen_fuer_route
def export(format_: str, von: str = "", bis: str = "", msp_id: list[str] = Query([]),
           fahrzeug_id: list[str] = Query([]), suche: str = "", nur_geschaetzt: str = "", import_id: str = "",
           ladekarte_id: str = "", unplausibel: str = "", land: list[str] = Query([]),
           betreiber: list[str] = Query([]), ladeort_id: list[str] = Query([]), ladeart: list[str] = Query([]),
           stromart: list[str] = Query([]), fahrer: list[str] = Query([]), bereich: str = "", sortierung: str = "",
           art: str = "", jahr: str = "", nr: str = "", preis_min: str = "", preis_max: str = "",
           db: Session = Depends(get_db)):
    einst = app_einstellungen.laden(db)
    z = zeitraum_lesen(db, art, jahr, nr, von, bis)
    datum_von, datum_bis = ("", "") if z.art == "alles" else (z.von.isoformat(), z.bis.isoformat())
    _, bedingungen, _ = _filter(datum_von, datum_bis, msp_id, fahrzeug_id, suche, nur_geschaetzt, import_id,
                                ladekarte_id, unplausibel, einst, land, betreiber, ladeort_id, ladeart, stromart,
                                fahrer)
    if (b := preis_bedingung(preis_lesen(preis_min), preis_lesen(preis_max))) is not None:
        bedingungen.append(b)
    if bereich and (b := kartendaten.im_bereich(bereich)) is not None:  # wie die Liste: nur der Kartenausschnitt
        bedingungen.append(b)
    vorgaenge = db.scalars(_abfrage(bedingungen, einst.stichtag, sortierung)).all()
    personen = personen_beim_laden(db, vorgaenge)
    k = kosten_dienst.kontext(db)
    ust = {v.id: kosten_dienst.fuer_vorgang(k, v) for v in vorgaenge}
    zeilen = [[v.start, v.ende, v.msp.name,
               (v.ladekarte.kennung if v.ladekarte else v.roh_kartenkennung) or "",
               v.ladekarte.bezeichnung or "" if v.ladekarte else "", v.standort or "", v.land or "",
               v.evse_id or "", v.betreiber_id or "", v.kwh,
               "ja" if v.kwh_geschaetzt else "", v.stromart or "", unplausibel_grund(v, einst) or "",
               v.kosten, v.kosten_netto, ust[v.id].ust, ust[v.id].satz, ust[v.id].land or "",
               "ja" if ust[v.id].geschaetzt else "", v.ladeart.label,
               v.fahrzeug.kennzeichen if v.fahrzeug else "", person_text(personen[v.id]),
               v.zuordnung_quelle.value, v.import_id]
              for v in vorgaenge]
    t = Tabelle("ladevorgaenge", [
        Spalte("Start", "datumzeit"), Spalte("Ende", "datumzeit"), Spalte("MSP"), Spalte("Karte"),
        Spalte("Kartenbezeichnung"), Spalte("Standort"), Spalte("Land"), Spalte("EVSE-ID"), Spalte("CPO"), Spalte("kWh", "kwh"), Spalte("kWh geschätzt"),
        Spalte("AC/DC"), Spalte("Unplausibel"),
        Spalte("Kosten €", "geld"), Spalte("Netto €", "geld"), Spalte("USt. €", "geld"), Spalte("USt.-Satz %", "preis"),
        Spalte("USt.-Land"), Spalte("USt. geschätzt"), Spalte("Ladeart"), Spalte("Fahrzeug"), Spalte("Person"), Spalte("Zuordnung"),
        Spalte("Import", "ganz")], zeilen)
    return datei_antwort(t, format_)


@router.get("/{vorgang_id}")
def detail(request: Request, vorgang_id: int, db: Session = Depends(get_db)):
    v = db.get(Ladevorgang, vorgang_id)
    if v is None:
        raise HTTPException(404, "Ladevorgang nicht gefunden")
    return _detail(request, db, v)


def _detail(request: Request, db: Session, v: Ladevorgang, status_code: int = 200, ladeort_werte: dict | None = None,
            ladeort_fehler: str | None = None):
    """`ladeort_werte`/`ladeort_fehler`: Formular PLZ/Ort nach einem Fehler – mit den Eingaben."""
    kartenanzeige.fuer_vorgaenge(db, [v], stichtag_art(db))
    fahrzeuge = db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv | (Fahrzeug.id == v.fahrzeug_id))
                           .order_by(Fahrzeug.kennzeichen)).all()
    return render(request, "ladevorgang_detail.html", {
        "v": v, "fahrzeuge": fahrzeuge, "leistung": leistung(v), "personen": personen_beim_laden(db, [v])[v.id],
        "laender": sorted(LAENDER.items(), key=lambda e: e[1]),
        "grund": unplausibel_grund(v, app_einstellungen.laden(db)),
        "cpo_eintrag": stromart_dienst.cpo_eintrag(stromart_dienst.cpo_regeln(db), v.betreiber_id)
        if v.stromart_quelle == "cpo" and v.betreiber_id else None,
        "kosten_info": kosten_dienst.fuer_vorgang(kosten_dienst.kontext(db), v, db),
        "firmenland": app_einstellungen.laden(db).firmenland,
        "rohdaten": json.dumps(v.rohdaten, ensure_ascii=False, indent=2),
        "verlauf": protokoll.verlauf(request, db, "ladevorgang", v.id),
        "liste_url": request.session.get("ladevorgaenge_liste", "/ladevorgaenge"),
        "von_aufgaben": "/aufgaben" in request.headers.get("referer", ""),
        "ladeort_werte": ladeort_werte, "ladeort_fehler": ladeort_fehler}, status_code)


@router.post("/{vorgang_id}")
def fahrzeug_aendern(request: Request, vorgang_id: int, fahrzeug_id: str = Form(""), db: Session = Depends(get_db)):
    """fahrzeug_id: ID (manuell setzen) oder „auto“ (automatisch ermitteln)."""
    v = db.get(Ladevorgang, vorgang_id)
    if v is None:
        raise HTTPException(404, "Ladevorgang nicht gefunden")
    if fahrzeug_id == "auto":
        v.zuordnung_quelle = ZuordnungQuelle.automatisch
        vorgang_aktualisieren(db, v)
        text = (f"Fahrzeug automatisch ermittelt: {v.fahrzeug.kennzeichen}." if v.fahrzeug
                else "Automatisch kein Fahrzeug ermittelt.")
    elif fahrzeug_id.isdigit() and (fz := db.get(Fahrzeug, int(fahrzeug_id))):
        fahrzeug_setzen(v, fz, ZuordnungQuelle.manuell)
        text = f"Ladevorgang manuell Fahrzeug {fz.kennzeichen} zugeordnet."
    else:
        meldung(request, "Bitte eine Auswahl treffen.", "fehler")
        return weiter(f"/ladevorgaenge/{v.id}")
    db.commit()
    meldung(request, text)
    return weiter(f"/ladevorgaenge/{v.id}")


@router.post("/{vorgang_id}/ladeort")
def ladeort_plz(request: Request, vorgang_id: int, plz: str = Form(""), ort: str = Form(""),
                db: Session = Depends(get_db)):
    """PLZ des Ladeorts manuell setzen (gilt für alle Vorgänge an dieser Adresse); leer = erkannte PLZ."""
    v = db.get(Ladevorgang, vorgang_id)
    if v is None or v.ladeort is None:
        raise HTTPException(404, "Ladeort nicht gefunden")
    if fehler := ladeort_plz_speichern(request, db, v.ladeort, plz, ort):
        return _detail(request, db, v, 422, {"plz": plz, "ort": ort}, fehler)  # Eingaben bleiben stehen
    return weiter(f"/ladevorgaenge/{v.id}")


def ladeort_plz_speichern(request: Request, db: Session, ort, plz: str, ortsname: str = "") -> str | None:
    """PLZ und/oder Ortsnamen prüfen, setzen, neu verorten und das Ergebnis melden (auch von „Aufgaben“ aus).
    Ungültige Eingabe: nichts geändert, Rückgabe ist der Fehlertext (der Aufrufer zeigt ihn an)."""
    neu = " ".join(plz.split()).upper() or None
    name = " ".join(ortsname.split())[:120] or None
    muster = PLZ_MUSTER.get(ort.land or "")
    if neu and muster and not re.fullmatch(muster, neu):
        return f"„{neu}“ ist keine gültige PLZ für {landname(ort.land)}."
    plz_setzen(db, ort, neu, name)
    db.commit()
    angaben = [(f"PLZ {ort.plz_wirksam}" + (" (manuell)" if ort.plz_manuell else " (aus der Adresse)"))
               if ort.plz_wirksam else "keine PLZ"] + ([f"Ort „{ort.ort_manuell}“ (manuell)"] if ort.ort_manuell else [])
    meldung(request, f"„{ort.adresse}“: {', '.join(angaben)}. {verortung_text(ort)}",
            "info" if ort.genauigkeit in ("plz", "ort", "manuell") else "warnung")


@router.post("/{vorgang_id}/stromart")
def stromart_setzen(request: Request, vorgang_id: int, stromart: str = Form(""), db: Session = Depends(get_db)):
    """AC/DC manuell korrigieren; die Angabe der Rechnung bleibt in den Rohdaten."""
    v = db.get(Ladevorgang, vorgang_id)
    if v is None:
        raise HTTPException(404, "Ladevorgang nicht gefunden")
    stromart_dienst.manuell_setzen(v, stromart)
    neu = v.stromart
    db.commit()
    grund = unplausibel_grund(v, app_einstellungen.laden(db))
    meldung(request, f"Stromart auf {neu or 'keine Angabe'} gesetzt."
            + (f" Weiterhin unplausibel: {grund}." if grund else ""), "warnung" if grund else "info")
    return weiter(f"/ladevorgaenge/{v.id}")


@router.post("/{vorgang_id}/land")
def land_setzen(request: Request, vorgang_id: int, land: str = Form(""), db: Session = Depends(get_db)):
    """Land manuell korrigieren (z. B. ausländische EVSE-ID an einem Ladepunkt in Österreich). Das ermittelte Land
    bleibt in den Rohdaten; der Ladeort wird mit dem neuen Land neu zugeordnet und verortet (Karte)."""
    v = db.get(Ladevorgang, vorgang_id)
    if v is None:
        raise HTTPException(404, "Ladevorgang nicht gefunden")
    neu = land if land in LAENDER else None
    if "land_ermittelt" not in (v.rohdaten or {}):
        v.rohdaten = {**(v.rohdaten or {}), "land_ermittelt": v.land}
    v.land = neu
    v.ladeort = ladeort_fuer(db, v.standort, neu)
    if v.ladeort is not None and v.ladeort.genauigkeit is None:
        geokodieren(db, v.ladeort)
    db.commit()
    meldung(request, f"Land auf {landname(neu) if neu else 'unbekannt'} gesetzt."
            + (f" Ladeort: {verortung_text(v.ladeort)}" if v.ladeort else ""))
    return weiter(f"/ladevorgaenge/{v.id}")


@router.post("/{vorgang_id}/profil")
def profil_ausnehmen(request: Request, vorgang_id: int, ausnehmen: str = Form(""), zurueck: str = Form(""),
                     db: Session = Depends(get_db)):
    """Aus dem Ladeprofil ausnehmen bzw. wieder aufnehmen; `zurueck`: Rücksprung (z. B. aus den Aufgaben)."""
    v = db.get(Ladevorgang, vorgang_id)
    if v is None:
        raise HTTPException(404, "Ladevorgang nicht gefunden")
    v.profil_ausgenommen = ausnehmen == "1"
    db.commit()
    meldung(request, "Vorgang aus dem Ladeprofil ausgenommen." if v.profil_ausgenommen
            else "Vorgang zählt wieder für das Ladeprofil (sofern plausibel).")
    return weiter(zurueck if zurueck.startswith("/") and not zurueck.startswith("//") else f"/ladevorgaenge/{v.id}")
