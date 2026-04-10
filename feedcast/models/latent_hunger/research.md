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
| Run date | 2026-04-10 |
| Export | `exports/export_narababy_silas_20260410(2).csv` |
| Dataset | `sha256:ff8b0a112f77742af35b44e97652b6108915a609526619b808546434315927b8` |
| Command | `.venv/bin/python -m feedcast.models.latent_hunger.analysis` |
| Canonical headline | 66.3 |
| Availability | 25/25 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("latent_hunger")` through
the shared replay infrastructure. This produces a multi-window
aggregate (lookback 96h, half-life 36h, episode-boundary cutoffs) that
is directly comparable across all models.

**Canonical tuning** last ran as a two-stage sweep via `run_replay.py`:
a 10-candidate coarse sweep (0.05–0.8), then a 7-candidate refinement
(0.12–0.28) to confirm the optimum is interior.

Growth rate is estimated at runtime from recent episodes and is not
overridable via constant overrides, so it is not part of the sweep.
Candidates are ranked by availability tier first, then headline score.

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

On the 20260410(2) export, the canonical optimum shifted downward from
the prior sr=0.55 plateau to sr=0.12–0.20. The prior production value
(sr=0.55) and the new production value (sr=0.18) compare as follows:

| Metric | Prior (sr=0.55) | Current (sr=0.18) |
|---|---|---|
| Headline | 65.2 | 66.3 |
| Count | 96.1 | 96.3 |
| Timing | 44.8 | 46.1 |

All 25 windows scored (100% availability) for all candidates. The
plateau at sr=0.12–0.20 spans only 0.032 headline points. The
improvement over sr=0.55 comes from both timing (+1.3) and count (+0.2).

Below the plateau, sr=0.03 scores headline 67.0 but count collapses to
84.5. Very low satiety rates produce near-constant gap predictions that
score well on timing but degrade count substantially. This repeats the
pattern seen at sr=0.05 on prior exports and was not adopted because
it neutralizes the model's volume sensitivity (the design hypothesis).

This is the third canonical optimum shift in two weeks (0.05→0.55→0.18),
confirming that the surface is unstable across exports. The baby's
volume-gap dynamics are still evolving faster than the canonical
evaluation window can stabilize.

Per-window timing scores range from 25.3 to 60.5. The weakest windows
cluster around overnight transitions and cluster-feed periods, consistent
with the cross-cutting timing bottleneck.

### Diagnostic findings

**Multiplicative vs. additive:** Multiplicative satiety (gap1_MAE=0.704h,
pred_std=0.544h) outperforms additive (gap1_MAE=0.704h,
pred_std=0.007h) on the raw-data walk-forward evaluation. The gap-MAE
difference is negligible, but the critical signal is prediction
diversity — additive collapses to near-constant gaps (pred_std near 0),
confirming the design rationale in `design.md`.

**Circadian modulation:** Best circadian amplitude is 0.050 with
gap1_MAE=0.685h, a marginal improvement over no-circadian 0.704h.
Joint refinement with circadian achieves 0.671h, but the gain does not
survive episode-level data (where volume already encodes time-of-day
effects). Production holds `CIRCADIAN_AMPLITUDE=0.0`.

**Episode-level impact:** Episode collapsing improves all metrics
substantially (gap1_MAE 0.704h→0.572h, fcount_MAE 0.97→0.86). Volume-
gap correlation strengthens at episode level on this export
(raw 0.284→episode 0.303). This remains the strongest single design
decision.

**Internal vs. canonical metric disagreement:** The episode-level grid
search finds best sr=0.360, while canonical scoring places the optimum
at sr=0.12–0.20. Both now prefer moderate satiety rates, narrowing the
disagreement from prior exports. The internal optimum (0.360) is higher
than the canonical optimum (0.18), but both are in the range where
volume sensitivity is substantive. Across three exports, the canonical
optimum has been 0.05, 0.55, and 0.18 — the instability is in the
canonical surface, not a stable structural property.

**Holdout 24h:** Predicted 7 feeds vs. 7 actual, mean timing error
0.56h on matched pairs. Feed count is exact. Timing errors concentrate
in the overnight stretch (21:21→20:32 err=0.82h, 00:22→23:20 err=1.03h,
06:23→07:40 err=1.30h).

**Naive baselines:** All model variants beat last-gap (0.894h) and
mean-3-gaps (0.829h). The multiplicative model at 0.704h represents a
21% improvement over last-gap.

## Conclusions

**Disposition: Change.** `SATIETY_RATE` lowered from 0.55 to 0.18.

On the 20260410(2) export, the canonical optimum shifted downward from
the sr=0.5–0.8 plateau to sr=0.12–0.20. The prior sr=0.55 degraded to
headline 65.2, while sr=0.18 scores 66.3. The value 0.18 was chosen
interior to the 0.12–0.20 plateau for robustness.

This is the third optimum shift in two weeks (0.05→0.55→0.18),
establishing that the canonical surface is unstable. The baby's
volume-gap dynamics are evolving faster than the 96-hour evaluation
window can stabilize. The instability is not surprising given the
baby's growth phase, but it means any specific satiety rate value is
provisional.

At sr=0.18, the satiety effect is 0.16 for 1oz and 0.51 for 4oz (3.1x
ratio). This is moderate volume sensitivity — stronger than sr=0.05
(which effectively neutralized the model) but less aggressive than
sr=0.55 (which over-committed to volume differentiation on this export).

The internal-canonical disagreement has narrowed on this export.
Episode-level gap1_MAE prefers sr=0.360, and canonical prefers sr=0.18.
Both are in the range where volume sensitivity is meaningful, unlike
prior exports where the two metrics pulled in opposite directions.
Whether this convergence persists across future exports will indicate
whether the disagreement is structural or data-window-dependent.

## Open questions

### Model-local

- **Canonical surface instability:** The canonical optimum has shifted
  three times in two weeks: sr=0.05 (20260327), sr=0.55 (20260410),
  sr=0.18 (20260410(2)). The surface is consistently shallow (top
  candidates span <1 headline point), so the exact optimum is sensitive
  to which data windows are included. If the optimum stabilizes on
  future exports, that would indicate the baby's volume-gap dynamics
  have settled. If it continues to shift, the satiety rate may need to
  be adapted more dynamically or the parameter may be fundamentally
  under-determined on this dataset.
- **Low-sr count-timing tradeoff:** sr=0.03 achieves higher headline
  (67.0 vs 66.3) but count collapses to 84.5 (vs 96.3). This pattern
  has appeared on multiple exports. Very low satiety rates effectively
  disable volume sensitivity, producing near-constant gap predictions.
  Whether the headline improvement at sr=0.03 is real signal or a
  scoring artifact (geometric mean rewarding an imbalanced count/timing
  tradeoff) is unresolved.

### Cross-cutting

- **Timing as shared bottleneck:** Timing (46.1) is substantially weaker
  than count (96.3). This pattern persists across all five models — see
  `feedcast/research/README.md`.
- **Internal vs. canonical metric divergence:** On this export, the
  divergence narrowed (internal sr=0.360, canonical sr=0.18). Whether
  this convergence persists is relevant to the cross-model pattern
  tracked in `feedcast/research/README.md`.
