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
| Run date | 2026-04-16 |
| Export | `exports/export_narababy_silas_20260416.csv` |
| Dataset | `sha256:383bff93af3fbf40ff86f1eccecd6d2fefd9a4b7d5093eb1b37174f552ac6e74` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 76.2 |
| Availability | 26/26 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("slot_drift")` through the
shared replay infrastructure. This produces a multi-window aggregate
(lookback 96h, half-life 36h, episode-boundary cutoffs) that is
directly comparable across all models.

**Canonical tuning** last ran as a 560-candidate joint sweep via
`tune_model()` on the 20260416 export, plus replay-CLI boundary checks
at finer resolution on all three axes:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.5`, `1.0`, `1.5`, `2.0`, `2.5`,
  `3.0`, `5.0`, `7.0`, `10.0`, `14.0`
- `MATCH_COST_THRESHOLD_HOURS`: `1.25`, `1.5`, `1.75`, `2.0`, `2.25`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `5`, `7`, `9`, `10`, `11`, `12`, `14`, `21`

LOOKBACK=11 is a sharp interior peak (10→68.3, 11→76.2, 12→75.3,
13→67.3). The prior 20260413 boundary artifact at LOOKBACK=28 (67.8
then) has receded to 63.5 on this export. THRESHOLD=2.25 is interior
(2.0→76.0, 2.25→76.2, 2.5→73.6). DRIFT=10.0 sits on a flat plateau
that extends from ~10 through 100+ (10→76.2, 14→76.2, 20→76.3,
30→76.2, 100→76.1); the plateau is not a boundary artifact because
headline declines past ~30 and because DRIFT=10 is the smallest value
that achieves the plateau score while preserving some recency
weighting.

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

Prior simulation runs found the synthetic DGP prefers long drift
half-life (e.g., 7.0 days) and short lookback (e.g., 7 days). The
current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=10.0`,
`LOOKBACK_DAYS=11`) now land closer to that synthetic preference
because the real data has converged toward a stable, oscillating
pattern with little directional drift — the regime for which both
synthetic and real tuning favor near-uniform weighting over short
lookbacks. The **pipeline is sound**: it correctly optimizes for
whichever data it sees. See the
[cross-model synthesis](../../research/simulation_study/research.md)
for the full classification across all four models.

## Results

### Canonical findings

Aggregate scores (weighted by recency, 36h half-life):

| Metric | Score |
|---|---|
| Headline | 76.2 |
| Count | 95.6 |
| Timing | 61.2 |

All 26 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=10.0`,
`LOOKBACK_DAYS=11`, `MATCH_COST_THRESHOLD_HOURS=2.25`) are rank 1 in
the 560-candidate sweep. LOOKBACK=11 is a sharp interior peak
(10→68.3, 11→76.2, 12→75.3, 13→67.3). THRESHOLD=2.25 is interior
(2.0→76.0, 2.25→76.2, 2.5→73.6). DRIFT=10.0 sits on a flat plateau
(10→76.2, 14→76.2, 20→76.3, 30→76.2, 100→76.1); the plateau peaks
internally near DRIFT=20 (+0.04 over DRIFT=10) and declines past 30.

Per-window timing scores range from 37.3 to 78.5. The most recent
windows (April 15) score 72-85 timing; April 14 scores 43-62; April 13
scores 37-54. The largest improvements over the prior constants
concentrate on Apr 14 midday cutoffs, where the old regime collapsed
to headline 43-58 (predicting 5-6 episodes for days with 8-9 actual)
and the new regime lifts those same windows to 69-78.

### Diagnostic findings

**Episode template stability:** 8 slots from a median of 8 episodes per
day (11 days), using the episode-level template. Only 2 of 11 days match
the median episode count of 8 (Apr 8, Apr 15), so the initial template
seed is fragile. The wider lookback's prior benefit (7 of 14 days
matching a median of 7) does not carry over because episode counts have
climbed: the 11-day window shows counts [7, 10, 7, 8, 9, 7, 7, 9, 9, 9,
8], median 8, with most days above or below the median. Template
refinement (Hungarian match + recompute slot centers) partially
compensates, and the tighter cost threshold keeps refinement anchored to
recent-pattern positions.

**Alignment quality with 2.25h threshold:** Most days have 0-2 unmatched
episodes at the episode level. Maximum assignment cost per day ranges
from 1.29h to 2.07h, all within the 2.25h threshold. The tighter
threshold (down from 3.0h) keeps the template from being pulled toward
outlier feeds while still admitting nearly every legitimate match.

**Raw vs. episode comparison:** Raw feeds produce a median of 9 per day
(mean 9.0); episodes produce a median of 8 (mean 8.2). Episode
collapsing removes 0-3 cluster-internal feeds per day, with April 5 and
April 10 showing the largest reductions (10→7 and 9→7 respectively,
each with multi-feed clusters).

## Conclusions

**Disposition: Change.** Constants updated to
`DRIFT_WEIGHT_HALF_LIFE_DAYS=10.0`, `LOOKBACK_DAYS=11`,
`MATCH_COST_THRESHOLD_HOURS=2.25`.

The LOOKBACK=14 regime degraded as the baby's recent pattern climbed
(Apr 10-15 episode-level counts 7, 7, 9, 9, 9, 8; median 9) while the
14-day median stayed at 8, pulling the template toward older, lower-count
days. Headline fell from 66.6 (20260413 export) to 65.3 (20260416
export, same constants), and the most recent retrospective scored 62.3
with the model predicting 6 episodes against 8 actual.

Narrower lookback (11 days) drops the oldest three days (Apr 2-4) and
produces a tighter fit to recent history. Tighter threshold (2.25h) sits
just above the observed maximum alignment cost (~2.1h on most days) and
rejects outliers that previously pulled drift estimates off. Near-uniform
drift weighting (DRIFT=10) reflects that per-slot timing has been
oscillating without directional drift; at an 11-day window the oldest
day carries 50% weight, which is the smallest value on the DRIFT
plateau (the flat region from DRIFT=10 through 100+).

All three metrics improved: headline +10.9 (65.3→76.2), count +3.3
(92.3→95.6), timing +14.4 (46.8→61.2). The count-timing gap has narrowed
from 45.5 to 34.4 points, its smallest value in this model's tuning
history.

## Open questions

### Model-local

- **Drift half-life trajectory:** The drift half-life has followed the
  trajectory 3.0→1.0→0.25→0.80→7.0→5.0→2.5→10.0, now at near-uniform
  weighting over an 11-day window (oldest day 50% weight). The plateau
  at DRIFT≥10 is evidence that the model's linear-drift hypothesis is
  not carrying much signal on current data — timing is oscillating, not
  drifting. Future exports should monitor whether a directional trend
  re-emerges (overnight consolidation, schedule shift) that would pull
  the optimum back toward a shorter half-life.
- **Fragile template seed at slot_count=8:** Only 2 of 11 days match the
  episode-level median of 8 (Apr 8, Apr 15). Template refinement via
  Hungarian matching partially compensates, but if episode counts drift
  further up (toward median 9), the seed may need to be rebuilt from a
  wider lookback window or with a tolerant seed rule.
- **LOOKBACK landscape volatility:** The optimum has moved 5→7→10→14→11
  across recent exports, tracking episode-count shifts. Monitor whether
  LOOKBACK=11 remains an interior peak on future exports or whether the
  optimum continues to move.

### Cross-cutting

- **Timing as shared bottleneck:** Count (95.6) still substantially
  outperforms timing (61.2) but the gap has narrowed to 34.4 points
  (from 38.5 on the prior export). This pattern persists across all
  five models — see `feedcast/research/README.md`. Slot Drift's +14.4
  timing gain on this export is the largest single-export timing
  improvement in its history; the mechanism (tighter threshold + near-
  uniform drift weighting over a narrower window) may be worth testing
  as a hypothesis in the timing-variance research item tracked at the
  hub.
