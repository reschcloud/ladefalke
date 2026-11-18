#!/bin/sh
# Feste Versionen der Python-Abhängigkeiten neu erzeugen (requirements-lock.txt, requirements-dev-lock.txt) – aus
# requirements.txt bzw. requirements-dev.txt, in einem frischen python:3.12-slim (wie das Image). Danach Tests laufen
# lassen und die beiden Lock-Dateien committen. Aufruf im Projektverzeichnis:  sh werkzeuge/abhaengigkeiten_sperren.sh
set -e
cd "$(dirname "$0")/.."
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e PIP_NO_CACHE_DIR=1 -v "$PWD:/w:Z" -w /w \
  docker.io/library/python:3.12-slim sh -c '
    set -e
    kopf="# Automatisch erzeugt mit werkzeuge/abhaengigkeiten_sperren.sh – nicht von Hand ändern (Dependabot schlägt Updates per Pull Request vor)"
    python -m venv /tmp/p && /tmp/p/bin/pip install -q -r requirements.txt
    { echo "$kopf"; /tmp/p/bin/pip freeze; } > requirements-lock.txt
    python -m venv /tmp/d && /tmp/d/bin/pip install -q -r requirements-dev.txt
    { echo "$kopf"; /tmp/d/bin/pip freeze; } > requirements-dev-lock.txt'
echo "requirements-lock.txt und requirements-dev-lock.txt neu erzeugt."
