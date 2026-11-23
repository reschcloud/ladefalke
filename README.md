# Ladefalke – Der elektrische Fuhrpark im Blick

Webanwendung zur Erfassung und Auswertung der Ladevorgänge der Firmenflotte.
Spezifikation: [SPEC.md](SPEC.md).

## Start

**Installation auf einem Server** mit dem fertigen Image: siehe [Betrieb](#betrieb). Aus dem Quelltext (Ausprobieren,
Entwicklung) im geklonten Repository:

```sh
cp .env.example .env      # Passwörter, SECRET_KEY und BACKUP_PASSWORT setzen
docker compose up --build
```

Für die **Entwicklung** in `.env`
zusätzlich `COMPOSE_FILE=compose.yaml:compose.dev.yaml` setzen – das Image enthält dann die Testwerkzeuge und die
Beispieldateien aus `samples/` werden eingebunden (`compose.dev.yaml`).

Die App läuft dann unter **https**://localhost:8000 (Port über `APP_PORT` in `.env`) – mit selbstsigniertem
Zertifikat, der Browser fragt beim ersten Aufruf einmal nach (siehe „HTTPS und Reverse Proxy“).
Beim Start werden die Datenbank-Migrationen automatisch ausgeführt.

Mit Podman: `podman compose up --build` (bzw. `podman-compose`). Bind-Mounts sind für
SELinux (Fedora) mit `:Z` gekennzeichnet.

## HTTPS und Reverse Proxy

**Ohne Proxy (Standard, `TLS=an`):** Die App spricht direkt HTTPS. Beim ersten Start erzeugt sie ein
selbstsigniertes Zertifikat (10 Jahre gültig) in `data/tls/` für `localhost`, `127.0.0.1` und die Namen/IP-Adressen
aus `TLS_NAMEN` (z. B. `TLS_NAMEN=ladekosten.firma.lan 192.168.1.20`). Es bleibt über Neustarts erhalten, damit die
Ausnahme im Browser gültig bleibt. Neu erzeugen: `data/tls/` leeren und die App neu starten. Ein eigenes Zertifikat:
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

Beim ersten Anmeldeversuch wird aus `ADMIN_USER`/`ADMIN_PASSWORD` in `.env` der erste Admin angelegt – danach zählt
nur die Benutzertabelle (Passwörter als scrypt-Hash). Weitere Benutzer:innen legt ein Admin unter „Benutzer:innen“ (Menü rechts)
an:

| Rolle | Darf |
|---|---|
| Admin | alles, auch Einstellungen ändern und Benutzerverwaltung (rote Leiste oben im Fenster) |
| Operator | alles außer Einstellungen und MSP ändern sowie Benutzerverwaltung |
| Viewer | nur lesen (inkl. Exporte und Originaldokumente) |

Passwort und Theme ändert jede:r unter „Mein Konto“. Löschen geht erst nach dem Deaktivieren (nie das eigene
Konto); der letzte aktive Admin lässt sich weder herabstufen noch deaktivieren.

## Unterstützte Rechnungsformate

Das Format wird beim Hochladen am Dateiinhalt erkannt (Parser unter `app/parsers/`, je Format ein Modul).

| MSP | Datei | Besonderheiten |
|---|---|---|
| Standort (eigene Wallboxen) | has.to.be CSV | keine Kosten in der Datei → kWh × Strompreis (Vorbelegung in den Einstellungen); Karte über `CardLabel`, Gäste über `GAST` + Referenz |
| Wien Energie | PDF | Zeittarife ohne kWh → kWh geschätzt (aus den Ladedaten je Fahrzeug bzw. Flotte); Brutto → netto exakt auf Rechnungssumme |
| DCS / BMW Charging | PDF | Teilrechnung je Land; Karte = Vertragsnummer; Uhrzeiten in UTC → Wiener Zeit; ausländische MwSt ignoriert |
| LINZ AG | PDF | Grundgebühr (Sammelposition) wird auf die Karten der Rechnung aufgeteilt |
| Salzburg AG | PDF | Transaktionsnummer als externe ID |
| EnBW mobility+ | PDF | eine Rechnung je Land und Monat; Transaktions-ID als externe ID |
| Elli / Cupra Charging („Easy Charging“) | PDF | Monatsübersicht (keine Rechnung); Text über PDFium; keine Uhrzeiten; Karte = Kontoinhaber:in; Netto je Land, abgestimmt auf die ausgewiesene MwSt |

Alle Beträge werden netto gespeichert. Tests laufen gegen anonymisierte Auszüge in `tests/fixtures/`
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

## Befehle

| Zweck | Befehl |
|---|---|
| Tests (nur mit `compose.dev.yaml`, siehe Start) | `docker compose run --rm --build app pytest` |
| Migration | `docker compose run --rm app alembic upgrade head` |
| Neue Migration | `docker compose run --rm -v ./alembic/versions:/app/alembic/versions:Z app alembic revision -m "…"` |

Die Tests legen im Datenbank-Container eine eigene Datenbank `<POSTGRES_DB>_test` an und
löschen sie danach wieder; die Produktivdaten bleiben unberührt.

## Geodaten (Karte, Verortung der Ladeorte)

Die App geht nie ins Internet. Für die Verortung der Ladeorte wird einmalig das freie Postleitzahlen-Verzeichnis
von GeoNames eingespielt (Lizenz CC-BY 4.0, © GeoNames, https://www.geonames.org/):

```
mkdir -p data/geo && cd data/geo
curl -LO https://download.geonames.org/export/zip/allCountries.zip && unzip -o allCountries.zip
cd ../.. && docker compose run --rm app python -m app.services.geokodierung
```

Eingespielt werden nur europäische Länder; danach werden alle Ladeorte neu verortet. `data/geo` wird nur lesend
in den Container eingebunden und nicht committet. Zum Aktualisieren die Schritte wiederholen.

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

Voraussetzung: Docker mit Compose-Plugin. Installiert wird immer ein **Release** (siehe [CHANGELOG.md](CHANGELOG.md))
als fertiges Image aus der GitHub Container Registry (`ghcr.io/reschcloud/ladefalke`); auf dem Server braucht es nur
`compose.yaml` und `.env`.

```sh
mkdir ladefalke && cd ladefalke
V=v1.5.0                            # gewünschtes Release
curl -fsSLO https://raw.githubusercontent.com/reschcloud/ladefalke/$V/compose.yaml
curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/$V/.env.example -o .env
nano .env                           # LADEFALKE_VERSION, Passwörter, SECRET_KEY, BACKUP_PASSWORT, ggf. TLS=aus
mkdir -p data/uploads data/backups data/tls data/geo
docker compose up -d                # lädt das Image und startet
docker compose ps                   # app „healthy“ = App antwortet und erreicht die Datenbank
```

**Selbst bauen statt laden** (z. B. eigene Änderungen, andere Prozessorarchitektur als amd64): Repository klonen,
Release auschecken (`git checkout v1.5.0`), `.env` aus `.env.example` anlegen und `docker compose up -d --build`.
Das selbst gebaute Image trägt den Namen des fertigen – Updates dann immer mit `--build`, nicht mit
`docker compose pull` (das würde es durch das fertige ersetzen).

Umzug bzw. Neuaufbau mit Daten: danach eine Sicherung einspielen (siehe [Backup und Umzug](#backup-und-umzug)) –
mit dem `BACKUP_PASSWORT` der Sicherung. Die Sicherung enthält auch die eingespielten Postleitzahlen; GeoNames muss
nicht neu geladen werden. Das TLS-Zertifikat entsteht neu (oder eigenes nach `data/tls/`). Sicherungen landen in
`data/backups/` auf demselben Server – zusätzlich auf ein anderes System kopieren.

### Update

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

Selbst gebaut: `git fetch --tags && git checkout v1.5.0 && docker compose up -d --build`. Der Footer zeigt danach die
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
   in dieser README die Beispielversion anpassen.
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
mitgeliefert, sondern bei Bedarf geladen (siehe „Geodaten“).

