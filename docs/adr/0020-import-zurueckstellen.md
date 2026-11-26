# 0020 – Import-Vorschauen zurückstellen statt verwerfen

- **Status:** angenommen
- **Entschieden:** 25.11.2026 (Migration 0038)

## Kontext

Manche Dateien lassen sich (noch) nicht richtig importieren – unbekanntes Format, Papierrechnung, die besser als
Pauschale erfasst wird. Verwerfen löscht die Datei und das Wissen, dass noch etwas zu tun ist; offen lassen blockiert
den Import-Durchgang.

## Entscheidung

Eine Vorschau kann mit optionaler Notiz zurückgestellt werden (Status `zurueckgestellt`). Es wird nichts übernommen,
die Datei bleibt; die Import-Seite listet sie mit „Wieder prüfen“ und „Erledigt“. Erneut hochgeladen gilt sie als
bekannt. Kein Eintrag unter „Aufgaben“, kein roter Punkt.

## Verworfene Alternativen

- Nur verwerfen – Original und Notiz gehen verloren.
- Als Aufgabe mit rotem Punkt – dauerhaft rot für Dinge, die bewusst später erledigt werden.

## Folgen

Zurückgestellte Dateien können liegen bleiben; die Import-Seite zeigt sie sichtbar an.
