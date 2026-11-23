#!/bin/sh
# Ladefalke – Einrichtung auf einem Server (oder lokal zum Ausprobieren).
#
#   mkdir ladefalke && cd ladefalke
#   curl -fsSL https://raw.githubusercontent.com/reschcloud/ladefalke/main/setup.sh -o setup.sh
#   sh setup.sh
#
# Ermittelt das neueste Release (oder LADEFALKE_VERSION=x.y.z vorgeben), holt dessen compose.yaml und .env.example
# (falls nicht vorhanden), legt .env mit zufällig erzeugten
# Geheimnissen an (Datenbank-Passwort, Admin-Passwort, SECRET_KEY, BACKUP_PASSWORT), legt die Datenordner an und
# startet auf Wunsch. Eine vorhandene .env wird nie überschrieben – darin werden nur noch offene Platzhalter
# („bitte-aendern“) ersetzt. Ohne Rückfragen: sh setup.sh --ja  (Servername dann über TLS_NAMEN=… vorgeben);
# nur einrichten, nicht starten: --ohne-start.
#
# Update einer bestehenden Installation:  sh setup.sh --update [x.y.z]   (ohne Version: neuestes Release)
# Zeigt die Hinweise „Beim Update beachten“ aller übersprungenen Versionen, sichert compose.yaml, holt die neue,
# ergänzt neue Einträge aus .env.example in .env (vorhandene Werte bleiben), setzt LADEFALKE_VERSION und startet neu.
# Vor anstehenden Datenbank-Migrationen legt die App selbst eine Sicherung an.
set -eu

RUECKFALL="1.6.1"  # nur ohne Zugriff auf die GitHub-API; bei jedem Release mitziehen (README „Release erstellen“)
REPO="reschcloud/ladefalke"
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

fehler() { printf '\n✗ %s\n' "$1" >&2; exit 1; }
hinweis() { printf '› %s\n' "$1"; }
frage() {  # frage "Text" "Vorgabe" → Antwort (ohne Terminal oder mit --ja: Vorgabe)
  if [ -n "$JA" ] || [ ! -t 0 ]; then printf '%s' "$2"; return; fi
  printf '%s ' "$1" >&2; read -r antwort || antwort=""
  printf '%s' "${antwort:-$2}"
}

# ---------------------------------------------------------------- Voraussetzungen
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
elif podman compose version >/dev/null 2>&1; then COMPOSE="podman compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"
else
  fehler "Docker mit Compose-Plugin (oder Podman mit Compose) wird gebraucht – siehe README „Voraussetzungen“."
fi
hinweis "Container-Werkzeug: $COMPOSE"

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

# ---------------------------------------------------------------- Version: vorgegeben, sonst neuestes Release
neueste() {  # höchster Tag vX.Y.Z laut GitHub
  command -v curl >/dev/null 2>&1 || return 0
  curl -fsSL "https://api.github.com/repos/$REPO/tags?per_page=100" 2>/dev/null \
    | sed -n 's/.*"name": *"v\([0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1
}
# ---------------------------------------------------------------- Update einer bestehenden Installation
if [ -n "$UPDATE" ]; then
  [ -f .env ] || fehler "Hier gibt es keine .env – zuerst einrichten: sh setup.sh"
  case "$(wert COMPOSE_FILE)" in *dev*) fehler "Entwicklungs-Installation (selbst gebaut) – Update per git, siehe README „Update“." ;; esac
  command -v curl >/dev/null 2>&1 || fehler "Für das Update wird curl gebraucht."
  ALT=$(wert LADEFALKE_VERSION)
  ZIEL="${UPDATE_ZIEL:-$(neueste)}"
  [ -n "$ZIEL" ] || fehler "Neuestes Release nicht ermittelbar – Version angeben: sh setup.sh --update 1.7.0"
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
  printf '\n✓ Update auf %s gestartet. Sicherung und Migration verfolgen: %s logs -f app\n' "$ZIEL" "$COMPOSE"
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
NEU=""
if [ -f .env ]; then
  hinweis ".env vorhanden – bleibt erhalten, nur offene Platzhalter werden ersetzt"
else
  cp .env.example .env
  NEU=1
  hinweis ".env aus .env.example angelegt"
fi
chmod 600 .env

ADMIN_NEU=""
[ "$(wert POSTGRES_PASSWORD)" = "bitte-aendern" ] && setzen POSTGRES_PASSWORD "$(zufall 32)"
if [ "$(wert ADMIN_PASSWORD)" = "bitte-aendern" ]; then setzen ADMIN_PASSWORD "$(zufall 16)"; ADMIN_NEU=1; fi
[ "$(wert SECRET_KEY)" = "bitte-aendern" ] && setzen SECRET_KEY "$(zufall 64)"
BACKUP_NEU=""
if [ -z "$(wert BACKUP_PASSWORT)" ] && [ -n "$NEU" ]; then setzen BACKUP_PASSWORT "$(zufall 24)"; BACKUP_NEU=1; fi
[ -n "$NEU" ] && setzen LADEFALKE_VERSION "$VERSION"

if [ -n "$NEU" ] && [ -z "$(wert TLS_NAMEN)" ]; then
  name=$(frage "Name oder IP-Adresse des Servers im Netz (für das HTTPS-Zertifikat, leer = nur localhost):" "${TLS_NAMEN:-}")
  [ -n "$name" ] && setzen TLS_NAMEN "$name"
fi

mkdir -p data/uploads data/backups data/tls data/geo

# ---------------------------------------------------------------- Zusammenfassung
PORT=$(wert APP_PORT); PORT=${PORT:-8000}
ADRESSE=$(wert TLS_NAMEN | cut -d ' ' -f 1); ADRESSE=${ADRESSE:-localhost}
SCHEMA=https; [ "$(wert TLS)" = "aus" ] && SCHEMA=http
printf '\n✓ Eingerichtet in %s\n\n' "$(pwd)"
printf '  Adresse:   %s://%s:%s\n' "$SCHEMA" "$ADRESSE" "$PORT"
if [ -n "$ADMIN_NEU" ]; then
  printf '  Anmeldung: %s / %s\n' "$(wert ADMIN_USER)" "$(wert ADMIN_PASSWORD)"
fi
if [ -n "$BACKUP_NEU" ]; then
  printf '  Backup-Passwort: %s\n' "$(wert BACKUP_PASSWORT)"
fi
if [ -n "$ADMIN_NEU$BACKUP_NEU" ]; then
  printf '\n  Diese Angaben stehen auch in .env. Das Backup-Passwort zusätzlich sicher aufbewahren –\n'
  printf '  ohne lassen sich Sicherungen nicht wiederherstellen. Das Admin-Passwort nach der ersten\n'
  printf '  Anmeldung unter „Mein Konto“ ändern.\n'
fi
[ "$SCHEMA" = "https" ] && printf '\n  Das Zertifikat ist selbst signiert: Der Browser fragt beim ersten Aufruf einmal nach.\n'

# ---------------------------------------------------------------- Start
if [ -n "$OHNE_START" ]; then start="n"; else start=$(frage "
Jetzt starten? [J/n]" "j"); fi
case "$start" in
  [nN]*) printf '\nStarten später mit: %s up -d\n' "$COMPOSE" ;;
  *)
    $COMPOSE up -d
    printf '\nDie App startet (erster Start mit Datenbank-Einrichtung ca. 1 Minute). Status: %s ps\n' "$COMPOSE"
    ;;
esac
