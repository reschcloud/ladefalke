"""Seite „Protokoll“ (nur Admins): Änderungsprotokoll mit Filtern; Verlauf je Objekt kommt aus services/protokoll.py."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.formatierung import datum_lesen
from app.models import Benutzer
from app.services import protokoll
from app.web import render

router = APIRouter(prefix="/protokoll")
JE_SEITE = 50


@router.get("")
def anzeigen(request: Request, von: str = "", bis: str = "", benutzer: str = "", typ: str = "", q: str = "",
             seite: int = 1, db: Session = Depends(get_db)):
    try:
        von_d, bis_d = datum_lesen(von), datum_lesen(bis)
    except ValueError:
        von_d = bis_d = None
    eintraege, anzahl = protokoll.suchen(db, von_d, bis_d, benutzer, typ, q, seite, JE_SEITE)
    seiten = max(1, -(-anzahl // JE_SEITE))
    return render(request, "protokoll.html", {
        "eintraege": eintraege, "anzahl": anzahl, "seite": min(max(seite, 1), seiten), "seiten": seiten,
        "filter": {"von": von, "bis": bis, "benutzer": benutzer, "typ": typ, "q": q},
        "benutzer_liste": db.scalars(select(Benutzer).order_by(Benutzer.name)).all(),
        "typen": sorted(protokoll.TYP_NAMEN.items(), key=lambda e: e[1].lower())})
