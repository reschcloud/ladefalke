# 0011 – AC/DC ohne Rechnungsangabe über eine feste Vorrangkette

- **Status:** angenommen
- **Entschieden:** 25.11.2026

## Kontext

Viele Rechnungen nennen die Stromart nicht. Bis dahin galt: ohne Angabe bleibt sie leer oder kommt aus der Vorgabe
des MSP; ein Vorschlag nach Ø-Leistung wurde erst durch ausdrückliches Setzen unter „Aufgaben“ übernommen. Bei
Betreibern wie Ionity (nur DC) war das unnötige Handarbeit.

## Entscheidung

Vorrang: **Rechnung → manuell → CPO-Liste → Ø-Leistung → Vorgabe des MSP**.

- CPO-Liste: Betreiber, die nur AC oder nur DC anbieten (Tabelle `cpo_stromart`, nur Admin).
- Ø-Leistung: kWh / angesteckte Dauer über die Plausibilitätsgrenzen; nicht bei geschätzten kWh.
- Herkunft je Vorgang in `stromart_quelle`. Neu bestimmt werden beim Import, bei geänderter CPO-Liste oder
  Plausibilitätsgrenze und auf Knopfdruck – nur Vorgänge ohne Angabe oder mit Herkunft cpo/leistung/vorgabe.
- Eine verdrängte MSP-Vorgabe bleibt in den Rohdaten und gilt wieder, wenn keine Regel mehr greift.

## Verworfene Alternativen

- Vorschlag nach Leistung nur durch ausdrückliches Setzen (bisherige Regel) – gilt jetzt nur noch für Vorgänge, die
  keine Regel trifft.
- Vorschlag nach Preis – Tarife ändern sich, kein verlässliches Merkmal.

## Folgen

Deutlich weniger offene AC/DC-Aufgaben. Rechnungs- und manuelle Angaben werden nie überschrieben.
