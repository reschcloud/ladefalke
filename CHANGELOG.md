# Änderungen

Versionen nach `HAUPT.NEBEN.FIX` (siehe README „Betrieb → Release erstellen“). Je Release: was neu ist und was beim
Update von Hand zu tun ist. Updates selbst: README „Betrieb → Update“.

## Unveröffentlicht

Wird zu **v1.0.0**, dem ersten Release.

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
