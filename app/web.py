"""Gemeinsame Hilfen für Routen: Templates, Meldungen, Weiterleitungen."""
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.version import version
from app import formatierung
from app.config import heute
from app.services.evse import cpo_land, landname
from app.services.geokodierung import verortung_text

def _optionen(objekte, wert_attr: str, text_attr: str) -> list[tuple]:
    """Objektliste -> [(wert, text)] für Auswahllisten."""
    return [(getattr(o, wert_attr), getattr(o, text_attr)) for o in objekte]


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters.update(
    datum=formatierung.datum,
    datumzeit=formatierung.datumzeit,
    zahl=formatierung.zahl,
    geld=formatierung.geld,
    monat=formatierung.monat,
    dauer=formatierung.dauer,
    prozent=formatierung.prozent,
    optionen=_optionen,
)
templates.env.globals["zeitraum"] = formatierung.zeitraum
templates.env.globals["minuten"] = formatierung.minuten
# Versionskennung für /static-Links, damit Browser nach Änderungen nicht die alte CSS/JS aus dem Cache nehmen
templates.env.globals["static_version"] = format(
    max(int(f.stat().st_mtime) for f in (Path(__file__).parent / "static").iterdir()), "x")
templates.env.globals["heute_datum"] = heute
templates.env.globals["app_version"] = version


def datumzeit_lokal(wert, sekunden: bool = False) -> str:
    """Zeitpunkt mit Zeitzone (z. B. aus timestamptz) in der Zeitzone der App."""
    if wert is None or wert == "":
        return ""
    if isinstance(wert, str):  # ISO-Text, z. B. aus einer Statusdatei
        from datetime import datetime
        wert = datetime.fromisoformat(wert)
    from zoneinfo import ZoneInfo
    from app.config import einstellungen
    if wert.tzinfo is not None:
        wert = wert.astimezone(ZoneInfo(einstellungen().tz))
    return wert.strftime("%d.%m.%Y %H:%M:%S" if sekunden else "%d.%m.%Y %H:%M")


templates.env.filters["datumzeit_lokal"] = datumzeit_lokal


def _protokoll_globals():
    from app.services import protokoll
    templates.env.globals.update(PROTOKOLL_TYPEN=protokoll.TYP_NAMEN, PROTOKOLL_AKTIONEN=protokoll.AKTIONEN,
                                 protokoll_warnung=protokoll.ist_warnung)


_protokoll_globals()
templates.env.globals["STROMART_TEXT"] = {"AC": "AC", "DC": "DC", None: "ohne Angabe"}


def leasing_daten(fahrzeuge, praefix: str = "") -> dict:
    """Für die Knöpfe „Gültig ab/bis = Leasing“ (_makros.leasing_knoepfe): Auswahlwert → [Beginn, Ende] (ISO)."""
    return {f"{praefix}{fz.id}": [fz.leasing_beginn.isoformat() if fz.leasing_beginn else None,
                                  fz.leasing_ende.isoformat() if fz.leasing_ende else None]
            for fz in fahrzeuge if fz.leasing_beginn or fz.leasing_ende}


templates.env.globals["leasing_daten"] = leasing_daten
# Abkürzung MSP mit Erklärung als Tooltip (für sichtbare Beschriftungen, nicht für Attribute oder Auswahltexte)
templates.env.filters["landname"] = landname
templates.env.filters["cpo_land"] = cpo_land
# Beschriftung des Stichtags: {{ STICHTAG[request.state.stichtag] }} → „Ladeende“ bzw. „Ladebeginn“
templates.env.globals["STICHTAG"] = {"ende": "Ladeende", "beginn": "Ladebeginn"}
templates.env.filters["flagge"] = formatierung.flagge
templates.env.globals["verortung"] = verortung_text
templates.env.globals["MSP"] = Markup('<abbr title="Mobility Service Provider = Ladekartenanbieter">MSP</abbr>')
templates.env.globals["CPO"] = Markup('<abbr title="Charge Point Operator = Betreiber der Ladestation '
                                      '(aus der EVSE-ID, z. B. AT*MER)">CPO</abbr>')


def zuordnung_vorbelegung(ab: str, bis: str = "") -> dict | None:
    """Formular „Neue Zuordnung“ aus Link-Parametern vorbelegen (z. B. Lücke in der Zeitleiste, Aufgaben);
    ungültige Daten werden ignoriert."""
    from datetime import date
    try:
        werte = {"ab": date.fromisoformat(ab).isoformat(), "neu_zuordnen": "on"}
        if bis:
            werte["bis"] = date.fromisoformat(bis).isoformat()
    except ValueError:
        return None
    return werte


def meldung(request: Request, text: str, art: str = "info", aktion: dict | None = None) -> None:
    """Meldung für die nächste angezeigte Seite merken (art: info | warnung | fehler).
    `aktion`: optionaler Knopf {"url", "text", "felder": {name: wert}} (POST); solche Meldungen bleiben stehen."""
    eintrag = {"text": text, "art": art}
    if aktion:
        eintrag["aktion"] = aktion
    request.session.setdefault("meldungen", []).append(eintrag)


def _meldungen_holen(request: Request) -> list[dict]:
    return request.session.pop("meldungen", [])


templates.env.globals["meldungen_holen"] = _meldungen_holen


def liste_merken(request: Request, art: str, **filter_werte) -> None:
    """Filter einer Liste in der Sitzung merken – für „← Alle …“ und das Blättern in den Detailseiten."""
    request.session[f"liste_{art}"] = {k: w for k, w in filter_werte.items() if w}


def blaettern(request: Request, db, art: str, basis: str, abfrage, aktuell_id: int, alle: dict | None = None) -> dict:
    """Vorherige/nächste Detailseite in der Reihenfolge der Liste, mit deren zuletzt gemerktem Filter.

    `abfrage(**filter)` liefert select(id, text) in Listenreihenfolge (text: Tooltip der Knöpfe). Liegt das Objekt
    nicht in der gefilterten Liste (z. B. inaktiv oder von anderswo geöffnet), wird in `alle` geblättert.
    Ergebnis: {"liste": URL der Liste, "position", "anzahl", "vorher"/"nachher": {"url", "text"} | None}."""
    from urllib.parse import urlencode
    gemerkt = request.session.get(f"liste_{art}") or {}
    ergebnis = {"liste": basis + (f"?{urlencode(gemerkt, doseq=True)}" if gemerkt else ""), "anzahl": 0}
    for filter_werte in (gemerkt, alle or {}):
        try:
            zeilen = db.execute(abfrage(**filter_werte)).all()
        except (TypeError, ValueError):  # veralteter Sitzungsinhalt
            continue
        ids = [z[0] for z in zeilen]
        if aktuell_id in ids:
            i = ids.index(aktuell_id)
            nachbar = lambda j: ({"url": f"{basis}/{zeilen[j][0]}", "text": zeilen[j][1]}
                                 if 0 <= j < len(zeilen) else None)
            return {**ergebnis, "position": i + 1, "anzahl": len(zeilen), "vorher": nachbar(i - 1),
                    "nachher": nachbar(i + 1)}
    return ergebnis


def render(request: Request, name: str, kontext: dict | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, kontext or {}, status_code=status_code)


def weiter(url: str) -> RedirectResponse:
    """Weiterleitung nach POST (Post/Redirect/Get)."""
    return RedirectResponse(url, status_code=303)
