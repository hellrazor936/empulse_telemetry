# Findings & Hypotheses

**Disclaimer**: everything below is specific to *this* bike, this pack's age/history, and this
owner's riding/charging habits -- none of it is a general claim about the Empulse R platform or
Elithion Lithiumate BMS behavior. The point of writing it down isn't "this is what's wrong with
your bike too" -- it's to show *which patterns in the data are worth cross-referencing* if you're
trying to diagnose your own pack: SoC jumps between sessions with nothing logged in between,
per-module balancing frequency, per-module voltage sag under load, and how those things can (or
can't) be tied together into a single explanation. Your numbers, your weak module (if any), and
your conclusions will very likely differ. SQL below assumes the schema in `schema.sql` and is
meant to be adapted, not copy-pasted blindly.

For confirmed byte-level decode findings (which byte means what), see the comments in
`decode_empulse_logs.py` and `schema.sql` instead; for open technical questions about the log
format itself, see the GitHub issues.

## Methodology caveat: how much of this depends on the BMS's own SoC%?

`overall_soc_pct` / `module{N}_soc_pct` are **BMS-computed** values -- decoded from the log, not
independently derived or validated. Everything else used below (cell voltage, current, the
balancing/fault bitmasks) is either a direct measurement or a directly-decoded BMS bit, and
doesn't depend on the BMS's internal SoC algorithm being accurate. If that algorithm drifted,
got recalibrated, or is just noisy near the flat part of the Li-ion voltage curve, here's what
that would mean for the findings above:

**Findings that would need to be reconsidered or rebuilt** if BMS SoC% isn't trustworthy:
- *Pack Capacity Degradation Trend / Capacity Fade panels* -- `estimated_capacity_wh` is
  `charged_wh / (soc_delta/100)`: a real energy measurement divided by a BMS-estimated
  percentage. A shift in the SoC algorithm over 10 years would show up as fake "degradation"
  here. This is the single biggest exposure in the whole project.
- *Module 3 aging + short-gap SoC drops* (below) -- built entirely on `overall_soc_pct`
  differences. Voltage relaxation misread by the BMS's own SoC estimator would produce exactly
  this pattern with zero real charge loss -- already flagged as a caveat on that entry, but
  worth being explicit that the whole hypothesis rests on trusting SoC%.
- *Module imbalance caused strandings* (below) -- uses `module{N}_soc_pct` spread as the
  evidence. Would need re-deriving from per-module cell voltage at the time of the stranding
  (e.g. distance from the ~3.7V fault threshold) instead, to stand independent of SoC.

**Findings that would only weaken slightly**: S56's "no correlation with SoC" is a negative
result -- easier to (wrongly) get if SoC itself is noisy -- but the positive part of that
finding (correlates with RPM/throttle, immediate torque cut) doesn't depend on SoC at all.

**Findings unaffected**: BMS fault flag = low-cell-voltage warning, passive/intra vs. active/
inter balancing, and modules 1 & 3 as weakest (both methods) -- none of these use SoC%, only
voltage, current, and directly-decoded bitmask fields.

A proper fix would replace the SoC-delta-based capacity estimate with a voltage/current-taper-
based one (detect "full" via charge current tapering near ~4.1-4.15V/cell instead of trusting a
SoC% label) and re-derive the module-imbalance and module-3 findings from voltage instead of
SoC. Not done here -- flagged as a known methodology risk instead, since redoing the whole
capacity pipeline is a bigger project than the findings it would touch.

## Confirmed

### Module imbalance caused "low battery" strandings, not a depleted pack

**Observation**: the bike had repeatedly stranded with a "low battery" warning despite
`overall_soc_pct` (the average across all 7 modules) reading well above 0%. The real cause: one
or two modules were reading near-empty while the others still had real charge -- the *pack
average* looked fine, but the BMS/controller reacts to the *weakest* module.

```sql
-- Widest gap between the weakest and strongest module, per drive session
SELECT source_file, "timestamp",
  GREATEST(module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct,
           module5_soc_pct, module6_soc_pct, module7_soc_pct)
  - LEAST(module1_soc_pct, module2_soc_pct, module3_soc_pct, module4_soc_pct,
          module5_soc_pct, module6_soc_pct, module7_soc_pct) AS module_spread_pct,
  overall_soc_pct
FROM battery_soc
WHERE source_file = '<a session that stranded>'
ORDER BY module_spread_pct DESC
LIMIT 10;
```

A wide spread (double digits) at a low `overall_soc_pct` is the signature to look for -- the
pack isn't "empty", one module is.

### Balancing is passive and within-module, not active and between-module

Before decoding the actual bitmask, the working theory was that the BMS does *active* balancing
(moving charge between modules) under certain conditions (e.g. one module hitting a low-voltage
threshold). Decoding B-record byte 31 (`module{N}_intrabalance_active`) against the official
tool's own "Module Intrabalance Active" / "Module Interbalance Active" columns showed the
opposite: intra-module (within a module, presumably cell-to-cell) balancing is what's actually
happening, verified active in the logs; inter-module balancing (between modules) was never once
observed active across 37+ reference files, so the platform may not really do that at all, or
does it under conditions this bike never hit.

```sql
-- How often is each module actively (intra-)balancing, all-time
SELECT
  avg(module1_intrabalance_active::int) AS m1, avg(module2_intrabalance_active::int) AS m2,
  avg(module3_intrabalance_active::int) AS m3, avg(module4_intrabalance_active::int) AS m4,
  avg(module5_intrabalance_active::int) AS m5, avg(module6_intrabalance_active::int) AS m6,
  avg(module7_intrabalance_active::int) AS m7
FROM battery_soc;
```

### Modules 1 and 3 are the weakest, found independently by two different methods

**Method 1 -- voltage sag under load**: for every sample, compute each cell's deviation from
the mean of all 28 cells, then compare that deviation during high-current moments vs. resting
moments. A cell/module with higher internal resistance sags further below its peers specifically
when current is high.

```sql
WITH joined AS (
  SELECT cv.*,
    (abs(mc.module1_current_a)+abs(mc.module2_current_a)+abs(mc.module3_current_a)
     +abs(mc.module4_current_a)+abs(mc.module5_current_a)+abs(mc.module6_current_a)
     +abs(mc.module7_current_a))/7.0 AS avg_current
  FROM cell_voltages cv
  JOIN module_current_temp mc ON mc.source_file=cv.source_file AND mc.timestamp=cv.timestamp
),
row_avg AS (
  SELECT *, (module1_cell1_v+module1_cell2_v+module1_cell3_v+module1_cell4_v
    +module2_cell1_v+module2_cell2_v+module2_cell3_v+module2_cell4_v
    +module3_cell1_v+module3_cell2_v+module3_cell3_v+module3_cell4_v
    +module4_cell1_v+module4_cell2_v+module4_cell3_v+module4_cell4_v
    +module5_cell1_v+module5_cell2_v+module5_cell3_v+module5_cell4_v
    +module6_cell1_v+module6_cell2_v+module6_cell3_v+module6_cell4_v
    +module7_cell1_v+module7_cell2_v+module7_cell3_v+module7_cell4_v) / 28.0 AS mean_v
  FROM joined
)
-- Repeat per cell column: deviation at high current vs. low current
SELECT
  avg(module1_cell1_v - mean_v) FILTER (WHERE avg_current > 100) AS high_dev,
  avg(module1_cell1_v - mean_v) FILTER (WHERE avg_current < 10) AS low_dev
FROM row_avg;
```

A cell whose `high_dev` is much more negative than its `low_dev` sags disproportionately under
load. Module 1's cells (all four) came out worst by this method.

**Method 2 -- balancing frequency**: module 3 balances far more often than any other, all-time
(see the query in the previous section) -- roughly 10% of samples vs. 3.6-6.3% for the rest.

These are two different mechanisms (internal resistance vs. capacity/voltage-curve mismatch) and
two different modules -- not a single clean "this module is bad" story, but two independent
signals worth having if you're deciding whether/when to consider a partial rebuild.

### BMS fault flag = low-cell-voltage warning (~3.7V threshold)

A pack-level flag (B-record byte 10 bit 3) with no official text label turned out to correlate
almost perfectly with the lowest cell's voltage: it never fires at or above ~3.70V, and becomes
steadily more likely the further the weakest cell drops below that.

```sql
SELECT bms_fault_flag, count(*),
  round(min(low_cell_v),3) AS min_low_cell_v,
  round(avg(low_cell_v),3) AS avg_low_cell_v,
  round(max(low_cell_v),3) AS max_low_cell_v
FROM battery_soc GROUP BY bms_fault_flag;
```

If you have an equivalent flag, bucket `low_cell_v` (e.g. `width_bucket(low_cell_v, 3.0, 4.0, 20)`)
against it to see if your pack has the same cutoff.

### S56 ("Motor low voltage") correlates with RPM/throttle, not pack condition

A Sevcon motor controller fault (`mc_fault_code = 56`) that sounds like it should mean "battery
is weak" instead only ever fires during hard acceleration at high RPM, is immediately followed by
a throttle/torque cut, and shows **no** correlation with SoC, pack voltage, or ambient
temperature at the time of the event.

```sql
SELECT dt.source_file, dt."timestamp", dt.speed_mph, dt.rpm, dt.throttle_pct,
  b.overall_soc_pct, b.pack_voltage_v
FROM drive_telemetry dt
JOIN battery_soc b ON b.source_file=dt.source_file AND b.timestamp=dt.timestamp
WHERE dt.mc_fault_code <> 0
ORDER BY dt."timestamp";
```

If your fault events cluster at a specific RPM/throttle combination but scatter freely across
SoC/voltage/temp, that's a sign it's a controller-side voltage-headroom limit (field-weakening
region), not a pack health issue -- worth checking before assuming a wiring/contact problem.

### SoC always settles down a couple points right after reaching 100% (not real energy loss)

Comparing the SoC reading at the end of a charge to the SoC reading at the very start of the
next session (whichever type, whatever the gap): after a charge that reached >=99.5% SoC, the
next reading is consistently *lower* -- never higher -- even when the gap is seconds long.

```sql
WITH first_soc AS (
  SELECT DISTINCT ON (source_file) source_file, overall_soc_pct AS soc_first
  FROM battery_soc ORDER BY source_file, "timestamp" ASC
),
last_soc AS (
  SELECT DISTINCT ON (source_file) source_file, overall_soc_pct AS soc_last
  FROM battery_soc ORDER BY source_file, "timestamp" DESC
),
sess AS (
  SELECT s.source_file, s.session_type, s.started_at, s.ended_at, f.soc_first, l.soc_last
  FROM sessions s JOIN first_soc f USING (source_file) JOIN last_soc l USING (source_file)
),
ordered AS (
  SELECT *,
    lead(soc_first) OVER (ORDER BY started_at) AS next_soc_first,
    lead(started_at) OVER (ORDER BY started_at) AS next_started_at
  FROM sess
)
SELECT source_file, ended_at, soc_last, next_started_at, next_soc_first,
  round(next_soc_first - soc_last, 1) AS soc_gap,
  round(extract(epoch FROM (next_started_at - ended_at))/3600.0, 2) AS idle_hours
FROM ordered
WHERE session_type = 'charge' AND soc_last >= 99.5
ORDER BY soc_gap;
```

Over 330 charges that reached >=99.5%, the gap to the next reading is *never positive* (max
0.00 -- can't gain charge without charging) and clusters between 0 and -2.4 points, most often
around -1.5 to -2.0 -- even with idle gaps as short as **29 seconds**. Far too fast to be real
energy loss. Bucketing by idle time shows the drop appears almost immediately and barely grows
from there: ~-1.1 at <1h, ~-1.8 to -2.0 out through a week, only clearly growing again past a
month (which is a different, already-known phenomenon -- real self-discharge over long storage,
see `long_idle_periods`).

**Crucially, this only happens after a charge that actually reached ~100%** -- partial charges
show essentially no gap at all:

```sql
-- same ordered/gaps CTEs as above, then:
SELECT
  CASE WHEN soc_last >= 99.5 THEN '100%' WHEN soc_last >= 90 THEN '90-99.5%'
       WHEN soc_last >= 70 THEN '70-90%' WHEN soc_last >= 50 THEN '50-70%' ELSE '<50%' END AS end_soc_bucket,
  count(*) AS n, round(avg(next_soc_first - soc_last),2) AS avg_gap
FROM ordered
WHERE session_type = 'charge'
  AND extract(epoch FROM (next_started_at - ended_at))/3600.0 < 1
GROUP BY 1 ORDER BY 1;
```

100% charges: avg -1.09 (n=160). Everything from 50% to 99.5%: avg between -0.18 and +0.17,
essentially noise (n=5-16 each). This points at a voltage-saturation effect rather than a
coulomb-counting drift: near full charge the cell voltage curve is very flat/high, so the
voltage-informed SoC estimate easily reads "100%" right at charge termination, then corrects
itself down by a couple points once the pack settles at rest -- a correction the algorithm
doesn't need to make anywhere below full, where the voltage curve is more informative.

**Caveat**: one short-gap case (`58E8AC06.CHG` -> `58E904ED.DRV`, 2024-07-08/09, gap -91.4 over
2.26h) was excluded from the stats above as a clear outlier, not part of this pattern. The
"next" session there is an 18-36-second power-on blip during the known July 2024 stranding
period (see the low-SoC-range entry below) -- likely a startup-transient garbage reading in
that very short session rather than a real SoC value, not the same settling effect described
here.

## Open / unresolved

### Low-SoC range has effectively never been exercised -- can't tell degradation from "never tested" (hypothesis)

**Motivation**: this is the actual reason I'm looking at a replacement pack. A 2026-09-14 drive
(68.4 km, 100% -> 42.8% SoC, 71.2 Wh/km) prompted the question of whether continuing another
~40 km on the same profile would have been safe. Extrapolating the weakest cell's voltage
against SoC for that drive (binning to 2%-SoC buckets to average out load-transient noise, then
a linear fit -- R^2=0.76) put the 3.30V crossing (my own lived shutdown threshold, see the BMS
fault flag finding above) at ~22% SoC, i.e. only ~25 km / ~30 min further than actually driven,
not 40. That matches how the bike has felt in the low range recently.

```sql
WITH joined AS (
  SELECT b.*, (dt.odometer_mi - s.odometer_start_mi) * 1.609344 AS dist_km
  FROM battery_soc b
  JOIN drive_telemetry dt USING (source_file, "timestamp")
  JOIN sessions s USING (source_file)
  WHERE b.source_file = '<the drive in question>'
),
binned AS (
  SELECT round(overall_soc_pct/2)*2 AS soc_bucket, avg(low_cell_v) AS avg_low_cell_v
  FROM joined GROUP BY 1
)
SELECT regr_slope(avg_low_cell_v, soc_bucket) AS slope_v_per_pct,
  regr_intercept(avg_low_cell_v, soc_bucket) AS intercept_v,
  regr_r2(avg_low_cell_v, soc_bucket) AS r2,
  (3.30 - regr_intercept(avg_low_cell_v, soc_bucket)) / regr_slope(avg_low_cell_v, soc_bucket) AS soc_at_330v
FROM binned;
```

**The problem**: I can't tell from this alone whether the pack has specifically gotten worse in
the low-SoC range, or whether it was simply always like this down there and nobody ever found
out, because the low-SoC range has almost never been visited in 10+ years of logs:

```sql
-- Every drive that ever reached <=15% SoC, across the whole 10-year history
SELECT source_file, started_at, min_soc_pct
FROM sessions WHERE session_type = 'drive' AND min_soc_pct <= 15
ORDER BY started_at;
```

Result: exactly **3 drives**, all from **July 2024** (`58E31613.DRV`, `58E904ED.DRV`,
`58EBBC16.DRV`, reaching 10.0%/8.6%/2.1% SoC) -- these line up with the 2024 stranding
investigation referenced in `schema.sql`. Their weakest cell readings: 3.195V, 3.29V, 3.38V, with
652/19/10 `bms_fault_flag` samples respectively -- consistent with the 3.30V danger zone. There
is no earlier (e.g. 2014-2015, pack-new) deep-discharge session on record to compare against, so
there's no baseline for "how did the low end behave when the pack was new" -- only "how does it
behave now, rarely tested." Two competing explanations, can't distinguish between them from the
data alone:
1. The pack's low-SoC voltage sag has genuinely worsened with age (aged/higher-resistance cells
   sag harder exactly where the discharge curve is already steepest).
2. The pack always behaved this way down there and it was simply never driven into that range
   before 2024 -- i.e. this isn't new degradation, just newly-observed original behavior.

Would need an early-life deep-discharge log (unlikely to exist -- deep discharges are rare by
nature and the owner wasn't logging every charge/drive habit from day one) or a like-for-like
comparison against a same-age pack to resolve which explanation is right.

### Module 3 aging + short-gap SoC drops (hypothesis)

Comparing SoC at the end of one drive to SoC at the start of the next (no charge session logged
in between), several cases show a real SoC drop (2-4 points) over just a few hours parked --
far too fast for normal Li-ion self-discharge (<1%/day) if it were a continuous drain.

```sql
WITH first_soc AS (
  SELECT DISTINCT ON (source_file) source_file, overall_soc_pct AS soc_first
  FROM battery_soc ORDER BY source_file, "timestamp" ASC
),
last_soc AS (
  SELECT DISTINCT ON (source_file) source_file, overall_soc_pct AS soc_last
  FROM battery_soc ORDER BY source_file, "timestamp" DESC
),
sess AS (
  SELECT s.source_file, s.session_type, s.started_at, s.ended_at, f.soc_first, l.soc_last
  FROM sessions s JOIN first_soc f USING (source_file) JOIN last_soc l USING (source_file)
),
ordered AS (
  SELECT *,
    lead(session_type) OVER (ORDER BY started_at) AS next_type,
    lead(soc_first) OVER (ORDER BY started_at) AS next_soc_first,
    lead(started_at) OVER (ORDER BY started_at) AS next_started_at
  FROM sess
)
SELECT source_file, ended_at, soc_last, next_started_at, next_soc_first,
  round(next_soc_first - soc_last, 1) AS soc_gap,
  round(extract(epoch FROM (next_started_at - ended_at))/86400.0, 2) AS idle_days
FROM ordered
WHERE session_type = 'drive' AND next_type = 'drive'
  AND (next_soc_first - soc_last) < -1.5
  AND extract(epoch FROM (next_started_at - ended_at))/86400.0 < 1
ORDER BY soc_gap;
```

Cross-referencing `module{N}_intrabalance_active` in the last few minutes of the preceding drive:
6 of 8 checked cases had active balancing right before shutdown, and module 3 was the dominant
balancer in nearly every one (up to 119 of 172 total balancing samples in one case) -- lining up
with module 3's all-time balancing lead and module 1's load-sag lead above.

**Working hypothesis**: an aged/drifted cell in module 3 needs more frequent balancing; passive
balancing burns the excess charge off as heat, producing exactly this kind of small SoC
step-down between sessions.

**Caveat**: balancing-sample count doesn't correlate cleanly with drop size (172 samples but only
-2.3 points in one case; 2 samples but -3.7 points in another) -- balancing/aging explains the
general pattern and the module-3 bias, not the exact magnitude of any single drop. Voltage
relaxation after a hard ride (cell voltage temporarily depressed under load, recovering once
removed, read by a voltage-informed SoC estimate as a step down) may also be contributing --
two of the eight cases showed no balancing logged at all yet still had a 2-2.5 point drop.

Not yet investigated: whether these short-gap drops have gotten more frequent/larger in recent
years, which would support the aging explanation more directly than a snapshot does.

### Single-module current divergence at top-of-charge (unexplained)

A specific charge session showed one module's current diverge to -3.96A for several minutes near
the top of charge while the other six modules stayed near 0A, without a clean correspondence to
the `intrabalance_active` flag for that module at the same timestamps. Looked like it might be
balancing-related at first glance, but the flag doesn't actually confirm it. Possibly related to
undecoded bq116 FET-status fields (`M{n} bq116 Fet Status` in the reference tool's columns,
which we have not attempted to decode). Left open.
