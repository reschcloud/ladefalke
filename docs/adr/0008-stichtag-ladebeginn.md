# 0008 – Stichtag ist fest der Ladebeginn

- **Status:** angenommen
- **Entschieden:** 11/2026 (vorher wählbar, Standard Ladeende)

## Kontext

Ein Ladevorgang über Mitternacht oder Monatsende gehört entweder zum Tag des Beginns oder des Endes. Der Stichtag
bestimmt Zeitraum, Fahrzeug- und Personenauflösung, Filter und Sortierung. Anfangs war er einstellbar (Standard
Ladeende, weil MSP nach Ladeende abrechnen).

## Entscheidung

Stichtag ist überall und fest der **Ladebeginn**. War „Ladeende“ gespeichert, stellt die App beim Start einmal um und
ordnet alle automatisch zugeordneten Vorgänge neu zu (manuelle bleiben).

## Verworfene Alternativen

- Weiter wählbar – jede Auswertung, jeder Test und jede Erklärung musste beide Fälle abdecken; kaum Nutzen.
- Fest Ladeende – passt zu Rechnungen, aber nicht zur Frage „wann wurde geladen“ und „wer saß am Steuer“.

## Folgen

Monatssummen können leicht von Rechnungen abweichen, die nach Ladeende abrechnen. Der Hinweis „Summen sind
Näherungen“ (ADR 0004) deckt das mit ab.
