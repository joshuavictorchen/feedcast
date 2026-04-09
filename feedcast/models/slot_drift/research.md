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
| Run date | 2026-04-09 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.slot_drift.analysis` |
| Canonical headline | 68.4 |
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

**Canonical tuning** last ran as a widened 588-candidate joint sweep via
`tune_model()` across the full current search domain:

- `DRIFT_WEIGHT_HALF_LIFE_DAYS`: `0.25`, `0.5`, `0.75`, `1.0`, `1.25`,
  `1.5`, `2.0`, `2.5`, `3.0`, `4.0`, `5.0`, `7.0`
- `MATCH_COST_THRESHOLD_HOURS`: `1.0`, `1.25`, `1.5`, `1.75`, `2.0`,
  `2.5`, `3.0`
- `LOOKBACK_DAYS`: `3`, `4`, `5`, `6`, `7`, `10`, `14`

This widened rerun supersedes the earlier narrower sweep plus boundary
follow-up framing. `MIN_COMPLETE_DAYS` is not swept because it is a
minimum-data guard, not a tuning knob.

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
| Headline | 68.4 |
| Count | 90.8 |
| Timing | 51.9 |

All 24 windows scored (100% availability).

The current production constants (`DRIFT_WEIGHT_HALF_LIFE_DAYS=1.0`,
`LOOKBACK_DAYS=5`, `MATCH_COST_THRESHOLD_HOURS=1.5`) are now
baseline=best in the widened 588-candidate canonical sweep. The nearest
challenger (`DRIFT_WEIGHT_HALF_LIFE_DAYS=0.75`, `LOOKBACK_DAYS=5`,
`MATCH_COST_THRESHOLD_HOURS=1.5`) scored headline 68.3, slightly below
the production 68.4. The widened search did not uncover a better
shorter-lookback, tighter-threshold, or faster-decay regime on the
current export.

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

**Disposition: Hold.** Current constants remain supported.

Production constants were previously updated based on canonical replay
(see `CHANGELOG.md` for details). The widened 2026-04-09 rerun removes
the remaining "incomplete sweep" objection on the current export:
lower half-lives, tighter thresholds, and shorter lookbacks all
underperformed the current triplet.

The 1-day drift half-life is still aggressive — the model weights
yesterday's drift far more than older days. The follow-up result makes
that aggressiveness easier to defend on the current export, but it
still implies a model that will react quickly to real pattern changes
and quickly to unusual days.

The timing score (51.9) is still the weaker component relative to
count (90.8). The follow-up removes the immediate "incomplete sweep"
objection for this export, but the remaining count-vs-timing gap likely
reflects structural limits of the fixed-slot approach.

## Open questions

### Model-local

- **Aggressive-side stability across exports:** The widened 2026-04-09
  rerun did not beat the current triplet with lower half-lives, tighter
  thresholds, or shorter lookbacks. That removes the immediate boundary
  concern on this export, but the optimum could still move if the
  volatility regime changes on later exports.
- **Slot count sensitivity:** The median slot count (8) is stable in
  the current 5-day window. A shorter lookback amplifies the impact
  of any single day's count on the median. If the baby's feeding
  pattern shifts (e.g., growth spurts), the template may respond
  quickly (advantage) or overreact to noise (risk).

### Cross-cutting

- **Timing as shared bottleneck:** Count (90.8) substantially outperforms
  timing (51.9). This pattern persists across all five models — see
  `feedcast/research/README.md`.
