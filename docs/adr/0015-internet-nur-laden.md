# 0015 – Die App darf aus dem Internet laden, aber keine Daten senden

- **Status:** ersetzt durch [0022](0022-grundsatz-internetzugriff.md) (Grundsatz präzisiert, die Abrufe selbst bleiben)
- **Entschieden:** 24.11.2026 (ersetzt den Grundsatz „die App geht nie ins Internet“)

## Kontext

Bisher galt: keinerlei Internetzugriff. Folge: PLZ-Verzeichnis und Spritpreise mussten von Hand eingespielt werden,
und niemand merkte, wenn ein Update bereitstand.

## Entscheidung

Die App darf öffentliche Daten **herunterladen**, sendet aber **keine eigenen Daten** an Dritte:
- GeoNames-PLZ (beim ersten Start, nach Updates, alle 30 Tage),
- Spritpreise aus dem EU Weekly Oil Bulletin (wöchentlich, nur mit eingeschalteter Schätzung),
- Versions-Tags auf GitHub/ghcr.io (täglich, für den Update-Hinweis).

Jeder Abruf ist abschaltbar und von Hand auslösbar; ohne Internet gelten lokale Dateien bzw. der bisherige Stand.
Ausgehende Verbindungen, die Daten übertragen (Mail, Syslog), richtet nur der Admin selbst ein.

## Verworfene Alternativen

- Weiter streng offline – Handarbeit, veraltete Daten, Updates bleiben unbemerkt.
- Externe Geokodierung jetzt erlauben – würde Adressen senden; bleibt ausgeschlossen (ADR 0014).

## Folgen

Die App muss ohne Internet voll funktionsfähig bleiben; Fehlschläge werden angezeigt und später wiederholt.
