"""Auswertungen pro Fahrzeug, MSP, gesamt; Topliste; Export als CSV/XLSX."""
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import app_einstellungen, diagramme, kennzahlen
from app.services.app_einstellungen import STICHTAGE
from app.config import heute
from app.models import Fahrer, Fahrzeug
from app.services.auswertung import (ARTEN, SPAR_MINDEST_KWH, TEILE, Auswertung, CpoDetail, FahrerZeile, Zeitraum, FahrzeugZeile, art_wechseln, auswerten,
                                     datenjahre, vormonat, zeitraum_aus_parametern)
from app.services.zuordnung import fahrer_von_fahrzeug_am
from app.services.evse import cpo_land, landname
from app.services.export import MEDIENTYPEN, Spalte, Tabelle, inhalt
from app.web import art_vorgabe, render

router = APIRouter(prefix="/auswertungen")


def _auswertung(db: Session, art: str, jahr: str, nr: str, von: str, bis: str) -> Auswertung:
    z = zeitraum_aus_parametern(art, jahr, nr, von, bis, standard=vormonat(), db=db)
    return auswerten(db, z, app_einstellungen.laden(db))


def zeitraum_navigation(db: Session, z: Zeitraum, basis: str, extra: str = "") -> dict:
    """Links für Zeitraum zurück/vor, Umschalten der Art, Auswahl Teilzeitraum/Jahr und die aktuellen Parameter.
    `extra`: weitere Parameter (urlencodiert), die alle Links behalten – z. B. die Filter der Ladevorgänge."""
    anhang = f"&{extra}" if extra else ""

    def link(zr: Zeitraum) -> str:
        return f"{basis}?{urlencode(zr.als_parameter())}{anhang}"

    arten_links = {art: link(art_wechseln(z, art, vormonat())) for art in ARTEN if art not in ("frei", "alles")}
    arten_links["alles"] = f"{basis}?art=alles{anhang}"
    return {"zurueck": link(z.verschoben(-1)), "vor": link(z.verschoben(1)),
            "parameter": urlencode(z.als_parameter()), "arten": ARTEN, "arten_links": arten_links,
            "teile": TEILE.get(z.art), "jahre": datenjahre(db, z), "basis": basis}


def navigation(db: Session, a: Auswertung, basis: str) -> dict:
    return {**zeitraum_navigation(db, a.zeitraum, basis), "stichtag": STICHTAGE[a.stichtag]}


@router.get("")
def uebersicht(request: Request, art: str = Depends(art_vorgabe), jahr: str = "", nr: str = "", von: str = "", bis: str = "",
               karten: str = "", ladeorte: str = "", db: Session = Depends(get_db)):
    """`karten=alle`: Ladekarten-Nutzung als vollständige Rangliste statt Top 10; `ladeorte=alle` ebenso."""
    a = _auswertung(db, art, jahr, nr, von, bis)
    return render(request, "auswertungen/uebersicht.html", {"a": a, "karten_alle": karten == "alle",
                                                           "dc": kennzahlen.ladedauer_dc(db, a.zeitraum, a.stichtag),
                                                           "dc_oeffentlich": diagramme.dc_oeffentlich(
                                                               db, a.zeitraum, a.stichtag),
                                                           "ladeorte_alle": ladeorte == "alle",
                                                           "karten_top": 10, **navigation(db, a, "/auswertungen")})


@router.get("/fahrzeug/{fahrzeug_id}")
def fahrzeug(request: Request, fahrzeug_id: int, art: str = Depends(art_vorgabe), jahr: str = "", nr: str = "", von: str = "",
             bis: str = "", db: Session = Depends(get_db)):
    a = _auswertung(db, art, jahr, nr, von, bis)
    zeile = a.fahrzeug(fahrzeug_id)
    if zeile is None:  # keine Daten im Zeitraum: leere Seite (blätterbar) statt Fehler
        fz = db.get(Fahrzeug, fahrzeug_id)
        if fz is None:
            raise HTTPException(404, "Fahrzeug nicht gefunden")
        zeile = FahrzeugZeile(fz, fahrer_aktuell=[f.name for f in fahrer_von_fahrzeug_am(db, fz.id, heute())])
    return render(request, "auswertungen/fahrzeug.html", {
        "a": a, "z": zeile, "dc": kennzahlen.ladedauer_dc(db, a.zeitraum, a.stichtag, fahrzeug_id=zeile.fahrzeug.id), **navigation(db, a, f"/auswertungen/fahrzeug/{fahrzeug_id}"),
        "msp": sorted(zeile.msp.items(), key=lambda e: -e[1].kosten),
        "standorte": sorted(zeile.standorte.items(), key=lambda e: -e[1].kwh)})


@router.get("/fahrer/{fahrer_id}")
def fahrer(request: Request, fahrer_id: int, art: str = Depends(art_vorgabe), jahr: str = "", nr: str = "", von: str = "",
           bis: str = "", db: Session = Depends(get_db)):
    a = _auswertung(db, art, jahr, nr, von, bis)
    zeile = a.fahrer_zeile(fahrer_id)
    if zeile is None:  # keine Ladevorgänge im Zeitraum: leere Seite (blätterbar) statt Fehler
        person = db.get(Fahrer, fahrer_id)
        if person is None:
            raise HTTPException(404, "Fahrer:in nicht gefunden")
        zeile = FahrerZeile(person)
    return render(request, "auswertungen/fahrer.html", {
        "a": a, "z": zeile, "dc": kennzahlen.ladedauer_dc(db, a.zeitraum, a.stichtag, fahrer_id=zeile.fahrer.id), **navigation(db, a, f"/auswertungen/fahrer/{fahrer_id}"),
        "fahrzeuge": sorted(zeile.fahrzeuge.items(), key=lambda e: -e[1].kwh),
        "msp": sorted(zeile.msp.items(), key=lambda e: -e[1].kosten),
        "standorte": sorted(zeile.standorte.items(), key=lambda e: -e[1].kwh)})


def cpo_schluessel(code: str) -> str | None:
    """CPO aus der URL: „-“ steht für „unbekannt“ (Ladungen ohne CPO)."""
    return None if code == "-" else code


@router.get("/cpo/{code:path}")
def cpo(request: Request, code: str, art: str = Depends(art_vorgabe), jahr: str = "", nr: str = "", von: str = "", bis: str = "",
        db: Session = Depends(get_db)):
    """Öffentliche Ladungen bei einem CPO: Fahrer:innen nach Kosten mit ihren Ladekarten, dazu MSP im Vergleich."""
    a = _auswertung(db, art, jahr, nr, von, bis)
    schluessel = cpo_schluessel(code)
    detail = a.cpo_personen.get(schluessel) or CpoDetail(schluessel)  # leer: blätterbar statt Fehler
    return render(request, "auswertungen/cpo.html", {
        "a": a, "d": detail, "code": code, "mindest": SPAR_MINDEST_KWH, **navigation(db, a, f"/auswertungen/cpo/{quote(code, safe='*')}")})


# ------------------------------------------------------------------ Export

def _summen_spalten() -> list[Spalte]:
    return [Spalte("Vorgänge", "ganz"), Spalte("kWh", "kwh"), Spalte("davon geschätzt kWh", "kwh"),
            Spalte("Kosten €", "geld"), Spalte("davon Gebühren €", "geld"), Spalte("Ø €/kWh", "preis"),
            Spalte("kWh öffentlich", "kwh"), Spalte("Kosten öffentlich €", "geld"),
            Spalte("kWh privat", "kwh"), Spalte("Kosten privat €", "geld")]


def _summen_werte(s) -> list:
    return [s.anzahl, s.kwh, s.kwh_geschaetzt, s.kosten, s.gebuehren, s.preis,
            s.kwh_oeffentlich, s.kosten_oeffentlich, s.kwh_privat, s.kosten_privat]


def tabelle(a: Auswertung, name: str, cpo: str = "") -> Tabelle:
    suffix = a.zeitraum.label.replace(" ", "_").replace("/", "-").replace(".", "-").replace("–", "bis")
    if name == "fahrzeuge":
        return Tabelle(f"fahrzeuge_{suffix}",
                       [Spalte("Kennzeichen"), Spalte("Modell"), Spalte("Fahrer:innen im Zeitraum")] + _summen_spalten(),
                       [[z.fahrzeug.kennzeichen, z.fahrzeug.modell or "", ", ".join(z.fahrer_im_zeitraum)]
                        + _summen_werte(z.summe) for z in a.fahrzeuge])
    if name == "fahrer":
        return Tabelle(f"fahrer_{suffix}", [Spalte("Fahrer:in"), Spalte("Fahrzeuge")] + _summen_spalten(),
                       [[z.fahrer.name, ", ".join(sorted(z.fahrzeuge))] + _summen_werte(z.summe) for z in a.fahrer])
    if name == "ladekarten":
        return Tabelle(f"ladekarten_{suffix}",
                       [Spalte("Kennung"), Spalte("Bezeichnung"), Spalte("MSP"), Spalte("Zugeordnet an"),
                        Spalte("aktiv")] + _summen_spalten(),
                       [[k.karte.kennung, k.karte.bezeichnung or "", k.karte.msp.name, k.zugeordnet,
                         "ja" if k.karte.aktiv else "nein"] + _summen_werte(k.summe) for k in a.karten])
    if name == "msp":
        return Tabelle(f"msp_{suffix}", [Spalte("MSP")] + _summen_spalten(),
                       [[n] + _summen_werte(s) for n, s in a.msp])
    if name == "cpo":
        return Tabelle(f"cpo_{suffix}", [Spalte("CPO"), Spalte("Land")] + _summen_spalten(),
                       [[c or "unbekannt", landname(cpo_land(c)) if cpo_land(c) else ""] + _summen_werte(s) for c, s in a.cpo])
    if name == "cpo_personen":
        d = a.cpo_personen.get(cpo_schluessel(cpo)) or CpoDetail(cpo_schluessel(cpo))
        teil = (cpo or "unbekannt").replace("*", "-").replace("/", "-")
        return Tabelle(f"cpo_{teil}_personen_{suffix}",
                       [Spalte("Fahrer:in"), Spalte("Ladekarte"), Spalte("Bezeichnung"), Spalte("MSP"),
                        Spalte("Vorgänge", "ganz"), Spalte("kWh", "kwh"), Spalte("Kosten €", "geld"),
                        Spalte("Ø €/kWh", "preis"), Spalte("Sparpotenzial €", "geld")],
                       [[p.name, k.kennung if k else "ohne Karte", (k.bezeichnung or "") if k else "",
                         k.msp.name if k else "", s.anzahl, s.kwh, s.kosten, s.preis, p.ersparnis_karte(k)]
                        for p in d.personen for k, s in p.karten_liste])
    if name == "ladeorte":
        return Tabelle(f"ladeorte_{suffix}",
                       [Spalte("Rang", "ganz"), Spalte("Adresse"), Spalte("PLZ"), Spalte("Ort"), Spalte("Land")]
                       + _summen_spalten(),
                       [[rang, o.adresse, o.plz_wirksam or "", o.ort or "", landname(o.land) if o.land else ""]
                        + _summen_werte(s) for rang, (o, s) in enumerate(a.ladeorte, start=1)])
    if name == "land":
        return Tabelle(f"laender_{suffix}", [Spalte("Land"), Spalte("ISO")] + _summen_spalten(),
                       [[landname(c), c or ""] + _summen_werte(s) for c, s in a.laender])
    if name == "gesamt":
        zeilen = [("Fahrzeuge", a.fahrzeuge_summe), ("Nicht zugeordnet", a.nicht_zugeordnet), ("Gäste", a.gaeste),
                  ("Gebühren nicht zuordenbar", a.gebuehren_nicht_zuordenbar), ("Pauschalen", a.pauschalen),
                  ("Gesamt", a.gesamt)]
        return Tabelle(f"gesamt_{suffix}", [Spalte("Bereich")] + _summen_spalten(),
                       [[n] + _summen_werte(s) for n, s in zeilen])
    if name in ("topliste", "topliste_oeffentlich"):
        t = a.topliste if name == "topliste" else a.topliste_oeffentlich
        zeilen = [[liste, rang, e.fahrer.name, e.kwh, e.kosten, e.preis, e.score, e.anteil_geschaetzt]
                  for liste, eintraege in (("günstigste", t.guenstigste), ("teuerste", t.teuerste))
                  for rang, e in enumerate(eintraege, start=1)]
        return Tabelle(f"{name}_{suffix}",
                       [Spalte("Liste"), Spalte("Rang", "ganz"), Spalte("Fahrer:in"), Spalte("kWh", "kwh"),
                        Spalte("Kosten €", "geld"), Spalte("Ø €/kWh", "preis"), Spalte("Score €/kWh", "preis"),
                        Spalte("Anteil geschätzt", "prozent")], zeilen)
    raise HTTPException(404, "Unbekannte Tabelle")


def datei_antwort(t: Tabelle, format_: str) -> Response:
    if format_ not in MEDIENTYPEN:
        raise HTTPException(404, "Unbekanntes Format")
    return Response(inhalt(t, format_), media_type=MEDIENTYPEN[format_],
                    headers={"Content-Disposition": f'attachment; filename="{t.name}.{format_}"'})


@router.get("/export/{name}.{format_}")
def export(name: str, format_: str, art: str = Depends(art_vorgabe), jahr: str = "", nr: str = "", von: str = "",
           bis: str = "", cpo: str = "", db: Session = Depends(get_db)):
    """`cpo`: für „cpo_personen“ der CPO („-“ = unbekannt)."""
    return datei_antwort(tabelle(_auswertung(db, art, jahr, nr, von, bis), name, cpo), format_)
