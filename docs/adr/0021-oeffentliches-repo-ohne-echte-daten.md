# 0021 – Öffentliches Repository ohne echte Daten, Historie neu begonnen

- **Status:** angenommen
- **Entschieden:** 18.11.2026

## Kontext

Das Repository wird öffentlich (AGPL-3.0-or-later). Frühere Stände enthielten echte Daten: Firmenname, echte
Ladungen in Fixtures, Rechnungsnummern.

## Entscheidung

- Die Git-Historie wurde vor der Veröffentlichung auf einen Commit reduziert; die alte Historie liegt nur außerhalb
  des Repos und wird nie gepusht.
- `samples/` und `data/` sind in `.gitignore` und werden nie committet oder in Ausgaben bzw. Fixtures kopiert.
- Fixtures enthalten nur erfundene Werte (Orte, PLZ, Zeiten, Nummern). Tests gegen echte Beispieldateien sprechen
  diese über einen SHA-256-Präfix an (`tests/beispiele.py`), nie über den Dateinamen.

## Verworfene Alternativen

- Historie mit `git filter-repo` bereinigen – fehleranfällig, ein übersehener Fund bleibt öffentlich.
- Repository privat lassen – widerspricht dem Ziel der Veröffentlichung.

## Folgen

Begründungen aus der Zeit vor dem 18.11.2026 sind nur noch in SPEC.md und diesen ADRs nachvollziehbar.
