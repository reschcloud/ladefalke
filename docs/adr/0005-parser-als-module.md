# 0005 – Parser als unabhängige Module, der Kern kennt keine Anbieter

- **Status:** angenommen
- **Entschieden:** Projektbeginn (vor 04.11.2026)

## Kontext

Jeder MSP liefert ein eigenes Format (CSV, PDF in mehreren Generationen). Formate ändern sich, neue MSP kommen dazu,
manche fallen weg. Anbieterwissen, das im Kern verstreut ist, macht jede Änderung riskant.

## Entscheidung

- Ein Modul je Format unter `app/parsers/` mit gemeinsamer Schnittstelle (`basis.Parser`), je ein pytest gegen
  anonymisierte Fixtures.
- Die Kern-App nennt keine konkreten Parser oder Anbieter – weder im Code noch in Texten; ein Test prüft das.
  Anbieterspezifisches ist eine Eigenschaft des Parsers (`liefert_evse`, `standardland`, `abrechnung_je_karte`, …).
- Parser werden beim Start automatisch gefunden. Jeder prüft eine Datei für sich; übernommen wird nur ein eindeutiges
  Ergebnis – passen mehrere oder keiner, wählt der Mensch. Es wird nicht geraten.

## Verworfene Alternativen

- Zentrale Erkennung mit Fallunterscheidung je Anbieter – jeder neue Anbieter ändert den Kern.
- Bestes Ergebnis automatisch nehmen, wenn mehrere passen – ein falsch erkanntes Format importiert stillschweigend
  falsche Daten.

## Folgen

Ein neuer MSP ist ein neues Modul plus Test, ohne Änderung am Kern. Ein deaktivierter oder fehlender Parser lässt
alle Daten des MSP unberührt.
