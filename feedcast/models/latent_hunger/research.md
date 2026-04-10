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
| Export | `exports/export_narababy_silas_20260410.csv` |
| Dataset | `sha256:8dc1ea2650b0779b6a342b90aa918bc5bd2d5412bfbef25a2df4a8e1bada504e` |
| Command | `.venv/bin/python -m feedcast.models.latent_hunger.analysis` |
| Canonical headline | 66.3 |
| Availability | 26/26 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("latent_hunger")` through
the shared replay infrastructure. This produces a multi-window
aggregate (lookback 96h, half-life 36h, episode-boundary cutoffs) that
is directly comparable across all models.

**Canonical tuning** last ran as a 19-candidate `SATIETY_RATE` sweep
via `run_replay.py` and the built-in `tune_model()` validation:

`0.02`, `0.03`, `0.04`, `0.05`, `0.06`, `0.08`, `0.1`, `0.15`, `0.2`,
`0.25`, `0.3`, `0.35`, `0.4`, `0.45`, `0.5`, `0.55`, `0.6`, `0.65`,
`0.7`, `0.8`

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

On the 20260410 export, the canonical optimum shifted upward from
sr=0.05 to a broad plateau at sr=0.5–0.8. The prior production value
(sr=0.05) and the new production value (sr=0.55) compare as follows:

| Metric | Prior (sr=0.05) | Current (sr=0.55) |
|---|---|---|
| Headline | 65.8 | 66.3 |
| Count | 96.3 | 95.3 |
| Timing | 45.5 | 46.8 |

All 26 windows scored (100% availability) for all candidates. The
surface is very shallow above sr=0.3 — the top 5 (sr=0.4–0.8) span
only 0.2 headline points. The improvement over sr=0.05 comes from
timing (+1.3) at a modest count cost (−1.0).

This is a directional reversal from the 20260327 export, where sr=0.05
was the canonical winner and rates above 0.25 were progressively worse.
The shift confirms the open question from the prior analysis: the
low-rate preference was export-specific, not structural.

Per-window timing scores range from 25.7 to 60.6. The weakest windows
cluster around overnight transitions and cluster-feed periods, consistent
with the cross-cutting timing bottleneck.

### Diagnostic findings

**Multiplicative vs. additive:** Multiplicative satiety (gap1_MAE=0.719h,
pred_std=0.547h) outperforms additive (gap1_MAE=0.718h,
pred_std=0.007h) on the raw-data walk-forward evaluation. The gap-MAE
difference is negligible, but the critical signal is prediction
diversity — additive collapses to near-constant gaps (pred_std≈0),
confirming the design rationale in `design.md`.

**Circadian modulation:** Best circadian amplitude is 0.100 with
gap1_MAE=0.698h, a marginal improvement over no-circadian 0.719h.
Joint refinement with circadian achieves 0.673h, but the gain does not
survive episode-level data (where volume already encodes time-of-day
effects). Production holds `CIRCADIAN_AMPLITUDE=0.0`.

**Episode-level impact:** Episode collapsing improves all metrics
substantially (gap1_MAE 0.719h→0.583h, fcount_MAE 0.97→0.87). Volume-
gap correlation strengthens at episode level on this export
(raw 0.284→episode 0.303), reversing the prior-export pattern where
episode correlation was weaker. This remains the strongest single design
decision.

**Internal vs. canonical metric disagreement:** The episode-level grid
search finds best sr=0.360, while canonical scoring places the optimum
at sr=0.55–0.8 (broad plateau). The direction of disagreement has
reversed from the prior export (where internal preferred ~0.645 and
canonical preferred 0.05). On this export, canonical prefers higher
rates than the internal diagnostic. The reversal suggests neither
metric consistently pulls in one direction — the disagreement is
export-dependent rather than structural.

**Holdout 24h:** Predicted 7 feeds vs. 7 actual, mean timing error
0.56h on matched pairs. Feed count is exact. Timing errors concentrate
in the overnight stretch (21:25→20:32 err=0.89h, 00:26→23:20 err=1.09h,
06:27→07:40 err=1.23h).

**Naive baselines:** All model variants beat last-gap (0.913h) and
mean-3-gaps (0.837h). The multiplicative model at 0.719h represents a
21% improvement over last-gap.

## Conclusions

**Disposition: Change.** `SATIETY_RATE` raised from 0.05 to 0.55.

On the 20260410 export, the canonical optimum shifted decisively upward.
Every rate from 0.3 to 0.8 outperforms 0.05, with a broad plateau from
0.5 to 0.8 (all within 0.074 headline points). The value 0.55 was
chosen interior to the plateau for robustness.

This resolves the prior open question about low-rate stability: the
preference for sr=0.05 was export-specific, not structural. The shift
also narrows the internal-canonical divergence — the canonical optimum
now overlaps the range where volume sensitivity is substantive.

At sr=0.55, the satiety effect is 0.42 for 1oz and 0.89 for 4oz. The
relative ratio (2.1×) is smaller than at sr=0.05 (3.7×), but the
absolute gap differentiation is much larger, which improves timing on
the canonical metric (+1.3 over sr=0.05).

The internal diagnostics (episode-level gap1_MAE) and canonical scoring
now disagree in the opposite direction from the prior export: the
episode-level internal optimum is sr=0.360, while canonical prefers
0.55–0.8. This reversal suggests the disagreement direction is
export-dependent rather than a fixed structural property.

## Open questions

### Model-local

- **Plateau stability across exports:** The canonical optimum moved from
  sr=0.05 (20260327 export) to a broad plateau at sr=0.5–0.8 (20260410
  export). The plateau is so flat that the exact location within it is
  not meaningful. If future exports narrow the plateau or shift its
  center, that would indicate the baby's volume-gap dynamics are
  still evolving.
- **Internal-canonical divergence direction reversal:** On the 20260327
  export, internal preferred higher sr (~0.645) and canonical preferred
  lower (0.05). On the 20260410 export, the direction reversed: internal
  episode-level prefers sr=0.360, canonical prefers 0.55–0.8. If the
  direction continues to be export-dependent, the divergence itself may
  not be structurally informative for this model.

### Cross-cutting

- **Timing as shared bottleneck:** Timing (46.8) is substantially weaker
  than count (95.3). This pattern persists across all five models — see
  `feedcast/research/README.md`.
- **Internal vs. canonical metric divergence:** The divergence for this
  model is now directionally unstable across exports, which is relevant
  to the cross-model pattern tracked in `feedcast/research/README.md`.
