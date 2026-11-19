"""Stammdaten: Ladekarten inkl. Zuordnungshistorie."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import heute
from app.db import get_db
from app.formatierung import datum_lesen
from app.models import Fahrer, Fahrzeug, Gebuehr, KennungTyp, Ladekarte, Ladevorgang, Msp, ZuordnungKarte
from app.routers.ladevorgaenge import mehrfach_bedingung
from app.services import mini_zeitleiste
from app.services import kennzahlen, protokoll
from app.services.aufloesung import neu_zuordnen_karte
from app.services.fahrzeug_hinweise import Hinweis
from app.services.ladevorgaenge import letzte_vorgaenge
from app.services.offen import ohne_fahrzeug_bedingungen
from app.services.zuordnung import ZuordnungsFehler, fahrer_von_fahrzeug_am, gueltig_am, karte_zuordnen
from app.web import blaettern, liste_merken, meldung, render, weiter, zuordnung_vorbelegung

router = APIRouter(prefix="/stammdaten/ladekarten")


def ziel_lesen(ziel: str) -> tuple[int | None, int | None]:
    """'fahrer:3' / 'fahrzeug:5' -> (fahrer_id, fahrzeug_id)"""
    art, _, nr = ziel.partition(":")
    if not nr.isdigit() or art not in ("fahrer", "fahrzeug"):
        raise ZuordnungsFehler("Bitte genau eine Fahrer:in oder ein Fahrzeug wählen.")
    return (int(nr), None) if art == "fahrer" else (None, int(nr))


def ziele(db: Session, fahrer_id: int | None = None, fahrzeug_id: int | None = None) -> dict:
    """Auswahllisten für Zuordnungsziele: aktive Einträge plus ggf. das bisherige Ziel."""
    return {"fahrer_liste": db.scalars(select(Fahrer).where(Fahrer.aktiv | (Fahrer.id == fahrer_id))
                                       .order_by(Fahrer.name)).all(),
            "fahrzeug_liste": db.scalars(select(Fahrzeug).where(Fahrzeug.aktiv | (Fahrzeug.id == fahrzeug_id))
                                         .order_by(Fahrzeug.kennzeichen)).all()}


def _hat_buchungen(db: Session, karte_id: int) -> bool:
    return bool(db.scalar(select(func.count()).select_from(Ladevorgang).where(Ladevorgang.ladekarte_id == karte_id))
                or db.scalar(select(func.count()).select_from(Gebuehr).where(Gebuehr.ladekarte_id == karte_id)))


def _laden(db: Session, karte_id: int) -> Ladekarte:
    karte = db.get(Ladekarte, karte_id)
    if karte is None:
        raise HTTPException(404, "Ladekarte nicht gefunden")
    return karte


def _werte(msp_id, kennung, kennung_typ, bezeichnung, ist_gastkarte, aktiv, notiz, vertragsnummer) -> dict:
    return {"msp_id": int(msp_id) if msp_id.isdigit() else None, "kennung": kennung.strip(),
            "kennung_typ": kennung_typ, "bezeichnung": bezeichnung.strip() or None,
            "vertragsnummer": vertragsnummer.strip() or None,
            "ist_gastkarte": ist_gastkarte is not None, "aktiv": aktiv is not None, "notiz": notiz.strip() or None}


def _pruefen(db: Session, werte: dict, karte: Ladekarte | None) -> dict:
    fehler = {}
    if werte["msp_id"] is None or db.get(Msp, werte["msp_id"]) is None:
        fehler["msp_id"] = "Bitte einen MSP wählen."
    if not werte["kennung"]:
        fehler["kennung"] = "Bitte eine Kennung angeben."
    elif "msp_id" not in fehler and db.scalar(select(Ladekarte.id).where(
            Ladekarte.msp_id == werte["msp_id"], Ladekarte.kennung == werte["kennung"],
            Ladekarte.id != (karte.id if karte else 0))):
        fehler["kennung"] = "Diese Kennung existiert bei diesem MSP bereits."
    if werte["kennung_typ"] not in KennungTyp.__members__:
        fehler["kennung_typ"] = "Bitte einen Kennungstyp wählen."
    if werte["vertragsnummer"] and "msp_id" not in fehler and db.scalar(select(Ladekarte.id).where(
            Ladekarte.msp_id == werte["msp_id"], Ladekarte.vertragsnummer == werte["vertragsnummer"],
            Ladekarte.id != (karte.id if karte else 0))):
        fehler["vertragsnummer"] = "Diese Vertragsnummer ist bei diesem MSP bereits einer anderen Karte zugeordnet."
    if werte["ist_gastkarte"] and karte is not None and karte.zuordnungen:
        fehler["ist_gastkarte"] = "Die Karte hat Zuordnungen – als Gastkarte nur ohne Zuordnungen möglich."
    return fehler


def _lokal(ziel: str) -> str:
    """Rücksprungziel nur als lokaler Pfad (sonst leer)."""
    return ziel if ziel.startswith("/") and not ziel.startswith("//") else ""


def _detail(request, db, karte, werte, fehler=None, status_code=200, zuordnung_werte=None, zuordnung_fehler=None,
            zurueck=""):
    kontext = {"karte": karte, "werte": werte, "fehler": fehler or {}, "zurueck": _lokal(zurueck),
               "msp_optionen": [(x.id, x.name) for x in db.scalars(select(Msp).order_by(Msp.name))],
               "kennung_typen": [(t.value, t.label) for t in KennungTyp],
               "zuordnung_werte": zuordnung_werte or {"ab": heute().isoformat(), "neu_zuordnen": "on"},  # Vorauswahl
               "zuordnung_fehler": zuordnung_fehler, "heute": heute(), **ziele(db),
               "vorgaenge": letzte_vorgaenge(db, Ladevorgang.ladekarte_id == karte.id) if karte else [],
               "referenziert": bool(karte and (karte.zuordnungen or _hat_buchungen(db, karte.id)))}
    if karte is not None:  # Ladekarte ohne Zuordnung ist kein Fehler: Lücken nur neutral („nicht zugeordnet“)
        kontext["mini"] = mini_zeitleiste.berechnen(
            [mini_zeitleiste.Eintrag(
                z.gueltig_ab, z.gueltig_bis, z.fahrer.name if z.fahrer else z.fahrzeug.kennzeichen,
                f"/stammdaten/fahrer/{z.fahrer_id}" if z.fahrer_id else f"/stammdaten/fahrzeuge/{z.fahrzeug_id}",
                bearbeiten=f"/stammdaten/zuordnungen/karte/{z.id}?zurueck=/stammdaten/ladekarten/{karte.id}",
                art="person" if z.fahrer_id else "auto")
             for z in karte.zuordnungen], heute(), luecken_art=None if karte.ist_gastkarte else "neutral")
    kontext["verlauf"] = protokoll.verlauf(request, db, "ladekarte", karte.id if karte else None)
    kontext["gesamt"] = kennzahlen.ladekarte(db, karte.id) if karte else None
    kontext["erster"], kontext["letzter"] = (kennzahlen.erster_und_letzter(db, Ladevorgang.ladekarte_id == karte.id)
                                             if karte else (None, None))
    ohne = db.scalar(select(func.count()).select_from(Ladevorgang).where(
        Ladevorgang.ladekarte_id == karte.id, *ohne_fahrzeug_bedingungen())) if karte else 0
    kontext["offen"] = [Hinweis("rot", f"{ohne} Ladevorgang ohne Fahrzeug" if ohne == 1
                                else f"{ohne} Ladevorgänge ohne Fahrzeug", f"/aufgaben?karte_id={karte.id}",
                                "Unter „Aufgaben“ einem Fahrzeug zuordnen")] if ohne else []
    kontext["blaettern"] = blaettern(
        request, db, "ladekarten", "/stammdaten/ladekarten",
        lambda **f: _abfrage(Ladekarte.id, Msp.name + " " + Ladekarte.kennung, **f), karte.id,
        alle={"status": "alle"}) if karte else None
    return render(request, "stammdaten/ladekarte_detail.html", kontext, status_code)


def _werte_von(karte: Ladekarte) -> dict:
    return {"msp_id": karte.msp_id, "kennung": karte.kennung, "kennung_typ": karte.kennung_typ.value,
            "bezeichnung": karte.bezeichnung, "vertragsnummer": karte.vertragsnummer, "ist_gastkarte": karte.ist_gastkarte, "aktiv": karte.aktiv,
            "notiz": karte.notiz}


def _abfrage(*spalten, status: str = "aktiv", msp_id: list[str] = ()):
    """Ladekarten der Liste in deren Reihenfolge (auch fürs Blättern in der Detailseite)."""
    q = select(*spalten).select_from(Ladekarte).join(Msp).order_by(Msp.name, Ladekarte.kennung, Ladekarte.id)
    if status != "alle":
        q = q.where(Ladekarte.aktiv == (status == "aktiv"))
    if (b := mehrfach_bedingung(Ladekarte.msp_id, list(msp_id), zahlen=True)) is not None:
        q = q.where(b)
    return q


@router.get("")
def liste(request: Request, status: str = "aktiv", msp_id: list[str] = Query([]), db: Session = Depends(get_db)):
    """`msp_id`: Mehrfachauswahl."""
    msp_id = [m for m in msp_id if m]
    q = _abfrage(Ladekarte, status=status, msp_id=msp_id).options(selectinload(Ladekarte.msp))
    liste_merken(request, "ladekarten", status=status, msp_id=msp_id)
    aktuell = {z.ladekarte_id: z for z in db.scalars(
        select(ZuordnungKarte).where(gueltig_am(ZuordnungKarte, heute())))}
    return render(request, "stammdaten/ladekarte_liste.html", {
        "karten": db.scalars(q).all(), "aktuell": aktuell, "status": status, "msp_id": msp_id,
        "letzte": kennzahlen.letzte_je_karte(db),
        # Fahrzeugkarten: wer das Fahrzeug heute fährt („Kennzeichen (Person)“)
        "fahrer_heute": {z.fahrzeug_id: fahrer_von_fahrzeug_am(db, z.fahrzeug_id, heute())
                         for z in aktuell.values() if z.fahrzeug_id},
        "msp_paare": [("msp_id", x) for x in msp_id],
        "msp_optionen": [(str(x.id), x.name) for x in db.scalars(select(Msp).order_by(Msp.name))]})


@router.get("/neu")
def neu_formular(request: Request, db: Session = Depends(get_db)):
    return _detail(request, db, None, {"aktiv": True, "kennung_typ": "kartennummer"})


@router.post("/neu")
def neu(request: Request, msp_id: str = Form(""), kennung: str = Form(""), kennung_typ: str = Form(""),
        bezeichnung: str = Form(""), ist_gastkarte: str | None = Form(None), aktiv: str | None = Form(None),
        notiz: str = Form(""), vertragsnummer: str = Form(""), db: Session = Depends(get_db)):
    werte = _werte(msp_id, kennung, kennung_typ, bezeichnung, ist_gastkarte, aktiv, notiz, vertragsnummer)
    if fehler := _pruefen(db, werte, None):
        return _detail(request, db, None, werte, fehler, 422)
    karte = Ladekarte(**{**werte, "kennung_typ": KennungTyp(kennung_typ)})
    db.add(karte)
    db.commit()
    meldung(request, f"Ladekarte {karte.kennung} angelegt.")
    return weiter(f"/stammdaten/ladekarten/{karte.id}")


@router.get("/zuordnen")
def zuordnen_auswahl(karte_id: str = "", ziel: str = "", zurueck: str = ""):
    """Von der Fahrzeug- bzw. Personenseite: gewählte Karte mit vorbelegtem Ziel öffnen, danach zurück."""
    if not karte_id.isdigit():
        return weiter(_lokal(zurueck) or "/stammdaten/ladekarten")
    return weiter(f"/stammdaten/ladekarten/{karte_id}?" + urlencode({"ziel": ziel, "zurueck": _lokal(zurueck)})
                  + "#neue-karten-zuordnung")


@router.get("/{karte_id}")
def detail(request: Request, karte_id: int, zurueck: str = "", ab: str = "", ziel: str = "",
           db: Session = Depends(get_db)):
    """`zurueck`: nach dem Zuordnen dorthin (z. B. Aufgaben); `ab`: Vorbelegung „Gültig ab“ (ISO-Datum);
    `ziel`: Vorbelegung Fahrer:in bzw. Fahrzeug („fahrer:ID“ / „fahrzeug:ID“)."""
    karte = _laden(db, karte_id)
    werte = zuordnung_vorbelegung(ab) if ab else None
    if ziel.startswith(("fahrer:", "fahrzeug:")):
        werte = (werte or {"ab": heute().isoformat(), "neu_zuordnen": "on"}) | {"ziel": ziel}
    return _detail(request, db, karte, _werte_von(karte), zuordnung_werte=werte, zurueck=zurueck)


@router.post("/{karte_id}")
def bearbeiten(request: Request, karte_id: int, msp_id: str = Form(""), kennung: str = Form(""),
               kennung_typ: str = Form(""), bezeichnung: str = Form(""), ist_gastkarte: str | None = Form(None),
               aktiv: str | None = Form(None), notiz: str = Form(""), vertragsnummer: str = Form(""),
               db: Session = Depends(get_db)):
    karte = _laden(db, karte_id)
    werte = _werte(msp_id, kennung, kennung_typ, bezeichnung, ist_gastkarte, aktiv, notiz, vertragsnummer)
    if fehler := _pruefen(db, werte, karte):
        return _detail(request, db, karte, werte, fehler, 422)
    for feld, wert in {**werte, "kennung_typ": KennungTyp(kennung_typ)}.items():
        setattr(karte, feld, wert)
    db.commit()
    meldung(request, f"Ladekarte {karte.kennung} gespeichert.")
    return weiter(f"/stammdaten/ladekarten/{karte.id}")


@router.post("/{karte_id}/zuordnung")
def zuordnen(request: Request, karte_id: int, ziel: str = Form(""), ab: str = Form(""), bis: str = Form(""),
             neu_zuordnen: str | None = Form(None), zurueck: str = Form(""), db: Session = Depends(get_db)):
    karte = _laden(db, karte_id)
    zuordnung_werte = {"ziel": ziel, "ab": ab, "bis": bis, "neu_zuordnen": neu_zuordnen}
    try:
        fahrer_id, fahrzeug_id = ziel_lesen(ziel)
        ab_datum = datum_lesen(ab)
        if ab_datum is None:
            raise ZuordnungsFehler("Bitte ein Beginndatum angeben.")
        bis_datum = datum_lesen(bis)
        neu, hinweise = karte_zuordnen(db, karte, ab=ab_datum, bis=bis_datum, fahrer_id=fahrer_id,
                                       fahrzeug_id=fahrzeug_id, heute=heute())
    except ValueError as e:  # ZuordnungsFehler oder ungültiges Datum
        db.rollback()
        return _detail(request, db, karte, _werte_von(karte), status_code=422,
                       zuordnung_werte=zuordnung_werte, zuordnung_fehler=str(e), zurueck=zurueck)
    if neu_zuordnen:
        neu_info = (f"{neu_zuordnen_karte(db, karte.id, ab_datum, bis_datum)} bereits importierte "
                         "Ladevorgänge wurden neu zugeordnet.")
    db.commit()
    meldung(request, f"Ladekarte {karte.kennung} ist ab {ab_datum:%d.%m.%Y} {neu.ziel_name} zugeordnet.")
    if neu_zuordnen:
        meldung(request, neu_info)
    for h in hinweise:
        meldung(request, h, "warnung")
    return weiter(_lokal(zurueck) or f"/stammdaten/ladekarten/{karte.id}")


@router.post("/{karte_id}/aktivieren")
def aktivieren(request: Request, karte_id: int, db: Session = Depends(get_db)):
    obj = _laden(db, karte_id)
    obj.aktiv = True
    db.commit()
    meldung(request, f"{f"Ladekarte {obj.kennung}"} ist wieder aktiv.")
    return weiter(f"/stammdaten/ladekarten/{obj.id}")


@router.post("/{karte_id}/loeschen")
def loeschen(request: Request, karte_id: int, db: Session = Depends(get_db)):
    karte = _laden(db, karte_id)
    if karte.zuordnungen or _hat_buchungen(db, karte.id):
        karte.aktiv = False
        db.commit()
        meldung(request, f"Ladekarte {karte.kennung} wird verwendet und wurde deaktiviert statt gelöscht.",
                "warnung")
        return weiter(f"/stammdaten/ladekarten/{karte.id}")
    db.delete(karte)
    db.commit()
    meldung(request, f"Ladekarte {karte.kennung} gelöscht.")
    return weiter("/stammdaten/ladekarten")
