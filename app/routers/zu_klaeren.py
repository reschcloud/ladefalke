"""Ansicht „Aufgaben“: Ladevorgänge ohne Fahrzeug, Gebühren ohne Ladekarte, unplausible AC/DC-Angaben,
Ladeorte ohne Koordinaten (SPEC 5)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Fahrzeug, Gebuehr, Import, Ladekarte, Ladeort, Ladevorgang, Msp, ZuordnungQuelle
from app.routers.ladevorgaenge import ladeort_plz_speichern
from app.services import app_einstellungen, fristen, kartenanzeige, kartenpruefung, luecken, stromart, zuordnung
from app.services.aufloesung import fahrzeug_setzen, neu_zuordnen, stichtag_spalte
from app.services.ladeprofil import leistung, unplausibel_grund, unplausibel_sql
from app.services.geokodierung import GENAUIGKEIT, alle_geokodieren, verzeichnis_vorhanden, verzichten, wieder_verorten
from app.services.offen import ladeorte_offen_bedingungen, ohne_fahrzeug_bedingungen, ursache
from app.web import meldung, render, weiter

router = APIRouter(prefix="/aufgaben")
alt = APIRouter()  # frühere Adressen „Nicht zugeordnet“ und „Zu klären“ (Lesezeichen)


@alt.get("/nicht-zugeordnet")
@alt.get("/zu-klaeren")
def frueher(request: Request):
    ziel = "/aufgaben" + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(ziel, status_code=301)


MAX_ZEILEN = 500


_ohne_fahrzeug = ohne_fahrzeug_bedingungen


def _zurueck(ziel: str, standard: str = "/aufgaben") -> str:
    """Rücksprung nach einer Aktion (Filter und Anker bleiben erhalten); nur Pfade unter /aufgaben."""
    return ziel if ziel.startswith("/aufgaben") else standard


@router.get("")
@zuordnung.vorgeladen_fuer_route  # Ursachen und Personen je Vorgang: Zuordnungen einmal laden statt je Tag/Vorgang
def uebersicht(request: Request, karte_id: str = "", roh: str = "", stromart_msp: str = "",
               db: Session = Depends(get_db)):
    tag = stichtag_spalte()  # Zeitraum und Sortierung nach dem Ladebeginn
    gruppen = db.execute(
        select(Ladevorgang.ladekarte_id, Ladevorgang.roh_kartenkennung, Ladevorgang.msp_id,
               func.count(), func.sum(Ladevorgang.kwh), func.sum(Ladevorgang.kosten),
               func.min(tag), func.max(tag))
        .where(*_ohne_fahrzeug())
        .group_by(Ladevorgang.ladekarte_id, Ladevorgang.roh_kartenkennung, Ladevorgang.msp_id)
        .order_by(func.count().desc())).all()
    karten = {k.id: k for k in db.scalars(select(Ladekarte).where(
        Ladekarte.id.in_([g[0] for g in gruppen if g[0]])).options(selectinload(Ladekarte.msp)))}
    ursachen = {kid: ursache(db, kid, tag) for kid in karten}

    filter_bed = []
    if karte_id.isdigit():
        filter_bed.append(Ladevorgang.ladekarte_id == int(karte_id))
    elif roh:
        filter_bed += [Ladevorgang.ladekarte_id.is_(None), Ladevorgang.roh_kartenkennung == roh]
    vorgaenge = db.scalars(
        select(Ladevorgang).where(*_ohne_fahrzeug(), *filter_bed)
        .options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.msp))
        .order_by(tag.desc()).limit(MAX_ZEILEN)).all()

    gaeste = db.execute(select(func.count(), func.coalesce(func.sum(Ladevorgang.kwh), 0),
                               func.coalesce(func.sum(Ladevorgang.kosten), 0))
                        .where(Ladevorgang.ladekarte.has(Ladekarte.ist_gastkarte))).one()
    gebuehren = db.scalars(select(Gebuehr).where(Gebuehr.ladekarte_id.is_(None))
                           .options(selectinload(Gebuehr.import_).selectinload(Import.msp))
                           .order_by(Gebuehr.id.desc())).all()
    karten_je_msp: dict[int, list] = {}
    for k in db.scalars(select(Ladekarte).where(Ladekarte.aktiv).order_by(Ladekarte.kennung)):
        karten_je_msp.setdefault(k.msp_id, []).append(k)

    einst = app_einstellungen.laden(db)
    unplausible = db.scalars(select(Ladevorgang).where(unplausibel_sql(einst))
                             .options(selectinload(Ladevorgang.ladekarte), selectinload(Ladevorgang.msp),
                                      selectinload(Ladevorgang.fahrzeug))
                             .order_by(tag.desc()).limit(MAX_ZEILEN)).all()  # Anzahl: request.state.offen

    ladeorte = db.execute(
        select(Ladeort, func.count(Ladevorgang.id), func.min(Ladevorgang.id))  # min: Link bei einem Vorgang
        .join(Ladevorgang, Ladevorgang.ladeort_id == Ladeort.id)
        .where(*ladeorte_offen_bedingungen()).group_by(Ladeort.id)
        .order_by(func.count(Ladevorgang.id).desc(), Ladeort.adresse)).all()

    ohne_stromart = db.execute(
        select(Msp, func.count(Ladevorgang.id)).join(Ladevorgang, Ladevorgang.msp_id == Msp.id)
        .where(stromart.ohne_angabe()).group_by(Msp.id).order_by(func.count(Ladevorgang.id).desc())).all()

    # einzelne Vorgänge ohne AC/DC-Angabe (optional je MSP) zum Abarbeiten
    stromart_bed = [stromart.ohne_angabe()] + ([Ladevorgang.msp_id == int(stromart_msp)] if stromart_msp.isdigit() else [])
    stromart_vorgaenge = db.scalars(
        select(Ladevorgang).where(*stromart_bed).options(selectinload(Ladevorgang.msp))
        .order_by(tag.desc()).limit(MAX_ZEILEN)).all()

    return render(request, "zu_klaeren.html", {
        "fristen": fristen.ermitteln(db, einst), "einst": einst, "luecken": luecken.ermitteln(db),
        "karten_befunde": kartenpruefung.ermitteln(db, einst),
        "ohne_stromart": ohne_stromart, "stromart_msp": stromart_msp if stromart_msp.isdigit() else "",
        "stromart_vorgaenge": [(v, kw, *stromart.vorschlag(kw, einst))
                               for v in stromart_vorgaenge for kw in [leistung(v)]],
        "ladeorte": ladeorte, "geo_vorhanden": verzeichnis_vorhanden(db),
        "nicht_verortet": db.execute(  # bewusst ohne Verortung – aufklappbar, mit „wieder verorten“
            select(Ladeort, func.count(Ladevorgang.id)).join(Ladevorgang, Ladevorgang.ladeort_id == Ladeort.id)
            .where(Ladeort.genauigkeit == "verzichtet").group_by(Ladeort.id).order_by(Ladeort.adresse)).all(),
        "unplausible": [(v, unplausibel_grund(v, einst)) for v in unplausible],
        "_karten": kartenanzeige.fuer_vorgaenge(db, [*vorgaenge, *unplausible]),
        "gruppen": gruppen, "karten": karten, "ursachen": ursachen, "vorgaenge": vorgaenge, "max_zeilen": MAX_ZEILEN,
        "gefiltert": bool(filter_bed), "gaeste": gaeste, "gebuehren": gebuehren, "karten_je_msp": karten_je_msp,
        "fahrzeuge": db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv).order_by(Fahrzeug.kennzeichen)).all()})


@router.post("/zuweisen")
def zuweisen(request: Request, vorgang_ids: list[int] = Form(default=[]), fahrzeug_id: str = Form(""),
             zurueck: str = Form(""), db: Session = Depends(get_db)):
    fahrzeug = db.get(Fahrzeug, int(fahrzeug_id)) if fahrzeug_id.isdigit() else None
    if fahrzeug is None or not vorgang_ids:
        meldung(request, "Bitte Ladevorgänge und ein Fahrzeug auswählen.", "fehler")
        return weiter(_zurueck(zurueck))
    for v in db.scalars(select(Ladevorgang).where(Ladevorgang.id.in_(vorgang_ids))):
        fahrzeug_setzen(v, fahrzeug, ZuordnungQuelle.manuell)
    db.commit()
    meldung(request, f"{len(vorgang_ids)} Ladevorgänge manuell Fahrzeug {fahrzeug.kennzeichen} zugeordnet.")
    return weiter(_zurueck(zurueck))


@router.post("/automatisch")
def automatisch(request: Request, zurueck: str = Form(""), db: Session = Depends(get_db)):
    vorher = db.scalar(select(func.count()).select_from(Ladevorgang).where(*_ohne_fahrzeug()))
    neu_zuordnen(db, Ladevorgang.fahrzeug_id.is_(None), Ladevorgang.zuordnung_quelle != ZuordnungQuelle.manuell)
    nachher = db.scalar(select(func.count()).select_from(Ladevorgang).where(*_ohne_fahrzeug()))
    db.commit()
    meldung(request, f"{vorher - nachher} Ladevorgänge konnten automatisch zugeordnet werden; "
                     f"{nachher} bleiben ohne Fahrzeug.")
    return weiter(_zurueck(zurueck))


@router.post("/gebuehr/{gebuehr_id}")
def gebuehr_zuweisen(request: Request, gebuehr_id: int, ladekarte_id: str = Form(""), zurueck: str = Form(""),
                     db: Session = Depends(get_db)):
    g = db.get(Gebuehr, gebuehr_id)
    if g is None:
        raise HTTPException(404, "Gebühr nicht gefunden")
    karte = db.get(Ladekarte, int(ladekarte_id)) if ladekarte_id.isdigit() else None
    if karte is None:
        meldung(request, "Bitte eine Ladekarte wählen.", "fehler")
        return weiter(_zurueck(zurueck))
    g.ladekarte_id = karte.id
    db.commit()
    meldung(request, f"Gebühr „{g.bezeichnung}“ der Ladekarte {karte.kennung} zugeordnet.")
    return weiter(_zurueck(zurueck))


@router.post("/ladeort/{ladeort_id}")
def ladeort_plz(request: Request, ladeort_id: int, plz: str = Form(""), ort: str = Form(""),
                db: Session = Depends(get_db)):
    ladeort = db.get(Ladeort, ladeort_id)
    if ladeort is None:
        raise HTTPException(404, "Ladeort nicht gefunden")
    if fehler := ladeort_plz_speichern(request, db, ladeort, plz, ort):
        meldung(request, fehler, "fehler")
    return weiter("/aufgaben#ladeorte")


@router.post("/ladeort/{ladeort_id}/verzichten")
def ladeort_verzichten(request: Request, ladeort_id: int, db: Session = Depends(get_db)):
    """Bewusst ohne Verortung: keine Aufgabe mehr; rückgängig durch Eintragen von PLZ oder Ort."""
    ladeort = db.get(Ladeort, ladeort_id)
    if ladeort is None:
        raise HTTPException(404, "Ladeort nicht gefunden")
    verzichten(ladeort)
    db.commit()
    meldung(request, f"„{ladeort.adresse}“ bleibt ohne Verortung (keine Aufgabe, nicht auf der Karte).", "info",
            {"url": f"/aufgaben/ladeort/{ladeort.id}/wieder-verorten", "text": "Rückgängig", "felder": {}})
    return weiter("/aufgaben#ladeorte")


@router.post("/ladeort/{ladeort_id}/wieder-verorten")
def ladeort_wieder_verorten(request: Request, ladeort_id: int, db: Session = Depends(get_db)):
    """„Nicht verorten“ zurücknehmen: der Ladeort wird neu verortet (und ist ggf. wieder eine Aufgabe)."""
    ladeort = db.get(Ladeort, ladeort_id)
    if ladeort is None:
        raise HTTPException(404, "Ladeort nicht gefunden")
    genauigkeit = wieder_verorten(db, ladeort)
    db.commit()
    if genauigkeit in ("plz", "ort", "manuell"):
        meldung(request, f"„{ladeort.adresse}“ ist wieder verortet ({GENAUIGKEIT[genauigkeit]}).")
    else:
        meldung(request, f"„{ladeort.adresse}“ wird wieder verortet, braucht aber eine PLZ oder einen Ort – "
                         "siehe „Ladeorte ohne Koordinaten“.", "warnung")
    return weiter("/aufgaben#ladeorte")


@router.post("/stromart")
def stromart_setzen(request: Request, msp_id: int = Form(...), stromart_vorgabe: str = Form(""),
                    db: Session = Depends(get_db)):
    """AC/DC für alle Ladevorgänge eines MSP ohne Angabe setzen."""
    art = stromart.wert(stromart_vorgabe)
    if art is None:
        meldung(request, "Bitte AC oder DC wählen.", "fehler")
        return weiter("/aufgaben#stromart")
    anzahl = stromart.vorgabe_anwenden(db, art, Ladevorgang.msp_id == msp_id)
    db.commit()
    meldung(request, f"{anzahl} Ladevorgänge auf {art} gesetzt.")
    return weiter("/aufgaben#stromart")


@router.post("/stromart-einzeln")
def stromart_einzeln(request: Request, vorgang_ids: list[int] = Form(default=[]), stromart_wert: str = Form(""),
                     stromart_msp: str = Form(""), db: Session = Depends(get_db)):
    """Markierte Vorgänge manuell auf AC, DC oder bewusst „keine Angabe“ setzen."""
    ziel = "/aufgaben" + (f"?stromart_msp={stromart_msp}" if stromart_msp.isdigit() else "") + "#stromart-einzeln"
    if not vorgang_ids:
        meldung(request, "Bitte Ladevorgänge markieren.", "fehler")
        return weiter(ziel)
    vorgaenge = db.scalars(select(Ladevorgang).where(Ladevorgang.id.in_(vorgang_ids))).all()
    for v in vorgaenge:
        stromart.manuell_setzen(v, stromart_wert)
    db.commit()
    meldung(request, f"{len(vorgaenge)} Ladevorgänge manuell auf {stromart.wert(stromart_wert) or 'keine Angabe'} gesetzt.")
    return weiter(ziel)


@router.post("/ladeorte/verorten")
def ladeorte_verorten(request: Request, db: Session = Depends(get_db)):
    """Alle Ladeorte neu verorten (z. B. nach dem Einspielen eines neuen Postleitzahlen-Verzeichnisses)."""
    ergebnis = alle_geokodieren(db)
    db.commit()
    meldung(request, f"Ladeorte neu verortet: {ergebnis.get('plz', 0)} über die PLZ, {ergebnis.get('ort', 0)} über "
                     f"den Ortsnamen, {ergebnis.get('land', 0)} nur auf das Land, {ergebnis.get(None, 0)} gar nicht.")
    return weiter("/aufgaben#ladeorte")
