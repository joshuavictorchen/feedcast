# Latent Hunger Research

> `design.md` documents why the model works the way it does.
> `methodology.md` is the report-facing description.
> This file is the evidence: current support and challenges for the
> model's design and constants.

## Overview

Latent Hunger models feeding as a hidden hunger state that rises over
time and is partially reset by each feed. The key research questions
are:

1. How well does the model forecast under canonical multi-window
   evaluation?
2. Is the production `SATIETY_RATE` well-tuned under canonical scoring?
3. Do the internal walk-forward diagnostics (gap MAE, feed count MAE)
   agree with canonical ranking direction?
4. Does the evidence still support the multiplicative satiety design
   over the additive alternative?

## Last run

| Field | Value |
|---|---|
| Run date | 2026-04-09 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.latent_hunger.analysis` |
| Canonical headline | 66.9 |
| Availability | 24/24 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("latent_hunger")` through
the shared replay infrastructure. This produces a multi-window
aggregate (lookback 96h, half-life 36h, episode-boundary cutoffs) that
is directly comparable across all models.

**Canonical tuning** last ran as a widened 16-candidate
`SATIETY_RATE` sweep via `tune_model()`:

`0.01`, `0.02`, `0.03`, `0.04`, `0.05`, `0.1`, `0.15`, `0.2`, `0.25`,
`0.3`, `0.35`, `0.4`, `0.5`, `0.6`, `0.7`, `0.8`

This widened rerun supersedes the earlier "full sweep plus low-end
follow-up" framing. Growth rate is estimated at runtime from recent
episodes and is not overridable via constant overrides, so it is not
part of the sweep. Candidates are ranked by availability tier first,
then headline score.

### Objective comparison contract

Canonical and internal diagnostics answer different questions. Canonical
evaluation uses the shared replay stack: bottle-only scoring events,
episode-boundary cutoffs over the most recent 96 hours, and the 24-hour
headline scorer. The local diagnostics use the model's own merged,
episode-collapsed history and optimize walk-forward gap/count errors
such as `gap1_MAE`, `gap3_MAE`, and feed-count MAE. When these
objectives disagree, interpret the result as a comparison between
different targets rather than as a silent tie-break between equivalent
metrics.

### Model-specific diagnostics

**Breastfeed merge impact** (Section 1) documents which events gain
attributed breastfeed volume. Currently negligible (3/121 events).

**Volume-to-gap relationship** (Section 2) measures the correlation
between feed volume and subsequent gap. This is the empirical basis for
the volume-sensitive satiety model — the design question `design.md`
addresses in its multiplicative vs. additive comparison.

**Circadian structure** (Section 3) bins gaps and volumes by time of
day. This is the evidence for the circadian modulation design decision:
volume already correlates with time-of-day (larger overnight feeds ->
longer gaps), so explicit circadian modulation adds no benefit.

**Additive vs. multiplicative satiety** (Section 4) runs parallel grid
searches to compare the two satiety models on walk-forward gap MAE.
This is the evidence for the multiplicative design choice in
`design.md`. The key signal is `pred_std`: additive collapses to
near-constant predictions while multiplicative produces meaningful
volume-sensitive variation.

**Multiplicative + circadian** (Section 5) tests whether adding
circadian modulation on top of volume sensitivity improves walk-forward
accuracy. Joint-refined parameters are the best the non-episode
exploratory search can achieve.

**Lookback window sensitivity** (Section 6) compares fitting on
different history windows (3-14 days vs. full). Informs the
`LOOKBACK_DAYS` and `RECENCY_HALF_LIFE_HOURS` choices in `design.md`.

**24h holdout** (Section 7) simulates a true holdout forecast from 24h
before cutoff, re-fitting parameters from only prior data. Tests
whether the model generalizes beyond the training window.

**Naive baseline comparison** (Section 8) benchmarks against last-gap
and mean-3-gap predictors, establishing that the model adds value
beyond simple heuristics.

**Volume prediction strategy** (Section 9) compares global vs.
recency-weighted median volumes. Informs the simulation volume choice.

**Simulation-study constraint:** Synthetic recovery tests must include
varying observed volumes; otherwise the growth-rate estimator can absorb
the satiety effect and make `SATIETY_RATE` unidentifiable. Synthetic
forecast and canonical checks should end in a constant-volume tail,
because the production forecaster simulates future gaps at the recent
median volume rather than a varying future volume sequence.

**Episode-level comparison** (Section 10) contrasts raw-event and
episode-collapsed performance. This is the evidence for the episode-
level history design decision in `design.md` — the most impactful
single change in the model's history (~20% gap MAE improvement).

## Results

### Canonical findings

The pre-update baseline (sr=0.257) and the current sweep winner
(sr=0.05) compared as follows:

| Metric | Pre-update (sr=0.257) | Sweep winner (sr=0.05) |
|---|---|---|
| Headline | 66.3 | 66.9 |
| Count | 92.6 | 94.0 |
| Timing | 47.8 | 47.9 |

All 24 windows scored (100% availability) for every widened-sweep
candidate. Production remains `sr=0.05`, and the current production
canonical score is headline 66.9, confirmed by re-running the research
script after widening the search domain (baseline=best in
`artifacts/research_results.txt`).

The widened-grid top 5 are `0.05`, `0.1`, `0.15`, `0.2`, and `0.25`,
all within 0.5 headline points of each other. The surface is still
shallow, but values below `0.05` do not improve the headline on the
current export. The gain over the prior production value comes
primarily from count (+1.4) with timing nearly unchanged (+0.1).

Per-window timing scores range from 28.8 to 59.2. The weakest window
is March 26 13:56 (headline 49.5, timing 28.8) — a period immediately
following a cluster feed (4.0oz at 13:56, 1.0oz top-up at 15:18) where
the model over-predicts the gap after the initial large feed.

### Diagnostic findings

**Multiplicative vs. additive:** Multiplicative satiety (gap1_MAE=0.720h,
pred_std=0.600h) outperforms additive (gap1_MAE=0.742h,
pred_std=0.202h) on the raw-data walk-forward evaluation. The critical
difference is prediction diversity — additive collapses to near-constant
gaps, confirming the design rationale in `design.md`.

**Circadian modulation:** Best circadian amplitude is 0.050 with
gap1_MAE=0.708h, a marginal improvement over no-circadian 0.720h.
Joint refinement with circadian achieves 0.683h, but the gain does not
survive episode-level data (where volume already encodes time-of-day
effects). Production holds `CIRCADIAN_AMPLITUDE=0.0`.

**Episode-level impact:** Episode collapsing improves all metrics
substantially (gap1_MAE 0.720h->0.580h, fcount_MAE 1.32->1.04). Volume-
gap correlation weakens (0.331->0.256), confirming that raw-data
correlation was partly a cluster artifact. This remains the strongest
single design decision.

**Internal vs. canonical metric disagreement:** The episode-level grid
search finds best sr=0.645, while canonical scoring finds best sr=0.05.
The metrics disagree on direction. At sr=0.05, the satiety effect
(`1 - exp(-rate * volume)`) is 0.049 for 1oz and 0.181 for 4oz — a
3.7x ratio, so meaningful volume sensitivity remains. However, the
lower rate produces more uniform gap predictions (lower pred_std),
which improves canonical episode-count matching. The internal gap-MAE
metric rewards stronger per-feed differentiation; the canonical metric
rewards consistent 24h trajectory quality.

**Holdout 24h:** Predicted 7 feeds vs. 9 actual, mean timing error
0.32h on matched pairs. The under-count is consistent with the
canonical finding that count is the stronger metric component — the
model's timing on matched feeds is good, but it misses cluster feeds.

**Naive baselines:** All model variants beat last-gap (0.820h) and
mean-3-gaps (0.780h). The multiplicative model at 0.720h represents a
12% improvement over last-gap.

## Conclusions

**Disposition: Hold.** Current `SATIETY_RATE=0.05` remains supported.

The canonical sweep selected sr=0.05 with headline +0.550
(66.3->66.9). The widened 2026-04-09 rerun then checked whether that
result was just a lower-bound artifact. It was not on the current
export: rates below `0.05` underperformed, while nearby rates above
`0.05` stayed close enough to confirm a shallow low-end plateau rather
than a hidden lower optimum.

The internal diagnostics (gap1_MAE) and canonical scoring (headline)
disagree on the optimal direction for `SATIETY_RATE`. The internal
metric evaluates one-step-ahead gap prediction accuracy and rewards
stronger per-feed differentiation (higher sr). The canonical metric
evaluates 24h forecast quality including episode count matching and
horizon weighting, and rewards consistency (lower sr). The model
retains meaningful volume sensitivity at sr=0.05 — the satiety effect
scales 3.7x from 1oz to 4oz — but the absolute variation in predicted
gaps is smaller than at the internal diagnostic optimum.

## Open questions

### Model-local

- **Low-rate stability across exports:** The current export no longer
  shows a lower-bound ambiguity, but the optimum still sits in a shallow
  low-rate plateau. If future exports shift the best value upward, that
  would suggest the current preference is more local than structural.
- **Internal-canonical metric divergence for this model:** The
  disagreement between gap1_MAE (prefers ~0.6) and canonical headline
  (prefers 0.05) is large. If future exports show the canonical optimum
  shifting toward higher sr, that would suggest the current preference
  is dataset-specific. Worth monitoring across exports.

### Cross-cutting

- **Timing as shared bottleneck:** Timing (47.9) is substantially weaker
  than count (94.0). This pattern persists across all five models — see
  `feedcast/research/README.md`.
- **Internal vs. canonical metric divergence:** The gap-MAE (sr≈0.6) vs
  canonical (sr=0.05) disagreement is part of a broader cross-model
  pattern — see `feedcast/research/README.md`.
