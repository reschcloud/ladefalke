# 0010 – kWh-Schätzung aus dem Ladeprofil, Obergrenze 95. Perzentil

- **Status:** angenommen
- **Entschieden:** 04.11.2026

## Kontext

Zeittarife (z. B. Wien Energie) liefern keine kWh. Ohne kWh fehlen Ø €/kWh, Toplisten und CO₂e-Schätzung.

## Entscheidung

- Geschätzt = Standzeit × Ladeleistung (höchstens Nennleistung des Ladepunkts), begrenzt auf eine Obergrenze je
  Vorgang; markiert mit `kwh_geschaetzt = true` und „≈“.
- Leistung und Obergrenze stammen aus gemessenen Daten, getrennt nach AC und DC: Leistung = Σ kWh / Σ angesteckte
  Stunden, Obergrenze = **95. Perzentil** der kWh je Vorgang.
- Grundlage der Reihe nach: eigenes Fahrzeug (ab 5 Vorgängen der Stromart) → Flottenschnitt (ab 5) → Vorgaben aus den
  Einstellungen.
- Keine manuelle Eingabe. Ändern sich Daten oder Einstellungen, werden alle Schätzungen neu berechnet; Kosten bleiben.

## Verworfene Alternativen

- Akkugröße als Obergrenze – es wird praktisch nie von 0 auf 100 % geladen; überschätzt deutlich.
- Durchschnitt statt Perzentil als Obergrenze – schätzt lange Ladungen systematisch zu niedrig.
- Manuelle kWh-Eingabe – nicht nachvollziehbar, veraltet bei neuen Daten.

## Folgen

Schätzungen ändern sich, wenn neue Daten dazukommen; deshalb sind kWh nicht Teil der Dublettenerkennung (ADR 0009).
Vorgänge mit unplausibler Leistung und geschätzte Vorgänge fließen nicht ins Profil.
