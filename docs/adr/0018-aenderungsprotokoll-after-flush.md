# 0018 – Änderungsprotokoll zentral beim Speichern (`after_flush`)

- **Status:** angenommen
- **Entschieden:** 11/2026 (Migration 0022; am 04.11.2026 als „kommt später“ vorgemerkt)

## Kontext

Es soll nachvollziehbar sein, wer was wann geändert hat. Protokollaufrufe in jeder Route vergisst man leicht.

## Entscheidung

Protokolliert wird zentral über das SQLAlchemy-Ereignis `after_flush` (`services/protokoll.py`, Tabelle `aenderung`):
Stammdaten und Einstellungen mit Feldern alt → neu, manuelle Korrekturen einzeln, Importe und Massenänderungen
(mehr als 10 gleichartige) als Sammeleintrag, abgeleitete Neuberechnungen gar nicht. Sensible Felder nur als
„geändert“. Ohne angemeldete Person steht „System“.

## Verworfene Alternativen

- Protokollaufruf je Route – lückenhaft.
- Datenbank-Trigger – kennen die handelnde Person und Anzeigetexte nicht.

## Folgen

Auch Skripte und der Taktgeber werden protokolliert. Massenänderungen müssen als solche erkennbar bleiben, sonst
flutet das Protokoll.
