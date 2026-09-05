import json
import os

# UNITS=metric (default) shows km/h, km, and Celsius; UNITS=imperial keeps the bike's own
# native mph/mi/Fahrenheit as-is (no conversion). Chosen at generation time, not live in
# Grafana -- Grafana's field "unit" is a display label, not a live converter, so a real
# toggle would need either fixed axis labels or a second set of dashboards; regenerating
# and redeploying is the simpler, more robust tradeoff for a single-user dashboard.
UNITS = os.environ.get("UNITS", "metric").lower()
assert UNITS in ("metric", "imperial"), f"UNITS must be 'metric' or 'imperial', got {UNITS!r}"


def conv_length(col, alias):
    """(bare_expr, alias, unit): km (from a *_mi column) in metric mode, raw miles in
    imperial mode. Caller adds "AS alias" where a column name is needed."""
    if UNITS == "metric":
        return f"{col} * 1.609344", f"{alias}_km", "lengthkm"
    return col, f"{alias}_mi", "lengthmi"


def conv_speed(col, alias):
    """km/h (from a *_mph column) in metric mode, raw mph in imperial mode."""
    if UNITS == "metric":
        return f"{col} * 1.609344", f"{alias}_kmh", "velocitykmh"
    return col, f"{alias}_mph", "velocitymph"


def conv_temp_f(col, alias):
    """Celsius (from a *_f column, e.g. air/motor temp) in metric mode, raw F in imperial."""
    if UNITS == "metric":
        return f"({col} - 32) * 5.0/9.0", f"{alias}_c", "celsius"
    return col, f"{alias}_f", "fahrenheit"


def conv_temp_c(col, alias):
    """Celsius as-is in metric mode (e.g. cell temps, already stored in C); converted to F
    in imperial mode."""
    if UNITS == "metric":
        return col, f"{alias}_c", "celsius"
    return f"({col} * 9.0/5.0) + 32", f"{alias}_f", "fahrenheit"


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


def heatmap_panel(id, title, x, y, w, h, sql, unit="volt", color_min=None, color_max=None, reverse=True):
    # RdYlGn's natural direction is low=red, high=green; reverse=True (the default, used by
    # the fault/balancing 0-1 heatmaps below) flips that to low=green, high=red, since there
    # 0=inactive is the "good" value. Cell voltage wants the natural direction instead --
    # low voltage=red, high=green -- so those calls pass reverse=False.
    color = {"mode": "scheme", "scheme": "RdYlGn", "reverse": reverse, "steps": 64}
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
    raw_sql = (
        "SELECT source_file AS __value, source_file || ' -- ' || "
        "to_char(started_at,'YYYY-MM-DD HH24:MI') || ' (' || "
        "round(extract(epoch from duration)/60) || ' min)' AS __text "
        f"FROM sessions WHERE session_type = '{session_type}' ORDER BY started_at DESC"
    )
    return {
        "name": name, "type": "query", "datasource": ds,
        # "definition" is what Grafana shows as text for this variable (e.g. in the variable
        # list/edit view) -- without it, some Grafana versions stringify the "query" object
        # directly for display, producing a literal "[object Object]".
        "definition": raw_sql,
        "query": {
            "rawSql": raw_sql,
            "format": "table",
        },
        "refresh": 1, "sort": 0, "includeAll": False, "multi": False, "current": {},
    }


CELL_COLS = ", ".join(
    f"module{m}_cell{c}_v" for m in range(1, 8) for c in range(1, 5)
)

# module{i}_cell_temp_c and min/max_cell_temp_c are stored in Celsius natively (BMS-internal),
# so this is the only place conv_temp_c's F-conversion direction actually applies.
_module_cell_temp_convs = [conv_temp_c(f"module{m}_cell_temp_c", f"module{m}_cell_temp") for m in range(1, 8)]
MODULE_CELL_TEMP_COLS = ", ".join(f"{expr} AS {alias}" for expr, alias, _ in _module_cell_temp_convs)
MODULE_CELL_TEMP_UNIT = _module_cell_temp_convs[0][2]
_min_ct_expr, _min_ct_alias, _cell_temp_unit = conv_temp_c("min_cell_temp_c", "min_cell_temp")
_max_ct_expr, _max_ct_alias, _ = conv_temp_c("max_cell_temp_c", "max_cell_temp")
CELL_TEMP_RANGE_COLS = f"{_min_ct_expr} AS {_min_ct_alias}, {_max_ct_expr} AS {_max_ct_alias}"
CELL_TEMP_RANGE_OVERRIDES = [
    {"matcher": {"id": "byName", "options": _min_ct_alias}, "properties": [{"id": "unit", "value": _cell_temp_unit}]},
    {"matcher": {"id": "byName", "options": _max_ct_alias}, "properties": [{"id": "unit", "value": _cell_temp_unit}]},
]

# ---------------------------------------------------------------- Dashboard 1: Sessions overview
sessions_panels = []

sessions_panels.append(stat_panel(1, "Drive Sessions", 0, 0, 4, 4,
    "SELECT count(*) FROM sessions WHERE session_type = 'drive'"))
sessions_panels.append(stat_panel(2, "Charge Sessions", 4, 0, 4, 4,
    "SELECT count(*) FROM sessions WHERE session_type = 'charge'"))
_total_dist_expr, _, _total_dist_unit = conv_length("sum(odometer_end_mi - odometer_start_mi)", "total")
sessions_panels.append(stat_panel(3, "Total Distance", 8, 0, 4, 4,
    f"SELECT round({_total_dist_expr}) FROM sessions WHERE session_type = 'drive'", unit=_total_dist_unit))
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

_drive_dist_expr, _drive_dist_alias, _drive_dist_unit = conv_length("(odometer_end_mi - odometer_start_mi)", "distance")
_drive_speed_expr, _drive_speed_alias, _drive_speed_unit = conv_speed("max_speed_mph", "max_speed")
MIN_DISTANCE_VAR_NAME = "min_distance_km" if UNITS == "metric" else "min_distance_mi"

drive_table_sql = f"""SELECT
  source_file, started_at, ended_at,
  round(extract(epoch from duration)/60,1) AS duration_min,
  round({_drive_dist_expr}, 1) AS {_drive_dist_alias},
  round({_drive_speed_expr}, 1) AS {_drive_speed_alias},
  min_soc_pct, max_soc_pct,
  extract(epoch from started_at)*1000 AS started_epoch,
  extract(epoch from ended_at)*1000 AS ended_epoch
FROM sessions WHERE session_type = 'drive'
  AND $__timeFilter(started_at)
  AND extract(epoch from duration)/60 >= ${{min_duration_min}}
  AND COALESCE({_drive_dist_expr}, 0) >= ${{{MIN_DISTANCE_VAR_NAME}}}
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
  AND $__timeFilter(s.started_at)
  AND extract(epoch from s.duration)/60 >= ${min_duration_min}
ORDER BY s.started_at DESC"""

sessions_panels.append(table_panel(7, "Drive Sessions (click a row to open)", 0, 4, 24, 9, drive_table_sql,
    overrides=[drive_link] + hide_epoch +
    [{"matcher": {"id": "byName", "options": _drive_dist_alias}, "properties": [{"id": "unit", "value": _drive_dist_unit}]},
     {"matcher": {"id": "byName", "options": _drive_speed_alias}, "properties": [{"id": "unit", "value": _drive_speed_unit}]},
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
WHERE $__timeFilter(started_at)
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
WHERE $__timeFilter(started_at)
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
WHERE $__timeFilter(ended_at)
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
WHERE $__timeFilter(started_at)
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
_fault_speed_expr, _fault_speed_alias, _fault_speed_unit = conv_speed("speed_mph", "speed")
sessions_panels.append(table_panel(20, "Motor Controller Fault Events (S56 = Motor Low Voltage)", 0, 58, 24, 9,
    f"""SELECT "timestamp" AS "time", source_file, {_fault_speed_expr} AS {_fault_speed_alias}, rpm, motor_voltage_vrms, motor_current_arms, mc_fault_code
FROM drive_telemetry WHERE mc_fault_code <> 0 AND $__timeFilter("timestamp") ORDER BY "timestamp" """,
    overrides=[{"matcher": {"id": "byName", "options": _fault_speed_alias}, "properties": [{"id": "unit", "value": _fault_speed_unit}]}]))

# B-record byte 10 bit 3 -- verified 100% but unconfirmed meaning (see decode_empulse_logs.py).
# Far more frequent than the S56 fault above (1942 samples vs. 207) and never coincides with
# it, so plotted as a weekly count rather than a full event table.
sessions_panels.append(ts_panel(21, "BMS Fault Flag Events Over Time (weekly count, meaning unconfirmed)", 0, 67, 24, 9,
    """SELECT time_bucket('7 days', "timestamp") AS "time", count(*) AS events
FROM battery_soc WHERE bms_fault_flag = 1 AND $__timeFilter("timestamp") GROUP BY 1 ORDER BY 1"""))

MIN_DURATION_VAR = {
    "name": "min_duration_min",
    "type": "textbox",
    "label": "Min duration (min)",
    "query": "0",
    "current": {"value": "0", "text": "0"},
}
MIN_DISTANCE_VAR = {
    "name": MIN_DISTANCE_VAR_NAME,
    "type": "textbox",
    "label": f"Min distance ({'km' if UNITS == 'metric' else 'mi'})",
    "query": "0",
    "current": {"value": "0", "text": "0"},
}

dash_sessions = dashboard(UID_SESSIONS, "Empulse R -- Sessions", sessions_panels, [MIN_DURATION_VAR, MIN_DISTANCE_VAR],
    time_from="2014-01-01", annotations=[IDLE_PERIODS_ANNOTATION])

# ---------------------------------------------------------------- Dashboard: Efficiency
# All three factors found while investigating why the naive range estimate (~159km) didn't
# match real-world experience (~100km): the extrapolation bias from short trips, the resulting
# speed dependency (drag ~v^2, less regen than city stop-start), and a smaller but real
# temperature dependency. See drive_range_estimates in import.sql for the underlying data.
eff_panels = []

# distance_expr converts the raw distance_mi column; weighted range-at-100% always derives
# from it (sum(distance)/sum(soc_used_pct)*100), never from averaging a per-row ratio.
# range_expr separately converts the precomputed per-row range_at_100pct_mi column, used only
# where the *unweighted* per-drive average is wanted alongside the weighted one.
distance_expr, distance_alias, distance_unit = conv_length("distance_mi", "total")
_, weighted_range_alias, range_unit = conv_length("distance_mi", "range_at_100pct")
range_expr, avg_range_alias, _ = conv_length("range_at_100pct_mi", "range_at_100pct")
speed_expr, speed_alias, speed_unit = conv_speed("avg_speed_mph", "avg_speed")
temp_expr, temp_alias, temp_unit = conv_temp_f("avg_air_temp_f", "avg_temp")


def weighted_range_sql(distance_col="distance_mi", soc_col="soc_used_pct"):
    factor = 1.609344 if UNITS == "metric" else 1
    return f"sum({distance_col}) / sum({soc_col}) * 100 * {factor}"


# Bucket thresholds are hand-picked round numbers per unit system, not mechanical
# conversions of the metric ones (e.g. imperial buckets are round mph/F, not "18.6 mph").
if UNITS == "metric":
    speed_buckets = f"WHEN {speed_alias} < 30 THEN '<30 km/h' WHEN {speed_alias} < 45 THEN '30-45 km/h' WHEN {speed_alias} < 60 THEN '45-60 km/h' ELSE '60+ km/h'"
    temp_buckets = f"WHEN {temp_alias} < 5 THEN '<5C' WHEN {temp_alias} < 15 THEN '5-15C' WHEN {temp_alias} < 25 THEN '15-25C' ELSE '25C+'"
else:
    speed_buckets = f"WHEN {speed_alias} < 20 THEN '<20 mph' WHEN {speed_alias} < 30 THEN '20-30 mph' WHEN {speed_alias} < 40 THEN '30-40 mph' ELSE '40+ mph'"
    temp_buckets = f"WHEN {temp_alias} < 40 THEN '<40F' WHEN {temp_alias} < 60 THEN '40-60F' WHEN {temp_alias} < 80 THEN '60-80F' ELSE '80F+'"

eff_panels.append(stat_panel(1, "Avg. Range at 100% SoC (deep-depletion drives, >=30% SoC used)", 0, 0, 8, 4,
    f"SELECT round({weighted_range_sql()}) FROM drive_range_estimates WHERE soc_used_pct >= 30 AND $__timeFilter(started_at)", unit=range_unit))
eff_panels.append(stat_panel(2, "Avg. Range at 100% SoC (all qualifying drives -- biased high, see below)", 8, 0, 8, 4,
    f"SELECT round({weighted_range_sql()}) FROM drive_range_estimates WHERE $__timeFilter(started_at)", unit=range_unit))
eff_panels.append(stat_panel(3, "Deep-Depletion Drives (>=30% SoC used)", 16, 0, 8, 4,
    "SELECT count(*) FROM drive_range_estimates WHERE soc_used_pct >= 30 AND $__timeFilter(started_at)"))

depth_sql = f"""SELECT
  CASE
    WHEN soc_used_pct < 15 THEN '5-15%'
    WHEN soc_used_pct < 30 THEN '15-30%'
    WHEN soc_used_pct < 50 THEN '30-50%'
    ELSE '50%+'
  END AS soc_used,
  count(*) AS n_drives,
  round(avg({speed_expr}),1) AS {speed_alias},
  round(sum({distance_expr})) AS {distance_alias},
  round({weighted_range_sql()}) AS {weighted_range_alias}
FROM drive_range_estimates
WHERE $__timeFilter(started_at)
GROUP BY 1
ORDER BY min(soc_used_pct)"""

eff_panels.append(table_panel(4, "Range by Depletion Depth (shows the extrapolation bias)", 0, 4, 12, 6,
    depth_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": speed_alias}, "properties": [{"id": "unit", "value": speed_unit}]},
        {"matcher": {"id": "byName", "options": distance_alias}, "properties": [{"id": "unit", "value": distance_unit}]},
        {"matcher": {"id": "byName", "options": weighted_range_alias}, "properties": [{"id": "unit", "value": range_unit}]},
    ]))

speed_sql = f"""WITH base AS (
  SELECT soc_used_pct, distance_mi, started_at,
    {speed_expr} AS {speed_alias}, {range_expr} AS {avg_range_alias}
  FROM drive_range_estimates
)
SELECT
  CASE {speed_buckets} END AS avg_speed,
  count(*) AS n_drives,
  round(avg({avg_range_alias})) AS avg_{avg_range_alias},
  round({weighted_range_sql()}) AS weighted_{weighted_range_alias}
FROM base
WHERE $__timeFilter(started_at)
GROUP BY 1
ORDER BY min({speed_alias})"""

eff_panels.append(table_panel(5, "Range by Average Riding Speed (drag scales ~v^2)", 12, 4, 12, 6,
    speed_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": f"avg_{avg_range_alias}"}, "properties": [{"id": "unit", "value": range_unit}]},
        {"matcher": {"id": "byName", "options": f"weighted_{weighted_range_alias}"}, "properties": [{"id": "unit", "value": range_unit}]},
    ]))

temp_sql = f"""WITH base AS (
  SELECT soc_used_pct, distance_mi, avg_air_temp_f, started_at,
    {temp_expr} AS {temp_alias}, {speed_expr} AS {speed_alias}
  FROM drive_range_estimates
)
SELECT
  CASE {temp_buckets} END AS air_temp,
  count(*) AS n_drives,
  round(avg({temp_alias}),1) AS {temp_alias},
  round(avg({speed_alias}),1) AS {speed_alias},
  round({weighted_range_sql()}) AS weighted_{weighted_range_alias}
FROM base
WHERE avg_air_temp_f IS NOT NULL AND $__timeFilter(started_at)
GROUP BY 1
ORDER BY min({temp_alias})"""

eff_panels.append(table_panel(6, "Range by Ambient Temperature (avg speed shown to rule out a speed confound)", 0, 10, 24, 6,
    temp_sql,
    overrides=[
        {"matcher": {"id": "byName", "options": temp_alias}, "properties": [{"id": "unit", "value": temp_unit}]},
        {"matcher": {"id": "byName", "options": speed_alias}, "properties": [{"id": "unit", "value": speed_unit}]},
        {"matcher": {"id": "byName", "options": f"weighted_{weighted_range_alias}"}, "properties": [{"id": "unit", "value": range_unit}]},
    ]))

dash_efficiency = dashboard(UID_EFFICIENCY, "Empulse R -- Efficiency", eff_panels, [], time_from="2014-01-01")

# ---------------------------------------------------------------- Dashboard 2: Session (drive) details
drive_panels = []
drive_panels.append(stat_panel(1, "Duration", 0, 0, 4, 4,
    "SELECT round(extract(epoch from duration)/60,1) FROM sessions WHERE source_file = '$session'", unit="m"))
_ddist_expr, _, _ddist_unit = conv_length("(odometer_end_mi - odometer_start_mi)", "distance")
_dspeed_expr, _, _dspeed_unit = conv_speed("max_speed_mph", "max_speed")
_dodo_expr, _, _dodo_unit = conv_length("odometer_end_mi", "odometer")
drive_panels.append(stat_panel(2, "Distance", 4, 0, 4, 4,
    f"SELECT round({_ddist_expr}, 1) FROM sessions WHERE source_file = '$session'", unit=_ddist_unit))
drive_panels.append(stat_panel(3, "Max Speed", 8, 0, 4, 4,
    f"SELECT round({_dspeed_expr}, 1) FROM sessions WHERE source_file = '$session'", unit=_dspeed_unit))
drive_panels.append(stat_panel(4, "Min SoC", 12, 0, 4, 4,
    "SELECT min_soc_pct FROM sessions WHERE source_file = '$session'", unit="percent"))
drive_panels.append(stat_panel(5, "Max SoC", 16, 0, 4, 4,
    "SELECT max_soc_pct FROM sessions WHERE source_file = '$session'", unit="percent"))
drive_panels.append(stat_panel(6, "Odometer (End)", 20, 0, 4, 4,
    f"SELECT round({_dodo_expr}, 1) FROM sessions WHERE source_file = '$session'", unit=_dodo_unit))

tf = "$__timeFilter(\"timestamp\")"
_speed_kmh_expr, _speed_kmh_alias, _speed_kmh_unit = conv_speed("speed_mph", "speed")
# gear (from gear_status, E-record) plotted on its own right-hand axis -- its 0-6 range
# would be invisible against speed/RPM otherwise.
drive_panels.append(ts_panel(7, "Speed & RPM & Gear", 0, 4, 12, 8,
    f"""SELECT dt."timestamp" AS "time", {_speed_kmh_expr} AS {_speed_kmh_alias}, rpm, gs.gear
FROM drive_telemetry dt LEFT JOIN gear_status gs ON gs.source_file = dt.source_file AND gs.timestamp = dt.timestamp
WHERE dt.source_file = '$session' AND $__timeFilter(dt."timestamp") ORDER BY 1""",
    overrides=[
        {"matcher": {"id": "byName", "options": _speed_kmh_alias}, "properties": [{"id": "unit", "value": _speed_kmh_unit}]},
        {"matcher": {"id": "byName", "options": "gear"}, "properties": [
            {"id": "custom.axisPlacement", "value": "right"},
            {"id": "max", "value": 6},
            {"id": "custom.lineWidth", "value": 0},
            {"id": "custom.fillOpacity", "value": 20},
            {"id": "custom.lineInterpolation", "value": "stepAfter"},
        ]},
    ]))
_motor_temp_expr, _motor_temp_alias, _motor_temp_unit = conv_temp_f("motor_temp_f", "motor_temp")
_air_temp_expr, _air_temp_alias, _air_temp_unit = conv_temp_f("air_temp_f", "air_temp")
# brake_applied (from gear_status, E-record) scaled to 0/100 so it overlays legibly on the
# same 0-100 throttle_pct axis -- a flat 100 band whenever the brake is pressed.
drive_panels.append(ts_panel(8, "Motor / Air Temp & Throttle & Brake", 12, 4, 12, 8,
    f"""SELECT dt."timestamp" AS "time", {_motor_temp_expr} AS {_motor_temp_alias},
    {_air_temp_expr} AS {_air_temp_alias}, throttle_pct, gs.brake_applied * 100 AS brake_applied_pct
FROM drive_telemetry dt LEFT JOIN gear_status gs ON gs.source_file = dt.source_file AND gs.timestamp = dt.timestamp
WHERE dt.source_file = '$session' AND $__timeFilter(dt."timestamp") ORDER BY 1""",
    overrides=[
        {"matcher": {"id": "byName", "options": _motor_temp_alias}, "properties": [{"id": "unit", "value": _motor_temp_unit}]},
        {"matcher": {"id": "byName", "options": _air_temp_alias}, "properties": [{"id": "unit", "value": _air_temp_unit}]},
        # No line, just a 20%-opacity fill that steps straight down instead of the default
        # linear interpolation sloping between an "applied" and a "released" sample.
        {"matcher": {"id": "byName", "options": "brake_applied_pct"}, "properties": [
            {"id": "custom.lineWidth", "value": 0},
            {"id": "custom.fillOpacity", "value": 20},
            {"id": "custom.lineInterpolation", "value": "stepAfter"},
        ]},
    ]))
drive_panels.append(ts_panel(9, "SoC / Pack Voltage / Cell V Range", 0, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", overall_soc_pct, pack_voltage_v, high_cell_v, low_cell_v FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1"))
drive_panels.append(ts_panel(10, "Cell Imbalance & Cell Temp Range", 12, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", cell_imbalance_mv, {CELL_TEMP_RANGE_COLS} FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=CELL_TEMP_RANGE_OVERRIDES))
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
    f"SELECT \"timestamp\" AS \"time\", {MODULE_CELL_TEMP_COLS} FROM module_current_temp WHERE source_file = '$session' AND {tf} ORDER BY 1", unit=MODULE_CELL_TEMP_UNIT))
drive_panels.append(heatmap_panel(15, "Cell Voltage Heatmap (28 cells)", 0, 36, 24, 9,
    f"SELECT \"timestamp\" AS \"time\", {CELL_COLS} FROM cell_voltages WHERE source_file = '$session' AND {tf} ORDER BY 1",
    color_min=3.3, color_max=4.15, reverse=False))
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
_est_range_expr, _est_range_alias, _est_range_unit = conv_length("estimated_range_mi", "estimated_range")
drive_panels.append(ts_panel(20, "Estimated Range (dash indicator)", 12, 58, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", {_est_range_expr} AS {_est_range_alias} FROM drive_telemetry WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=[{"matcher": {"id": "byName", "options": _est_range_alias}, "properties": [{"id": "unit", "value": _est_range_unit}]}]))
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
    f"SELECT \"timestamp\" AS \"time\", cell_imbalance_mv, {CELL_TEMP_RANGE_COLS} FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1",
    overrides=CELL_TEMP_RANGE_OVERRIDES))
charge_panels.append(ts_panel(10, "Per-Module Cell Temp", 12, 12, 12, 8,
    f"SELECT \"timestamp\" AS \"time\", {MODULE_CELL_TEMP_COLS} FROM module_current_temp WHERE source_file = '$session' AND {tf} ORDER BY 1", unit=MODULE_CELL_TEMP_UNIT))
charge_panels.append(heatmap_panel(11, "Cell Voltage Heatmap (28 cells) -- watch balancing at top of charge", 0, 20, 24, 9,
    f"SELECT \"timestamp\" AS \"time\", {CELL_COLS} FROM cell_voltages WHERE source_file = '$session' AND {tf} ORDER BY 1",
    color_min=3.3, color_max=4.15, reverse=False))
charge_panels.append(heatmap_panel(13, "Per-Module Intra-Balancing Activity", 0, 29, 24, 7,
    f"SELECT \"timestamp\" AS \"time\", {INTRABALANCE_COLS} FROM battery_soc WHERE source_file = '$session' AND {tf} ORDER BY 1",
    unit="none", color_min=0, color_max=1))
charge_panels.append(table_panel(12, "Other Records (raw diagnostic codes)", 0, 36, 24, 6,
    f"SELECT \"timestamp\" AS \"time\", code, length, data_ascii_or_hex FROM other_records WHERE source_file = '$session' AND {tf} ORDER BY 1"))

dash_charge = dashboard(UID_CHARGE, "Empulse R -- Charge Details", charge_panels, [session_var("session", "charge")])

# Also written to grafana/dashboards/<uid>.json (checked into the repo) so the dashboard
# definitions are reviewable in a diff, not just reconstructable by re-running this script.
dashboards_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")
os.makedirs(dashboards_dir, exist_ok=True)

for d in (dash_sessions, dash_efficiency, dash_drive, dash_charge):
    payload = {"dashboard": d, "folderUid": FOLDER_UID, "overwrite": True}
    print(json.dumps(payload))
    with open(os.path.join(dashboards_dir, f"{d['uid']}.json"), "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
