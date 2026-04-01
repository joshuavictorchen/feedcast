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

## Last canonical run

| Field | Value |
|---|---|
| Run date | 2026-03-29 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.research` |
| Canonical headline | 68.4 |
| Availability | 24/24 windows (100%) |
| Full output | [`research_results.txt`](research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("slot_drift")` through the
shared replay infrastructure. This produces a multi-window aggregate
(lookback 96h, half-life 36h, episode-boundary cutoffs) that is
directly comparable across all models.

**Canonical tuning** sweeps three constants jointly via `tune_model()`:
`DRIFT_WEIGHT_HALF_LIFE_DAYS` (8 values: 1.0–7.0),
`MATCH_COST_THRESHOLD_HOURS` (4 values: 1.5–3.0), and `LOOKBACK_DAYS`
(4 values: 5–14) — 128 total candidates. `MIN_COMPLETE_DAYS` is not
swept because it is a minimum-data guard, not a tuning knob.

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

## Results

### Canonical findings

Aggregate scores (weighted by recency, 36h half-life):

| Metric | Score |
|---|---|
| Headline | 68.4 |
| Count | 90.8 |
| Timing | 51.9 |

All 24 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=1.0`,
`LOOKBACK_DAYS=5`, `MATCH_COST_THRESHOLD_HOURS=1.5`) are the best of
128 candidates in the canonical sweep. The next-best candidate
(DRIFT=7.0, LOOKBACK=10, THRESHOLD=2.5, headline=67.1) trades timing
for count but scores lower overall. No candidate with equal or higher
availability outperforms the current constants.

These constants were updated from the prior values (DRIFT=3.0,
LOOKBACK=7, THRESHOLD=2.0, headline=59.2) based on this sweep.
The improvement was primarily in timing (+11.5 points), which was
the identified bottleneck. All three changes push the model toward
more recent, more focused data.

Per-window timing scores range from 27.9 to 61.0. The weakest windows
are in the March 24 14:45–18:02 range — a period with cluster feeds
(3-feed episode at 20:16) that disrupts the template for the
following day's forecast.

### Diagnostic findings

**Episode template stability:** 8 slots from a median of 8 episodes
per day (5 days). Two days matched the canonical count exactly and
were used for template construction. Daily episode counts range from
7 to 9.

**Alignment quality with tighter threshold (1.5h):** Most days have
0-2 unmatched episodes. Maximum assignment cost per day ranges from
0.73h to 1.37h, all within the 1.5h threshold. March 22 is an
outlier with 3 unmatched episodes out of 7 — its feeding pattern
is the most distant from the current template, which is expected
since it's the oldest day in the 5-day lookback.

**Raw vs. episode comparison:** Raw feeds produce a median of 9 per
day; episodes produce 8. The difference confirms that episode
collapsing removes ~1 cluster-internal feed per day on average.

## Conclusions

**Disposition: Change.** Constants updated.

Production constants were updated based on the 128-candidate canonical
sweep (see `CHANGELOG.md` for details). The new values
(`DRIFT_WEIGHT_HALF_LIFE_DAYS=1.0`, `LOOKBACK_DAYS=5`,
`MATCH_COST_THRESHOLD_HOURS=1.5`) improved the headline from 59.2 to
68.4 (+9.2), with the timing bottleneck improving from 40.4 to 51.9
(+11.5) and count from 87.6 to 90.8 (+3.2). Availability remained at
24/24.

The 1-day drift half-life is aggressive — the model now weights
yesterday's drift almost entirely and older days negligibly. This
makes it highly responsive to recent pattern changes but potentially
volatile if a single unusual day occurs. The 5-day lookback and 1.5h
threshold complement this by keeping the template focused on recent,
well-matching data.

The timing score (51.9) is still the weaker component relative to
count (90.8). The winning constants sit at the grid boundary
(lowest half-life, shortest lookback, tightest threshold tested),
so further improvement from more aggressive values cannot be ruled
out. However, the remaining count-vs-timing gap likely reflects
structural limits of the fixed-slot approach.

## Open questions

### Model-local

- **Drift half-life stability:** The 1-day half-life is the lowest
  value tested and the sweep winner. It may be worth monitoring
  whether this causes volatility on unusual feeding days — a single
  outlier day now dominates the drift estimate.
- **Slot count sensitivity:** The median slot count (8) is stable in
  the current 5-day window. A shorter lookback amplifies the impact
  of any single day's count on the median. If the baby's feeding
  pattern shifts (e.g., growth spurts), the template may respond
  quickly (advantage) or overreact to noise (risk).

### Cross-cutting

- **Timing as shared bottleneck:** If other models also show stronger
  count than timing scores, the timing gap may reflect dataset
  variability rather than model-specific issues. See
  `feedcast/research/index.md` for cross-model patterns once all
  canonical evaluations are complete.
