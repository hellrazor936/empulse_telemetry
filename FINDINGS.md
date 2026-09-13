# Findings & Hypotheses

**Disclaimer**: everything below is specific to *this* bike, this pack's age/history, and this
owner's riding/charging habits -- none of it is a general claim about the Empulse R platform or
Elithion Lithiumate BMS behavior. The point of writing it down isn't "this is what's wrong with
your bike too" -- it's to show *which patterns in the data are worth cross-referencing* if you're
trying to diagnose your own pack: SoC jumps between sessions with nothing logged in between,
per-module balancing frequency, per-module voltage sag under load, and how those three things
can (or can't) be tied together into a single explanation. Your numbers, your weak module (if
any), and your conclusions will very likely differ.

Interpretive observations about the bike/pack that go beyond what's verified in the decoder
(see `decode_empulse_logs.py` comments and `schema.sql` for byte-level verification methodology).
These are informed guesses based on patterns in the data, not proven facts -- flagged as such
below. For confirmed byte-level decode findings, see the code comments instead; for open
technical questions about the log format itself, see the GitHub issues.

## Module 3 aging + short-gap SoC drops (hypothesis)

**Observation**: comparing SoC at the end of one drive to SoC at the start of the next drive
(with no charge session logged in between), several cases show a real SoC drop (2-4 points)
over just a few hours of the bike sitting off -- far too fast to be normal Li-ion self-discharge
(<1%/day) if it were a continuous drain.

Cross-referencing against `module{N}_intrabalance_active` in the last 5 minutes of the preceding
drive: 6 of 8 checked cases had active balancing right before shutdown, and **module 3 was the
dominant balancer in nearly every one** (e.g. 119 of 172 total balancing samples in one case).

This lines up with two other findings:
- Module 1 showed the steepest cell-voltage sag under load (of all 7 modules) when checked
  across the full 10-year dataset -- a signature of higher internal resistance.
- Module 3 has, by a wide margin, the highest all-time average intra-balancing frequency
  (~10% of samples, vs. 3.6-6.3% for the others) -- see the Sessions dashboard's "Average
  Balancing Frequency by Module" panel.

**Working hypothesis**: a cell in module 3 has aged/drifted enough (capacity or voltage curve
slightly off from its neighbors) that it needs more frequent balancing than the rest of the
pack. Passive/resistive balancing burns the excess charge off as heat, which would show up as
exactly this kind of small SoC step-down between sessions -- aging is the likely root cause,
balancing is the mechanism that makes it visible in the logs.

**Caveat**: balancing-sample count doesn't correlate cleanly with the size of the SoC drop
(one case had 172 balancing samples but only a -2.3 point drop, another had just 2 samples but
a -3.7 point drop) -- so balancing/aging explains the general pattern and the module-3 bias
well, but not the exact magnitude of any single drop. Voltage relaxation after a hard ride
(temporarily depressed cell voltage recovering once load is removed, which a voltage-informed
SoC estimate would read as a step down) may also be contributing, and doesn't require active
balancing to have been logged -- two of the eight cases showed no balancing at all yet still
had a 2-2.5 point drop.

Not yet investigated: whether these short-gap drops have become more frequent or larger in
recent years (which would support the aging explanation more directly than a snapshot).
