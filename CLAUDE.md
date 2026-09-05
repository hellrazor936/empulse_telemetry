# Claude Code Context: empulse_telemetry

TimescaleDB pipeline + Grafana dashboards for the telemetry logs (`.DRV`/`.CHG`)
of a 2014 Brammo Empulse R electric motorcycle.

## Required reading before any code change

The full overview lives in `README.md` (data model, dashboards, Docker and
Kubernetes setup). Additionally, **before writing a single line of SQL or
Python**:

1. The comments at the top of every `DROP TABLE ... / CREATE TABLE ... AS`
   block in `import.sql` -- they document *why* a calculation is done the way
   it is (e.g. FIRST/LAST instead of MIN/MAX for SoC start/end, Coulomb- vs.
   energy-integration for the capacity estimate, the 5-point glitch threshold
   for SoC jumps). Don't simplify anything there without reading the
   reasoning first.
2. The ctid/hypertable gotcha (see the "staging dedupe" section in
   `import.sql`): `ctid` is only unique *per chunk* on a TimescaleDB
   hypertable -- a `DELETE`/`UPDATE` joined on `ctid` run directly against a
   hypertable can hit the wrong rows across chunks (this actually happened
   once here: ~15k rows deleted instead of ~100). Always dedupe on the
   staging table (a plain, single-relation table), never on the hypertable
   itself.
3. `schema.sql` vs. `import.sql`: the former creates the hypertables + unique
   constraints once, the latter is the repeatable, idempotent loader
   (`ON CONFLICT DO NOTHING`). A new raw-data table needs entries in **both**
   files, or the next reimport breaks.

## Important boundaries

- **Never commit raw data.** CSVs and `.DRV`/`.CHG` files never belong in
  this repo -- only schema, import logic, and dashboard definitions.
- **Never commit secrets.** DB passwords / Grafana credentials always go
  through environment variables (`DB_PASSWORD`, `GRAFANA_PASSWORD`, ...) --
  never check in a real default value in `docker-compose.yml`,
  `create_datasource.sh`, or `build_dashboards.py`.
- **Don't assert an absolute nominal capacity.** The Empulse R pack's factory
  spec isn't reliably verified. `charge_capacity_estimates` produces a
  **relative** degradation curve, additionally scaled in the dashboard to an
  assumed 10 kWh starting value. Always label absolute kWh figures as an
  estimate/assumption, never as a factory fact.
- **Don't linearly extrapolate range/efficiency numbers naively.** Short
  drives systematically overestimate range at 100% SoC (see
  `drive_range_estimates` / the Efficiency dashboard: efficiency depends
  noticeably on depletion depth, riding speed, and ambient temperature). Any
  new range estimate should be bucketed accordingly, not averaged flat
  across all drives.
- **Every new derived table must be reproducible**: `DROP TABLE IF EXISTS`
  + `CREATE TABLE ... AS` at the end of `import.sql`, so a re-run rebuilds it
  cleanly. Don't leave manual, non-repeatable one-off fixes in the script.
- **Dashboard changes always go through `grafana/build_dashboards.py`**, not
  just made in the Grafana UI and left there -- otherwise the UI state and
  the committed code drift apart.
