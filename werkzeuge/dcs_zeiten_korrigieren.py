"""Einmalige Korrektur: DCS-Ladevorgänge aus Rechnungen im neueren Format (Spalte „Datum/Uhrzeit“ ohne „(UTC)“).

Bis zur Parser-Korrektur wurden auch diese Uhrzeiten als UTC gelesen und nach Wiener Zeit umgerechnet – die Vorgänge
liegen dadurch 1 Stunde (Sommer: 2 Stunden) zu spät. Das Werkzeug liest je abgeschlossenem DCS-Import die
Originaldatei, prüft den Spaltenkopf und setzt bei Rechnungen ohne „(UTC)“ Start und Ende auf die Uhrzeit aus der
Rechnung (Rohdaten „zeit_utc“ → „zeit_ortszeit“). Danach werden die betroffenen, automatisch zugeordneten Vorgänge neu
zugeordnet (der Tag kann sich geändert haben; manuelle Zuordnungen bleiben); geschätzte kWh werden beim Speichern neu
berechnet. Mehrfaches Ausführen ist unschädlich (bereits korrigierte Vorgänge haben „zeit_ortszeit“).

Aufruf (zuerst ohne Schalter: zeigt nur, was sich ändern würde):
    docker compose exec app python -m werkzeuge.dcs_zeiten_korrigieren
    docker compose exec app python -m werkzeuge.dcs_zeiten_korrigieren --ausfuehren
"""
import sys
from collections import Counter

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Import, ImportStatus, Ladevorgang, Msp
from app.parsers.basis import pdf_text
from app.parsers.dcs import _zeiten_in_utc, _zeitpunkt
from app.services.aufloesung import neu_zuordnen
from app.services.importe import datei


def korrigieren(db, ausfuehren: bool) -> list[int]:
    """Korrigiert (bzw. zählt nur) und liefert die IDs der verschobenen Ladevorgänge."""
    importe = db.scalars(select(Import).join(Msp, Import.msp_id == Msp.id).where(
        Msp.parser_key == "dcs", Import.status == ImportStatus.abgeschlossen).order_by(Import.id)).all()
    geaendert: list[int] = []
    verschiebungen: Counter = Counter()
    betroffene_importe = fehlend = 0
    for imp in importe:
        try:
            text = pdf_text(datei(imp))
        except Exception:
            fehlend += 1
            continue
        if _zeiten_in_utc(text):
            continue
        vorher = len(geaendert)
        for v in db.scalars(select(Ladevorgang).where(Ladevorgang.import_id == imp.id)):
            roh = (v.rohdaten or {}).get("zeit_utc")
            if not roh:
                continue
            tag, zeit = roh.split()
            richtig = _zeitpunkt(tag, zeit)
            versatz = richtig - v.start
            if versatz:
                verschiebungen[int(versatz.total_seconds() // 3600)] += 1
                v.start, v.ende = v.start + versatz, v.ende + versatz
                geaendert.append(v.id)
            daten = dict(v.rohdaten)
            daten["zeit_ortszeit"] = daten.pop("zeit_utc")
            v.rohdaten = daten
        betroffene_importe += len(geaendert) > vorher
    print(f"DCS-Importe: {len(importe)}, davon im Format ohne „(UTC)“ mit Änderungen: {betroffene_importe}"
          + (f", Originaldatei nicht lesbar: {fehlend}" if fehlend else ""))
    print(f"Ladevorgänge zu korrigieren: {len(geaendert)} – Verschiebung in Stunden: "
          + ", ".join(f"{h:+d} h: {n}" for h, n in sorted(verschiebungen.items())))
    if not ausfuehren:
        db.rollback()
        print("Nur Vorschau – nichts geändert. Zum Ausführen: --ausfuehren")
        return geaendert
    neu = neu_zuordnen(db, Ladevorgang.id.in_(geaendert)) if geaendert else 0
    db.commit()
    print(f"Korrigiert. Danach neu zugeordnet (Fahrzeug geändert): {neu}")
    return geaendert


if __name__ == "__main__":
    with SessionLocal() as sitzung:
        korrigieren(sitzung, "--ausfuehren" in sys.argv[1:])
