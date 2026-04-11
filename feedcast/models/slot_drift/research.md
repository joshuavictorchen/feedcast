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
| Run date | 2026-04-11 |
| Export | `exports/export_narababy_silas_20260411.csv` |
| Dataset | `sha256:138b5d3ad7d106444951acc6c56154bcd1ae94184f58a566f83c032ad41ef5ec` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 73.1 |
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
`tune_model()` on the 20260411 export, plus a 3-candidate boundary
check extending DRIFT beyond the standard grid:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.25`, `0.5`, `0.75`, `1.0`, `1.25`,
  `1.5`, `2.0`, `2.5`, `3.0`, `4.0`, `5.0`, `7.0` (standard grid),
  plus `8.0`, `10.0`, `14.0` (boundary check)
- `MATCH_COST_THRESHOLD_HOURS`: `1.0`, `1.25`, `1.5`, `1.75`, `2.0`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `3`, `4`, `5`, `6`, `7`, `10`, `14`

The sweep identified LOOKBACK=5, DRIFT=7.0, THRESHOLD=2.0 as the
winner (headline 73.1). The boundary check confirmed a flat plateau
from DRIFT=7.0 through 14.0 (7.0→73.1, 8.0→73.1, 10.0→73.2,
14.0→73.0), resolving the prior concern that DRIFT=7.0 was a boundary
artifact. THRESHOLD=2.0 is interior (1.75→<71.5, 2.0→73.1, 2.5→71.9).
LOOKBACK=5 is the clear regime winner: all top 10 candidates use it.

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
| Headline | 73.1 |
| Count | 91.1 |
| Timing | 59.3 |

All 24 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=7.0`,
`LOOKBACK_DAYS=5`, `MATCH_COST_THRESHOLD_HOURS=2.0`) are the sweep
winner. The boundary check (DRIFT=8.0, 10.0, 14.0 at LOOKBACK=5,
THRESHOLD=2.0) shows a flat plateau from 7.0 through 14.0 with a gentle
peak at 10.0 (73.2). The next challengers within the LOOKBACK=5 regime
are DRIFT=5.0 (72.9) and DRIFT=4.0 (72.6). THRESHOLD=2.0 is interior
(1.75→<71.5, 2.0→73.1, 2.5→71.9).

The LOOKBACK=10 regime, which was the prior winner, now scores 71.0 at
its best (DRIFT=0.80, THRESHOLD=1.5). The LOOKBACK=5 regime's advantage
widened from +0.9 on the 20260410(2) export to +2.1 on this export.

Per-window timing scores range from 37.1 to 73.7. The wider range
reflects the shorter lookback's sensitivity to recent pattern, with
high-weight recent windows (April 9-10) scoring substantially better
than the April 8 cluster.

### Diagnostic findings

**Episode template stability:** 8 slots from a median of 8 episodes per
day (5 days), using the episode-level template. One of 5 days matched
the median episode count of 8 (April 8) and was used for initial
template construction. Template refinement via all-day matching
compensates for the narrow initial seed. Daily episode counts range from
7 to 10, with April 6 as the high outlier (10 episodes, no clustering).

**Alignment quality with 2.0h threshold:** Most days have 0–2 unmatched
episodes at the episode level. Maximum assignment cost per day ranges
from 0.00h to 1.45h. The 2.0h threshold admits enough matches for
template refinement while still rejecting outlier feeds.

**Raw vs. episode comparison:** Raw feeds produce a median of 9 per day
(mean 8.6); episodes produce a median of 8 (mean 8.2). Episode
collapsing removes 0–2 cluster-internal feeds per day, with April 10
showing the largest reduction (9 raw to 7 episodes, one 3-feed
cluster).

## Conclusions

**Disposition: Change.** Constants updated to
`DRIFT_WEIGHT_HALF_LIFE_DAYS=7.0`, `LOOKBACK_DAYS=5`,
`MATCH_COST_THRESHOLD_HOURS=2.0`.

The LOOKBACK=5/high-DRIFT regime, first identified as a competing
alternative on the 20260410(2) export (+0.9), has strengthened to +2.1
on the 20260411 export. All top 10 sweep candidates use LOOKBACK=5. The
prior DRIFT=7.0 boundary concern is resolved: an extended sweep through
DRIFT=14.0 shows a flat plateau from 7.0 through 14.0, with the gentle
peak at 10.0 (73.2 vs. 73.1 at 7.0).

The 7.0-day drift half-life with a 5-day lookback produces near-uniform
weighting (oldest day receives ~61% of yesterday's weight). This
effectively averages slot positions over the recent window rather than
extrapolating linear drift. The shift reflects the baby's feeding
pattern stabilizing: active drift tracking now adds noise rather than
signal.

Timing improved +4.1 (55.2 to 59.3), continuing as the primary gain
channel. Count traded down -0.7 (91.8 to 91.1). The timing score
(59.3) remains the weaker component relative to count (91.1), consistent
with the cross-cutting finding, but the gap has narrowed to 31.8 points
from 36.6 on the prior export.

## Open questions

### Model-local

- **Drift half-life trajectory:** The drift half-life has followed the
  trajectory 3.0→1.0→0.25→0.80→7.0, reflecting a progression from
  aggressive recency weighting toward near-uniform averaging as the
  baby's pattern stabilizes. The 7.0-day half-life effectively disables
  drift tracking within the 5-day window. Future exports should monitor
  whether this remains optimal or whether drift tracking regains value as
  new pattern shifts emerge (growth spurts, schedule changes).
- **Template seed fragility at LOOKBACK=5:** Only 1 of 5 days matched
  the median episode count (8), providing a narrow initial template
  seed. Template refinement compensates, but a period where no day
  within the 5-day window matches the median could degrade template
  quality. Monitor on exports where episode-count variability increases.

### Cross-cutting

- **Timing as shared bottleneck:** Count (91.1) substantially outperforms
  timing (59.3). This pattern persists across all five models — see
  `feedcast/research/README.md`. The gap has narrowed from 36.6 to 31.8
  points on this export.
