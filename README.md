# Ladefalke – Der elektrische Fuhrpark im Blick

Webanwendung zur Erfassung und Auswertung der Ladevorgänge der Firmenflotte.
Spezifikation: [SPEC.md](SPEC.md).

## Installation

### Voraussetzungen

- Ein **Linux-Server oder eine VM** (z. B. Debian, Ubuntu, Fedora) mit **x86-64-Prozessor** (amd64); andere
  Architekturen gehen nur mit selbst gebautem Image (siehe [Betrieb](#betrieb)).
- **Docker Engine mit Compose-Plugin** – oder Podman mit Compose. Fehlt Docker, bietet `setup.sh` an, es über das
  offizielle Skript von get.docker.com zu installieren (braucht root bzw. sudo).
- **Normaler Benutzer genügt:** `setup.sh` prüft selbst, ob sudo nötig ist – zum Installieren oder Starten von
  Docker und, wenn der Benutzer nicht in der Gruppe `docker` ist, für Docker selbst – sagt vorher, wofür, und fragt
  das Passwort einmal ab. Gehört eine bestehende Installation root, bietet es an, sich mit sudo neu zu starten.
- Etwa **2 GB RAM** und **5 GB Speicher** (mehr, je nach Menge der Rechnungen und Sicherungen).
- **Internetzugang**: bei der Installation für das Image; im Betrieb holt die App Postleitzahlen (GeoNames), auf
  Wunsch Spritpreise und sieht nach neuen Versionen – gesendet werden dabei keine Daten (siehe
  [Geodaten](#geodaten-karte-verortung-der-ladeorte)). Ohne Internet geht es auch, mit ein paar Handgriffen.
- Erreichbarkeit im Netz über **Port 8000** (änderbar mit `APP_PORT`); für den Echtbetrieb am besten hinter einem
  Reverse Proxy mit eigenem Zertifikat (siehe [HTTPS und Reverse Proxy](#https-und-reverse-proxy)).

### Einrichten

Ein Befehl:

```sh
curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/main/setup.sh | sh
```

`setup.sh` installiert bei Bedarf Docker (nach Rückfrage), legt den Ordner `ladefalke` an (im leeren Ordner
installiert es direkt dort), ermittelt das neueste Release, holt dessen `compose.yaml` und `.env.example`, legt `.env`
mit **zufällig erzeugten** Passwörtern und Schlüsseln an (Datenbank, `SECRET_KEY`, `BACKUP_PASSWORT`), fragt nach
dem Namen des Servers für das HTTPS-Zertifikat, legt die Datenordner an, startet die App, wartet, bis sie bereit ist,
und legt dann den ersten Admin an. Am Ende zeigt es Adresse, Admin-Zugang und Backup-Passwort an – **das
Admin-Passwort wird nur dieses eine Mal angezeigt** (es steht nicht in `.env`), **das Backup-Passwort sicher
aufbewahren**, ohne lassen sich Sicherungen nicht wiederherstellen. Sich selbst legt es als `setup.sh` in den Ordner
(für Updates). Die Postleitzahlen für die Karte lädt die App danach selbst – sonst ist nichts nachzuladen.

Den Servernamen prüft es und lässt ihn bestätigen. Liegt noch eine Datenbank aus einem früheren Versuch ohne
zugehörige `.env` herum, fragt es, ob sie gelöscht werden soll (ihr Passwort wäre sonst unbekannt). Startet die App
nicht, bricht es das Warten gleich ab und zeigt den Grund.

Eine vorhandene `.env` überschreibt es nie; in `.env` steht die installierte Version (`LADEFALKE_VERSION`).
Bestimmtes Release: `LADEFALKE_VERSION=1.13.0 sh setup.sh`. Ohne Rückfragen: `sh setup.sh --ja` (Servername dann mit
`TLS_NAMEN=… sh setup.sh --ja`); nur einrichten, nicht starten: `--ohne-start`.

### Erster Aufruf

Die App läuft unter **https://\<server\>:8000** – mit selbst signiertem Zertifikat, der Browser fragt beim ersten
Aufruf einmal nach. Mit dem angezeigten Admin-Zugang anmelden (ohne `setup.sh`: erst „Ersten Admin anlegen“), das Passwort unter „Mein Konto“ ändern und unter
„Einstellungen“ das Nötige einrichten (z. B. E-Mail für Reports). Weitere Benutzer:innen legt ein Admin unter
„Benutzer:innen“ an. Den Zustand zeigt `docker compose ps` (app „healthy“ = läuft und erreicht die Datenbank).

**Update:** Gibt es ein neues Release, zeigt die App neben der Version „Update auf … verfügbar“ (Footer bzw.
Seitenleiste; Einzelheiten unter Einstellungen → System). Dann im Installationsordner:

```sh
sh setup.sh
```

Details, Sicherungen und Umzug: siehe [Betrieb](#betrieb) und [Backup und Umzug](#backup-und-umzug).

## HTTPS und Reverse Proxy

**Ohne Proxy (Standard, `TLS=an`):** Die App spricht direkt HTTPS. Beim ersten Start erzeugt sie ein
selbstsigniertes Zertifikat (10 Jahre gültig) in `data/tls/` für `localhost`, `127.0.0.1` und die Namen/IP-Adressen
aus `TLS_NAMEN` (z. B. `TLS_NAMEN=ladefalke.firma.lan 192.168.1.20`). Es bleibt über Neustarts erhalten, damit die
Ausnahme im Browser gültig bleibt. Neu erzeugen, z. B. nach einem Tippfehler beim Servernamen: in `.env` `TLS_NAMEN`
korrigieren, `data/tls/cert.pem` und `key.pem` löschen, dann `docker compose up -d`. Ungültige Angaben (z. B. mit
Umlaut) überspringt die App mit einer Warnung im Log. Ein eigenes Zertifikat:
`cert.pem` und `key.pem` (PEM, Schlüssel unverschlüsselt) nach `data/tls/` legen. Reines HTTP auf diesem Port wird
nicht beantwortet.

**Hinter einem Reverse Proxy (Echtbetrieb, `TLS=aus`):** Die App spricht HTTP auf Port 8000; der Proxy übernimmt
HTTPS und reicht `X-Forwarded-For`/`X-Forwarded-Proto` durch. `FORWARDED_ALLOW_IPS` auf die Adresse des Proxys
setzen (Standard `*`), den Port in `compose.yaml` am besten nur an den Proxy bzw. `127.0.0.1` binden. Beispiel nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 50m;  # Rechnungs-PDFs
}
```

Das Sitzungs-Cookie wird nur über HTTPS gesendet (`COOKIE_SECURE=true`); nur für einen reinen HTTP-Testbetrieb
ohne Proxy auf `false` setzen.

## Benutzer:innen und Rollen

Konten verwaltet die App selbst (Passwörter als scrypt-Hash); in `.env` steht kein Admin-Passwort. Weitere
Benutzer:innen legt ein Admin unter „Benutzer:innen“ (Menü rechts) an:

| Rolle | Darf |
|---|---|
| Admin | alles, auch Einstellungen ändern und Benutzerverwaltung (rote Leiste oben im Fenster) |
| Operator | alles außer Einstellungen und MSP ändern sowie Benutzerverwaltung |
| Viewer | nur lesen (inkl. Exporte und Originaldokumente) |

### Admin-Zugang

- **Erster Admin:** `setup.sh` legt ihn nach dem Start an und zeigt das Passwort einmal an. Ohne `setup.sh` (oder mit
  `--ohne-start`) zeigt die App beim ersten Aufruf statt der Anmeldung „Ersten Admin anlegen“ – wer die Seite zuerst
  abschickt, wird Admin, deshalb gleich nach der Installation erledigen. Die Seite ist zu, sobald es ein Konto gibt.
- **Notfall** (kein Admin mehr, Passwort vergessen) – auf dem Server im Installationsordner:

  ```sh
  docker compose exec app python -m werkzeuge.admin [benutzername]
  ```

  legt das Konto als aktiven Admin an bzw. macht ein vorhandenes Konto wieder zum aktiven Admin, mit neuem
  Zufallspasswort (wird angezeigt; ohne Angabe: „admin“). Im Protokoll steht „System“.
- **Frühere Installationen** hatten `ADMIN_USER`/`ADMIN_PASSWORD` in `.env` (nur für den allerersten Start
  gebraucht). `sh setup.sh` entfernt die Zeilen beim nächsten Update, sobald es Konten gibt; bis dahin weist
  Einstellungen → System darauf hin. Vorgeben lässt sich der erste Admin weiterhin so (danach wieder entfernen).

Passwort und Theme ändert jede:r unter „Mein Konto“. Löschen geht erst nach dem Deaktivieren (nie das eigene
Konto); der letzte aktive Admin lässt sich weder herabstufen noch deaktivieren.

## Unterstützte Rechnungsformate

Das Format wird beim Hochladen am Dateiinhalt erkannt (Parser unter `app/parsers/`, je Format ein Modul).

| MSP | Datei | Besonderheiten |
|---|---|---|
| Standort (eigene Wallboxen) | has.to.be CSV | keine Kosten in der Datei → kWh × Strompreis (Vorbelegung beim MSP, zuletzt verwendeter Preis); Karte über `CardLabel`, Gäste über `GAST` + Referenz |
| Wien Energie | PDF | Zeittarife ohne kWh → kWh geschätzt (aus den Ladedaten je Fahrzeug bzw. Flotte); Brutto → netto exakt auf Rechnungssumme |
| DCS / BMW Charging | PDF | Teilrechnung je Land; Karte = Vertragsnummer; Uhrzeiten in UTC → Wiener Zeit; USt. je Teilrechnung (ausländisch oder Reverse Charge) |
| LINZ AG | PDF | Grundgebühr (Sammelposition) wird auf die Karten der Rechnung aufgeteilt |
| Salzburg AG | PDF oder CSV | Transaktionsnummer als externe ID; CSV-Export („Stromladen_Beleg“) mit USt. je Vorgang, Grundgebühren dort nicht je Karte aufgeschlüsselt (→ Gebühr ohne Karte; besser das PDF) |
| EnBW mobility+ | PDF oder CSV | eine Rechnung je Land und Monat; Transaktions-ID als externe ID; USt. des Rechnungslands (CSV: Brutto − Netto je Position) |
| Shell Recharge | PDF | je Karte (eMA-ID) eine Tabelle; Betreiber aus der EVSE-ID; USt. laut Spezifikation |
| Elli / Cupra Charging („Easy Charging“) | PDF | Monatsübersicht (keine Rechnung); Text über PDFium; keine Uhrzeiten; Karte = Kontoinhaber:in; Netto je Land, abgestimmt auf die ausgewiesene MwSt |

Gespeichert werden je Posten Nettobetrag und USt. laut Rechnung; angezeigt werden tatsächliche Kosten (netto plus nicht
abziehbare USt., siehe Einstellungen → Kosten und Umsatzsteuer). Tests laufen gegen anonymisierte Auszüge in `tests/fixtures/`
und zusätzlich gegen echte Dateien in `samples/`, falls vorhanden.

### Parser hinzufügen oder entfernen

Der Kern der App kennt keine konkreten Anbieter – alles Anbieterspezifische steht im jeweiligen Parser.

**Hinzufügen:** ein Modul `app/parsers/<name>.py` mit einer Unterklasse von `basis.Parser` anlegen:

```python
class NeuerParser(Parser):
    key = "neuer_anbieter"           # eindeutig, wird am MSP gespeichert
    name = "Neuer Anbieter (PDF)"    # erscheint in Auswahl und Formatliste
    msp_name = "Neuer Anbieter"      # optional: Name des automatisch angelegten MSP
    standard_ladeart = "oeffentlich" # optional: Vorbelegung des MSP („privat“ für eigene Wallboxen)
    abrechnung_je_karte = False      # optional: Rechnung je Ladekarte (Vollständigkeitsprüfung)
    standardland = None              # optional: ISO-Land, wenn die Rechnung keines nennt
    liefert_evse = True              # optional: False, wenn der Ladepunkt keine EVSE-ID ist

    def erkennen(self, pfad): ...    # True, wenn die Datei zu diesem Format passt (am Inhalt)
    def parsen(self, pfad): ...      # liefert basis.Ergebnis (Rechnung, Vorgänge, Gebühren, Warnungen)
```

Dazu ein pytest-Test gegen eine anonymisierte Fixture in `tests/fixtures/`. Nach dem Neustart wird der Parser
automatisch gefunden und bekommt einen eigenen MSP – keine Migration, keine Änderung am Kern.

**Deaktivieren** (ohne etwas zu löschen): Stammdaten → MSP → „Parser aktiv“ ausschalten (nur Admin). Die App tut
dann, als gäbe es den Parser nicht; importierte Daten bleiben.

**Entfernen:** das Modul löschen und neu starten. Die MSP-Liste warnt dann „Parser fehlt“; die Daten bleiben
vollständig. Ist der Parser am MSP deaktiviert, lässt sich dort die Verknüpfung entfernen (der MSP bleibt).

## Entwicklung

Im geklonten Repository: `sh setup.sh --ohne-start` (legt `.env` mit Zufallswerten an), dann in `.env`
`COMPOSE_FILE=compose.yaml:compose.dev.yaml` setzen – das Image enthält dann die Testwerkzeuge und die
Beispieldateien aus `samples/` werden eingebunden – und

```sh
docker compose up --build
```

Beim Start laufen die Datenbank-Migrationen automatisch. Mit Podman: `podman compose up --build` (bzw.
`podman-compose`); Bind-Mounts sind für SELinux (Fedora) mit `:Z` gekennzeichnet.

## Befehle

| Zweck | Befehl |
|---|---|
| Tests (nur mit `compose.dev.yaml`, siehe Start) | `docker compose run --rm --build app pytest` |
| Migration | `docker compose run --rm app alembic upgrade head` |
| Neue Migration | `docker compose run --rm -v ./alembic/versions:/app/alembic/versions:Z app alembic revision -m "…"` |

Die Tests legen im Datenbank-Container eine eigene Datenbank `<POSTGRES_DB>_test` an und
löschen sie danach wieder; die Produktivdaten bleiben unberührt.

## Geodaten (Karte, Verortung der Ladeorte)

Was die App selbst aus dem Internet holt – es werden dabei keine Daten gesendet, alles abschaltbar:

- **Postleitzahlen** von GeoNames (Lizenz CC-BY 4.0, © GeoNames, https://www.geonames.org/) für die Verortung der
  Ladeorte: beim ersten Start, nach jedem Update und einmal im Monat (`allCountries.zip` von download.geonames.org,
  nur europäische Länder werden eingespielt), danach werden alle Ladeorte neu verortet. Stand und „Jetzt
  aktualisieren“ unter Einstellungen → System. Schlägt der Download fehl, bleibt der bisherige Stand; neuer Versuch
  am nächsten Tag.
- **Neue Versionen**: einmal täglich die Versions-Tags auf GitHub (Einstellungen → System).
- **Spritpreise** aus dem EU Weekly Oil Bulletin, wöchentlich, nur mit eingeschalteter Schätzung der gesparten
  Tankkosten (Einstellungen → Auswertungen → Gesparte Tankkosten; energy.ec.europa.eu).

Dazu, was man selbst einrichtet: Mail-Versand und Syslog.

**Ohne Internet:** `allCountries.zip` (https://download.geonames.org/export/zip/allCountries.zip) nach `data/geo/`
legen – geht der Download nicht, nimmt die App diese Datei (oder ein entpacktes `allCountries.txt`). Sofort
einspielen: Einstellungen → System → „Jetzt aktualisieren“ oder
`docker compose run --rm app python -m app.services.geokodierung`. `data/geo` wird nur lesend eingebunden und nie
committet. Spritpreise lassen sich als Datei hochladen.

Die Heatmap im Dashboard braucht keine Kartenkacheln: Leaflet und Leaflet.heat liegen unter `app/static/leaflet/`,
die Grenzen (Natural Earth, gemeinfrei) vereinfacht in `app/static/geo/basiskarte.js`. Neu erzeugen nur bei Bedarf,
aus `ne_50m_admin_0_countries.geojson`, `ne_10m_admin_1_states_provinces.geojson` und
`ne_10m_populated_places.geojson` (https://github.com/nvkelso/natural-earth-vector, Ordner `geojson`) sowie
`cities5000.txt` von GeoNames (https://download.geonames.org/export/dump/cities5000.zip, entpackt):

```
python3 werkzeuge/basiskarte.py data/geo/ne_50m_admin_0_countries.geojson \
  data/geo/ne_10m_admin_1_states_provinces.geojson data/geo/ne_10m_populated_places.geojson \
  data/geo/cities5000.txt app/static/geo/basiskarte.js
```

Straßen, Flüsse, Seen und das schattierte Relief (`app/static/geo/details.json`, `relief.jpg`) erzeugt
`werkzeuge/kartendetails.py` (braucht Pillow und numpy) aus den Natural-Earth-Dateien `ne_10m_roads`,
`ne_10m_rivers_lake_centerlines`, `ne_10m_rivers_europe`, `ne_10m_lakes`, `ne_10m_lakes_europe` (GeoJSON, wie oben)
und dem Raster https://naciscdn.org/naturalearth/10m/raster/SR_HR.zip (entpackt nach `sr_hr/`):

```
python3 werkzeuge/kartendetails.py data/geo/natural_earth app/static/geo
```

## Backup und Umzug

Einstellungen → **Backup** (Aktionen nur für Admins): „Jetzt sichern“, Liste mit Herunterladen und Löschen,
Zeitplan (aus / täglich / wöchentlich, Uhrzeit, wie viele automatische Sicherungen behalten werden; manuelle
bleiben, bis man sie löscht). Ablage: `data/backups/` (Volume).

Eine Sicherung (`ladefalke-backup-JJJJ-MM-TT_HHMMSS[-auto].tar.gz`) enthält die **ganze Datenbank** (Daten,
Einstellungen, Benutzer:innen mit Passwort-Hashes, eingespielte Postleitzahlen), die **Originaldateien** aus
`data/uploads/`, eine **Kopie der Parser** und ein Manifest (Datum, App-Version, Datenbankstand, Parser mit
Prüfsumme, Anzahlen). Nicht enthalten und für einen Umzug nicht nötig: `.env`, GeoNames-Rohdaten, TLS-Zertifikat.

**Verschlüsselung:** Ist `BACKUP_PASSWORT` in `.env` gesetzt, werden alle Sicherungen damit verschlüsselt
(AES-256-GCM, Endung `.enc`). Das Passwort sicher aufbewahren – ohne ist die Sicherung wertlos. Ohne Passwort sind
Sicherungen unverschlüsselt (Warnung in den Einstellungen); sie enthalten Personendaten. Achtung, falls `data/` in
einem Sync-Ordner liegt: Die Sicherungen werden dann mit synchronisiert.

Per Kommando:

```sh
docker compose exec app python -m werkzeuge.backup erstellen
docker compose exec app python -m werkzeuge.backup liste
```

**Wiederherstellen / Umziehen** (bewusst nur per Kommando – ersetzt Datenbank und Originaldateien):

1. Neue Installation einrichten (siehe [Installation auf einem Server](#installation-auf-einem-server)).
2. Sicherungsdatei nach `data/backups/` kopieren.
3. ```sh
   docker compose exec -it app python -m werkzeuge.backup wiederherstellen /data/backups/<datei> --ueberschreiben
   docker compose restart app
   ```

Das Kommando fragt bei verschlüsselten Sicherungen nach dem Passwort (oder nimmt `BACKUP_PASSWORT`), lehnt
Sicherungen einer neueren App-Version ab, hebt ältere per Migration an und meldet Parser, die in der Sicherung
vorkommen, hier aber fehlen oder einen anderen Stand haben. Deren Kopien legt es neben die Sicherung
(`<datei>_parser/`); übernommen werden sie nur von Hand (nach `app/parsers/` kopieren, Image neu bauen) – Code aus
einer Sicherung wird nie automatisch geladen. Ohne `--ueberschreiben` verweigert es das Einspielen in eine
Datenbank, die schon Ladevorgänge enthält. Danach gelten die Benutzer:innen aus der Sicherung.

## Betrieb

### Installation auf einem Server

Am einfachsten mit `setup.sh` (siehe [Installation](#installation)). Installiert wird immer ein **Release** (siehe
[CHANGELOG.md](CHANGELOG.md)) als fertiges Image aus der GitHub Container Registry (`ghcr.io/reschcloud/ladefalke`);
auf dem Server braucht es nur `compose.yaml` und `.env`. Von Hand ohne Skript:

```sh
mkdir ladefalke && cd ladefalke
V=v1.13.0                            # gewünschtes Release
curl -fsSLO https://raw.githubusercontent.com/reschcloud/ladefalke/$V/compose.yaml
curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/$V/.env.example -o .env
nano .env                           # jedes „bitte-aendern“ ersetzen, BACKUP_PASSWORT setzen, ggf. TLS=aus
mkdir -p data/uploads data/backups data/tls data/geo
docker compose up -d                # lädt das Image und startet
docker compose ps                   # app „healthy“ = App antwortet und erreicht die Datenbank
                                    # dann im Browser: „Ersten Admin anlegen“
```

**Selbst bauen statt laden** (z. B. eigene Änderungen, andere Prozessorarchitektur als amd64): Repository klonen,
Release auschecken (`git checkout v1.13.0`), `.env` aus `.env.example` anlegen und `docker compose up -d --build`.
Das selbst gebaute Image trägt den Namen des fertigen – Updates dann immer mit `--build`, nicht mit
`docker compose pull` (das würde es durch das fertige ersetzen).

Umzug bzw. Neuaufbau mit Daten: danach eine Sicherung einspielen (siehe [Backup und Umzug](#backup-und-umzug)) –
mit dem `BACKUP_PASSWORT` der Sicherung. Die Sicherung enthält auch die eingespielten Postleitzahlen. Das TLS-Zertifikat entsteht neu (oder eigenes nach `data/tls/`). Sicherungen landen in
`data/backups/` auf demselben Server – zusätzlich auf ein anderes System kopieren.

### Update

**Mit `setup.sh`** (empfohlen): `sh setup.sh` im Installationsordner bietet das neueste Release an, wenn es neuer
ist als das installierte (ohne Terminal, z. B. per Cron, nur mit `--ja`); gezielt: `sh setup.sh --update 1.8.0`. Das
Skript zeigt die Hinweise „Beim Update beachten“ aller übersprungenen Versionen, fragt nach, sichert die bisherige
`compose.yaml` (`compose.yaml.vor-<version>`), holt die neue, ergänzt neue Einträge aus `.env.example` in `.env`
(vorhandene Werte bleiben), setzt `LADEFALKE_VERSION`, startet mit dem neuen Image und wartet, bis die App bereit
ist; die Geodaten aktualisiert die App danach selbst. Ältere Versionen lehnt es ab
(siehe [Zurück auf ein älteres Release](#zurück-auf-ein-älteres-release)). Das Skript ist dasselbe wie bei der
Installation; fehlt es, im Installationsordner
`curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/main/setup.sh | sh`. Beim Update erneuert es sich
selbst aus dem neuen Release (ab v1.12.2). Wer noch eine ältere Fassung im Ordner hat, holt einmal das aktuelle Skript:
`curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/main/setup.sh -o setup.sh`.

Ob es ein neues Release gibt, sieht die App einmal täglich auf GitHub nach (Einstellungen → System, abschaltbar;
„Jetzt nachsehen“ sofort). Ein Release zählt erst, wenn sein Image fertig gebaut ist – direkt nach dem Taggen
laufen noch Tests und Build (einige Minuten).

**Von Hand:**

1. In [CHANGELOG.md](CHANGELOG.md) „Beim Update beachten“ des neuen Release lesen (z. B. ob `compose.yaml` oder
   `.env` neue Einträge brauchen). Eine geänderte `compose.yaml` holen mit
   `curl -fsSLO https://raw.githubusercontent.com/reschcloud/ladefalke/v1.3.0/compose.yaml`; neue Einträge für `.env`
   stehen in `.env.example` desselben Release.
2. In `.env` `LADEFALKE_VERSION` auf die neue Version setzen (z. B. `1.3.0`), dann:

```sh
docker compose pull
docker compose up -d
docker compose logs -f app          # Sicherung/Migration verfolgen, bis die App läuft
```

Selbst gebaut: `git fetch --tags && git checkout v1.13.0 && docker compose up -d --build`. Der Footer zeigt danach die
neue Version (z. B. „v1.3.0 · 20.01.2027“).

**Datenbank-Migrationen** laufen beim Start automatisch (`start.sh`). Stehen welche an, legt die App **vorher eine
Sicherung** an (`ladefalke-backup-…-update.tar.gz`, Art „vor Update“; wird nie automatisch gelöscht). Schlägt diese
Sicherung fehl, migriert und startet die App nicht (Meldung im Log) – abschaltbar mit `BACKUP_VOR_UPDATE=aus` in
`.env`. Startet die App nach einer fehlgeschlagenen Migration immer wieder neu, entsteht keine weitere Sicherung.
PostgreSQL führt Schemaänderungen in einer Transaktion aus: Schlägt eine Migration fehl, bleibt die Datenbank auf dem
alten Stand; die Ursache steht in `docker compose logs app`.

### Zurück auf ein älteres Release

Verlässlich ist nur: altes Release starten und die Sicherung von vor dem Update einspielen. In `.env`
`LADEFALKE_VERSION` auf die alte Version setzen (selbst gebaut: `git checkout v1.0.1 && docker compose build app`),
dann:

```sh
docker compose pull app
docker compose stop app             # die alte App startet ohnehin nicht, solange die Datenbank neuer ist
docker compose run --rm app python -m werkzeuge.backup liste
docker compose run --rm -it app python -m werkzeuge.backup wiederherstellen \
  /data/backups/<…-update.tar.gz.enc> --ueberschreiben
docker compose up -d
```

Alles, was nach dem Update erfasst wurde, ist danach weg – vorher als CSV exportieren oder nach dem Rückschritt neu
importieren.

### PostgreSQL-Hauptversion wechseln (z. B. 16 → 17)

Das App-Image enthält die PostgreSQL-Werkzeuge für Sicherungen in der Version der Datenbank (`pg_dump` 16 zu
`postgres:16`). Ein `pg_dump` kann keine neuere Server-Version sichern – die Datenbank daher **nie allein** auf eine
neue Hauptversion stellen. Der Wechsel kommt als Ladefalke-Release (Hauptversion, z. B. 2.0.0) mit Image und
`compose.yaml` für die neue PostgreSQL-Version; die CHANGELOG beschreibt ihn. Ablauf dann, weil sich das
Datenverzeichnis einer Hauptversion nicht mit der nächsten öffnen lässt:

1. Mit der bisherigen Version eine Sicherung erstellen
   (`docker compose exec app python -m werkzeuge.backup erstellen`) und auf ein anderes System kopieren.
2. `compose.yaml` des neuen Release holen (neues PostgreSQL-Image, neuer Volume-Name, z. B. `pgdata17`) und in `.env`
   `LADEFALKE_VERSION` setzen. Das alte Volume `ladefalke_pgdata` bleibt unberührt – das ist der Rückweg.
3. `docker compose pull && docker compose up -d` – startet mit leerer Datenbank.
4. Sicherung einspielen (siehe [Backup und Umzug](#backup-und-umzug)). Läuft alles, kann das alte Volume später mit
   `docker volume rm ladefalke_pgdata` weg.

Selbst gebaut gilt dasselbe: `postgres:` in `compose.yaml` und `postgresql-client-…` im `Dockerfile` immer gemeinsam
ändern.

### Release erstellen (Entwicklung)

Versionen nach `HAUPT.NEBEN.FIX`: FIX = nur Fehlerbehebungen, NEBEN = neue Funktionen (Update ohne Zutun),
HAUPT = beim Update ist etwas von Hand zu tun (z. B. neue Pflichtangabe in `.env`). Migrationen allein machen noch
keine Hauptversion.

1. Tests grün (`docker compose run --rm --build app pytest`).
2. In [CHANGELOG.md](CHANGELOG.md) den Abschnitt „Unveröffentlicht“ zur neuen Version mit Datum machen; unter
   „Beim Update beachten“ alles, was von Hand zu tun ist (oder „nichts“). In `.env.example` `LADEFALKE_VERSION` und
   in dieser README die Beispielversion anpassen, ebenso die Rückfall-Version in `setup.sh` (gilt nur ohne Zugriff
   auf GitHub).
3. Commit, dann **einfacher** Tag (ohne `-a`, damit die Versionsanzeige ihn auch beim Selbstbauen erkennt):

   ```sh
   git tag v1.3.0 && git push origin main v1.3.0
   ```

4. Der Tag startet die Pipeline [.github/workflows/release.yml](.github/workflows/release.yml) (GitHub → Actions):
   erst die Tests, dann das Image `ghcr.io/reschcloud/ladefalke` mit den Tags `1.3.0`, `1.3`, `1` und `latest`.
   Schlagen die Tests fehl, entsteht kein Image. Beim ersten Mal unter GitHub → Packages → ladefalke →
   Package settings die Sichtbarkeit auf „Public“ stellen, falls das Image nicht öffentlich ist.

### Abhängigkeiten aktualisieren

Das Image installiert feste Versionen aus `requirements-lock.txt` (Entwicklung: `requirements-dev-lock.txt`); so baut jeder
Server dieselben Bibliotheken wie getestet. Direkte Abhängigkeiten stehen in `requirements.txt` bzw.
`requirements-dev.txt`. Aktualisieren (z. B. für Sicherheitsupdates):

```sh
sh werkzeuge/abhaengigkeiten_sperren.sh   # löst neu auf und schreibt beide Lock-Dateien
docker compose run --rm --build app pytest
```

Danach die Lock-Dateien committen – mit dem nächsten Release erreichen sie die Server.

**Automatische Prüfungen auf GitHub:**
- **Dependabot** ([.github/dependabot.yml](.github/dependabot.yml)) schlägt wöchentlich Updates als Pull Request vor:
  Python-Pakete (kleinere Updates gesammelt in einem PR), Basis-Image, PostgreSQL in `compose.yaml` (ohne
  Hauptversionen – Wechsel nur über die Sicherung, siehe oben) und monatlich die GitHub Actions.
- **Prüfung** ([.github/workflows/pruefung.yml](.github/workflows/pruefung.yml)) führt bei jedem Push auf `main` und
  jedem Pull Request die Tests aus und prüft die festen Versionen mit `pip-audit` auf bekannte Sicherheitslücken –
  diese Prüfung zusätzlich jeden Montag, auch ohne Änderungen. Ein Dependabot-PR mit grüner Prüfung kann
  übernommen werden; es wird erst mit dem nächsten Release ausgeliefert.

## Datenschutz

`samples/`, `data/` und `.env` enthalten echte Personendaten bzw. Zugangsdaten und werden
nie committet (siehe `.gitignore`).

## Lizenz

Ladefalke ist freie Software unter der **GNU Affero General Public License, Version 3 oder später**
(AGPL-3.0-or-later, siehe [LICENSE](LICENSE)). Du darfst sie nutzen, verändern und weitergeben; veränderte Versionen
müssen wieder unter der AGPL und mit Quelltext weitergegeben werden. Ohne jede Gewährleistung.

**Betrieb einer veränderten Version:** Die AGPL gilt auch, wenn die App nur über das Netz genutzt wird – wer eine
veränderte Version für andere betreibt, muss deren Nutzer:innen den Quelltext dieser Version anbieten. Dafür steht
im Footer der Link „Quelltext“; in `.env` mit `QUELLTEXT_URL` auf den eigenen Quelltext zeigen lassen. Für den
unveränderten Einsatz (auch intern in einer Firma) ist nichts zu tun.

Mitgelieferte Fremdbestandteile behalten ihre Lizenzen: Leaflet und Leaflet.heat (BSD-2-Clause), htmx (Zero-Clause
BSD), Pico CSS (MIT), Material Design Icons von Pictogrammers (Apache 2.0, ausgewählte Icons in `app/icons_mdi.py`,
Lizenztexte unter `app/static/mdi/`), Natural-Earth-Daten (gemeinfrei); die GeoNames-Postleitzahlen (CC-BY 4.0) werden nicht
mitgeliefert, sondern von der App geladen (siehe „Geodaten“).

