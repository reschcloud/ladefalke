# Änderungen

Versionen nach `HAUPT.NEBEN.FIX` (siehe README „Betrieb → Release erstellen“). Je Release: was neu ist und was beim
Update von Hand zu tun ist. Updates selbst: README „Betrieb → Update“.

## Unveröffentlicht

### Neu

- Neue Symbole (Material Design Icons, wie in Home Assistant): im Untermenü „Stammdaten“, farbig je Art – Fahrzeug
  blau, Person violett, Ladekarte türkis, MSP orange; Warnung, Erledigt, Frist, E-Mail, Schloss usw. als Symbol statt
  Zeichen, auch in den PDF-Reports.
- Detailseiten von Fahrzeug, Fahrer:in, Ladekarte und MSP tragen das Symbol ihrer Art im Titel; ihre Abschnitte
  (Fahrzeug, Fahrer:innen, Ladekarten, Zuordnungen, Letzte Ladevorgänge) haben Symbol und Linie in der passenden Farbe.

### Geändert

- Zuordnungen haben je eine zuständige Seite, auf der Überschneidungen sichtbar sind: Ladekarten werden nur bei der
  Karte zugeordnet, Fahrzeuge nur bei der Person (dort jetzt auch „Fahrerwechsel“). Auf der Fahrzeugseite öffnet
  „Fahrer:in zuordnen →“ die Person mit vorbelegtem Fahrzeug und kehrt danach zurück; „Ohne Fahrer:in“ hat dort ein
  eigenes Formular. Die übrigen Seiten zeigen „bei Person ändern →“ bzw. „bei Karte ändern →“, und die
  Bearbeitungsseite einer Zuordnung zeigt die übrigen Zeiträume derselben Person bzw. Karte.

### Beim Update beachten

- nichts

## v1.2.0 – 20.11.2026

### Neu

- Ladekarten je Fahrzeug: Zuordnungen „Karte → Fahrzeug“ nur innerhalb des Leasings (ein offenes Ende wird auf das
  Leasing-Ende begrenzt); bestehende Zuordnungen außerhalb werden zur Aufgabe. Zeiträume, in denen einem Fahrzeug
  weniger oder mehr Karten direkt zugeordnet sind als erlaubt (Einstellungen: an/aus, Mindest- und Höchstzahl, Standard
  genau 1), erscheinen beim Fahrzeug, unter „Aufgaben“ und im Report „Offene Aufgaben“.
- Fahrzeugseite: gemeinsame Zeitleiste mit Fahrer:innen und darunter den Ladekarten, Befunde der Kartenprüfung
  markiert.
- Warnung beim Fahrzeug, wenn Fahrer:innen, Vignette oder Streckenmaut FLEX außerhalb des Leasings liegen
  (offene Fahrer:in-Zuordnung erst nach dem Leasing-Ende; Vignette/Streckenmaut nur komplett außerhalb).
- Ladekarten: Die Liste zeigt bei Fahrzeugkarten neben dem Kennzeichen, wer das Fahrzeug heute fährt, und den letzten
  Ladevorgang je Karte (mit „vor x Tagen“); die Detailseite Kennzahlen (wie bei Fahrzeug und
  Fahrer:in) sowie ersten und letzten Ladevorgang mit Link.

### Behoben

- Fahrer:in: Der Knopf „Heute“ neben „Letzte Führerscheinkontrolle“ sitzt bündig neben dem Datumsfeld.
- Monatssäulen (Dashboard, Reports) über lange Zeiträume: Die Achse zeigt nur noch Quartale bzw. Jahre statt jedes
  Monats (die Texte überlagerten sich); der Tooltip trifft auch bei schmalen Säulen den richtigen Monat.
- README „Betrieb“ durchgehend auf das fertige Image ausgerichtet: Wiederherstellen, Update mit geänderter
  `compose.yaml`, PostgreSQL-Hauptversion nur gemeinsam mit einem Ladefalke-Release (das `pg_dump` im Image muss zur
  Datenbank passen).

### Beim Update beachten

- Die Prüfung der Kartenanzahl ist eingeschaltet (genau 1 Karte je Fahrzeug). Fahrzeuge ohne bzw. mit mehreren
  Fahrzeugkarten erscheinen danach unter „Aufgaben“ – Grenzen oder Abschalten unter Einstellungen → Fristen.

## v1.1.0 – 19.11.2026

### Neu

- Abhängigkeiten werden auf GitHub geprüft: Dependabot schlägt Updates vor (Python-Pakete, Basis-Image, PostgreSQL,
  Actions), der Workflow „Prüfung“ testet jeden Push und Pull Request und sucht wöchentlich mit `pip-audit` nach
  bekannten Sicherheitslücken.
- Lock-Dateien heißen `requirements-lock.txt` und `requirements-dev-lock.txt` (Dependabot erkennt nur `.txt`).
- Ältere Rechnungsformate werden erkannt: Salzburg AG (Layout bis etwa 2024 mit „Belegnummer“) und Elli bzw.
  „Easy Charging“ (Rechnung bis etwa 2023, mit Uhrzeit, EVSE-ID, AC/DC und eindeutiger ID je Ladevorgang;
  „Grundgebühr“ als Gebühr der Karte, auch Rechnungen nur mit Grundgebühr).
- Wien Energie: ältere Rechnungen (bis etwa 2023) mit der Vertragsnummer statt einer Kartennummer nach „Ladekarte:“.

### Geändert

- Lücken ohne Fahrer:in: Mit Leasing-Ende wird der ganze Leasingzeitraum geprüft, auch künftige Zeiträume (z. B. eine
  Zuordnung endet vor dem Leasing) – unter „Aufgaben“, auf der Fahrzeugseite und im Report „Offene Aufgaben“. Ohne
  Leasing-Ende wie bisher bis heute. Aktuelle Lücken nennen ihr Ende, falls bekannt.

### Beim Update beachten

- Nichts.

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
