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
| Run date | 2026-04-12 |
| Export | `exports/export_narababy_silas_20260412.csv` |
| Dataset | `sha256:1fc8695c14bda5dabdbdf2c554024159f9efbc5e853e5ed449ed1c4f7156f481` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 65.8 |
| Availability | 24/24 windows (100%) |
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
`tune_model()` on the 20260412 export, plus boundary checks extending
LOOKBACK to 21 and 28 and THRESHOLD to 3.5 and 4.0:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.25`, `0.5`, `0.75`, `1.0`, `1.25`,
  `1.5`, `2.0`, `2.5`, `3.0`, `4.0`, `5.0`, `7.0`
- `MATCH_COST_THRESHOLD_HOURS`: `1.0`, `1.25`, `1.5`, `1.75`, `2.0`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `3`, `4`, `5`, `6`, `7`, `10`, `14`

Two competing regimes: LOOKBACK=14 (headline 65.9) and LOOKBACK=7
(headline 65.8). Boundary checks revealed LOOKBACK=14 is not interior
(performance continues climbing: 14→65.9, 21→66.5, 28→66.6).
LOOKBACK=7 is a confirmed interior peak (6→58.3, 7→65.8, 10→61.0).
THRESHOLD=3.0 is interior at LOOKBACK=7 (2.5→63.3, 3.0→65.8,
3.5→worse). DRIFT has a flat plateau from 3.0 to 7.0 at LOOKBACK=7
(range 0.2).

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
| Headline | 65.8 |
| Count | 89.3 |
| Timing | 48.9 |

All 24 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=5.0`,
`LOOKBACK_DAYS=7`, `MATCH_COST_THRESHOLD_HOURS=3.0`) are rank 6 in the
588-sweep, within 0.1 of the LOOKBACK=14 sweep winner (65.9). The
LOOKBACK=14+ regime was not selected because LOOKBACK=14 is a boundary
artifact: performance continues climbing through 21 (66.5) and 28
(66.6) without plateauing.

Within the LOOKBACK=7 regime, DRIFT shows a flat plateau from 3.0 to
7.0 (65.6-65.8, range 0.2) with 5.0 at the peak. THRESHOLD=3.0 is
interior (2.5→63.3, 3.0→65.8, 3.5→worse). LOOKBACK=7 is an interior
peak with a bimodal landscape: 6→58.3, 7→65.8, 10→61.0.

Per-window timing scores range from 33.2 to 59.9. The most recent
windows (April 11) show timing scores of 33-54, substantially improved
from the prior constants (30-36 on the same windows). April 10 windows
score 52-66.

### Diagnostic findings

**Episode template stability:** 7 slots from a median of 7 episodes per
day (7 days), using the episode-level template. Four of 7 days matched
the median episode count of 7 (April 5, 7, 10, 11) and were used for
initial template construction. Daily episode counts range from 7 to 10,
with April 6 as the high outlier (10 episodes, no clustering). The
template seed is substantially more robust than the prior LOOKBACK=5
configuration, where only 1 of 5 days matched the median.

**Alignment quality with 3.0h threshold:** Most days have 0-2 unmatched
episodes at the episode level. Maximum assignment cost per day ranges
from 0.37h to 2.68h. The 3.0h threshold admits all episodes on days
matching the median count (zero unmatched on 4 of 7 days) while allowing
1-3 unmatched on higher-count days.

**Raw vs. episode comparison:** Raw feeds produce a median of 9 per day
(mean 8.7); episodes produce a median of 7 (mean 7.9). Episode
collapsing removes 0-3 cluster-internal feeds per day, with April 5 and
April 10 showing the largest reductions (10→7 and 9→7 respectively,
each with multi-feed clusters).

## Conclusions

**Disposition: Change.** Constants updated to
`DRIFT_WEIGHT_HALF_LIFE_DAYS=5.0`, `LOOKBACK_DAYS=7`,
`MATCH_COST_THRESHOLD_HOURS=3.0`.

The baby's daily episode count has shifted from a stable 8 toward 7
(episode counts over 7-day window: 7, 10, 7, 8, 9, 7, 7; median 7).
The LOOKBACK=5 regime that was optimal on the 20260411 export degraded
from headline 73.1 to 64.4 as this transition progressed. The headline
drop is driven by timing (59.3→46.0), with April 11 windows scoring
30-36 on timing.

The LOOKBACK=7 regime provides a wider window for template building
during this transition. Four of 7 days match the median episode count
(vs. 1 of 5 at LOOKBACK=5), giving a more robust template seed. The
bimodal LOOKBACK landscape (peaks at 7 and 14+, valleys at 6 and 10)
reflects a tradeoff: LOOKBACK=7 trades count (89.3 vs. 93.2) for timing
(48.9 vs. 46.0). LOOKBACK=14+ achieves slightly higher headline (65.9)
but is a boundary artifact (performance continues climbing at 21, 28).

Timing improved +2.9 (46.0 to 48.9), with the largest gains on the most
recent windows (April 11 retrospective: 35.6→53.5). Count traded down
-3.9 (93.2 to 89.3). The count-timing gap (89.3 vs. 48.9) has widened
to 40.4 points from 31.8 on the prior export, consistent with the
cross-cutting finding that timing is the shared bottleneck.

## Open questions

### Model-local

- **Drift half-life trajectory:** The drift half-life has followed the
  trajectory 3.0→1.0→0.25→0.80→7.0→5.0, reflecting a recent return
  toward mild recency weighting after a period of near-uniform averaging.
  The 5.0-day half-life with a 7-day lookback gives the oldest day ~44%
  of yesterday's weight. Future exports should monitor whether the
  episode-count transition from 8 to 7 stabilizes (allowing longer
  smoothing) or continues shifting (requiring shorter half-life).
- **Bimodal LOOKBACK landscape:** LOOKBACK=7 and LOOKBACK=14+ form two
  distinct peaks separated by a valley at LOOKBACK=10. The LOOKBACK=14+
  peak has no stable upper bound (performance continues climbing at 21,
  28), suggesting it overfits to older, stale history. Monitor whether
  this regime stabilizes as the episode-count transition completes.
- **Episode-count transition:** Daily episode count has dropped from
  median 8 (on 20260411 export, LOOKBACK=5) to median 7 (on 20260412
  export, LOOKBACK=7). If this stabilizes at 7, the template should
  become more robust and timing scores should recover.

### Cross-cutting

- **Timing as shared bottleneck:** Count (89.3) substantially outperforms
  timing (48.9). This pattern persists across all five models — see
  `feedcast/research/README.md`. The gap has widened from 31.8 to 40.4
  points on this export, likely driven by the episode-count transition
  disrupting template alignment.
