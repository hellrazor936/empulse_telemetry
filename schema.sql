CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE battery_soc (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    overall_soc_pct numeric,
    module1_soc_pct numeric,
    module2_soc_pct numeric,
    module3_soc_pct numeric,
    module4_soc_pct numeric,
    module5_soc_pct numeric,
    module6_soc_pct numeric,
    module7_soc_pct numeric,
    pack_voltage_v numeric,
    high_cell_v numeric,
    low_cell_v numeric,
    cell_imbalance_mv numeric,
    min_cell_temp_c numeric,
    max_cell_temp_c numeric,
    bms_firmware_rev text,
    bms_customer_code text
);

CREATE TABLE cell_voltages (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    module1_cell1_v numeric, module1_cell2_v numeric, module1_cell3_v numeric, module1_cell4_v numeric,
    module2_cell1_v numeric, module2_cell2_v numeric, module2_cell3_v numeric, module2_cell4_v numeric,
    module3_cell1_v numeric, module3_cell2_v numeric, module3_cell3_v numeric, module3_cell4_v numeric,
    module4_cell1_v numeric, module4_cell2_v numeric, module4_cell3_v numeric, module4_cell4_v numeric,
    module5_cell1_v numeric, module5_cell2_v numeric, module5_cell3_v numeric, module5_cell4_v numeric,
    module6_cell1_v numeric, module6_cell2_v numeric, module6_cell3_v numeric, module6_cell4_v numeric,
    module7_cell1_v numeric, module7_cell2_v numeric, module7_cell3_v numeric, module7_cell4_v numeric
);

CREATE TABLE drive_telemetry (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    speed_mph numeric,
    rpm integer,
    odometer_mi numeric,
    air_temp_f numeric,
    motor_temp_f numeric,
    throttle_pct numeric
);

CREATE TABLE module_current_temp (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    module1_current_a numeric, module2_current_a numeric, module3_current_a numeric, module4_current_a numeric,
    module5_current_a numeric, module6_current_a numeric, module7_current_a numeric,
    module1_cell_temp_c numeric, module2_cell_temp_c numeric, module3_cell_temp_c numeric, module4_cell_temp_c numeric,
    module5_cell_temp_c numeric, module6_cell_temp_c numeric, module7_cell_temp_c numeric
);

CREATE TABLE module_status (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    module smallint,
    heater_current_ma numeric,
    bq116_rebuilds integer
);

CREATE TABLE other_records (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp,
    code text,
    length integer,
    data_ascii_or_hex text
);

CREATE TABLE status_flags (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    kickstand text,
    kickstand_raw smallint
);

SELECT create_hypertable('battery_soc', 'timestamp');
SELECT create_hypertable('cell_voltages', 'timestamp');
SELECT create_hypertable('drive_telemetry', 'timestamp');
SELECT create_hypertable('module_current_temp', 'timestamp');
SELECT create_hypertable('module_status', 'timestamp');
SELECT create_hypertable('other_records', 'timestamp');
SELECT create_hypertable('status_flags', 'timestamp');

-- Uniqueness lets import.sql use ON CONFLICT DO NOTHING, so it can be re-run safely
-- whenever new CSVs (cumulative or incremental) are dropped into /import.
ALTER TABLE battery_soc ADD CONSTRAINT battery_soc_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE cell_voltages ADD CONSTRAINT cell_voltages_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE drive_telemetry ADD CONSTRAINT drive_telemetry_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE module_current_temp ADD CONSTRAINT module_current_temp_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE module_status ADD CONSTRAINT module_status_uniq UNIQUE (source_file, "timestamp", module);
ALTER TABLE other_records ADD CONSTRAINT other_records_uniq UNIQUE (source_file, "timestamp", code, data_ascii_or_hex);
ALTER TABLE status_flags ADD CONSTRAINT status_flags_uniq UNIQUE (source_file, "timestamp");
