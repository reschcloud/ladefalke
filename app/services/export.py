"""Tabellen-Export als CSV (`;`, Dezimalkomma, UTF-8 mit BOM für Excel) und XLSX (echte Zahlenformate)."""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.formatierung import datum, datumzeit, zahl

# Spaltentypen: text, ganz, kwh (3 Stellen), geld (2), preis (2 Stellen €/kWh), prozent (Anteil 0–1), datum, datumzeit
STELLEN = {"kwh": 3, "geld": 2, "preis": 2, "prozent": 1}
XLSX_FORMATE = {"ganz": "0", "kwh": "#,##0.000", "geld": "#,##0.00", "preis": "0.00", "prozent": "0.0%",
                "datum": "DD.MM.YYYY", "datumzeit": "DD.MM.YYYY HH:MM"}


@dataclass
class Spalte:
    titel: str
    typ: str = "text"


@dataclass
class Tabelle:
    name: str  # Dateiname ohne Endung, auch Blattname
    spalten: list[Spalte]
    zeilen: list[list]


def _csv_wert(wert, typ: str) -> str:
    if wert is None:
        return ""
    if typ == "prozent":
        return zahl(Decimal(wert) * 100, STELLEN["prozent"])
    if typ in STELLEN:
        return zahl(wert, STELLEN[typ])
    if typ == "datumzeit" and isinstance(wert, datetime):
        return datumzeit(wert)
    if typ == "datum" and isinstance(wert, date):
        return datum(wert)
    return str(wert)


def csv_inhalt(t: Tabelle) -> bytes:
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    w.writerow([s.titel for s in t.spalten])
    for z in t.zeilen:
        w.writerow([_csv_wert(wert, s.typ) for wert, s in zip(z, t.spalten)])
    return puffer.getvalue().encode("utf-8-sig")


def xlsx_inhalt(t: Tabelle) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = t.name[:31]
    ws.append([s.titel for s in t.spalten])
    for zelle in ws[1]:
        zelle.font = Font(bold=True)
    for z in t.zeilen:
        ws.append(list(z))  # Decimal bleibt Decimal (openpyxl schreibt es als Zahl)
    for i, s in enumerate(t.spalten, start=1):
        if fmt := XLSX_FORMATE.get(s.typ):
            for (zelle,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                zelle.number_format = fmt
        breite = max([len(s.titel)] + [len(_csv_wert(z[i - 1], s.typ)) for z in t.zeilen[:200]])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(breite + 2, 8), 60)
    ws.freeze_panes = "A2"
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


MEDIENTYPEN = {"csv": "text/csv; charset=utf-8",
               "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def inhalt(t: Tabelle, format_: str) -> bytes:
    return csv_inhalt(t) if format_ == "csv" else xlsx_inhalt(t)
