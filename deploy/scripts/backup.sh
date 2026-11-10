#!/usr/bin/env bash
set -euo pipefail
umask 077
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"
BACKUP_ROOT="${DEPLOY_DIR}/backups"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <prod|test-b|test-a>" >&2
    exit 1
fi

TARGET_ENV="$1"
case "$TARGET_ENV" in
    prod|test-b|test-a) ;;
    *)
        echo "[ERROR] Invalid environment '${TARGET_ENV}'. Must be: prod | test-b | test-a" >&2
        exit 1
        ;;
esac

ENV_FILE="${DEPLOY_DIR}/env/${TARGET_ENV}.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "[ERROR] Env file not found: $ENV_FILE" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Prevent concurrent backup runs for the same environment
mkdir -p "${BACKUP_ROOT}"
LOCK_FILE="${BACKUP_ROOT}/.${TARGET_ENV}.lock"
exec 9>"$LOCK_FILE"
if command -v flock >/dev/null 2>&1; then
    if ! flock -n 9; then
        echo "[ERROR] Another backup for '${TARGET_ENV}' is already running." >&2
        exit 1
    fi
fi

# Verify DB container is running
if [ "$(docker inspect -f '{{.State.Running}}' "${DB_CONTAINER}" 2>/dev/null || echo false)" != "true" ]; then
    echo "[ERROR] Database container '${DB_CONTAINER}' is not running." >&2
    exit 1
fi

# Verify target Odoo database exists
DB_EXISTS="$(docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" \
    psql -U "${POSTGRES_USER}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${ODOO_DB_NAME}'")"
if [ "$DB_EXISTS" != "1" ]; then
    echo "[ERROR] Database '${ODOO_DB_NAME}' does not exist in '${DB_CONTAINER}'." >&2
    exit 1
fi

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
DAY_OF_WEEK="$(date +%u)"   # 7 = Sunday
DAY_OF_MONTH="$(date +%d)"  # 01 = 1st of month

DAILY_DIR="${BACKUP_ROOT}/${TARGET_ENV}/daily"
WEEKLY_DIR="${BACKUP_ROOT}/${TARGET_ENV}/weekly"
MONTHLY_DIR="${BACKUP_ROOT}/${TARGET_ENV}/monthly"
mkdir -p "$DAILY_DIR" "$WEEKLY_DIR" "$MONTHLY_DIR"

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

ARCHIVE_NAME="${ODOO_DB_NAME}_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="${DAILY_DIR}/${ARCHIVE_NAME}"

echo "[1/4] Dumping PostgreSQL database '${ODOO_DB_NAME}' from '${DB_CONTAINER}'..."
docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" \
    pg_dump -U "${POSTGRES_USER}" -Fc --no-owner --no-acl "${ODOO_DB_NAME}" > "${TMP_DIR}/db.dump"

echo "[2/4] Verifying database dump integrity..."
docker exec -i "${DB_CONTAINER}" pg_restore -l < "${TMP_DIR}/db.dump" >/dev/null

echo "[3/4] Archiving Odoo filestore from volume '${ODOO_VOLUME}'..."
docker run --rm -v "${ODOO_VOLUME}:/var/lib/odoo:ro" alpine:3.20 sh -c \
    "if [ -d '/var/lib/odoo/filestore/${ODOO_DB_NAME}' ]; then \
        tar -C /var/lib/odoo/filestore -czf - '${ODOO_DB_NAME}'; \
     else \
        mkdir -p '/tmp/empty_fs/${ODOO_DB_NAME}' && tar -C /tmp/empty_fs -czf - '${ODOO_DB_NAME}'; \
     fi" > "${TMP_DIR}/filestore.tar.gz"

GIT_COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
cat > "${TMP_DIR}/metadata.txt" <<META
environment=${TARGET_ENV}
database=${ODOO_DB_NAME}
timestamp=${TIMESTAMP}
git_branch=${GIT_BRANCH}
git_commit=${GIT_COMMIT}
META

echo "[4/4] Packaging archive -> ${ARCHIVE_PATH}"
tar -C "${TMP_DIR}" -czf "${ARCHIVE_PATH}" db.dump filestore.tar.gz metadata.txt
(cd "${DAILY_DIR}" && sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256")
chmod 600 "${ARCHIVE_PATH}" "${ARCHIVE_PATH}.sha256"

# Weekly & Monthly Promotion
WEEKLY_CREATED=""
MONTHLY_CREATED=""
if [ "$DAY_OF_WEEK" = "7" ] && [ "${BACKUP_RETENTION_WEEKLY:-4}" -gt 0 ]; then
    cp -f "${ARCHIVE_PATH}" "${WEEKLY_DIR}/${ARCHIVE_NAME}"
    (cd "${WEEKLY_DIR}" && sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256")
    WEEKLY_CREATED="${WEEKLY_DIR}/${ARCHIVE_NAME}"
    echo "[INFO] Promoted weekly backup: ${WEEKLY_CREATED}"
fi

if [ "$DAY_OF_MONTH" = "01" ] && [ "${BACKUP_RETENTION_MONTHLY:-12}" -gt 0 ]; then
    cp -f "${ARCHIVE_PATH}" "${MONTHLY_DIR}/${ARCHIVE_NAME}"
    (cd "${MONTHLY_DIR}" && sha256sum "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256")
    MONTHLY_CREATED="${MONTHLY_DIR}/${ARCHIVE_NAME}"
    echo "[INFO] Promoted monthly backup: ${MONTHLY_CREATED}"
fi

# Rotation helper: keep N newest .tar.gz archives and their .sha256 files
rotate_dir() {
    local dir="$1"
    local keep="$2"
    if [ "$keep" -le 0 ]; then
        return 0
    fi
    local count=0
    while IFS= read -r file; do
        count=$((count + 1))
        if [ "$count" -gt "$keep" ]; then
            rm -f "$file" "${file}.sha256"
            echo "[ROTATE] Removed old backup: $file"
        fi
    done < <(ls -1t "${dir}"/*.tar.gz 2>/dev/null || true)
}

rotate_dir "$DAILY_DIR" "${BACKUP_RETENTION_DAILY:-14}"
rotate_dir "$WEEKLY_DIR" "${BACKUP_RETENTION_WEEKLY:-4}"
rotate_dir "$MONTHLY_DIR" "${BACKUP_RETENTION_MONTHLY:-12}"

# Off-site Cloud Sync via rclone
if [ -n "${RCLONE_REMOTE:-}" ]; then
    if command -v rclone >/dev/null 2>&1; then
        echo "[CLOUD] Copying backup to ${RCLONE_REMOTE}/${TARGET_ENV}/..."
        rclone copy "${ARCHIVE_PATH}" "${RCLONE_REMOTE}/${TARGET_ENV}/daily/"
        rclone copy "${ARCHIVE_PATH}.sha256" "${RCLONE_REMOTE}/${TARGET_ENV}/daily/"
        if [ -n "$WEEKLY_CREATED" ]; then
            rclone copy "${WEEKLY_CREATED}" "${RCLONE_REMOTE}/${TARGET_ENV}/weekly/"
            rclone copy "${WEEKLY_CREATED}.sha256" "${RCLONE_REMOTE}/${TARGET_ENV}/weekly/"
        fi
        if [ -n "$MONTHLY_CREATED" ]; then
            rclone copy "${MONTHLY_CREATED}" "${RCLONE_REMOTE}/${TARGET_ENV}/monthly/"
            rclone copy "${MONTHLY_CREATED}.sha256" "${RCLONE_REMOTE}/${TARGET_ENV}/monthly/"
        fi
        if [ "${RCLONE_CLOUD_RETENTION_DAYS:-30}" -gt 0 ]; then
            rclone delete "${RCLONE_REMOTE}/${TARGET_ENV}/daily" --min-age "${RCLONE_CLOUD_RETENTION_DAYS:-30}d"
        fi
        echo "[CLOUD] Sync completed."
    else
        echo "[WARN] RCLONE_REMOTE is set but 'rclone' binary is not installed. Skipping cloud sync." >&2
    fi
fi

ARCHIVE_SIZE="$(du -h "${ARCHIVE_PATH}" | awk '{print $1}')"
echo "[DONE] Backup complete: ${ARCHIVE_PATH} (${ARCHIVE_SIZE})"

