# 0016 – Erster Admin ohne dauerhaftes Passwort in `.env`

- **Status:** angenommen
- **Entschieden:** 24.11.2026 (ersetzt „der `.env`-Admin dient zum Einrichten“ vom 04.11.2026)

## Kontext

Anfangs stand ein Admin-Passwort in `.env`, um die App einzurichten. Es blieb dort im Klartext liegen, auch wenn
längst Konten in der Datenbank existierten.

## Entscheidung

- `setup.sh` legt den ersten Admin nach dem Start mit Zufallspasswort an (`werkzeuge/admin.py --nur-wenn-leer`) und
  zeigt es einmal an.
- Ohne Konto und ohne `ADMIN_PASSWORD` führt `/login` zu „Ersten Admin anlegen“ (`/einrichten`, gegen gleichzeitige
  Versuche per Advisory-Lock), danach ist die Seite zu.
- `ADMIN_USER`/`ADMIN_PASSWORD` sind optional; stehen sie trotz vorhandener Konten noch in `.env`, warnt die App und
  `setup.sh` entfernt sie.
- Notfall: `python -m werkzeuge.admin [name]`.

## Verworfene Alternativen

- `.env`-Admin dauerhaft – Klartextpasswort auf der Platte, zweite Wahrheit neben der Datenbank.
- Fester Standardzugang (admin/admin) – unsicher, wird nie geändert.

## Folgen

Nach der Einrichtung zählt nur die Tabelle `benutzer`.
