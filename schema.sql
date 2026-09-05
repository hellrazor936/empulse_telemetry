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
    bms_customer_code text,
    -- Byte 31 of the B-record ("Module Intrabalance Active" bitmask), decoded per module.
    -- Reverse-engineered against an official-tool decode (see decode_empulse_logs.py).
    module1_intrabalance_active smallint,
    module2_intrabalance_active smallint,
    module3_intrabalance_active smallint,
    module4_intrabalance_active smallint,
    module5_intrabalance_active smallint,
    module6_intrabalance_active smallint,
    module7_intrabalance_active smallint,
    -- Byte 10 bit 3 of the B-record -- a pack-level fault flag, verified 100%
    -- (16968/16968 samples, 28 sessions) against the official tool's "Fault List" bit 34
    -- and "BMS Faults" bit 4 (same condition, mirrored in both). Meaning unconfirmed --
    -- no text description available, and it never coincides with an S56 mc_fault_code
    -- event, so it's an unrelated condition.
    bms_fault_flag smallint
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
    throttle_pct numeric,
    -- D-record bytes 4, 5-6, 15-16 -- reverse-engineered against official-tool reference
    -- decodes, verified 100% (0/4580 mismatches across 3 sessions). motor_power_kw is
    -- derived (voltage * current), not itself a separate logged field.
    motor_voltage_vrms numeric,
    motor_current_arms numeric,
    motor_power_kw numeric,
    estimated_range_mi numeric,
    -- D-record byte 7 -- Sevcon motor controller fault code, verified 100% (0/10214
    -- mismatches across 19 sessions). Only value confirmed against reference decodes is
    -- 56 ("S56: SEVCON -- 0x45c9 Motor low voltage"); other nonzero values are raw/unconfirmed.
    mc_fault_code smallint
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

-- E-record byte 2, verified 100% (10195/10195 samples, 19 sessions) against the official
-- tool's "AIM Gear Selected" and "AIM Status Bits" columns, one frame offset between
-- exports (same quirk as several B-record fields). See decode_empulse_logs.py.
CREATE TABLE gear_status (
    source_file text NOT NULL,
    session_type text NOT NULL,
    "timestamp" timestamp NOT NULL,
    gear smallint,
    side_stand_up smallint,
    start_pressed smallint,
    brake_applied smallint
);

SELECT create_hypertable('battery_soc', 'timestamp');
SELECT create_hypertable('cell_voltages', 'timestamp');
SELECT create_hypertable('drive_telemetry', 'timestamp');
SELECT create_hypertable('module_current_temp', 'timestamp');
SELECT create_hypertable('module_status', 'timestamp');
SELECT create_hypertable('other_records', 'timestamp');
SELECT create_hypertable('status_flags', 'timestamp');
SELECT create_hypertable('gear_status', 'timestamp');

-- Uniqueness lets import.sql use ON CONFLICT DO NOTHING, so it can be re-run safely
-- whenever new CSVs (cumulative or incremental) are dropped into /import.
ALTER TABLE battery_soc ADD CONSTRAINT battery_soc_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE cell_voltages ADD CONSTRAINT cell_voltages_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE drive_telemetry ADD CONSTRAINT drive_telemetry_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE module_current_temp ADD CONSTRAINT module_current_temp_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE module_status ADD CONSTRAINT module_status_uniq UNIQUE (source_file, "timestamp", module);
ALTER TABLE other_records ADD CONSTRAINT other_records_uniq UNIQUE (source_file, "timestamp", code, data_ascii_or_hex);
ALTER TABLE status_flags ADD CONSTRAINT status_flags_uniq UNIQUE (source_file, "timestamp");
ALTER TABLE gear_status ADD CONSTRAINT gear_status_uniq UNIQUE (source_file, "timestamp");
