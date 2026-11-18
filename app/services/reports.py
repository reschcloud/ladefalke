"""PDF-Reports (WeasyPrint: HTML/CSS → PDF) mit denselben Berechnungen wie Dashboard und Auswertungen.

- Zusammenfassung eines Zeitraums (Monat, Quartal, Halbjahr, Jahr; auch „Alles“): Kennzahlen mit Vergleich zum
  Durchschnitt der drei Vorperioden, Vollständigkeit, Verteilungen (Torten), Toplisten, Gesamt, pro Fahrer:in,
  Fahrzeug, CPO, MSP, Land, meistgenutzte Ladekarten.
- Fahrer:innen-Report: die eigenen Daten einer Person (Summen, kein Einzelnachweis), dazu nur Aggregate der Flotte –
  Ø €/kWh im Vergleich zum Flottenschnitt und der eigene Platz in der Topliste der günstigsten Öffentlich-Lader:innen,
  aber keine Namen oder Werte anderer Personen. Vergleich mit den eigenen Vorperioden; Torten kWh nach MSP und CPO,
  Heatmap der eigenen öffentlichen Ladungen.
- Flottenbilanz (Marketing-Report) zum Weitergeben nach außen, 16:9 ohne Kopf- und Fußzeile: Flottenzahlen und
  Heatmap (ohne Personen-, Fahrzeug- und Kartendaten, nur Ø €/kWh), dazu das Siegertreppchen der günstigsten
  Öffentlich-Lader:innen (Namen, keine Kennzeichen); ohne TLP-Kennzeichnung.
- Zuordnungen am Stichtag (A4 quer): je Fahrer:in Fahrzeug und Ladekarten je MSP.
- Alle mit Heatmap des öffentlichen Ladens (services/kartenbild.py, aus der lokalen Grundkarte).
Druckvorlagen unter templates/reports/; Kopf- und Fußzeile über @page (Seite x von y).
"""
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import einstellungen
from sqlalchemy import and_, func, select

from app.models import Fahrer, Ladeart, Ladevorgang
from app.services import app_einstellungen, diagramme, karte, kartenbild
from app.services.aufloesung import personen_filter
from app.services.auswertung import FahrerZeile, Zeitraum, auswerten, vergleichen, zeitbedingung
from app.services.vollstaendigkeit import pruefen as vollstaendigkeit_pruefen

STATIC = Path(__file__).resolve().parent.parent / "static"


def _heatmap(k: karte.Karte, breite: int = 1600, hoehe: int = 950) -> dict:
    """Kartenbild der öffentlichen Ladeorte (data-URI) mit Kennzahlen für die Bildunterschrift."""
    return {"bild": kartenbild.data_uri(kartenbild.bild([(p.lat, p.lon, p.anzahl) for p in k.punkte], breite, hoehe)),
            "ladungen": k.anzahl, "orte": len(k.punkte), "ohne_ort": k.ohne_ort}


def _ladedauer_dc(db: Session, z: Zeitraum, stichtag: str, fahrer_id: int | None = None):
    """Ø DC-Ladedauer (services/kennzahlen.py) – der Person mit Flottenschnitt bzw. der Flotte."""
    from app.services import kennzahlen
    return kennzahlen.ladedauer_dc(db, z, stichtag, fahrer_id=fahrer_id)


def _vorperioden(z: Zeitraum) -> list[Zeitraum]:
    """Wie im Dashboard: die drei Vorperioden (bei „Alles“ keine)."""
    return [] if z.art == "alles" else [z.verschoben(-i) for i in (1, 2, 3)]


def _erstellt() -> datetime:
    return datetime.now(ZoneInfo(einstellungen().tz))


def _pdf(vorlage: str, **kontext) -> bytes:
    from weasyprint import HTML  # spät importiert: nur beim Erzeugen gebraucht
    from app.services.app_einstellungen import TLP
    from app.web import templates
    tlp = kontext.pop("tlp", "")
    html = templates.get_template(vorlage).render(erstellt=_erstellt(), tlp=tlp, tlp_farbe=TLP[tlp][1] if tlp else "",
                                                  **kontext)
    return HTML(string=html, base_url=str(STATIC)).write_pdf()


def zusammenfassung(db: Session, z: Zeitraum) -> bytes:
    einst = app_einstellungen.laden(db)
    a = auswerten(db, z, einst)
    vergleich = vergleichen(a.gesamt, [(vz, auswerten(db, vz, einst, mit_personen=False).gesamt)
                                       for vz in _vorperioden(z)])
    monate = diagramme.je_monat(db, a, einst)
    return _pdf("reports/zusammenfassung.html", a=a, z=z, vergleich=vergleich, dia=diagramme,
                verteilung=diagramme.berechnen(db, a, einst, monate), cpo_verteilung=diagramme.cpo_verteilung(monate),
                msp_verteilung=diagramme.msp_verteilung(db, monate),
                vollstaendigkeit=vollstaendigkeit_pruefen(db, z, einst), stichtag=einst.stichtag,
                dc=_ladedauer_dc(db, z, einst.stichtag), tlp=einst.report_tlp,
                dc_oeffentlich=diagramme.dc_oeffentlich(db, z, einst.stichtag),
                heatmap=_heatmap(karte.berechnen(db, z, einst)))


def flottenbilanz(db: Session, z: Zeitraum) -> bytes:
    """Flottenbilanz (Marketing-Report), 16:9 quer, ohne Kopf- und Fußzeile: Seite 1 Kennzahlen und Heatmap des
    öffentlichen Ladens (keine Namen, Kennzeichen, Karten, Adressen oder Kostensummen – als Geldwert nur Ø €/kWh),
    Seite 2 die drei günstigsten Öffentlich-Lader:innen auf dem Siegertreppchen (mit Namen, ohne Kennzeichen)."""
    einst = app_einstellungen.laden(db)
    a = auswerten(db, z, einst)
    spalte = Ladevorgang.start if einst.stichtag == "beginn" else Ladevorgang.ende
    fahrzeuge = db.scalar(select(func.count(func.distinct(Ladevorgang.fahrzeug_id))).where(
        zeitbedingung(spalte, z), Ladevorgang.fahrzeug_id.is_not(None), Ladevorgang.kwh > 0)) or 0
    return _pdf("reports/flottenbilanz.html", a=a, z=z, g=a.gesamt, fahrzeuge=fahrzeuge,
                fahrer=len([f for f in a.fahrer if not f.summe.leer]),
                kwh_je_fahrzeug=(a.fahrzeuge_summe.kwh / fahrzeuge) if fahrzeuge else None,
                laender=len([c for c, s in a.laender if s.kwh > 0]), cpo_anzahl=len([c for c, _ in a.cpo if c]),
                topliste=a.topliste_oeffentlich, dc=_ladedauer_dc(db, z, einst.stichtag), stichtag=einst.stichtag,
                tlp="", heatmap=_heatmap(karte.berechnen(db, z, einst), 1300, 1230))  # rechte Hälfte der Titelseite


def zuordnungen(db: Session, stichtag) -> bytes:
    """Report „Zuordnungen am Stichtag“ (A4 quer): je Fahrer:in Fahrzeug und Karten je MSP, dazu Fahrzeuge ohne
    Fahrer:in und Karten ohne Zuordnung."""
    from app.services import stichtag_zuordnungen
    from app.services.auswertung import zeitraum_frei
    return _pdf("reports/zuordnungen.html", d=stichtag_zuordnungen.berechnen(db, stichtag),
                z=zeitraum_frei(stichtag, stichtag), tlp=app_einstellungen.laden(db).report_tlp)


def zuordnungen_dateiname(stichtag) -> str:
    return f"ladefalke-zuordnungen-{stichtag:%Y-%m-%d}.pdf"


def als_jpg(pdf: bytes, seite: int, breite_px: int = 3840) -> bytes:
    """Eine PDF-Seite (1 = erste) als JPG; bei 16:9 mit 3840 px Breite also 4K (3840 × 2160)."""
    import pypdfium2
    dokument = pypdfium2.PdfDocument(pdf)
    if not 1 <= seite <= len(dokument):
        raise HTTPException(404, "Seite nicht vorhanden")
    s = dokument[seite - 1]
    bild = s.render(scale=breite_px / s.get_width()).to_pil().convert("RGB")
    puffer = io.BytesIO()
    bild.save(puffer, "JPEG", quality=92, optimize=True)
    return puffer.getvalue()


def karten_cpo(db: Session, z: Zeitraum) -> bytes:
    """Report „Karten und Preise je CPO“: Sparpotenzial gegenüber dem günstigsten MSP je CPO und Stromart."""
    from app.services.auswertung import SPAR_MINDEST_KWH
    einst = app_einstellungen.laden(db)
    a = auswerten(db, z, einst)
    details = sorted(a.cpo_personen.values(), key=lambda d: (d.code is None, -d.summe.kosten))
    return _pdf("reports/karten_cpo.html", a=a, z=z, details=details, potenziale=a.sparpotenziale(),
                mindest=SPAR_MINDEST_KWH, stichtag=einst.stichtag, tlp=einst.report_tlp, MSP_TEXT="MSP")


def offene_aufgaben(db: Session) -> bytes:
    """Report „Offene Aufgaben“: Stand heute, unabhängig vom Zeitraum (wie die Seite „Aufgaben“)."""
    from app.config import heute
    from app.services import offen
    from app.services.auswertung import zeitraum_frei
    einst = app_einstellungen.laden(db)
    tag = heute()
    return _pdf("reports/aufgaben.html", z=zeitraum_frei(tag, tag), stand=tag, tlp=einst.report_tlp,
                stichtag=einst.stichtag, **offen.fuer_report(db))


@dataclass
class Grundlage:
    """Einmal berechnete Auswertungen für die Fahrer:innen-Reports eines Zeitraums (Zeitraum und drei Vorperioden),
    damit mehrere Reports (z. B. alle als ZIP) nicht je Person neu rechnen."""
    einst: object
    a: object
    vorher: list


def grundlage(db: Session, z: Zeitraum) -> Grundlage:
    einst = app_einstellungen.laden(db)
    return Grundlage(einst, auswerten(db, z, einst), [(vz, auswerten(db, vz, einst)) for vz in _vorperioden(z)])


def fahrer(db: Session, z: Zeitraum, fahrer_id: int, g: Grundlage | None = None) -> bytes:
    person = db.get(Fahrer, fahrer_id)
    if person is None:
        raise HTTPException(404, "Fahrer:in nicht gefunden")
    g = g or grundlage(db, z)
    einst, a = g.einst, g.a
    zeile = a.fahrer_zeile(fahrer_id) or FahrerZeile(person)
    flotte = a.topliste.p_flotte  # Ø €/kWh aller Fahrer:innen im Zeitraum (wie die Topliste)
    oeffentlich = a.topliste_oeffentlich
    vorher = []
    for vz, alt_a in g.vorher:
        alt = alt_a.fahrer_zeile(fahrer_id)
        vorher.append((vz, alt.summe if alt else FahrerZeile(person).summe))
    spalte = Ladevorgang.start if einst.stichtag == "beginn" else Ladevorgang.ende
    eigene_orte = karte.aus_bedingungen(db, [and_(Ladevorgang.ladeart == Ladeart.oeffentlich, zeitbedingung(spalte, z)),
                                             personen_filter([fahrer_id], art=einst.stichtag)])
    return _pdf("reports/fahrer.html", z=z, zeile=zeile, person=person, vergleich=vergleichen(zeile.summe, vorher),
                sortiert=lambda d, n=None: sorted(d.items(), key=lambda e: -e[1].kosten)[:n],
                stichtag=einst.stichtag, dia=diagramme, heatmap=_heatmap(eigene_orte),
                flottenschnitt=flotte, dc=_ladedauer_dc(db, z, einst.stichtag, fahrer_id), tlp=einst.report_tlp,
                dc_oeffentlich=diagramme.dc_oeffentlich(db, z, einst.stichtag,
                                                        personen_filter([fahrer_id], art=einst.stichtag)),
                gegen_flotte=(zeile.summe.preis / flotte - 1) if flotte and zeile.summe.preis is not None else None,
                rang=oeffentlich.rang(fahrer_id) if oeffentlich else None, topliste=oeffentlich,
                msp_verteilung=diagramme.aus_summen(list(zeile.msp.items())),
                cpo_verteilung=diagramme.aus_summen(list(zeile.cpo.items())))


def alle_fahrer_zip(db: Session, z: Zeitraum) -> tuple[bytes, int]:
    """Fahrer:innen-Reports aller Personen mit Ladungen im Zeitraum als ZIP; liefert (ZIP, Anzahl)."""
    g = grundlage(db, z)
    puffer = io.BytesIO()
    personen = sorted(g.a.fahrer, key=lambda zl: zl.fahrer.name.lower())
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as archiv:
        for zl in personen:
            archiv.writestr(dateiname(z, "report", zl.fahrer.name), fahrer(db, z, zl.fahrer.id, g))
    return puffer.getvalue(), len(personen)


def _kurz(text: str) -> str:
    """Für Dateinamen: ASCII, Kleinbuchstaben, Bindestriche."""
    text = unicodedata.normalize("NFKD", text.replace("ß", "ss")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "report"


def dateiname(z: Zeitraum, art: str, name: str | None = None) -> str:
    teile = ["ladefalke", art] + ([_kurz(name)] if name else []) + [_kurz(z.label)]
    return "-".join(teile) + ".pdf"


def antwort(inhalt: bytes, name: str, mime: str = "application/pdf") -> Response:
    """Datei als Download (Dateiname auch mit Umlauten korrekt)."""
    return Response(inhalt, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="{name}"; filename*=UTF-8\'\'{quote(name)}'})
