"""Dashboard: Vollständigkeit, Kennzahlen, Verteilung auf Fahrer:innen (Standard: Vorauswahl aus Mein Konto, sonst Vormonat), Heatmap."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.auswertungen import navigation
from app.services import app_einstellungen, kennzahlen
from app.services.auswertung import auswerten, vergleichen, vormonat, zeitraum_aus_parametern
from app.services import diagramme, karte, zuordnung
from app.services.vollstaendigkeit import pruefen as vollstaendigkeit_pruefen
from app.web import art_vorgabe, render

router = APIRouter()


def _verteilungen(db, a, einst) -> dict:
    monate = diagramme.je_monat(db, a, einst)  # Auswertungen je Monat nur einmal berechnen
    return {"verteilung": diagramme.berechnen(db, a, einst, monate), "cpo_verteilung": diagramme.cpo_verteilung(monate),
            "msp_verteilung": diagramme.msp_verteilung(db, monate)}


@router.get("/")
def dashboard(request: Request, art: str = Depends(art_vorgabe), jahr: str = "", nr: str = "", von: str = "", bis: str = "",
              db: Session = Depends(get_db)):
    z = zeitraum_aus_parametern(art, jahr, nr, von, bis, standard=vormonat(), db=db)
    with zuordnung.vorgeladen(db):  # Zuordnungen einmal laden statt tausender Einzelabfragen (nur lesend)
        return _dashboard(request, db, z)


def _dashboard(request: Request, db: Session, z):
    einst = app_einstellungen.laden(db)
    a = auswerten(db, z, einst)
    # Vergleich mit dem Durchschnitt der drei Vorperioden (bei Monat: die drei Vormonate)
    vorher = [(vz, auswerten(db, vz, einst, mit_personen=False).gesamt) for vz in (z.verschoben(-i) for i in (1, 2, 3))
              if z.art != "alles"]  # bei „Alles“ gibt es keine Vorperioden
    vergleich = vergleichen(a.gesamt, vorher)
    dc = kennzahlen.ladedauer_dc(db, z, einst.stichtag, vorperioden=3)  # mit Vergleich wie die übrigen Kacheln
    return render(request, "dashboard.html", {"a": a, "vergleich": vergleich, "dc": dc,
                                              "vollstaendigkeit": vollstaendigkeit_pruefen(db, z, einst),
                                              **_verteilungen(db, a, einst), "dia": diagramme,
                                              "karte": karte.berechnen(db, z, einst),
                                              **navigation(db, a, "/")})
