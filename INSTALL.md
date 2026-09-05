# Installation

## What's here

| File | Purpose |
|---|---|
| `decode_empulse_logs.py` | Decodes `.DRV`/`.CHG` binary logs into 7 CSV files. Format reverse-engineered by Richard Champalbert (FreepZ) and Jim Graham (flar), recovered from the archived [enspector](https://bitbucket.org/freepz/enspector) project. |
| `namespace.yaml` | Kubernetes namespace (`brammo`). |
| `timescaledb.yaml` | StatefulSet + Service for TimescaleDB (`timescale/timescaledb:2.29.2-pg16`, arm64-compatible). |
| `schema.sql` | One-time schema setup: 8 raw hypertables + unique constraints (so imports can be re-run safely). |
| `import.sql` | Idempotent bulk loader: `\copy`s the 8 CSVs into staging tables, dedupes, `INSERT ... ON CONFLICT DO NOTHING`s into the hypertables, then rebuilds all derived tables. |
| `grafana/create_datasource.sh` | Registers the Postgres/TimescaleDB datasource in Grafana via its HTTP API. |
| `grafana/build_dashboards.py` | Generates and posts 4 Grafana dashboards via the Grafana API. |
| `docker-compose.yml` | TimescaleDB + Grafana, for running all of this without Kubernetes. |

## Data model

Eight raw hypertables, one row per logged frame, all keyed on `(source_file, timestamp[, module])`:

- `battery_soc` -- overall + per-module SoC%, pack voltage, high/low cell voltage, cell imbalance, per-module intra-balancing active flag, BMS fault flag
- `cell_voltages` -- all 28 individual cell voltages (7 modules x 4 cells)
- `drive_telemetry` -- speed, RPM, odometer, air/motor temp, throttle, motor voltage/current/power, estimated range, Sevcon motor controller fault code
- `module_current_temp` -- per-module current and cell temperature
- `module_status` -- per-module heater current, BMS chip rebuild count
- `other_records` -- raw diagnostic/event codes that don't fit the above
- `status_flags` -- kickstand state
- `gear_status` -- selected gear (0=neutral, 1-6), side stand/start button/brake status

Plus derived tables, rebuilt on every `import.sql` run:

- `sessions` -- one row per drive/charge (start/end time, distance, speed, SoC range)
- `charge_capacity_estimates` -- Coulomb-counted + energy-integrated pack capacity per charge, for charges with a >=30% SoC swing and no mid-charge SoC glitch
- `module_soc_spread` -- widest gap between the strongest and weakest module's SoC per session (diagnostic for weak-module strandings)
- `eoc_cell_imbalance` -- cell voltage imbalance right after charges that reached >=95% SoC
- `long_idle_periods` -- gaps of 30+ days between sessions, with the SoC the bike was left at
- `drive_range_estimates` -- per-drive distance vs. SoC used, plus average speed and ambient temperature, for range/efficiency analysis

## Running with Docker

The simplest way to try this out -- no Kubernetes needed. Requires Docker and Docker Compose.

**1. Start TimescaleDB and Grafana:**

```bash
echo "DB_PASSWORD=$(openssl rand -base64 24)" > .env
docker compose up -d
```

**2. Create the schema:**

```bash
docker compose cp schema.sql timescaledb:/tmp/schema.sql
docker compose exec timescaledb psql -U brammo -d brammo -f /tmp/schema.sql
```

**3. Decode your log files and load them:**

```bash
python3 decode_empulse_logs.py /path/to/DRV_CHG_files ./LOGS_csv

docker compose exec timescaledb mkdir -p /import
for f in battery_soc cell_voltages drive_telemetry module_current_temp module_status other_records status_flags gear_status; do
  docker compose cp "./LOGS_csv/$f.csv" "timescaledb:/import/$f.csv"
done
docker compose cp import.sql timescaledb:/tmp/import.sql
docker compose exec timescaledb psql -U brammo -d brammo -f /tmp/import.sql
```

`import.sql` is safe to re-run any time you have new logs -- it only inserts rows that aren't
already there, whether the CSV export is cumulative (old + new sessions) or contains only the
new sessions.

**4. Set up Grafana** (http://localhost:3000, login `admin` / `admin` unless you set
`GRAFANA_PASSWORD`):

```bash
# Register the datasource -- prints its "uid" in the response, note it down
source .env
DB_PASSWORD="$DB_PASSWORD" bash grafana/create_datasource.sh

# Create a folder for the dashboards -- prints its "uid" too
curl -s -u admin:admin -X POST http://localhost:3000/api/folders \
  -H "Content-Type: application/json" -d '{"title":"Empulse R"}'

# Generate and post the 4 dashboards, using the two uids from above
GRAFANA_DS_UID=<datasource_uid> GRAFANA_FOLDER_UID=<folder_uid> python3 grafana/build_dashboards.py \
  | while read -r line; do
      echo "$line" | curl -s -u admin:admin -X POST http://localhost:3000/api/dashboards/db \
        -H "Content-Type: application/json" -d @-
    done
```

That's it -- the dashboards will be under the "Empulse R" folder in Grafana.

By default all dashboards show metric units (km/h, km, Celsius) -- the bike itself logs in miles
and Fahrenheit, and the conversion happens in `build_dashboards.py`. Set `UNITS=imperial` before
running the script to generate dashboards in the bike's native units instead:

```bash
UNITS=imperial python3 grafana/build_dashboards.py | ...
```

This is a build-time choice, not a live in-Grafana toggle -- Grafana's field "unit" is a display
label, not a live converter, so switching means regenerating and redeploying. `grafana/dashboards/*.json`
always reflects whichever `UNITS` value was used for the last regeneration.

## Setting it up on Kubernetes

```bash
kubectl apply -f namespace.yaml
kubectl -n brammo create secret generic brammo-timescaledb-auth --from-literal=password=<password>
kubectl apply -f timescaledb.yaml
kubectl -n brammo exec <pod> -- psql -U brammo -d brammo -f schema.sql
```

Loading logs works the same way as the Docker instructions above, just swap `docker compose cp` /
`docker compose exec timescaledb` for `kubectl -n brammo cp` / `kubectl -n brammo exec <pod> --`.

Registering the Grafana datasource and dashboards also works the same way -- point `GRAFANA_URL`,
`GRAFANA_USER`/`GRAFANA_PASSWORD`, and `DB_HOST` (the TimescaleDB service's DNS name, e.g.
`brammo-timescaledb.brammo.svc.cluster.local`) at your cluster's Grafana and TimescaleDB service
instead of localhost.
