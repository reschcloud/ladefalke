#!/bin/sh
# Start im Container: Sicherung (falls Migrationen anstehen), Migrationen, dann die App
#   BACKUP_VOR_UPDATE=aus – keine Sicherung vor Migrationen (Standard: an; schlägt sie fehl, startet die App nicht)
#   TLS=an  (Standard) – HTTPS mit selbstsigniertem bzw. eigenem Zertifikat aus TLS_DIR (app/tls.py)
#   TLS=aus – HTTP, für den Betrieb hinter einem Reverse Proxy; dessen X-Forwarded-*-Header werden übernommen
#             (FORWARDED_ALLOW_IPS: Adressen des Proxys, Standard alle)
set -e
if [ "${BACKUP_VOR_UPDATE:-an}" != "aus" ]; then
  python -m werkzeuge.backup vor-update || {
    echo "Sicherung vor dem Update fehlgeschlagen – Migrationen und Start abgebrochen (siehe README „Betrieb“)." >&2
    exit 1
  }
fi
alembic upgrade head

if [ "${TLS:-an}" = "aus" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
fi

set -- $(python -m app.tls)
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-certfile "$1" --ssl-keyfile "$2"
