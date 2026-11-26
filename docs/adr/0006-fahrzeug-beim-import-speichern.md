# 0006 – Fahrzeug beim Import speichern statt bei jeder Abfrage auflösen

- **Status:** angenommen
- **Entschieden:** Projektbeginn (Phase 2)

## Kontext

Rechnungen kennen nur Ladekarten. Welches Fahrzeug geladen hat, ergibt sich über die zeitlich versionierten
Zuordnungen Karte → Fahrzeug bzw. Karte → Fahrer:in → Fahrzeug. Zuordnungen werden oft nachträglich korrigiert.

## Entscheidung

Das aufgelöste `fahrzeug_id` wird beim Import am Ladevorgang gespeichert, mit Herkunft `zuordnung_quelle`
(automatisch | manuell | keine). Spätere Änderungen an Zuordnungen wirken nur auf künftige Vorgänge – außer mit der
Option „bereits importierte Vorgänge im Zeitraum neu zuordnen“ (überall vorausgewählt). Manuell zugewiesene
Vorgänge werden bei Neuberechnung nie überschrieben.

## Verworfene Alternativen

- Fahrzeug bei jeder Abfrage aus den Zuordnungen berechnen – teure Joins in jeder Auswertung, und eine Korrektur der
  Stammdaten verändert unbemerkt abgeschlossene Monatsauswertungen.

## Folgen

Auswertungen sind einfach und stabil. Wer Zuordnungen ändert, muss sich für oder gegen die Neuzuordnung entscheiden;
abgeleitete Werte (geschätzte kWh, Kosten über den Vorsteuerabzug) werden dabei mit neu berechnet.
