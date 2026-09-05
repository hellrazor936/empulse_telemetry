-- Idempotent import: safe to re-run whenever new/updated CSVs are dropped into /import.
-- Existing (source_file, timestamp[, module]) rows are skipped via ON CONFLICT DO NOTHING,
-- so a cumulative export (old + new sessions) or an export containing only new sessions
-- both work without creating duplicates.
\timing on

CREATE TEMP TABLE stg_battery_soc (LIKE battery_soc INCLUDING DEFAULTS);
CREATE TEMP TABLE stg_cell_voltages (LIKE cell_voltages INCLUDING DEFAULTS);
CREATE TEMP TABLE stg_drive_telemetry (LIKE drive_telemetry INCLUDING DEFAULTS);
CREATE TEMP TABLE stg_module_current_temp (LIKE module_current_temp INCLUDING DEFAULTS);
CREATE TEMP TABLE stg_module_status (LIKE module_status INCLUDING DEFAULTS);
CREATE TEMP TABLE stg_other_records (LIKE other_records INCLUDING DEFAULTS);
-- other_records legitimately has rows with a NULL timestamp (unparseable frames) that get
-- filtered out below; create_hypertable() force-added NOT NULL on the real table's timestamp
-- column, which INCLUDING DEFAULTS would otherwise propagate here and abort the whole COPY
-- batch on the first NULL row instead of just skipping it.
ALTER TABLE stg_other_records ALTER COLUMN "timestamp" DROP NOT NULL;
CREATE TEMP TABLE stg_status_flags (LIKE status_flags INCLUDING DEFAULTS);
CREATE TEMP TABLE stg_gear_status (LIKE gear_status INCLUDING DEFAULTS);

\copy stg_battery_soc FROM '/import/battery_soc.csv' WITH (FORMAT csv, HEADER true)
\copy stg_cell_voltages FROM '/import/cell_voltages.csv' WITH (FORMAT csv, HEADER true)
\copy stg_drive_telemetry FROM '/import/drive_telemetry.csv' WITH (FORMAT csv, HEADER true)
\copy stg_module_current_temp FROM '/import/module_current_temp.csv' WITH (FORMAT csv, HEADER true)
\copy stg_module_status FROM '/import/module_status.csv' WITH (FORMAT csv, HEADER true)
\copy stg_other_records FROM '/import/other_records.csv' WITH (FORMAT csv, HEADER true)
\copy stg_status_flags FROM '/import/status_flags.csv' WITH (FORMAT csv, HEADER true)
\copy stg_gear_status FROM '/import/gear_status.csv' WITH (FORMAT csv, HEADER true)

-- A handful of raw frames share an identical (source_file, timestamp[, module]) key (logger
-- clock glitch bursts). Dedupe within the staging table (a plain, single-relation temp table,
-- so ctid is safe here) *before* inserting into the hypertable -- ctid is only unique per
-- chunk on a hypertable, so a dedupe done against the hypertable itself can silently delete
-- unrelated rows from other chunks.
DELETE FROM stg_battery_soc a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp" ORDER BY ctid) rn FROM stg_battery_soc
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_cell_voltages a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp" ORDER BY ctid) rn FROM stg_cell_voltages
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_drive_telemetry a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp" ORDER BY ctid) rn FROM stg_drive_telemetry
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_module_current_temp a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp" ORDER BY ctid) rn FROM stg_module_current_temp
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_module_status a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp", module ORDER BY ctid) rn FROM stg_module_status
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_other_records a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp", code, data_ascii_or_hex ORDER BY ctid) rn FROM stg_other_records
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_status_flags a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp" ORDER BY ctid) rn FROM stg_status_flags
) t WHERE a.ctid = t.ctid AND t.rn > 1;
DELETE FROM stg_gear_status a USING (
  SELECT ctid, row_number() OVER (PARTITION BY source_file, "timestamp" ORDER BY ctid) rn FROM stg_gear_status
) t WHERE a.ctid = t.ctid AND t.rn > 1;

INSERT INTO battery_soc SELECT * FROM stg_battery_soc ON CONFLICT DO NOTHING;
INSERT INTO cell_voltages SELECT * FROM stg_cell_voltages ON CONFLICT DO NOTHING;
INSERT INTO drive_telemetry SELECT * FROM stg_drive_telemetry ON CONFLICT DO NOTHING;
INSERT INTO module_current_temp SELECT * FROM stg_module_current_temp ON CONFLICT DO NOTHING;
INSERT INTO module_status SELECT * FROM stg_module_status ON CONFLICT DO NOTHING;
INSERT INTO other_records SELECT * FROM stg_other_records WHERE "timestamp" IS NOT NULL ON CONFLICT DO NOTHING;
INSERT INTO status_flags SELECT * FROM stg_status_flags ON CONFLICT DO NOTHING;
INSERT INTO gear_status SELECT * FROM stg_gear_status ON CONFLICT DO NOTHING;

-- Rebuild derived tables from scratch (cheap: ~thousands of rows, not the raw telemetry).
DROP TABLE IF EXISTS sessions;
CREATE TABLE sessions AS
SELECT
    source_file,
    session_type,
    min("timestamp") AS started_at,
    max("timestamp") AS ended_at,
    max("timestamp") - min("timestamp") AS duration,
    count(*) AS sample_count
FROM drive_telemetry
GROUP BY source_file, session_type
ORDER BY min("timestamp");

ALTER TABLE sessions ADD PRIMARY KEY (source_file);
ALTER TABLE sessions ADD COLUMN max_speed_mph numeric;
ALTER TABLE sessions ADD COLUMN odometer_start_mi numeric;
ALTER TABLE sessions ADD COLUMN odometer_end_mi numeric;
ALTER TABLE sessions ADD COLUMN min_soc_pct numeric;
ALTER TABLE sessions ADD COLUMN max_soc_pct numeric;

WITH agg AS (
    SELECT source_file,
           max(speed_mph) AS max_speed_mph,
           min(odometer_mi) FILTER (WHERE odometer_mi > 0) AS odometer_start_mi,
           max(odometer_mi) AS odometer_end_mi
    FROM drive_telemetry
    GROUP BY source_file
)
UPDATE sessions s SET
    max_speed_mph = agg.max_speed_mph,
    odometer_start_mi = agg.odometer_start_mi,
    odometer_end_mi = agg.odometer_end_mi
FROM agg WHERE s.source_file = agg.source_file;

WITH agg AS (
    SELECT source_file, min(overall_soc_pct) AS min_soc_pct, max(overall_soc_pct) AS max_soc_pct
    FROM battery_soc
    GROUP BY source_file
)
UPDATE sessions s SET min_soc_pct = agg.min_soc_pct, max_soc_pct = agg.max_soc_pct
FROM agg WHERE s.source_file = agg.source_file;

-- Estimated pack capacity per charge session via direct energy integration: trapezoidal
-- integration of instantaneous power (7-module-averaged pack current x pack voltage) between
-- the charge's start/end SoC readings, scaled to a full 0-100% swing. This integrates V*I at
-- each sample rather than deriving Wh from Ah x avg(V) -- the latter is biased whenever
-- voltage and current are correlated over the session (e.g. CC/CV charging: most of the Ah
-- is delivered during the lower-voltage CC phase, so a simple time-average of voltage over-
-- weights the high-voltage CV tail). charged_ah (Coulomb count) is kept alongside as a
-- cross-check -- it does not depend on the voltage-weighting question at all.
-- soc_start/soc_end use the FIRST/LAST reading by time (DISTINCT ON), not min()/max() over
-- the session, so a transient SoC glitch mid-charge (BMS resync, sensor dropout) can't widen
-- the apparent delta. Sessions are additionally dropped if any single sample-to-sample SoC
-- step exceeds 5 points (implausible for real charging -> treated as an unreliable reading),
-- or if the net SoC swing is < 30 points (too little signal for a stable estimate).
DROP TABLE IF EXISTS charge_capacity_estimates;
CREATE TABLE charge_capacity_estimates AS
WITH readings AS (
    SELECT
        m.source_file,
        m.timestamp,
        (m.module1_current_a + m.module2_current_a + m.module3_current_a + m.module4_current_a
         + m.module5_current_a + m.module6_current_a + m.module7_current_a) / 7.0 AS avg_current_a,
        b.overall_soc_pct,
        b.pack_voltage_v
    FROM module_current_temp m
    JOIN battery_soc b USING (source_file, "timestamp")
    JOIN sessions s USING (source_file)
    WHERE s.session_type = 'charge'
),
integrated AS (
    SELECT
        source_file, "timestamp", overall_soc_pct, pack_voltage_v,
        EXTRACT(EPOCH FROM ("timestamp" - LAG("timestamp") OVER w)) AS dt_s,
        (avg_current_a + LAG(avg_current_a) OVER w) / 2.0 AS trapz_current_a,
        (avg_current_a * pack_voltage_v + LAG(avg_current_a * pack_voltage_v) OVER w) / 2.0 AS trapz_power_w,
        overall_soc_pct - LAG(overall_soc_pct) OVER w AS soc_step
    FROM readings
    WINDOW w AS (PARTITION BY source_file ORDER BY "timestamp")
),
per_session AS (
    SELECT
        source_file,
        min("timestamp") AS started_at,
        sum(COALESCE(trapz_current_a * dt_s, 0)) / 3600.0 AS charged_ah,
        sum(COALESCE(trapz_power_w * dt_s, 0)) / 3600.0 AS charged_wh,
        max(abs(soc_step)) AS max_abs_soc_step
    FROM integrated
    GROUP BY source_file
),
soc_start AS (
    SELECT DISTINCT ON (source_file) source_file, overall_soc_pct AS soc_start
    FROM readings ORDER BY source_file, "timestamp" ASC
),
soc_end AS (
    SELECT DISTINCT ON (source_file) source_file, overall_soc_pct AS soc_end
    FROM readings ORDER BY source_file, "timestamp" DESC
)
SELECT
    p.source_file,
    p.started_at,
    p.charged_ah,
    p.charged_wh,
    ss.soc_start,
    se.soc_end,
    (se.soc_end - ss.soc_start) AS soc_delta,
    p.charged_ah / ((se.soc_end - ss.soc_start) / 100.0) AS estimated_capacity_ah,
    p.charged_wh / ((se.soc_end - ss.soc_start) / 100.0) AS estimated_capacity_wh
FROM per_session p
JOIN soc_start ss USING (source_file)
JOIN soc_end se USING (source_file)
WHERE (se.soc_end - ss.soc_start) >= 30
  AND p.max_abs_soc_step <= 5
ORDER BY p.started_at;

ALTER TABLE charge_capacity_estimates ADD PRIMARY KEY (source_file);

-- Idle/storage periods (gap >= 30 days between the end of one session and the start of the
-- next) with the SoC the bike was left sitting at -- used to annotate the capacity chart,
-- since high-SoC long storage is a known accelerant of lithium cell aging.
DROP TABLE IF EXISTS long_idle_periods;
CREATE TABLE long_idle_periods AS
WITH ordered AS (
    SELECT source_file, session_type, ended_at, max_soc_pct, min_soc_pct,
           lead(started_at) OVER (ORDER BY started_at) AS next_started_at
    FROM sessions
)
SELECT
    source_file,
    ended_at AS idle_from,
    next_started_at AS idle_until,
    CASE WHEN session_type = 'charge' THEN max_soc_pct ELSE min_soc_pct END AS soc_at_standstill,
    extract(epoch FROM (next_started_at - ended_at)) / 86400.0 AS idle_days
FROM ordered
WHERE next_started_at - ended_at >= interval '30 days'
ORDER BY idle_from;

-- Per-session, per-module fraction of samples where that module's cells were actively
-- intra-balancing (module1..7_intrabalance_active in battery_soc, decoded from the B-record's
-- byte 31 bitmask). Ties into the module-imbalance investigation: do the modules that turned
-- out weaker (wider SoC spread, earlier stranding) also balance more often?
DROP TABLE IF EXISTS module_balancing_summary;
CREATE TABLE module_balancing_summary AS
SELECT
    source_file,
    session_type,
    min("timestamp") AS started_at,
    count(*) AS sample_count,
    avg(module1_intrabalance_active) AS module1_balance_frac,
    avg(module2_intrabalance_active) AS module2_balance_frac,
    avg(module3_intrabalance_active) AS module3_balance_frac,
    avg(module4_intrabalance_active) AS module4_balance_frac,
    avg(module5_intrabalance_active) AS module5_balance_frac,
    avg(module6_intrabalance_active) AS module6_balance_frac,
    avg(module7_intrabalance_active) AS module7_balance_frac
FROM battery_soc
GROUP BY source_file, session_type
ORDER BY min("timestamp");

-- Module-to-module SoC spread per session: the widest gap between the strongest and weakest
-- of the 7 modules' own SoC% at any point in the session. Found via a 2024 stranding
-- investigation that overall_soc_pct can read misleadingly low (e.g. 8.6%) while some modules
-- are actually near-empty (0%) and others still hold real charge (~15%) -- i.e. a module
-- imbalance/weak-module problem, not a fully depleted pack. Tracked over time to see whether
-- the imbalance is worsening.
DROP TABLE IF EXISTS module_soc_spread;
CREATE TABLE module_soc_spread AS
SELECT
    source_file,
    session_type,
    min("timestamp") AS started_at,
    max(GREATEST(module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct,
                  module5_soc_pct, module6_soc_pct, module7_soc_pct)
        - LEAST(module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct,
                module5_soc_pct, module6_soc_pct, module7_soc_pct)) AS max_module_spread_pct,
    min(overall_soc_pct) AS min_overall_soc_pct
FROM battery_soc
GROUP BY source_file, session_type
ORDER BY min("timestamp");

-- End-of-charge cell voltage imbalance (only for charges that actually reached >=95% SoC --
-- the moment balancing differences are most visible) per session, to track pack balance health
-- over calendar time independent of the module-SoC-estimate quirks above.
DROP TABLE IF EXISTS eoc_cell_imbalance;
CREATE TABLE eoc_cell_imbalance AS
SELECT DISTINCT ON (b.source_file)
    b.source_file,
    b.timestamp AS ended_at,
    b.cell_imbalance_mv,
    b.high_cell_v,
    b.low_cell_v
FROM battery_soc b
JOIN sessions s USING (source_file)
WHERE s.session_type = 'charge' AND s.max_soc_pct >= 95
ORDER BY b.source_file, b.timestamp DESC;

-- Per-drive distance vs. SoC consumed, for estimating real-world range at 100% SoC. Uses
-- FIRST/LAST SoC by time (not min/max) for the same reason as charge_capacity_estimates --
-- robust against a transient SoC glitch. Only keeps drives with a meaningful distance (>=5km)
-- and SoC drop (>=5 points) to avoid short-trip noise dominating the estimate.
-- avg_speed_kmh and avg_air_temp_c are carried along so the Efficiency dashboard can break
-- range down by riding speed and ambient temperature without expensive live joins per panel --
-- both turned out to matter: deep-depletion drives (>=30% SoC used) average ~55 km/h vs ~37
-- km/h for short trips (aerodynamic drag ~v^2, plus less regen opportunity than stop-start
-- city riding), and cool weather (5-15C) shows ~8% less range than 15-25C at the same speed.
DROP TABLE IF EXISTS drive_range_estimates;
CREATE TABLE drive_range_estimates AS
WITH drive_soc AS (
    SELECT
        s.source_file,
        s.started_at,
        s.duration,
        (s.odometer_end_mi - s.odometer_start_mi) * 1.609344 AS distance_km,
        (SELECT overall_soc_pct FROM battery_soc b WHERE b.source_file = s.source_file ORDER BY b."timestamp" ASC LIMIT 1) AS soc_start,
        (SELECT overall_soc_pct FROM battery_soc b WHERE b.source_file = s.source_file ORDER BY b."timestamp" DESC LIMIT 1) AS soc_end,
        (SELECT avg((d.air_temp_f - 32) * 5.0/9.0) FROM drive_telemetry d WHERE d.source_file = s.source_file) AS avg_air_temp_c
    FROM sessions s
    WHERE s.session_type = 'drive'
      AND s.odometer_end_mi IS NOT NULL AND s.odometer_start_mi IS NOT NULL
)
SELECT
    source_file,
    started_at,
    distance_km,
    (soc_start - soc_end) AS soc_used_pct,
    distance_km / (soc_start - soc_end) * 100 AS range_at_100pct_km,
    distance_km / NULLIF(extract(epoch from duration)/3600.0, 0) AS avg_speed_kmh,
    avg_air_temp_c
FROM drive_soc
WHERE distance_km >= 5 AND (soc_start - soc_end) >= 5
ORDER BY started_at;

-- sanity check counts
SELECT 'battery_soc' t, count(*) FROM battery_soc
UNION ALL SELECT 'cell_voltages', count(*) FROM cell_voltages
UNION ALL SELECT 'drive_telemetry', count(*) FROM drive_telemetry
UNION ALL SELECT 'module_current_temp', count(*) FROM module_current_temp
UNION ALL SELECT 'module_status', count(*) FROM module_status
UNION ALL SELECT 'other_records', count(*) FROM other_records
UNION ALL SELECT 'status_flags', count(*) FROM status_flags
UNION ALL SELECT 'gear_status', count(*) FROM gear_status
UNION ALL SELECT 'sessions', count(*) FROM sessions
UNION ALL SELECT 'charge_capacity_estimates', count(*) FROM charge_capacity_estimates
UNION ALL SELECT 'long_idle_periods', count(*) FROM long_idle_periods
UNION ALL SELECT 'module_soc_spread', count(*) FROM module_soc_spread
UNION ALL SELECT 'module_balancing_summary', count(*) FROM module_balancing_summary
UNION ALL SELECT 'eoc_cell_imbalance', count(*) FROM eoc_cell_imbalance
UNION ALL SELECT 'drive_range_estimates', count(*) FROM drive_range_estimates;
