# CLAUDE.md

Projekt: Ladekosten-Auswertung (Spezifikation: siehe SPEC.md – maßgeblich).

## Regeln
- Sprache der Oberfläche, Kommentare und Commit-Messages: Deutsch.
- Alle Geldbeträge netto, als Decimal (nie float); kWh als Decimal mit 3 Nachkommastellen.
- Schemaänderungen nur über Alembic-Migrationen.
- Jeder Parser: eigenes Modul unter `app/parsers/`, gemeinsame Schnittstelle, pytest-Test gegen Datei in `samples/`.
- Dateien in `samples/` und `data/` niemals committen oder in Ausgaben/Fixtures kopieren (echte Personendaten); für Fixtures anonymisierte Auszüge erzeugen.
- Arbeite phasenweise nach SPEC.md Abschnitt 9; vor jeder Phase kurz den Plan zeigen und auf Freigabe warten.
- Bei Unklarheiten in SPEC.md nachfragen statt raten.

## Befehle
- Start: `docker compose up --build`
- Tests: `docker compose run --rm app pytest`
- Migration: `docker compose run --rm app alembic upgrade head`
