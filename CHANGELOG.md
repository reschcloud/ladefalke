# Änderungen

Versionen nach `HAUPT.NEBEN.FIX` (siehe README „Betrieb → Release erstellen“). Je Release: was neu ist und was beim
Update von Hand zu tun ist. Updates selbst: README „Betrieb → Update“.

## Unveröffentlicht

Noch nichts.

## v1.0.1 – 18.11.2026

### Neu

- Fertige Images in der GitHub Container Registry (`ghcr.io/reschcloud/ladefalke`): Jeder Release-Tag startet eine
  Pipeline, die erst die Tests ausführt und dann das Image baut. Installation und Update laden das Image, statt es
  auf dem Server zu bauen (README „Betrieb“); selbst bauen geht weiterhin.
- Die Versionsanzeige kommt im Image aus dem Build (kein `.git` mehr nötig).

### Beim Update beachten

- Selbst gebaute Installationen laufen unverändert weiter (`git checkout v1.0.1 && docker compose up -d --build`).
- Umstieg auf das fertige Image: `compose.yaml` von v1.0.1 übernehmen, in `.env` `LADEFALKE_VERSION=1.0.1`
  eintragen, dann `docker compose pull && docker compose up -d`.
- Entwicklung: Das lokal gebaute Image heißt jetzt `ladefalke-app:dev` (`compose.dev.yaml`).

## v1.0.0 – 18.11.2026

Erstes Release.

### Enthalten

- Import der Rechnungen und Ladelisten der unterstützten Anbieter (README „Unterstützte Rechnungsformate“) mit
  Vorschau, Vollständigkeitsprüfung und Pauschalen
- Stammdaten: Fahrzeuge, Fahrer:innen, Ladekarten, MSP, Zuordnungen mit Zeitleiste; Detailseiten mit offenen
  Punkten und Blättern; Zeiträume „ohne Fahrer:in“ (bewusst niemandem zugeordnet, keine Lücke); aneinandergrenzende
  Zuordnungen werden zusammengefasst
- Datumsfelder in Safari (macOS) als Texteingabe TT.MM.JJJJ
- Ladevorgänge mit automatischer Zuordnung zu Fahrzeug und Person, Aufgaben für Offenes
- Auswertungen je Zeitraum, Fahrzeug, Fahrer:in und CPO, Toplisten, Karte, Histogramme zum öffentlichen DC-Laden;
  Export als CSV/XLSX
- PDF-Reports (Zusammenfassung, Fahrer:innen, Karten und Preise je CPO, Flottenbilanz, Zuordnungen, Offene Aufgaben)
  mit Versand per Mail und Zeitplan
- Benutzer:innen mit Rollen, Änderungsprotokoll, Syslog
- Sicherungen (verschlüsselt, Zeitplan, Wiederherstellen per Kommando), automatische Sicherung vor Migrationen
- HTTPS mit selbstsigniertem Zertifikat oder hinter einem Reverse Proxy; Healthcheck `/gesund`

### Beim Update beachten

- Erstinstallation: siehe README „Betrieb → Installation auf einem Server“.
- Bestehende Testinstallationen: `samples/` wird nicht mehr eingebunden und das Image enthält keine Testwerkzeuge
  mehr. Für die Entwicklung in `.env` `COMPOSE_FILE=compose.yaml:compose.dev.yaml` setzen (README „Start“).
