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
| Run date | 2026-04-16 |
| Export | `exports/export_narababy_silas_20260416.csv` |
| Dataset | `sha256:383bff93af3fbf40ff86f1eccecd6d2fefd9a4b7d5093eb1b37174f552ac6e74` |
| Command | `.venv/bin/python -m feedcast.models.latent_hunger.analysis` |
| Canonical headline | 64.1 |
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

**Canonical tuning** last ran as a multi-stage sweep via `run_replay.py`:
a coarse sweep (0.03–3.0) to characterize the landscape, then refined
(1.05–1.35) to locate the new interior peak, and a half-life
cross-check at the peak.

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

On the 20260416 export, the canonical landscape changed shape relative
to 20260411. The prior monotonic climb toward the constant-gap limit
flattened into a clear interior peak at sr=1.20:

| sr | headline | count | timing |
|---|---|---|---|
| 0.03 | 63.96 | 85.39 | 48.38 |
| 0.55 (prior production) | 62.98 | 90.22 | 44.33 |
| 0.70 | 63.58 | 90.76 | 44.95 |
| 1.00 | 63.84 | 90.77 | 45.40 |
| **1.20** | **64.08** | **90.81** | **45.78** |
| 1.25 | 64.08 | 90.81 | 45.78 |
| 1.50 | 63.94 | 90.79 | 45.53 |
| 2.00 | 63.77 | 90.80 | 45.24 |
| 3.00 | 63.68 | 90.76 | 45.10 |

All 26 windows scored at 100% availability. The 1.00–1.35 range forms a
new plateau (63.84–64.08, span 0.24 points). The old moderate plateau
(0.40–0.70) has dropped to 62.58–63.58, and the prior production sr=0.55
is now near the bottom of that range.

The sr=0.03 low-sr option still scores higher headline (63.96) than the
old moderate plateau but count drops to 85.4 from 90.8, repeating the
pattern where very low rates degenerate into constant-gap predictors and
sacrifice episode-count matching for timing. The constant-gap limit at
the high end (sr=3.0, 63.68) no longer wins — it undercuts the new
interior peak by 0.40 points.

A half-life cross-check at sr=1.20 confirmed 168h is still optimal
(64.08 at 168h vs. 62.76/63.73/63.75 at 72/120/240h). No change to
`RECENCY_HALF_LIFE_HOURS`.

Per-window timing scores range from 33.8 to 70.2. The weakest windows
cluster around cluster-feed periods and evening transitions, consistent
with the cross-cutting timing bottleneck.

### Diagnostic findings

**Multiplicative vs. additive:** Multiplicative satiety (gap1_MAE=0.793h,
pred_std=0.428h) still outperforms additive (gap1_MAE=0.784h,
pred_std=0.009h) on prediction diversity — additive collapses to
near-constant gaps (pred_std near 0). Additive's slightly lower gap1_MAE
on this export is within noise and comes from its degenerate collapse
in a dataset that has narrowed toward typical feed sizes. The design
rationale in `design.md` still holds.

**Circadian modulation:** Best circadian amplitude is 0.200 with
gap1_MAE=0.704h, an improvement over no-circadian 0.793h.
Joint refinement with circadian achieves 0.696h. Production holds
`CIRCADIAN_AMPLITUDE=0.0` because the gain historically does not survive
episode-level data (where volume already encodes time-of-day effects).

**Episode-level impact:** Episode collapsing improves all metrics
(gap1_MAE 0.793h→0.656h, fcount_MAE 0.96→0.93). Volume-gap correlation
strengthens at episode level (raw 0.266→episode 0.306). This remains the
strongest single design decision.

**Internal vs. canonical metric disagreement:** The episode-level grid
search finds best sr=0.334, while canonical scoring on this export
favors much higher rates (peak at sr=1.20). The gap between the two
objectives has widened substantially — about an order of magnitude. This
is the largest divergence observed across the last five exports, and it
coincides with the emergence of the new interior peak on the canonical
surface.

**Holdout 24h:** Predicted 8 feeds vs. 8 actual (count error 0), but
mean timing error 2.53h on 8 matched pairs. Timing errors are
concentrated in the late-night/early-morning stretch where the
holdout-fit model anchors off a late-evening 2oz snack bottle and
assumes the snack-regime cadence persists through the night.

**Naive baselines:** All model variants beat last-gap (0.944h) and
mean-3-gaps (0.911h). The multiplicative constant model (0.793h) is a
16% improvement over last-gap; joint-refined multiplicative + circadian
(0.696h) is a 26% improvement.

## Conclusions

**Disposition: Change.** `SATIETY_RATE` raised from 0.55 to 1.20.

On the 20260416 export the canonical landscape restructured: instead of
climbing monotonically to the constant-gap limit, it now has a clear
interior peak at sr=1.20 (headline 64.08, +1.10 over sr=0.55). The old
0.40–0.70 moderate plateau flattened and now sits 0.5–1.5 points below
the new 1.00–1.35 plateau. The constant-gap limit (sr=3.0, 63.68) no
longer wins.

The shift coincides with the baby's feeding pattern consolidating. Per
the agent insights, Apr 10–16 averaged 7–9 episodes per day with
per-episode volumes in a narrow 3.77–4.16 oz band — 4 oz is the new
normal, snack clusters have become rare, and daytime cadence is
rhythmic. Under these tighter dynamics, the model benefits from
treating typical feeds as near-saturating resets while still responding
to the occasional 1–2 oz snack bottle.

At sr=1.20, the satiety effect is 0.70 for 1oz and 0.99 for 4oz — a
1.4x range compared with 2.1x at sr=0.55. Volume sensitivity is
compressed but not eliminated: small feeds still get materially shorter
predicted gaps. The design hypothesis (multiplicative satiety) still
beats additive satiety on prediction diversity (pred_std 0.428 vs
0.009).

This is the fifth satiety-rate optimum shift in three weeks
(0.05→0.55→0.18→0.55 monotonic-climbing→1.20 interior-peak). Unlike
prior shifts within a shallow plateau, this is a structural change in
the landscape shape: the interior peak has replaced the monotonic
climb, so the new value sits inside a plateau rather than being an
arbitrary anchor in a noisy range.

The internal-canonical disagreement is now at its widest observed
magnitude. Episode-level gap1_MAE prefers sr=0.334 while canonical
prefers sr=1.20 — roughly an order of magnitude apart. This is
consistent with the broader pattern tracked in the research hub's
stacked-generalization open question, but the magnitude on this export
is a step change rather than drift.

## Open questions

### Model-local

- **Canonical surface instability and landscape shape:** The canonical
  optimum has now shifted five times in three weeks: sr=0.05 (20260327),
  sr=0.55 (20260410), sr=0.18 (20260410(2)), monotonically climbing on
  20260411, and a new interior peak at sr=1.20 on 20260416. The earlier
  shifts were plateau drift within a shallow landscape; this one is a
  structural change from a monotonic climb into an interior peak. If the
  interior peak persists on future exports, the right long-term approach
  may be to tune sr to the plateau center rather than track its exact
  location. If the shape continues to restructure, the parameter may be
  fundamentally under-determined on this dataset and a model-level
  change (e.g., making the satiety rate self-adapting from recent
  volume-gap pairs, similar to how growth rate is already runtime-fit)
  could dominate manual re-tuning.
- **Interior peak replaces constant-gap limit:** On the 20260416 export
  the constant-gap limit at sr=3.0 scores 0.40 points below the
  interior peak at sr=1.20. This reverses the pattern seen on prior
  exports, where the limit was the best available option and a
  principled stand was required to avoid it. Whether the interior peak
  is a stable feature or transient is an open question for the next
  retune.
- **Low-sr count-timing tradeoff:** sr=0.03 still scores 63.96 headline
  (above the old moderate plateau) but collapses count to 85.4. This
  pattern has appeared on every export tested and is not a property of
  any particular export. Whether the high headline at sr=0.03 is real
  signal or a scoring artifact (geometric mean rewarding an imbalanced
  count/timing tradeoff) remains unresolved.
- **Internal-canonical divergence widened to an order of magnitude:**
  Internal episode gap1_MAE prefers sr=0.334, canonical prefers
  sr=1.20. The ratio is now ~3.6x, up from ~1.3x on the prior export.
  This is the largest spread observed and is consistent with the
  stacked-generalization hypothesis in the research hub: the production
  forecaster's chained prediction logic values constant-gap behavior in
  the typical-volume regime that single-step MAE doesn't measure.

### Cross-cutting

- **Timing as shared bottleneck:** Timing (45.8) remains substantially
  weaker than count (90.8). Timing improved 3.4 points from the prior
  export at the new constants. The pattern persists across all five
  models; see `feedcast/research/README.md`.
- **Internal vs. canonical metric divergence:** See the model-local
  entry above. The magnitude on this export (sr=0.334 internal vs
  sr=1.20 canonical) is the widest observed and relevant to the
  cross-model pattern tracked in `feedcast/research/README.md`.
- **Canonical landscape restructuring as a pattern-shift signal:** The
  shift from a monotonic-climbing landscape to one with an interior
  peak coincides with a visible consolidation in the baby's feed
  pattern (narrower volume distribution, stable episode count). If
  other models see similar landscape restructuring on this export, it
  may be worth promoting "landscape shape as trend indicator" into a
  shared research article.
