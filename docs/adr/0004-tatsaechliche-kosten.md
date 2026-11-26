# 0004 – Tatsächliche Kosten statt reiner Nettobeträge

- **Status:** angenommen
- **Entschieden:** 23.11.2026

## Kontext

Ursprünglich zeigte die App nur Nettobeträge. Für das Unternehmen kostet eine Ladung aber netto plus den Teil der
Umsatzsteuer, der nicht als Vorsteuer zurückkommt – ausländische USt. ganz, inländische je nach Fahrzeug
(Luxustangente) anteilig. Reine Nettowerte unterschätzen Auslandsladungen systematisch.

## Entscheidung

- Gespeichert werden je Posten Nettobetrag, USt.-Betrag, USt.-Satz und Land der USt. getrennt (maßgeblich ist die
  Rechnung, nicht der Ladeort).
- Angezeigt werden überall die **tatsächlichen Kosten** = netto + USt. × (1 − Abzug); Abzug = Vorsteuer-% des
  Fahrzeugs bei inländischer USt., sonst 0. Beschriftung „Kosten“, Exporte zusätzlich mit Netto und USt.
- Der Wert wird als Spalte `kosten` gespeichert und bei Import, Neuzuordnung und geänderten Steuereinstellungen neu
  berechnet.
- Ausländische USt. zählt voll (keine Erstattung über das EU-Verfahren angenommen). Reverse Charge wird wie
  inländische USt. behandelt (Land „RC“).
- Fehlt die USt. auf der Rechnung, wird sie aus der Steuersatz-Tabelle geschätzt und mit ≈ gekennzeichnet.

## Verworfene Alternativen

- Weiter nur netto – falsch für Auslandsladungen und Fahrzeuge mit eingeschränktem Vorsteuerabzug.
- Brutto anzeigen – überschätzt inländische Ladungen mit vollem Vorsteuerabzug.
- Kosten bei jeder Abfrage berechnen – Summen, Filter und Sortierung in der Datenbank wären nicht mehr einfach möglich.

## Folgen

Jeder Parser liefert USt. je Posten (`basis.ust_setzen`). Altbestand wurde einmalig aus den gespeicherten
Originalrechnungen nachgetragen. Die gespeicherte Spalte muss bei jeder Änderung der Einflussgrößen aktualisiert
werden.
