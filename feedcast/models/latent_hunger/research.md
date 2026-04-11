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
| Run date | 2026-04-11 |
| Export | `exports/export_narababy_silas_20260411.csv` |
| Dataset | `sha256:138b5d3ad7d106444951acc6c56154bcd1ae94184f58a566f83c032ad41ef5ec` |
| Command | `.venv/bin/python -m feedcast.models.latent_hunger.analysis` |
| Canonical headline | 63.1 |
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

**Canonical tuning** last ran as a multi-stage sweep via `run_replay.py`:
a coarse sweep (0.05-0.70), extended upward (0.80-5.0) to confirm the
monotonic climb reaches the constant-gap limit, then refined (0.40-0.70)
to characterize the moderate plateau.

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

On the 20260411 export, headline dropped to 62.6 at the prior
production sr=0.18, a 3.7-point decline from the prior export. The
decline affected all satiety rates: even the constant-gap limit
(sr>3.0) only reached 64.8, compared to 66.3 at the prior export's
best. The drop is driven by a broader pattern shift, not the satiety
rate specifically.

Within that context, the landscape climbs monotonically from low to
high satiety rates. The prior (sr=0.18) and new production (sr=0.55)
compare as follows:

| Metric | Prior (sr=0.18) | Current (sr=0.55) |
|---|---|---|
| Headline | 62.6 | 63.1 |
| Count | 94.7 | 94.8 |
| Timing | 41.9 | 42.4 |

All 24 windows scored (100% availability) for all candidates. The
0.40-0.70 range forms a gentle plateau (63.0-63.2, span 0.13 points).
Above sr=0.80 the curve steepens as the model converges toward the
constant-gap limit (64.8 at sr>3.0), where volume sensitivity is
effectively neutralized.

The analysis script's internal canonical sweep identifies sr=0.03 as
headline-best (67.0), but count collapses to 83.7 (from 94.8). This
repeats the pattern seen on prior exports: very low satiety rates
improve timing by producing near-constant gap predictions, at the cost
of substantial count degradation.

This is the fourth canonical optimum shift in two weeks
(0.05->0.55->0.18->0.55), confirming that the surface is unstable
across exports. The value 0.55 was chosen for stability: it has been at
or near optimal on 3 of the last 4 exports and sits in the moderate
zone that preserves meaningful volume sensitivity.

Per-window timing scores range from 25.9 to 60.6. The weakest windows
cluster around overnight transitions and cluster-feed periods, consistent
with the cross-cutting timing bottleneck.

### Diagnostic findings

**Multiplicative vs. additive:** Multiplicative satiety (gap1_MAE=0.748h,
pred_std=0.546h) outperforms additive (gap1_MAE=0.755h,
pred_std=0.007h) on the raw-data walk-forward evaluation. The critical
signal remains prediction diversity: additive collapses to near-constant
gaps (pred_std near 0), confirming the design rationale in `design.md`.

**Circadian modulation:** Best circadian amplitude is 0.150 with
gap1_MAE=0.719h, an improvement over no-circadian 0.748h.
Joint refinement with circadian achieves 0.688h. Production holds
`CIRCADIAN_AMPLITUDE=0.0` because the gain historically does not survive
episode-level data (where volume already encodes time-of-day effects).

**Episode-level impact:** Episode collapsing improves all metrics
substantially (gap1_MAE 0.748h->0.568h, fcount_MAE 0.99->0.82).
Volume-gap correlation strengthens at episode level on this export
(raw 0.287->episode 0.320). This remains the strongest single design
decision.

**Internal vs. canonical metric disagreement:** The episode-level grid
search finds best sr=0.231, while canonical scoring on this export
favors higher rates (monotonically climbing toward the constant-gap
limit, with sr=0.55 chosen in the moderate plateau). The internal
optimum (0.231) is close to the prior production value (0.18). Across
four exports, the canonical optimum has been 0.05, 0.55, 0.18, and now
monotonically climbing. The instability is in the canonical surface, not
a stable structural property.

**Holdout 24h:** Predicted 7 feeds vs. 9 actual, mean timing error
0.93h on 7 matched pairs. Feed count error of 2 (under-predicted).
Timing errors concentrate in the daytime stretch
(09:52 err=0.81h, 12:16 err=1.42h, 15:29 err=1.21h) and early morning
(06:30 err=1.77h).

**Naive baselines:** All model variants beat last-gap (0.937h) and
mean-3-gaps (0.906h). The multiplicative model at 0.748h represents a
20% improvement over last-gap.

## Conclusions

**Disposition: Change.** `SATIETY_RATE` raised from 0.18 to 0.55.

On the 20260411 export, headline dropped 3.7 points across all
satiety rates. The decline is driven by a broader pattern shift, not
the satiety rate. Within the available improvement range, the canonical
landscape climbs monotonically from sr=0.05 to the constant-gap limit
(sr>3.0). The prior sr=0.18 sits near the bottom (headline 62.6),
while the 0.40-0.70 plateau scores 63.0-63.2, and the constant-gap
limit reaches 64.8.

The value 0.55 was chosen for cross-export stability rather than
single-export optimality. Across four recent exports, sr=0.55 has been
either optimal or within ~1 point of optimal. The constant-gap limit
(sr>3.0) was not adopted despite its +2.2 headline advantage because
it neutralizes volume sensitivity, the model's distinguishing design
hypothesis and ensemble contribution.

This is the fourth optimum shift in two weeks (0.05->0.55->0.18->0.55),
confirming that the canonical surface is unstable. The surface is
consistently shallow near the optimum (top candidates span <1 headline
point in the moderate range), so the exact value matters less than
staying in a reasonable zone.

At sr=0.55, the satiety effect is 0.42 for 1oz and 0.89 for 4oz (2.1x
ratio). This is strong volume sensitivity that meaningfully
differentiates gap predictions by feed size.

The internal-canonical disagreement continues. Episode-level gap1_MAE
prefers sr=0.231, while canonical on this export favors higher rates.
The disagreement has widened compared to the prior export. Whether this
reflects a genuine structural divergence or transient data-window
effects remains unresolved.

## Open questions

### Model-local

- **Canonical surface instability:** The canonical optimum has shifted
  four times in two weeks: sr=0.05 (20260327), sr=0.55 (20260410),
  sr=0.18 (20260410(2)), and monotonically climbing on 20260411. The
  surface is consistently shallow (the 0.40-0.70 moderate zone spans
  0.13 headline points), so the exact optimum is sensitive to which
  data windows are included. The value sr=0.55 has been chosen for
  cross-export stability. If the optimum stabilizes on future exports,
  that would indicate the baby's volume-gap dynamics have settled. If
  it continues to shift, the satiety rate may need to be adapted more
  dynamically or the parameter may be fundamentally under-determined on
  this dataset.
- **Monotonic climb toward constant-gap limit:** On the 20260411 export,
  the canonical landscape climbs monotonically to sr>3.0, where the
  model degenerates to a constant-gap predictor. The full +2.2 headline
  improvement was not adopted because it neutralizes volume sensitivity.
  Whether this pattern persists on future exports would indicate whether
  the baby's current feeding pattern has weaker volume-gap dynamics than
  historical data, or whether this is a transient data-window effect.
- **Low-sr count-timing tradeoff:** sr=0.03 achieves higher headline
  (67.0 vs 63.1) but count collapses to 83.7 (vs 94.8). This pattern
  has appeared on every export tested. Very low satiety rates effectively
  disable volume sensitivity, producing near-constant gap predictions.
  Whether the headline improvement at sr=0.03 is real signal or a
  scoring artifact (geometric mean rewarding an imbalanced count/timing
  tradeoff) is unresolved.

### Cross-cutting

- **Timing as shared bottleneck:** Timing (42.4) is substantially weaker
  than count (94.8). This pattern persists across all five models; see
  `feedcast/research/README.md`.
- **Internal vs. canonical metric divergence:** On this export, the
  divergence widened (internal sr=0.231, canonical favoring higher
  rates). This follows a narrowing on the prior export (internal 0.360,
  canonical 0.18). The direction and magnitude of the disagreement
  fluctuates across exports, relevant to the cross-model pattern tracked
  in `feedcast/research/README.md`.
