# 0007 – Zuordnungsperioden inklusiv, Überlappung per Datenbank verboten

- **Status:** angenommen
- **Entschieden:** Projektbeginn (Phase 1)

## Kontext

Ladekarten gehören zeitweise einer Person oder einem Fahrzeug, Personen fahren zeitweise ein Fahrzeug. Überlappende
Zeiträume machen die Auflösung (ADR 0006) mehrdeutig.

## Entscheidung

- `gueltig_ab` und `gueltig_bis` sind beide inklusive; `gueltig_bis = NULL` heißt offen.
- Überlappung ist je Karte und je Fahrer:in verboten – geprüft in der Validierung und zusätzlich als
  `EXCLUDE USING gist`-Constraint über `daterange(…, '[]')`.
- Ein Fahrzeug darf mehrere Fahrer:innen gleichzeitig haben (Poolfahrzeug).
- Eine neue offene Zuordnung beendet die offene Vorperiode automatisch zum Vortag.
- Jede Zuordnung hat genau eine zuständige Seite – die, auf der ihre Regel gilt (Karte bzw. Person).

## Verworfene Alternativen

- Halboffene Intervalle `[ab, bis)` – fachlich ungewohnt („gültig bis 31.03.“ meint den 31. mit).
- Überlappung nur in der Anwendung prüfen – Massenänderungen und Skripte könnten inkonsistente Daten erzeugen.
- Pool verbieten – entspricht nicht der Realität (geteilte Fahrzeuge).

## Folgen

Bei Poolfahrzeugen mit Fahrzeugkarte ist die Person beim Laden nicht eindeutig; solche Ladungen zählen bei
personenbezogenen Auswertungen und der Topliste nicht (ADR 0013).
