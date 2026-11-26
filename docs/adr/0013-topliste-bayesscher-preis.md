# 0013 – Topliste nach Fahrer:innen mit kWh-gewichtetem (bayesschem) Preis

- **Status:** angenommen
- **Entschieden:** 05.11.2026 (vorher nach Fahrzeugen)

## Kontext

Die Topliste soll günstiges Laden anerkennen. Wer das Laden beeinflusst, ist die Person, nicht das Fahrzeug. Ein
einfacher Ø-Preis setzt Personen mit einer einzigen günstigen Ladung an die Spitze.

## Entscheidung

- Bewertet werden Fahrer:innen; jede Ladung zählt für die Person, die zum Ladezeitpunkt gefahren ist. Poolladungen
  ohne eindeutige Person zählen nicht. Nur Ladekosten, keine Gebühren.
- score = (Kosten + m × P_Schnitt) / (kWh + m); m standardmäßig der Median der kWh je Person, Mindestmenge 20 kWh.
- Keine Überschneidung zwischen „günstigste“ und „teuerste“.
- Zweite Topliste nur für öffentliches Laden.

## Verworfene Alternativen

- Topliste nach Fahrzeugen – Poolfahrzeuge und Fahrzeugwechsel verwischen das Verhalten der Person.
- Einfacher Ø-Preis – wenig ladende Personen landen zufällig oben oder unten.
- Nur Mindestmenge ohne Gewichtung – harte Schwelle, knapp darüber gilt wieder der Zufall.

## Folgen

Der angezeigte Score ist nicht der tatsächliche Ø-Preis; beide werden nebeneinander gezeigt.
