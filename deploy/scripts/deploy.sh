#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
    cat <<EOF
Usage: $0 <target> <action> [args...]

Targets:
  proxy    Reverse proxy (Caddy HTTPS)
  prod     Environment 3: Production (ตัวจริง)
  test-b   Environment 2: Demo / UAT (ทดสอบ B)
  test-a   Environment 1: Team Dev / QA (ทดสอบ A)
  all      All stacks (proxy + prod + test-b + test-a)

Actions:
  up                 Start (and build if needed) containers in detached mode
  down               Stop and remove containers (preserves data volumes)
  restart            Restart containers
  status | ps        Show container status
  logs               Follow container logs
  build              Build Docker image (odoo targets only)
  update <mod,...>   Upgrade Odoo module(s) in DB and restart Odoo
  install <mod,...>  Install Odoo module(s) in DB and restart Odoo
  fix-urls           Lock web.base.url and report.url=http://127.0.0.1:8069 in DB

Examples:
  $0 all up
  $0 prod update thai_billing_reports,custom_invoice_report
  $0 test-b fix-urls
EOF
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

TARGET="$1"
ACTION="$2"
ARG="${3:-}"

ensure_proxy_network() {
    if ! docker network inspect odoo_proxy_net >/dev/null 2>&1; then
        echo "[INFO] Creating external Docker network 'odoo_proxy_net'..."
        docker network create odoo_proxy_net >/dev/null
    fi
}

run_single() {
    local tgt="$1"
    local act="$2"
    local extra="${3:-}"
    local compose_file="${DEPLOY_DIR}/docker-compose.${tgt}.yml"
    local env_file="${DEPLOY_DIR}/env/${tgt}.env"

    if [ ! -f "$compose_file" ]; then
        echo "[ERROR] Compose file not found: $compose_file" >&2
        exit 1
    fi
    if [ ! -f "$env_file" ]; then
        echo "[ERROR] Env file not found: $env_file (run ./deploy/scripts/init-server.sh first)" >&2
        exit 1
    fi

    # Load env vars safely for script helper actions
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a

    local dc=(docker compose --env-file "$env_file" -f "$compose_file")

    case "$act" in
        up)
            ensure_proxy_network
            if [ "$tgt" = "proxy" ]; then
                "${dc[@]}" up -d
            else
                "${dc[@]}" up -d --build
            fi
            ;;
        down)
            "${dc[@]}" down
            ;;
        restart)
            "${dc[@]}" restart
            ;;
        status|ps)
            echo "--- ${tgt} ---"
            "${dc[@]}" ps
            ;;
        logs)
            "${dc[@]}" logs -f --tail=100
            ;;
        build)
            if [ "$tgt" = "proxy" ]; then
                echo "[SKIP] proxy uses prebuilt caddy image."
            else
                "${dc[@]}" build
            fi
            ;;
        update)
            [ "$tgt" = "proxy" ] && { echo "[ERROR] Cannot update modules on proxy." >&2; exit 1; }
            [ -z "$extra" ] && { echo "[ERROR] Specify module name(s) to update." >&2; exit 1; }
            echo "[INFO] Updating module(s) '${extra}' on ${tgt} (DB: ${ODOO_DB_NAME})..."
            "${dc[@]}" exec -T odoo python odoo-bin --config /etc/odoo/odoo.conf --workers=0 --max-cron-threads=0 -d "${ODOO_DB_NAME}" -u "${extra}" --stop-after-init --no-http
            "${dc[@]}" restart odoo
            ;;
        install)
            [ "$tgt" = "proxy" ] && { echo "[ERROR] Cannot install modules on proxy." >&2; exit 1; }
            [ -z "$extra" ] && { echo "[ERROR] Specify module name(s) to install." >&2; exit 1; }
            echo "[INFO] Installing module(s) '${extra}' on ${tgt} (DB: ${ODOO_DB_NAME})..."
            "${dc[@]}" exec -T odoo python odoo-bin --config /etc/odoo/odoo.conf --workers=0 --max-cron-threads=0 -d "${ODOO_DB_NAME}" -i "${extra}" --stop-after-init --no-http
            "${dc[@]}" restart odoo
            ;;
        fix-urls)
            [ "$tgt" = "proxy" ] && { echo "[ERROR] Not applicable to proxy." >&2; exit 1; }
            echo "[INFO] Setting web.base.url=${ODOO_BASE_URL} and report.url=http://127.0.0.1:8069 on ${ODOO_DB_NAME}..."
            docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD}" "${DB_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${ODOO_DB_NAME}" <<SQL
INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
VALUES
    ('web.base.url', '${ODOO_BASE_URL}', 1, 1, NOW(), NOW()),
    ('web.base.url.freeze', 'True', 1, 1, NOW(), NOW()),
    ('report.url', 'http://127.0.0.1:8069', 1, 1, NOW(), NOW())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value, write_date = NOW(), write_uid = 1;
SQL
            "${dc[@]}" restart odoo
            ;;
        *)
            usage
            ;;
    esac
}

if [ "$TARGET" = "all" ]; then
    case "$ACTION" in
        up)
            ensure_proxy_network
            run_single prod build
            for t in proxy prod test-b test-a; do
                run_single "$t" up
            done
            ;;
        down)
            for t in test-a test-b prod proxy; do
                run_single "$t" down
            done
            ;;
        restart|status|ps|build)
            for t in proxy prod test-b test-a; do
                run_single "$t" "$ACTION"
            done
            ;;
        *)
            echo "[ERROR] Action '${ACTION}' is not supported with target 'all'." >&2
            usage
            ;;
    esac
else
    case "$TARGET" in
        proxy|prod|test-b|test-a)
            run_single "$TARGET" "$ACTION" "$ARG"
            ;;
        *)
            usage
            ;;
    esac
fi

