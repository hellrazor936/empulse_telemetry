#!/usr/bin/env bash
# Registers the TimescaleDB datasource in Grafana via its HTTP API. All settings are
# overridable via environment variables so this works both against a Kubernetes Grafana
# and a local `docker compose` one -- defaults below match the docker-compose.yml in this repo.
set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-admin}"
DB_HOST="${DB_HOST:-timescaledb}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-brammo}"
DB_NAME="${DB_NAME:-brammo}"
: "${DB_PASSWORD:?Set DB_PASSWORD to the TimescaleDB password (e.g. the POSTGRES_PASSWORD from docker-compose.yml, or from the brammo-timescaledb-auth k8s secret)}"

curl -s -u "$GRAFANA_USER:$GRAFANA_PASSWORD" -X POST "$GRAFANA_URL/api/datasources" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
ds = {
    'name': 'Empulse R',
    'type': 'grafana-postgresql-datasource',
    'access': 'proxy',
    'url': '$DB_HOST:$DB_PORT',
    'user': '$DB_USER',
    'database': '$DB_NAME',
    'basicAuth': False,
    'isDefault': False,
    'jsonData': {'sslmode': 'disable', 'postgresVersion': 1600, 'timescaledb': True},
    'secureJsonData': {'password': '''$DB_PASSWORD'''},
}
print(json.dumps(ds))
")" | python3 -m json.tool
# Note the "id"/"uid" in the output above -- build_dashboards.py needs the datasource uid
# (GRAFANA_DS_UID) and a folder uid (GRAFANA_FOLDER_UID, create one via POST /api/folders).
