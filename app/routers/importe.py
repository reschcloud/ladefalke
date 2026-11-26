"""Import von Laderechnungen: Hochladen, Vorschau, Bestätigen, Löschen.

Ablauf in drei Schritten (Schrittanzeige auf Import- und Vorschauseite): ① Dateien hochladen → ② je Datei prüfen und
bestätigen („Datei x von y“) → ③ abgeschlossen. Ein Durchgang (Sitzung, `DURCHGANG`) merkt sich die bestätigten bzw.
verworfenen Vorschauen seit dem letzten Hochladen; nach der letzten offenen Vorschau geht es zurück auf die
Import-Seite mit einem Abschlusskasten (Summen, Ladevorgänge zum späteren Zuordnen, Links)."""
import mimetypes
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.db import get_db
from app.formatierung import anzahl as anzahl_text, decimal_lesen
from app.models import Gebuehr, Import, ImportStatus, Ladeart, Ladekarte, Ladevorgang, Msp, ZuordnungKarte
from app.parsers import PARSER
from app.services import parser_verwaltung
from app.routers import pauschalen
from app.services.importe import (BereitsImportiert, ImportFehler, KartenEntscheidung, bestaetigen, datei, hochladen,
                                  loeschen, naechste_vorschau, offene_vorschauen, vorschau_berechnen, wieder_pruefen,
                                  zurueckstellen)
from app.services import kartenanzeige, stromart
from app.services.offen import ohne_fahrzeug_bedingungen
from app.web import meldung, render, weiter

router = APIRouter(prefix="/import")

DURCHGANG = "import_durchgang"
BEKANNT = "import_bekannt"  # beim letzten Hochladen übersprungene, schon importierte Dateien: [Import-ID]
# Die Sitzung liegt im Cookie (Browser verwerfen Cookies über ~4 KB stillschweigend): bei Hunderten Dateien nur IDs,
# Anzahlen und wenige Namen darin ablegen
NAMEN_MAX = 10  # so viele Namen verworfener Dateien merkt sich ein Durchgang
EINZELN_MAX = 3  # bis zu so vielen Dateien je Ergebnisart eine Meldung pro Datei, darüber eine Sammelmeldung


def _durchgang(request: Request) -> dict:
    """Aktueller Durchgang: erledigte Import-IDs, Namen verworfener Dateien, ob alles abgearbeitet ist."""
    d = request.session.get(DURCHGANG)
    return {"verworfen_n": len(d.get("verworfen", [])), "zurueckgestellt_n": 0, **d} if isinstance(d, dict) else _leer()


def _leer() -> dict:
    """`verworfen`: höchstens NAMEN_MAX Namen, `verworfen_n`: Anzahl aller verworfenen Dateien."""
    return {"erledigt": [], "verworfen": [], "verworfen_n": 0, "zurueckgestellt_n": 0, "fertig": False}


def _durchgang_neu(request: Request) -> None:
    request.session[DURCHGANG] = _leer()


def _verworfen(d: dict, namen: list[str]) -> dict:
    return {**d, "verworfen": [*d["verworfen"], *namen][:NAMEN_MAX], "verworfen_n": d["verworfen_n"] + len(namen)}


def _vorschau_erledigt(request: Request, db: Session, bezug: int, import_id: int | None = None,
                       verworfen: str | None = None, zurueckgestellt: bool = False):
    """Vorschau bestätigt (`import_id`), verworfen (`verworfen` = Dateiname) oder zurückgestellt: weiter zur nächsten offenen Vorschau
    (nach `bezug`, der ID der erledigten Vorschau) oder – war es die letzte – zurück auf die Import-Seite mit
    Abschlusskasten."""
    d = _durchgang(request)
    if d["fertig"]:  # nach einem abgeschlossenen Durchgang beginnt mit der nächsten Vorschau ein neuer
        d = _leer()
    if import_id is not None:
        d["erledigt"] = [*d["erledigt"], import_id]
    if verworfen is not None:
        d = _verworfen(d, [verworfen])
    if zurueckgestellt:
        d["zurueckgestellt_n"] += 1
    naechste = naechste_vorschau(db, bezug)
    d["fertig"] = naechste is None
    request.session[DURCHGANG] = d
    if naechste is not None:
        meldung(request, "Weiter mit der nächsten Datei.")
        return weiter(f"/import/{naechste}")
    return weiter("/import#abschluss")


def _abschluss(request: Request, db: Session) -> dict | None:
    """Daten für den Abschlusskasten nach dem letzten Durchgang (oder None)."""
    d = _durchgang(request)
    if not d["fertig"] or not (d["erledigt"] or d["verworfen_n"] or d["zurueckgestellt_n"]):
        return None
    importe = db.scalars(select(Import).where(Import.id.in_(d["erledigt"]),
                                              Import.status == ImportStatus.abgeschlossen)
                         .order_by(Import.id)).all()
    ids = [i.id for i in importe]
    anzahl, kwh, kosten = db.execute(
        select(func.count(), func.coalesce(func.sum(Ladevorgang.kwh), 0),
               func.coalesce(func.sum(Ladevorgang.kosten_netto), 0))
        .where(Ladevorgang.import_id.in_(ids))).one() if ids else (0, 0, 0)
    gebuehren = db.scalar(select(func.coalesce(func.sum(Gebuehr.betrag_netto), 0))
                          .where(Gebuehr.import_id.in_(ids))) if ids else 0
    spaeter = db.scalar(select(func.count()).select_from(Ladevorgang)
                        .where(Ladevorgang.import_id.in_(ids), *ohne_fahrzeug_bedingungen())) if ids else 0
    # Karten aus diesen Importen ohne jede Zuordnung (meist gerade neu angelegt): direkt zum Zuordnen verlinken
    ohne_zuordnung = db.execute(
        select(Ladekarte, func.min(Ladevorgang.start)).join(Ladevorgang, Ladevorgang.ladekarte_id == Ladekarte.id)
        .where(Ladevorgang.import_id.in_(ids), Ladekarte.ist_gastkarte.is_(False),
               ~select(ZuordnungKarte.id).where(ZuordnungKarte.ladekarte_id == Ladekarte.id).exists())
        .group_by(Ladekarte.id).order_by(Ladekarte.kennung)).all() if ids else []
    return {"importe": importe, "verworfen": d["verworfen"], "verworfen_n": d["verworfen_n"],
            "zurueckgestellt_n": d["zurueckgestellt_n"], "anzahl": anzahl,
            "kwh": Decimal(kwh), "kosten": Decimal(kosten), "gebuehren": Decimal(gebuehren), "spaeter_zuordnen": spaeter,
            "karten_ohne_zuordnung": ohne_zuordnung}


def _bekannt(request: Request, db: Session) -> list[Import]:
    """Beim letzten Hochladen übersprungene Dateien (bereits importiert) mit dem bestehenden Import – nur einmal."""
    ids = request.session.pop(BEKANNT, None)
    if not isinstance(ids, list) or not ids:
        return []
    return db.scalars(select(Import).where(Import.id.in_(ids)).order_by(Import.dateiname)).all()


def _laden(db: Session, import_id: int) -> Import:
    imp = db.get(Import, import_id)
    if imp is None:
        raise HTTPException(404, "Import nicht gefunden")
    return imp


def _liste(request: Request, db: Session, fehler: str | None = None, status_code: int = 200,
           pauschalen_jahr: str = ""):
    summen = (select(Ladevorgang.import_id, func.count().label("anzahl"), func.sum(Ladevorgang.kwh).label("kwh"),
                     func.sum(Ladevorgang.kosten_netto).label("kosten"))
              .group_by(Ladevorgang.import_id).subquery())
    gebuehren = (select(Gebuehr.import_id, func.sum(Gebuehr.betrag_netto).label("gebuehren"))
                 .group_by(Gebuehr.import_id).subquery())
    zeilen = db.execute(
        select(Import, summen.c.anzahl, summen.c.kwh, summen.c.kosten, gebuehren.c.gebuehren)
        .outerjoin(summen, summen.c.import_id == Import.id)
        .outerjoin(gebuehren, gebuehren.c.import_id == Import.id)
        .order_by(Import.id.desc())).all()
    vorschauen = [imp for imp, *_ in zeilen if imp.status == ImportStatus.vorschau]
    vorschauen.sort(key=lambda imp: imp.id)
    zurueckgestellt = sorted((imp for imp, *_ in zeilen if imp.status == ImportStatus.zurueckgestellt),
                             key=lambda imp: imp.zurueckgestellt_am or imp.hochgeladen_am)
    abschluss = None if vorschauen else _abschluss(request, db)
    if abschluss:  # nur einmal zeigen – beim Neuladen ist er weg
        request.session.pop(DURCHGANG, None)
    schritt = 2 if vorschauen else 3 if abschluss else 1
    return render(request, "importe/liste.html", {
        "zeilen": zeilen, "fehler": fehler, "vorschauen": vorschauen, "zurueckgestellt": zurueckgestellt, "abschluss": abschluss, "schritt": schritt,
        "bekannt": _bekannt(request, db),
        "formate": parser_verwaltung.formate(db),  # aus den installierten Parsern – die Oberfläche kennt keine Anbieter
        "neu_importiert": {i.id for i in abschluss["importe"]} if abschluss else set(),
        **pauschalen.uebersicht(db, pauschalen_jahr)}, status_code)


@router.get("")
def liste(request: Request, pauschalen_jahr: str = "", abschluss: str = "", db: Session = Depends(get_db)):
    """`pauschalen_jahr`: Jahr im Abschnitt „Pauschalen“. `abschluss=aus`: Abschlusskasten ausblenden."""
    if abschluss == "aus":
        request.session.pop(DURCHGANG, None)
        return RedirectResponse("/import", status_code=303)
    return _liste(request, db, pauschalen_jahr=pauschalen_jahr)


@router.post("")
async def hochladen_route(request: Request, db: Session = Depends(get_db)):
    """Eine oder mehrere Dateien hochladen; je Datei entsteht eine Vorschau, die einzeln bearbeitet wird."""
    formular = await request.form()
    # leeres Dateifeld kommt als Text an – nur echte Dateien übernehmen
    dateien = [(d.filename, await d.read()) for d in formular.getlist("datei_upload")
               if isinstance(d, StarletteUploadFile) and d.filename]
    if not dateien:
        return _liste(request, db, "Bitte mindestens eine Datei auswählen.", 422)
    _durchgang_neu(request)
    request.session.pop(BEKANNT, None)
    schon_offen = set(offene_vorschauen(db))
    ergebnisse = []  # (Dateiname, Import oder None, Fehlermeldung oder None)
    bekannt = []  # IDs bereits importierter Dateien: übersprungen, auf der Import-Seite gesammelt angezeigt
    for name, inhalt in dateien:
        try:
            imp = hochladen(db, name, inhalt)
            db.commit()
            ergebnisse.append((name, imp, None))
        except BereitsImportiert as e:
            if e.import_.id not in bekannt:
                bekannt.append(e.import_.id)  # ID vor dem Rollback lesen
            db.rollback()
        except ImportFehler as e:
            db.rollback()
            ergebnisse.append((name, None, str(e)))

    if bekannt:
        request.session[BEKANNT] = bekannt
    if len(ergebnisse) == 1 and len(dateien) == 1:  # wie bisher: direkt zur Vorschau bzw. Fehler auf der Import-Seite
        _, imp, fehler = ergebnisse[0]
        return _liste(request, db, fehler, 422) if fehler else weiter(f"/import/{imp.id}")

    # je Ergebnisart: (Art der Meldung, Text je Datei, Sammeltext mit {n}) → Einzeltexte
    arten = {"fehler": ("fehler", "{n} Dateien konnten nicht importiert werden ({namen} …)."),
             "offen": ("warnung", "{n} Dateien lagen bereits als offene Vorschau vor."),
             "format": ("warnung", "{n} Vorschauen angelegt, deren Format nicht erkannt oder nicht eindeutig ist "
                                   "– bitte in der Vorschau das Format wählen."),
             "neu": ("info", "{n} Vorschauen angelegt.")}
    gruppen = {k: [] for k in arten}  # Art → [(Dateiname, Einzeltext)]
    gesehen = set()
    for name, imp, fehler in ergebnisse:
        if fehler:
            gruppen["fehler"].append((name, f"{name}: {fehler}"))
        elif imp.id in schon_offen or imp.id in gesehen:
            gruppen["offen"].append((name, f"{name}: liegt bereits als offene Vorschau vor."))
        elif imp.parser_key is None:
            passend = parser_verwaltung.passende(db, datei(imp))
            grund = (f"Format nicht eindeutig (passt zu {', '.join(PARSER[k].name for k in passend)})"
                     if len(passend) > 1 else "Format nicht erkannt")
            gruppen["format"].append((name, f"{name}: Vorschau angelegt – {grund}, bitte Format wählen."))
        else:
            gruppen["neu"].append((name, f"{name}: Vorschau angelegt ({PARSER[imp.parser_key].name})."))
        if imp:
            gesehen.add(imp.id)
    for k, (art, sammel) in arten.items():
        eintraege = gruppen[k]
        if len(eintraege) <= EINZELN_MAX:
            for _, text in eintraege:
                meldung(request, text, art)
        else:
            namen = ", ".join(n[:40] for n, _ in eintraege[:EINZELN_MAX])
            meldung(request, sammel.format(n=len(eintraege), namen=namen), art)
    return weiter("/import")


def _vorschau_seite(request: Request, db: Session, imp: Import, fehler: str | None = None, status_code: int = 200):
    offen = offene_vorschauen(db)
    pos = offen.index(imp.id) if imp.id in offen else 0
    d = _durchgang(request)
    schon = 0 if d["fertig"] else len(d["erledigt"]) + d["verworfen_n"] + d["zurueckgestellt_n"]  # bereits erledigt
    navigation = {"nr": schon + pos + 1, "anzahl": schon + len(offen), "offen": len(offen),
                  "zurueck": offen[pos - 1] if pos > 0 else None,
                  "vor": offen[pos + 1] if pos + 1 < len(offen) else None}
    return render(request, "importe/vorschau.html", {
        "imp": imp, "vs": vorschau_berechnen(db, imp), "fehler": fehler, "navigation": navigation,
        "parser": [(p.key, p.name) for p in parser_verwaltung.aktive(db).values()],
        "msps": [(m.id, m.name) for m in db.scalars(select(Msp).order_by(Msp.name))],
        "ladearten": [(a.value, a.label) for a in Ladeart]}, status_code)


@router.get("/{import_id}")
def anzeigen(request: Request, import_id: int, db: Session = Depends(get_db)):
    imp = _laden(db, import_id)
    if imp.status == ImportStatus.vorschau:
        return _vorschau_seite(request, db, imp)
    if imp.status == ImportStatus.zurueckgestellt:  # nichts übernommen – die Merkliste steht auf der Import-Seite
        return RedirectResponse("/import#zurueckgestellt", status_code=303)
    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)
                           .order_by(Ladevorgang.ende)).all()
    gebuehren = db.scalars(select(Gebuehr).where(Gebuehr.import_id == imp.id).order_by(Gebuehr.id)).all()
    return render(request, "importe/detail.html", {
        "imp": imp, "vorgaenge": vorgaenge, "gebuehren": gebuehren,
        "_karten": kartenanzeige.fuer_vorgaenge(db, vorgaenge),
        "summe_kwh": sum((v.kwh for v in vorgaenge), Decimal(0)),
        "summe_kosten": sum((v.kosten_netto for v in vorgaenge), Decimal(0)),
        "summe_gebuehren": sum((g.betrag_netto for g in gebuehren), Decimal(0)),
        "ohne_fahrzeug": sum(1 for v in vorgaenge if v.fahrzeug_id is None),
        "ohne_stromart": sum(1 for v in vorgaenge if v.stromart is None and v.stromart_quelle is None)})


@router.post("/{import_id}/einstellungen")
def einstellungen_speichern(request: Request, import_id: int, parser_key: str = Form(""), msp_id: str = Form(""),
                            ladeart: str = Form(""), strompreis: str = Form(""), db: Session = Depends(get_db)):
    imp = _laden(db, import_id)
    if imp.status != ImportStatus.vorschau:
        raise HTTPException(409, "Import ist bereits abgeschlossen")
    try:
        preis = decimal_lesen(strompreis)
        if preis is not None and preis < 0:
            raise ValueError("Der Strompreis darf nicht negativ sein.")
    except ValueError as e:
        return _vorschau_seite(request, db, imp, str(e), 422)
    imp.parser_key = parser_key if parser_key in parser_verwaltung.aktive(db) else None
    imp.msp_id = int(msp_id) if msp_id.isdigit() and db.get(Msp, int(msp_id)) else None
    if ladeart in Ladeart.__members__:
        imp.ladeart = Ladeart(ladeart)
    imp.strompreis_netto_kwh = preis
    db.commit()
    return weiter(f"/import/{imp.id}")


@router.post("/{import_id}/bestaetigen")
async def bestaetigen_route(request: Request, import_id: int, db: Session = Depends(get_db)):
    imp = _laden(db, import_id)
    formular = await request.form()
    # Je unbekannter Karte: karte_<n>_kennung, karte_<n>_aktion (anlegen|ignorieren), _bezeichnung, _gast
    entscheidungen = {}
    for schluessel, kennung in formular.items():
        if schluessel.startswith("karte_") and schluessel.endswith("_kennung"):
            n = schluessel[len("karte_"):-len("_kennung")]
            entscheidungen[str(kennung)] = KartenEntscheidung(
                anlegen=formular.get(f"karte_{n}_aktion") != "ignorieren",
                bezeichnung=str(formular.get(f"karte_{n}_bezeichnung", "")).strip() or None,
                gast=formular.get(f"karte_{n}_gast") is not None)
    # AC/DC je Ladevorgang geändert: stromart_<Index in der Vorschau> = AC | DC | keine („so lassen“ = leer)
    je_vorgang = {int(k[len("stromart_"):]): str(w) for k, w in formular.items()
                  if re.fullmatch(r"stromart_\d+", k) and w in ("AC", "DC", "keine")}
    try:
        vs = bestaetigen(db, imp, entscheidungen, stromart.wert(str(formular.get("stromart_vorgabe", ""))), je_vorgang)
    except ImportFehler as e:
        db.rollback()
        return _vorschau_seite(request, db, imp, str(e), 422)
    db.commit()
    neue_karten = sum(1 for e in entscheidungen.values() if e.anlegen)
    meldung(request, f"{imp.dateiname} übernommen: {anzahl_text(len(vs.neue), 'Ladevorgang')}, "
                     f"{anzahl_text(len(vs.gebuehren), 'Gebühr')}"
                     + (f", {neue_karten} neue {'Ladekarte' if neue_karten == 1 else 'Ladekarten'}" if neue_karten else "") + ".")
    return _vorschau_erledigt(request, db, imp.id, import_id=imp.id)


@router.post("/{import_id}/zurueckstellen")
def zurueckstellen_route(request: Request, import_id: int, notiz: str = Form(""), db: Session = Depends(get_db)):
    """Vorschau auf die Merkliste „Zurückgestellt“ (z. B. nicht lesbar, später als Pauschale erfassen); weiter mit
    der nächsten Datei wie beim Bestätigen."""
    imp = _laden(db, import_id)
    try:
        zurueckstellen(imp, notiz)
    except ImportFehler as e:
        meldung(request, str(e), "fehler")
        return weiter("/import")
    db.commit()
    meldung(request, f"{imp.dateiname} zurückgestellt – steht auf der Import-Seite unter „Zurückgestellt“.")
    return _vorschau_erledigt(request, db, imp.id, zurueckgestellt=True)


@router.post("/{import_id}/wieder-pruefen")
def wieder_pruefen_route(request: Request, import_id: int, db: Session = Depends(get_db)):
    imp = _laden(db, import_id)
    try:
        wieder_pruefen(imp)
    except ImportFehler as e:
        meldung(request, str(e), "fehler")
        return weiter("/import")
    db.commit()
    return weiter(f"/import/{imp.id}")


@router.post("/vorschauen/loeschen")
def alle_vorschauen_loeschen(request: Request, db: Session = Depends(get_db)):
    """Alle offenen Vorschauen auf einmal verwerfen (samt hochgeladener Dateien) und den Durchgang abschließen."""
    vorschauen = db.scalars(select(Import).where(Import.status == ImportStatus.vorschau).order_by(Import.id)).all()
    if not vorschauen:
        meldung(request, "Keine offenen Vorschauen vorhanden.", "warnung")
        return weiter("/import")
    namen = [imp.dateiname for imp in vorschauen]
    pfade = [loeschen(db, imp) for imp in vorschauen]
    db.commit()
    for pfad in pfade:
        if pfad is not None:
            pfad.unlink(missing_ok=True)
    d = _durchgang(request)
    if d["fertig"]:
        d = _leer()
    request.session[DURCHGANG] = {**_verworfen(d, namen), "fertig": True}
    meldung(request, f"{len(namen)} Vorschau{'en' if len(namen) != 1 else ''} verworfen.")
    return weiter("/import#abschluss")


@router.post("/{import_id}/loeschen")
def loeschen_route(request: Request, import_id: int, db: Session = Depends(get_db)):
    imp = _laden(db, import_id)
    status, import_id, dateiname = imp.status, imp.id, imp.dateiname
    war_vorschau = status == ImportStatus.vorschau
    pfad = loeschen(db, imp)
    db.commit()
    if pfad is not None:
        pfad.unlink(missing_ok=True)
    if status == ImportStatus.zurueckgestellt:
        meldung(request, f"{dateiname} erledigt – von der Liste genommen und gelöscht.")
        return weiter("/import#zurueckgestellt")
    if not war_vorschau:
        meldung(request, "Import gelöscht – zugehörige Ladevorgänge und Gebühren wurden entfernt.")
        return weiter("/import")
    meldung(request, f"{dateiname} verworfen.")
    return _vorschau_erledigt(request, db, import_id, verworfen=dateiname)


@router.get("/{import_id}/datei")
def originaldatei(import_id: int, anzeigen: str = "", db: Session = Depends(get_db)):
    """Originaldatei herunterladen; `anzeigen=1`: im Browser öffnen (z. B. PDF in neuem Tab)."""
    imp = _laden(db, import_id)
    pfad = datei(imp)
    if not pfad.exists():
        raise HTTPException(404, "Originaldatei nicht mehr vorhanden")
    medientyp = mimetypes.guess_type(imp.dateiname)[0] or "application/octet-stream"
    return FileResponse(pfad, filename=imp.dateiname, media_type=medientyp,
                        content_disposition_type="inline" if anzeigen else "attachment")


@router.get("/{import_id}/vorschaubild")
def vorschaubild(import_id: int, db: Session = Depends(get_db)):
    """Erste Seite einer PDF-Rechnung als kleines JPG (Miniatur in der Vorschau)."""
    from app.services.reports import als_jpg
    imp = _laden(db, import_id)
    pfad = datei(imp)
    if not imp.dateiname.lower().endswith(".pdf") or not pfad.exists():
        raise HTTPException(404, "Keine PDF-Datei")
    try:
        bild = als_jpg(pfad.read_bytes(), 1, breite_px=900)  # scharf auch groß und auf HiDPI
    except HTTPException:
        raise
    except Exception:  # beschädigte oder verschlüsselte PDF: dann eben ohne Miniatur
        raise HTTPException(404, "PDF nicht lesbar")
    # Inhalt hängt nur an der Datei (Name = Hash) – darf der Browser lange behalten
    return Response(bild, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
