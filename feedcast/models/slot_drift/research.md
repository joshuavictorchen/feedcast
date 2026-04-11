# Slot Drift Research

> `design.md` documents why the model works the way it does.
> `methodology.md` is the report-facing description.
> This file is the evidence: current support and challenges for the
> model's design and constants.

## Overview

Slot Drift is a daily template model — it finds recurring feeding
episode slots and tracks their drift. The key research questions are:

1. How well does the model forecast under canonical multi-window
   evaluation?
2. Are the structural constants (`LOOKBACK_DAYS`,
   `MATCH_COST_THRESHOLD_HOURS`, `DRIFT_WEIGHT_HALF_LIFE_DAYS`)
   well-tuned?
3. Is the episode-level template stable enough to justify a fixed-slot
   approach?

## Last run

| Field | Value |
|---|---|
| Run date | 2026-04-10 |
| Export | `exports/export_narababy_silas_20260410(2).csv` |
| Dataset | `sha256:ff8b0a112f77742af35b44e97652b6108915a609526619b808546434315927b8` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 71.1 |
| Availability | 25/25 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("slot_drift")` through the
shared replay infrastructure. This produces a multi-window aggregate
(lookback 96h, half-life 36h, episode-boundary cutoffs) that is
directly comparable across all models.

**Canonical tuning** last ran as targeted sweeps (84 + 24 + 54 + 9
candidates) followed by a 588-candidate joint sweep via `tune_model()`
across the full search domain on the 20260410(2) export:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.25`, `0.5`, `0.75`, `1.0`, `1.25`,
  `1.5`, `2.0`, `2.5`, `3.0`, `4.0`, `5.0`, `7.0`
- `MATCH_COST_THRESHOLD_HOURS`: `1.0`, `1.25`, `1.5`, `1.75`, `2.0`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `3`, `4`, `5`, `6`, `7`, `10`, `14`

The targeted sweeps identified DRIFT=0.80, LOOKBACK=10, THRESHOLD=1.5
as the interior peak in the LOOKBACK=10 regime (0.75→69.8, 0.80→71.1,
0.85→70.8; THRESHOLD 1.25→67.7, 1.5→71.1, 1.75→63.6). The full sweep
found a competing regime at LOOKBACK=5, DRIFT=7.0, THRESHOLD=2.0
(headline 72.0, +0.9 above baseline), but DRIFT=7.0 is a boundary
value with a flattening gradient (4.0→71.7, 5.0→71.9, 7.0→72.0) and
LOOKBACK=5 is more exposed to single-day outliers.

`MIN_COMPLETE_DAYS` is not swept because it is a minimum-data guard,
not a tuning knob.

### Model-specific diagnostics

**Daily feed and episode counts** show the raw and episode-collapsed
feed counts per day, providing context for template stability.

**Template construction** reports which days were used to build the
template and the resulting slot positions. The template is built from
days matching the canonical slot count (median daily episode count).

**Trial alignment** matches each day's episodes against the template
using Hungarian assignment with the production cost threshold. Reports
matched/unmatched counts and maximum assignment cost per day. This
validates whether the template is a reasonable fit for the observed
feeding pattern.

**Raw vs. episode comparison** reports both raw-feed and episode-level
statistics side by side, confirming the episode collapsing decision
documented in `design.md`.

### Simulation study

Synthetic validation uses two fixtures. Deterministic linear-drift
histories verify slot count recovery, template construction, and
next-day drift extrapolation. Canonical replay needs a different
fixture: materially non-zero per-slot drift plus bounded Gaussian
jitter so `LOOKBACK_DAYS`, `MATCH_COST_THRESHOLD_HOURS`, and
`DRIFT_WEIGHT_HALF_LIFE_DAYS` are meaningfully distinguishable (zero
jitter or small drift lets many parameter combinations tie).

On this DGP, canonical replay prefers
`DRIFT_WEIGHT_HALF_LIFE_DAYS=7.0` and `LOOKBACK_DAYS=7` over the
production values `1.0` and `5`. The production half-life decays
observation weight by 50% per day, which over-reacts to jitter when
the true drift is stationary — longer smoothing recovers the linear
trend more reliably. The **pipeline is sound** — it correctly
optimizes for whichever data it sees. On synthetic data with
stationary drift, it picks parameters that exploit the clean trend.
On real data where drift is non-stationary (template reorganization,
slot appearance or disappearance), it picks parameters that react
quickly to recent changes. The production constants are not optimal
for the synthetic DGP, but that is because they were tuned for real
data that does not fully conform to the stationary drift hypothesis.
See the
[cross-model synthesis](../../research/simulation_study/research.md)
for the full classification across all four models.

## Results

### Canonical findings

Aggregate scores (weighted by recency, 36h half-life):

| Metric | Score |
|---|---|
| Headline | 71.1 |
| Count | 90.2 |
| Timing | 56.4 |

All 25 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=0.80`,
`LOOKBACK_DAYS=10`, `MATCH_COST_THRESHOLD_HOURS=1.5`) are the interior
peak in the LOOKBACK=10 regime. The 588-candidate full sweep found a
competing regime at LOOKBACK=5, DRIFT=7.0, THRESHOLD=2.0 (headline
72.0, +0.9), but DRIFT=7.0 is a boundary value. Within the LOOKBACK=10
regime, the nearest challengers are DRIFT=0.85 (headline 70.8) and
DRIFT=0.75 (headline 69.8).

These constants were updated from the prior values (DRIFT=0.25,
LOOKBACK=10, THRESHOLD=1.0, headline=65.7 on 20260410(2) export). The
prior aggressive 0.25-day half-life had degraded from its original 69.3
headline on the earlier 20260410 export as the baby's timing pattern
began stabilizing, making the aggressive recency weighting
counterproductive.

Per-window timing scores range from 46.7 to 78.4. Timing improved
substantially (+8.9) from the prior constants, narrowing the
count-vs-timing gap.

### Diagnostic findings

**Episode template stability:** 7 slots from a median of 8 episodes
per day (10 days), using the episode-level template. Five of 10 days
matched the median episode count of 7 and were used for template
construction. Daily episode counts range from 7 to 10, with April 6
as the high outlier (10 episodes, no clustering).

**Alignment quality with 1.5h threshold:** Most days have 0–4 unmatched
episodes at the episode level. Maximum assignment cost per day ranges
from 0.49h to 1.49h. The looser threshold (vs. prior 1.0h) admits more
matches for drift estimation while still rejecting clearly misplaced
feeds.

**Raw vs. episode comparison:** Raw feeds produce a median of 8 per
day (mean 8.6); episodes produce a median of 8 (mean 7.9). Episode
collapsing removes 0–3 cluster-internal feeds per day, with April 5
showing the largest reduction (10 raw → 7 episodes, three 2-feed
clusters).

## Conclusions

**Disposition: Change.** Constants updated to
`DRIFT_WEIGHT_HALF_LIFE_DAYS=0.80`, `LOOKBACK_DAYS=10`,
`MATCH_COST_THRESHOLD_HOURS=1.5`.

The prior aggressive 0.25-day drift half-life degraded as the baby's
feeding pattern began stabilizing. Headline dropped from 69.3 to 65.7
on the 20260410(2) export, with timing falling from 52.4 to 47.5. The
0.25-day half-life assigns ~94% of weight to the most recent day,
which over-reacts to day-to-day noise once timing shifts become smaller
and more consistent.

The new 0.80-day half-life gives about 42% weight to yesterday's drift,
smoothing the estimate across 2-3 days of effective history. The looser
1.5h threshold admits more matches for drift estimation. Together these
changes improved timing by +8.9 (the primary gain this round) while
count traded down only -1.5. LOOKBACK=10 is unchanged: the 7-10
episode-count variability still benefits from the wider stabilizing
window.

A competing regime at LOOKBACK=5, DRIFT=7.0, THRESHOLD=2.0 scored
72.0 (+0.9 above baseline). This regime effectively disables drift
tracking (7-day half-life ≈ uniform weighting) and uses a very short,
loosely matched window. It was not chosen because DRIFT=7.0 is a
boundary value and LOOKBACK=5 is more vulnerable to single-day
outliers, but it should be monitored on future exports.

The timing score (56.4) remains the weaker component relative to count
(90.2), consistent with the cross-cutting finding.

## Open questions

### Model-local

- **Drift half-life trajectory:** The drift half-life has oscillated
  across exports (3.0→1.0→0.25→0.80), tracking the baby's pattern
  stability. The 0.80-day value is moderate and interior, but the
  competing LOOKBACK=5/DRIFT=7.0 regime suggests the pattern may be
  stabilizing enough that drift tracking adds limited value. Future
  exports should check whether the optimal drift continues to lengthen
  or stabilizes.
- **LOOKBACK=5 vs. LOOKBACK=10:** LOOKBACK=10 has been stable for two
  consecutive tunes, but the LOOKBACK=5/DRIFT=7.0 regime scored within
  0.9 of the winner on this export. If episode-count variability
  decreases (currently 7-10), the shorter window with its better count
  score (93.6 vs 90.2) may reassert itself.

### Cross-cutting

- **Timing as shared bottleneck:** Count (90.2) substantially outperforms
  timing (56.4). This pattern persists across all five models — see
  `feedcast/research/README.md`.
