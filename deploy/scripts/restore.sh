#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    echo "Usage: $0 <prod|test-b|test-a> <backup_archive.tar.gz> [--force-prod]" >&2
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

TARGET_ENV="$1"
ARCHIVE_PATH="$2"
FORCE_FLAG="${3:-}"

case "$TARGET_ENV" in
    prod|test-b|test-a) ;;
    *)
        echo "[ERROR] Invalid target environment '${TARGET_ENV}'." >&2
        usage
        ;;
esac

if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "[ERROR] Backup archive not found: ${ARCHIVE_PATH}" >&2
    exit 1
fi

ENV_FILE="${DEPLOY_DIR}/env/${TARGET_ENV}.env"
COMPOSE_FILE="${DEPLOY_DIR}/docker-compose.${TARGET_ENV}.yml"
if [ ! -f "$ENV_FILE" ] || [ ! -f "$COMPOSE_FILE" ]; then
    echo "[ERROR] Missing env or compose file for '${TARGET_ENV}'." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Safety lock for Production restores
if [ "$TARGET_ENV" = "prod" ] && [ "$FORCE_FLAG" != "--force-prod" ]; then
    echo "================================================================"
    echo "WARNING: You are about to OVERWRITE PRODUCTION database '${ODOO_DB_NAME}'!"
    echo "Source archive: ${ARCHIVE_PATH}"
    echo "================================================================"
    read -r -p "Type 'RESTORE PRODUCTION' to continue: " confirm
    if [ "$confirm" != "RESTORE PRODUCTION" ]; then
        echo "[ABORTED] Production restore cancelled."
        exit 1
    fi
fi

# Verify checksum if .sha256 sidecar exists
if [ -f "${ARCHIVE_PATH}.sha256" ]; then
    echo "[1/6] Verifying archive SHA-256 checksum..."
    (cd "$(dirname "$ARCHIVE_PATH")" && sha256sum -c "$(basename "${ARCHIVE_PATH}.sha256")")
else
    echo "[1/6] No .sha256 file found, skipping checksum verification."
fi

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "[2/6] Extracting backup archive..."
tar -C "$TMP_DIR" -xzf "$ARCHIVE_PATH"

if [ ! -f "${TMP_DIR}/db.dump" ] || [ ! -f "${TMP_DIR}/filestore.tar.gz" ]; then
    echo "[ERROR] Invalid backup archive: missing db.dump or filestore.tar.gz" >&2
    exit 1
fi

SOURCE_DB=""
if [ -f "${TMP_DIR}/metadata.txt" ]; then
    SOURCE_DB="$(grep -E '^database=' "${TMP_DIR}/metadata.txt" | cut -d'=' -f2- || true)"
fi
if [ -z "$SOURCE_DB" ]; then
    # Fallback: inspect first top-level directory inside filestore.tar.gz
    SOURCE_DB="$(tar -tzf "${TMP_DIR}/filestore.tar.gz" | head -n 1 | cut -d'/' -f1)"
fi
echo "[INFO] Source DB: ${SOURCE_DB:-unknown} -> Target DB: ${ODOO_DB_NAME} (${TARGET_ENV})"

# Ensure external proxy network exists before Compose parses it, then start DB
if ! docker network inspect odoo_proxy_net >/dev/null 2>&1; then
    docker network create odoo_proxy_net >/dev/null
fi
dc=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
echo "[3/6] Preparing Compose stack and ensuring DB '${DB_CONTAINER}' is healthy..."
"${dc[@]}" up --no-start
"${dc[@]}" stop odoo >/dev/null 2>&1 || true
"${dc[@]}" up -d --wait db

# Verify dump against PostgreSQL container before dropping target DB
docker exec -i "${DB_CONTAINER}" pg_restore -l < "${TMP_DIR}/db.dump" >/dev/null

echo "[4/6] Recreating database '${ODOO_DB_NAME}' and restoring PostgreSQL dump..."
docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d postgres <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${ODOO_DB_NAME}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "${ODOO_DB_NAME}";
CREATE DATABASE "${ODOO_DB_NAME}" WITH OWNER "${POSTGRES_USER}" ENCODING 'UTF8' TEMPLATE template0;
SQL

docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" \
    pg_restore -U "${POSTGRES_USER}" --no-owner --no-acl -d "${ODOO_DB_NAME}" < "${TMP_DIR}/db.dump"

echo "[5/6] Restoring filestore into volume '${ODOO_VOLUME}'..."
docker run --rm -i -v "${ODOO_VOLUME}:/var/lib/odoo" alpine:3.20 sh -c "
    set -e
    mkdir -p /var/lib/odoo/filestore
    rm -rf '/var/lib/odoo/filestore/${ODOO_DB_NAME}'
    if [ -n '${SOURCE_DB}' ] && [ '${SOURCE_DB}' != '${ODOO_DB_NAME}' ]; then
        rm -rf '/var/lib/odoo/filestore/${SOURCE_DB}'
    fi
    tar -C /var/lib/odoo/filestore -xzf -
    if [ -n '${SOURCE_DB}' ] && [ '${SOURCE_DB}' != '${ODOO_DB_NAME}' ] && [ -d '/var/lib/odoo/filestore/${SOURCE_DB}' ]; then
        mv '/var/lib/odoo/filestore/${SOURCE_DB}' '/var/lib/odoo/filestore/${ODOO_DB_NAME}'
    fi
    mkdir -p '/var/lib/odoo/filestore/${ODOO_DB_NAME}'
    chown -R 1000:1000 '/var/lib/odoo/filestore/${ODOO_DB_NAME}'
" < "${TMP_DIR}/filestore.tar.gz"

echo "[6/6] Applying environment post-restore parameters..."
if [ "$TARGET_ENV" != "prod" ]; then
    echo "[SANITIZE] Disabling outgoing/incoming mail servers and regenerating database UUID on '${TARGET_ENV}'..."
    docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${ODOO_DB_NAME}" <<SQL
UPDATE ir_mail_server SET active = false;
DO \$\$
BEGIN
    IF to_regclass('public.fetchmail_server') IS NOT NULL THEN
        EXECUTE 'UPDATE fetchmail_server SET active = false';
    END IF;
END \$\$;
UPDATE ir_config_parameter
SET value = gen_random_uuid()::text, write_date = NOW(), write_uid = 1
WHERE key = 'database.uuid';
SQL
fi

# Always lock web.base.url and report.url (ensures wkhtmltopdf CSS/fonts render behind proxy)
docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${ODOO_DB_NAME}" <<SQL
INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
VALUES
    ('web.base.url', '${ODOO_BASE_URL}', 1, 1, NOW(), NOW()),
    ('web.base.url.freeze', 'True', 1, 1, NOW(), NOW()),
    ('report.url', 'http://127.0.0.1:8069', 1, 1, NOW(), NOW())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, write_date = NOW(), write_uid = 1;
SQL

echo "[INFO] Starting Odoo container '${ODOO_CONTAINER}'..."
"${dc[@]}" up -d odoo
echo "[DONE] Restore to '${TARGET_ENV}' (${ODOO_DB_NAME}) complete!"
