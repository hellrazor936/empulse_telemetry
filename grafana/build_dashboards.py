import json

DS_UID = "bfx97xdyvr6dce"
FOLDER_UID = "bfx97yq6k77y8d"

UID_SESSIONS = "empulse-r-sessions"
UID_DRIVE = "empulse-r-session-details"
UID_CHARGE = "empulse-r-charge-details"
UID_EFFICIENCY = "empulse-r-efficiency"

ds = {"type": "grafana-postgresql-datasource", "uid": DS_UID}


def sql_target(rawSql, refId="A", fmt="time_series"):
    return {"datasource": ds, "rawSql": rawSql, "format": fmt, "refId": refId, "editorMode": "code"}


def ts_panel(id, title, x, y, w, h, sql, unit=None, overrides=None):
    p = {
        "id": id, "type": "timeseries", "title": title, "datasource": ds,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [sql_target(sql)],
        "fieldConfig": {"defaults": {"custom": {"spanNulls": True}}, "overrides": overrides or []},
        # tooltip mode "multi" -- show every series' value in the tooltip, not just the
        # one nearest the cursor.
        "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi", "sort": "none"}},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    return p


def stat_panel(id, title, x, y, w, h, sql, unit=None, noValue="n/a"):
    p = {
        "id": id, "type": "stat", "title": title, "datasource": ds,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [sql_target(sql, fmt="table")],
        "fieldConfig": {"defaults": {"noValue": noValue}, "overrides": []},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    return p


def table_panel(id, title, x, y, w, h, sql, overrides=None):
    return {
        "id": id, "type": "table", "title": title, "datasource": ds,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [sql_target(sql, fmt="table")],
        "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": overrides or []},
        "options": {"showHeader": True},
    }


def heatmap_panel(id, title, x, y, w, h, sql, unit="volt", color_min=None, color_max=None):
    color = {"mode": "scheme", "scheme": "RdYlGn", "reverse": True, "steps": 64}
    if color_min is not None:
        color["min"] = color_min
    if color_max is not None:
        color["max"] = color_max
    return {
        "id": id, "type": "heatmap", "title": title, "datasource": ds,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [sql_target(sql)],
        "options": {
            "calculate": False,
            "color": color,
            "yAxis": {"unit": unit},
            "cellGap": 1,
        },
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
    }


def dashboard(uid, title, panels, variables, time_from="now-10y", annotations=None):
    d = {
        # "utc" (not "browser"): our timestamp columns are timezone-naive local (bike clock)
        # values. The postgres driver treats them as UTC internally; with timezone="browser"
        # Grafana then converts that to the viewer's local zone (e.g. +2h in CEST), showing a
        # shifted time. "utc" renders the value with no conversion, matching the raw data.
        "uid": uid, "title": title, "tags": ["brammo"], "timezone": "utc",
        "schemaVersion": 39, "version": 1,
        # graphTooltip 2 = shared crosshair + shared tooltip (values from all panels)
        # across the whole dashboard.
        "graphTooltip": 2,
        "time": {"from": time_from, "to": "now"},
        "templating": {"list": variables},
        "panels": panels,
    }
    if annotations:
        d["annotations"] = {"list": annotations}
    return d


IDLE_PERIODS_ANNOTATION = {
    "name": "Long idle / storage periods (>=30d)",
    "datasource": ds,
    "enable": True,
    "iconColor": "orange",
    "target": {
        "rawSql": (
            "SELECT idle_from AS time, idle_until AS timeEnd, "
            "'Idle ' || round(idle_days) || 'd at ' || round(soc_at_standstill,0) || '% SoC' AS text "
            "FROM long_idle_periods ORDER BY idle_from"
        ),
        "format": "table",
    },
}


def session_var(name, session_type):
    return {
        "name": name, "type": "query", "datasource": ds,
        "query": {
            "rawSql": (
                "SELECT source_file AS __value, source_file || ' -- ' || "
                "to_char(started_at,'YYYY-MM-DD HH24:MI') || ' (' || "
                "round(extract(epoch from duration)/60) || ' min)' AS __text "
                f"FROM sessions WHERE session_type = '{session_type}' ORDER BY started_at DESC"
            ),
            "format": "table",
        },
        "refresh": 1, "sort": 0, "includeAll": False, "multi": False, "current": {},
    }


CELL_COLS = ", ".join(
    f"module{m}_cell{c}_v" for m in range(1, 8) for c in range(1, 5)
)

# ---------------------------------------------------------------- Dashboard 1: Sessions overview
sessions_panels = []

sessions_panels.append(stat_panel(1, "Drive Sessions", 0, 0, 4, 4,
    "SELECT count(*) FROM sessions WHERE session_type = 'drive'"))
sessions_panels.append(stat_panel(2, "Charge Sessions", 4, 0, 4, 4,
    "SELECT count(*) FROM sessions WHERE session_type = 'charge'"))
sessions_panels.append(stat_panel(3, "Total Distance", 8, 0, 4, 4,
    "SELECT round(sum(odometer_end_mi - odometer_start_mi) * 1.609344) FROM sessions WHERE session_type = 'drive'", unit="suffix:km"))
sessions_panels.append(stat_panel(4, "Total Ride Time", 12, 0, 4, 4,
    "SELECT round(sum(extract(epoch from duration))/3600,0) FROM sessions WHERE session_type = 'drive'", unit="h"))
sessions_panels.append(stat_panel(5, "Logged Since", 16, 0, 4, 4,
    "SELECT min(started_at) FROM sessions", unit="dateTimeAsLocal"))
sessions_panels.append(stat_panel(6, "Logged Until", 20, 0, 4, 4,
    "SELECT max(ended_at) FROM sessions", unit="dateTimeAsLocal"))

drive_link = {
    "matcher": {"id": "byName", "options": "started_at"},
    "properties": [
        {"id": "displayName", "value": "Date"},
        {"id": "unit", "value": "dateTimeAsLocal"},
        {"id": "links", "value": [{
            "title": "Open ride",
            "targetBlank": False,
            "url": (f"/d/{UID_DRIVE}/empulse-r-session-details?orgId=1"
                    "&var-session=${__data.fields.source_file}"
                    "&from=${__data.fields.started_epoch.numeric}"
                    "&to=${__data.fields.ended_epoch.numeric}"),
        }]},
    ],
}
charge_link = {
    "matcher": {"id": "byName", "options": "started_at"},
    "properties": [
        {"id": "displayName", "value": "Date"},
        {"id": "unit", "value": "dateTimeAsLocal"},
        {"id": "links", "value": [{
            "title": "Open charge",
            "targetBlank": False,
            "url": (f"/d/{UID_CHARGE}/empulse-r-charge-details?orgId=1"
                    "&var-session=${__data.fields.source_file}"
                    "&from=${__data.fields.started_epoch.numeric}"
                    "&to=${__data.fields.ended_epoch.numeric}"),
        }]},
    ],
}
hide_epoch = [
    {"matcher": {"id": "byName", "options": "started_epoch"}, "properties": [{"id": "custom.hidden", "value": True}]},
    {"matcher": {"id": "byName", "options": "ended_epoch"}, "properties": [{"id": "custom.hidden", "value": True}]},
]

drive_table_sql = """SELECT
  source_file, started_at, ended_at,
  round(extract(epoch from duration)/60,1) AS duration_min,
  round((odometer_end_mi - odometer_start_mi) * 1.609344, 1) AS distance_km,
  round(max_speed_mph * 1.609344, 1) AS max_speed_kmh,
  min_soc_pct, max_soc_pct,
  extract(epoch from started_at)*1000 AS started_epoch,
  extract(epoch from ended_at)*1000 AS ended_epoch
FROM sessions WHERE session_type = 'drive'
  AND extract(epoch from duration)/60 >= ${min_duration_min}
  AND COALESCE((odometer_end_mi - odometer_start_mi) * 1.609344, 0) >= ${min_distance_km}
ORDER BY started_at DESC"""

charge_table_sql = """SELECT
  s.source_file, s.started_at, s.ended_at,
  round(extract(epoch from s.duration)/60,1) AS duration_min,
  s.min_soc_pct, s.max_soc_pct,
  c.estimated_capacity_ah, c.estimated_capacity_wh,
  extract(epoch from s.started_at)*1000 AS started_epoch,
  extract(epoch from s.ended_at)*1000 AS ended_epoch
FROM sessions s
LEFT JOIN charge_capacity_estimates c USING (source_file)
WHERE s.session_type = 'charge'
  AND extract(epoch from s.duration)/60 >= ${min_duration_min}
ORDER BY s.started_at DESC"""

sessions_panels.append(table_panel(7, "Drive Sessions (click a row to open)", 0, 4, 24, 9, drive_table_sql,
    overrides=[drive_link] + hide_epoch +
    [{"matcher": {"id": "byName", "options": "distance_km"}, "properties": [{"id": "unit", "value": "lengthkm"}]},
     {"matcher": {"id": "byName", "options": "max_speed_kmh"}, "properties": [{"id": "unit", "value": "velocitykmh"}]},
     {"matcher": {"id": "byName", "options": "duration_min"}, "properties": [{"id": "unit", "value": "m"}]}]))

sessions_panels.append(table_panel(8, "Charge Sessions (click a row to open)", 0, 13, 24, 9, charge_table_sql,
    overrides=[charge_link] + hide_epoch +
    [{"matcher": {"id": "byName", "options": "duration_min"}, "properties": [{"id": "unit", "value": "m"}]},
     {"matcher": {"id": "byName", "options": "estimated_capacity_ah"}, "properties": [{"id": "unit", "value": "amph"}, {"id": "decimals", "value": 1}]},
     {"matcher": {"id": "byName", "options": "estimated_capacity_wh"}, "properties": [{"id": "unit", "value": "watth"}, {"id": "decimals", "value": 0}]}]))

# estimated_capacity_wh comes from direct trapezoidal integration of pack power (V x I), not
# Ah x avg(V) -- see charge_capacity_estimates in import.sql for why that matters. The measured
# values are scaled by a constant factor so the earliest reliable reading (avg of the first 5
# charges, Sept 2014) lines up with an assumed 10 kWh nominal pack capacity -- this is a display
# anchor, not a re-measurement: the fade %/year and total-fade stats are scale-invariant and
# identical either way, since scaling is a pure multiplicative constant.
capacity_sql = """WITH baseline AS (
  SELECT avg(estimated_capacity_wh) AS b FROM (
    SELECT estimated_capacity_wh FROM charge_capacity_estimates ORDER BY started_at ASC LIMIT 5
  ) x
),
scaled AS (
  SELECT started_at, estimated_capacity_wh * (10000.0 / baseline.b) AS estimated_capacity_wh
  FROM charge_capacity_estimates, baseline
)
SELECT started_at AS "time", estimated_capacity_wh,
  avg(estimated_capacity_wh) OVER () AS average_capacity_wh,
  estimated_capacity_wh / 10000.0 * 100 AS pct_of_baseline
FROM scaled
ORDER BY started_at"""

sessions_panels.append(ts_panel(9, "Estimated Pack Capacity Over Time (scaled to 10 kWh nominal at pack start)",
    0, 22, 18, 9, capacity_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": "estimated_capacity_wh"}, "properties": [{"id": "unit", "value": "watth"}, {"id": "displayName", "value": "Capacity"}]},
        {"matcher": {"id": "byName", "options": "average_capacity_wh"}, "properties": [
            {"id": "unit", "value": "watth"},
            {"id": "displayName", "value": "Average"},
            {"id": "custom.lineWidth", "value": 3},
            {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
            {"id": "custom.fillOpacity", "value": 0},
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "semi-dark-red"}},
        ]},
        {"matcher": {"id": "byName", "options": "pct_of_baseline"}, "properties": [
            {"id": "unit", "value": "percent"},
            {"id": "custom.axisPlacement", "value": "right"},
        ]},
    ]))

sessions_panels.append(stat_panel(10, "Capacity Fade / Year", 18, 22, 6, 3,
    """WITH baseline AS (
  SELECT avg(estimated_capacity_wh) AS b FROM (
    SELECT estimated_capacity_wh FROM charge_capacity_estimates ORDER BY started_at ASC LIMIT 5
  ) x
)
SELECT regr_slope(estimated_capacity_wh, extract(epoch from started_at)/31557600.0) / baseline.b * 100 AS pct_per_year
FROM charge_capacity_estimates, baseline GROUP BY baseline.b""", unit="percent"))

sessions_panels.append(stat_panel(12, "Capacity Fade (Total, earliest vs. most recent)", 18, 25, 6, 3,
    """WITH earliest AS (
  SELECT avg(estimated_capacity_wh) AS v FROM (
    SELECT estimated_capacity_wh FROM charge_capacity_estimates ORDER BY started_at ASC LIMIT 5
  ) x
), recent AS (
  SELECT avg(estimated_capacity_wh) AS v FROM (
    SELECT estimated_capacity_wh FROM charge_capacity_estimates ORDER BY started_at DESC LIMIT 5
  ) x
)
SELECT (recent.v - earliest.v) / earliest.v * 100 AS total_fade_pct
FROM earliest, recent""", unit="percent"))

sessions_panels.append(stat_panel(11, "Reliable Capacity Samples", 18, 28, 6, 3,
    "SELECT count(*) FROM charge_capacity_estimates"))

# Module-to-module SoC spread: found via the 2024 stranding investigation that overall_soc_pct
# can read misleadingly low while some modules are near-empty and others still hold real
# charge -- a module imbalance/weak-module signature, not a fully depleted pack. Tracked here
# over calendar time to see whether it's getting worse.
spread_sql = """SELECT started_at AS "time", max_module_spread_pct,
  avg(max_module_spread_pct) OVER () AS average_spread_pct
FROM module_soc_spread
ORDER BY started_at"""

sessions_panels.append(ts_panel(13, "Module-to-Module SoC Spread Over Time (max gap between weakest & strongest module per session)",
    0, 31, 18, 9, spread_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": "max_module_spread_pct"}, "properties": [{"id": "unit", "value": "percent"}, {"id": "displayName", "value": "Spread"}]},
        {"matcher": {"id": "byName", "options": "average_spread_pct"}, "properties": [
            {"id": "unit", "value": "percent"},
            {"id": "displayName", "value": "Average"},
            {"id": "custom.lineWidth", "value": 3},
            {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
            {"id": "custom.fillOpacity", "value": 0},
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "semi-dark-red"}},
        ]},
    ]))

sessions_panels.append(stat_panel(14, "Latest Module Spread", 18, 31, 6, 4,
    "SELECT max_module_spread_pct FROM module_soc_spread ORDER BY started_at DESC LIMIT 1", unit="percent"))
sessions_panels.append(stat_panel(15, "Worst Module Spread Ever", 18, 35, 6, 5,
    "SELECT max(max_module_spread_pct) FROM module_soc_spread", unit="percent"))

eoc_sql = """SELECT ended_at AS "time", cell_imbalance_mv / 1000.0 AS cell_imbalance_v,
  avg(cell_imbalance_mv / 1000.0) OVER () AS average_imbalance_v
FROM eoc_cell_imbalance
ORDER BY ended_at"""

sessions_panels.append(ts_panel(16, "End-of-Charge Cell Voltage Imbalance Over Time (charges reaching >=95% SoC)",
    0, 40, 18, 9, eoc_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": "cell_imbalance_v"}, "properties": [{"id": "unit", "value": "volt"}, {"id": "displayName", "value": "Imbalance"}]},
        {"matcher": {"id": "byName", "options": "average_imbalance_v"}, "properties": [
            {"id": "unit", "value": "volt"},
            {"id": "displayName", "value": "Average"},
            {"id": "custom.lineWidth", "value": 3},
            {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
            {"id": "custom.fillOpacity", "value": 0},
            {"id": "color", "value": {"mode": "fixed", "fixedColor": "semi-dark-red"}},
        ]},
    ]))

sessions_panels.append(stat_panel(17, "Full-Charge Sessions Tracked", 18, 40, 6, 9,
    "SELECT count(*) FROM eoc_cell_imbalance"))

# module1..7_intrabalance_active in battery_soc is decoded from the B-record's byte 31 bitmask
# (reverse-engineered against an official-tool decode, see decode_empulse_logs.py). Ties into
# the module-imbalance investigation: do the modules with the widest SoC spread also balance
# more often? "Inter-module" balancing exists as a field in the official tool's output too but
# was never once active across 37+ reference files, so it isn't tracked here.
balancing_sql = """SELECT started_at AS "time",
  module1_balance_frac * 100 AS module1_pct, module2_balance_frac * 100 AS module2_pct,
  module3_balance_frac * 100 AS module3_pct, module4_balance_frac * 100 AS module4_pct,
  module5_balance_frac * 100 AS module5_pct, module6_balance_frac * 100 AS module6_pct,
  module7_balance_frac * 100 AS module7_pct
FROM module_balancing_summary
ORDER BY started_at"""

sessions_panels.append(ts_panel(18, "Per-Module Intra-Balancing Frequency Over Time (% of samples actively balancing)",
    0, 49, 18, 9, balancing_sql, unit="percent"))

sessions_panels.append(table_panel(19, "Average Balancing Frequency by Module (All-Time)", 18, 49, 6, 9,
    """SELECT * FROM (
  SELECT 1 AS module, avg(module1_balance_frac) * 100 AS avg_balance_pct FROM module_balancing_summary
  UNION ALL SELECT 2, avg(module2_balance_frac) * 100 FROM module_balancing_summary
  UNION ALL SELECT 3, avg(module3_balance_frac) * 100 FROM module_balancing_summary
  UNION ALL SELECT 4, avg(module4_balance_frac) * 100 FROM module_balancing_summary
  UNION ALL SELECT 5, avg(module5_balance_frac) * 100 FROM module_balancing_summary
  UNION ALL SELECT 6, avg(module6_balance_frac) * 100 FROM module_balancing_summary
  UNION ALL SELECT 7, avg(module7_balance_frac) * 100 FROM module_balancing_summary
) x ORDER BY avg_balance_pct DESC""",
    overrides=[{"matcher": {"id": "byName", "options": "avg_balance_pct"}, "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 1}]}]))

# D-record byte 7 -- Sevcon motor controller fault code (see decode_empulse_logs.py). Only
# code 56 is confirmed against reference data ("S56: SEVCON -- 0x45c9 Motor low voltage"),
# always seen during hard acceleration at high RPM, immediately followed by a throttle/torque
# cut -- consistent with a brief DC-bus voltage sag under a current spike, not literally "low
# voltage" in the sense of a depleted pack.
sessions_panels.append(table_panel(20, "Motor Controller Fault Events (S56 = Motor Low Voltage)", 0, 58, 24, 9,
    """SELECT "timestamp" AS "time", source_file, speed_mph, rpm, motor_voltage_vrms, motor_current_arms, mc_fault_code
FROM drive_telemetry WHERE mc_fault_code <> 0 ORDER BY "timestamp" """))

# B-record byte 10 bit 3 -- verified 100% but unconfirmed meaning (see decode_empulse_logs.py).
# Far more frequent than the S56 fault above (1942 samples vs. 207) and never coincides with
# it, so plotted as a weekly count rather than a full event table.
sessions_panels.append(ts_panel(21, "BMS Fault Flag Events Over Time (weekly count, meaning unconfirmed)", 0, 67, 24, 9,
    """SELECT time_bucket('7 days', "timestamp") AS "time", count(*) AS events
FROM battery_soc WHERE bms_fault_flag = 1 GROUP BY 1 ORDER BY 1"""))

MIN_DURATION_VAR = {
    "name": "min_duration_min",
    "type": "textbox",
    "label": "Min duration (min)",
    "query": "0",
    "current": {"value": "0", "text": "0"},
}
MIN_DISTANCE_VAR = {
    "name": "min_distance_km",
    "type": "textbox",
    "label": "Min distance (km)",
    "query": "0",
    "current": {"value": "0", "text": "0"},
}

dash_sessions = dashboard(UID_SESSIONS, "Empulse R -- Sessions", sessions_panels, [MIN_DURATION_VAR, MIN_DISTANCE_VAR],
    annotations=[IDLE_PERIODS_ANNOTATION])

# ---------------------------------------------------------------- Dashboard: Efficiency
# All three factors found while investigating why the naive range estimate (~159km) didn't
# match real-world experience (~100km): the extrapolation bias from short trips, the resulting
# speed dependency (drag ~v^2, less regen than city stop-start), and a smaller but real
# temperature dependency. See drive_range_estimates in import.sql for the underlying data.
eff_panels = []

eff_panels.append(stat_panel(1, "Avg. Range at 100% SoC (deep-depletion drives, >=30% SoC used)", 0, 0, 8, 4,
    "SELECT round(sum(distance_km) / sum(soc_used_pct) * 100) FROM drive_range_estimates WHERE soc_used_pct >= 30", unit="suffix:km"))
eff_panels.append(stat_panel(2, "Avg. Range at 100% SoC (all qualifying drives -- biased high, see below)", 8, 0, 8, 4,
    "SELECT round(sum(distance_km) / sum(soc_used_pct) * 100) FROM drive_range_estimates", unit="suffix:km"))
eff_panels.append(stat_panel(3, "Deep-Depletion Drives (>=30% SoC used)", 16, 0, 8, 4,
    "SELECT count(*) FROM drive_range_estimates WHERE soc_used_pct >= 30"))

depth_sql = """SELECT
  CASE
    WHEN soc_used_pct < 15 THEN '5-15%'
    WHEN soc_used_pct < 30 THEN '15-30%'
    WHEN soc_used_pct < 50 THEN '30-50%'
    ELSE '50%+'
  END AS soc_used,
  count(*) AS n_drives,
  round(avg(avg_speed_kmh),1) AS avg_speed_kmh,
  round(sum(distance_km)) AS total_km,
  round(sum(distance_km) / sum(soc_used_pct) * 100) AS range_at_100pct_km
FROM drive_range_estimates
GROUP BY 1
ORDER BY min(soc_used_pct)"""

eff_panels.append(table_panel(4, "Range by Depletion Depth (shows the extrapolation bias)", 0, 4, 12, 6,
    depth_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": "avg_speed_kmh"}, "properties": [{"id": "unit", "value": "velocitykmh"}]},
        {"matcher": {"id": "byName", "options": "total_km"}, "properties": [{"id": "unit", "value": "suffix:km"}]},
        {"matcher": {"id": "byName", "options": "range_at_100pct_km"}, "properties": [{"id": "unit", "value": "suffix:km"}]},
    ]))

speed_sql = """SELECT
  CASE
    WHEN avg_speed_kmh < 30 THEN '<30 km/h'
    WHEN avg_speed_kmh < 45 THEN '30-45 km/h'
    WHEN avg_speed_kmh < 60 THEN '45-60 km/h'
    ELSE '60+ km/h'
  END AS avg_speed,
  count(*) AS n_drives,
  round(avg(range_at_100pct_km)) AS avg_range_at_100pct_km,
  round(sum(distance_km) / sum(soc_used_pct) * 100) AS weighted_range_at_100pct_km
FROM drive_range_estimates
GROUP BY 1
ORDER BY min(avg_speed_kmh)"""

eff_panels.append(table_panel(5, "Range by Average Riding Speed (drag scales ~v^2)", 12, 4, 12, 6,
    speed_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": "avg_range_at_100pct_km"}, "properties": [{"id": "unit", "value": "suffix:km"}]},
        {"matcher": {"id": "byName", "options": "weighted_range_at_100pct_km"}, "properties": [{"id": "unit", "value": "suffix:km"}]},
    ]))

temp_sql = """SELECT
  CASE
    WHEN avg_air_temp_c < 5 THEN '<5C'
    WHEN avg_air_temp_c < 15 THEN '5-15C'
    WHEN avg_air_temp_c < 25 THEN '15-25C'
    ELSE '25C+'
  END AS air_temp,
  count(*) AS n_drives,
  round(avg(avg_air_temp_c),1) AS avg_temp_c,
  round(avg(avg_speed_kmh),1) AS avg_speed_kmh,
  round(sum(distance_km) / sum(soc_used_pct) * 100) AS weighted_range_at_100pct_km
FROM drive_range_estimates
WHERE avg_air_temp_c IS NOT NULL
GROUP BY 1
ORDER BY min(avg_air_temp_c)"""

eff_panels.append(table_panel(6, "Range by Ambient Temperature (avg speed shown to rule out a speed confound)", 0, 10, 24, 6,
    temp_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": "avg_temp_c"}, "properties": [{"id": "unit", "value": "celsius"}]},
        {"matcher": {"id": "byName", "options": "avg_speed_kmh"}, "properties": [{"id": "unit", "value": "velocitykmh"}]},
        {"matcher": {"id": "byName", "options": "weighted_range_at_100pct_km"}, "properties": [{"id": "unit", "value": "suffix:km"}]},
    ]))

dash_efficiency = dashboard(UID_EFFICIENCY, "Empulse R -- Efficiency", eff_panels, [])

# ---------------------------------------------------------------- Dashboard 2: Session (drive) details
drive_panels = []
drive_panels.append(stat_panel(1, "Duration", 0, 0, 4, 4,
    "SELECT round(extract(epoch from duration)/60,1) FROM sessions WHERE source_file = '$session'", unit="m"))
drive_panels.append(stat_panel(2, "Distance", 4, 0, 4, 4,
    "SELECT round((odometer_end_mi - odometer_start_mi) * 1.609344, 1) FROM sessions WHERE source_file = '$session'", unit="lengthkm"))
drive_panels.append(stat_panel(3, "Max Speed", 8, 0, 4, 4,
    "SELECT round(max_speed_mph * 1.609344, 1) FROM sessions WHERE source_file = '$session'", unit="velocitykmh"))
drive_panels.append(stat_panel(4, "Min SoC", 12, 0, 4, 4,
    "SELECT min_soc_pct FROM sessions WHERE source_file = '$session'", unit="percent"))
drive_panels.append(stat_panel(5, "Max SoC", 16, 0, 4, 4,
    "SELECT max_soc_pct FROM sessions WHERE source_file = '$session'", unit="percent"))
drive_panels.append(stat_panel(6, "Odometer (End)", 20, 0, 4, 4,
    "SELECT round(odometer_end_mi * 1.609344, 1) FROM sessions WHERE source_file = '$session'", unit="suffix:km"))

tf = "$__timeFilter(\"timestamp\")"
drive_panels.append(ts_panel(7, "Speed & RPM", 0, 4, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", speed_mph * 1.609344 AS speed_kmh, rpm FROM drive_telemetry WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=[{"matcher": {"id": "byName", "options": "speed_kmh"}, "properties": [{"id": "unit", "value": "velocitykmh"}]}]))
drive_panels.append(ts_panel(8, "Motor / Air Temp & Throttle", 12, 4, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", (motor_temp_f - 32) * 5.0/9.0 AS motor_temp_c, (air_temp_f - 32) * 5.0/9.0 AS air_temp_c, throttle_pct FROM drive_telemetry WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=[
        {"matcher": {"id": "byName", "options": "motor_temp_c"}, "properties": [{"id": "unit", "value": "celsius"}]},
        {"matcher": {"id": "byName", "options": "air_temp_c"}, "properties": [{"id": "unit", "value": "celsius"}]},
    ]))
drive_panels.append(ts_panel(9, "SoC / Pack Voltage / Cell V Range", 0, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", overall_soc_pct, pack_voltage_v, high_cell_v, low_cell_v FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1"))
drive_panels.append(ts_panel(10, "Cell Imbalance & Cell Temp Range", 12, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", cell_imbalance_mv, min_cell_temp_c, max_cell_temp_c FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1"))
# Per-module SoC and the max-min spread between them -- the detail that revealed the July 2024
# strandings were a module imbalance (some modules near 0% while others still ~15%), not a
# fully depleted pack. Most actionable live, during the ride, so it lives on this dashboard.
drive_panels.append(ts_panel(11, "Per-Module SoC", 0, 20, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct, module5_soc_pct, module6_soc_pct, module7_soc_pct FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1", unit="percent"))
drive_panels.append(ts_panel(12, "Module-to-Module SoC Spread", 12, 20, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", GREATEST(module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct, module5_soc_pct, module6_soc_pct, module7_soc_pct) - LEAST(module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct, module5_soc_pct, module6_soc_pct, module7_soc_pct) AS module_spread_pct FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1", unit="percent"))
drive_panels.append(ts_panel(13, "Per-Module Current", 0, 28, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", module1_current_a, module2_current_a, module3_current_a, module4_current_a, module5_current_a, module6_current_a, module7_current_a FROM module_current_temp WHERE source_file = '$session' AND {tf} ORDER BY 1", unit="amp"))
drive_panels.append(ts_panel(14, "Per-Module Cell Temp", 12, 28, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", module1_cell_temp_c, module2_cell_temp_c, module3_cell_temp_c, module4_cell_temp_c, module5_cell_temp_c, module6_cell_temp_c, module7_cell_temp_c FROM module_current_temp WHERE source_file = '$session' AND {tf} ORDER BY 1", unit="celsius"))
drive_panels.append(heatmap_panel(15, "Cell Voltage Heatmap (28 cells)", 0, 36, 24, 9,
    f"SELECT \"timestamp\" AS \"time\", {CELL_COLS} FROM cell_voltages WHERE source_file = '$session' AND {tf} ORDER BY 1"))
# Per-module intra-balancing heatmap: module1..7_intrabalance_active (0/1, from the B-record's
# byte 31 bitmask -- see decode_empulse_logs.py) shown as one row per module, color = active.
INTRABALANCE_COLS = ", ".join(f"module{m}_intrabalance_active" for m in range(1, 8))
drive_panels.append(heatmap_panel(16, "Per-Module Intra-Balancing Activity", 0, 45, 24, 7,
    f"SELECT \"timestamp\" AS \"time\", {INTRABALANCE_COLS} FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1",
    unit="none", color_min=0, color_max=1))
drive_panels.append({
    "id": 17, "type": "state-timeline", "title": "Kickstand", "datasource": ds,
    "gridPos": {"x": 0, "y": 52, "w": 12, "h": 6},
    "targets": [sql_target(f"SELECT \"timestamp\" AS \"time\", kickstand FROM status_flags WHERE source_file = '$session' AND {tf} ORDER BY 1")],
    "fieldConfig": {"defaults": {}, "overrides": []}, "options": {},
})
drive_panels.append(table_panel(18, "Other Records (raw diagnostic codes)", 12, 52, 12, 6,
    f"SELECT \"timestamp\" AS \"time\", code, length, data_ascii_or_hex FROM other_records WHERE source_file = '$session' AND {tf} ORDER BY 1"))
# D-record bytes 4, 5-6, 15-16 -- see decode_empulse_logs.py. motor_power_kw is derived
# (voltage * current), not a separately logged field.
drive_panels.append(ts_panel(19, "Motor Voltage / Current / Power", 0, 58, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", motor_voltage_vrms, motor_current_arms, motor_power_kw FROM drive_telemetry WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=[
        {"matcher": {"id": "byName", "options": "motor_voltage_vrms"}, "properties": [{"id": "unit", "value": "volt"}]},
        {"matcher": {"id": "byName", "options": "motor_current_arms"}, "properties": [{"id": "unit", "value": "amp"}]},
        {"matcher": {"id": "byName", "options": "motor_power_kw"}, "properties": [{"id": "unit", "value": "kwatt"}]},
    ]))
drive_panels.append(ts_panel(20, "Estimated Range (dash indicator)", 12, 58, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", estimated_range_mi * 1.609344 AS estimated_range_km FROM drive_telemetry WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=[{"matcher": {"id": "byName", "options": "estimated_range_km"}, "properties": [{"id": "unit", "value": "lengthkm"}]}]))
# D-record byte 7 -- Sevcon motor controller fault code (see decode_empulse_logs.py). Only
# code 56 is confirmed against reference data ("S56: SEVCON -- 0x45c9 Motor low voltage"),
# observed exclusively during hard acceleration at high RPM, immediately followed by a
# throttle/torque cut -- consistent with a brief DC-bus voltage sag under a current spike.
drive_panels.append({
    "id": 21, "type": "state-timeline",
    "title": "Motor Controller Fault Code (56 = S56 Motor Low Voltage)", "datasource": ds,
    "gridPos": {"x": 0, "y": 66, "w": 24, "h": 4},
    "targets": [sql_target(f"SELECT \"timestamp\" AS \"time\", mc_fault_code FROM drive_telemetry WHERE source_file = '$session' AND {tf} ORDER BY 1")],
    "fieldConfig": {"defaults": {}, "overrides": []}, "options": {},
})
# B-record byte 10 bit 3 -- a pack-level fault flag, verified 100% but with unconfirmed
# meaning (see decode_empulse_logs.py). Never coincides with mc_fault_code=56 in reference
# data, so it's a separate, unrelated condition.
drive_panels.append({
    "id": 22, "type": "state-timeline",
    "title": "BMS Fault Flag (meaning unconfirmed)", "datasource": ds,
    "gridPos": {"x": 0, "y": 70, "w": 24, "h": 4},
    "targets": [sql_target(f"SELECT \"timestamp\" AS \"time\", bms_fault_flag FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1")],
    "fieldConfig": {"defaults": {}, "overrides": []}, "options": {},
})

dash_drive = dashboard(UID_DRIVE, "Empulse R -- Session Details", drive_panels, [session_var("session", "drive")])

# ---------------------------------------------------------------- Dashboard 3: Charge details
charge_panels = []
charge_panels.append(stat_panel(1, "Duration", 0, 0, 4, 4,
    "SELECT round(extract(epoch from duration)/60,1) FROM sessions WHERE source_file = '$session'", unit="m"))
charge_panels.append(stat_panel(2, "SoC Start", 4, 0, 4, 4,
    "SELECT min_soc_pct FROM sessions WHERE source_file = '$session'", unit="percent"))
charge_panels.append(stat_panel(3, "SoC End", 8, 0, 4, 4,
    "SELECT max_soc_pct FROM sessions WHERE source_file = '$session'", unit="percent"))
charge_panels.append(stat_panel(4, "Estimated Capacity (Ah)", 12, 0, 4, 4,
    "SELECT estimated_capacity_ah FROM charge_capacity_estimates WHERE source_file = '$session'", unit="amph"))
charge_panels.append(stat_panel(5, "Estimated Capacity (Wh)", 16, 0, 4, 4,
    "SELECT estimated_capacity_wh FROM charge_capacity_estimates WHERE source_file = '$session'", unit="watth"))
charge_panels.append(stat_panel(6, "Avg Pack Voltage", 20, 0, 4, 4,
    "SELECT avg_pack_voltage_v FROM charge_capacity_estimates WHERE source_file = '$session'", unit="volt"))

charge_panels.append(ts_panel(7, "SoC & Pack Voltage", 0, 4, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", overall_soc_pct, pack_voltage_v FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1"))
charge_panels.append(ts_panel(8, "Per-Module Charge Current", 12, 4, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", module1_current_a, module2_current_a, module3_current_a, module4_current_a, module5_current_a, module6_current_a, module7_current_a FROM module_current_temp WHERE source_file = '$session' AND {tf} ORDER BY 1", unit="amp"))
charge_panels.append(ts_panel(9, "Cell Imbalance & Cell Temp Range", 0, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", cell_imbalance_mv, min_cell_temp_c, max_cell_temp_c FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1"))
charge_panels.append(ts_panel(10, "Per-Module Cell Temp", 12, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", module1_cell_temp_c, module2_cell_temp_c, module3_cell_temp_c, module4_cell_temp_c, module5_cell_temp_c, module6_cell_temp_c, module7_cell_temp_c FROM module_current_temp WHERE source_file = '$session' AND {tf} ORDER BY 1", unit="celsius"))
charge_panels.append(heatmap_panel(11, "Cell Voltage Heatmap (28 cells) -- watch balancing at top of charge", 0, 20, 24, 9,
    f"SELECT \"timestamp\" AS \"time\", {CELL_COLS} FROM cell_voltages WHERE source_file = '$session' AND {tf} ORDER BY 1"))
charge_panels.append(heatmap_panel(13, "Per-Module Intra-Balancing Activity", 0, 29, 24, 7,
    f"SELECT \"timestamp\" AS \"time\", {INTRABALANCE_COLS} FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1",
    unit="none", color_min=0, color_max=1))
charge_panels.append(table_panel(12, "Other Records (raw diagnostic codes)", 0, 36, 24, 6,
    f"SELECT \"timestamp\" AS \"time\", code, length, data_ascii_or_hex FROM other_records WHERE source_file = '$session' AND {tf} ORDER BY 1"))

dash_charge = dashboard(UID_CHARGE, "Empulse R -- Charge Details", charge_panels, [session_var("session", "charge")])

for d in (dash_sessions, dash_efficiency, dash_drive, dash_charge):
    payload = {"dashboard": d, "folderUid": FOLDER_UID, "overwrite": True}
    print(json.dumps(payload))
