#!/bin/sh
# Ladefalke – Installation und Update auf einem Server (oder lokal zum Ausprobieren), ein Befehl für alles:
#
#   curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/main/setup.sh | sh
#
# Installation: fehlt Docker, bietet das Skript die Installation an (offizielles Skript get.docker.com). Es legt den
# Ordner „ladefalke“ an (außer im leeren Ordner bzw. dort, wo schon compose.yaml oder .env liegt), ermittelt das
# neueste Release (oder LADEFALKE_VERSION=x.y.z vorgeben), holt dessen compose.yaml und .env.example, legt .env mit
# zufällig erzeugten Geheimnissen an (Datenbank-Passwort, Admin-Passwort, SECRET_KEY, BACKUP_PASSWORT), legt die
# Datenordner an, speichert sich selbst als setup.sh daneben, startet und wartet, bis die App bereit ist. Geodaten
# (Postleitzahlen) lädt die App danach selbst. Eine vorhandene .env wird nie überschrieben – darin werden nur noch
# offene Platzhalter („bitte-aendern“) ersetzt. Ohne Rückfragen: --ja (Servername dann über TLS_NAMEN=…);
# nur einrichten, nicht starten: --ohne-start.
#
# Update: im Installationsordner dasselbe Skript noch einmal –  sh setup.sh  – gibt es ein neueres Release, bietet es
# das Update an; gezielt auf eine Version:  sh setup.sh --update x.y.z
# Zeigt die Hinweise „Beim Update beachten“ aller übersprungenen Versionen, sichert compose.yaml, holt die neue,
# ergänzt neue Einträge aus .env.example in .env (vorhandene Werte bleiben), setzt LADEFALKE_VERSION und startet neu.
# Vor anstehenden Datenbank-Migrationen legt die App selbst eine Sicherung an.
set -eu

RUECKFALL="1.12.0"  # nur ohne Zugriff auf die GitHub-API; bei jedem Release mitziehen (README „Release erstellen“)
REPO="reschcloud/ladefalke"
ROH="https://raw.githubusercontent.com/$REPO"
ARGUMENTE="$*"  # für einen Neustart mit sudo (Optionen enthalten keine Leerzeichen)
JA=""
OHNE_START=""
UPDATE=""
UPDATE_ZIEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ja) JA=1 ;;
    --ohne-start) OHNE_START=1 ;;
    --update) UPDATE=1
      case "${2:-}" in [0-9]*.[0-9]*.[0-9]*) UPDATE_ZIEL="${2#v}"; shift ;; esac ;;
    *) printf 'Unbekannte Option: %s (erlaubt: --ja, --ohne-start, --update [x.y.z])\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

# Rückfragen über das Terminal – auch wenn das Skript per „curl … | sh“ kommt (stdin ist dann die Pipe)
TTY=""
if [ -z "$JA" ] && (exec </dev/tty) 2>/dev/null; then TTY=1; fi
# eigener Pfad (beim Aufruf als Datei), um sich in den Installationsordner zu kopieren
SKRIPT=""
case "$0" in *setup.sh) [ -f "$0" ] && SKRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")" ;; esac

fehler() { printf '\n✗ %s\n' "$1" >&2; exit 1; }
hinweis() { printf '› %s\n' "$1"; }
frage() {  # frage "Text" "Vorgabe" → Antwort (ohne Terminal oder mit --ja: Vorgabe)
  if [ -z "$TTY" ]; then printf '%s' "$2"; return; fi
  printf '%s ' "$1" >/dev/tty; read -r antwort </dev/tty || antwort=""
  printf '%s' "${antwort:-$2}"
}

# ---------------------------------------------------------------- Voraussetzungen: Docker (oder Podman) mit Compose
compose_finden() {  # COMPOSE: Compose-Befehl, ENGINE: docker bzw. podman (für Volumes und Container)
  if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"; ENGINE="docker"
  elif podman compose version >/dev/null 2>&1; then COMPOSE="podman compose"; ENGINE="podman"
  elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"; ENGINE="docker"
  else return 1
  fi
}
# ---------------------------------------------------------------- Rechte: wofür sudo nötig ist
# Ohne root nur dort sudo, wo es gebraucht wird: Docker installieren bzw. starten, Zugriff auf Docker ohne Gruppe
# „docker“. Vorher sagen, wofür, und das Passwort einmal am Stück abfragen (nicht mitten in einer Ausgabe).
ALS_ROOT=""
sudo_bereit() {  # sudo_bereit "Zweck"
  [ "$(id -u)" -eq 0 ] && return 0
  command -v sudo >/dev/null 2>&1 || fehler "Für $1 werden root-Rechte gebraucht, sudo fehlt – bitte als root ausführen."
  if ! sudo -n true 2>/dev/null; then
    [ -n "$TTY" ] || fehler "Für $1 wird sudo gebraucht – ohne Terminal geht die Passwortabfrage nicht (als root ausführen)."
    hinweis "Für $1 wird sudo gebraucht – bitte das Passwort von $(id -un) eingeben."
    sudo -v </dev/tty || fehler "sudo nicht möglich – bitte als root ausführen."
  fi
  ALS_ROOT="sudo"
}
neu_mit_sudo() {  # neu_mit_sudo "Grund" – als Datei aufgerufen: anbieten, das Skript mit sudo neu zu starten
  if [ "$(id -u)" -ne 0 ] && [ -n "$SKRIPT" ] && [ -n "$TTY" ] && command -v sudo >/dev/null 2>&1; then
    case "$(frage "$1 – mit sudo neu starten? [J/n]" "j")" in
      [nN]*) ;;
      *) sudo -v </dev/tty || fehler "sudo nicht möglich."
         cd "$START_ORDNER"
         # shellcheck disable=SC2086 – Optionen bewusst einzeln
         exec sudo sh "$SKRIPT" $ARGUMENTE ;;
    esac
  fi
  fehler "$1 – bitte mit root-Rechten ausführen: sudo sh setup.sh${ARGUMENTE:+ $ARGUMENTE}"
}
START_ORDNER=$(pwd)

if ! compose_finden; then
  [ "$(uname -s)" = "Linux" ] || fehler "Docker mit Compose (oder Podman mit Compose) wird gebraucht – siehe README „Voraussetzungen“."
  command -v curl >/dev/null 2>&1 || fehler "Docker fehlt, und für die Installation wird curl gebraucht."
  case "$(frage "Docker ist nicht installiert. Jetzt mit dem offiziellen Skript von get.docker.com installieren? [J/n]" "j")" in
    [nN]*) fehler "Ohne Docker geht es nicht – Installation siehe https://docs.docker.com/engine/install/" ;;
  esac
  sudo_bereit "die Installation von Docker"
  hinweis "Installiere Docker (get.docker.com) …"
  installer=$(mktemp)
  curl -fsSL https://get.docker.com -o "$installer" || fehler "Konnte das Docker-Installationsskript nicht laden."
  $ALS_ROOT sh "$installer" || { rm -f "$installer"; fehler "Docker-Installation fehlgeschlagen (Ausgabe oben)."; }
  rm -f "$installer"
  $ALS_ROOT systemctl enable --now docker >/dev/null 2>&1 || true
  compose_finden || fehler "Docker ist installiert, aber „docker compose“ fehlt noch – bitte die Ausgabe oben prüfen."
fi

# Zugriff auf den Docker-Dienst: läuft er nicht → starten (sudo); keine Berechtigung (nicht in der Gruppe „docker“)
# → Compose über sudo. Podman läuft ohne Dienst und ohne sudo.
RECHTE="ohne sudo"
[ "$(id -u)" -eq 0 ] && RECHTE="als root"
if [ "$COMPOSE" != "podman compose" ]; then
  if ! meldung=$(docker info 2>&1 >/dev/null); then
    case "$meldung" in
      *ermission*denied*) ;;  # läuft, aber keine Berechtigung: unten
      *)  # Dienst nicht erreichbar: starten
        command -v systemctl >/dev/null 2>&1 || fehler "Der Docker-Dienst läuft nicht: $meldung"
        sudo_bereit "das Starten des Docker-Dienstes"
        hinweis "Docker-Dienst läuft nicht – starte ihn …"
        $ALS_ROOT systemctl enable --now docker >/dev/null 2>&1 || fehler "Konnte den Docker-Dienst nicht starten (sudo systemctl status docker)."
        sleep 2
        meldung=$(docker info 2>&1 >/dev/null) && meldung="" ;;
    esac
    if [ -n "$meldung" ]; then
      case "$meldung" in
        *ermission*denied*)
          sudo_bereit "den Zugriff auf Docker ($(id -un) ist nicht in der Gruppe „docker“)"
          sudo docker info >/dev/null 2>&1 || fehler "Docker ist auch mit sudo nicht erreichbar (sudo systemctl status docker)."
          COMPOSE="sudo $COMPOSE"
          ENGINE="sudo $ENGINE"
          RECHTE="Docker über sudo"
          hinweis "Künftig ohne sudo: sudo usermod -aG docker $(id -un) (danach neu anmelden)" ;;
        *) fehler "Der Docker-Dienst ist nicht erreichbar: $meldung" ;;
      esac
    fi
  fi
fi
hinweis "Container-Werkzeug: $COMPOSE ($RECHTE)"

# ---------------------------------------------------------------- Installationsordner
# Hier, wenn schon eine Installation (oder ein Checkout) da ist oder der Ordner leer ist; sonst ./ladefalke
if [ ! -f .env ] && [ ! -f compose.yaml ]; then
  if [ -f ladefalke/.env ]; then
    cd ladefalke
  elif [ -n "$(ls -A | grep -vx 'setup.sh' || true)" ]; then
    [ -w . ] || [ -d ladefalke ] || neu_mit_sudo "Keine Schreibrechte in $(pwd) für den Ordner „ladefalke“"
    mkdir -p ladefalke && cd ladefalke
  fi
fi
hinweis "Ordner: $(pwd)"
# Schreibrechte: eine früher mit sudo bzw. als root angelegte Installation gehört root
for pfad in . .env compose.yaml .env.example setup.sh; do
  if [ -e "$pfad" ] && [ ! -w "$pfad" ]; then
    neu_mit_sudo "Keine Schreibrechte auf $(pwd)/$pfad (gehört $(ls -ld "$pfad" | awk '{print $3}'))"
  fi
done
if [ ! -f setup.sh ]; then  # für spätere Updates griffbereit
  if [ -n "$SKRIPT" ]; then cp "$SKRIPT" setup.sh
  else curl -fsSL "$ROH/main/setup.sh" -o setup.sh 2>/dev/null || rm -f setup.sh
  fi
fi

zufall() {  # zufall <Länge> → Zeichen aus a–z, A–Z, 0–9
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 96 | tr -dc 'A-Za-z0-9' | cut -c "1-$1"
  else
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"
  fi
}

setzen() {  # setzen SCHLÜSSEL WERT – ersetzt die Zeile SCHLÜSSEL=… in .env
  tmp=$(mktemp)
  awk -v k="$1" -v v="$2" 'BEGIN { FS = OFS = "=" } $1 == k { print k "=" v; next } { print }' .env >"$tmp"
  cat "$tmp" >.env && rm -f "$tmp"
}
wert() { sed -n "s/^$1=//p" .env | head -n 1; }
kleiner() {  # kleiner A B: Version A < B
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)" = "$1" ]
}

startfehler() {  # letzte Fehlerzeilen der App und, wo bekannt, was zu tun ist
  protokoll=$($COMPOSE logs --tail 60 app 2>/dev/null | sed 's/^[^|]*| //')
  printf '\n✗ Die App startet nicht – sie bricht beim Start ab und wird immer wieder neu gestartet.\n' >&2
  printf '%s\n' "$protokoll" | grep -E 'Error|FATAL|fehlgeschlagen|abgebrochen' | tail -n 3 | sed 's/^/  /' >&2
  case "$protokoll" in
    *"password authentication failed"*)
      printf '\n  Die Datenbank stammt aus einer früheren Installation und hat ein anderes Passwort als .env.\n' >&2
      printf '  Frische Installation ohne Daten: alte Datenbank löschen und neu starten:\n' >&2
      printf '    %s down -v && %s up -d\n' "$COMPOSE" "$COMPOSE" >&2
      printf '  Mit Daten: das alte POSTGRES_PASSWORD wieder in .env eintragen.\n' >&2 ;;
  esac
  printf '\n  Ganzes Protokoll: %s logs app\n' "$COMPOSE" >&2
}

warten() {  # bis die App antwortet (Sicherung und Migrationen laufen vorher), höchstens 10 Minuten
  printf '› Warte, bis die App bereit ist '
  i=0
  while [ $i -lt 120 ]; do
    if $COMPOSE exec -T app python -m werkzeuge.gesund >/dev/null 2>&1; then printf ' bereit.\n'; return 0; fi
    # startet der Container immer wieder neu, wird das nichts mehr – sofort melden statt 10 Minuten warten
    id=$($COMPOSE ps -q app 2>/dev/null | head -n 1)
    neustarts=$([ -n "$id" ] && $ENGINE inspect -f '{{.RestartCount}}' "$id" 2>/dev/null || echo 0)
    if [ "${neustarts:-0}" -ge 2 ] 2>/dev/null; then printf ' abgebrochen.\n'; startfehler; return 1; fi
    printf '.'; sleep 5; i=$((i + 1))
  done
  printf '\n'
  hinweis "Die App ist noch nicht bereit – Verlauf ansehen: $COMPOSE logs -f app"
  return 1
}

# Erster Admin: per Befehl in der laufenden App (nur solange es keine Benutzer:innen gibt), Passwort nur hier
# angezeigt – nicht in .env. Gibt ADMIN_ANMELDUNG („name / passwort“) zurück.
ADMIN_ANMELDUNG=""
admin_anlegen() {
  ausgabe=$($COMPOSE exec -T app python -m werkzeuge.admin admin --nur-wenn-leer 2>/dev/null) || return 0
  pw=$(printf '%s\n' "$ausgabe" | sed -n 's/^Passwort: //p')
  [ -n "$pw" ] && ADMIN_ANMELDUNG="admin / $pw"
}

# ADMIN_USER/ADMIN_PASSWORD aus .env (frühere Installationen): werden nicht mehr gebraucht, sobald es Konten gibt –
# entfernen und die App mit der bereinigten .env neu erzeugen
env_admin_aufraeumen() {
  grep -q -e '^ADMIN_USER=' -e '^ADMIN_PASSWORD=' .env || return 0
  $COMPOSE exec -T app python -m werkzeuge.admin --vorhanden >/dev/null 2>&1 || return 0
  tmp=$(mktemp)
  grep -v -e '^ADMIN_USER=' -e '^ADMIN_PASSWORD=' .env >"$tmp" || true
  cat "$tmp" >.env && rm -f "$tmp"
  hinweis "ADMIN_USER/ADMIN_PASSWORD aus .env entfernt – die Konten verwaltet die App (dein Passwort bleibt, wie es ist)."
  hinweis "Notfall (kein Admin, Passwort vergessen): $COMPOSE exec app python -m werkzeuge.admin"
  $COMPOSE up -d >/dev/null 2>&1 || true  # App mit der bereinigten .env neu erzeugen
  warten || true
}

# ---------------------------------------------------------------- Version: vorgegeben, sonst neuestes Release
neueste() {  # höchster Tag vX.Y.Z laut GitHub
  command -v curl >/dev/null 2>&1 || return 0
  curl -fsSL "https://api.github.com/repos/$REPO/tags?per_page=100" 2>/dev/null \
    | sed -n 's/.*"name": *"v\([0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1
}
# ---------------------------------------------------------------- Update einer bestehenden Installation
# Ohne --update: bestehende Release-Installation (nicht selbst gebaut) und neueres Release → Update anbieten
if [ -z "$UPDATE" ] && [ -f .env ] && [ -z "${LADEFALKE_VERSION:-}" ] && ! wert COMPOSE_FILE | grep -q dev; then
  installiert=$(wert LADEFALKE_VERSION)
  if [ -n "$installiert" ]; then
    UPDATE_ZIEL=$(neueste)
    if [ -n "$UPDATE_ZIEL" ] && kleiner "$installiert" "$UPDATE_ZIEL"; then
      if [ -n "$TTY$JA" ]; then UPDATE=1  # sonst (z. B. per Cron) kein Update ohne Zustimmung
      else hinweis "Version $UPDATE_ZIEL ist verfügbar (installiert: $installiert) – Update: sh setup.sh --update"; UPDATE_ZIEL=""
      fi
    else UPDATE_ZIEL=""
    fi
  fi
fi
if [ -n "$UPDATE" ]; then
  [ -f .env ] || fehler "Hier gibt es keine .env – zuerst einrichten: sh setup.sh"
  case "$(wert COMPOSE_FILE)" in *dev*) fehler "Entwicklungs-Installation (selbst gebaut) – Update per git, siehe README „Update“." ;; esac
  command -v curl >/dev/null 2>&1 || fehler "Für das Update wird curl gebraucht."
  ALT=$(wert LADEFALKE_VERSION)
  ZIEL="${UPDATE_ZIEL:-$(neueste)}"
  [ -n "$ZIEL" ] || fehler "Neuestes Release nicht ermittelbar – Version angeben: sh setup.sh --update 1.12.0"
  if [ "$ALT" = "$ZIEL" ]; then hinweis "Bereits auf Version $ZIEL – nichts zu tun."; exit 0; fi
  if [ -n "$ALT" ] && kleiner "$ZIEL" "$ALT"; then
    fehler "$ZIEL ist älter als die installierte $ALT – Rückschritt nur mit Sicherung, siehe README „Zurück auf ein älteres Release“."
  fi
  QUELLE="https://raw.githubusercontent.com/$REPO/v${ZIEL}"
  hinweis "Update von ${ALT:-unbekannt} auf $ZIEL"
  hinweise=$(curl -fsSL "$QUELLE/CHANGELOG.md" 2>/dev/null | awk -v ziel="## v$ZIEL" -v alt="## v${ALT:-0.0.0-keine}" '
    index($0, alt) == 1 { exit }
    index($0, ziel) == 1 { an = 1 }
    an && /^## v/ { version = $0; abschnitt = 0; next }
    an && /^### / { abschnitt = ($0 ~ /Beim Update beachten/); if (abschnitt) print "\n" substr(version, 4); next }
    an && abschnitt && NF { print "  " $0 }') || hinweise=""
  if [ -n "$hinweise" ]; then printf '\nBeim Update beachten (CHANGELOG):%s\n' "$hinweise"; fi
  case "$(frage "
Update jetzt durchführen? [J/n]" "j")" in [nN]*) printf 'Abgebrochen.\n'; exit 0 ;; esac
  [ -f compose.yaml ] && cp compose.yaml "compose.yaml.vor-${ALT:-update}"
  curl -fsSL "$QUELLE/compose.yaml" -o compose.yaml.neu || fehler "Konnte compose.yaml $ZIEL nicht laden."
  mv compose.yaml.neu compose.yaml
  curl -fsSL "$QUELLE/.env.example" -o .env.example || fehler "Konnte .env.example $ZIEL nicht laden."
  ergaenzt=""
  for schluessel in $(sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/\1/p' .env.example); do
    if ! grep -q "^$schluessel=" .env; then
      zeile=$(grep "^$schluessel=" .env.example | head -n 1)
      [ "${zeile#*=}" = "bitte-aendern" ] && zeile="$schluessel=$(zufall 32)"
      printf '%s\n' "$zeile" >>.env
      ergaenzt="$ergaenzt $schluessel"
    fi
  done
  [ -n "$ergaenzt" ] && hinweis "Neu in .env:$ergaenzt (Werte aus .env.example – bei Bedarf anpassen)"
  setzen LADEFALKE_VERSION "$ZIEL"
  $COMPOSE pull
  $COMPOSE up -d
  printf '\n'
  if warten; then
    env_admin_aufraeumen
    printf '\n✓ Update auf %s fertig. Geodaten aktualisiert die App jetzt selbst im Hintergrund.\n' "$ZIEL"
  else
    printf '\n✓ Update auf %s gestartet (Sicherung und Migrationen laufen noch).\n' "$ZIEL"
  fi
  printf '  Die bisherige compose.yaml liegt als compose.yaml.vor-%s daneben.\n' "${ALT:-update}"
  exit 0
fi

if [ -f .env ] && [ -z "${LADEFALKE_VERSION:-}" ]; then
  VERSION=$(sed -n 's/^LADEFALKE_VERSION=//p' .env | head -n 1)  # bestehende Installation: ihre Version
fi
VERSION="${LADEFALKE_VERSION:-${VERSION:-}}"
[ -n "$VERSION" ] || VERSION=$(neueste)
if [ -z "$VERSION" ]; then
  VERSION="$RUECKFALL"
  hinweis "Neuestes Release nicht ermittelbar – nehme $VERSION"
fi
hinweis "Version: $VERSION"
QUELLE="https://raw.githubusercontent.com/$REPO/v${VERSION}"

# ---------------------------------------------------------------- Dateien des Release
laden() {
  [ -f "$1" ] && return
  command -v curl >/dev/null 2>&1 || fehler "$1 fehlt und curl ist nicht installiert."
  hinweis "Lade $1 (Version $VERSION)"
  curl -fsSL "$QUELLE/$1" -o "$1" || fehler "Konnte $1 nicht laden ($QUELLE/$1)."
}
laden compose.yaml
laden .env.example

# ---------------------------------------------------------------- .env
# Datenbank aus einer früheren Installation, aber keine .env dazu: ihr Passwort steht nur in der alten .env –
# mit neuen Zufallswerten käme die App nicht hinein. Löschen nur nach ausdrücklicher Zustimmung (nie mit --ja).
alte_datenbank() {
  $ENGINE volume inspect ladefalke_pgdata >/dev/null 2>&1 || return 0
  printf '\n'
  hinweis "Es gibt schon eine Datenbank aus einer früheren Installation (Volume ladefalke_pgdata), aber keine .env dazu."
  hinweis "Ihr Passwort steht nur in der alten .env – mit einer neuen .env käme die App nicht hinein."
  [ -n "$TTY" ] || fehler "Abgebrochen: alte .env hierher zurücklegen oder die alte Datenbank löschen ($ENGINE volume rm ladefalke_pgdata – löscht alle Daten darin)."
  case "$(frage "Alte Datenbank löschen und neu beginnen? Alle Daten darin gehen verloren. [j/N]" "n")" in
    [jJ]*) ;;
    *) fehler "Abgebrochen – nichts geändert. Gibt es die alte .env noch: zurücklegen und erneut starten." ;;
  esac
  $ENGINE rm -f ladefalke-app-1 ladefalke-db-1 ladefalke_app_1 ladefalke_db_1 >/dev/null 2>&1 || true
  $ENGINE volume rm ladefalke_pgdata >/dev/null || fehler "Konnte die alte Datenbank nicht löschen."
  hinweis "Alte Datenbank gelöscht."
}

# Servername(n) für das Zertifikat: IPv4, IPv6 oder Rechnername (nur ASCII), mehrere mit Leerzeichen oder Komma
name_gueltig() {
  case "$1" in
    *[!0-9.]*) ;;
    *) printf '%s' "$1" | LC_ALL=C grep -Eq '^((25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])$'
       return ;;
  esac
  case "$1" in *:*) printf '%s' "$1" | LC_ALL=C grep -Eq '^[0-9A-Fa-f:.]+$'; return ;; esac
  printf '%s' "$1" | LC_ALL=C grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$'
}
namen_pruefen() {  # gibt die erste ungültige Angabe aus (Rückgabe 1) bzw. nichts
  set -f
  for n in $(printf '%s' "$1" | tr ',' ' '); do
    name_gueltig "$n" || { set +f; printf '%s' "$n"; return 1; }
  done
  set +f
}

NEU=""
if [ -f .env ]; then
  hinweis ".env vorhanden – bleibt erhalten, nur offene Platzhalter werden ersetzt"
else
  alte_datenbank
  cp .env.example .env
  NEU=1
  hinweis ".env aus .env.example angelegt"
fi
chmod 600 .env

[ "$(wert POSTGRES_PASSWORD)" = "bitte-aendern" ] && setzen POSTGRES_PASSWORD "$(zufall 32)"
if [ "$(wert ADMIN_PASSWORD)" = "bitte-aendern" ]; then  # Vorlage früherer Versionen: Admin entsteht jetzt per Befehl
  tmp=$(mktemp); grep -v -e '^ADMIN_USER=' -e '^ADMIN_PASSWORD=' .env >"$tmp" || true; cat "$tmp" >.env; rm -f "$tmp"
fi
[ "$(wert SECRET_KEY)" = "bitte-aendern" ] && setzen SECRET_KEY "$(zufall 64)"
BACKUP_NEU=""
if [ -z "$(wert BACKUP_PASSWORT)" ] && [ -n "$NEU" ]; then setzen BACKUP_PASSWORT "$(zufall 24)"; BACKUP_NEU=1; fi
[ -n "$NEU" ] && setzen LADEFALKE_VERSION "$VERSION"

if [ -n "$NEU" ] && [ -z "$(wert TLS_NAMEN)" ]; then
  while :; do
    name=$(frage "Name oder IP-Adresse des Servers im Netz (für das HTTPS-Zertifikat, leer = nur localhost):" "${TLS_NAMEN:-}")
    if falsch=$(namen_pruefen "$name"); then
      [ -z "$name" ] || [ -z "$TTY" ] && break
      case "$(frage "Zertifikat für „$name“ – stimmt das? [J/n]" "j")" in [nN]*) continue ;; *) break ;; esac
    fi
    [ -n "$TTY" ] || fehler "TLS_NAMEN: „$falsch“ ist keine IP-Adresse und kein gültiger Rechnername."
    printf '  „%s“ ist keine IP-Adresse und kein gültiger Rechnername (z. B. 192.168.1.20 oder server.firma.lan).\n' "$falsch" >/dev/tty
  done
  [ -n "$name" ] && setzen TLS_NAMEN "$(printf '%s' "$name" | tr ',' ' ')"
fi

mkdir -p data/uploads data/backups data/tls data/geo

# ---------------------------------------------------------------- Zusammenfassung
PORT=$(wert APP_PORT); PORT=${PORT:-8000}
ADRESSE=$(wert TLS_NAMEN | cut -d ' ' -f 1); ADRESSE=${ADRESSE:-localhost}
SCHEMA=https; [ "$(wert TLS)" = "aus" ] && SCHEMA=http
printf '\n✓ Eingerichtet in %s\n\n' "$(pwd)"
printf '  Adresse:   %s://%s:%s\n' "$SCHEMA" "$ADRESSE" "$PORT"
if [ -n "$BACKUP_NEU" ]; then
  printf '  Backup-Passwort: %s\n' "$(wert BACKUP_PASSWORT)"
  printf '\n  Steht auch in .env – zusätzlich sicher aufbewahren, ohne lassen sich Sicherungen nicht\n'
  printf '  wiederherstellen.\n'
fi
[ "$SCHEMA" = "https" ] && printf '\n  Das Zertifikat ist selbst signiert: Der Browser fragt beim ersten Aufruf einmal nach.\n'

# ---------------------------------------------------------------- Start
if [ -n "$OHNE_START" ]; then start="n"; else start=$(frage "
Jetzt starten? [J/n]" "j"); fi
case "$start" in
  [nN]*) printf '\nStarten später mit: %s up -d\n' "$COMPOSE"
    printf 'Den ersten Admin legst du dann beim ersten Aufruf im Browser an.\n' ;;
  *)
    $COMPOSE up -d
    printf '\n'
    if warten; then
      admin_anlegen
      env_admin_aufraeumen
      printf '\n✓ Die App läuft: %s://%s:%s\n' "$SCHEMA" "$ADRESSE" "$PORT"
      if [ -n "$ADMIN_ANMELDUNG" ]; then
        printf '\n  Anmeldung: %s\n' "$ADMIN_ANMELDUNG"
        printf '  Wird nur jetzt angezeigt und steht nirgends sonst – notieren und nach der ersten Anmeldung\n'
        printf '  unter „Mein Konto“ ändern. Vergessen? %s exec app python -m werkzeuge.admin\n' "$COMPOSE"
      fi
      printf '\n  Postleitzahlen für die Karte lädt die App in den nächsten Minuten selbst (Einstellungen → System).\n'
    fi
    printf '\nUpdate später – in diesem Ordner: sh setup.sh\n'
    ;;
esac
