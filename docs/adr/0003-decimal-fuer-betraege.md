# 0003 – Geldbeträge und kWh als Decimal, nie float

- **Status:** angenommen
- **Entschieden:** Projektbeginn (vor 04.11.2026)

## Kontext

Rechnungssummen müssen centgenau aufgehen; Abweichungen von einem Cent lösen Plausibilitätswarnungen aus und
untergraben das Vertrauen in die Auswertung.

## Entscheidung

Alle Geldbeträge sind `Decimal` (Python) bzw. `NUMERIC` (Datenbank), kWh `Decimal` mit 3 Nachkommastellen.
Einzelbeträge, die aus einer Summe verteilt werden (Netto aus Brutto, USt., Grundgebühren auf Karten), werden
centgenau so gerundet, dass ihre Summe exakt der Rechnungssumme entspricht.

## Verworfene Alternativen

- `float` – Rundungsfehler summieren sich über hunderte Positionen.
- Ganzzahlige Cent – Tarife und Strompreise haben mehr als zwei Nachkommastellen (z. B. 4 beim Strompreis).

## Folgen

Parser müssen Zahlen als Text in `Decimal` umwandeln, nie über `float`. Anzeige: €/kWh mit 2, eingegebene
Strompreise mit 4, einzelne kWh mit 3 Nachkommastellen, Summen ganzzahlig.
