FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pg_dump/pg_restore in der Version des Datenbankservers (16) für das Backup – aus dem offiziellen PostgreSQL-Archiv;
# Pango/HarfBuzz und Schriften für die PDF-Reports (WeasyPrint)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation2 fonts-dejavu-core \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Feste Versionen (werkzeuge/abhaengigkeiten_sperren.sh); DEV=1 (compose.dev.yaml) zusätzlich pytest und httpx
COPY requirements.lock requirements-dev.lock ./
ARG DEV=0
RUN if [ "$DEV" = "1" ]; then pip install -r requirements-dev.lock; else pip install -r requirements.lock; fi

COPY . .
# Versionsanzeige im Footer (app/version.py): Build-Datum; APP_VERSION setzt die Release-Pipeline (z. B. „v1.0.1“),
# beim Selbstbauen ergibt sich die Version aus dem Git-Stand
ARG APP_VERSION=""
RUN date +%Y-%m-%d > /app/BUILD_DATUM && if [ -n "$APP_VERSION" ]; then echo "$APP_VERSION" > /app/VERSION; fi

EXPOSE 8000
# Migrationen, dann HTTPS mit selbstsigniertem Zertifikat (TLS=an) bzw. HTTP hinter einem Reverse Proxy (TLS=aus)
CMD ["sh", "start.sh"]
