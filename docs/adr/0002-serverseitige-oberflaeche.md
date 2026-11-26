# 0002 – Serverseitig gerenderte Oberfläche statt Single-Page-App

- **Status:** angenommen
- **Entschieden:** Projektbeginn (vor 04.11.2026)

## Kontext

Kleine Nutzergruppe im lokalen Netz, eine Person entwickelt und betreibt. Die App soll mit `docker compose up` laufen
und ohne Build-Kette für das Frontend auskommen.

## Entscheidung

Python 3.12 mit FastAPI, SQLAlchemy 2 und Alembic; PostgreSQL 16 als eigener Container. Die Oberfläche wird mit
Jinja2 serverseitig gerendert, Interaktion über HTMX und wenig eigenes JavaScript. Pico.css, Leaflet und alle
übrigen Bibliotheken liegen lokal unter `static/` – kein CDN.

## Verworfene Alternativen

- SPA (React, Vue) mit JSON-API – zweite Sprache und Build-Kette, doppelte Validierung, für den Umfang überdimensioniert.
- SQLite – keine Ausschluss-Constraints für Zeiträume (siehe ADR 0007), kein `pg_dump`-Backup im laufenden Betrieb.
- Bibliotheken aus einem CDN – funktioniert nicht ohne Internet und verrät Aufrufe an Dritte (siehe ADR 0014).

## Folgen

Schemaänderungen nur über Alembic-Migrationen. Aufwendige Seiten (Dashboard über lange Zeiträume) brauchen
serverseitige Optimierung statt Client-Caching (Seiten-Cache, siehe SPEC.md Abschnitt 7 „Leistung“).
