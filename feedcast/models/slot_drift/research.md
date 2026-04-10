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
| Export | `exports/export_narababy_silas_20260410.csv` |
| Dataset | `sha256:8dc1ea2650b0779b6a342b90aa918bc5bd2d5412bfbef25a2df4a8e1bada504e` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 69.3 |
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

**Canonical tuning** last ran as a 588-candidate joint sweep via
`tune_model()` across the full current search domain on the 20260410
export, plus a 28-candidate boundary check at LOOKBACK=10 to verify
the winning drift half-life (0.25) and match threshold (1.0) are
interior optima:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.25`, `0.5`, `0.75`, `1.0`, `1.25`,
  `1.5`, `2.0`, `2.5`, `3.0`, `4.0`, `5.0`, `7.0`
- `MATCH_COST_THRESHOLD_HOURS`: `1.0`, `1.25`, `1.5`, `1.75`, `2.0`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `3`, `4`, `5`, `6`, `7`, `10`, `14`

Boundary check (LOOKBACK=10): `DRIFT_WEIGHT_HALF_LIFE_DAYS` 0.10–0.75
(7 values) x `MATCH_COST_THRESHOLD_HOURS` 0.75–1.5 (4 values).
DRIFT=0.25 peaked at 69.3; values below (0.10→68.4, 0.15→68.5,
0.20→68.9) and above (0.35→69.1, 0.50→69.0) scored lower.
THRESHOLD=1.0 peaked; 0.75 scored 66.3–66.9 across drift values.

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
| Headline | 69.3 |
| Count | 92.2 |
| Timing | 52.4 |

All 26 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=0.25`,
`LOOKBACK_DAYS=10`, `MATCH_COST_THRESHOLD_HOURS=1.0`) are
baseline=best in the 588-candidate canonical sweep on the 20260410
export. The nearest challengers are `DRIFT=0.75, LOOKBACK=10,
THRESHOLD=1.5` (headline 69.0, better timing but worse count) and
`DRIFT=0.5, LOOKBACK=10, THRESHOLD=1.0` (headline 69.0). No
shorter-lookback regime scored within 1.0 of the winner.

These constants were updated from the prior values (DRIFT=1.0,
LOOKBACK=5, THRESHOLD=1.5, headline=63.1 on 20260410 export) based on
the sweep (see `CHANGELOG.md` for details). The prior constants had
degraded substantially from their original 68.4 headline on the
20260327 export, primarily due to count dropping from 90.8 to 83.2 as
the baby's daily episode count became more variable (7–10 range).

Per-window timing scores range from 39.5 to 60.9. The weakest windows
are in the April 7 17:39–20:19 range — a period where the model
over-projects drift from a volatile preceding day.

### Diagnostic findings

**Episode template stability:** 7 slots from a median of 8 episodes
per day (10 days), using the episode-level template. Five of 10 days
matched the median episode count of 7 and were used for template
construction. Daily episode counts range from 7 to 10, with April 6
as the high outlier (10 episodes, no clustering — all feeds were
separated by >96 minutes).

**Alignment quality with tighter threshold (1.0h):** The tighter
threshold rejects more weak matches: most days have 1–5 unmatched
episodes at the episode level. Maximum assignment cost per day ranges
from 0.49h to 0.95h, all well within the 1.0h threshold. The higher
unmatched count is expected with a tighter threshold and reflects the
model's increased selectivity during a variable period.

**Raw vs. episode comparison:** Raw feeds produce a median of 8.5 per
day; episodes produce a median of 8 (mean 7.9). Episode collapsing
removes 0–3 cluster-internal feeds per day, with April 5 showing the
largest reduction (10 raw → 7 episodes, three 2-feed clusters).

## Conclusions

**Disposition: Change.** Constants updated to
`DRIFT_WEIGHT_HALF_LIFE_DAYS=0.25`, `LOOKBACK_DAYS=10`,
`MATCH_COST_THRESHOLD_HOURS=1.0`.

The baby's daily episode count has become more variable (7–10 range on
the 20260410 export vs. 7–9 on the prior export). The prior 5-day
lookback was too narrow to build a stable template during this volatile
period, and headline degraded from 68.4 (on 20260327 export) to 63.1
(on 20260410 export). The new constants restore count accuracy (+9.0)
by widening the lookback to 10 days, which captures more days matching
the median episode count and smooths over single-day outliers like
April 6 (10 episodes).

The 0.25-day drift half-life is very aggressive — yesterday's drift
dominates the projection with >90% of the total weight. This is
intentional: during a volatile period, multi-day drift averaging
dilutes the signal. The longer lookback provides structural stability
(template and slot count), while the short half-life provides timing
responsiveness. This is the opposite tradeoff from the prior constants
(short lookback, moderate drift), reflecting a regime change in the
data.

The timing score (52.4) remains the weaker component relative to count
(92.2). The count-vs-timing gap likely reflects structural limits of
the fixed-slot approach, consistent with the cross-cutting finding.

## Open questions

### Model-local

- **Drift half-life stability across exports:** The 0.25-day half-life
  is the most aggressive setting tested. It performed best on this
  export's volatile period but may overreact to single anomalous days.
  If the baby's pattern stabilizes, a longer half-life may re-emerge
  as optimal — the direction reversed between exports (3.0→1.0 on
  20260327, 1.0→0.25 on 20260410).
- **Lookback direction reversal:** LOOKBACK moved from 7→5 on the prior
  export and now 5→10 on this export. The direction depends on
  episode-count variability: when counts are stable, shorter lookback
  reacts faster; when counts are volatile, longer lookback stabilizes
  the template. Future exports should check whether 10 remains
  appropriate or whether variability has subsided.

### Cross-cutting

- **Timing as shared bottleneck:** Count (90.8) substantially outperforms
  timing (51.9). This pattern persists across all five models — see
  `feedcast/research/README.md`.
