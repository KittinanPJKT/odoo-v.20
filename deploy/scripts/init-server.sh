#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_DIR="${DEPLOY_DIR}/env"
BACKUP_DIR="${DEPLOY_DIR}/backups"

echo "=== Odoo 20 Multi-Environment Server Initializer ==="

# 1. Check required tools
for cmd in docker openssl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "[ERROR] Missing required command: $cmd" >&2
        exit 1
    fi
done

if ! docker compose version >/dev/null 2>&1; then
    echo "[ERROR] Docker Compose v2 plugin ('docker compose') is required." >&2
    exit 1
fi

# 2. Ensure shared proxy network exists
if ! docker network inspect odoo_proxy_net >/dev/null 2>&1; then
    echo "[INFO] Creating Docker network 'odoo_proxy_net'..."
    docker network create odoo_proxy_net >/dev/null
else
    echo "[INFO] Docker network 'odoo_proxy_net' already exists."
fi

# 3. Create backup directories
for env in prod test-b test-a; do
    mkdir -p "${BACKUP_DIR}/${env}/daily" "${BACKUP_DIR}/${env}/weekly" "${BACKUP_DIR}/${env}/monthly"
done
echo "[INFO] Backup directories prepared under ${BACKUP_DIR}"

# Helper: generate safe alphanumeric secret (no '$' to avoid Compose variable interpolation issues)
gen_secret() {
    openssl rand -hex 20
}

# 4. Create .env files from .example templates if not present
if [ ! -f "${ENV_DIR}/proxy.env" ]; then
    cp "${ENV_DIR}/proxy.env.example" "${ENV_DIR}/proxy.env"
    chmod 600 "${ENV_DIR}/proxy.env"
    echo "[CREATED] ${ENV_DIR}/proxy.env (please update ACME_EMAIL and domain names)"
else
    echo "[SKIP] ${ENV_DIR}/proxy.env already exists"
fi

for env in prod test-b test-a; do
    target="${ENV_DIR}/${env}.env"
    template="${ENV_DIR}/${env}.env.example"
    if [ ! -f "$target" ]; then
        cp "$template" "$target"
        db_pass="$(gen_secret)"
        master_pass="$(gen_secret)"
        sed -i \
            -e "s|CHANGE_ME_PROD_DB_PASSWORD|${db_pass}|g" \
            -e "s|CHANGE_ME_TEST_B_DB_PASSWORD|${db_pass}|g" \
            -e "s|CHANGE_ME_TEST_A_DB_PASSWORD|${db_pass}|g" \
            -e "s|CHANGE_ME_PROD_MASTER_PASSWORD|${master_pass}|g" \
            -e "s|CHANGE_ME_TEST_B_MASTER_PASSWORD|${master_pass}|g" \
            -e "s|CHANGE_ME_TEST_A_MASTER_PASSWORD|${master_pass}|g" \
            "$target"
        chmod 600 "$target"
        echo "[CREATED] ${target} (generated strong random DB & Master passwords)"
    else
        echo "[SKIP] ${target} already exists"
    fi
done

chmod +x "${SCRIPT_DIR}/"*.sh

if ! command -v rclone >/dev/null 2>&1; then
    echo "[NOTE] 'rclone' is not installed yet. Install it later if you want Cloud backup sync (apt install rclone)."
fi

echo ""
echo "=== Next Steps ==="
echo "1. Edit ${ENV_DIR}/proxy.env with your real domains (erp / demo / dev) and ACME_EMAIL."
echo "2. Edit ODOO_BASE_URL (and RCLONE_REMOTE if used) in:"
echo "   - ${ENV_DIR}/prod.env"
echo "   - ${ENV_DIR}/test-b.env"
echo "   - ${ENV_DIR}/test-a.env"
echo "3. Start Reverse Proxy + Environments:"
echo "   ./deploy/scripts/deploy.sh proxy up"
echo "   ./deploy/scripts/deploy.sh prod up"
echo "   ./deploy/scripts/deploy.sh test-b up"
echo "   ./deploy/scripts/deploy.sh test-a up"

