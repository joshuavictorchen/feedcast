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
| Run date | 2026-04-13 |
| Export | `exports/export_narababy_silas_20260413.csv` |
| Dataset | `sha256:1820a6f33b499f22c5adbfc8bbb0538fca2366fbf4661452b57fddd31a0a6d8d` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 66.6 |
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

**Canonical tuning** last ran as a 588-candidate joint sweep via
`tune_model()` on the 20260413 export, plus boundary checks extending
LOOKBACK to 12, 13, 15, 16, 21, and 28 and THRESHOLD to 3.5 and 4.0:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.25`, `0.5`, `0.75`, `1.0`, `1.25`,
  `1.5`, `2.0`, `2.5`, `3.0`, `4.0`, `5.0`, `7.0`
- `MATCH_COST_THRESHOLD_HOURS`: `1.0`, `1.25`, `1.5`, `1.75`, `2.0`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `3`, `4`, `5`, `6`, `7`, `10`, `14`

LOOKBACK=14 is a sharp interior peak (13→64.9, 14→66.6, 15→62.0). The
boundary artifact identified on the prior export (14→65.9, 21→66.5,
28→66.6 climbing monotonically) has resolved: LOOKBACK=21 now scores
62.1, below LOOKBACK=14. LOOKBACK=28 (67.8) is isolated beyond a
valley at 21. DRIFT=2.5 is interior at LOOKBACK=14 (2.0→66.0,
2.5→66.6, 3.0→66.3). THRESHOLD=3.0 is near-tied with 3.5 (66.6 vs.
66.8, both interior).

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
| Headline | 66.6 |
| Count | 88.8 |
| Timing | 50.3 |

All 25 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=2.5`,
`LOOKBACK_DAYS=14`, `MATCH_COST_THRESHOLD_HOURS=3.0`) are rank 1 in the
588-sweep. LOOKBACK=14 is a sharp interior peak with fine-grained
boundary confirmation (13→64.9, 14→66.6, 15→62.0). The LOOKBACK=14+
boundary artifact from the prior export has resolved: LOOKBACK=21 now
scores 62.1, below both 14 (66.6) and 7 (64.0). LOOKBACK=28 (67.8) is
isolated beyond a valley at 21.

The LOOKBACK landscape shows two interior peaks at 7 (64.0) and 14
(66.6), separated by a valley at 10 (61.7). DRIFT=2.5 is interior at
LOOKBACK=14 (2.0→66.0, 2.5→66.6, 3.0→66.3), with a gentle plateau
from 2.0 to 4.0 (range 0.6). THRESHOLD=3.0 is near-tied with 3.5
(66.6 vs. 66.8); both are interior (4.0→63.2).

Per-window timing scores range from 38.7 to 62.4. The most recent
windows (April 12) show timing scores of 44-62, improved from the prior
constants (37-54 on the same windows). April 11 windows score 41-58.

### Diagnostic findings

**Episode template stability:** 7 slots from a median of 7 episodes per
day (14 days), using the episode-level template. Seven of 14 days
matched the median episode count of 7 (March 31, April 1, 4, 5, 7, 10,
11) and were used for initial template construction. Daily episode
counts range from 7 to 10, with April 6 as the high outlier (10
episodes, no clustering). The template seed is substantially more robust
than the prior LOOKBACK=7 configuration, where only 1 of 7 days matched
the median.

**Alignment quality with 3.0h threshold:** Most days have 0-2 unmatched
episodes at the episode level. Maximum assignment cost per day ranges
from 0.31h to 2.83h. The 3.0h threshold admits all episodes on days
matching the median count (zero unmatched on 7 of 14 days) while
allowing 1-3 unmatched on higher-count days (April 3, 6, 9, 12).

**Raw vs. episode comparison:** Raw feeds produce a median of 9 per day
(mean 8.7); episodes produce a median of 8 (mean 7.9). Episode
collapsing removes 0-3 cluster-internal feeds per day, with April 5 and
April 10 showing the largest reductions (10→7 and 9→7 respectively,
each with multi-feed clusters).

## Conclusions

**Disposition: Change.** Constants updated to
`DRIFT_WEIGHT_HALF_LIFE_DAYS=2.5`, `LOOKBACK_DAYS=14`,
`MATCH_COST_THRESHOLD_HOURS=3.0`.

Episode-count volatility continues (7-10 range, median 7 at episode
level over 14 days). The prior LOOKBACK=7 regime degraded from headline
65.8 to 64.0 as only 1 of 7 days matched the episode median, producing
a fragile single-day template seed.

LOOKBACK=14 is now a confirmed interior peak (13→64.9, 14→66.6,
15→62.0). The boundary artifact from the prior export (14→65.9,
21→66.5, 28→66.6 climbing monotonically) has resolved: LOOKBACK=21 now
scores 62.1. The wider lookback captures 7 of 14 days matching the
median episode count, providing a robust template seed.

DRIFT=2.5 provides moderate recency weighting (oldest data at ~2.7% of
yesterday's weight), focusing drift estimation on recent data while
using the full 14-day window for template stability.

All three metrics improved: headline +2.6 (64.0→66.6), count +1.7
(87.1→88.8), timing +3.0 (47.3→50.3). The count-timing gap (88.8 vs.
50.3) has narrowed slightly to 38.5 points from 40.4 on the prior
export.

## Open questions

### Model-local

- **Drift half-life trajectory:** The drift half-life has followed the
  trajectory 3.0→1.0→0.25→0.80→7.0→5.0→2.5, now at moderate recency
  weighting. The 2.5-day half-life with a 14-day lookback gives the
  oldest day ~2.7% of yesterday's weight. Future exports should monitor
  whether the episode-count volatility (7-10 range) stabilizes, which
  could allow longer smoothing.
- **Bimodal LOOKBACK landscape:** LOOKBACK=7 and LOOKBACK=14 form two
  interior peaks separated by a valley at LOOKBACK=10. LOOKBACK=28
  (67.8) is isolated beyond a valley at 21 (62.1); its status as
  interior or boundary is unknown (35, 42 not checked). Monitor whether
  the LOOKBACK=14 peak remains interior on future exports.
- **Episode-count volatility:** Daily episode counts range from 7 to 10
  (median 7 at episode level over 14 days, median 8 over 7 days). The
  8→7 transition identified on the prior export has not fully stabilized.
  If episode counts consolidate around 7, the 14-day lookback provides
  ample template-building data. If they shift upward, the median could
  change, affecting template slot count.

### Cross-cutting

- **Timing as shared bottleneck:** Count (88.8) substantially outperforms
  timing (50.3). This pattern persists across all five models — see
  `feedcast/research/README.md`. The gap has narrowed slightly from 40.4
  to 38.5 points on this export, with timing improving +3.0.
