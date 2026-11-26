# 0009 – Dublettenerkennung ohne kWh

- **Status:** angenommen
- **Entschieden:** 04.11.2026

## Kontext

Dieselbe Rechnung kommt teils mehrfach (erneuter Upload, erst PDF, dann CSV derselben Rechnung). Nicht jeder MSP
liefert eine Transaktions-ID.

## Entscheidung

Drei Stufen:
1. Gleicher Datei-Hash → Datei abweisen.
2. Einzelne Vorgänge über `(msp, externe_id)`, ohne externe ID über `(msp, karte, start, ende)`.
3. Gleiche Rechnungsnummer desselben MSP aus anderer Datei → Warnung; Gebühren dieser Rechnung werden übersprungen,
   Ladevorgänge nur übernommen, soweit sie fehlen.

kWh gehen bewusst nicht in den Vergleich ein.

## Verworfene Alternativen

- kWh im Schlüssel – geschätzte kWh hängen vom Fahrzeug und vom Ladeprofil ab und ändern sich (ADR 0010); derselbe
  Vorgang würde dann als neu erkannt.

## Folgen

Zwei echte Ladungen mit identischer Karte, Beginn und Ende würden zusammenfallen – in der Praxis ausgeschlossen.
