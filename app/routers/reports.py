"""Reports: alle PDF-Reports an einem Ort – Zusammenfassung, Karten und Preise je CPO und Fahrer:innen-Reports für einen Zeitraum, zum
Herunterladen (alle Rollen) und Versenden per Mail (Admin und Operator; services/versand.py)."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Benutzer, Fahrer
from app.routers.auswertungen import zeitraum_navigation
from app.services import app_einstellungen, mail, reports, versand
from app.services.auswertung import auswerten, vormonat, zeitraum_aus_parametern
from app.web import meldung, render, weiter

router = APIRouter(prefix="/reports")


def _zeitraum(db: Session, art: str, jahr: str, nr: str, von: str, bis: str):
    return zeitraum_aus_parametern(art, jahr, nr, von, bis, standard=vormonat(), db=db)


@router.get("")
def seite(request: Request, art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
          fahrer: str = "", db: Session = Depends(get_db)):
    """`fahrer`: Person hervorheben (Links von der Fahrer:innen-Seite und der Auswertung)."""
    z = _zeitraum(db, art, jahr, nr, von, bis)
    a = auswerten(db, z, app_einstellungen.laden(db))
    hervor = int(fahrer) if fahrer.isdigit() else None
    personen = sorted(a.fahrer, key=lambda zl: zl.fahrer.name.lower())
    ohne = None
    if hervor and not any(zl.fahrer.id == hervor for zl in personen):
        ohne = db.get(Fahrer, hervor)  # hervorgehobene Person ohne Ladungen im Zeitraum
    from app.services.app_einstellungen import TLP, laden
    e = laden(db)
    tlp = e.report_tlp
    benutzer = db.scalars(select(Benutzer).where(Benutzer.aktiv).order_by(Benutzer.name)).all()
    zuletzt = versand.zuletzt(db, z)
    # Versanddialog: letzter Versand je Report und Benutzer:in (offene Aufgaben unabhängig vom Zeitraum)
    zuletzt_je = {r: {bid: v for (art, bid), v in zuletzt.items() if art == r}
                  for r in ("zusammenfassung", "karten_cpo", "flottenbilanz")}
    zuletzt_je["aufgaben"] = versand.zuletzt_aufgaben(db)
    zuletzt_je["zuordnungen"] = versand.zuletzt_aufgaben(db, "zuordnungen")
    return render(request, "reports.html", {"a": a, "personen": personen, "hervor": hervor, "ohne": ohne,
                                            "tlp": tlp, "tlp_bedeutung": TLP[tlp][0] if tlp else "",
                                            "mail_fehlt": mail.konfiguration(db).fehlt, "zuletzt": zuletzt,
                                            "zuletzt_je": zuletzt_je, "zuletzt_adressen": versand.zuletzt_adressen(db),
                                            "benutzer": benutzer, "protokoll": versand.protokoll(db),
                                            "aufgaben_zeitplan": _aufgaben_zeitplan(e),
                                            "zuordnungen_zeitplan": _zeitplan_text(
                                                e.zuordnungen_auto, e.zuordnungen_auto_wochentag,
                                                e.zuordnungen_auto_tag, e.zuordnungen_auto_uhrzeit),
                                            **zeitraum_navigation(db, z, "/reports",
                                                                  f"fahrer={hervor}" if hervor else "")})


def _zeitplan_text(intervall: str, wochentag: int, tag: int, uhrzeit: str) -> str:
    """„wöchentlich, Montag 07:00“, „monatlich, am Monatsletzten 07:00“ bzw. leer, wenn aus."""
    from app.services.app_einstellungen import AUFGABEN_ZEITPLAENE, WOCHENTAGE
    if intervall == "aus":
        return ""
    wann = {"woechentlich": f", {WOCHENTAGE[wochentag]}",
            "monatlich": ", am Monatsletzten" if tag == 0 else f", am {tag}."}
    return f"{AUFGABEN_ZEITPLAENE[intervall]}{wann.get(intervall, '')} {uhrzeit}"


def _aufgaben_zeitplan(e) -> str:
    text = _zeitplan_text(e.aufgaben_auto, e.aufgaben_auto_wochentag, e.aufgaben_auto_tag, e.aufgaben_auto_uhrzeit)
    return text + (" (nur wenn etwas offen ist)" if text and e.aufgaben_auto_nur_offen else "")


@router.get("/zusammenfassung.pdf")
def zusammenfassung(art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
                    db: Session = Depends(get_db)):
    z = _zeitraum(db, art, jahr, nr, von, bis)
    return reports.antwort(reports.zusammenfassung(db, z), reports.dateiname(z, "zusammenfassung"))


@router.get("/karten-cpo.pdf")
def karten_cpo(art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
               db: Session = Depends(get_db)):
    z = _zeitraum(db, art, jahr, nr, von, bis)
    return reports.antwort(reports.karten_cpo(db, z), reports.dateiname(z, "karten-cpo"))


@router.get("/flottenbilanz.pdf")
def flottenbilanz(art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
                  db: Session = Depends(get_db)):
    z = _zeitraum(db, art, jahr, nr, von, bis)
    return reports.antwort(reports.flottenbilanz(db, z), reports.dateiname(z, "flottenbilanz"))


@router.get("/flottenbilanz-{seite}.jpg")
def flottenbilanz_jpg(seite: int, art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
                      db: Session = Depends(get_db)):
    """Eine Seite der Flottenbilanz als Bild in 4K (3840 × 2160), z. B. für Präsentationen oder Social Media."""
    z = _zeitraum(db, art, jahr, nr, von, bis)
    name = reports.dateiname(z, "flottenbilanz").removesuffix(".pdf") + f"-seite-{seite}.jpg"
    return reports.antwort(reports.als_jpg(reports.flottenbilanz(db, z), seite), name, "image/jpeg")


def _stichtag(text: str):
    """Stichtag aus ISO-Datum; leer oder ungültig = heute."""
    from datetime import date
    from app.config import heute
    try:
        return date.fromisoformat(text) if text else heute()
    except ValueError:
        return heute()


@router.get("/zuordnungen.pdf")
def zuordnungen(stichtag: str = "", db: Session = Depends(get_db)):
    tag = _stichtag(stichtag)
    return reports.antwort(reports.zuordnungen(db, tag), reports.zuordnungen_dateiname(tag))


@router.get("/offene-aufgaben.pdf")
def offene_aufgaben(db: Session = Depends(get_db)):
    from app.config import heute
    return reports.antwort(reports.offene_aufgaben(db), f"ladefalke-offene-aufgaben-{heute():%Y-%m-%d}.pdf")


@router.get("/fahrer/{fahrer_id}.pdf")
def fahrer_report(fahrer_id: int, art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
                  db: Session = Depends(get_db)):
    z = _zeitraum(db, art, jahr, nr, von, bis)
    person = db.get(Fahrer, fahrer_id)
    if person is None:
        raise HTTPException(404, "Fahrer:in nicht gefunden")
    return reports.antwort(reports.fahrer(db, z, fahrer_id), reports.dateiname(z, "report", person.name))


@router.get("/fahrer.zip")
def alle_fahrer(art: str = "monat", jahr: str = "", nr: str = "", von: str = "", bis: str = "",
                db: Session = Depends(get_db)):
    z = _zeitraum(db, art, jahr, nr, von, bis)
    inhalt, _ = reports.alle_fahrer_zip(db, z)
    return reports.antwort(inhalt, reports.dateiname(z, "reports-fahrerinnen").replace(".pdf", ".zip"), "application/zip")


# ------------------------------------------------------------------ Versand (Admin und Operator)

def _zurueck(z, anker: str = "") -> str:
    return f"/reports?{urlencode(z.als_parameter())}{anker}"


def _senden(request: Request, db: Session, z, ausfuehren, was: str, anker: str):
    try:
        ergebnis = ausfuehren()
    except mail.MailFehler as e:
        meldung(request, f"Nicht gesendet: {e}", "fehler")
        return weiter(_zurueck(z, anker))
    db.commit()
    meldung(request, ergebnis.text(was), "fehler" if ergebnis.fehler and not ergebnis.gesendet else
            "warnung" if ergebnis.fehler or ergebnis.ohne_adresse else "info")
    return weiter(_zurueck(z, anker))


@router.post("/fahrer/senden")
async def fahrer_senden(request: Request, art: str = "monat", jahr: str = "", nr: str = "", von: str = "",
                        bis: str = "", db: Session = Depends(get_db)):
    """Versanddialog Fahrer:innen-Reports: an die angehakten Personen (nur an deren eigene Adresse), auf Wunsch je
    Report eine Kopie an die angemeldete Benutzer:in."""
    z = _zeitraum(db, art, jahr, nr, von, bis)
    formular = await request.form()
    ids = [int(x) for x in formular.getlist("fahrer_id") if str(x).isdigit()]
    if not ids:
        meldung(request, "Bitte mindestens eine Fahrer:in auswählen.", "fehler")
        return weiter(_zurueck(z, "#fahrerinnen"))
    ich = request.state.benutzer
    kopie = db.get(Benutzer, ich.id) if formular.get("kopie") is not None else None
    anker = f"#fahrer-{ids[0]}" if len(ids) == 1 else "#fahrerinnen"
    return _senden(request, db, z, lambda: versand.fahrer_senden(db, z, ids, von=ich.id, kopie_an=kopie),
                   f"Ladereport {z.label}" if len(ids) == 1 else f"Fahrer:innen-Reports {z.label}", anker)


@router.post("/{report}/senden")
async def report_senden(request: Request, report: str, art: str = "monat", jahr: str = "", nr: str = "", von: str = "",
                        bis: str = "", db: Session = Depends(get_db)):
    """Versanddialog: Report (zusammenfassung, karten_cpo, flottenbilanz, aufgaben, zuordnungen) einmalig an ausgewählte
    Benutzer:innen und frei eingegebene Adressen."""
    if report not in versand.TITEL or report == "fahrer":
        raise HTTPException(404, "Unbekannter Report")
    z = _zeitraum(db, art, jahr, nr, von, bis)
    anker = "#" + {"karten_cpo": "karten-cpo", "aufgaben": "offene-aufgaben"}.get(report, report)
    formular = await request.form()
    ids = [int(x) for x in formular.getlist("benutzer_id") if str(x).isdigit()]
    adressen, ungueltig = versand.adressen_lesen(str(formular.get("adressen", "")))
    if ungueltig:
        meldung(request, f"Nicht gesendet – ungültige Adresse{'n' if len(ungueltig) > 1 else ''}: {', '.join(ungueltig)}",
                "fehler")
        return weiter(_zurueck(z, anker))
    if not ids and not adressen:
        meldung(request, "Bitte mindestens eine:n Empfänger:in auswählen oder eine Adresse eintragen.", "fehler")
        return weiter(_zurueck(z, anker))
    if report == "zuordnungen":  # Stichtag aus dem Dialog
        zeit = versand.stand_heute(_stichtag(str(formular.get("stichtag", ""))))
        titel = f"Zuordnungen am {zeit.von:%d.%m.%Y}"
    else:
        zeit = versand.stand_heute() if report == "aufgaben" else z
        titel = versand.TITEL[report] + ("" if report == "aufgaben" else f" {z.label}")
    return _senden(request, db, z, lambda: versand.report_senden(
        db, report, zeit, ids, adressen, von=request.state.benutzer.id, mit_bildern=formular.get("bilder") is not None),
        titel, anker)
