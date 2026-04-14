# Survival Hazard Research

> `design.md` documents why the model works the way it does.
> `methodology.md` is the report-facing description.
> This file is the evidence: current support and challenges for the
> model's design and constants.

## Overview

Survival Hazard models feeding episodes as survival events with
day-part-specific Weibull hazards. The key research questions are:

1. How well does the model forecast under canonical multi-window
   evaluation?
2. Do canonical replay results support the current
   `OVERNIGHT_SHAPE` and `DAYTIME_SHAPE` constants?
3. Do the internal Weibull fitting diagnostics point in the same
   direction as canonical replay, or do they diverge?
4. Do the volume-covariate and breastfeed-merge checks justify any
   change to the current production design?

## Last run

| Field | Value |
|---|---|
| Run date | 2026-04-13 |
| Export | `exports/export_narababy_silas_20260413.csv` |
| Dataset | `sha256:1820a6f33b499f22c5adbfc8bbb0538fca2366fbf4661452b57fddd31a0a6d8d` |
| Command | `.venv/bin/python -m feedcast.models.survival_hazard.analysis` |
| Canonical headline | 68.3 |
| Availability | 25/25 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("survival_hazard")`
through the shared replay infrastructure. This produces a multi-window
aggregate (lookback 96h, half-life 36h, episode-boundary cutoffs) that
is directly comparable across all models. The research script enables
`parallel=True` for this path because window-level parallelism is safe
for a fixed constant set and keeps the canonical run practical.

**Canonical tuning** sweeps `OVERNIGHT_SHAPE` and `DAYTIME_SHAPE`
jointly via `tune_model()`. The current sweep is a mixed-resolution
154-candidate grid:

- `OVERNIGHT_SHAPE`: `3.0, 3.5, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0`
- `DAYTIME_SHAPE`: `1.0, 1.25, 1.5, 1.625, 1.75, 1.875, 2.0, 2.5, 3.0, 3.5, 4.0`

The original 40-candidate grid bottomed out at the lowest-tested
corner, so the wider grid is the authoritative search.
Scale is still estimated at runtime; only the fixed shape parameters
are tuned canonically.

### Objective comparison contract

Canonical and internal diagnostics answer different questions.
Canonical evaluation uses the shared replay stack: bottle-only scoring
events, episode-boundary cutoffs over the most recent 96 hours, and the
24-hour headline scorer applied to the shipped chained-median
forecaster. The internal diagnostics fit or test the gap distribution
more directly: weighted MLE on observed gaps and walk-forward gap/count
errors. When these objectives disagree, the comparison is between a
distribution-fit question and a shipped-forecast-quality question, not
between two equivalent estimators of the same target.

### Model-specific diagnostics

**Raw-gap Weibull fits** (Sections 1-4) establish the basic hazard
shape, the overnight/daytime regime split, and the volume-covariate
signal on the uncollapsed bottle-only event stream.

**Discrete hazard comparison** (Section 5) is a historical design
alternative. It asks whether a more flexible non-parametric hazard is
actually needed. On the current export it remains a useful baseline,
not a production contender.

**Raw walk-forward comparisons** (Sections 6-7) compare plain Weibull,
volume-adjusted Weibull, and the day-part split on gap MAE / feed-count
MAE. This is the main local evidence that the overnight/daytime split
is real and helpful.

**24h holdout replay** (Section 8) is a sanity check that the shipped
model can forecast the most recent full day from only prior data.

**Simulation-study constraint:** The analysis MLE and the shipped
forecaster validate different objects. Shape-recovery tests are
cleanest on direct day-part gap samples drawn from known Weibull
distributions. Shipped-forecast and replay tests should use
chronological histories and compare against the deterministic
median-path target, with the first forecast gap anchored to the last
feed's day-part rather than the cutoff wall clock.

**Episode-level analysis** (Section 9) re-runs the key fitting and
walk-forward logic on cluster-collapsed episodes. This is the evidence
for the episode-history design choice in `design.md`.

**Episode-level volume overlay testing** (Section 9) asks a narrower
question than the canonical sweep: not "does volume matter at all,"
but "does the current scalar AFT overlay improve this model?" That is
why this section remains diagnostic even when the likelihood-ratio test
is significant.

**Breastfeed merge policy comparison** (Section 10) checks whether
merging estimated breastfeed volume into bottle events changes episode
boundaries. This matters here because the clustering rule's extension
arm depends on the later event's volume.

## Results

### Canonical findings

The current production constants (`OVERNIGHT_SHAPE=6.0`,
`DAYTIME_SHAPE=1.75`) score:

| Metric | Score |
|---|---|
| Headline | 68.3 |
| Count | 94.2 |
| Timing | 50.1 |

All 25 windows scored (100% availability). Headline improved +4.0 over
the prior constants (4.5/3.0) on the same export. All three sub-metrics
improved: count +0.9, timing +5.5.

The 154-candidate canonical sweep confirms the current constants as
near-best. The best candidate (`OVERNIGHT_SHAPE=8.0`,
`DAYTIME_SHAPE=1.0`) gains +0.9 headline but trades 3 count points
(94.2 to 91.1) for timing (50.1 to 53.0). DT=1.0 is the structural
boundary (exponential, memoryless), so the sweep does not extend below
it. The top candidates at DT=1.75 span a broad plateau (68.0-68.3)
from OVERNIGHT 5.5 to 8.0.

The weakest canonical windows are concentrated on 2026-04-10: the
12:16 window scores headline 46.4 with timing 25.5. The strongest
windows are the most recent (April 12), with the highest-weight
window (19:15, weight 1.0) scoring 82.4 headline.

### Diagnostic findings

**Day-part split remains justified:** On the raw bottle-only event
stream, day-part split Weibull beats discrete hazard, plain Weibull,
and naive baselines on one-step walk-forward gap MAE:

| Model | gap1 MAE |
|---|---|
| Naive last-gap | 0.937h |
| Naive mean-3-gaps | 0.906h |
| Discrete hazard | 0.758h |
| Plain Weibull | 0.772h |
| Day-part split Weibull | 0.705h |

This still supports the overnight/daytime regime split in `design.md`.

**Episode history remains essential:** Raw bottle-only events (270)
collapse to 238 episode events, absorbing 32 cluster feeds. The direct
day-part Weibull fits sharpen materially after collapsing:
overnight `4.98 -> 5.96`, daytime `2.61 -> 3.47`. That is the cleanest
evidence that raw feeds were contaminating the gap distribution with
cluster-internal noise.

**Internal fit and canonical replay have partially converged:** The
episode-level MLE fit prefers shapes (`5.96`, `3.47`), while canonical
replay selects (`6.0`, `1.75`). The overnight shape converged for the
first time: MLE 5.96, production 6.0. The daytime shape diverges (MLE
3.47 vs production 1.75), consistent with the established pattern where
the production forecaster's chained-median mechanics prefer softer
daytime shapes than the raw distribution fit.

The overnight convergence is a structural improvement over prior
exports, where MLE and canonical disagreed on overnight by 1-3 shape
units. The convergence suggests that 6.0 is well-anchored as the
baby's current overnight regularity rather than an artifact of replay
window weighting.

The contributor analysis from prior exports still applies:

1. *Data window.* The episode-level fit uses all 237 gaps in the full
   export. Canonical replay optimizes over the last ~96h with 36h
   half-life. The baby's feeding patterns shift with growth, so
   full-history MLE and recent-window replay describe different regimes.

2. *Model compensation.* The production forecaster chains deterministic
   medians, re-estimates scale at runtime, and uses conditional survival
   for the first feed. These mechanics pull optimal shapes away from
   the MLE. The daytime divergence (MLE 3.47 vs production 1.75) is
   consistent with this effect.

**Half-life trade-off stays real:** In the episode-level walk-forward
diagnostic, `48h` and `120h` tie for best on gap1 MAE (`0.621h`) while
the current `168h` is best on feed-count MAE (`0.97`). The gap1
difference is negligible (0.003h), while the feed-count advantage of
`168h` is substantial (0.97 vs 1.51).

**Volume overlay remains rejected:** On episode-level data, the scalar
AFT volume overlay is statistically significant by LR test (`6.762`),
but every positive beta worsens walk-forward performance relative to the
no-volume baseline. This is a consistent repeat of prior results:
"volume is real" does not imply "this overlay helps."

**Breastfeed merge still does nothing structural:** Bottle-only and
breastfeed-merged inputs produce identical episode boundaries on the
current export. Only 3 episode volumes change, and because the current
production model does not use a volume covariate, merge policy has no
effect on its forecasts.

**Holdout: count and timing both improved.** The shipped model
predicts 9 feeds vs. 9 actual in the most recent 24h holdout (perfect
count), with 0.54h mean timing error on the 9 matched pairs. This is a
marked improvement from the prior export (8 vs 11 actual, 3 count miss).
The count improvement reflects both the shape changes and the baby's
feeding frequency on this holdout day aligning with the model's
7-day lookback window. The persistent count miss from prior exports
does not appear on this holdout.

## Conclusions

**Disposition: Re-tuned.** `OVERNIGHT_SHAPE` changed from `4.5` to
`6.0`. `DAYTIME_SHAPE` changed from `3.0` to `1.75`.

The 20260413 export (18 new rows vs the 20260411(1) export) shifted
the canonical replay landscape substantially. With the prior shapes
(4.5/3.0), the new export scored 64.2 headline. With the updated shapes
(6.0/1.75), it scores 68.3 (+4.0). All three sub-metrics improved:
count +0.9, timing +5.5.

Both shapes sit on broad plateaus: OVERNIGHT 5.5-7.5 at DT=1.75 (all
score 68.0+), and DAYTIME 1.5-2.0 across most overnight values. The
constants were chosen at plateau centers to reduce sensitivity to the
next data shift.

The overnight shape (6.0) converges with the episode-level MLE (5.96)
for the first time. Prior exports showed MLE/canonical disagreement of
1-3 shape units for overnight. This convergence suggests 6.0 is
structurally anchored. The overnight oscillation (4.75 to 7.5 to 4.5
to 6.0) may be stabilizing: 6.0 is near the center of the historical
range and is supported by both fitting approaches.

The daytime shape (1.75) diverges from MLE (3.47) in the expected
direction: the production forecaster's chained-median mechanics prefer
softer daytime shapes. DT=1.75 is the same value that was optimal on
the 20260327 export, suggesting it may be a more stable point for
daytime than the 3.0 from the brief April 10-11 period.

## Open questions

### Model-local

- **Overnight shape oscillation may be stabilizing.** The sequence
  (4.75 to 7.5 to 4.5 to 6.0) placed the overnight shape near the
  center of its historical range and at the MLE convergence point. If
  subsequent exports confirm that 6.0 +/- 0.5 remains optimal, the
  oscillation concern is resolved. If the shape jumps again, the
  fixed-shape architecture may need structural changes (e.g., adaptive
  shape estimation, wider lookback for shape).
- **Holdout count miss resolved on this export (9 vs 9).** The prior
  persistent count miss (8 vs 11) did not recur. The improvement may
  reflect the shape changes, or the baby's feeding frequency on this
  holdout day may simply align with the model's lookback. If the count
  miss returns on subsequent exports, `LOOKBACK_DAYS` or model
  structure may need revisiting.
- **Can volume help under a different formulation?** The scalar AFT
  overlay is decisively bad. A residual or regime-specific volume term
  may still be worth testing, but it should be treated as a new model
  idea, not a tweak to the rejected overlay.

### Cross-cutting

- **MLE/canonical have partially converged for this model.** Overnight
  shape converged (MLE 5.96, production 6.0) for the first time.
  Daytime still diverges (MLE 3.47, production 1.75), consistent with
  the production forecaster's mechanics preferring softer shapes. The
  overnight convergence is relevant to the stacked generalization
  question: if a model's canonical optimum aligns with its native fit,
  internal tuning and canonical tuning would agree, weakening the case
  for separate optimization levels.
- **Timing as shared bottleneck:** Timing (50.1) lags count (94.2).
  This pattern persists across all models, see
  `feedcast/research/README.md`. The current tune improved timing by
  5.5 points, narrowing the gap somewhat.
